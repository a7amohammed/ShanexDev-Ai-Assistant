"""
Plugin: panel OSINT (Inteligencia de Fuentes Abiertas) en ventana HUD.
Se descubre solo al arrancar; la ventana vive en core/osint_hud_widget.py.
"""

PLUGIN = {
    "name": "osint_search",
    "description": (
        "Opens the HUD panel for OSINT (open-source intelligence) search: alias search across platforms, "
        "full name, and image with EXIF and geolocation. Call it when the user says "
        "'open the OSINT search', 'open the OSINT panel', 'open the intelligence module' or asks to "
        "investigate an alias/photo with open sources. If they give an alias, name or image path, "
        "pass them as parameters. Only for the user's own data or with authorisation; do not use it for "
        "general web searches (use web_search)."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "username": {"type": "STRING", "description": "Uno o varios alias separados por coma (opcional)"},
            "full_name": {"type": "STRING", "description": "Nombre completo a buscar (opcional)"},
            "image_path": {"type": "STRING", "description": "Ruta local de una imagen a analizar (opcional)"},
            "auto_start": {"type": "BOOLEAN", "description": "true only if the user asks to launch the scan right away"},
            "open_library": {"type": "BOOLEAN", "description": "true to open the library of saved profiles"},
        },
        "required": [],
    },
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    if player is None or not hasattr(player, "show_osint_panel"):
        return "The OSINT panel is not available in this interface."
    try:
        pre = {k: parameters.get(k) for k in ("username", "full_name", "image_path") if parameters.get(k)}
        pre["auto_start"] = bool(parameters.get("auto_start")) and len(pre) > 0
        if parameters.get("open_library"):
            pre["open_library"] = True
        player.show_osint_panel(pre)
        try:
            player.write_log("SYS: panel OSINT abierto")
        except Exception:
            pass
        return "Panel OSINT abierto. Confirmalo en una frase corta."
    except Exception as e:
        return f"Sir, no pude abrir el panel OSINT: {e}"
