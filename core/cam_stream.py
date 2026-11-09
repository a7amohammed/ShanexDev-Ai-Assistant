"""
DIRECT reading of a live camera, without a browser.

Why it exists: the QtWebEngine installed with `pip install PyQt6-WebEngine` does not ship the
proprietary codecs (H.264/AAC) and almost all webcams use them, so their pages
show "update your browser" and do not play. Here, instead of asking the browser to
play, we do what an external player would do:

    1. the camera page is downloaded and the real stream address (.m3u8) is looked up,
    2. that stream is opened with OpenCV (already a project dependency, with ffmpeg built in),
    3. the frames are handed to whoever paints them.

This module does not import Qt: ui.py (LiveCamWindow) uses it from a thread. No sound: OpenCV only
decodes video.

Stream lookup (`resolve_page`), in order:
    · .m3u8 written in the HTML (SkylineWebcams and most others),
    · EarthCam (html5_streamingdomain + html5_streampath),
    · YouTube (embedded in the page, or `youtube` from the catalogue): the HLS list is requested from yt-dlp
      if installed and, if not, `hlsManifestUrl` is read from the YouTube page itself,
    · iframes of the page (up to 2 hops),
    · if the page is an INDEX (country/city) or no longer exists (404), the listing is downloaded,
      the camera most similar to the catalogue name is chosen and that one is resolved.
"""
from __future__ import annotations

import difflib
import html as _htmllib
import os
import re
import threading
import time
import unicodedata
from urllib.parse import urljoin, urlparse

# No ';' or '|' : OPENCV_FFMPEG_CAPTURE_OPTIONS uses those two characters as separators.
UA = "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# Absolute addresses ("https://…/live.m3u8?a=…", with or without JSON-escaped slashes) and
# relative ones with a token ("live.m3u8?a=…").
_M3U8_RE = re.compile(
    r"""(?:https?:)?(?:\\?/){2}[^"'\s<>]+?\.m3u8[^"'\s<>]*|[A-Za-z0-9_\-./]+\.m3u8\?[^"'\s<>]+"""
)
_SKYLINE_BASE = "https://hd-auth.skylinewebcams.com/"


def _fix_skyline(u: str) -> str:
    """Skyline writes 'livee.m3u8?a=TOKEN' in its HTML, but the real stream is 'live.m3u8?a=TOKEN':
    the double 'e' is a decoy that serves a clip with the Skyline logo instead of the camera
    (exactly what was shown in the window). Their own player, and tools such as streamlink,
    correct it before opening it."""
    if "skylinewebcams.com" in u:
        u = re.sub(r"/livee(\.m3u8)", r"/live\1", u)
    return u

_env_lock = threading.Lock()


def find_stream_urls(page_url: str, html: str) -> list[str]:
    """All .m3u8 addresses that appear in the HTML, absolute and without duplicates."""
    out: list[str] = []
    for raw in _M3U8_RE.findall(html or ""):
        u = raw.replace("\\/", "/").replace("&amp;", "&").rstrip("\\,;)")
        if u.startswith("//"):
            u = "https:" + u
        elif not u.startswith("http"):
            base = _SKYLINE_BASE if "skylinewebcams" in page_url else page_url
            u = urljoin(base, u)
        u = _fix_skyline(u)
        if u not in out:
            out.append(u)
    # first those that look like the live stream (with a token), then the rest
    out.sort(key=lambda s: (0 if ("live" in s.lower() or "a=" in s) else 1))
    return out


def fetch_stream_urls(page_url: str, timeout: float = 15.0) -> list[str]:
    import requests
    r = requests.get(
        page_url, timeout=timeout,
        headers={"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9"},
    )
    r.raise_for_status()
    return find_stream_urls(page_url, r.text)


# ── extended resolver ───────────────────────────────────────────────────────────────────
# Consent cookies: without them, from the EU YouTube responds with the cookie screen
# instead of the live page.
_COOKIES = {"CONSENT": "YES+cb.20210328-17-p0.en+FX+917", "SOCS": "CAI"}
_HEADERS = {"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9"}
_AD_RE = re.compile(r"doubleclick|googlesyndication|adservice|/ads?/|facebook\.com|twitter\.com", re.I)
_YT_RE = re.compile(
    r"(?:youtube(?:-nocookie)?\.com/(?:embed/|watch\?v=|live/)|youtu\.be/)([A-Za-z0-9_-]{11})")
_YT_HLS_RE = re.compile(r'"hlsManifestUrl"\s*:\s*"([^"]+)"')
_EARTHCAM_RE = re.compile(
    r'"html5_streamingdomain"\s*:\s*"([^"]+)"\s*,\s*"html5_streampath"\s*:\s*"([^"]+)"')
