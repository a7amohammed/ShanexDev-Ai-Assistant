"""
Shared Spotify Web API client for the spotify_control plugin.

Leading underscore, same convention as _template.py's helper note: the
plugin loader skips any plugins/*.py file starting with '_', so this is a
library, not a discoverable tool. Import it with an ABSOLUTE import
(`from plugins import _spotify_client`) rather than a relative one — plugin
files are loaded individually via importlib.util.spec_from_file_location,
which does not reliably register the `plugins` package the way a normal
`import plugins` does, and relative imports depend on that registration.

Auth: spotipy's Authorization Code flow. Client ID/Secret are entered by the
user in ⚙ → PLUGINS → SPOTIFY (stored via memory.config_manager's generic
plugin_config store, namespace "spotify" — see PLUGIN_SETTINGS in
spotify_control.py). The resulting OAuth token is cached to disk by spotipy
itself so the one-time browser consent screen isn't repeated on every
restart.

Playback modes (play(query, kind="auto")):
  • RADIO   — a concrete song: start it, then queue ~15 similar tracks so the
              music never stops when the first one ends.
  • ARTIST  — an artist name: start the artist context (their catalogue).
  • MOOD    — a genre / ambience / playlist: start the best matching public
              playlist, falling back to genre recommendations.
`kind="auto"` picks between them from the query itself.

Everything here returns short, spoken-friendly strings (for the plugin's
run()) or None/plain dicts (for now_playing(), polled by the HUD
mini-player) — nothing raises out to a caller that isn't expecting it.
"""
from __future__ import annotations

import random
import re
import threading
import time
import unicodedata
from pathlib import Path

from memory.config_manager import get_plugin_config

try:                                   # optional dependency: never fatal at import
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
    SpotifyException = spotipy.SpotifyException
except ModuleNotFoundError:            # pragma: no cover
    spotipy = None                     # type: ignore[assignment]
    SpotifyOAuth = None                # type: ignore[assignment]

    class SpotifyException(Exception):  # type: ignore[no-redef]
        http_status = None
        msg = ""

NAMESPACE     = "spotify"
REDIRECT_URI  = "http://127.0.0.1:8734/callback"
SCOPES        = (
    "user-read-private "
    "user-modify-playback-state "
    "user-read-playback-state "
    "user-read-currently-playing"
)
_CACHE_PATH = Path(__file__).resolve().parent.parent / "config" / ".spotify_cache"

_client       = None   # cached spotipy.Spotify instance
_client_creds = None   # (client_id, client_secret) it was built with


def _get_client():
    """Build (or reuse) an authenticated spotipy.Spotify instance.

    Returns None when credentials aren't configured yet, so every caller
    turns that into a spoken "sir, Spotify isn't set up" instead of a
    traceback. Rebuilds automatically if the saved credentials changed."""
    global _client, _client_creds

    cfg           = get_plugin_config(NAMESPACE)
    client_id     = (cfg.get("client_id") or "").strip()
    client_secret = (cfg.get("client_secret") or "").strip()
    if not client_id or not client_secret:
        return None

    creds = (client_id, client_secret)
    if _client is not None and _client_creds == creds:
        return _client
    if spotipy is None:                 # package missing → same path as "not set up"
        return None

    auth = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=REDIRECT_URI,
        scope=SCOPES,
        cache_path=str(_CACHE_PATH),
        open_browser=True,
    )
    _client = spotipy.Spotify(auth_manager=auth)
    _client_creds = creds
    return _client


def test_connection(values: dict) -> tuple[bool, str]:
    """Called by the ⚙ → SPOTIFY → CONNECT button. Runs the OAuth flow right
    now (opening a browser the first time) using whatever the user just
    typed, rather than whatever was last saved — so CONNECT always tests the
    fields on screen."""
    client_id     = (values.get("client_id") or "").strip()
    client_secret = (values.get("client_secret") or "").strip()
    if not client_id or not client_secret:
        return False, "Enter both Client ID and Client Secret first."

    if spotipy is None:
        return False, "Missing package — run: pip install spotipy"

    try:
        auth = SpotifyOAuth(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=REDIRECT_URI,
            scope=SCOPES,
            cache_path=str(_CACHE_PATH),
            open_browser=True,
        )
        sp = spotipy.Spotify(auth_manager=auth)
        me = sp.current_user()

        global _client, _client_creds
        _client, _client_creds = sp, (client_id, client_secret)

        name = me.get("display_name") or me.get("id") or "your account"
        if me.get("product") != "premium":
            return False, f"Connected as {name}, but playback control needs Spotify Premium."
        return True, f"Connected as {name} ✓"
    except ModuleNotFoundError:
        return False, "Missing package — run: pip install spotipy"
    except Exception as e:
        return False, f"Failed: {e}"


