"""
Synced-lyrics client for the floating lyrics window.

Source: LRCLIB (https://lrclib.net) — free, no API key, serves time-stamped
("LRC") lyrics. Leading underscore → the plugin loader skips this file, it is
a library (import it with `from plugins import _lyrics_client`).

fetch() never raises: it returns a dict
    {"status": "synced" | "plain" | "instrumental" | "notfound" | "error",
     "synced": [(ms, line), ...],   # only for "synced"
     "plain":  "text",              # only for "plain"
     "source": "lrclib"}
Results are cached in memory (not errors), so skipping back and forth between
tracks does not hit the network again.
"""
from __future__ import annotations

import re
import threading
from collections import OrderedDict

_API = "https://lrclib.net/api"
_HEADERS = {"User-Agent": "JARVIS-MarkLIV/1.0 (local desktop assistant)"}
_TIMEOUT = 7

_TS_RE = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")

# Suffixes Spotify appends to titles that LRCLIB usually doesn't have.
_NOISE_RE = re.compile(
    r"\s*[-–(\[]\s*(?:(?:remaster(?:ed)?|\d{4}\s*remaster(?:ed)?|live|mono|stereo|"
    r"single version|album version|radio edit|edit|deluxe|bonus|with)\b|feat\.|ft\.).*$",
    re.I)

_cache: "OrderedDict[tuple, dict]" = OrderedDict()
_cache_lock = threading.Lock()
_CACHE_MAX = 40


# ── helpers ──────────────────────────────────────────────────────────────────

def parse_lrc(text: str) -> list[tuple[int, str]]:
    """'[01:23.45] some text' → [(83450, 'some text')], sorted by time.
    Lines with several time tags are expanded; metadata tags ([ar:…]) skipped."""
    out: list[tuple[int, str]] = []
    for raw in (text or "").splitlines():
        tags = list(_TS_RE.finditer(raw))
        if not tags:
            continue
        body = _TS_RE.sub("", raw).strip()
        for m in tags:
            mm, ss, frac = m.groups()
            ms = int(mm) * 60000 + int(ss) * 1000 + int((frac or "0").ljust(3, "0")[:3])
            out.append((ms, body))
    out.sort(key=lambda t: t[0])
    return out


def _clean_title(title: str) -> str:
    return _NOISE_RE.sub("", title or "").strip() or (title or "")


def _first_artist(artist: str) -> str:
    return re.split(r",|&|\sfeat\.|\sft\.|\sx\s", artist or "", maxsplit=1)[0].strip()


def _from_record(rec: dict) -> dict | None:
    if not isinstance(rec, dict):
        return None
    if rec.get("instrumental"):
        return {"status": "instrumental", "source": "lrclib"}
    synced = parse_lrc(rec.get("syncedLyrics") or "")
    if synced:
        return {"status": "synced", "synced": synced, "source": "lrclib"}
    plain = (rec.get("plainLyrics") or "").strip()
    if plain:
        return {"status": "plain", "plain": plain, "source": "lrclib"}
    return None


def _pick_from_search(items: list, dur_s: int | None) -> dict | None:
    """Best of /search results: synced first, then closest duration."""
    def score(rec):
        has_sync = 0 if rec.get("syncedLyrics") else 1
        d = rec.get("duration")
        dd = abs(d - dur_s) if (dur_s and isinstance(d, (int, float))) else 999
        return (has_sync, dd)
    for rec in sorted((r for r in items if isinstance(r, dict)), key=score):
        res = _from_record(rec)
        if res:
            return res
    return None


# ── public ───────────────────────────────────────────────────────────────────

def fetch(title: str, artist: str, album: str = "", duration_ms: int = 0) -> dict:
    key = ((title or "").lower(), (artist or "").lower(), int((duration_ms or 0) / 2000))
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]

    try:
        import requests
    except ModuleNotFoundError:
        return {"status": "error", "source": "lrclib", "error": "pip install requests"}

    dur = round(duration_ms / 1000) if duration_ms else None
    ctitle, cartist = _clean_title(title), _first_artist(artist)

    attempts = [
        (title,  artist,  album, dur),
        (ctitle, cartist, album, dur),
        (ctitle, cartist, "",    dur),
    ]
    tried = set()
    try:
        for t, a, al, d in attempts:
            sig = (t, a, al, d)
            if not t or sig in tried:
                continue
            tried.add(sig)
            params = {"track_name": t, "artist_name": a}
            if al:
                params["album_name"] = al
            if d:
                params["duration"] = d
            r = requests.get(f"{_API}/get", params=params, headers=_HEADERS, timeout=_TIMEOUT)
            if r.status_code == 200:
                res = _from_record(r.json())
                if res:
                    return _store(key, res)
            elif r.status_code not in (404,):
                r.raise_for_status()

        r = requests.get(f"{_API}/search",
                         params={"track_name": ctitle, "artist_name": cartist},
                         headers=_HEADERS, timeout=_TIMEOUT)
        if r.status_code == 200:
            res = _pick_from_search(r.json(), dur)
            if res:
                return _store(key, res)
        elif r.status_code != 404:
            r.raise_for_status()
    except Exception as e:                    # network down, timeout, bad JSON…
        return {"status": "error", "source": "lrclib", "error": str(e)}

    return _store(key, {"status": "notfound", "source": "lrclib"})


def _store(key: tuple, res: dict) -> dict:
    with _cache_lock:
        _cache[key] = res
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return res
