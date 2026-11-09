"""
osint_profiles.py  --  Local profile library for the J.A.R.V.I.S. OSINT module.

No Qt dependencies: it can be tested and reused on its own.

* Profiles are created ONLY when the user decides (UI button); this module
  never saves anything on its own.
* Everything lives in  <base>/memory/osint_profiles/
      profiles.json     -> list of profiles
      photos/           -> own copy of each photo (+ optional thumbnail)
* Atomic writes (temporary file + os.replace) and a .bak copy if the JSON is corrupted,
  so the library is not lost to an abrupt shutdown.

Profile schema (all fields are editable):
    id, name, photo, aliases[str], locations[{label,address,lat,lon,note}],
    links[{label,url}], fields[{key,value}], tags[str], context, notes,
    created, updated
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 1


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def default_dir() -> Path:
    return get_base_dir() / "memory" / "osint_profiles"


# ── Normalizacion ─────────────────────────────────────────────────────────────

def _s(v) -> str:
    return "" if v is None else str(v).strip()


def _num(v):
    try:
        if v is None or str(v).strip() == "":
            return None
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def parse_coords(text: str):
    """'41.38, 2.17' / '41.38 2.17' / '41,38;2,17' -> (lat, lon) o None."""
    t = _s(text)
    if not t:
        return None
    nums = re.findall(r"-?\d+(?:[.,]\d+)?", t)
    if len(nums) != 2:
        return None
    lat, lon = _num(nums[0]), _num(nums[1])
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def format_coords(lat, lon) -> str:
    la, lo = _num(lat), _num(lon)
    return "" if la is None or lo is None else f"{la:.6f}, {lo:.6f}"


def new_profile(**kw) -> dict:
    now = datetime.now().isoformat(timespec="seconds")
    p = {"id": uuid.uuid4().hex[:12], "name": "", "photo": "", "aliases": [], "locations": [],
         "links": [], "fields": [], "tags": [], "context": "", "notes": "",
         "created": now, "updated": now}
    p.update(kw)
    return normalize(p)


def normalize(p: dict) -> dict:
    """Returns a clean copy with every field; tolerates incomplete data."""
    p = dict(p or {})
    out = {
        "id": _s(p.get("id")) or uuid.uuid4().hex[:12],
        "name": _s(p.get("name")),
        "photo": _s(p.get("photo")),
        "context": _s(p.get("context")),
        "notes": str(p.get("notes") or "").strip(),
        "created": _s(p.get("created")) or datetime.now().isoformat(timespec="seconds"),
        "updated": _s(p.get("updated")) or datetime.now().isoformat(timespec="seconds"),
    }
    seen, aliases = set(), []
    for a in p.get("aliases") or []:
        a = _s(a).lstrip("@").strip()
        if a and a.lower() not in seen:
            seen.add(a.lower())
            aliases.append(a)
    out["aliases"] = aliases
    seen, tags = set(), []
    for t in p.get("tags") or []:
        t = _s(t).lstrip("#").strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            tags.append(t)
    out["tags"] = tags
    locs = []
    for l in p.get("locations") or []:
        if not isinstance(l, dict):
            continue
        item = {"label": _s(l.get("label")), "address": _s(l.get("address")),
                "lat": _num(l.get("lat")), "lon": _num(l.get("lon")), "note": _s(l.get("note"))}
        if item["lat"] is not None and not -90 <= item["lat"] <= 90:
            item["lat"] = item["lon"] = None
        if item["lon"] is not None and not -180 <= item["lon"] <= 180:
            item["lat"] = item["lon"] = None
        if item["lat"] is None or item["lon"] is None:
            item["lat"] = item["lon"] = None
        if item["label"] or item["address"] or item["lat"] is not None or item["note"]:
            locs.append(item)
    out["locations"] = locs
    out["links"] = [{"label": _s(l.get("label")), "url": _s(l.get("url"))}
                    for l in (p.get("links") or [])
                    if isinstance(l, dict) and (_s(l.get("url")) or _s(l.get("label")))]
    out["fields"] = [{"key": _s(f.get("key")), "value": _s(f.get("value"))}
                     for f in (p.get("fields") or [])
                     if isinstance(f, dict) and (_s(f.get("key")) or _s(f.get("value")))]
    return out


def display_name(p: dict) -> str:
    return p.get("name") or (p.get("aliases") or [""])[0] or "SIN NOMBRE"


def matches(p: dict, query: str) -> bool:
    q = _s(query).lower()
    if not q:
        return True
    hay = [p.get("name"), p.get("context"), p.get("notes")]
    hay += p.get("aliases", []) + p.get("tags", [])
    for l in p.get("locations", []):
        hay += [l.get("label"), l.get("address"), l.get("note")]
    for l in p.get("links", []):
        hay += [l.get("label"), l.get("url")]
    for f in p.get("fields", []):
        hay += [f.get("key"), f.get("value")]
    blob = " ".join(_s(x) for x in hay).lower()
    return all(tok in blob for tok in q.split())


# ── Almacen ───────────────────────────────────────────────────────────────────

class ProfileStore:
    def __init__(self, base: Path | str | None = None):
        self.base = Path(base) if base else default_dir()
        self.file = self.base / "profiles.json"
        self.photos = self.base / "photos"
        self._lock = threading.RLock()
        self._profiles: dict[str, dict] = {}
        self.load_error = ""
        self.load()

    # -- E/S --
    def load(self) -> None:
        with self._lock:
            self._profiles = {}
            self.load_error = ""
            if not self.file.is_file():
                return
            try:
                data = json.loads(self.file.read_text(encoding="utf-8"))
                items = data.get("profiles", []) if isinstance(data, dict) else data
                for raw in items:
                    p = normalize(raw)
                    self._profiles[p["id"]] = p
            except Exception as e:
                bak = self.file.with_suffix(f".corrupt-{int(time.time())}.bak")
                try:
                    shutil.copy2(self.file, bak)
                except Exception:
                    pass
                self._profiles = {}
                self.load_error = f"profiles.json ilegible ({e}); copia en {bak.name}"

    def _write(self) -> None:
        self.base.mkdir(parents=True, exist_ok=True)
        payload = {"version": SCHEMA_VERSION,
                   "profiles": sorted(self._profiles.values(), key=lambda x: x["created"])}
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.file)

    # -- consulta --
    def all(self, query: str = "") -> list[dict]:
        with self._lock:
            rows = [dict(p) for p in self._profiles.values() if matches(p, query)]
        rows.sort(key=lambda p: p.get("updated", ""), reverse=True)
        return rows

    def get(self, pid: str) -> dict | None:
        with self._lock:
            p = self._profiles.get(pid)
            return json.loads(json.dumps(p)) if p else None

    def count(self) -> int:
        with self._lock:
            return len(self._profiles)

    def photo_path(self, p: dict, thumb: bool = False) -> str:
        rel = p.get("photo") or ""
        if not rel:
            return ""
        full = self.base / rel
        if thumb:
            th = full.with_name(full.stem + "_t.jpg")
            if th.is_file():
                return str(th)
        return str(full) if full.is_file() else ""

    # -- escritura --
    def save(self, profile: dict, photo_src: str = "", thumb_writer=None) -> dict:
        """Create or update. `photo_src` = path of a NEW image to copy (or '' to leave unchanged).
        To remove the photo, pass profile['photo'] = '' and photo_src = ''.
        `thumb_writer(src, dst)` (optional) generates a thumbnail; supplied by the UI."""
        with self._lock:
            p = normalize(profile)
            old = self._profiles.get(p["id"])
            if old:
                p["created"] = old["created"]
            p["updated"] = datetime.now().isoformat(timespec="seconds")
            keep_old_photo = old["photo"] if old else ""

            src = Path(photo_src) if photo_src else None
            if src and src.is_file():
                self.photos.mkdir(parents=True, exist_ok=True)
                dst = self.photos / f"{p['id']}_{time.time_ns() // 1000}{src.suffix.lower() or '.img'}"
                shutil.copy2(src, dst)
                if thumb_writer:
                    try:
                        thumb_writer(str(dst), str(dst.with_name(dst.stem + "_t.jpg")))
                    except Exception:
                        pass
                p["photo"] = f"photos/{dst.name}"
            elif not p["photo"]:
                p["photo"] = ""
            else:                                  # conserva la foto ya almacenada
                p["photo"] = keep_old_photo if keep_old_photo else p["photo"]

            if keep_old_photo and keep_old_photo != p["photo"]:
                self._remove_photo_files(keep_old_photo)
            self._profiles[p["id"]] = p
            self._write()
            return dict(p)

    def delete(self, pid: str) -> bool:
        with self._lock:
            p = self._profiles.pop(pid, None)
            if not p:
                return False
            self._remove_photo_files(p.get("photo", ""))
            self._write()
            return True

    def _remove_photo_files(self, rel: str) -> None:
        if not rel:
            return
        try:
            full = (self.base / rel).resolve()
            if self.photos.resolve() not in full.parents:     # nunca borra fuera de photos/
                return
            for f in (full, full.with_name(full.stem + "_t.jpg")):
                if f.is_file():
                    f.unlink()
        except Exception:
            pass
