"""
Core of the MARKET RADAR: favourites, pinned assets, settings and spoken alerts.
No Qt: used by the HUD widget, the voice plugin and the main.py loop.

  RadarStore    → config/market_radar.json  (assets, HUD and alert settings)
  MarketWatcher → decides when JARVIS speaks without being asked:
                    · a news item that scores as important, or
                    · an abnormal price movement,
                  only for assets with the alert bell enabled.

Model of a saved asset (favourite):
  visible → appears on the HUD         pinned → pinned: always on top and
  alerts  → JARVIS may notify you               visible in the compact strip
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from core import market_data as md


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_CFG_FILE   = _base_dir() / "config" / "market_radar.json"
_STATE_FILE = _base_dir() / "config" / "market_radar_state.json"

DEFAULT_ASSETS = [
    # The sample assets do NOT trigger spoken alerts: JARVIS only speaks about what you mark.
    {"symbol": "BTC-USD", "name": "Bitcoin",  "kind": "crypto", "visible": True, "pinned": True,  "alerts": False},
    {"symbol": "ETH-USD", "name": "Ethereum", "kind": "crypto", "visible": True, "pinned": False, "alerts": False},
    {"symbol": "AAPL",    "name": "Apple",    "kind": "stock",  "visible": True, "pinned": False, "alerts": False},
    {"symbol": "NVDA",    "name": "NVIDIA",   "kind": "stock",  "visible": True, "pinned": False, "alerts": False},
]

DEFAULT_UI = {
    "fx": 0.02, "fy": 0.98,          # position (fraction of the panel's free space)
    "w": 400, "h": 250,
    "collapsed": False,              # compact strip with the pinned assets
    "opacity": 0.92,
    "accent": "",                    # "" = follow the HUD colour; or "#rrggbb"
    "chart_type": "area",            # line | area | candles
    "range": "1D",
    "show_list": True, "show_news": True, "show_volume": False,
    "sparklines": True, "locked": False,
    "refresh_secs": 60,
    "selected": "BTC-USD",
}

DEFAULT_ALERTS = {
    "voice": True,
    "sensitivity": "medium",         # low | medium | high
    "move_pct_crypto": 6.0,          # movimiento diario que dispara aviso
    "move_pct_stock": 4.0,
    "cooldown_min": 20,              # minimum gap between two spoken alerts
    "max_per_day": 6,
    "poll_min": 10,
    "quiet_enabled": False,
    "quiet_start": "23:00", "quiet_end": "08:00",
}

SENSITIVITY = {"low": 5, "medium": 4, "high": 3}   # minimum news points


class RadarStore:
    """Persistent state, thread-safe. `version` increases with every change: the
    widget polls it every second and repaints if it changed (so the voice thread and
    the interface thread never overwrite each other)."""

    def __init__(self, path: Path = _CFG_FILE):
        self._path = path
        self._lock = threading.RLock()
        self.version = 0
        self._data = self._load()

    # ── persistencia ──────────────────────────────────────────────────────────
    def _load(self) -> dict:
        data = {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if not isinstance(data.get("assets"), list):
            data["assets"] = [dict(a) for a in DEFAULT_ASSETS]
        data["ui"] = {**DEFAULT_UI, **(data.get("ui") or {})}
        data["alerts"] = {**DEFAULT_ALERTS, **(data.get("alerts") or {})}
        return data

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._path)
        except Exception as e:
            print(f"[Radar] Could not save the configuration: {e}")

    def _touch(self) -> None:
        self.version += 1
        self._save()

    def bump(self) -> None:
        """Tell the interface that something changed outside the settings (e.g. a new alert)."""
        self.version += 1

    # ── activos ───────────────────────────────────────────────────────────────
    def assets(self) -> list[dict]:
        """Sorted copy: pinned first, then the user's order."""
        with self._lock:
            items = [dict(a) for a in self._data["assets"]]
        return [a for a in items if a.get("pinned")] + [a for a in items if not a.get("pinned")]

    def get(self, symbol: str) -> dict | None:
        s = (symbol or "").upper()
        with self._lock:
            for a in self._data["assets"]:
                if a["symbol"].upper() == s:
                    return dict(a)
        return None

    def add(self, symbol: str, name: str = "", kind: str = "stock", *, pinned=False,
            visible=True, alerts=True) -> dict:
        s = symbol.upper()
        with self._lock:
            for a in self._data["assets"]:
                if a["symbol"].upper() == s:                  # ya estaba: se reactiva
                    a["visible"] = True
                    a["alerts"] = a.get("alerts", True) or alerts
                    if pinned:
                        a["pinned"] = True
                    self._touch()
                    return dict(a)
            a = {"symbol": s, "name": name or md.pretty_symbol(s), "kind": kind,
                 "visible": visible, "pinned": pinned, "alerts": alerts}
            self._data["assets"].append(a)
            self._touch()
            return dict(a)

    def remove(self, symbol: str) -> bool:
        s = symbol.upper()
        with self._lock:
            n = len(self._data["assets"])
            self._data["assets"] = [a for a in self._data["assets"] if a["symbol"].upper() != s]
            changed = len(self._data["assets"]) != n
            if changed:
                if self._data["ui"].get("selected", "").upper() == s:
                    rest = self._data["assets"]
                    self._data["ui"]["selected"] = rest[0]["symbol"] if rest else ""
                self._touch()
            return changed

    def set_flag(self, symbol: str, key: str, value: bool) -> bool:
        if key not in ("visible", "pinned", "alerts"):
            return False
        s = symbol.upper()
        with self._lock:
            for a in self._data["assets"]:
                if a["symbol"].upper() == s:
                    a[key] = bool(value)
                    self._touch()
                    return True
        return False

    def toggle_flag(self, symbol: str, key: str) -> bool | None:
        a = self.get(symbol)
        if a is None:
            return None
        new = not a.get(key, False)
        self.set_flag(symbol, key, new)
        return new

    # ── ajustes ───────────────────────────────────────────────────────────────
    def ui(self) -> dict:
        with self._lock:
            return dict(self._data["ui"])

    def alerts(self) -> dict:
        with self._lock:
            return dict(self._data["alerts"])

    def set_ui(self, **kw) -> None:
        with self._lock:
            self._data["ui"].update(kw)
            self._touch()

    def set_alerts(self, **kw) -> None:
        with self._lock:
            self._data["alerts"].update(kw)
            self._touch()


