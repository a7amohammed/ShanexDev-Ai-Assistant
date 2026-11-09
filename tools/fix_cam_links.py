"""Fixes the Spanish camera links by reading the REAL SkylineWebcams pages.

    python tools/fix_cam_links.py            # descubre y escribe config/live_cams.json
    python tools/fix_cam_links.py --dry-run  # only shows what would change
    python tools/fix_cam_links.py --fallback # cameras not found point to their region page

Why it exists: the links in Cámaras_España.txt were not real per-camera addresses (the
camera name does exist on the site, but the exact path does not match), so they return 404.
This script downloads the listing of each region, collects every camera link that Skyline
actually publishes and matches each catalogue camera to the closest one (by path and by
name). Its output goes to config/live_cams.json, which takes precedence over the built-in catalogue.
"""
import difflib
import html as htmllib
import json
import re
import sys
import unicodedata
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.live_cams_es import SPAIN_CAMS, _BASE  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
USER_FILE = ROOT / "config" / "live_cams.json"
LINK_RE = re.compile(r'<a[^>]+href="((?:https://www\.skylinewebcams\.com)?/es/webcam/espana/[^"#?]+\.html)"[^>]*>(.*?)</a>',
                     re.S | re.I)
MIN_SCORE = 0.78


def norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "es-ES,es;q=0.9"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "ignore")


def discover(region_slug: str, fetch_fn=fetch) -> dict:
    """{absolute_url: link_text} of a region (and its provinces if there are sub-pages)."""
    found, queue, seen = {}, [f"{_BASE}espana/{region_slug}.html"], set()
    while queue:
        page = queue.pop(0)
        if page in seen:
            continue
        seen.add(page)
        try:
            body = fetch_fn(page)
        except Exception as e:  # noqa: BLE001
            print(f"  ! no pude leer {page}: {e}")
            continue
        for href, label in LINK_RE.findall(body):
            url = href if href.startswith("http") else "https://www.skylinewebcams.com" + href
            text = htmllib.unescape(re.sub(r"<[^>]+>", " ", label)).strip()
            found[url] = text or found.get(url, "")
            # a path without a specific camera (province) may list more links: explore it once
            depth = url[len(_BASE):-5].count("/")
            if depth <= 2 and url not in seen and len(seen) < 40:
                queue.append(url)
    return found


def best_match(cam: dict, pool: dict):
    slug = cam["id"][3:]
    want_name = norm(f"{cam['name']} {cam['place']}")
    best, best_score = None, 0.0
    for url, text in pool.items():
        real_slug = url.rsplit("/", 1)[1][:-5]
        s1 = difflib.SequenceMatcher(None, slug, real_slug).ratio()
        s2 = difflib.SequenceMatcher(None, want_name, norm(text)).ratio() if text else 0.0
        s3 = 1.0 if slug == real_slug else 0.0
        score = max(s1, s2 * 0.95, s3)
        if score > best_score:
            best, best_score = url, score
    return (best, best_score) if best_score >= MIN_SCORE else (None, best_score)


def main() -> None:
    dry, fallback = "--dry-run" in sys.argv, "--fallback" in sys.argv
    regions = sorted({c["page"][len(_BASE):].split("/")[1] for c in SPAIN_CAMS})
    pools = {}
    for r in regions:
        print(f"Leyendo {r}…")
        pools[r] = discover(r)
        print(f"  {len(pools[r])} camera links found")
    if not any(pools.values()):
        sys.exit("No links found: the website may have changed or blocked the request. "
                 "Open a real camera in the browser and send me its URL.")

    out, fixed, same, missing = [], 0, 0, []
    for c in SPAIN_CAMS:
        region = c["page"][len(_BASE):].split("/")[1]
        url, score = best_match(c, pools.get(region, {}))
        if url is None:
            missing.append(c)
            if fallback:
                out.append({"id": c["id"], "page": f"{_BASE}espana/{region}.html"})
            continue
        if url == c["page"]:
            same += 1
            continue
        fixed += 1
        out.append({"id": c["id"], "page": url})
        print(f"  {c['name']:<38} {score:4.2f}  {url}")
    print(f"\nCorregidas: {fixed} · ya correctas: {same} · sin encontrar: {len(missing)}")
    for c in missing:
        print(f"  sin enlace: {c['id']}  ({c['name']}, {c['place']})")
    if dry:
        return
    prev = []
    if USER_FILE.exists():
        try:
            prev = json.loads(USER_FILE.read_text(encoding="utf-8"))
            prev = prev.get("cams", []) if isinstance(prev, dict) else prev
        except Exception:  # noqa: BLE001
            prev = []
    keep = [p for p in prev if p.get("id") not in {o["id"] for o in out}]
    USER_FILE.parent.mkdir(exist_ok=True)
    USER_FILE.write_text(json.dumps(keep + out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Escrito {USER_FILE}")


if __name__ == "__main__":
    main()
