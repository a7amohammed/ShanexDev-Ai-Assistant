"""
Data layer of the MARKET RADAR (stocks + crypto). No Qt, no API keys.

Sources (all public, no registration):
  · Yahoo Finance  → quotes, OHLC charts and news, for stocks AND crypto
                     (cryptocurrencies are pairs such as "BTC-USD").
  · CoinGecko      → only as a fallback for crypto if Yahoo fails.
  · ddgs           → only as a news fallback if Yahoo returns nothing
                     (already a project dependency, see actions/web_search.py).

Everything is "best effort": no function raises; if there is no network or the source
changes, they return None / [] and leave the reason in `last_error()`. Results are
cached for a few seconds so that the widget and the news watcher, which
request the same thing at once, do not duplicate requests.

The data is informational and may be delayed; this is not financial
advice.
"""
from __future__ import annotations

import re
import threading
import time
from urllib.parse import quote as _urlq

try:
    import requests
except Exception:  # pragma: no cover — requests is in requirements.txt
    requests = None


_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
       "Accept": "application/json,text/plain,*/*"}

_YF_CHART  = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
_YF_SEARCH = "https://query1.finance.yahoo.com/v1/finance/search"
_CG_CHART  = "https://api.coingecko.com/api/v3/coins/{cid}/market_chart"

# Range of the large chart → Yahoo (range, interval) and CoinGecko days.
RANGES: dict[str, tuple[str, str, int]] = {
    "1D": ("1d", "5m", 1),
    "5D": ("5d", "30m", 5),
    "1M": ("1mo", "1d", 30),
    "6M": ("6mo", "1d", 180),
    "1Y": ("1y", "1d", 365),
    "5Y": ("5y", "1wk", 1825),
}
RANGE_ORDER = ["1D", "5D", "1M", "6M", "1Y", "5Y"]

# Common cryptocurrencies: name/alias → (Yahoo symbol, name, CoinGecko id)
_CRYPTO: dict[str, tuple[str, str, str]] = {
    "btc": ("BTC-USD", "Bitcoin", "bitcoin"),
    "bitcoin": ("BTC-USD", "Bitcoin", "bitcoin"),
    "eth": ("ETH-USD", "Ethereum", "ethereum"),
    "ethereum": ("ETH-USD", "Ethereum", "ethereum"),
    "ether": ("ETH-USD", "Ethereum", "ethereum"),
    "sol": ("SOL-USD", "Solana", "solana"),
    "solana": ("SOL-USD", "Solana", "solana"),
    "xrp": ("XRP-USD", "XRP", "ripple"),
    "ripple": ("XRP-USD", "XRP", "ripple"),
    "bnb": ("BNB-USD", "BNB", "binancecoin"),
    "ada": ("ADA-USD", "Cardano", "cardano"),
    "cardano": ("ADA-USD", "Cardano", "cardano"),
    "doge": ("DOGE-USD", "Dogecoin", "dogecoin"),
    "dogecoin": ("DOGE-USD", "Dogecoin", "dogecoin"),
    "avax": ("AVAX-USD", "Avalanche", "avalanche-2"),
    "avalanche": ("AVAX-USD", "Avalanche", "avalanche-2"),
    "dot": ("DOT-USD", "Polkadot", "polkadot"),
    "polkadot": ("DOT-USD", "Polkadot", "polkadot"),
    "link": ("LINK-USD", "Chainlink", "chainlink"),
    "chainlink": ("LINK-USD", "Chainlink", "chainlink"),
    "ltc": ("LTC-USD", "Litecoin", "litecoin"),
    "litecoin": ("LTC-USD", "Litecoin", "litecoin"),
    "trx": ("TRX-USD", "TRON", "tron"),
    "ton": ("TON11419-USD", "Toncoin", "the-open-network"),
    "shib": ("SHIB-USD", "Shiba Inu", "shiba-inu"),
    "matic": ("MATIC-USD", "Polygon", "matic-network"),
    "xlm": ("XLM-USD", "Stellar", "stellar"),
    "atom": ("ATOM-USD", "Cosmos", "cosmos"),
}
_CG_BY_SYMBOL = {v[0]: v[2] for v in _CRYPTO.values()}