_IFRAME_RE = re.compile(r'<iframe[^>]+src=["\']([^"\']+)["\']', re.I)
_HREF_RE = re.compile(r'<a[^>]+href=["\']([^"\'#?]+)["\'][^>]*>(.*?)</a>', re.I | re.S)


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _get(url: str, timeout: float = 15.0):
    import requests
    return requests.get(url, timeout=timeout, headers=_HEADERS, cookies=_COOKIES,
                        allow_redirects=True)


def _unesc(u: str) -> str:
    return u.replace("\\u0026", "&").replace("\\/", "/").replace("&amp;", "&")


def youtube_hls(video_id: str = "", channel: str = "") -> str | None:
    """HLS address (.m3u8) of a YouTube live stream, without a web player.

    1st yt-dlp (if installed: the most robust), 2nd `hlsManifestUrl` from the page.
    """
    page = (f"https://www.youtube.com/channel/{channel}/live" if channel
            else f"https://www.youtube.com/watch?v={video_id}")
    try:                                              # yt-dlp: opcional
        import yt_dlp
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True,
                               "noplaylist": True,
                               "format": "best[height<=720][protocol^=m3u8]/best[protocol^=m3u8]/best"}) as ydl:
            info = ydl.extract_info(page, download=False)
            if info and info.get("url"):
                return info["url"]
            for f in reversed((info or {}).get("formats") or []):
                if str(f.get("protocol", "")).startswith("m3u8"):
                    return f["url"]
    except ImportError:
        pass
    except Exception as e:  # noqa: BLE001
        print(f"[Cam] yt-dlp no pudo con {page}: {type(e).__name__}")
    try:
        body = _get(page).text
    except Exception as e:  # noqa: BLE001
        print(f"[Cam] no pude leer {page}: {type(e).__name__}")
        return None
    m = _YT_HLS_RE.search(body)
    return _unesc(m.group(1)) if m else None


def _streams_in_html(page_url: str, body: str) -> list[str]:
    """Direct streams that the page itself exposes (without jumping to another page)."""
    out = list(find_stream_urls(page_url, body))
    for dom, path in _EARTHCAM_RE.findall(body or ""):
        u = urljoin(dom.replace("\\/", "/") + "/", path.replace("\\/", "/").lstrip("/"))
        if u not in out:
            out.insert(0, u)
    return out


def _youtube_in_html(body: str) -> str | None:
    m = _YT_RE.search((body or "").replace("\\/", "/"))
    return m.group(1) if m else None


def _parent(url: str) -> str | None:
    """'Parent' page (…/country/city/camera.html → …/country/city.html) or None."""
    u = urlparse(url)
    html_style = u.path.endswith(".html")
    segs = [x for x in u.path.strip("/").split("/") if x]
    floor = 3 if "skylinewebcams" in u.netloc else 1      # /en/webcam/<country> is the minimum
    if len(segs) <= floor:
        return None
    segs = segs[:-1]
    if html_style:
        segs[-1] = re.sub(r"\.html$", "", segs[-1])
    return f"{u.scheme}://{u.netloc}/" + "/".join(segs) + (".html" if html_style else "/")


def _rank_links(page_url: str, body: str, hint: str) -> list[str]:
    """Links to 'child' cameras of an index page, the one most similar to `hint` first."""
    u = urlparse(page_url)
    base = re.sub(r"\.html$", "", u.path).rstrip("/") + "/"
    want = _norm(hint)
    scored: dict[str, float] = {}
    for href, label in _HREF_RE.findall(body or ""):
        full = urljoin(page_url, href)
        pu = urlparse(full)
        if pu.netloc != u.netloc or not pu.path.startswith(base) or pu.path == u.path:
            continue
        text = _norm(_htmllib.unescape(re.sub(r"<[^>]+>", " ", label)))
        slug = _norm(pu.path.rsplit("/", 1)[-1].replace(".html", ""))
        words = set(want.split())
        overlap = len(words & set((text + " " + slug).split())) / (len(words) or 1)
        score = max(difflib.SequenceMatcher(None, want, text).ratio() if text else 0,
                    difflib.SequenceMatcher(None, want, slug).ratio()) + overlap
        if full not in scored or score > scored[full]:
            scored[full] = score
    return [k for k, _ in sorted(scored.items(), key=lambda kv: -kv[1])]