# ═════════════════════════════════════════════════════════════════════════════
#  Devices & error handling
# ═════════════════════════════════════════════════════════════════════════════

_NOT_CONNECTED = (
    "Sir, Spotify isn't connected yet — add your Client ID and "
    "Client Secret in Plugin Settings first."
)


def _status(e: Exception):
    return getattr(e, "http_status", None)


def _is_no_device(e: Exception) -> bool:
    msg = str(e)
    return ("NO_ACTIVE_DEVICE" in msg or "No active device" in msg
            or "Device not found" in msg)


def _pick_device(devices: list) -> str | None:
    """Active device first, then any non-restricted one, then whatever is there."""
    for d in devices:
        if d.get("is_active") and d.get("id"):
            return d["id"]
    for d in devices:
        if d.get("id") and not d.get("is_restricted"):
            return d["id"]
    for d in devices:
        if d.get("id"):
            return d["id"]
    return None


def _wake_device(sp) -> str | None:
    """Re-scan devices and (re)activate one via transfer_playback, without
    forcing playback to start. This is the automatic reconnection step used
    when Spotify reports that no device is active (app was closed/idle)."""
    devices = sp.devices().get("devices", [])
    dev = _pick_device(devices)
    if not dev:
        return None
    try:
        sp.transfer_playback(device_id=dev, force_play=False)
        time.sleep(0.6)               # give the device a moment to wake up
    except SpotifyException:
        pass                          # start_playback(device_id=…) may still work
    return dev


def _start(sp, **kwargs) -> str | None:
    """start_playback with device selection + one automatic reconnect/retry.
    Returns the device id that ended up being used."""
    device_id = _pick_device(sp.devices().get("devices", []))
    for attempt in range(2):
        try:
            sp.start_playback(device_id=device_id, **kwargs)
            return device_id
        except SpotifyException as e:
            if attempt == 0 and _is_no_device(e):
                device_id = _wake_device(sp)
                if device_id:
                    continue
            elif attempt == 0 and (_status(e) or 0) >= 500:
                time.sleep(0.8)      # transient Spotify-side hiccup
                continue
            raise
    return device_id


def _friendly_error(e: Exception) -> str:
    msg = str(e)
    status = _status(e)
    if _is_no_device(e):
        return ("Sir, Spotify has no active device — open Spotify once on this PC "
                "or your phone, then try again.")
    if "Premium" in msg or "PREMIUM_REQUIRED" in msg:
        return "Sir, controlling playback needs Spotify Premium."
    if status == 429:
        return "Sir, Spotify is rate-limiting me — give it a few seconds."
    if status == 401:
        return "Sir, the Spotify session expired — press CONNECT in Plugin Settings."
    return f"Sir, Spotify failed: {msg}"


# ═════════════════════════════════════════════════════════════════════════════
#  Small text helpers
# ═════════════════════════════════════════════════════════════════════════════

def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


_LEAD_RE = re.compile(
    r"^(?:(?:play|put on|listen to)\s+)+"
    r"(?:(?:a|algo de|some)\s+)?", re.I)


def _clean_query(q: str) -> str:
    return _LEAD_RE.sub("", q.strip()).strip(" .,'\"")


# Words that make a query a mood/genre/playlist request instead of a title.
_MOOD_HINTS = (
    "playlist", "lista de", "musica para", "music for", "ambiente", "mood",
    "concentr", "estudiar", "study", "focus", "relax", "chill", "dormir",
    "sleep", "gym", "workout", "entrenar", "fiesta", "party", "cenar",
    "lofi", "lo-fi", "synthwave", "jazz", "rock", "pop", "metal", "techno",
    "house", "clasica", "classical", "reggaeton", "hip hop", "hiphop", "rap",
    "blues", "indie", "ambient", "electronica", "electronic", "trance",
    "salsa", "flamenco", "country", "punk", "soul", "funk", "latin",
)