_lock = threading.Lock()
_cache: dict[tuple, tuple[float, object]] = {}
_blocked_until = 0.0          # parada corta tras un 429 / bloqueo
_last_error = ""


def last_error() -> str:
    return _last_error


def _set_error(msg: str) -> None:
    global _last_error
    _last_error = msg


def _cached(key: tuple, ttl: float):
    with _lock:
        hit = _cache.get(key)
    if hit and (time.time() - hit[0]) < ttl:
        return hit[1]
    return None


def _store(key: tuple, value):
    with _lock:
        _cache[key] = (time.time(), value)
        if len(_cache) > 400:                        # simple pruning, so it cannot grow without limit
            for k in sorted(_cache, key=lambda k: _cache[k][0])[:100]:
                _cache.pop(k, None)
    return value


def _get_json(url: str, params: dict | None = None, timeout: float = 8.0):
    """GET → JSON, or None. Honours a pause after a 429 so the source is not hammered."""
    global _blocked_until
    if requests is None:
        _set_error("falta el paquete 'requests'")
        return None
    if time.time() < _blocked_until:
        _set_error("fuente de datos en pausa (demasiadas peticiones)")
        return None
    try:
        r = requests.get(url, params=params, headers=_UA, timeout=timeout)
        if r.status_code == 429:
            _blocked_until = time.time() + 90
            _set_error("request limit reached (429)")
            return None
        if r.status_code != 200:
            _set_error(f"HTTP {r.status_code}")
            return None
        return r.json()
    except Exception as e:  # network down, invalid JSON, timeout…
        _set_error(str(e)[:120])
        return None


# ── Symbol utilities ──────────────────────────────────────────────────────────

_TICKER_RE = re.compile(r"^[A-Za-z0-9.\-^=]{1,12}$")


def is_crypto_symbol(symbol: str) -> bool:
    s = (symbol or "").upper()
    return s.endswith("-USD") or s.endswith("-EUR") or s.endswith("-USDT")


def pretty_symbol(symbol: str) -> str:
    """BTC-USD → BTC (for short labels); anything else is returned unchanged."""
    s = (symbol or "").upper()
    return s.split("-")[0] if is_crypto_symbol(s) else s


def format_price(v: float | None, currency: str = "USD") -> str:
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1000:
        txt = f"{v:,.2f}"
    elif a >= 1:
        txt = f"{v:,.2f}"
    elif a >= 0.01:
        txt = f"{v:.4f}"
    else:
        txt = f"{v:.6f}"
    sym = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}.get((currency or "").upper())
    return f"{sym}{txt}" if sym else f"{txt} {currency}".strip()


# ── Asset search / resolution ─────────────────────────────────────────────────

def search(query: str, limit: int = 8) -> list[dict]:
    """Search stocks, ETFs, indices and cryptocurrencies by name or ticker."""
    q = (query or "").strip()
    if not q:
        return []
    key = ("search", q.lower(), limit)
    hit = _cached(key, 300)
    if hit is not None:
        return hit
    out: list[dict] = []
    # Atajo local: criptos conocidas, sin red.
    c = _CRYPTO.get(q.lower())
    if c:
        out.append({"symbol": c[0], "name": c[1], "kind": "crypto", "exchange": "Crypto"})
    j = _get_json(_YF_SEARCH, {"q": q, "quotesCount": limit, "newsCount": 0,
                               "lang": "en-US", "region": "US"})
    for it in (j or {}).get("quotes", []) or []:
        qt = str(it.get("quoteType", "")).upper()
        sym = it.get("symbol")
        if not sym or qt not in ("EQUITY", "ETF", "CRYPTOCURRENCY", "INDEX"):
            continue
        if any(o["symbol"] == sym for o in out):
            continue
        out.append({
            "symbol": sym,
            "name": it.get("longname") or it.get("shortname") or sym,
            "kind": "crypto" if qt == "CRYPTOCURRENCY" else "stock",
            "exchange": it.get("exchDisp") or it.get("exchange") or "",
        })
    out = out[:limit]
    if out or j is not None:
        _store(key, out)
    return out


