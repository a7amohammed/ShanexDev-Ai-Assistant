"""
Voice market radar: quotes, favourites, pinned assets and control of the HUD
module (stocks and cryptocurrencies). It only reports data; it gives no investment
advice. The data is public and may be delayed.
"""
from core import market_data as md
from core import market_radar as mr

PLUGIN = {
    "name": "market_radar",
    "description": (
        "Stock-market and crypto radar on the HUD. Use for ANY request about the "
        "price or performance of a stock, company shares or cryptocurrency "
        "('how is Bitcoin doing', 'price of Tesla', 'how is Nvidia doing'), to show "
        "a chart on the HUD, to manage the user's favorites ('follow Ethereum', "
        "'add Apple to my favorites', 'remove Solana', 'pin Bitcoin', 'pin "
        "Tesla'), to show/hide an asset or the whole radar module, to list the "
        "favorites, to read the latest headlines of an asset, or to turn spoken "
        "proactive alerts on or off. Report data only — NEVER give buy/sell or "
        "investment advice. Do NOT use manage_monitor for crypto or stocks."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "enum": ["price", "chart", "news", "list", "add", "remove", "pin", "unpin",
                         "show", "hide", "alerts_on", "alerts_off", "module_on", "module_off",
                         "voice_alerts_on", "voice_alerts_off"],
                "description": (
                    "price = current price and daily change (also for assets that are not "
                    "favorites). chart = open the asset's chart on the HUD. news = latest "
                    "headlines. list = read the favorites. add = save to favorites (JARVIS "
                    "will then alert about important news). remove = delete from favorites. "
                    "pin/unpin = anchor the asset at the top of the HUD list. show/hide = "
                    "show or hide ONE favorite in the HUD (it stays saved). alerts_on/"
                    "alerts_off = proactive alerts for ONE asset. module_on/module_off = "
                    "show or hide the whole radar on the HUD. voice_alerts_on/"
                    "voice_alerts_off = all proactive spoken alerts."
                ),
            },
            "symbol": {
                "type": "STRING",
                "description": ("Company, ticker or crypto as the user said it: 'Tesla', "
                                "'TSLA', 'bitcoin', 'BTC'. Needed for every action except "
                                "list, module_* and voice_alerts_*."),
            },
            "range": {
                "type": "STRING",
                "enum": ["1D", "5D", "1M", "6M", "1Y", "5Y"],
                "description": "Chart range (action=chart). Default 1D.",
            },
        },
        "required": ["action"],
    },
}


def _ui(player, cmd, payload=None):
    try:
        if player is not None and hasattr(player, "radar_command"):
            player.radar_command(cmd, payload)
    except Exception:
        pass


def _fmt_quote(q: dict, name: str) -> str:
    px = md.format_price(q["price"], q["currency"])
    state = {"CLOSED": " (mercado cerrado)", "PRE": " (premercado)", "POST": " (postmercado)"}.get(
        q.get("market_state", ""), "")
    return (f"{name} ({md.pretty_symbol(q['symbol'])}): {px}, {q['change_pct']:+.2f}% hoy "
            f"(high {md.format_price(q['day_high'], q['currency'])}, "
            f"low {md.format_price(q['day_low'], q['currency'])}){state}. "
            "Public data, may be delayed.")


def run(parameters: dict, player=None, session_memory=None) -> str:
    action = (parameters.get("action") or "").strip().lower()
    text = (parameters.get("symbol") or "").strip()
    rng = (parameters.get("range") or "").upper()
    store = mr.get_store()

    # — acciones globales —
    if action == "module_on":
        _ui(player, "enable")
        return "Radar de mercados activado en el HUD."
    if action == "module_off":
        _ui(player, "disable")
        return "Radar de mercados oculto. Sigue disponible en Ajustes."
    if action in ("voice_alerts_on", "voice_alerts_off"):
        on = action.endswith("_on")
        store.set_alerts(voice=on)
        return ("Spoken radar alerts enabled: I will speak if there is important news about your favourites."
                if on else "Spoken radar alerts disabled.")
    if action == "list":
        items = store.assets()
        if not items:
            return "There are no favourites saved in the radar."
        parts = []
        for a in items:
            flags = [f for f, k in (("anclado", "pinned"), ("avisos", "alerts")) if a.get(k)]
            if not a.get("visible"):
                flags.append("oculto")
            parts.append(f"{a['name']} ({md.pretty_symbol(a['symbol'])})" + (f" [{', '.join(flags)}]" if flags else ""))
        return "Favoritos: " + "; ".join(parts) + "."

    if not text:
        return "Tell me which stock or cryptocurrency you mean."
    asset = store.get(text) or None
    if asset is None:
        found = md.resolve(text)
        if found is None:
            return f"I cannot find any stock or crypto called «{text}»."
        asset = store.get(found["symbol"]) or found
    sym, name = asset["symbol"], asset.get("name") or md.pretty_symbol(asset["symbol"])
    saved = store.get(sym) is not None

    if action == "price":
        q = md.fetch_quote(sym)
        if q is None:
            return f"I could not get the quote for {name} right now ({md.last_error() or 'no connection'})."
        return _fmt_quote(q, name)

    if action == "chart":
        if not saved:
            _ui(player, "preview", asset)
        _ui(player, "select", {"symbol": sym, "range": rng if rng in md.RANGES else None})
        q = md.fetch_quote(sym)
        base = f"Chart of {name} opened on the HUD."
        return base + (" " + _fmt_quote(q, name) if q else "")

    if action == "news":
        items = md.fetch_news(sym, name, 5)
        if not items:
            return f"No encuentro noticias recientes de {name}."
        return f"Latest headlines for {name}: " + " | ".join(
            f"{i['title']} ({i['publisher']})" if i["publisher"] else i["title"] for i in items[:4])

    if action == "add":
        if not saved and not asset.get("exchange") and md.fetch_quote(sym) is None:
            return (f"I could not verify that «{text}» exists (no data right now). "
                    "Tell me the exact ticker or try again in a moment.")
        store.add(sym, name, asset.get("kind", "stock"), alerts=True)
        _ui(player, "enable")
        return f"{name} saved to favourites. I will alert you if there is important news or strong moves."

    if not saved:
        return f"{name} is not in your favourites. Shall I add it?"

    if action == "remove":
        store.remove(sym)
        return f"{name} removed from favourites."
    flags = {"pin": ("pinned", True, "pinned to the top"), "unpin": ("pinned", False, "unpinned"),
             "show": ("visible", True, "visible on the HUD"), "hide": ("visible", False, "hidden from the HUD (still saved)"),
             "alerts_on": ("alerts", True, "with alerts enabled"),
             "alerts_off": ("alerts", False, "with alerts disabled")}
    if action in flags:
        key, val, msg = flags[action]
        store.set_flag(sym, key, val)
        return f"{name} {msg}."
    return f"Unknown action: {action}."
