"""
osint_hud_widget.py  --  OSINT (Open-Source Intelligence) module for J.A.R.V.I.S.

PyQt6 HUD pop-up window with:
  * Dynamic user aliases (+ / X buttons drawn in code, no emojis)
  * Full name
  * Image: full EXIF + geolocation (EXIF GPS, visual inference with Gemini,
    reverse-search links)
  * Parallel alias scan (Sherlock style), image in separate threads (QThread)
  * Results as dossier cards with a preliminary map

For use only on your own data or with the authorisation of the person concerned.
It performs no facial recognition: visual analysis is limited to the surroundings (place).

Dependencias: PyQt6, Pillow, requests   (opcionales: google-genai, ddgs)

Standalone:   python osint_hud_widget.py
Integrado:    ver plugins/osint_search.py y el parche de ui.py
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import random
import re
import sys
import threading
import time
import urllib.parse
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

# ══════════════════════════════════════════════════════════════════════════════
#  BACKEND  (no Qt: it can be tested and reused separately)
# ══════════════════════════════════════════════════════════════════════════════

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) " \
     "Chrome/124.0 Safari/537.36"
NOMINATIM_UA = "JARVIS-OSINT-HUD/1.0 (personal research tool)"

FOUND, NOT_FOUND, UNKNOWN = "found", "not_found", "unknown"

# name, visible profile, mode, extra, probe (if different from the profile)
# modes: status (200 + alias in the final URL)  | status_api (200 / 400-404-410)
#        absent (200 and the text does NOT appear) | json_list (non-empty JSON under the key)
#        keybase (special response)
SITES: list[dict] = [
    {"n": "GitHub",      "u": "https://github.com/{}", "m": "status"},
    {"n": "GitLab",      "u": "https://gitlab.com/{}", "m": "json_list", "x": "",
     "p": "https://gitlab.com/api/v4/users?username={}"},
    {"n": "Keybase",     "u": "https://keybase.io/{}", "m": "keybase",
     "p": "https://keybase.io/_/api/1.0/user/lookup.json?usernames={}"},
    {"n": "Hacker News", "u": "https://news.ycombinator.com/user?id={}", "m": "absent",
     "x": "No such user."},
    {"n": "Reddit",      "u": "https://www.reddit.com/user/{}", "m": "status_api",
     "p": "https://www.reddit.com/user/{}/about.json"},
    {"n": "Dev.to",      "u": "https://dev.to/{}", "m": "status"},
    {"n": "Medium",      "u": "https://medium.com/@{}", "m": "status"},
    {"n": "Pinterest",   "u": "https://www.pinterest.com/{}/", "m": "status"},
    {"n": "SoundCloud",  "u": "https://soundcloud.com/{}", "m": "status"},
    {"n": "YouTube",     "u": "https://www.youtube.com/@{}", "m": "status"},
    {"n": "Vimeo",       "u": "https://vimeo.com/{}", "m": "status"},
    {"n": "Flickr",      "u": "https://www.flickr.com/people/{}/", "m": "status"},
    {"n": "Behance",     "u": "https://www.behance.net/{}", "m": "status"},
    {"n": "Dribbble",    "u": "https://dribbble.com/{}", "m": "status"},
    {"n": "DeviantArt",  "u": "https://www.deviantart.com/{}", "m": "status"},
    {"n": "Patreon",     "u": "https://www.patreon.com/{}", "m": "status"},
    {"n": "Replit",      "u": "https://replit.com/@{}", "m": "status"},
    {"n": "CodePen",     "u": "https://codepen.io/{}", "m": "status"},
    {"n": "npm",         "u": "https://www.npmjs.com/~{}", "m": "status"},
    {"n": "PyPI",        "u": "https://pypi.org/user/{}/", "m": "status"},
    {"n": "Docker Hub",  "u": "https://hub.docker.com/u/{}", "m": "status_api",
     "p": "https://hub.docker.com/v2/users/{}/"},
    {"n": "Wikipedia",   "u": "https://en.wikipedia.org/wiki/User:{}", "m": "status"},
    {"n": "Gravatar",    "u": "https://gravatar.com/{}", "m": "status_api",
     "p": "https://en.gravatar.com/{}.json"},
    {"n": "About.me",    "u": "https://about.me/{}", "m": "status"},
    {"n": "Mastodon.social", "u": "https://mastodon.social/@{}", "m": "status"},
    {"n": "Bluesky",     "u": "https://bsky.app/profile/{}.bsky.social", "m": "status_api",
     "p": "https://public.api.bsky.app/xrpc/app.bsky.actor.getProfile?actor={}.bsky.social"},
    {"n": "Chess.com",   "u": "https://www.chess.com/member/{}", "m": "status_api",
     "p": "https://api.chess.com/pub/player/{}"},
    {"n": "Lichess",     "u": "https://lichess.org/@/{}", "m": "status_api",
     "p": "https://lichess.org/api/user/{}"},
    {"n": "Last.fm",     "u": "https://www.last.fm/user/{}", "m": "status"},
    {"n": "Letterboxd",  "u": "https://letterboxd.com/{}/", "m": "status"},
    {"n": "Tumblr",      "u": "https://{}.tumblr.com", "m": "status", "nourl": True},
    {"n": "Itch.io",     "u": "https://{}.itch.io", "m": "status", "nourl": True},
    {"n": "Linktree",    "u": "https://linktr.ee/{}", "m": "status"},
    {"n": "Ko-fi",       "u": "https://ko-fi.com/{}", "m": "status"},
    {"n": "Kaggle",      "u": "https://www.kaggle.com/{}", "m": "status"},
    {"n": "Scratch",     "u": "https://scratch.mit.edu/users/{}/", "m": "status"},
    {"n": "Instructables", "u": "https://www.instructables.com/member/{}/", "m": "status"},
    {"n": "Pastebin",    "u": "https://pastebin.com/u/{}", "m": "status"},
    {"n": "Minecraft",   "u": "https://namemc.com/profile/{}", "m": "status_api",
     "p": "https://api.minecraftservices.com/minecraft/profile/lookup/name/{}"},
    {"n": "Duolingo",    "u": "https://www.duolingo.com/profile/{}", "m": "json_list", "x": "users",
     "p": "https://www.duolingo.com/2017-06-30/users?username={}"},
    {"n": "Wattpad",     "u": "https://www.wattpad.com/user/{}", "m": "status"},
    {"n": "Mixcloud",    "u": "https://www.mixcloud.com/{}/", "m": "status"},
    {"n": "Bandcamp",    "u": "https://bandcamp.com/{}", "m": "status"},
    {"n": "Genius",      "u": "https://genius.com/{}", "m": "status"},
    {"n": "Steam",       "u": "https://steamcommunity.com/id/{}", "m": "absent",
     "x": "The specified profile could not be found"},
]

_ALIAS_RE = re.compile(r"^[\w.\-]{2,40}$", re.UNICODE)


def clean_alias(raw: str) -> str:
    return (raw or "").strip().lstrip("@").strip()


def valid_alias(alias: str) -> bool:
    return bool(_ALIAS_RE.match(alias or ""))


@dataclass
class UserHit:
    alias: str
    platform: str
    url: str
    state: str = FOUND
    detail: str = ""


def _http_get(url: str, timeout: float = 8.0):
    import requests
    return requests.get(url, timeout=timeout, allow_redirects=True,
                        headers={"User-Agent": UA, "Accept-Language": "en,es;q=0.8"})


def check_site(site: dict, alias: str, timeout: float = 8.0) -> UserHit:
    q = urllib.parse.quote(alias, safe="")
    profile = site["u"].format(q)
    probe = site.get("p", site["u"]).format(q)
    hit = UserHit(alias, site["n"], profile, UNKNOWN)
    try:
        r = _http_get(probe, timeout)
    except Exception as e:
        hit.detail = type(e).__name__
        return hit
    code, mode = r.status_code, site["m"]
    if code in (403, 429, 503, 999):
        hit.detail = f"bloqueado ({code})"
        return hit
    try:
        if mode == "status":
            if code == 200:
                ok = site.get("nourl") or alias.lower() in r.url.lower()
                hit.state = FOUND if ok else NOT_FOUND
            elif code in (404, 410):
                hit.state = NOT_FOUND
            else:
                hit.detail = f"HTTP {code}"
        elif mode == "status_api":
            if code == 200:
                hit.state = FOUND
            elif code in (400, 404, 410):
                hit.state = NOT_FOUND
            else:
                hit.detail = f"HTTP {code}"
        elif mode == "absent":
            if code == 200:
                hit.state = NOT_FOUND if site["x"] in r.text else FOUND
            else:
                hit.detail = f"HTTP {code}"
        elif mode == "json_list":
            if code == 200:
                js = r.json()
                data = js.get(site["x"]) if site.get("x") else js
                hit.state = FOUND if data else NOT_FOUND
            else:
                hit.detail = f"HTTP {code}"
        elif mode == "keybase":
            if code == 200:
                them = (r.json() or {}).get("them") or []
                hit.state = FOUND if (them and them[0]) else NOT_FOUND
            else:
                hit.detail = f"HTTP {code}"
    except Exception as e:
        hit.state, hit.detail = UNKNOWN, f"respuesta no valida ({type(e).__name__})"
    return hit


def scan_usernames(aliases: list[str], on_hit=None, on_progress=None,
                   cancel: threading.Event | None = None, workers: int = 24,
                   sites: list[dict] | None = None) -> dict:
    """Parallel scan of aliases x platforms. Returns statistics."""
    sites = sites or SITES
    tasks = [(s, a) for a in aliases for s in sites]
    total, done = len(tasks), 0
    stats = {"total": total, "found": 0, "not_found": 0, "unknown": 0, "cancelled": False}
    if not tasks:
        return stats
    cancel = cancel or threading.Event()
    with ThreadPoolExecutor(max_workers=max(1, min(workers, total))) as ex:
        futs = [ex.submit(lambda s=s, a=a: None if cancel.is_set() else check_site(s, a))
                for s, a in tasks]
        for f in as_completed(futs):
            done += 1
            try:
                hit = f.result()
            except Exception:
                hit = None
            if hit is not None:
                stats[hit.state] += 1
                if hit.state == FOUND and on_hit:
                    on_hit(hit)
            if on_progress:
                on_progress(done, total)
    stats["cancelled"] = cancel.is_set()
    return stats


# ── Identity (full name) ───────────────────────────────────────────────

def identity_dork_links(full_name: str) -> list[tuple[str, str]]:
    q = urllib.parse.quote_plus(f'"{full_name}"')
    return [
        ("Google",    f"https://www.google.com/search?q={q}"),
        ("Bing",      f"https://www.bing.com/search?q={q}"),
        ("DuckDuckGo", f"https://duckduckgo.com/?q={q}"),
        ("LinkedIn",  f"https://www.linkedin.com/search/results/people/?keywords={urllib.parse.quote_plus(full_name)}"),
        ("Wikipedia", f"https://es.wikipedia.org/w/index.php?search={urllib.parse.quote_plus(full_name)}"),
        ("Google Scholar", f"https://scholar.google.com/scholar?q={q}"),
    ]


def search_identity(full_name: str, limit: int = 10) -> dict:
    """Public web search for the exact name. Namesakes may appear."""
    out = {"results": [], "links": identity_dork_links(full_name), "error": ""}
    DDGS = None
    try:
        from ddgs import DDGS  # type: ignore
    except Exception:
        try:
            from duckduckgo_search import DDGS  # type: ignore
        except Exception:
            out["error"] = "Install 'ddgs' to see results inside the panel (pip install ddgs)."
            return out
    try:
        with DDGS() as d:
            for r in d.text(f'"{full_name}"', max_results=limit):
                out["results"].append({"title": (r.get("title") or "").strip(),
                                       "url": r.get("href") or r.get("url") or "",
                                       "snippet": (r.get("body") or "").strip()})
    except Exception as e:
        out["error"] = f"Web search unavailable: {e}"
    return out


# ── EXIF ──────────────────────────────────────────────────────────────────────

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp", ".gif", ".heic", ".heif"}

_SKIP_TAGS = {"MakerNote", "PrintImageMatching", "ComponentsConfiguration", "FileSource",
              "SceneType", "ExifVersion", "FlashPixVersion", "InteropIndex"}
_LABELS = {
    "Make": "Manufacturer", "Model": "Camera model", "LensModel": "Lens",
    "LensMake": "Lens manufacturer", "Software": "Software",
    "DateTimeOriginal": "Capture date", "DateTimeDigitized": "Digitisation date",
    "DateTime": "Modification date", "OffsetTimeOriginal": "Time offset",
    "ExposureTime": "Exposure (s)", "FNumber": "Aperture (f)", "ISOSpeedRatings": "ISO",
    "PhotographicSensitivity": "ISO", "FocalLength": "Focal length (mm)",
    "FocalLengthIn35mmFilm": "35mm equivalent focal length", "Flash": "Flash", "Orientation": "Orientation",
    "WhiteBalance": "White balance", "ExposureProgram": "Exposure programme",
    "MeteringMode": "Metering", "BodySerialNumber": "Body serial no.",
    "LensSerialNumber": "Lens serial no.", "CameraOwnerName": "Owner",
    "Artist": "Author", "Copyright": "Copyright", "ImageDescription": "Description",
    "HostComputer": "Host computer", "XResolution": "X resolution", "YResolution": "Y resolution",
    "ExifImageWidth": "EXIF width", "ExifImageHeight": "EXIF height", "ColorSpace": "Colour space",
}


def _clean_val(v):
    try:
        from PIL.TiffImagePlugin import IFDRational
        if isinstance(v, IFDRational):
            f = float(v)
            return round(f, 5) if f != int(f) else int(f)
    except Exception:
        pass
    if isinstance(v, bytes):
        try:
            s = v.decode("utf-8").strip("\x00 ")
            if s.isprintable():
                return s[:200]
        except Exception:
            pass
        return f"<{len(v)} bytes>"
    if isinstance(v, (tuple, list)):
        return [_clean_val(x) for x in v][:12]
    if isinstance(v, str):
        return v.strip("\x00 ")[:200]
    if isinstance(v, float):
        return round(v, 5)
    return v


def _dms_to_deg(dms, ref) -> float | None:
    try:
        d, m, s = (float(x) for x in dms)
        val = d + m / 60.0 + s / 3600.0
        return -val if str(ref).upper() in ("S", "W") else val
    except Exception:
        return None


def extract_exif(path: str) -> dict:
    """Complete metadata. Never raises: returns {'error': ...} on failure."""
    from PIL import Image, ExifTags
    res: dict = {"file": {}, "tags": [], "gps": None, "stripped": False, "error": ""}
    p = Path(path)
    try:
        raw = p.read_bytes()
        res["file"] = {
            "File": p.name,
            "Size": f"{len(raw) / 1024:.1f} KB",
            "SHA-256": hashlib.sha256(raw).hexdigest(),
            "Modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime)),
        }
        img = Image.open(io.BytesIO(raw))
        res["file"]["Format"] = img.format or p.suffix.upper().strip(".")
        res["file"]["Dimensions"] = f"{img.width} x {img.height} px"
        exif = img.getexif()
    except Exception as e:
        res["error"] = f"The image could not be read: {e}"
        return res

    rows: dict[str, object] = {}

    def put(tag_id, value, table=ExifTags.TAGS):
        name = table.get(tag_id, f"Tag_{tag_id}")
        if name in _SKIP_TAGS:
            return
        rows[name] = _clean_val(value)

    for tid, val in exif.items():
        if tid in (0x8769, 0x8825, 0xA005):    # sub-IFDs se leen aparte
            continue
        put(tid, val)
    try:
        for tid, val in exif.get_ifd(0x8769).items():
            put(tid, val)
    except Exception:
        pass

    gps_raw = {}
    try:
        gps_raw = {ExifTags.GPSTAGS.get(k, str(k)): v for k, v in exif.get_ifd(0x8825).items()}
    except Exception:
        pass

    if gps_raw:
        lat = _dms_to_deg(gps_raw.get("GPSLatitude"), gps_raw.get("GPSLatitudeRef"))
        lon = _dms_to_deg(gps_raw.get("GPSLongitude"), gps_raw.get("GPSLongitudeRef"))
        if lat is not None and lon is not None and (abs(lat) > 0.0001 or abs(lon) > 0.0001):
            g = {"lat": lat, "lon": lon}
            try:
                alt = float(gps_raw.get("GPSAltitude"))
                g["alt"] = -alt if gps_raw.get("GPSAltitudeRef") in (1, b"\x01") else alt
            except Exception:
                pass
            for key, out in (("GPSImgDirection", "direction"), ("GPSSpeed", "speed")):
                try:
                    g[out] = float(gps_raw[key])
                except Exception:
                    pass
            res["gps"] = g
        for k, v in gps_raw.items():
            if k in ("GPSLatitude", "GPSLongitude"):
                continue
            rows[k] = _clean_val(v)

    tags = []
    for name, val in rows.items():
        label = _LABELS.get(name, re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name))
        tags.append((label, str(val)))
    res["tags"] = tags
    res["stripped"] = not tags
    return res


# ── Geocoding / map ────────────────────────────────────────────────────

_geo_lock = threading.Lock()
_geo_last = [0.0]


def _nominatim(path: str, params: dict) -> dict | list | None:
    import requests
    with _geo_lock:                              # politica de uso: max 1 peticion/s
        wait = 1.1 - (time.time() - _geo_last[0])
        if wait > 0:
            time.sleep(wait)
        _geo_last[0] = time.time()
    try:
        r = requests.get("https://nominatim.openstreetmap.org/" + path, params=params,
                         headers={"User-Agent": NOMINATIM_UA}, timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def reverse_geocode(lat: float, lon: float) -> dict:
    d = _nominatim("reverse", {"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 18,
                               "accept-language": "en"})
    if not isinstance(d, dict) or "address" not in d:
        return {}
    a = d["address"]
    return {"display": d.get("display_name", ""),
            "country": a.get("country", ""),
            "region": a.get("state") or a.get("region") or "",
            "city": a.get("city") or a.get("town") or a.get("village") or a.get("municipality") or "",
            "road": a.get("road", ""), "postcode": a.get("postcode", "")}


def forward_geocode(query: str) -> tuple[float, float] | None:
    d = _nominatim("search", {"format": "jsonv2", "q": query, "limit": 1, "accept-language": "en"})
    if isinstance(d, list) and d:
        try:
            return float(d[0]["lat"]), float(d[0]["lon"])
        except Exception:
            return None
    return None


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def _deg2tile(lat: float, lon: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def zoom_for_radius(radius_km: float | None) -> int:
    r = radius_km if radius_km is not None else 1
    for limit, z in ((0.5, 17), (2, 15), (8, 13), (40, 11), (200, 9), (800, 6)):
        if r <= limit:
            return z
    return 4


def build_map(lat: float, lon: float, zoom: int = 13) -> dict | None:
    """Composes 3x3 OSM tiles into a dark cyan-tinted map. Returns
    {'png': bytes, 'x': fraction, 'y': fraction} or None when offline."""
    from PIL import Image, ImageOps, ImageChops
    import requests
    fx, fy = _deg2tile(lat, lon, zoom)
    cx, cy = int(fx), int(fy)
    n = 2 ** zoom
    canvas = Image.new("RGB", (768, 768), (8, 11, 16))
    got = 0
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            tx, ty = (cx + dx) % n, cy + dy
            if ty < 0 or ty >= n:
                continue
            try:
                r = requests.get(f"https://tile.openstreetmap.org/{zoom}/{tx}/{ty}.png",
                                 headers={"User-Agent": NOMINATIM_UA}, timeout=8)
                if r.status_code == 200:
                    tile = Image.open(io.BytesIO(r.content)).convert("RGB")
                    canvas.paste(tile, ((dx + 1) * 256, (dy + 1) * 256))
                    got += 1
            except Exception:
                continue
    if not got:
        return None
    dark = ImageOps.invert(canvas)
    dark = ImageChops.multiply(dark, Image.new("RGB", dark.size, (70, 190, 230)))
    dark = ImageOps.autocontrast(dark, cutoff=1)
    buf = io.BytesIO()
    dark.save(buf, "PNG")
    px, py = (fx - (cx - 1)) * 256, (fy - (cy - 1)) * 256
    return {"png": buf.getvalue(), "x": px / 768.0, "y": py / 768.0, "zoom": zoom}


# ── Visual location inference (Gemini) ───────────────────────────────────

_GEO_PROMPT = """You are an image geolocation analyst (GEOINT) in an open-source investigation.
Determine WHERE this photograph was taken using ONLY the surroundings: architecture, vegetation, terrain,
climate, signage and legible lettering (language, alphabet, licence plates, driving direction),
poles, street furniture, asphalt, recognisable monuments or landmarks, position of the sun.
Do NOT identify any person and do not attempt to recognise faces.
Be honest about uncertainty: if there are not enough clues, lower the confidence and widen the radius.
Reply ONLY with valid JSON (no extra text, no markdown) in this form:
{"country": "", "region": "", "city": "", "place": "name of the place or landmark, if any",
 "lat": 0.0, "lon": 0.0, "confidence": 0-100, "radius_km": 0.0,
 "clues": ["specific visual clue 1", "clue 2"],
 "landmarks": ["identified landmark"],
 "alternatives": [{"place": "", "confidence": 0-100}],
 "reasoning": "brief reasoning"}