def resolve(text: str) -> dict | None:
    """'tesla' / 'TSLA' / 'btc' / 'bitcoin' → {'symbol','name','kind'} o None."""
    t = (text or "").strip()
    if not t:
        return None
    c = _CRYPTO.get(t.lower())
    if c:
        return {"symbol": c[0], "name": c[1], "kind": "crypto", "exchange": "Crypto"}
    if is_crypto_symbol(t) and _TICKER_RE.match(t):
        return {"symbol": t.upper(), "name": pretty_symbol(t), "kind": "crypto", "exchange": "Crypto"}
    res = search(t, 6)
    if res:
        exact = [r for r in res if r["symbol"].upper() == t.upper()]
        return (exact or res)[0]
    # No network or no results: if it looks like a ticker, accept it unverified.
    if _TICKER_RE.match(t) and t.upper() == t or (_TICKER_RE.match(t) and len(t) <= 5):
        return {"symbol": t.upper(), "name": t.upper(), "kind": "stock", "exchange": ""}
    return None


# ── Quotes and charts ─────────────────────────────────────────────────────────

def _downsample(vals: list[float], n: int) -> list[float]:
    if len(vals) <= n:
        return vals
    step = len(vals) / n
    return [vals[int(i * step)] for i in range(n - 1)] + [vals[-1]]


def _parse_yahoo(j: dict) -> dict | None:
    try:
        res = j["chart"]["result"][0]
        meta = res.get("meta", {}) or {}
        ts = res.get("timestamp") or []
        q = (res.get("indicators", {}).get("quote") or [{}])[0]
        rows = []
        for i, t in enumerate(ts):
            c = (q.get("close") or [None] * len(ts))[i]
            if c is None:
                continue
            o = (q.get("open") or [None] * len(ts))[i]
            h = (q.get("high") or [None] * len(ts))[i]
            lo = (q.get("low") or [None] * len(ts))[i]
            v = (q.get("volume") or [None] * len(ts))[i]
            rows.append((t, o if o is not None else c, h if h is not None else c,
                         lo if lo is not None else c, c, v or 0))
        if not rows:
            return None
        price = meta.get("regularMarketPrice")
        if price is None:
            price = rows[-1][4]
        return {
            "symbol": meta.get("symbol", ""),
            "name": meta.get("longName") or meta.get("shortName") or meta.get("symbol", ""),
            "currency": meta.get("currency", "USD"),
            "exchange": meta.get("exchangeName", ""),
            "market_state": meta.get("marketState", ""),
            "price": float(price),
            "prev_close": meta.get("chartPreviousClose") or meta.get("previousClose"),
            "ts": [r[0] for r in rows],
            "open": [r[1] for r in rows], "high": [r[2] for r in rows],
            "low": [r[3] for r in rows], "close": [r[4] for r in rows],
            "volume": [r[5] for r in rows],
        }
    except Exception as e:
        _set_error(f"respuesta inesperada: {e}")
        return None