_store: RadarStore | None = None
_store_lock = threading.RLock()


def get_store() -> RadarStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = RadarStore()
        return _store


# ── News scoring ──────────────────────────────────────────────────────────────

# Each pattern adds points according to what usually moves an asset. Headlines
# arrive in English.
_TIER3 = (r"\bsec\b", r"lawsuit", r"\bsues?\b", r"sued\b", r"\bhack(ed|s|ing)?\b", r"exploit",
          r"bankrupt", r"insolven", r"\bfraud", r"investigation", r"\bprobe\b", r"\bban(s|ned)?\b",
          r"\bhalt(s|ed)?\b", r"delist", r"etf approv", r"approves? .*etf", r"\bsubpoena",
          r"recall", r"antitrust", r"\bfda\b", r"prohibit", r"suspend")
_TIER2 = (r"earnings", r"guidance", r"acquir", r"acquisition", r"merger", r"\bbuyout", r"takeover",
          r"downgrad", r"upgrad", r"layoffs?", r"resign", r"steps? down", r"\bceo\b", r"\bipo\b",
          r"plunge", r"plummet", r"crash", r"tumble", r"surge", r"soar", r"skyrocket", r"spike",
          r"all-time high", r"record high", r"halving", r"hard fork", r"liquidat", r"outage",
          r"stock split", r"dividend", r"buyback")
_TIER1 = (r"rall(y|ies)", r"jump", r"slump", r"drop(s|ped)?\b", r"rises?\b", r"falls?\b",
          r"forecast", r"outlook", r"regulat", r"partnership", r"launch", r"announce",
          r"price target", r"warns?\b", r"whale")
_RE3 = [re.compile(p, re.I) for p in _TIER3]
_RE2 = [re.compile(p, re.I) for p in _TIER2]
_RE1 = [re.compile(p, re.I) for p in _TIER1]
_FLUFF = re.compile(r"\b(top \d+|reasons? to|should you|is it (a )?(good|worth)|"
                    r"best .* to buy|motley fool|how to|beginner)\b", re.I)


