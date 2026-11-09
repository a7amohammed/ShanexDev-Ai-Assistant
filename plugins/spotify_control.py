"""
Play, pause, resume, skip and rewind Spotify — entirely through the Spotify
Web API, so Spotify's own window never has to come to the foreground (or
even be open) on this PC. Requires Spotify Premium; free accounts can't be
remote-controlled this way, only played back locally in the Spotify app.

Setup (once): ⚙ → PLUGINS → SPOTIFY. Paste the Client ID and Client Secret
from an app you create at https://developer.spotify.com/dashboard (add
http://127.0.0.1:8734/callback as that app's Redirect URI), then hit
CONNECT — a browser tab opens once to grant access, after which the token is
cached and nothing further is needed.
"""
from plugins import _spotify_client as spotify

PLUGIN = {
    "name": "spotify_control",
    "description": (
        "Controls Spotify playback in the background — no window ever pops "
        "up on screen. Use for ANY music-related request: playing a song, "
        "artist, album, genre or playlist; pausing, resuming, skipping or "
        "going back. Also handles imprecise or natural phrasing such as: "
        "'put on some jazz', 'play something by Radiohead', 'I want to "
        "listen to chill music', 'next song', 'stop the music', 'can you "
        "play <anything> on Spotify'. Spotify only — NOT for YouTube. "
        "When the user says 'play music' or 'put something on' with no "
        "specific track, use action=play with an empty query to resume "
        "whatever was last paused, or pick a reasonable default search term "
        "based on context."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["play", "pause", "resume", "next", "previous", "shuffle", "lyrics"],
                "description": (
                    "Which playback action to take. Use 'play' for any "
                    "request involving a song, artist, album, genre or "
                    "playlist — even vague ones like 'put on some rock'. "
                    "Use 'resume' only when the user explicitly asks to "
                    "unpause/continue. Use 'pause' to stop playback. "
                    "Use 'next'/'previous' to skip tracks. "
                    "Use 'shuffle' to toggle shuffle on/off. "
                    "Use 'lyrics' whenever the user asks for the lyrics / "
                    "letra of a song, to sing along, or for karaoke: it opens "
                    "a floating, resizable window with the lyrics scrolling "
                    "in sync with the song. For the song that is playing "
                    "leave query empty; if the user names a song "
                    "('la letra de Bohemian Rhapsody') put it in query and "
                    "it will be played first."
                ),
            },
            "query": {
                "type": "STRING",
                "description": (
                    "What to search for on Spotify. Used with action=play. "
                    "Can be a song name, artist, album, genre (e.g. 'jazz', "
                    "'lo-fi hip hop', 'classical'), mood, or any combination "
                    "(e.g. 'upbeat songs by The Weeknd'). Leave empty only "
                    "when the user clearly wants to resume what was paused."
                ),
            },
            "kind": {
                "type": "STRING",
                "enum": ["auto", "track", "artist", "genre"],
                "description": (
                    "What the query is, so the right playback mode is used. "
                    "'track' = a specific song (plays it, then keeps playing "
                    "similar songs — radio mode). 'artist' = a band/singer "
                    "('pon AC/DC', 'escuchar a Queen' → query 'AC/DC' / "
                    "'Queen'): plays their catalogue continuously. 'genre' = "
                    "a genre, mood, activity or playlist ('synthwave', "
                    "'music to focus'): plays a matching playlist. "
                    "Use 'auto' (default) when unsure."
                ),
            },
        },
        "required": ["action"],
    },
}

PLUGIN_SETTINGS = {
    "namespace": "spotify",
    "title": "◈  SPOTIFY",
    "fields": [
        {"key": "client_id", "label": "Client ID", "type": "text",
         "placeholder": "from developer.spotify.com/dashboard"},
        {"key": "client_secret", "label": "Client Secret", "type": "password"},
    ],
    "action": {"label": "▸  CONNECT", "run": spotify.test_connection},
}


def _open_lyrics(query: str, kind: str, player) -> str:
    """Optionally start `query` first, then open the floating lyrics window
    (it follows whatever Spotify is playing and syncs by itself)."""
    msg = ""
    if query:
        msg = spotify.play(query, kind if kind != "auto" else "track")
        if msg.startswith("Sir,"):            # play failed → say why, no window
            return msg
    if player is None or not hasattr(player, "show_lyrics"):
        return (msg + " " if msg else "") + "Sir, the lyrics window isn't available in this build."
    player.show_lyrics()
    return (msg + " " if msg else "") + "Opening the lyrics window, sir."


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "").strip().lower()
    query  = (parameters.get("query") or "").strip()
    kind   = (parameters.get("kind") or "auto").strip().lower()
    try:
        if action == "play":
            return spotify.play(query, kind)
        if action == "pause":
            return spotify.pause()
        if action == "resume":
            return spotify.resume()
        if action == "next":
            return spotify.next_track()
        if action == "previous":
            return spotify.previous_track()
        if action == "shuffle":
            return spotify.toggle_shuffle()
        if action == "lyrics":
            return _open_lyrics(query, kind, player)
        return f"Sir, I don't recognise the Spotify action '{action}'."
    except Exception as e:
        return f"Sir, Spotify control failed: {e}"
