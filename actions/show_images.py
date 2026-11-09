"""
Shows images on the HUD itself instead of opening a browser tab.

Before this action existed, the model had no tool that actually *displayed*
a picture inside the app — so a request like "show me the Eiffel Tower" or
"show me the Mark LV" fell through to web_search / browser_control, which
only ever opens a results page. This gives it a dedicated tool: it looks the
query up via DuckDuckGo's image search, downloads a handful of results, and
hands the raw bytes to the UI, which opens each one as its own floating,
closable, downloadable window wired to the JARVIS core (see
FloatingImageWindow / show_floating_images in ui.py).
"""

TOOL_NAME = "show_images"


def _download(url: str, timeout: float = 6.0) -> bytes | None:
    try:
        import requests
        r = requests.get(
            url, timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (Jarvis HUD image fetch)"},
        )
        r.raise_for_status()
        if not r.content:
            return None
        return r.content
    except Exception:
        return None


def show_images_action(parameters: dict, player=None, session_memory=None) -> str:
    query = (parameters.get("query") or "").strip()
    try:
        count = int(parameters.get("count") or 3)
    except (TypeError, ValueError):
        count = 3
    count = max(1, min(count, 5))

    if not query:
        msg = "Sir, I need to know what to show you."
        _log(msg, player)
        return msg

    try:
        from actions.web_search import _get_ddgs
        DDGS = _get_ddgs()
    except Exception as e:
        msg = f"Sir, the image search backend is unavailable: {e}"
        _log(msg, player)
        return msg

    hits = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.images(query, max_results=count * 3):
                url = r.get("image") or r.get("thumbnail")
                if url:
                    hits.append({"url": url, "title": r.get("title") or query})
    except Exception as e:
        msg = f"Sir, the image search failed: {e}"
        _log(msg, player)
        return msg

    if not hits:
        msg = f"Sir, I couldn't find any images for '{query}'."
        _log(msg, player)
        return msg

    images: list[bytes] = []
    titles: list[str] = []
    for hit in hits:
        if len(images) >= count:
            break
        data = _download(hit["url"])
        if data:
            images.append(data)
            titles.append(hit["title"][:40] or query)

    if not images:
        msg = f"Sir, I found results for '{query}' but couldn't download any of them."
        _log(msg, player)
        return msg

    if player is not None:
        try:
            player.show_floating_images(images, titles)
        except Exception as e:
            _log(f"Floating image display failed: {e}", player)

    msg = f"Here you go, sir — {len(images)} image(s) for '{query}'."
    _log(msg, player)

    if session_memory:
        try:
            session_memory.set_last_search(query=f"images: {query}", response=msg)
        except Exception:
            pass

    return msg


def _log(message: str, player=None) -> None:
    print(f"[ShowImages] {message}")
    if player:
        try:
            player.write_log(f"JARVIS: {message}")
        except Exception:
            pass


# ── Tool declaration (auto-discovered by core/action_loader.py) ────────────
TOOL = {
    "name": TOOL_NAME,
    "description": (
        "Displays one or more images directly on the HUD, each as its own "
        "floating, closable, downloadable window wired to the JARVIS core. "
        "Use this — never web_search or browser_control — for ANY request "
        "to see, show, display or pull up a picture of something: 'show me "
        "the Eiffel Tower', 'show me a Bugatti', 'let me see what a "
        "pangolin looks like', 'pull up some pictures of the Mark LV'."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "query": {
                "type": "STRING",
                "description": "What to find and show images of.",
            },
            "count": {
                "type": "NUMBER",
                "description": "How many images to show (1-5). Defaults to 3.",
            },
        },
        "required": ["query"],
    },
    "handler": show_images_action,
}