def score_headline(title: str, *, symbol: str = "", name: str = "", ts: int = 0,
                   pinned: bool = False, move_pct: float = 0.0, move_ref: float = 4.0,
                   extra_keywords: list[str] | None = None) -> int:
    """Puntos de importancia de un titular. ≥4 (sensibilidad media) = avisar."""
    t = title or ""
    pts = 0
    pts += 3 if any(r.search(t) for r in _RE3) else 0
    pts += 2 if any(r.search(t) for r in _RE2) else 0
    pts += 1 if any(r.search(t) for r in _RE1) else 0
    for kw in extra_keywords or []:
        if kw and kw.lower() in t.lower():
            pts += 2
            break
    low = t.lower()
    base = md.pretty_symbol(symbol).lower()
    nm = (name or "").lower().split(" ")[0]
    if (len(base) >= 3 and re.search(rf"\b{re.escape(base)}\b", low)) or (len(nm) >= 3 and nm in low):
        pts += 1
    if ts:
        age_h = (time.time() - ts) / 3600.0
        if age_h <= 3:
            pts += 1
        elif age_h > 24:
            pts -= 2
    if move_ref > 0 and abs(move_pct) >= move_ref * 0.5:
        pts += 1
    if pinned:
        pts += 1
    if _FLUFF.search(t):
        pts -= 3
    return pts


def _norm_tokens(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", title.lower())}


def _similar(a: str, b: str) -> bool:
    ta, tb = _norm_tokens(a), _norm_tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= 0.6


# ── Vigilante ─────────────────────────────────────────────────────────────────

@dataclass
class Alert:
    symbol: str
    name: str
    kind: str                     # 'news' | 'move'
    headline: str = ""
    publisher: str = ""
    link: str = ""
    price: float = 0.0
    currency: str = "USD"
    change_pct: float = 0.0
    score: int = 0
    key: str = ""
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "symbol", "name", "kind", "headline", "publisher", "link",
            "price", "currency", "change_pct", "score", "ts")}

    def to_prompt(self, language: str = "English") -> str:
        px = md.format_price(self.price, self.currency)
        head = [f"[MARKET_ALERT] {self.name} ({md.pretty_symbol(self.symbol)})",
                f"Price now: {px} ({self.change_pct:+.1f}% today)"]
        if self.kind == "news":
            head.append(f"Headline: {self.headline}")
            if self.publisher:
                head.append(f"Source: {self.publisher}")
        else:
            head.append("Reason: unusually large price move today.")
        head.append(
            f"\nTell the user about this naturally in {language}: one or two short "
            "sentences, say which asset and what happened, include the move if it helps. "
            "This is information only — never give buy/sell advice, never read out URLs, "
            "and do not call any tools.")
        return "\n".join(head)