def _looks_like_mood(q: str) -> bool:
    n = _norm(q)
    ql = q.lower()
    # exact-ish single genre word, or an explicit "music for / playlist" phrase
    return any(_norm(h) in n for h in _MOOD_HINTS) and (
        len(q.split()) <= 4 or "para" in ql or " for " in ql or "playlist" in ql)


def _artists_str(item: dict) -> str:
    return ", ".join(a["name"] for a in item.get("artists", []))


# ═════════════════════════════════════════════════════════════════════════════
#  Radio queue
# ═════════════════════════════════════════════════════════════════════════════

_queue_gen  = 0                 # bumped on every new play → cancels stale fillers
_queue_lock = threading.Lock()


def _similar_fallback(sp, track: dict, want: int) -> list[str]:
    """Used when sp.recommendations() is unavailable. Spotify retired that
    endpoint for apps created after Nov-2024 (HTTP 404/403), so build a
    'radio' from endpoints that still exist: the artist's top tracks plus a
    search on the artist's genre."""
    uris: list[str] = []
    artists = track.get("artists") or []
    artist_id = artists[0].get("id") if artists else None
    if artist_id:
        try:
            tops = sp.artist_top_tracks(artist_id).get("tracks", [])
            uris += [t["uri"] for t in tops if t.get("uri")]
        except Exception:
            pass
        try:
            genres = sp.artist(artist_id).get("genres") or []
            if genres:
                res = sp.search(q=f'genre:"{genres[0]}"', type="track", limit=10)
                uris += [t["uri"] for t in res.get("tracks", {}).get("items", [])
                         if t and t.get("uri")]
        except Exception:
            pass
    if len(uris) < want and artists:
        try:
            res = sp.search(q=f'artist:"{artists[0]["name"]}"', type="track", limit=10)
            uris += [t["uri"] for t in res.get("tracks", {}).get("items", [])
                     if t and t.get("uri")]
        except Exception:
            pass
    return uris