def resolve_page(page_url: str, hint: str = "", fetch=None, _depth: int = 0,
                 _seen: set | None = None) -> list[str]:
    """List of likely stream addresses for `page_url` (may be empty)."""
    fetch = fetch or _get
    _seen = _seen if _seen is not None else set()
    if page_url in _seen or _depth > 3:
        return []
    _seen.add(page_url)
    try:
        r = fetch(page_url)
    except Exception as e:  # noqa: BLE001
        print(f"[Cam] no pude leer {page_url}: {type(e).__name__}")
        return []
    body = getattr(r, "text", "") or ""
    if getattr(r, "status_code", 200) >= 400:
        par = _parent(page_url)                       # the path changed: try the listing
        return resolve_page(par, hint, fetch, _depth + 1, _seen) if par else []

    found = _streams_in_html(page_url, body)
    if found:
        return found
    vid = _youtube_in_html(body)
    if vid:
        h = youtube_hls(video_id=vid)
        if h:
            return [h]
    for src in _IFRAME_RE.findall(body):                       # reproductor en un iframe
        src = urljoin(page_url, _htmllib.unescape(src))
        if not src.startswith("http") or _AD_RE.search(src) or src in _seen:
            continue
        res = resolve_page(src, hint, fetch, _depth + 1, _seen)
        if res:
            return res
    for link in _rank_links(page_url, body, hint)[:2]:         # index page → the camera
        res = resolve_page(link, hint, fetch, _depth + 1, _seen)
        if res:
            return res
    return []


def _origin(url: str) -> str:
    m = re.match(r"(https?://[^/]+)", url or "")
    return m.group(1) if m else ""


def read_frames(url: str, page_url: str | None, stop: threading.Event, on_frame,
                max_width: int = 960) -> bool:
    """Opens `url` and calls on_frame(rgb_bytes, width, height) for each frame, at its own pace.

    Returns True if it delivered at least one frame (False = it could not be opened or
    nothing arrived). Ends when `stop` is set, when the stream is cut or after 30 consecutive
    failed reads.
    """
    import cv2

    if page_url:
        # Referer/Origin: many cameras reject the stream if it does not look requested from their website.
        hdr = f"Referer: {page_url}\r\nOrigin: {_origin(page_url)}\r\n"
        opts = f"headers;{hdr}|user_agent;{UA}"
    else:
        opts = ""
    with _env_lock:     # the variable is global and OpenCV reads it on open: protect it
        if opts:
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = opts
        else:
            os.environ.pop("OPENCV_FFMPEG_CAPTURE_OPTIONS", None)
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        cap.release()
        return False

    got = False
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        period = 1.0 / (fps if 5.0 <= fps <= 60.0 else 25.0)
        nxt = time.time()
        fails = 0
        while not stop.is_set():
            ok, bgr = cap.read()
            if not ok:
                fails += 1
                if fails > 30:
                    break
                time.sleep(0.1)
                continue
            fails = 0
            h, w = bgr.shape[:2]
            if w > max_width:
                bgr = cv2.resize(bgr, (max_width, max(1, int(h * max_width / w))),
                                 interpolation=cv2.INTER_AREA)
                h, w = bgr.shape[:2]
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            on_frame(rgb.tobytes(), w, h)
            got = True
            nxt += period
            delay = nxt - time.time()
            if delay > 0:
                time.sleep(delay)
            else:
                nxt = time.time()       # vamos con retraso: no acumularlo
    finally:
        cap.release()
    return got


def run_stream(page_url: str, direct: str | None, stop: threading.Event, on_frame, on_fail,
               youtube: str = "", hint: str = "") -> None:
    """Complete thread: locate the stream and play it. Reports through on_fail(message).

    `direct`  = fixed .m3u8 from the catalogue; `youtube` = video id or "channel:UC…" from the catalogue;
    `hint`    = camera name + place (to choose the right one on an index page).
    """
    try:
        if direct:
            urls = [direct]
        elif youtube:
            yt = youtube.strip()
            h = youtube_hls(channel=yt[8:]) if yt.startswith("channel:") else youtube_hls(video_id=yt)
            urls = [h] if h else []
            if not urls:
                on_fail("no pude obtener el directo de YouTube (instala yt-dlp: pip install yt-dlp)")
                return
        else:
            urls = resolve_page(page_url, hint)
    except Exception as e:  # noqa: BLE001
        on_fail(f"could not read the page ({type(e).__name__})")
        return
    if not urls:
        on_fail("could not find the stream on the page")
        return
    for u in urls[:4]:
        if stop.is_set():
            return
        try:
            played = read_frames(u, None if direct and not direct.startswith("http") else page_url,
                                 stop, on_frame)
        except Exception as e:  # noqa: BLE001
            print(f"[Cam] failed reading {u[:80]}…: {e}")
            played = False
        if played:
            if not stop.is_set():
                on_fail("the stream was interrupted")
            return
    on_fail("the stream could not be opened")
