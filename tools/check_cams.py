"""Checks which cameras of the catalogue (core/live_cams.py + config/live_cams.json) are still alive.

    python tools/check_cams.py

For each camera it requests its page and prints the HTTP code. A 200/3xx is a
good sign; a 404/410 means the website changed the path (fix it in
config/live_cams.json with the same `id`); 403 is usually just an anti-bot protection of the
website: in that case open the URL manually to confirm. A page that loads does NOT guarantee
that the live stream is broadcasting right now (that can only be seen in the window itself).
"""
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.live_cams import load_cams  # noqa: E402

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

for c in load_cams():
    req = urllib.request.Request(c["page"], headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            status = f"{r.status}"
    except urllib.error.HTTPError as e:
        status = f"{e.code}"
    except Exception as e:  # noqa: BLE001
        status = f"ERROR ({type(e).__name__})"
    print(f"{status:>12}  {c['id']:<28} {c['page']}")