def _radio_uris(sp, track: dict, limit: int = 15) -> list[str]:
    """Up to `limit` track URIs 'like' the seed track (seed itself excluded)."""
    seen = {track["uri"]}
    uris: list[str] = []
    try:
        recs = sp.recommendations(seed_tracks=[track["id"]], limit=limit)
        uris = [t["uri"] for t in recs.get("tracks", []) if t.get("uri")]
    except Exception:
        uris = []                       # deprecated / unavailable → fallback below
    if len(uris) < max(3, limit // 2):
        extra = _similar_fallback(sp, track, limit)
        random.shuffle(extra)
        uris += extra
    out: list[str] = []
    for u in uris:
        if u not in seen:
            seen.add(u)
            out.append(u)
        if len(out) >= limit:
            break
    return out


def _fill_queue(uris: list[str], device_id: str | None, gen: int) -> None:
    """Background worker: push URIs into the active queue one by one."""
    sp = _get_client()
    if sp is None:
        return
    time.sleep(0.5)                     # let the new track become 'current'
    for uri in uris:
        with _queue_lock:
            if gen != _queue_gen:       # a newer play() superseded this one
                return
        try:
            sp.add_to_queue(uri, device_id=device_id)
        except SpotifyException as e:
            if _status(e) == 429:
                time.sleep(2.0)
                try:
                    sp.add_to_queue(uri, device_id=device_id)
                except Exception:
                    return
            elif _is_no_device(e):
                return
            # any other error on a single track → skip it, keep going
        except Exception:
            return
        time.sleep(0.12)


def _queue_in_background(uris: list[str], device_id: str | None) -> None:
    global _queue_gen
    with _queue_lock:
        _queue_gen += 1
        gen = _queue_gen
    if uris:
        threading.Thread(target=_fill_queue, args=(uris, device_id, gen),
                         daemon=True).start()


def _cancel_queue_fill() -> None:
    global _queue_gen
    with _queue_lock:
        _queue_gen += 1


# ═════════════════════════════════════════════════════════════════════════════
#  The three playback modes
# ═════════════════════════════════════════════════════════════════════════════

def _search_track(sp, q: str) -> dict | None:
    items = sp.search(q=q, type="track", limit=1).get("tracks", {}).get("items", [])
    items = [i for i in items if i]
    return items[0] if items else None


def _search_artist(sp, q: str) -> dict | None:
    items = sp.search(q=q, type="artist", limit=5).get("artists", {}).get("items", [])
    items = [i for i in items if i]
    if not items:
        return None
    n = _norm(q)
    for it in items:                    # prefer an exact (normalised) name match
        if _norm(it.get("name", "")) == n:
            return it
    return items[0]


def _play_radio(sp, track: dict) -> str:
    """Mode 1 — RADIO: play the track, then queue 15 similar ones."""
    _cancel_queue_fill()
    device_id = _start(sp, uris=[track["uri"]])
    _queue_in_background(_radio_uris(sp, track, 15), device_id)
    return (f"Playing {track['name']} by {_artists_str(track)} on Spotify, "
            f"with similar tracks queued after it.")


def _play_artist(sp, artist: dict) -> str:
    """Mode 2 — ARTIST: start the artist context (continuous catalogue)."""
    _cancel_queue_fill()
    _start(sp, context_uri=artist["uri"])
    return f"Playing {artist['name']} on Spotify."


_genre_seeds_cache: list[str] | None = None


def _match_genre(sp, query: str) -> str:
    """Best Spotify genre seed for a free-text query (hyphen-slug fallback)."""
    global _genre_seeds_cache
    if _genre_seeds_cache is None:
        try:
            _genre_seeds_cache = sp.recommendation_genre_seeds().get("genres", [])
        except Exception:
            _genre_seeds_cache = []
    n = _norm(query)
    best = ""
    for g in _genre_seeds_cache:
        gn = _norm(g)
        if gn == n:
            return g
        if gn and gn in n and len(gn) > len(_norm(best)):
            best = g
    return best or re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")


def _play_mood(sp, query: str) -> str:
    """Mode 3 — GENRE / MOOD / PLAYLIST: best public playlist, else genre recs."""
    _cancel_queue_fill()
    try:
        res = sp.search(q=query, type="playlist", limit=5)
        playlists = [p for p in res.get("playlists", {}).get("items", []) if p and p.get("uri")]
    except SpotifyException:
        playlists = []
    for pl in playlists:
        try:
            _start(sp, context_uri=pl["uri"])
            return f"Playing the playlist {pl.get('name', query)} on Spotify."
        except SpotifyException as e:
            if _is_no_device(e) or _status(e) in (401, 403, 429):
                raise                   # not a bad-playlist problem → surface it
            continue                    # unplayable playlist → try the next one

    # ── fallback: recommendations by genre ──────────────────────────────────
    genre = _match_genre(sp, query)
    uris: list[str] = []
    try:
        recs = sp.recommendations(seed_genres=[genre], limit=20)
        uris = [t["uri"] for t in recs.get("tracks", []) if t.get("uri")]
    except Exception:
        uris = []
    if not uris:                        # endpoint retired → plain track search
        try:
            res = sp.search(q=f'genre:"{genre}"', type="track", limit=10)
            uris = [t["uri"] for t in res.get("tracks", {}).get("items", [])
                    if t and t.get("uri")]
            if not uris:
                res = sp.search(q=query, type="track", limit=10)
                uris = [t["uri"] for t in res.get("tracks", {}).get("items", [])
                        if t and t.get("uri")]
        except SpotifyException:
            uris = []
    if not uris:
        return f"Sir, I couldn't find any playlist or music for '{query}' on Spotify."
    random.shuffle(uris)
    _start(sp, uris=uris[:20])
    return f"Playing a {genre.replace('-', ' ')} mix on Spotify."


# ═════════════════════════════════════════════════════════════════════════════
#  Public API (names kept stable — plugin and HUD call these)
# ═════════════════════════════════════════════════════════════════════════════

def play(query: str = "", kind: str = "auto") -> str:
    """kind: 'auto' | 'track' | 'artist' | 'genre' (aliases: song, mood,
    playlist). With an empty query this just resumes playback."""
    sp = _get_client()
    if sp is None:
        return _NOT_CONNECTED
    try:
        query = _clean_query(query or "")
        kind  = (kind or "auto").strip().lower()
        kind  = {"song": "track", "mood": "genre", "playlist": "genre",
                 "ambient": "genre", "ambience": "genre"}.get(kind, kind)

        if not query:
            _start(sp)
            return "Resuming playback."

        if kind == "genre":
            return _play_mood(sp, query)

        if kind == "artist":
            artist = _search_artist(sp, query)
            if not artist:
                return f"Sir, I couldn't find an artist called '{query}' on Spotify."
            return _play_artist(sp, artist)

        if kind == "track":
            track = _search_track(sp, query)
            if not track:
                return f"Sir, I couldn't find anything matching '{query}' on Spotify."
            return _play_radio(sp, track)

        # ── auto ─────────────────────────────────────────────────────────────
        if _looks_like_mood(query):
            return _play_mood(sp, query)

        artist = _search_artist(sp, query)
        track  = _search_track(sp, query)
        n = _norm(query)
        artist_exact = bool(artist) and _norm(artist.get("name", "")) == n
        track_exact  = bool(track) and _norm(track.get("name", "")) == n

        if artist_exact and not (
                track_exact and track.get("popularity", 0) > artist.get("popularity", 0)):
            return _play_artist(sp, artist)
        if track:
            return _play_radio(sp, track)
        if artist:
            return _play_artist(sp, artist)
        return _play_mood(sp, query)    # nothing literal matched → treat as a mood
    except SpotifyException as e:
        return _friendly_error(e)
    except Exception as e:
        return _friendly_error(e)


def pause() -> str:
    sp = _get_client()
    if sp is None:
        return "Sir, Spotify isn't connected yet."
    try:
        sp.pause_playback()
        return "Paused."
    except Exception as e:
        return _friendly_error(e)


def resume() -> str:
    sp = _get_client()
    if sp is None:
        return "Sir, Spotify isn't connected yet."
    try:
        _start(sp)
        return "Resuming."
    except Exception as e:
        return _friendly_error(e)


def next_track() -> str:
    sp = _get_client()
    if sp is None:
        return "Sir, Spotify isn't connected yet."
    try:
        sp.next_track()
        return "Skipped to the next track."
    except SpotifyException as e:
        if _is_no_device(e):
            try:
                _wake_device(sp)
                sp.next_track()
                return "Skipped to the next track."
            except Exception as e2:
                return _friendly_error(e2)
        return _friendly_error(e)
    except Exception as e:
        return _friendly_error(e)


def previous_track() -> str:
    sp = _get_client()
    if sp is None:
        return "Sir, Spotify isn't connected yet."
    try:
        sp.previous_track()
        return "Back to the previous track."
    except SpotifyException as e:
        if _is_no_device(e):
            try:
                _wake_device(sp)
                sp.previous_track()
                return "Back to the previous track."
            except Exception as e2:
                return _friendly_error(e2)
        return _friendly_error(e)
    except Exception as e:
        return _friendly_error(e)


def seek(ms: int) -> str:
    """Jump to `ms` inside the current track (used by click-to-seek in the
    lyrics window)."""
    sp = _get_client()
    if sp is None:
        return "Sir, Spotify isn't connected yet."
    try:
        sp.seek_track(max(0, int(ms)))
        return "Seeking."
    except Exception as e:
        return _friendly_error(e)


def toggle_shuffle() -> str:
    sp = _get_client()
    if sp is None:
        return "Sir, Spotify isn't connected yet."
    try:
        cur = sp.current_playback() or {}
        new_state = not bool(cur.get("shuffle_state"))
        sp.shuffle(new_state)
        return "Shuffle on." if new_state else "Shuffle off."
    except Exception as e:
        return _friendly_error(e)


def now_playing() -> dict | None:
    """Polled every few seconds by the HUD mini-player. Returns None if
    nothing is playing, Spotify isn't configured, or the call fails for any
    reason — never raises, since a timer callback crashing would be far
    worse than the mini-player just staying hidden."""
    sp = _get_client()
    if sp is None:
        return None
    try:
        t0  = time.monotonic()
        cur = sp.current_playback()
        t1  = time.monotonic()
        if not cur or not cur.get("item"):
            return None
        item = cur["item"]
        images = (item.get("album", {}) or {}).get("images") or []
        return {
            "id":          item.get("id") or item.get("uri") or "",
            "album":       (item.get("album", {}) or {}).get("name", ""),
            # monotonic time at the middle of the request ≈ when Spotify
            # measured progress_ms → lets the lyrics window interpolate exactly
            "fetched_at":  (t0 + t1) / 2,
            "playing":     bool(cur.get("is_playing")),
            "title":       item.get("name", ""),
            "artist":      ", ".join(a["name"] for a in item.get("artists", [])),
            "album_art":   images[0].get("url", "") if images else "",
            "progress_ms": cur.get("progress_ms") or 0,
            "duration_ms": item.get("duration_ms") or 0,
            "shuffle":     bool(cur.get("shuffle_state")),
        }
    except Exception:
        return None