def _parse_coingecko(j: dict, symbol: str) -> dict | None:
    try:
        pts = j.get("prices") or []
        if not pts:
            return None
        ts = [int(p[0] // 1000) for p in pts]
        close = [float(p[1]) for p in pts]
        vols = [float(v[1]) for v in (j.get("total_volumes") or [])][:len(close)]
        vols += [0.0] * (len(close) - len(vols))
        return {
            "symbol": symbol, "name": pretty_symbol(symbol), "currency": "USD",
            "exchange": "CoinGecko", "market_state": "REGULAR",
            "price": close[-1], "prev_close": close[0],
            "ts": ts, "open": close, "high": close, "low": close, "close": close,
            "volume": vols, "line_only": True,
        }
    except Exception as e:
        _set_error(f"respuesta inesperada (CoinGecko): {e}")
        return None


def fetch_series(symbol: str, rng: str = "1D") -> dict | None:
    """Serie OHLC del rango pedido. None si no hay datos."""
    rng = rng if rng in RANGES else "1D"
    key = ("series", symbol.upper(), rng)
    ttl = 25 if rng == "1D" else 180
    hit = _cached(key, ttl)
    if hit is not None:
        return hit
    yr, yi, days = RANGES[rng]
    j = _get_json(_YF_CHART.format(sym=_urlq(symbol, safe="")),
                  {"range": yr, "interval": yi, "includePrePost": "false"})
    data = _parse_yahoo(j) if j else None
    if data is None and is_crypto_symbol(symbol):
        cid = _CG_BY_SYMBOL.get(symbol.upper())
        if cid:
            j = _get_json(_CG_CHART.format(cid=cid), {"vs_currency": "usd", "days": days})
            data = _parse_coingecko(j, symbol.upper()) if j else None
    if data is None:
        return None
    data["symbol"] = data.get("symbol") or symbol.upper()
    data["range"] = rng
    return _store(key, data)


def fetch_quote(symbol: str) -> dict | None:
    """Current quote + day change + mini chart (sparkline)."""
    key = ("quote", symbol.upper())
    hit = _cached(key, 20)
    if hit is not None:
        return hit
    d = fetch_series(symbol, "1D")
    if d is None:
        return None
    price = d["price"]
    prev = d.get("prev_close")
    if not prev:
        prev = d["close"][0]
    change = price - prev
    pct = (change / prev * 100.0) if prev else 0.0
    closes = d["close"]
    q = {
        "symbol": d["symbol"], "name": d["name"], "currency": d["currency"],
        "exchange": d.get("exchange", ""), "market_state": d.get("market_state", ""),
        "price": price, "prev_close": prev, "change": change, "change_pct": pct,
        "day_high": max(d["high"]) if d["high"] else price,
        "day_low": min(d["low"]) if d["low"] else price,
        "volume": sum(d["volume"]) if d["volume"] else 0,
        "spark": _downsample(closes, 48),
        "updated": time.time(),
    }
    return _store(key, q)


# ── Noticias ──────────────────────────────────────────────────────────────────

def fetch_news(symbol: str, name: str = "", limit: int = 8) -> list[dict]:
    """Recent headlines for the asset, newest first.
    Cada uno: {title, publisher, link, ts (epoch o 0 si se desconoce)}."""
    key = ("news", symbol.upper(), limit)
    hit = _cached(key, 240)
    if hit is not None:
        return hit
    items: list[dict] = []
    j = _get_json(_YF_SEARCH, {"q": symbol, "quotesCount": 0, "newsCount": limit,
                               "lang": "en-US", "region": "US"})
    got_response = j is not None
    for n in (j or {}).get("news", []) or []:
        title = (n.get("title") or "").strip()
        if not title:
            continue
        items.append({"title": title, "publisher": n.get("publisher", ""),
                      "link": n.get("link", ""), "ts": int(n.get("providerPublishTime") or 0)})
    if not items:
        items = _news_fallback(name or pretty_symbol(symbol), limit)
        got_response = got_response or bool(items)
    items.sort(key=lambda x: x["ts"], reverse=True)
    if got_response:
        _store(key, items)
    return items


def _news_fallback(query: str, limit: int) -> list[dict]:
    """Fallback using ddgs (keeps the date, which actions.web_search discards)."""
    try:
        from actions.web_search import _get_ddgs
        DDGS = _get_ddgs()
        out = []
        with DDGS() as d:
            for r in d.news(f"{query} news", max_results=limit):
                title = (r.get("title") or "").strip()
                if not title:
                    continue
                out.append({"title": title, "publisher": r.get("source", ""),
                            "link": r.get("url", ""), "ts": _parse_date(r.get("date"))})
        return out
    except Exception as e:
        _set_error(f"noticias (respaldo): {str(e)[:100]}")
        return []


def _parse_date(s) -> int:
    if not s:
        return 0
    try:
        from datetime import datetime, timezone
        return int(datetime.fromisoformat(str(s).replace("Z", "+00:00"))
                   .astimezone(timezone.utc).timestamp())
    except Exception:
        return 0