class MarketWatcher:
    """collect() checks prices and headlines (call from a thread, it is slow); take() delivers
    the alerts that pass the frequency filter (cooldown, daily cap, quiet
    hours). Whatever is not delivered stays pending for the next cycle."""

    def __init__(self, store: RadarStore | None = None, state_path: Path = _STATE_FILE):
        self.store = store or get_store()
        self._path = state_path
        self._lock = threading.Lock()
        self._pending: list[Alert] = []
        self._state = self._load_state()
        self._last_collect = 0.0

    # state across launches: headlines already seen and alerts of the day
    def _load_state(self) -> dict:
        try:
            st = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(st, dict):
                st.setdefault("seen", {}); st.setdefault("announced", [])
                st.setdefault("spoken", []); st.setdefault("log", []); st.setdefault("move_levels", {})
                return st
        except Exception:
            pass
        return {"seen": {}, "announced": [], "spoken": [], "log": [], "move_levels": {}}

    def _save_state(self) -> None:
        try:
            now = time.time()
            self._state["seen"] = {k: v for k, v in self._state["seen"].items() if now - v < 3 * 86400}
            self._state["announced"] = self._state["announced"][-40:]
            self._state["log"] = self._state["log"][-30:]
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._state, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._path)
        except Exception as e:
            print(f"[Radar] No se pudo guardar el estado: {e}")

    def recent_log(self) -> list[dict]:
        with self._lock:
            return list(self._state["log"])

    def due(self) -> bool:
        poll = max(3, int(self.store.alerts().get("poll_min", 10)))
        return (time.time() - self._last_collect) >= poll * 60

    def has_pending(self) -> bool:
        with self._lock:
            return bool(self._pending)

    # ── recogida ──────────────────────────────────────────────────────────────
    def collect(self) -> int:
        """Check every asset with alerts enabled. Returns how many candidates there are."""
        self._last_collect = time.time()
        cfg = self.store.alerts()
        if not cfg.get("voice", True):
            return 0
        need = SENSITIVITY.get(cfg.get("sensitivity", "medium"), 4)
        today = datetime.now().strftime("%Y-%m-%d")
        found: list[Alert] = []
        for a in self.store.assets():
            if not a.get("alerts"):
                continue
            sym, name = a["symbol"], a.get("name", "")
            ref = float(cfg.get("move_pct_crypto" if a.get("kind") == "crypto" else "move_pct_stock", 5.0))
            q = md.fetch_quote(sym)
            if q is None:
                continue
            pct = q["change_pct"]
            base = dict(symbol=sym, name=name or q["name"], price=q["price"],
                        currency=q["currency"], change_pct=pct)

            # 1) Price movement: another alert only if it grows by another "step" that day.
            step = int(abs(pct) // ref) if ref > 0 else 0
            lvl_key = f"{sym}:{today}"
            if step >= 1:
                with self._lock:
                    done = self._state["move_levels"].get(lvl_key, 0)
                if step > done:
                    found.append(Alert(kind="move", score=need + step, key=f"move:{lvl_key}:{step}", **base))

            # 2) Noticias
            news = md.fetch_news(sym, name)
            first_run = not any(k.startswith(f"n:{sym}:") for k in self._state["seen"])
            for n in news:
                nk = f"n:{sym}:{hashlib.md5(n['title'].encode('utf-8', 'ignore')).hexdigest()[:12]}"
                with self._lock:
                    if nk in self._state["seen"]:
                        continue
                sc = score_headline(n["title"], symbol=sym, name=name, ts=n["ts"],
                                    pinned=bool(a.get("pinned")), move_pct=pct, move_ref=ref)
                fresh = n["ts"] and (time.time() - n["ts"]) < 90 * 60
                # First time we see the asset: old items are treated as seen, not narrated.
                if first_run and not (fresh and sc >= need):
                    with self._lock:
                        self._state["seen"][nk] = time.time()
                    continue
                if sc >= need:
                    found.append(Alert(kind="news", headline=n["title"], publisher=n["publisher"],
                                       link=n["link"], score=sc, key=nk, **base))
                else:
                    with self._lock:
                        self._state["seen"][nk] = time.time()
        found.sort(key=lambda x: x.score, reverse=True)
        with self._lock:
            known = {p.key for p in self._pending}
            self._pending = [p for p in self._pending if p.ts > time.time() - 12 * 3600]
            self._pending += [f for f in found if f.key not in known]
            n = len(self._pending)
        self._save_state()
        return n

    # ── entrega ───────────────────────────────────────────────────────────────
    def _quiet_now(self, cfg: dict) -> bool:
        if not cfg.get("quiet_enabled"):
            return False
        try:
            h1, m1 = map(int, str(cfg["quiet_start"]).split(":"))
            h2, m2 = map(int, str(cfg["quiet_end"]).split(":"))
        except Exception:
            return False
        now = datetime.now()
        cur, a, b = now.hour * 60 + now.minute, h1 * 60 + m1, h2 * 60 + m2
        return (a <= cur < b) if a <= b else (cur >= a or cur < b)

    def take(self, max_n: int = 1) -> list[Alert]:
        """Alerts that can be spoken NOW. Marks only the delivered ones as seen."""
        cfg = self.store.alerts()
        if not cfg.get("voice", True) or self._quiet_now(cfg):
            return []
        now = time.time()
        with self._lock:
            self._state["spoken"] = [t for t in self._state["spoken"] if now - t < 86400]
            if len(self._state["spoken"]) >= int(cfg.get("max_per_day", 6)):
                return []
            last = self._state["spoken"][-1] if self._state["spoken"] else 0
            if now - last < int(cfg.get("cooldown_min", 20)) * 60:
                return []
            out: list[Alert] = []
            for al in list(self._pending):
                if len(out) >= max_n:
                    break
                if any(_similar(al.headline, t) for t in self._state["announced"] if al.headline):
                    self._pending.remove(al)             # the same event was already reported
                    self._state["seen"][al.key] = now
                    continue
                out.append(al)
                self._pending.remove(al)
                self._state["seen"][al.key] = now
                if al.kind == "move":
                    k, lvl = al.key.rsplit(":", 1)
                    self._state["move_levels"][k.replace("move:", "", 1)] = int(lvl)
                if al.headline:
                    self._state["announced"].append(al.headline)
                self._state["spoken"].append(now)
                self._state["log"].append(al.to_dict())
            if not out:
                return []
        self._save_state()
        self.store.bump()
        return out


_watcher: MarketWatcher | None = None


def get_watcher() -> MarketWatcher:
    global _watcher
    with _store_lock:
        if _watcher is None:
            _watcher = MarketWatcher()
        return _watcher