If you cannot estimate coordinates, set lat and lon to null."""


def _gemini_key() -> str:
    k = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if k:
        return k
    here = Path(__file__).resolve().parent
    for base in (here, here.parent):
        f = base / "config" / "api_keys.json"
        if f.exists():
            try:
                return json.loads(f.read_text(encoding="utf-8")).get("gemini_api_key", "") or ""
            except Exception:
                pass
    return ""


def _parse_json(raw: str):
    if not raw:
        return None
    if "{" in raw and "}" in raw:
        raw = raw[raw.find("{"): raw.rfind("}") + 1]
    try:
        return json.loads(raw)
    except Exception:
        return None


def _vision_json(prompt: str, jpeg: bytes) -> tuple[dict | None, str]:
    try:
        from google.genai import types as gt
    except Exception:
        return None, "The google-genai package is missing (pip install google-genai)."
    part = gt.Part.from_bytes(data=jpeg, mime_type="image/jpeg")
    try:
        from core import gemini as g            # inside the project
    except Exception:
        g = None
    if g is not None:
        data = g.as_json([part, prompt], tier=g.SMART, timeout_ms=60000)
        return (data, "") if isinstance(data, dict) else (None, "El modelo no devolvio un JSON valido.")
    key = _gemini_key()
    if not key:
        return None, "No Gemini key (GEMINI_API_KEY variable or config/api_keys.json)."
    try:
        from google import genai
        cl = genai.Client(api_key=key)
        last = ""
        for model in ("gemini-2.5-flash", "gemini-2.5-flash-lite"):
            try:
                r = cl.models.generate_content(model=model, contents=[part, prompt])
                data = _parse_json(getattr(r, "text", "") or "")
                if isinstance(data, dict):
                    return data, ""
                last = "Invalid response."
            except Exception as e:
                last = str(e)[:140]
        return None, last or "No response from the model."
    except Exception as e:
        return None, f"Error de Gemini: {e}"


def infer_location_visual(path: str) -> dict:
    """Estimates the place of the photo from visual clues. Never raises."""
    from PIL import Image, ImageOps
    try:
        img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        img.thumbnail((1600, 1600))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=88)               # re-encode: no EXIF is sent to the API
    except Exception as e:
        return {"error": f"The image could not be prepared: {e}"}
    data, err = _vision_json(_GEO_PROMPT, buf.getvalue())
    if not data:
        return {"error": err or "No result."}

    def num(v):
        try:
            return float(v)
        except Exception:
            return None

    out = {
        "country": str(data.get("country") or ""), "region": str(data.get("region") or ""),
        "city": str(data.get("city") or ""), "place": str(data.get("place") or ""),
        "lat": num(data.get("lat")), "lon": num(data.get("lon")),
        "confidence": max(0, min(100, int(num(data.get("confidence")) or 0))),
        "radius_km": num(data.get("radius_km")),
        "clues": [str(c) for c in (data.get("clues") or [])][:10],
        "landmarks": [str(c) for c in (data.get("landmarks") or [])][:6],
        "alternatives": [a for a in (data.get("alternatives") or []) if isinstance(a, dict)][:4],
        "reasoning": str(data.get("reasoning") or ""), "error": "",
    }
    if out["lat"] is None or out["lon"] is None or (out["lat"] == 0 and out["lon"] == 0):
        q = ", ".join(x for x in (out["place"], out["city"], out["region"], out["country"]) if x)
        pt = forward_geocode(q) if q else None
        out["lat"], out["lon"] = (pt if pt else (None, None))
    return out


def reverse_image_links() -> list[tuple[str, str]]:
    """Reverse-search services (the image must be uploaded manually).
    PimEyes u otros buscadores faciales quedan fuera a proposito."""
    return [("Google Lens", "https://lens.google.com/"),
            ("TinEye", "https://tineye.com/"),
            ("Yandex Imagenes", "https://yandex.com/images/"),
            ("Bing Visual Search", "https://www.bing.com/visualsearch")]


def osm_link(lat: float, lon: float, zoom: int = 15) -> str:
    return f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map={zoom}/{lat:.6f}/{lon:.6f}"


# ══════════════════════════════════════════════════════════════════════════════
#  HUD INTERFACE  (PyQt6, everything drawn with QPainter: zero emojis)
# ══════════════════════════════════════════════════════════════════════════════
import html as _html

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (QColor, QConicalGradient, QDesktopServices, QFont, QFontMetricsF,
                         QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap)
from PyQt6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                             QPlainTextEdit, QScrollArea, QSizePolicy, QStackedWidget,
                             QVBoxLayout, QWidget)

try:                                    # integrado:  core.osint_hud_widget
    from core import osint_profiles as OP
except ImportError:                     # standalone: python osint_hud_widget.py
    import osint_profiles as OP

BG = "#080b10"
CYAN = "#00d4ff"
CYAN_DIM = "#0a6c85"
AMBER = "#ffaa00"
LINE = "#12445a"
TEXT = "#bfeeff"
TEXT_DIM = "#5f8fa0"
GREEN = "#00ff9c"


def qc(hex_: str, alpha: int = 255) -> QColor:
    c = QColor(hex_)
    c.setAlpha(alpha)
    return c


def mono(size: int = 9, bold: bool = False) -> QFont:
    f = QFont()
    f.setFamilies(["Consolas", "Courier New", "DejaVu Sans Mono", "monospace"])
    f.setPointSize(size)
    f.setBold(bold)
    return f


def spaced(font: QFont, px: float = 1.2) -> QFont:
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, px)
    return font


def fit_font(text: str, width: float, pt: int = 9, bold: bool = True, spacing: float = 1.4,
             min_pt: int = 6) -> QFont:
    """Largest font (<= pt) in which `text` fits in `width`; if none does, the minimum."""
    f = spaced(mono(pt, bold), spacing)
    while pt > min_pt and QFontMetricsF(f).horizontalAdvance(text) * 1.04 > width:
        pt -= 1
        f = spaced(mono(pt, bold), spacing)
    return f


def elide(text: str, font: QFont, width: float) -> str:
    """Truncates with '...' instead of letting the text be cut off mid-way."""
    return QFontMetricsF(font).elidedText(text, Qt.TextElideMode.ElideRight, max(0.0, width))


def soft_wrap(s: str, n: int = 26) -> str:
    """Allows long strings (hashes, URLs) to break inside a label."""
    return re.sub(r"(\S{%d})" % n, "\\1\u200b", str(s))


def bevel_path(r: QRectF, c: float = 12.0, c2: float | None = None) -> QPainterPath:
    """Rectangle with bevelled corners (top-left and bottom-right)."""
    c2 = c if c2 is None else c2
    p = QPainterPath()
    p.moveTo(r.left() + c, r.top())
    p.lineTo(r.right(), r.top())
    p.lineTo(r.right(), r.bottom() - c2)
    p.lineTo(r.right() - c2, r.bottom())
    p.lineTo(r.left(), r.bottom())
    p.lineTo(r.left(), r.top() + c)
    p.closeSubpath()
    return p


def draw_icon(p: QPainter, kind: str, r: QRectF, color: QColor, width: float = 1.7) -> None:
    """Iconos vectoriales: plus, cross, minus, upload, arrow, pin, copy, target."""
    p.save()
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    c = r.center()
    s = min(r.width(), r.height()) / 2.0
    L = lambda x1, y1, x2, y2: p.drawLine(QPointF(c.x() + x1, c.y() + y1), QPointF(c.x() + x2, c.y() + y2))
    if kind == "plus":
        L(-s, 0, s, 0)
        L(0, -s, 0, s)
    elif kind == "minus":
        L(-s, 0, s, 0)
    elif kind == "cross":
        L(-s, -s, s, s)
        L(-s, s, s, -s)
    elif kind == "upload":
        L(0, s * 0.55, 0, -s)
        L(0, -s, -s * 0.6, -s * 0.35)
        L(0, -s, s * 0.6, -s * 0.35)
        L(-s, s, s, s)
    elif kind == "arrow":
        L(-s * 0.8, s * 0.8, s * 0.8, -s * 0.8)
        L(s * 0.1, -s * 0.8, s * 0.8, -s * 0.8)
        L(s * 0.8, -s * 0.8, s * 0.8, -s * 0.1)
    elif kind == "pin":
        path = QPainterPath()
        path.moveTo(c.x(), c.y() + s)
        path.cubicTo(c.x() - s * 1.2, c.y() - s * 0.1, c.x() - s * 0.9, c.y() - s,
                     c.x(), c.y() - s)
        path.cubicTo(c.x() + s * 0.9, c.y() - s, c.x() + s * 1.2, c.y() - s * 0.1,
                     c.x(), c.y() + s)
        p.drawPath(path)
        p.drawEllipse(QPointF(c.x(), c.y() - s * 0.35), s * 0.28, s * 0.28)
    elif kind == "copy":
        p.drawRect(QRectF(c.x() - s, c.y() - s * 0.4, s * 1.2, s * 1.4))
        p.drawRect(QRectF(c.x() - s * 0.2, c.y() - s, s * 1.2, s * 1.4))
    elif kind == "target":
        p.drawEllipse(c, s * 0.7, s * 0.7)
        L(-s, 0, -s * 0.35, 0)
        L(s * 0.35, 0, s, 0)
        L(0, -s, 0, -s * 0.35)
        L(0, s * 0.35, 0, s)
    p.restore()


# ── Widgets basicos ───────────────────────────────────────────────────────────

class IconButton(QWidget):
    clicked = pyqtSignal()

    def __init__(self, kind: str, color: str = CYAN, size: int = 26, tooltip: str = ""):
        super().__init__()
        self._kind, self._color, self._hover = kind, color, False
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = bevel_path(r, 5, 5)
        p.fillPath(path, qc(self._color, 60 if self._hover else 16))
        p.setPen(QPen(qc(self._color, 255 if self._hover else 150), 1))
        p.drawPath(path)
        draw_icon(p, self._kind, r.adjusted(7, 7, -7, -7), qc(self._color))


class HudButton(QWidget):
    clicked = pyqtSignal()

    def __init__(self, text: str, accent: str = "cyan", primary: bool = False, height: int = 34):
        super().__init__()
        self._text, self._accent, self._primary, self._hover = text, accent, primary, False
        self.setFixedHeight(height)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def setText(self, t: str):
        self._text = t
        self.update()

    def setPrimary(self, on: bool):
        self._primary = on
        self.update()

    def setAccent(self, accent: str):
        self._accent = accent
        self.update()

    def setEnabled(self, on: bool):
        super().setEnabled(on)
        self.setCursor(Qt.CursorShape.PointingHandCursor if on else Qt.CursorShape.ArrowCursor)
        self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if self.isEnabled() and e.button() == Qt.MouseButton.LeftButton \
                and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        col = AMBER if self._accent == "amber" else CYAN
        en = self.isEnabled()
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = bevel_path(r, 9, 9)
        fill = 80 if (self._hover and en) else (42 if self._primary else 12)
        p.fillPath(path, qc(col, fill if en else 8))
        p.setPen(QPen(qc(col, 255 if en else 70), 1.2 if self._primary else 1))
        p.drawPath(path)
        room = r.width() - 20                      # margin for the bevels
        f = fit_font(self._text, room, 9, True, 1.4, 7)
        p.setFont(f)
        p.setPen(qc(col, 255 if en else 80))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, elide(self._text, f, room))


class HudLineEdit(QLineEdit):
    def __init__(self, placeholder: str = ""):
        super().__init__()
        self.setPlaceholderText(placeholder)
        self.setFont(mono(10))
        self.setMinimumHeight(34)
        self.setStyleSheet(
            f"QLineEdit{{background:rgba(0,212,255,14);color:{TEXT};border:none;"
            f"border-bottom:1px solid {LINE};padding:7px 8px;"
            f"selection-background-color:{CYAN_DIM};selection-color:#ffffff;}}"
            f"QLineEdit:focus{{border-bottom:1px solid {CYAN};background:rgba(0,212,255,26);}}")


def section_label(num: str, text: str) -> QLabel:
    lb = QLabel(f"{num} // {text}")
    lb.setFont(spaced(mono(8, True), 1.6))
    lb.setStyleSheet(f"color:{CYAN};background:transparent;")
    return lb


def dim_label(text: str, wrap: bool = True) -> QLabel:
    lb = QLabel(text)
    lb.setFont(mono(8))
    lb.setWordWrap(wrap)
    lb.setStyleSheet(f"color:{TEXT_DIM};background:transparent;")
    return lb


class AliasRow(QWidget):
    removed = pyqtSignal(object)
    submitted = pyqtSignal()

    def __init__(self, text: str = ""):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.tag = QLabel("01")
        self.tag.setFont(mono(8, True))
        self.tag.setFixedWidth(20)
        self.tag.setStyleSheet(f"color:{TEXT_DIM};background:transparent;")
        self.edit = HudLineEdit("alias / username")
        self.edit.setText(text)
        self.edit.returnPressed.connect(self.submitted)
        self.btn = IconButton("cross", AMBER, 26, "Remove alias")
        self.btn.clicked.connect(lambda: self.removed.emit(self))
        lay.addWidget(self.tag)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.btn)


class AliasList(QWidget):
    MAX = 10
    submitted = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(6)
        self._rows: list[AliasRow] = []
        self._rows_box = QVBoxLayout()
        self._rows_box.setSpacing(6)
        self._lay.addLayout(self._rows_box)
        foot = QHBoxLayout()
        foot.setSpacing(8)
        self._add = IconButton("plus", CYAN, 26, "Anadir otro alias")
        self._add.clicked.connect(lambda: self.add_row())
        self._cnt = dim_label("", False)
        foot.addWidget(self._add)
        foot.addWidget(self._cnt, 1)
        self._lay.addLayout(foot)
        self.add_row()

    def add_row(self, text: str = "") -> AliasRow | None:
        if len(self._rows) >= self.MAX:
            return None
        row = AliasRow(text)
        row.removed.connect(self._remove)
        row.submitted.connect(self.submitted)
        self._rows.append(row)
        self._rows_box.addWidget(row)
        self._refresh()
        row.edit.setFocus()
        return row

    def _remove(self, row: AliasRow) -> None:
        if len(self._rows) <= 1:               # el ultimo no se elimina: se vacia
            row.edit.clear()
            return
        self._rows.remove(row)
        self._rows_box.removeWidget(row)
        row.deleteLater()
        self._refresh()

    def _refresh(self) -> None:
        for i, r in enumerate(self._rows, 1):
            r.tag.setText(f"{i:02d}")
        self._cnt.setText(f"ADD ALIAS  [{len(self._rows)}/{self.MAX}]")
        self._add.setVisible(len(self._rows) < self.MAX)

    def values(self) -> list[str]:
        return [r.edit.text() for r in self._rows]

    def set_values(self, vals: list[str]) -> None:
        while len(self._rows) > 1:
            self._remove(self._rows[-1])
        self._rows[0].edit.setText(vals[0] if vals else "")
        for v in vals[1:]:
            self.add_row(v)


class DropZone(QWidget):
    changed = pyqtSignal(str)

    def __init__(self, height: int = 150, hint: str = "DRAG AN IMAGE HERE"):
        super().__init__()
        self._hint = hint
        self.setAcceptDrops(True)
        self.setFixedHeight(height)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._path = ""
        self._pix: QPixmap | None = None
        self._over = False
        self._clear = IconButton("cross", AMBER, 22, "Remove image")
        self._clear.setParent(self)
        self._clear.clicked.connect(lambda: self.set_path(""))
        self._clear.hide()

    def path(self) -> str:
        return self._path

    def set_path(self, path: str) -> bool:
        if not path:
            self._path, self._pix = "", None
            self._clear.hide()
            self.update()
            self.changed.emit("")
            return True
        if Path(path).suffix.lower() not in IMAGE_EXTS or not Path(path).is_file():
            return False
        pix = QPixmap(path)
        self._path = path
        self._pix = pix if not pix.isNull() else None
        self._clear.show()
        self._clear.raise_()
        self.update()
        self.changed.emit(path)
        return True

    def resizeEvent(self, e):
        self._clear.move(self.width() - self._clear.width() - 6, 6)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            exts = " ".join("*" + x for x in sorted(IMAGE_EXTS))
            f, _ = QFileDialog.getOpenFileName(self, "Select image", "",
                                               f"Images ({exts});;All (*)")
            if f:
                self.set_path(f)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            self._over = True
            self.update()
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._over = False
        self.update()

    def dropEvent(self, e):
        self._over = False
        for u in e.mimeData().urls():
            if u.isLocalFile() and self.set_path(u.toLocalFile()):
                break
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = bevel_path(r, 12, 12)
        col = AMBER if self._over else CYAN
        p.fillPath(path, qc(col, 30 if self._over else 12))
        pen = QPen(qc(col, 200 if self._over else 120), 1)
        if not self._pix:
            pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawPath(path)
        if self._pix:
            area = r.adjusted(10, 10, -10, -22)
            scaled = self._pix.scaled(int(area.width()), int(area.height()),
                                      Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation)
            x = area.center().x() - scaled.width() / 2
            y = area.top() + (area.height() - scaled.height()) / 2
            p.drawPixmap(int(x), int(y), scaled)
            ir = QRectF(x, y, scaled.width(), scaled.height())
            p.setPen(QPen(qc(CYAN), 1.6))
            k = 9
            for cx, cy, dx, dy in ((ir.left(), ir.top(), 1, 1), (ir.right(), ir.top(), -1, 1),
                                   (ir.left(), ir.bottom(), 1, -1), (ir.right(), ir.bottom(), -1, -1)):
                p.drawLine(QPointF(cx, cy), QPointF(cx + dx * k, cy))
                p.drawLine(QPointF(cx, cy), QPointF(cx, cy + dy * k))
            p.setFont(mono(7))
            p.setPen(qc(TEXT_DIM))
            name = Path(self._path).name
            p.drawText(QRectF(r.left() + 8, r.bottom() - 18, r.width() - 16, 14),
                       Qt.AlignmentFlag.AlignCenter,
                       name if len(name) < 40 else name[:18] + "..." + name[-18:])
        else:
            top = max(8.0, (r.height() - 104) / 2)          # bloque icono + textos centrado
            draw_icon(p, "upload", QRectF(r.center().x() - 16, r.top() + top, 32, 32), qc(col), 2)
            hf = fit_font(self._hint, r.width() - 20, 8, True, 1.2, 6)
            p.setFont(hf)
            p.setPen(qc(col))
            p.drawText(QRectF(r.left() + 10, r.top() + top + 40, r.width() - 20, 20),
                       Qt.AlignmentFlag.AlignCenter, elide(self._hint, hf, r.width() - 20))
            sf = fit_font("or click to select", r.width() - 20, 7, False, 0, 6)
            p.setFont(sf)
            p.setPen(qc(TEXT_DIM))
            p.drawText(QRectF(r.left() + 10, r.top() + top + 62, r.width() - 20, 18),
                       Qt.AlignmentFlag.AlignCenter, "or click to select")
            p.drawText(QRectF(r.left() + 10, r.top() + top + 80, r.width() - 20, 18),
                       Qt.AlignmentFlag.AlignCenter, "JPG  PNG  WEBP  TIFF")


class RadarWidget(QWidget):
    def __init__(self, size: int = 112):
        super().__init__()
        self.setFixedSize(size, size)
        self._a = 90.0
        self._active = False
        self._blips: list[list[float]] = []      # [angulo, radio, vida]
        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)

    def set_active(self, on: bool) -> None:
        self._active = on
        if on:
            self._t.start(33)
        else:
            self._t.stop()
            self._blips.clear()
        self.update()

    def ping(self) -> None:
        self._blips.append([random.uniform(0, 360), random.uniform(0.2, 0.9), 1.0])

    def _tick(self) -> None:
        self._a = (self._a - 5.0) % 360
        for b in self._blips:
            b[2] -= 0.018
        self._blips = [b for b in self._blips if b[2] > 0]
        if random.random() < 0.06:
            self.ping()
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        R = self.width() / 2 - 4
        col = AMBER if self._active else CYAN
        p.setBrush(qc(BG, 200))
        p.setPen(QPen(qc(col, 200), 1.2))
        p.drawEllipse(c, R, R)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qc(col, 70), 1))
        for k in (0.33, 0.66):
            p.drawEllipse(c, R * k, R * k)
        p.drawLine(QPointF(c.x() - R, c.y()), QPointF(c.x() + R, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - R), QPointF(c.x(), c.y() + R))
        for i in range(0, 360, 30):                       # edge tick marks
            a = math.radians(i)
            p.drawLine(QPointF(c.x() + math.cos(a) * (R - 4), c.y() - math.sin(a) * (R - 4)),
                       QPointF(c.x() + math.cos(a) * R, c.y() - math.sin(a) * R))
        if self._active:
            g = QConicalGradient(c, self._a)
            g.setColorAt(0.0, qc(col, 170))
            g.setColorAt(0.16, qc(col, 0))
            g.setColorAt(1.0, qc(col, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawEllipse(c, R, R)
            a = math.radians(self._a)
            p.setPen(QPen(qc(col), 1.6))
            p.drawLine(c, QPointF(c.x() + math.cos(a) * R, c.y() - math.sin(a) * R))
            for ang, rad, life in self._blips:
                a2 = math.radians(ang)
                pt = QPointF(c.x() + math.cos(a2) * R * rad, c.y() - math.sin(a2) * R * rad)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(qc(CYAN, int(255 * life)))
                p.drawEllipse(pt, 2.6, 2.6)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qc(col))
        p.drawEllipse(c, 2.2, 2.2)


class ScanLine(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(4)
        self._pos = 0.0
        self._active = False
        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)

    def set_active(self, on: bool) -> None:
        self._active = on
        self._t.start(25) if on else self._t.stop()
        self.update()

    def _tick(self) -> None:
        self._pos = (self._pos + 0.012) % 1.3
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        w, h = self.width(), self.height()
        p.fillRect(0, h // 2, w, 1, qc(LINE))
        if self._active:
            x = (self._pos - 0.15) * w
            g = QLinearGradient(x - w * 0.15, 0, x + w * 0.15, 0)
            g.setColorAt(0.0, qc(AMBER, 0))
            g.setColorAt(0.5, qc(AMBER, 255))
            g.setColorAt(1.0, qc(AMBER, 0))
            p.fillRect(int(x - w * 0.15), 0, int(w * 0.3), h, g)


class ConfBar(QWidget):
    def __init__(self, value: int):
        super().__init__()
        self._v = max(0, min(100, int(value)))
        self.setFixedHeight(16)

    def paintEvent(self, e):
        p = QPainter(self)
        col = CYAN if self._v >= 60 else AMBER
        n, gap, w = 24, 2, self.width() - 52
        seg = (w - gap * (n - 1)) / n
        for i in range(n):
            on = (i + 1) / n <= self._v / 100.0 + 0.0001
            p.fillRect(QRectF(i * (seg + gap), 2, seg, 12), qc(col, 220 if on else 28))
        p.setFont(mono(8, True))
        p.setPen(qc(col))
        p.drawText(QRectF(w + 6, 0, 46, 16), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   f"{self._v}%")


class MapPreview(QWidget):
    """Preliminary map: darkened OSM tiles if online; otherwise a schematic grid."""

    def __init__(self, lat: float, lon: float):
        super().__init__()
        self._lat, self._lon = lat, lon
        self._pix: QPixmap | None = None
        self._mx = self._my = 0.5
        self._ph = 0.0
        self.setMinimumHeight(220)
        self.setMaximumHeight(240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)

    def showEvent(self, e):
        self._t.start(50)

    def hideEvent(self, e):
        self._t.stop()

    def _tick(self):
        self._ph = (self._ph + 0.05) % 1.0
        self.update()

    def set_map(self, data: dict | None) -> None:
        if data and data.get("png"):
            pix = QPixmap()
            if pix.loadFromData(data["png"]):
                self._pix, self._mx, self._my = pix, data["x"], data["y"]
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        r = QRectF(0, 0, w, h).adjusted(1, 1, -1, -1)
        path = bevel_path(r, 12, 12)
        p.setClipPath(path)
        p.fillRect(self.rect(), qc("#050a0f"))
        if self._pix:
            pw = self._pix.width()
            cw = min(pw, 440.0)
            ch = cw * h / w
            mx, my = self._mx * pw, self._my * pw
            left = max(0.0, min(pw - cw, mx - cw / 2))
            top = max(0.0, min(pw - ch, my - ch / 2))
            p.drawPixmap(QRectF(0, 0, w, h), self._pix, QRectF(left, top, cw, ch))
            x, y = (mx - left) / cw * w, (my - top) / ch * h
            note = ""
        else:
            p.setPen(QPen(qc(CYAN, 40), 1))
            for i in range(0, 13):
                p.drawLine(QPointF(i * w / 12, 0), QPointF(i * w / 12, h))
            for i in range(0, 7):
                p.drawLine(QPointF(0, i * h / 6), QPointF(w, i * h / 6))
            x = (self._lon + 180.0) / 360.0 * w
            y = (90.0 - self._lat) / 180.0 * h
            note = "SCHEMATIC MAP  /  NO TILES"
        pt = QPointF(x, y)
        p.setPen(QPen(qc(CYAN, 150), 1))
        p.drawLine(QPointF(0, y), QPointF(w, y))
        p.drawLine(QPointF(x, 0), QPointF(x, h))
        rad = 8 + 22 * self._ph
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qc(AMBER, int(255 * (1 - self._ph))), 1.6))
        p.drawEllipse(pt, rad, rad)
        p.setPen(QPen(qc(AMBER), 1.6))
        p.drawEllipse(pt, 5, 5)
        p.setBrush(qc(AMBER))
        p.drawEllipse(pt, 2, 2)
        p.setClipping(False)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qc(CYAN, 200), 1))
        p.drawPath(path)
        p.fillRect(QRectF(8, h - 26, 190, 18), qc(BG, 215))
        p.setFont(mono(8, True))
        p.setPen(qc(AMBER))
        p.drawText(QRectF(12, h - 26, 190, 18), Qt.AlignmentFlag.AlignVCenter,
                   f"{self._lat:.5f}, {self._lon:.5f}")
        if note:
            p.setFont(mono(7))
            p.setPen(qc(TEXT_DIM))
            p.drawText(QRectF(0, 6, w - 10, 14), Qt.AlignmentFlag.AlignRight, note)


# ── Dossier cards ──────────────────────────────────────────────────────

class DossierCard(QWidget):
    _count = 0

    def __init__(self, title: str, accent: str = CYAN):
        super().__init__()
        DossierCard._count += 1
        self._title, self._accent = title, accent
        self._tag = f"EXP-{DossierCard._count:04d}"
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 40, 16, 14)
        self.body.setSpacing(6)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = bevel_path(r, 14, 14)
        p.fillPath(path, qc(self._accent, 12))
        p.setPen(QPen(qc(self._accent, 150), 1))
        p.drawPath(path)
        p.fillRect(QRectF(16, 15, 8, 8), qc(self._accent))
        show_tag = r.width() >= 420                       # on narrow cards the title takes priority
        tag_w = 96 if show_tag else 0
        avail = r.width() - 32 - 14 - tag_w
        tf = fit_font(self._title, avail, 9, True, 1.8, 7)
        p.setFont(tf)
        p.setPen(qc(self._accent))
        p.drawText(QRectF(32, 8, avail, 22), Qt.AlignmentFlag.AlignVCenter,
                   elide(self._title, tf, avail))
        if show_tag:
            p.setFont(mono(7))
            p.setPen(qc(TEXT_DIM))
            p.drawText(QRectF(r.width() - 110, 8, 100, 22),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, self._tag)
        p.setPen(QPen(qc(self._accent, 60), 1))
        p.drawLine(QPointF(14, 32), QPointF(r.width() - 14, 32))

    # content helpers
    def add_kv(self, key: str, value: str, color: str = TEXT) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        k = QLabel(key.upper())
        k.setFont(spaced(mono(7, True), 0.8))
        k.setFixedWidth(150)
        k.setWordWrap(True)
        k.setAlignment(Qt.AlignmentFlag.AlignTop)
        k.setStyleSheet(f"color:{TEXT_DIM};background:transparent;")
        v = QLabel(soft_wrap(value))
        v.setFont(mono(9))
        v.setWordWrap(True)
        v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.setStyleSheet(f"color:{color};background:transparent;")
        h.addWidget(k)
        h.addWidget(v, 1)
        self.body.addWidget(row)
        return row

    def add_sub(self, text: str, color: str = CYAN) -> None:
        lb = QLabel(text)
        lb.setFont(spaced(mono(8, True), 1.4))
        lb.setStyleSheet(f"color:{color};background:transparent;padding-top:8px;")
        self.body.addWidget(lb)

    def add_note(self, text: str, color: str = TEXT_DIM) -> QLabel:
        lb = QLabel(soft_wrap(text, 40))
        lb.setFont(mono(8))
        lb.setWordWrap(True)
        lb.setStyleSheet(f"color:{color};background:transparent;")
        self.body.addWidget(lb)
        return lb

    def add_link(self, left: str, text: str, url: str, left_color: str = CYAN) -> None:
        lb = QLabel(f"<span style='color:{left_color};font-weight:bold'>{_html.escape(left)}</span>"
                    f"&nbsp;&nbsp;<a href='{_html.escape(url, quote=True)}' "
                    f"style='color:{TEXT};text-decoration:none'>{_html.escape(soft_wrap(text, 38))}</a>")
        lb.setFont(mono(9))
        lb.setWordWrap(True)
        lb.setOpenExternalLinks(True)
        lb.setStyleSheet("background:transparent;")
        self.body.addWidget(lb)


class UsernameCard(DossierCard):
    def __init__(self, aliases: list[str]):
        super().__init__("USERS  /  PLATFORM FOOTPRINT", CYAN)
        self.add_kv("Alias objetivo", ", ".join(aliases))
        self._prog = self.add_note(f"Scanning {len(SITES)} platforms by alias...", AMBER)
        self._hits = 0
        self._last_alias = ""

    def add_hit(self, hit: UserHit) -> None:
        if hit.alias != self._last_alias:
            self.add_sub(f"ALIAS  {hit.alias}")
            self._last_alias = hit.alias
        self.add_link(hit.platform, hit.url, hit.url)
        self._hits += 1

    def set_progress(self, done: int, total: int) -> None:
        self._prog.setText(f"Progress {done}/{total}  /  matches: {self._hits}")

    def finish(self, stats: dict) -> None:
        if "error" in stats:
            self._prog.setText(f"Error: {stats['error']}")
            return
        txt = (f"Completed  /  {stats['found']} match(es)  /  {stats['not_found']} no profile  /  "
               f"{stats['unknown']} unconfirmed (blocked or network error)")
        if stats.get("cancelled"):
            txt = "ABORTED  /  " + txt
        self._prog.setText(txt)
        self._prog.setStyleSheet(f"color:{GREEN if stats['found'] else AMBER};background:transparent;")
        if not stats["found"]:
            self.add_note("No matches on the platforms checked.", AMBER)
        self.add_note("A match only indicates that a profile with that alias exists; it does not guarantee that "
                      "it belongs to the person being searched for.")


def build_identity_card(data: dict, name: str) -> DossierCard:
    card = DossierCard("IDENTITY  /  PUBLIC SOURCES", CYAN)
    card.add_kv("Name searched", name)
    if data.get("results"):
        card.add_sub("WEB MATCHES (exact name)")
        for r in data["results"]:
            card.add_link("»", r["title"] or r["url"], r["url"])
            if r.get("snippet"):
                card.add_note(r["snippet"][:180])
        card.add_note("Namesakes may appear: cross-check each result before accepting it as valid.", AMBER)
    elif data.get("error"):
        card.add_note(data["error"], AMBER)
    card.add_sub("MANUAL SEARCH ON")
    for n, u in data.get("links", []):
        card.add_link("»", n, u)
    return card


def build_exif_card(exif: dict) -> DossierCard:
    card = DossierCard("IMAGE  /  EXIF METADATA", CYAN)
    if exif.get("error"):
        card.add_note(exif["error"], AMBER)
        return card
    for k, v in exif["file"].items():
        card.add_kv(k, v)
    gps = exif.get("gps")
    if gps:
        card.add_sub("GPS EMBEDDED IN THE IMAGE", AMBER)
        card.add_kv("Coordinates", f"{gps['lat']:.6f}, {gps['lon']:.6f}", AMBER)
        if "alt" in gps:
            card.add_kv("Altitud", f"{gps['alt']:.1f} m")
        if "direction" in gps:
            card.add_kv("Camera heading", f"{gps['direction']:.1f} degrees")
    if exif["stripped"]:
        card.add_note("The image contains no EXIF tags: they were probably removed by the "
                      "originating platform or by a privacy tool.", AMBER)
    else:
        card.add_sub("TAGS")
        shown = exif["tags"][:48]
        for k, v in shown:
            card.add_kv(k, v)
        if len(exif["tags"]) > len(shown):
            card.add_note(f"+ {len(exif['tags']) - len(shown)} additional tags not shown.")
    return card


class GeoCard(DossierCard):
    def __init__(self, report: dict):
        super().__init__("GEOLOCATION  /  LOCATION ANALYSIS", AMBER)
        self.map: MapPreview | None = None
        self.coords: tuple[float, float] | None = None
        gps, place, vis = report.get("gps"), report.get("place") or {}, report.get("visual") or {}
        zoom = 15
        if gps:
            self.coords = (gps["lat"], gps["lon"])
            self.add_sub("SOURCE: EXIF GPS  (high reliability)", GREEN)
            self.add_kv("Coordinates", f"{gps['lat']:.6f}, {gps['lon']:.6f}", AMBER)
            if place.get("display"):
                self.add_kv("Address", place["display"])
            for k, v in (("Country", place.get("country")), ("Region", place.get("region")),
                         ("City", place.get("city"))):
                if v:
                    self.add_kv(k, v)
        if vis:
            self.add_sub("VISUAL INFERENCE  (AI, unverified estimate)", AMBER)
            if vis.get("error"):
                self.add_note(vis["error"], AMBER)
            else:
                where = ", ".join(x for x in (vis["place"], vis["city"], vis["region"], vis["country"]) if x)
                self.add_kv("Estimated place", where or "undetermined")
                if vis.get("lat") is not None:
                    self.add_kv("Coordinates", f"{vis['lat']:.5f}, {vis['lon']:.5f}", AMBER)
                    if not self.coords:
                        self.coords = (vis["lat"], vis["lon"])
                        zoom = zoom_for_radius(vis.get("radius_km"))
                row = QWidget()
                h = QHBoxLayout(row)
                h.setContentsMargins(0, 0, 0, 0)
                h.setSpacing(10)
                k = QLabel("CONFIDENCE")
                k.setFont(spaced(mono(7, True), 0.8))
                k.setFixedWidth(150)
                k.setStyleSheet(f"color:{TEXT_DIM};background:transparent;")
                h.addWidget(k)
                h.addWidget(ConfBar(vis["confidence"]), 1)
                self.body.addWidget(row)
                if vis.get("radius_km") is not None:
                    self.add_kv("Error radius", f"~{vis['radius_km']:.1f} km")
                if vis["landmarks"]:
                    self.add_kv("Landmarks", "; ".join(vis["landmarks"]))
                if vis["clues"]:
                    self.add_kv("Visual clues", "\n".join("- " + c for c in vis["clues"]))
                alts = [f"{a.get('place', '?')} ({a.get('confidence', '?')}%)" for a in vis["alternatives"]]
                if alts:
                    self.add_kv("Alternatives", "; ".join(alts))
                if vis["reasoning"]:
                    self.add_kv("Reasoning", vis["reasoning"])
        mm = report.get("mismatch_km")
        if mm is not None:
            self.add_kv("EXIF / visual cross-check", f"{mm:.0f} km difference",
                        GREEN if mm < 50 else AMBER)
        if self.coords:
            self.map = MapPreview(*self.coords)
            self.map_zoom = zoom
            self.body.addSpacing(6)
            self.body.addWidget(self.map)
            row = QHBoxLayout()
            row.setSpacing(8)
            b1 = HudButton("OPEN IN OPENSTREETMAP", "cyan")
            b1.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(osm_link(*self.coords, zoom))))
            b2 = HudButton("COPY COORDINATES", "amber")
            b2.clicked.connect(lambda: QGuiApplication.clipboard().setText(
                f"{self.coords[0]:.6f}, {self.coords[1]:.6f}"))
            row.addWidget(b1)
            row.addWidget(b2)
            self.body.addLayout(row)
        else:
            self.add_note("It was not possible to determine a location: the image has no GPS and the "
                          "visual clues are not enough.", AMBER)
        self.add_note("Visual geolocation is probabilistic. Always verify with independent "
                      "sources (satellite, street view, signage).")


def build_reverse_card() -> DossierCard:
    card = DossierCard("IMAGE  /  REVERSE SEARCH", CYAN)
    card.add_note("Open the service and upload the image manually to find other appearances.")
    for n, u in reverse_image_links():
        card.add_link("»", n, u)
    return card


# ── Worker threads ──────────────────────────────────────────────────────────

_KEEP: set = set()          # avoids destroying a QThread that is still running


class _Worker(QThread):
    def __init__(self):
        super().__init__()
        self.cancel = threading.Event()
        _KEEP.add(self)
        self.finished.connect(lambda: _KEEP.discard(self))


class UsernameWorker(_Worker):
    hit = pyqtSignal(object)
    progress = pyqtSignal(int, int)
    done = pyqtSignal(object)

    def __init__(self, aliases: list[str]):
        super().__init__()
        self.aliases = aliases

    def run(self):
        try:
            stats = scan_usernames(self.aliases, self.hit.emit, self.progress.emit, self.cancel)
        except Exception as e:
            stats = {"error": str(e)}
        self.done.emit(stats)


class IdentityWorker(_Worker):
    done = pyqtSignal(object)

    def __init__(self, name: str):
        super().__init__()
        self.name = name

    def run(self):
        try:
            self.done.emit(search_identity(self.name))
        except Exception as e:
            self.done.emit({"results": [], "links": identity_dork_links(self.name), "error": str(e)})


class ImageWorker(_Worker):
    exif_ready = pyqtSignal(object)
    geo_ready = pyqtSignal(object)
    map_ready = pyqtSignal(object)
    status = pyqtSignal(str)
    done = pyqtSignal(object)

    def __init__(self, path: str):
        super().__init__()
        self.path = path

    def run(self):
        exif, vis = {}, {}
        try:
            with ThreadPoolExecutor(max_workers=2) as ex:      # EXIF e inferencia a la vez
                futs = {ex.submit(extract_exif, self.path): "exif",
                        ex.submit(infer_location_visual, self.path): "vis"}
                for f in as_completed(futs):
                    try:
                        val = f.result()
                    except Exception as e:
                        val = {"error": str(e)}
                    if futs[f] == "exif":
                        exif = val
                        self.exif_ready.emit(val)
                    else:
                        vis = val
                        self.status.emit("Inferencia visual completada")
            if self.cancel.is_set():
                self.done.emit({})
                return
            gps = exif.get("gps")
            place = reverse_geocode(gps["lat"], gps["lon"]) if gps else {}
            report = {"gps": gps, "place": place, "visual": vis, "mismatch_km": None}
            best = None
            if gps:
                best = (gps["lat"], gps["lon"], 0.5)
                if vis.get("lat") is not None:
                    report["mismatch_km"] = haversine_km((gps["lat"], gps["lon"]), (vis["lat"], vis["lon"]))
            elif vis.get("lat") is not None:
                best = (vis["lat"], vis["lon"], vis.get("radius_km"))
            self.geo_ready.emit(report)
            if best and not self.cancel.is_set():
                self.status.emit("Downloading preliminary map")
                self.map_ready.emit(build_map(best[0], best[1], zoom_for_radius(best[2])))
        except Exception as e:
            self.status.emit(f"Error in the image analysis: {e}")
        self.done.emit({})


# ── Results panel ───────────────────────────────────────────────────────

_SCROLL_QSS = (f"QScrollArea{{background:transparent;border:none;}}"
               f"QScrollBar:vertical{{background:transparent;width:7px;margin:0;}}"
               f"QScrollBar::handle:vertical{{background:{CYAN_DIM};min-height:30px;border-radius:2px;}}"
               f"QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}"
               f"QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{{background:transparent;}}")


def make_scroll(inner: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QScrollArea.Shape.NoFrame)
    sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    sa.setStyleSheet(_SCROLL_QSS)
    inner.setStyleSheet(inner.styleSheet() + "background:transparent;")
    sa.setWidget(inner)
    sa.viewport().setStyleSheet("background:transparent;")
    return sa


class ResultsPanel(QWidget):
    def __init__(self):
        super().__init__()
        self._inner = QWidget()
        self._lay = QVBoxLayout(self._inner)
        self._lay.setContentsMargins(0, 0, 6, 0)
        self._lay.setSpacing(12)
        self._lay.addStretch(1)
        self._empty = QLabel("NO DATA\nEnter an alias, name or image and start the scan.")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setFont(spaced(mono(9), 1.2))
        self._empty.setStyleSheet(f"color:{TEXT_DIM};background:transparent;")
        self._lay.insertWidget(0, self._empty)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(make_scroll(self._inner))

    def clear(self) -> None:
        while self._lay.count() > 1:
            it = self._lay.takeAt(0)
            if it.widget() is not None and it.widget() is not self._empty:
                it.widget().deleteLater()
        self._lay.insertWidget(0, self._empty)
        self._empty.show()

    def add(self, card: QWidget, top: bool = False) -> None:
        self._empty.hide()
        self._lay.removeWidget(self._empty)
        idx = 0 if top else max(0, self._lay.count() - 1)
        self._lay.insertWidget(idx, card)


# ══════════════════════════════════════════════════════════════════════════════
#  PROFILE LIBRARY  (a profile is only created when YOU decide)
# ══════════════════════════════════════════════════════════════════════════════

def make_thumb(src: str, dst: str, size: int = 256) -> None:
    """Square thumbnail cropped to the centre (so the library loads quickly)."""
    pix = QPixmap(src)
    if pix.isNull():
        raise ValueError("unreadable image")
    s = pix.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                   Qt.TransformationMode.SmoothTransformation)
    s.copy((s.width() - size) // 2, (s.height() - size) // 2, size, size).save(dst, "JPG", 88)


class _GeocodeWorker(_Worker):
    done = pyqtSignal(object)

    def __init__(self, query: str):
        super().__init__()
        self.query = query

    def run(self):
        try:
            self.done.emit(forward_geocode(self.query))
        except Exception:
            self.done.emit(None)


_EDIT_QSS = (f"QPlainTextEdit{{background:rgba(0,212,255,14);color:{TEXT};border:none;"
             f"border-bottom:1px solid {LINE};padding:7px 8px;"
             f"selection-background-color:{CYAN_DIM};selection-color:#ffffff;}}"
             f"QPlainTextEdit:focus{{border-bottom:1px solid {CYAN};background:rgba(0,212,255,26);}}")


class ElidedLabel(QLabel):
    """A single line that is truncated with '...' instead of being cut off."""

    def __init__(self, text: str = "", color: str = TEXT, font: QFont | None = None):
        super().__init__()
        self._full, self._col = text, color
        self.setFont(font or mono(9))
        self.setMinimumWidth(30)
        self.setFixedHeight(int(QFontMetricsF(self.font()).height()) + 6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip(text)

    def sizeHint(self) -> QSize:
        return QSize(200, self.height())

    def paintEvent(self, e):
        p = QPainter(self)
        p.setFont(self.font())
        p.setPen(qc(self._col))
        p.drawText(QRectF(0, 0, self.width(), self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   elide(self._full, self.font(), self.width()))


class FieldRow(QWidget):
    """Generic row: N text boxes + remove button."""
    removed = pyqtSignal(object)

    def __init__(self, specs: list[tuple[str, str, int]]):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.edits: dict[str, HudLineEdit] = {}
        for key, ph, stretch in specs:
            e = HudLineEdit(ph)
            self.edits[key] = e
            lay.addWidget(e, stretch)
        btn = IconButton("cross", AMBER, 26, "Quitar")
        btn.clicked.connect(lambda: self.removed.emit(self))
        lay.addWidget(btn)

    def data(self) -> dict:
        return {k: e.text().strip() for k, e in self.edits.items()}

    def load(self, d: dict) -> None:
        for k, e in self.edits.items():
            e.setText(str(d.get(k, "") or ""))

    def is_empty(self) -> bool:
        return not any(self.data().values())

    def error(self) -> str:
        return ""


class LocationRow(QWidget):
    """Location on two lines (never cut off): label+address / coordinates+actions+note."""
    removed = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        l1 = QHBoxLayout()
        l1.setSpacing(6)
        self.label = HudLineEdit("label (home, work...)")
        self.address = HudLineEdit("address / place")
        btn = IconButton("cross", AMBER, 26, "Remove location")
        btn.clicked.connect(lambda: self.removed.emit(self))
        l1.addWidget(self.label, 2)
        l1.addWidget(self.address, 5)
        l1.addWidget(btn)
        l2 = QHBoxLayout()
        l2.setSpacing(6)
        self.coords = HudLineEdit("lat, lon")
        self.coords.textChanged.connect(lambda _t: self.coords.setStyleSheet(self._qss(False)))
        self.b_geo = IconButton("pin", CYAN, 26, "Look up coordinates from the address")
        self.b_geo.clicked.connect(self._geocode)
        self.b_open = IconButton("arrow", CYAN, 26, "Open in OpenStreetMap")
        self.b_open.clicked.connect(self._open)
        self.note = HudLineEdit("note")
        l2.addWidget(self.coords, 3)
        l2.addWidget(self.b_geo)
        l2.addWidget(self.b_open)
        l2.addWidget(self.note, 4)
        l2.addSpacing(32)
        v.addLayout(l1)
        v.addLayout(l2)
        self._wk: _GeocodeWorker | None = None

    @staticmethod
    def _qss(bad: bool) -> str:
        col = AMBER if bad else LINE
        return (f"QLineEdit{{background:rgba(0,212,255,14);color:{TEXT};border:none;"
                f"border-bottom:1px solid {col};padding:7px 8px;"
                f"selection-background-color:{CYAN_DIM};selection-color:#ffffff;}}"
                f"QLineEdit:focus{{border-bottom:1px solid {CYAN};background:rgba(0,212,255,26);}}")

    def _geocode(self) -> None:
        q = self.address.text().strip() or self.label.text().strip()
        if not q or self._wk is not None:
            return
        self.coords.setPlaceholderText("searching...")
        self._wk = _GeocodeWorker(q)
        self._wk.done.connect(self._geo_done)
        self._wk.start()

    def _geo_done(self, pt) -> None:
        self._wk = None
        self.coords.setPlaceholderText("lat, lon")
        if pt:
            self.coords.setText(OP.format_coords(*pt))
        else:
            self.coords.setPlaceholderText("no result")

    def _open(self) -> None:
        pt = OP.parse_coords(self.coords.text())
        if pt:
            QDesktopServices.openUrl(QUrl(osm_link(pt[0], pt[1])))

    def data(self) -> dict:
        pt = OP.parse_coords(self.coords.text())
        return {"label": self.label.text().strip(), "address": self.address.text().strip(),
                "lat": pt[0] if pt else None, "lon": pt[1] if pt else None,
                "note": self.note.text().strip()}

    def load(self, d: dict) -> None:
        self.label.setText(d.get("label", "") or "")
        self.address.setText(d.get("address", "") or "")
        self.coords.setText(OP.format_coords(d.get("lat"), d.get("lon")))
        self.note.setText(d.get("note", "") or "")

    def is_empty(self) -> bool:
        return not (self.label.text().strip() or self.address.text().strip()
                    or self.coords.text().strip() or self.note.text().strip())

    def error(self) -> str:
        t = self.coords.text().strip()
        if t and OP.parse_coords(t) is None:
            self.coords.setStyleSheet(self._qss(True))
            return "invalid coordinates (use 'lat, lon', e.g. 41.3874, 2.1686)"
        return ""


class RowList(QWidget):
    """Editable list of rows with a + button (each row is created by `factory`)."""

    def __init__(self, factory, add_text: str, max_rows: int = 40):
        super().__init__()
        self._factory, self._add_text, self.MAX = factory, add_text, max_rows
        self._rows: list[QWidget] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self._box = QVBoxLayout()
        self._box.setSpacing(8)
        lay.addLayout(self._box)
        foot = QHBoxLayout()
        foot.setSpacing(8)
        self._add = IconButton("plus", CYAN, 26, add_text.capitalize())
        self._add.clicked.connect(lambda: self.add_row())
        self._cnt = dim_label("", False)
        foot.addWidget(self._add)
        foot.addWidget(self._cnt, 1)
        lay.addLayout(foot)
        self._refresh()

    def add_row(self, d: dict | None = None):
        if len(self._rows) >= self.MAX:
            return None
        row = self._factory()
        if d:
            row.load(d)
        row.removed.connect(self._remove)
        self._rows.append(row)
        self._box.addWidget(row)
        self._refresh()
        return row

    def _remove(self, row) -> None:
        if row in self._rows:
            self._rows.remove(row)
            self._box.removeWidget(row)
            row.deleteLater()
            self._refresh()

    def _refresh(self) -> None:
        self._cnt.setText(f"{self._add_text}  [{len(self._rows)}/{self.MAX}]")
        self._add.setVisible(len(self._rows) < self.MAX)

    def set_values(self, vals: list[dict]) -> None:
        for r in list(self._rows):
            self._remove(r)
        for v in vals:
            self.add_row(v)

    def values(self) -> list[dict]:
        return [r.data() for r in self._rows if not r.is_empty()]

    def errors(self) -> list[str]:
        out = []
        for i, r in enumerate([r for r in self._rows if not r.is_empty()], 1):
            e = r.error()
            if e:
                out.append(f"fila {i}: {e}")
        return out


class ThumbBox(QWidget):
    """Square thumbnail of a profile (cropped photo or initials)."""

    def __init__(self, path: str, name: str, size: int = 84):
        super().__init__()
        self.setFixedSize(size, size)
        self._pix = QPixmap(path) if path else None
        if self._pix is not None and self._pix.isNull():
            self._pix = None
        parts = [w for w in re.split(r"\s+", name.strip()) if w]
        self._ini = ("".join(w[0] for w in parts[:2]) or "?").upper()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = bevel_path(r, 10, 10)
        p.fillPath(path, qc(CYAN, 14))
        if self._pix:
            p.save()
            p.setClipPath(path)
            s = self._pix.scaled(self.width(), self.height(),
                                 Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                 Qt.TransformationMode.SmoothTransformation)
            p.drawPixmap((self.width() - s.width()) // 2, (self.height() - s.height()) // 2, s)
            p.restore()
        else:
            p.setFont(spaced(mono(18, True), 2))
            p.setPen(qc(CYAN, 170))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, self._ini)
        p.setPen(QPen(qc(CYAN, 150), 1))
        p.drawPath(path)


class ProfileRow(QWidget):
    """Card of a profile in the library."""
    open_me = pyqtSignal(str)
    scan_me = pyqtSignal(str)
    delete_me = pyqtSignal(str)

    def __init__(self, profile: dict, thumb_path: str):
        super().__init__()
        self._id = profile["id"]
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(14)
        top = QVBoxLayout()
        top.addWidget(ThumbBox(thumb_path, OP.display_name(profile)))
        top.addStretch(1)
        lay.addLayout(top)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(ElidedLabel(OP.display_name(profile), CYAN, spaced(mono(11, True), 1.2)))
        if profile["aliases"]:
            col.addWidget(ElidedLabel("alias: " + ", ".join(profile["aliases"]), TEXT, mono(8)))
        loc = next((l for l in profile["locations"] if l.get("address") or l.get("label")), None)
        if loc:
            col.addWidget(ElidedLabel("lugar: " + " / ".join(x for x in (loc["label"], loc["address"]) if x)
                                      + (f"   (+{len(profile['locations']) - 1})"
                                         if len(profile["locations"]) > 1 else ""), TEXT, mono(8)))
        if profile["tags"]:
            col.addWidget(ElidedLabel("  ".join("#" + t for t in profile["tags"]), AMBER, mono(8)))
        col.addWidget(ElidedLabel(f"{len(profile['links'])} links  /  {len(profile['locations'])} locations"
                                  f"  /  upd. {profile['updated'][:10]}", TEXT_DIM, mono(7)))
        col.addSpacing(4)
        btns = QHBoxLayout()
        btns.setSpacing(6)
        self.b_edit = HudButton("EDIT", "cyan", False, 26)
        self.b_scan = HudButton("IN SCANNER", "cyan", False, 26)
        self.b_del = HudButton("DELETE", "amber", False, 26)
        self.b_edit.clicked.connect(lambda: self.open_me.emit(self._id))
        self.b_scan.clicked.connect(lambda: self.scan_me.emit(self._id))
        self.b_del.clicked.connect(self._delete_click)
        for b in (self.b_edit, self.b_scan, self.b_del):
            btns.addWidget(b)
        col.addLayout(btns)
        lay.addLayout(col, 1)
        self._armed = False
        self._t = QTimer(self)
        self._t.setSingleShot(True)
        self._t.timeout.connect(self._disarm)

    def _delete_click(self) -> None:
        if not self._armed:
            self._armed = True
            self.b_del.setText("CONFIRM?")
            self._t.start(3500)
            return
        self._t.stop()
        self.delete_me.emit(self._id)

    def _disarm(self) -> None:
        self._armed = False
        self.b_del.setText("DELETE")

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.open_me.emit(self._id)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = bevel_path(r, 14, 14)
        p.fillPath(path, qc(CYAN, 10))
        p.setPen(QPen(qc(CYAN, 110), 1))
        p.drawPath(path)


class LibraryPanel(QWidget):
    """Library: search + list of profiles. It creates nothing on its own."""
    open_profile = pyqtSignal(str)
    new_profile = pyqtSignal()
    scan_profile = pyqtSignal(str)
    changed = pyqtSignal()

    def __init__(self, store: "OP.ProfileStore"):
        super().__init__()
        self.store = store
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.search = HudLineEdit("search by name, alias, label, place, link, note...")
        self.search.textChanged.connect(lambda _t: self.refresh())
        self.btn_new = HudButton("NEW PROFILE", "cyan", True, 34)
        self.btn_new.setFixedWidth(170)
        self.btn_new.clicked.connect(self.new_profile)
        top.addWidget(self.search, 1)
        top.addWidget(self.btn_new)
        outer.addLayout(top)
        self.lbl_count = dim_label("", False)
        outer.addWidget(self.lbl_count)
        self.lbl_warn = dim_label("", True)
        self.lbl_warn.setStyleSheet(f"color:{AMBER};background:transparent;")
        self.lbl_warn.hide()
        outer.addWidget(self.lbl_warn)
        self._inner = QWidget()
        self._lay = QVBoxLayout(self._inner)
        self._lay.setContentsMargins(0, 0, 6, 0)
        self._lay.setSpacing(10)
        self._lay.addStretch(1)
        self._scroll = make_scroll(self._inner)
        outer.addWidget(self._scroll, 1)
        self._empty = QLabel("")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        self._empty.setFont(spaced(mono(9), 1.0))
        self._empty.setStyleSheet(f"color:{TEXT_DIM};background:transparent;")
        outer.addWidget(self._empty, 1)
        self.refresh()

    def refresh(self) -> None:
        while self._lay.count() > 1:
            it = self._lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        q = self.search.text()
        rows = self.store.all(q)
        total = self.store.count()
        for p in rows:
            row = ProfileRow(p, self.store.photo_path(p, thumb=True))
            row.open_me.connect(self.open_profile)
            row.scan_me.connect(self.scan_profile)
            row.delete_me.connect(self._delete)
            self._lay.insertWidget(self._lay.count() - 1, row)
        self.lbl_count.setText(f"{len(rows)} of {total} profiles" if q.strip() else f"{total} profiles")
        self.lbl_warn.setText(self.store.load_error)
        self.lbl_warn.setVisible(bool(self.store.load_error))
        if rows:
            self._empty.hide()
            self._scroll.show()
        else:
            self._scroll.hide()
            self._empty.setText(
                "NO RESULTS FOR THAT SEARCH." if total else
                "LIBRARY EMPTY\n\nProfiles are only created when you decide: press NEW PROFILE "
                "or use SAVE AS PROFILE after a scan.")
            self._empty.show()

    def _delete(self, pid: str) -> None:
        self.store.delete(pid)
        self.refresh()
        self.changed.emit()


class ProfileEditor(QWidget):
    """Full profile editor. Nothing is saved until SAVE PROFILE is pressed."""
    saved = pyqtSignal(str)
    deleted = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, store: "OP.ProfileStore", profile: dict | None = None,
                 photo_src: str = "", hint: str = ""):
        super().__init__()
        self.store = store
        self._orig = OP.normalize(profile) if profile else OP.new_profile()
        self._exists = store.get(self._orig["id"]) is not None
        self._orig_photo_abs = store.photo_path(self._orig) if self._exists else ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(section_label("//", "EDIT PROFILE" if self._exists else "NEW PROFILE"))
        head.addStretch(1)
        self.lbl_flash = dim_label("", False)
        head.addWidget(self.lbl_flash)
        outer.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(16)
        outer.addLayout(body, 1)

        # photo column
        left = QVBoxLayout()
        left.setSpacing(8)
        left.addWidget(section_label("00", "PHOTO"))
        self.photo = DropZone(210, "PROFILE PHOTO")
        left.addWidget(self.photo)
        self.lbl_meta = dim_label("")
        left.addWidget(self.lbl_meta)
        left.addStretch(1)
        lw = QWidget()
        lw.setFixedWidth(220)
        lw.setLayout(left)
        body.addWidget(lw)

        # formulario
        form = QWidget()
        f = QVBoxLayout(form)
        f.setContentsMargins(0, 0, 8, 0)
        f.setSpacing(8)
        if hint:
            hl = dim_label(hint)
            hl.setStyleSheet(f"color:{AMBER};background:transparent;")
            f.addWidget(hl)
            f.addSpacing(4)
        f.addWidget(section_label("01", "NAME"))
        self.name = HudLineEdit("full name")
        f.addWidget(self.name)
        f.addSpacing(6)
        f.addWidget(section_label("02", "ALIASES / USERNAMES"))
        self.aliases = RowList(lambda: FieldRow([("v", "alias / username", 1)]), "ADD ALIAS", 20)
        f.addWidget(self.aliases)
        f.addSpacing(6)
        f.addWidget(section_label("03", "LOCATIONS"))
        self.locations = RowList(LocationRow, "ADD LOCATION", 30)
        f.addWidget(self.locations)
        f.addSpacing(6)
        f.addWidget(section_label("04", "LINKS AND PROFILES"))
        self.links = RowList(lambda: FieldRow([("label", "platform / label", 2),
                                               ("url", "https://...", 5)]), "ADD LINK", 60)
        f.addWidget(self.links)
        f.addSpacing(6)
        f.addWidget(section_label("05", "ADDITIONAL DATA"))
        self.fields = RowList(lambda: FieldRow([("key", "field (e.g. company, phone)", 2),
                                                ("value", "value", 3)]), "ADD FIELD", 40)
        f.addWidget(self.fields)
        f.addSpacing(6)
        f.addWidget(section_label("06", "TAGS"))
        self.tags = HudLineEdit("comma-separated")
        f.addWidget(self.tags)
        f.addSpacing(6)
        f.addWidget(section_label("07", "CONTEXT / REASON"))
        self.context = HudLineEdit("why you are investigating / authorisation")
        f.addWidget(self.context)
        f.addSpacing(6)
        f.addWidget(section_label("08", "NOTES"))
        self.notes = QPlainTextEdit()
        self.notes.setFont(mono(10))
        self.notes.setMinimumHeight(130)
        self.notes.setStyleSheet(_EDIT_QSS)
        self.notes.setPlaceholderText("free notes...")
        f.addWidget(self.notes)
        f.addSpacing(8)
        body.addWidget(make_scroll(form), 1)

        # bottom bar (outside the scroll area: the buttons are never cut off)
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.b_save = HudButton("SAVE PROFILE", "cyan", True, 36)
        self.b_cancel = HudButton("CANCEL", "cyan", False, 36)
        self.b_del = HudButton("DELETE PROFILE", "amber", False, 36)
        self.b_save.clicked.connect(self._save)
        self.b_cancel.clicked.connect(self._cancel_click)
        self.b_del.clicked.connect(self._delete_click)
        bar.addWidget(self.b_save, 3)
        bar.addWidget(self.b_cancel, 2)
        if self._exists:
            bar.addWidget(self.b_del, 2)
        else:
            self.b_del.hide()
        outer.addLayout(bar)

        # carga
        o = self._orig
        self.name.setText(o["name"])
        self.aliases.set_values([{"v": a} for a in o["aliases"]])
        self.locations.set_values(o["locations"])
        self.links.set_values(o["links"])
        self.fields.set_values(o["fields"])
        self.tags.setText(", ".join(o["tags"]))
        self.context.setText(o["context"])
        self.notes.setPlainText(o["notes"])
        if self._orig_photo_abs:
            self.photo.set_path(self._orig_photo_abs)
        elif photo_src:
            self.photo.set_path(photo_src)
        self.lbl_meta.setText(f"ID {o['id']}\ncreated {o['created'][:16].replace('T', ' ')}\n"
                              f"modificado {o['updated'][:16].replace('T', ' ')}"
                              if self._exists else f"ID {o['id']}\nnew profile: unsaved")

        self._flash_t = QTimer(self)
        self._flash_t.setSingleShot(True)
        self._flash_t.timeout.connect(lambda: self.lbl_flash.setText(""))
        self._arm = {"cancel": False, "delete": False}
        self._arm_t = QTimer(self)
        self._arm_t.setSingleShot(True)
        self._arm_t.timeout.connect(self._disarm)
        self._initial = self._snapshot()

    # -- estado --
    def _snapshot(self) -> str:
        return json.dumps(self._collect(), sort_keys=True, ensure_ascii=False, default=str)

    def is_dirty(self) -> bool:
        return self._snapshot() != self._initial

    def _collect(self) -> dict:
        tags = [t for t in re.split(r"[,;]+", self.tags.text()) if t.strip()]
        return {"id": self._orig["id"], "name": self.name.text(),
                "photo": self.photo.path(),
                "aliases": [r["v"] for r in self.aliases.values()],
                "locations": self.locations.values(), "links": self.links.values(),
                "fields": self.fields.values(), "tags": tags, "context": self.context.text(),
                "notes": self.notes.toPlainText()}

    def _flash(self, text: str, amber: bool = True) -> None:
        self.lbl_flash.setText(text)
        self.lbl_flash.setStyleSheet(f"color:{AMBER if amber else GREEN};background:transparent;")
        self._flash_t.start(6000)

    # -- acciones --
    def _save(self) -> None:
        errs = self.locations.errors()
        if errs:
            self._flash("Locations: " + errs[0])
            return
        data = self._collect()
        prof = OP.normalize(data)
        if not (prof["name"] or prof["aliases"]):
            self._flash("Provide at least a name or an alias.")
            return
        cur = self.photo.path()
        if not cur:
            prof["photo"], src = "", ""
        elif cur == self._orig_photo_abs:
            prof["photo"], src = self._orig["photo"], ""
        else:
            prof["photo"], src = "", cur
        prof["created"] = self._orig["created"]
        try:
            saved = self.store.save(prof, src, thumb_writer=make_thumb)
        except Exception as e:
            self._flash(f"Could not save: {e}")
            return
        self.saved.emit(saved["id"])

    def _disarm(self) -> None:
        self._arm = {"cancel": False, "delete": False}
        self.b_cancel.setText("CANCEL")
        self.b_del.setText("DELETE PROFILE")

    def _cancel_click(self) -> None:
        if self.is_dirty() and not self._arm["cancel"]:
            self._arm["cancel"] = True
            self.b_cancel.setText("DISCARD CHANGES?")
            self._arm_t.start(4000)
            return
        self.cancelled.emit()

    def _delete_click(self) -> None:
        if not self._arm["delete"]:
            self._arm["delete"] = True
            self.b_del.setText("CONFIRM DELETION?")
            self._arm_t.start(4000)
            return
        pid = self._orig["id"]
        self.store.delete(pid)
        self.deleted.emit(pid)


class _Grip(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(18, 18)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)

    def mousePressEvent(self, e):
        wh = self.window().windowHandle()
        if wh and e.button() == Qt.MouseButton.LeftButton:
            wh.startSystemResize(Qt.Edge.RightEdge | Qt.Edge.BottomEdge)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setPen(QPen(qc(CYAN, 150), 1))
        for i in (4, 9, 14):
            p.drawLine(self.width() - 2, i, i, self.height() - 2)


# ── Ventana principal ─────────────────────────────────────────────────────────

class OsintHudWindow(QWidget):
    closed = pyqtSignal(object)
    HEADER_H = 50

    def __init__(self, parent=None, store=None):
        super().__init__(parent, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("OSINT  /  J.A.R.V.I.S.")
        self.resize(1060, 740)
        self.setMinimumSize(880, 640)
        self.setStyleSheet(f"QToolTip{{background:{BG};color:{CYAN};border:1px solid {LINE};}}")
        self._token = 0
        self._pending = 0
        self._scanning = False
        self._workers: list[_Worker] = []
        self._geo_card: GeoCard | None = None
        self._stats = {"hits": 0, "geo": False, "t0": 0.0}
        self._ph = 0.0
        self.store = store or OP.ProfileStore()
        self._hits: list[UserHit] = []              # matches of the last scan
        self._geo_report: dict | None = None
        self._scan_img = ""
        self._editor: ProfileEditor | None = None
        self._armed = False

        root = QVBoxLayout(self)
        root.setContentsMargins(16, self.HEADER_H + 10, 16, 18)
        root.setSpacing(0)

        # tabs: SCANNER / LIBRARY
        nav = QHBoxLayout()
        nav.setSpacing(8)
        self.tab_scan = HudButton("SCANNER", "cyan", True, 30)
        self.tab_scan.setFixedWidth(150)
        self.tab_scan.clicked.connect(self.show_scan)
        self.tab_lib = HudButton("LIBRARY", "cyan", False, 30)
        self.tab_lib.setFixedWidth(190)
        self.tab_lib.clicked.connect(self.show_library)
        self.lbl_nav = dim_label("", False)
        nav.addWidget(self.tab_scan)
        nav.addWidget(self.tab_lib)
        nav.addSpacing(8)
        nav.addWidget(self.lbl_nav, 1)
        root.addLayout(nav)
        root.addSpacing(10)
        self._nav_t = QTimer(self)
        self._nav_t.setSingleShot(True)
        self._nav_t.timeout.connect(self._nav_clear)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        page_scan = QWidget()
        body = QHBoxLayout(page_scan)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(16)

        # input panel (no fixed width: the scroll area fixes the width and the content adapts,
        # so the scroll bar no longer clips boxes or buttons on the right)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 12, 0)
        ll.setSpacing(8)
        ll.addWidget(section_label("01", "ALIASES / USERNAMES"))
        self.aliases = AliasList()
        self.aliases.submitted.connect(self.start_scan)
        ll.addWidget(self.aliases)
        ll.addSpacing(8)
        ll.addWidget(section_label("02", "REAL IDENTITY"))
        self.name_edit = HudLineEdit("full name")
        self.name_edit.returnPressed.connect(self.start_scan)
        ll.addWidget(self.name_edit)
        ll.addSpacing(8)
        ll.addWidget(section_label("03", "IMAGE  /  EXIF + GEOLOCATION"))
        self.drop = DropZone()
        ll.addWidget(self.drop)
        ll.addSpacing(6)
        self.btn_go = HudButton("START SCAN", "cyan", True, 38)
        self.btn_go.clicked.connect(self.start_scan)
        self.btn_stop = HudButton("ABORTAR", "amber", False, 32)
        self.btn_stop.clicked.connect(self.abort_scan)
        self.btn_stop.setEnabled(False)
        ll.addWidget(self.btn_go)
        ll.addWidget(self.btn_stop)
        ll.addSpacing(4)
        self.btn_profile = HudButton("SAVE AS PROFILE", "cyan", False, 32)
        self.btn_profile.setToolTip("Opens the editor with this data. Nothing is saved until you press "
                                    "SAVE PROFILE.")
        self.btn_profile.clicked.connect(self.create_profile_from_scan)
        ll.addWidget(self.btn_profile)
        ll.addStretch(1)
        radar_row = QHBoxLayout()
        radar_row.setSpacing(12)
        self.radar = RadarWidget(104)
        radar_row.addWidget(self.radar)
        st = QVBoxLayout()
        st.setSpacing(3)
        self.lbl_state = QLabel("STANDBY")
        self.lbl_state.setFont(spaced(mono(9, True), 1.6))
        self.lbl_state.setStyleSheet(f"color:{CYAN};background:transparent;")
        self.lbl_info = dim_label("System ready. No active tasks.")
        st.addStretch(1)
        st.addWidget(self.lbl_state)
        st.addWidget(self.lbl_info)
        st.addStretch(1)
        radar_row.addLayout(st, 1)
        ll.addLayout(radar_row)
        ll.addSpacing(4)
        ll.addWidget(dim_label("For use only on your own data or with express authorisation. "
                               "No facial recognition."))
        left_scroll = make_scroll(left)
        left_scroll.setFixedWidth(368)
        body.addWidget(left_scroll)

        # results
        right = QVBoxLayout()
        right.setSpacing(6)
        top = QHBoxLayout()
        top.addWidget(section_label("//", "DOSSIERS"))
        top.addStretch(1)
        self.lbl_count = dim_label("0 cards", False)
        top.addWidget(self.lbl_count)
        right.addLayout(top)
        self.scanline = ScanLine()
        right.addWidget(self.scanline)
        self.results = ResultsPanel()
        right.addWidget(self.results, 1)
        body.addLayout(right, 1)
        self.stack.addWidget(page_scan)

        # profile library
        self.library = LibraryPanel(self.store)
        self.library.new_profile.connect(lambda: self.open_editor(OP.new_profile()))
        self.library.open_profile.connect(self._open_profile)
        self.library.scan_profile.connect(self._scan_profile)
        self.library.changed.connect(self._update_lib_count)
        self.stack.addWidget(self.library)
        self._update_lib_count()

        # header buttons and grip
        self.btn_close = IconButton("cross", CYAN, 28, "Close")
        self.btn_close.setParent(self)
        self.btn_close.clicked.connect(self.close)
        self.btn_min = IconButton("minus", CYAN, 28, "Minimizar")
        self.btn_min.setParent(self)
        self.btn_min.clicked.connect(self.showMinimized)
        self.grip = _Grip()
        self.grip.setParent(self)

        self._anim = QTimer(self)
        self._anim.timeout.connect(self._tick)
        self._anim.start(60)

    # ── API publica ──
    def prefill(self, username: str = "", full_name: str = "", image_path: str = "",
                auto_start: bool = False, open_library: bool = False) -> None:
        if open_library:
            self.show_library()
            return
        names = [clean_alias(x) for x in re.split(r"[,;\n]+", username or "") if clean_alias(x)]
        if names:
            self.aliases.set_values(names)
        if full_name:
            self.name_edit.setText(full_name.strip())
        if image_path:
            self.drop.set_path(image_path)
        if auto_start:
            self.start_scan()

    # ── navegacion ──
    def _nav_msg(self, text: str, amber: bool = True, ms: int = 4500) -> None:
        self.lbl_nav.setText(text)
        self.lbl_nav.setStyleSheet(f"color:{AMBER if amber else GREEN};background:transparent;")
        self._nav_t.start(ms)

    def _nav_clear(self) -> None:
        self._armed = False
        self.lbl_nav.setText("")

    def _update_lib_count(self) -> None:
        self.tab_lib.setText(f"LIBRARY  [{self.store.count()}]")

    def _leave_ok(self) -> bool:
        """If a profile is half-edited, the first press warns and the second discards."""
        ed = self._editor
        if ed is None or self.stack.currentWidget() is not ed or not ed.is_dirty():
            return True
        if self._armed:
            self._nav_clear()
            return True
        self._armed = True
        self._nav_msg("UNSAVED CHANGES  //  repeat the action to discard them")
        return False

    def _drop_editor(self) -> None:
        if self._editor is not None:
            self.stack.removeWidget(self._editor)
            self._editor.deleteLater()
            self._editor = None

    def _show(self, widget: QWidget) -> None:
        self.stack.setCurrentWidget(widget)
        self.tab_scan.setPrimary(widget is self.stack.widget(0))
        self.tab_lib.setPrimary(widget is not self.stack.widget(0))

    def show_scan(self) -> None:
        if not self._leave_ok():
            return
        self._drop_editor()
        self._show(self.stack.widget(0))

    def show_library(self) -> None:
        if not self._leave_ok():
            return
        self._show_library_now()

    def _show_library_now(self) -> None:
        self._drop_editor()
        self.library.refresh()
        self._update_lib_count()
        self._show(self.library)

    def open_editor(self, profile: dict, photo: str = "", hint: str = "") -> None:
        if not self._leave_ok():
            return
        self._drop_editor()
        ed = ProfileEditor(self.store, profile, photo, hint)
        ed.saved.connect(self._on_saved)
        ed.deleted.connect(self._on_deleted)
        ed.cancelled.connect(self._show_library_now)
        self.stack.addWidget(ed)
        self._editor = ed
        self._show(ed)

    def _open_profile(self, pid: str) -> None:
        p = self.store.get(pid)
        if p:
            self.open_editor(p)

    def _on_saved(self, pid: str) -> None:
        self._show_library_now()
        self._nav_msg("PROFILE SAVED", False, 3000)

    def _on_deleted(self, pid: str) -> None:
        self._show_library_now()
        self._nav_msg("PROFILE DELETED", False, 3000)

    def _scan_profile(self, pid: str) -> None:
        """Loads a profile's data into the scanner (without launching it)."""
        p = self.store.get(pid)
        if not p:
            return
        self.aliases.set_values([])
        self.name_edit.clear()
        self.drop.set_path("")
        self.prefill(username=",".join(p["aliases"][:AliasList.MAX]), full_name=p["name"],
                     image_path=self.store.photo_path(p))
        self._show(self.stack.widget(0))
        self._set_state("STANDBY", "Profile data loaded. Press START SCAN.")

    @staticmethod
    def _locations_from_report(rep: dict | None) -> list[dict]:
        out: list[dict] = []
        if not rep:
            return out
        gps, place, vis = rep.get("gps"), rep.get("place") or {}, rep.get("visual") or {}
        if gps:
            out.append({"label": "Photo (EXIF GPS)", "address": place.get("display") or "",
                        "lat": gps["lat"], "lon": gps["lon"],
                        "note": "Source: EXIF metadata of the image"})
        if vis and not vis.get("error") and vis.get("lat") is not None:
            where = ", ".join(x for x in (vis.get("place"), vis.get("city"), vis.get("region"),
                                          vis.get("country")) if x)
            out.append({"label": "Photo (visual inference)", "address": where,
                        "lat": vis["lat"], "lon": vis["lon"],
                        "note": f"Unverified AI estimate, confidence {vis.get('confidence', 0)}%"})
        return out

    def create_profile_from_scan(self) -> None:
        """SAVE AS PROFILE button: opens the pre-filled editor. It saves nothing by itself."""
        seen, aliases = set(), []
        for raw in self.aliases.values():
            a = clean_alias(raw)
            if a and a.lower() not in seen:
                seen.add(a.lower())
                aliases.append(a)
        img = self.drop.path()
        links = [{"label": f"{h.platform} ({h.alias})", "url": h.url}
                 for h in self._hits if h.alias.lower() in seen]
        locs = self._locations_from_report(self._geo_report) if img and img == self._scan_img else []
        prof = OP.new_profile(name=self.name_edit.text().strip(), aliases=aliases,
                              links=links, locations=locs)
        hint = ""
        if links or locs:
            hint = ("Data pre-filled from the scan. Review and remove anything that does not apply: "
                    "alias matches and the estimated location are not verified.")
        self.open_editor(prof, photo=img, hint=hint)

    # ── pintura / ventana ──
    def _tick(self) -> None:
        self._ph = (self._ph + 0.06) % 1.0
        self.update(0, 0, self.width(), self.HEADER_H)

    def resizeEvent(self, e):
        self.btn_close.move(self.width() - 16 - 28, 11)
        self.btn_min.move(self.width() - 16 - 28 - 34, 11)
        self.grip.move(self.width() - 22, self.height() - 22)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() < self.HEADER_H:
            wh = self.windowHandle()
            if wh:
                wh.startSystemMove()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        r = QRectF(1, 1, w - 2, h - 2)
        path = bevel_path(r, 22, 22)
        p.fillPath(path, qc(BG, 238))
        p.save()
        p.setClipPath(path)
        p.setPen(qc(CYAN, 7))
        for y in range(0, h, 4):                               # lineas de barrido holograficas
            p.drawLine(0, y, w, y)
        p.restore()
        p.setPen(QPen(qc(CYAN, 230), 1.2))
        p.drawPath(path)
        p.setPen(QPen(qc(LINE), 1))
        p.drawPath(bevel_path(r.adjusted(6, 6, -6, -6), 16, 16))
        # crosshair corners
        p.setPen(QPen(qc(CYAN), 2.2))
        k = 20
        for cx, cy, dx, dy in ((r.left() + 24, r.top(), 1, 1), (r.right(), r.top(), -1, 1),
                               (r.left(), r.bottom(), 1, -1), (r.right() - 24, r.bottom(), -1, -1)):
            p.drawLine(QPointF(cx, cy), QPointF(cx + dx * k, cy))
        p.setPen(QPen(qc(CYAN), 2.2))
        p.drawLine(QPointF(r.right(), r.top()), QPointF(r.right(), r.top() + k))
        p.drawLine(QPointF(r.left(), r.bottom()), QPointF(r.left(), r.bottom() - k))
        # cabecera
        scanning = self._scanning
        col = AMBER if scanning else CYAN
        pulse = 0.5 + 0.5 * math.sin(self._ph * 2 * math.pi)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qc(col, int(90 + 165 * pulse)))
        p.drawEllipse(QPointF(34, 25), 5, 5)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qc(col, 120), 1))
        p.drawEllipse(QPointF(34, 25), 9 + 3 * pulse, 9 + 3 * pulse)
        title = "OSINT  //  OPEN-SOURCE RECONNAISSANCE"
        subtitle = "J.A.R.V.I.S.  /  INTELLIGENCE MODULE  /  SECURE LINK"
        state_txt = "SCANNING" if scanning else "STANDBY"
        sf = spaced(mono(8, True), 2.0)
        state_w = QFontMetricsF(sf).horizontalAdvance(state_txt) + 10
        right_edge = w - 92                       # to the left of minimise / close
        avail = right_edge - 54
        show_state = avail - state_w - 16 >= 360   # if there is no room, the title takes priority
        if show_state:
            avail -= state_w + 16
        tf = fit_font(title, avail, 12, True, 3.0, 8)
        p.setFont(tf)
        p.setPen(qc(CYAN))
        p.drawText(QRectF(54, 8, avail, 22), Qt.AlignmentFlag.AlignVCenter, elide(title, tf, avail))
        stf = fit_font(subtitle, avail, 7, False, 1.6, 6)
        p.setFont(stf)
        p.setPen(qc(TEXT_DIM))
        p.drawText(QRectF(54, 28, avail, 14), Qt.AlignmentFlag.AlignVCenter,
                   elide(subtitle, stf, avail))
        if show_state:
            p.setFont(sf)
            p.setPen(qc(col))
            p.drawText(QRectF(right_edge - state_w, 14, state_w, 22),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, state_txt)
        p.setPen(QPen(qc(CYAN, 90), 1))
        p.drawLine(QPointF(18, self.HEADER_H), QPointF(w - 18, self.HEADER_H))

    def closeEvent(self, e):
        self._token += 1
        for wk in self._workers:
            wk.cancel.set()
        self._anim.stop()
        self.radar.set_active(False)
        self.closed.emit(self)
        super().closeEvent(e)

    # ── logica ──
    def _guard(self, fn, token: int):
        return lambda *a: fn(*a) if token == self._token else None

    def _set_state(self, state: str, info: str, amber: bool = False) -> None:
        self.lbl_state.setText(state)
        self.lbl_state.setStyleSheet(f"color:{AMBER if amber else CYAN};background:transparent;")
        self.lbl_info.setText(info)

    def _card(self, card: QWidget, top: bool = False) -> None:
        self.results.add(card, top)
        self._cards = getattr(self, "_cards", 0) + 1
        self.lbl_count.setText(f"{self._cards} cards")

    def start_scan(self) -> None:
        if self._scanning:
            return
        seen, aliases, skipped = set(), [], []
        for raw in self.aliases.values():
            a = clean_alias(raw)
            if not a:
                continue
            if not valid_alias(a):
                skipped.append(a)
            elif a.lower() not in seen:
                seen.add(a.lower())
                aliases.append(a)
        name = self.name_edit.text().strip()
        img = self.drop.path()
        if not (aliases or name or img):
            self._set_state("NO DATA", "Enter an alias, a name or an image.", True)
            return
        self._token += 1
        tok = self._token
        self.results.clear()
        self._cards = 0
        self.lbl_count.setText("0 cards")
        self._geo_card = None
        self._hits, self._geo_report, self._scan_img = [], None, img
        self._stats = {"hits": 0, "geo": False, "t0": time.time(), "aliases": len(aliases)}
        self._pending = 0
        self._workers = []
        self._scanning = True
        self.btn_go.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.radar.set_active(True)
        self.scanline.set_active(True)
        self._set_state("SCANNING", "Launching tasks in parallel...", True)

        if skipped:
            warn = DossierCard("WARNING", AMBER)
            warn.add_note("Aliases discarded for invalid format: " + ", ".join(skipped), AMBER)
            self._card(warn)

        if aliases:
            card = UsernameCard(aliases)
            self._card(card)
            wk = UsernameWorker(aliases)
            wk.hit.connect(self._guard(lambda h, c=card: (c.add_hit(h), self._hits.append(h),
                                                          self.radar.ping(), self._bump()), tok))
            wk.progress.connect(self._guard(card.set_progress, tok))
            wk.done.connect(self._guard(lambda s, c=card: (c.finish(s), self._job_done("users")), tok))
            self._start(wk)
        if name:
            wk = IdentityWorker(name)
            wk.done.connect(self._guard(lambda d, n=name: (self._card(build_identity_card(d, n)),
                                                           self._job_done("identity")), tok))
            self._start(wk)
        if img:
            wk = ImageWorker(img)
            wk.exif_ready.connect(self._guard(lambda d: (self._card(build_exif_card(d)),
                                                         self._set_state("SCANNING", "EXIF extracted", True)), tok))
            wk.geo_ready.connect(self._guard(self._on_geo, tok))
            wk.map_ready.connect(self._guard(self._on_map, tok))
            wk.status.connect(self._guard(lambda s: self.lbl_info.setText(s), tok))
            wk.done.connect(self._guard(lambda _d: (self._card(build_reverse_card()),
                                                    self._job_done("image")), tok))
            self._start(wk)

    def _start(self, wk: _Worker) -> None:
        self._pending += 1
        self._workers.append(wk)
        wk.start()

    def _bump(self) -> None:
        self._stats["hits"] += 1
        self.lbl_info.setText(f"{self._stats['hits']} user matches")

    def _on_geo(self, report: dict) -> None:
        card = GeoCard(report)
        self._geo_report = report
        self._geo_card = card
        self._stats["geo"] = card.coords is not None
        self._card(card)

    def _on_map(self, data) -> None:
        if self._geo_card is not None and self._geo_card.map is not None:
            self._geo_card.map.set_map(data)

    def _job_done(self, what: str) -> None:
        self._pending -= 1
        if self._pending > 0:
            self.lbl_info.setText(f"Task completed: {what}")
            return
        self._finish()

    def _finish(self) -> None:
        self._scanning = False
        self.btn_go.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.radar.set_active(False)
        self.scanline.set_active(False)
        dt = time.time() - self._stats["t0"]
        summ = DossierCard("OPERATION SUMMARY", GREEN)
        summ.add_kv("Duracion", f"{dt:.1f} s")
        if self._stats.get("aliases"):
            summ.add_kv("User profiles", str(self._stats["hits"]))
        if self.drop.path():
            summ.add_kv("Image location", "determined" if self._stats["geo"] else "not determined",
                        GREEN if self._stats["geo"] else AMBER)
        b = HudButton("SAVE AS PROFILE", "cyan", False, 30)
        b.setToolTip("Opens the editor with this data. Nothing is saved until you press SAVE PROFILE.")
        b.clicked.connect(self.create_profile_from_scan)
        summ.body.addSpacing(4)
        summ.body.addWidget(b)
        self._card(summ, top=True)
        self._set_state("COMPLETED", f"Operation finished in {dt:.1f} s.")

    def abort_scan(self) -> None:
        if not self._scanning:
            return
        self._token += 1                       # pending results are ignored
        for wk in self._workers:
            wk.cancel.set()
        self._scanning = False
        self._pending = 0
        self.btn_go.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.radar.set_active(False)
        self.scanline.set_active(False)
        self._set_state("ABORTED", "Operation cancelled by the user.", True)


# ── Ejecucion independiente ───────────────────────────────────────────────────

def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    win = OsintHudWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
