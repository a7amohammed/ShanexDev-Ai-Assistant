"""
Shows a place on the HUD's interactive 3D globe from a spoken request
("Jarvis, show me the pyramids of Giza"). Geocodes the name to coordinates
via geopy/Nominatim (OpenStreetMap — free, no API key), then hands them to
the globe widget, which does the expand + orbit animation on its own.

The narration itself needs no special wiring: this action's return string is
what the rest of the app already speaks over TTS (see every other action in
this folder), and the globe's animation runs independently in Qt/JS — one
never waits on the other, so a slow geocode can't stall speech and a slow
TTS engine can't stall the globe.
"""

TOOL_NAME = "show_place_on_globe"


def show_place_action(parameters: dict, player=None, session_memory=None) -> str:
    query = (parameters.get("place") or "").strip()
    if not query:
        msg = "Sir, I need a place name to show on the globe."
        _log(msg, player)
        return msg

    try:
        from geopy.geocoders import Nominatim
    except ImportError:
        msg = "Sir, the globe's location lookup needs geopy — run: pip install geopy"
        _log(msg, player)
        return msg

    try:
        geolocator = Nominatim(user_agent="mark-liv-jarvis-globe")
        location = geolocator.geocode(query, timeout=8)
    except Exception as e:
        msg = f"Sir, I couldn't look up '{query}': {e}"
        _log(msg, player)
        return msg

    if location is None:
        msg = f"Sir, I couldn't find '{query}' on the map."
        _log(msg, player)
        return msg

    lat, lon = location.latitude, location.longitude
    display_name = query

    if player is not None:
        try:
            player.show_place(lat, lon, display_name)
        except Exception as e:
            _log(f"Globe display failed: {e}", player)

    msg = f"Here's {display_name} on the globe, sir."
    _log(msg, player)

    if session_memory:
        try:
            session_memory.set_last_search(query=f"globe: {query}", response=msg)
        except Exception:
            pass

    return msg


def _log(message: str, player=None) -> None:
    print(f"[Globe] {message}")
    if player:
        try:
            player.write_log(f"JARVIS: {message}")
        except Exception:
            pass


# ── Tool declaration (auto-discovered by core/action_loader.py) ─────────────
TOOL = {
    "name": TOOL_NAME,
    "description": (
        "Shows a place, landmark, city or monument on the HUD's interactive "
        "3D globe. Use for requests like 'show me the pyramids of Giza', "
        "'take me to Tokyo', 'where is Machu Picchu', 'show the Eiffel "
        "Tower on the globe', or any request to see/visit/locate a place "
        "visually. Speak a short, natural description of the place while "
        "this runs — the globe animates and orbits on its own."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "place": {
                "type": "STRING",
                "description": "The place, landmark or city to find and show, "
                                "e.g. 'Eiffel Tower', 'Kyoto, Japan', 'Machu Picchu'.",
            },
        },
        "required": ["place"],
    },
    "handler": show_place_action,
}
