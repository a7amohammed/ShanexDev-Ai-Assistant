"""
MARKET RADAR — HUD module (stocks + crypto).

A child panel of the HudCanvas, like the 3D globe, but free-floating: it is dragged by the
header, resized from the bottom-right corner and folds into a
strip with your pinned assets. Everything is drawn with QPainter (zero new dependencies;
charts are vector-based) and the icons are strokes, not emojis.

  · Favourites list (pinned on top) with price, change and mini chart.
  · Large chart: line / area / candles, ranges 1D·5D·1M·6M·1Y·5Y, cursor with
    data, optional volume.
  · Latest headlines of the selected asset (click = open); those JARVIS
    considers important carry a marker.
  · ＋ search stocks and crypto · ⚙ customise EVERYTHING (look, voice alerts,
    favourites: visible / pinned / alerts).

Data comes from core.market_data and is requested in threads; the state lives in
core.market_radar.RadarStore, so that the voice plugin and the interface
share a single source of truth.

Colours are read on every repaint from the `theme` object (class C of ui.py),
so the radar follows the HUD colour instantly without importing ui.py.
"""
from __future__ import annotations

import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF, QTime, pyqtSignal
from PyQt6.QtGui import (QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen,
                         QBrush, QLinearGradient, QCursor)
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QCheckBox, QComboBox,
                             QSpinBox, QDoubleSpinBox, QSlider, QPushButton, QLineEdit,
                             QListWidget, QListWidgetItem, QScrollArea, QTimeEdit, QColorDialog)

from core import market_data as md
from core import market_radar as mr

HDR_H = 26
GRIP = 14
MIN_W, MIN_H = 300, 180


def _qc(hex_: str, a: int = 255) -> QColor:
    c = QColor(hex_)
    c.setAlpha(max(0, min(255, int(a))))
    return c


def _ago(ts: int) -> str:
    if not ts:
        return ""
    s = max(0, time.time() - ts)
    if s < 3600:
        return f"{int(s // 60)} min"
    if s < 86400:
        return f"{int(s // 3600)} h"
    return f"{int(s // 86400)} d"


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:+.2f}%"


# ── Style of the panels with real widgets (settings / search) ──────────────────

def _page_qss(t) -> str:
    return f"""
    QWidget {{ background: transparent; color: {t.TEXT}; font-family: 'Courier New'; font-size: 11px; }}
    QLabel#sec {{ color: {t.PRI}; font-weight: bold; border-bottom: 1px solid {t.BORDER_B}; padding-top: 6px; }}
    QCheckBox {{ spacing: 6px; }}
    QCheckBox::indicator {{ width: 11px; height: 11px; border: 1px solid {t.PRI_DIM}; background: {t.PRI_GHO}; }}
    QCheckBox::indicator:checked {{ background: {t.PRI}; }}
    QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit, QTimeEdit {{
        background: {t.PRI_GHO}; border: 1px solid {t.BORDER_B}; padding: 2px 4px; color: {t.WHITE}; }}
    QComboBox QAbstractItemView {{ background: {t.DARK}; color: {t.TEXT}; selection-background-color: {t.BORDER_A}; }}
    QPushButton {{ background: transparent; border: 1px solid {t.BORDER_B}; color: {t.PRI}; padding: 3px 8px; }}
    QPushButton:hover {{ background: {t.PRI_GHO}; border-color: {t.PRI}; }}
    QListWidget {{ background: {t.PRI_GHO}; border: 1px solid {t.BORDER}; color: {t.TEXT}; }}
    QListWidget::item {{ padding: 3px; }}
    QListWidget::item:selected {{ background: {t.BORDER_A}; color: {t.WHITE}; }}
    QScrollArea {{ border: none; background: transparent; }}
    QSlider::groove:horizontal {{ height: 3px; background: {t.BORDER_B}; }}
    QSlider::handle:horizontal {{ width: 10px; margin: -5px 0; background: {t.PRI}; }}
    """


class _SettingsPage(QWidget):
    """Full customisation. Each control writes to the store instantly."""
    back = pyqtSignal()
    reset_geometry = pyqtSignal()

    def __init__(self, store, theme, parent=None):
        super().__init__(parent)
        self._store, self._t = store, theme
        self._syncing = False
        self.setStyleSheet(_page_qss(theme))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 4, 8, 6)
        outer.setSpacing(4)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        self._lay = QVBoxLayout(inner)
        self._lay.setContentsMargins(0, 0, 6, 0)
        self._lay.setSpacing(4)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        # — Aspecto —
        self._section("ASPECTO")
        self.c_type = self._combo([("Area", "area"), ("Line", "line"), ("Candles", "candles")],
                                  lambda v: self._ui(chart_type=v), "Chart type")
        self.c_range = self._combo([(r, r) for r in md.RANGE_ORDER],
                                   lambda v: self._ui(range=v), "Rango por defecto")
        self.s_refresh = self._spin(15, 900, " s", lambda v: self._ui(refresh_secs=v), "Actualizar cada")
        self.sl_op = QSlider(Qt.Orientation.Horizontal)
        self.sl_op.setRange(30, 100)
        self.sl_op.valueChanged.connect(lambda v: self._ui(opacity=v / 100.0))
        self._row("Opacidad del fondo", self.sl_op)
        self.k_list = self._check("Mostrar lista de activos", lambda v: self._ui(show_list=v))
        self.k_news = self._check("Mostrar noticias", lambda v: self._ui(show_news=v))
        self.k_spark = self._check("Mini charts in the list", lambda v: self._ui(sparklines=v))
        self.k_vol = self._check("Mostrar volumen", lambda v: self._ui(show_volume=v))
        self.k_lock = self._check("Lock position and size", lambda v: self._ui(locked=v))
        row = QHBoxLayout()
        b_col = QPushButton("Color…")
        b_col.clicked.connect(self._pick_color)
        b_hud = QPushButton("Color del HUD")
        b_hud.clicked.connect(lambda: self._ui(accent=""))
        b_pos = QPushButton("Reset position")
        b_pos.clicked.connect(self.reset_geometry.emit)
        for b in (b_col, b_hud, b_pos):
            row.addWidget(b)
        self._lay.addLayout(row)

        # — Avisos —
        self._section("AVISOS DE JARVIS (te habla solo)")
        self.k_voice = self._check("Avisarme por voz de noticias y movimientos",
                                   lambda v: self._al(voice=v))
        self.c_sens = self._combo([("Low — only the very important", "low"), ("Medium", "medium"),
                                   ("High — more alerts", "high")],
                                  lambda v: self._al(sensitivity=v), "Sensibilidad")
        self.d_crypto = self._dspin(1, 50, lambda v: self._al(move_pct_crypto=v), "Crypto move (day)")
        self.d_stock = self._dspin(1, 30, lambda v: self._al(move_pct_stock=v), "Stock move (day)")
        self.s_cool = self._spin(1, 240, " min", lambda v: self._al(cooldown_min=v), "Minimum between alerts")
        self.s_max = self._spin(1, 30, "", lambda v: self._al(max_per_day=v), "Maximum per day")
        self.s_poll = self._spin(3, 120, " min", lambda v: self._al(poll_min=v), "Revisar cada")
        self.k_quiet = self._check("Horas de silencio (sin avisos)", lambda v: self._al(quiet_enabled=v))
        qrow = QHBoxLayout()
        self.t_from, self.t_to = QTimeEdit(), QTimeEdit()
        for te, key in ((self.t_from, "quiet_start"), (self.t_to, "quiet_end")):
            te.setDisplayFormat("HH:mm")
            te.timeChanged.connect(lambda q, k=key: self._al(**{k: q.toString("HH:mm")}))
            qrow.addWidget(te)
        self._lay.addLayout(qrow)

        # — Favoritos —
        self._section("FAVORITOS   (ver · anclar · avisos)")
        self._fav_box = QVBoxLayout()
        self._fav_box.setSpacing(2)
        self._lay.addLayout(self._fav_box)
        self._lay.addStretch()

        b_back = QPushButton("◂  VOLVER AL RADAR")
        b_back.clicked.connect(self.back.emit)
        outer.addWidget(b_back)

    # ── constructores de filas ────────────────────────────────────────────────
    def _section(self, title):
        lb = QLabel(title)
        lb.setObjectName("sec")
        self._lay.addWidget(lb)

    def _row(self, label, widget):
        r = QHBoxLayout()
        lb = QLabel(label)
        r.addWidget(lb, 1)
        r.addWidget(widget, 1)
        self._lay.addLayout(r)

    def _check(self, label, cb):
        c = QCheckBox(label)
        c.toggled.connect(lambda v: (not self._syncing) and cb(bool(v)))
        self._lay.addWidget(c)
        return c

    def _combo(self, items, cb, label):
        c = QComboBox()
        for txt, val in items:
            c.addItem(txt, val)
        c.currentIndexChanged.connect(lambda _i, c=c: (not self._syncing) and cb(c.currentData()))
        self._row(label, c)
        return c

    def _spin(self, lo, hi, suffix, cb, label):
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setSuffix(suffix)
        s.valueChanged.connect(lambda v: (not self._syncing) and cb(int(v)))
        self._row(label, s)
        return s

    def _dspin(self, lo, hi, cb, label):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(1)
        s.setSingleStep(0.5)
        s.setSuffix(" %")
        s.valueChanged.connect(lambda v: (not self._syncing) and cb(float(v)))
        self._row(label, s)
        return s

    def _ui(self, **kw):
        if not self._syncing:
            self._store.set_ui(**kw)

    def _al(self, **kw):
        if not self._syncing:
            self._store.set_alerts(**kw)

    def _pick_color(self):
        cur = self._store.ui().get("accent") or self._t.PRI
        col = QColorDialog.getColor(QColor(cur), self, "Color del radar")
        if col.isValid():
            self._store.set_ui(accent=col.name())

    # ── synchronise with the store ────────────────────────────────────────────
    def sync(self):
        ui, al = self._store.ui(), self._store.alerts()
        self._syncing = True
        try:
            self.c_type.setCurrentIndex(max(0, self.c_type.findData(ui["chart_type"])))
            self.c_range.setCurrentIndex(max(0, self.c_range.findData(ui["range"])))
            self.s_refresh.setValue(int(ui["refresh_secs"]))
            self.sl_op.setValue(int(float(ui["opacity"]) * 100))
            self.k_list.setChecked(bool(ui["show_list"]))
            self.k_news.setChecked(bool(ui["show_news"]))
            self.k_spark.setChecked(bool(ui["sparklines"]))
            self.k_vol.setChecked(bool(ui["show_volume"]))
            self.k_lock.setChecked(bool(ui["locked"]))
            self.k_voice.setChecked(bool(al["voice"]))
            self.c_sens.setCurrentIndex(max(0, self.c_sens.findData(al["sensitivity"])))
            self.d_crypto.setValue(float(al["move_pct_crypto"]))
            self.d_stock.setValue(float(al["move_pct_stock"]))
            self.s_cool.setValue(int(al["cooldown_min"]))
            self.s_max.setValue(int(al["max_per_day"]))
            self.s_poll.setValue(int(al["poll_min"]))
            self.k_quiet.setChecked(bool(al["quiet_enabled"]))
            self.t_from.setTime(QTime.fromString(str(al["quiet_start"]), "HH:mm"))
            self.t_to.setTime(QTime.fromString(str(al["quiet_end"]), "HH:mm"))
        finally:
            self._syncing = False
        self._rebuild_favs()

    def _rebuild_favs(self):
        while self._fav_box.count():
            it = self._fav_box.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
            elif it.layout() is not None:
                lay = it.layout()
                while lay.count():
                    sub = lay.takeAt(0).widget()
                    if sub is not None:
                        sub.setParent(None)
                        sub.deleteLater()
        assets = self._store.assets()
        if not assets:
            lb = QLabel("No favourites. Use ＋ to add stocks or crypto.")
            self._fav_box.addWidget(lb)
        for a in assets:
            row = QHBoxLayout()
            lb = QLabel(f"{md.pretty_symbol(a['symbol'])}  {a.get('name', '')[:16]}")
            row.addWidget(lb, 1)
            for key, tip in (("visible", "Ver en el HUD"), ("pinned", "Anclar"), ("alerts", "Avisos")):
                c = QCheckBox()
                c.setToolTip(tip)
                c.setChecked(bool(a.get(key)))
                c.toggled.connect(lambda v, s=a["symbol"], k=key: self._store.set_flag(s, k, v))
                row.addWidget(c)
            x = QPushButton("✕")
            x.setFixedWidth(24)
            x.setToolTip("Quitar de favoritos")
            x.clicked.connect(lambda _=False, s=a["symbol"]: (self._store.remove(s), self._rebuild_favs()))
            row.addWidget(x)
            holder = QWidget()
            holder.setLayout(row)
            self._fav_box.addWidget(holder)


class _AddPage(QWidget):
    """Buscador de acciones y criptos."""
    back = pyqtSignal()
    search_requested = pyqtSignal(str)
    chosen = pyqtSignal(dict, bool)      # activo, guardar_en_favoritos

    def __init__(self, theme, parent=None):
        super().__init__(parent)
        self.setStyleSheet(_page_qss(theme))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(5)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Search stock or crypto: Tesla, NVDA, BTC, Solana…")
        lay.addWidget(self.edit)
        self.lst = QListWidget()
        lay.addWidget(self.lst, 1)
        self.status = QLabel("Escribe y pulsa Enter.")
        lay.addWidget(self.status)
        row = QHBoxLayout()
        b_view = QPushButton("VIEW CHART")
        b_save = QPushButton("★  GUARDAR EN FAVORITOS")
        b_back = QPushButton("◂")
        b_back.setFixedWidth(30)
        row.addWidget(b_back)
        row.addWidget(b_view, 1)
        row.addWidget(b_save, 2)
        lay.addLayout(row)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(lambda: self._go())
        self.edit.textChanged.connect(lambda _t: self._timer.start(550))
        self.edit.returnPressed.connect(self._go)
        b_back.clicked.connect(self.back.emit)
        b_view.clicked.connect(lambda: self._pick(False))
        b_save.clicked.connect(lambda: self._pick(True))
        self.lst.itemDoubleClicked.connect(lambda _i: self._pick(True))

    def _go(self):
        q = self.edit.text().strip()
        if len(q) >= 2:
            self.status.setText("Buscando…")
            self.search_requested.emit(q)

    def show_results(self, results: list[dict]):
        self.lst.clear()
        for r in results:
            tag = "CRIPTO" if r["kind"] == "crypto" else (r.get("exchange") or "BOLSA")
            it = QListWidgetItem(f"{md.pretty_symbol(r['symbol']):<8} {r['name'][:28]}  [{tag}]")
            it.setData(Qt.ItemDataRole.UserRole, r)
            self.lst.addItem(it)
        if results:
            self.lst.setCurrentRow(0)
        self.status.setText(f"{len(results)} resultado(s)" if results else "Sin resultados.")

    def _pick(self, save: bool):
        it = self.lst.currentItem()
        if it is None:
            self.status.setText("Elige un resultado de la lista.")
            return
        self.chosen.emit(it.data(Qt.ItemDataRole.UserRole), save)

    def focus_search(self):
        self.edit.setFocus()
        self.edit.selectAll()


# ── Widget principal ──────────────────────────────────────────────────────────

class MarketRadarWidget(QWidget):
    enabledChanged = pyqtSignal(bool)
    _data_ready = pyqtSignal(str, str, object)        # type, key, payload (from threads)

    def __init__(self, parent=None, theme=None):
        super().__init__(parent)
        self._t = theme
        self._store = mr.get_store()
        self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="radar")
        self._quotes: dict[str, dict] = {}
        self._series: dict[tuple, dict] = {}
        self._failed: dict[tuple, float] = {}
        self._news: dict[str, list] = {}
        self._news_at: dict[str, float] = {}
        self._inflight: set = set()
        self._zones: list[tuple[QRectF, str, str]] = []
        self._hover: tuple[str, str] | None = None
        self._mouse = QPointF(-1, -1)
        self._page = "main"
        self._panel_rect = parent.rect() if parent is not None else self.rect()
        self._preview: dict | None = None
        self._list_off = 0
        self._alert: tuple[dict, float] | None = None
        self._last_ok = 0.0
        self._last_fail = 0.0
        self._last_refresh = 0.0
        self._seen_version = -1
        self._drag: tuple | None = None
        self._enabled = True

        self.setMouseTracking(True)
        self.setMinimumSize(170, 34)
        self._settings = _SettingsPage(self._store, theme, self)
        self._settings.hide()
        self._settings.back.connect(lambda: self._set_page("main"))
        self._settings.reset_geometry.connect(self.reset_geometry)
        self._add = _AddPage(theme, self)
        self._add.hide()
        self._add.back.connect(lambda: self._set_page("main"))
        self._add.search_requested.connect(self._search)
        self._add.chosen.connect(self._on_chosen)
        self._data_ready.connect(self._on_data)

        self._tick = QTimer(self)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start(1000)
        self._anim = QTimer(self)                       # solo corre mientras hay un aviso visible
        self._anim.timeout.connect(self.update)
        self.reposition(self._panel_rect)
        QTimer.singleShot(400, self.refresh_now)

    # ── colores ───────────────────────────────────────────────────────────────
    def _col(self, name: str) -> str:
        return getattr(self._t, name)

    def _accent(self) -> str:
        a = self._store.ui().get("accent") or ""
        return a if (a.startswith("#") and len(a) == 7) else self._col("PRI")

    # ── geometry ──────────────────────────────────────────────────────────────
    def reposition(self, panel_rect=None):
        if panel_rect is not None:
            self._panel_rect = panel_rect
        ui = self._store.ui()
        pr = self._panel_rect
        if ui["collapsed"]:
            w, h = self._strip_width(), 34
            if self._alert:
                h = 34
        else:
            w = int(max(MIN_W, min(ui["w"], pr.width() - 8)))
            h = int(max(MIN_H, min(ui["h"], pr.height() - 8)))
        w = max(MIN_W if not ui["collapsed"] else 170, min(w, max(170, pr.width() - 8)))
        x = int(pr.left() + 4 + max(0.0, ui["fx"]) * max(0, pr.width() - w - 8))
        y = int(pr.top() + 4 + max(0.0, ui["fy"]) * max(0, pr.height() - h - 8))
        self.setGeometry(x, y, w, h)
        self._layout_pages()

    def _layout_pages(self):
        body = QRectF(2, HDR_H, self.width() - 4, max(0, self.height() - HDR_H - 4)).toRect()
        self._settings.setGeometry(body)
        self._add.setGeometry(body)

    def reset_geometry(self):
        self._store.set_ui(fx=0.02, fy=0.98, w=mr.DEFAULT_UI["w"], h=mr.DEFAULT_UI["h"], collapsed=False)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layout_pages()

    def _strip_chips(self) -> list[dict]:
        assets = [a for a in self._store.assets() if a.get("visible")]
        pinned = [a for a in assets if a.get("pinned")]
        return (pinned or assets)[:6]

    def _strip_width(self) -> int:
        fm = QFontMetrics(QFont("Courier New", 8, QFont.Weight.Bold))
        w = 74
        for a in self._strip_chips():
            q = self._quotes.get(a["symbol"])
            txt = f"{md.pretty_symbol(a['symbol'])} {md.format_price(q['price'], q['currency']) if q else '…'} {_pct(q['change_pct']) if q else ''}"
            w += fm.horizontalAdvance(txt) + 24
        return int(min(max(w, 190), max(190, self._panel_rect.width() - 8)))

    # ── module state / pages ──────────────────────────────────────────────────
    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def apply_enabled(self, on: bool):
        self._enabled = bool(on)
        self.setVisible(self._enabled)
        if self._enabled:
            self.raise_()
            self.refresh_now()

    def _set_page(self, page: str):
        self._page = page
        self._settings.setVisible(page == "settings")
        self._add.setVisible(page == "add")
        if page == "settings":
            if self._store.ui()["collapsed"]:
                self._store.set_ui(collapsed=False)
            self._settings.sync()
        elif page == "add":
            if self._store.ui()["collapsed"]:
                self._store.set_ui(collapsed=False)
            self._add.focus_search()
        self.update()

    def open_settings(self):
        self.apply_enabled(True)
        self._set_page("settings")

    def open_add(self):
        self.apply_enabled(True)
        self._set_page("add")

    # ── datos ─────────────────────────────────────────────────────────────────
    def refresh_now(self):
        if not self._enabled:
            return
        self._last_refresh = time.time()
        syms = [a["symbol"] for a in self._store.assets() if a.get("visible")]
        sel = self._selected()
        if sel and sel not in syms:
            syms.append(sel)
        if syms and ("quotes",) not in self._inflight:
            self._inflight.add(("quotes",))
            self._pool.submit(self._job_quotes, syms)
        if sel:
            self._ensure_series(sel, force=True)
            self._ensure_news(sel)

    def _ensure_series(self, sym: str, force: bool = False):
        rng = self._store.ui()["range"]
        key = (sym, rng)
        if key in self._inflight:
            return
        if not force and key in self._series:
            return
        if time.time() - self._failed.get(key, 0) < 20:
            return
        self._inflight.add(key)
        self._pool.submit(self._job_series, sym, rng)

    def _ensure_news(self, sym: str):
        if time.time() - self._news_at.get(sym, 0) < 300 or ("news", sym) in self._inflight:
            return
        a = self._store.get(sym) or (self._preview if self._preview and self._preview["symbol"] == sym else None)
        self._inflight.add(("news", sym))
        self._pool.submit(self._job_news, sym, (a or {}).get("name", ""))

    def _job_quotes(self, syms):
        try:
            for s in syms:
                self._data_ready.emit("quote", s, md.fetch_quote(s))
        finally:
            self._data_ready.emit("done", "quotes", None)

    def _job_series(self, sym, rng):
        self._data_ready.emit("series", f"{sym}|{rng}", md.fetch_series(sym, rng))

    def _job_news(self, sym, name):
        self._data_ready.emit("news", sym, md.fetch_news(sym, name, 6))

    def _search(self, q: str):
        self._pool.submit(lambda: self._data_ready.emit("search", q, md.search(q, 8)))

    def _on_data(self, kind: str, key: str, payload):
        if kind == "quote":
            if payload:
                self._quotes[key] = payload
                self._last_ok = time.time()
            else:
                self._last_fail = time.time()
        elif kind == "series":
            sym, rng = key.rsplit("|", 1)
            self._inflight.discard((sym, rng))
            if payload:
                self._series[(sym, rng)] = payload
                self._failed.pop((sym, rng), None)
                self._last_ok = time.time()
            else:
                self._failed[(sym, rng)] = time.time()
                self._last_fail = time.time()
        elif kind == "news":
            self._inflight.discard(("news", key))
            self._news[key] = payload or []
            self._news_at[key] = time.time()
        elif kind == "done":
            self._inflight.discard(("quotes",))
            if self._store.ui()["collapsed"]:
                self.reposition()
        elif kind == "search":
            self._add.show_results(payload or [])
        self.update()

    def _selected(self) -> str:
        return (self._store.ui().get("selected") or "").upper()

    def _on_tick(self):
        if not self._enabled:
            return
        v = self._store.version
        if v != self._seen_version:
            self._seen_version = v
            self.reposition()
            if self._page == "settings":
                self._settings.sync()
            sel = self._selected()
            if sel:
                self._ensure_series(sel)
                self._ensure_news(sel)
            self.update()
        every = max(15, int(self._store.ui().get("refresh_secs", 60)))
        if time.time() - self._last_refresh >= every:
            self.refresh_now()
        if self._alert and time.time() > self._alert[1]:
            self._alert = None
            self._anim.stop()
            self.update()

    # ── avisos ────────────────────────────────────────────────────────────────
    def notify_alert(self, d: dict):
        """JARVIS has just announced this by voice: highlight it on the HUD."""
        self._alert = (dict(d), time.time() + 14)
        self._anim.start(60)
        sym = (d.get("symbol") or "").upper()
        if sym and self._store.get(sym):
            self._store.set_ui(selected=sym)
        self.update()

    # ── interaction ───────────────────────────────────────────────────────────
    def show_preview(self, asset: dict):
        """Chart of a non-favourite asset (shows the star to save it)."""
        self._preview = {"symbol": str(asset.get("symbol", "")).upper(),
                         "name": asset.get("name", ""), "kind": asset.get("kind", "stock")}

    def select_symbol(self, sym: str, rng: str | None = None):
        kw = {"selected": sym.upper()}
        if rng in md.RANGES:
            kw["range"] = rng
        self._store.set_ui(**kw)

    def _do(self, action: str, arg: str):
        s = self._store
        if action == "close":
            self.apply_enabled(False)
            self.enabledChanged.emit(False)
        elif action == "collapse":
            s.set_ui(collapsed=not s.ui()["collapsed"])
        elif action == "settings":
            self._set_page("main" if self._page == "settings" else "settings")
        elif action == "add":
            self._set_page("main" if self._page == "add" else "add")
        elif action in ("sel", "chip"):
            if action == "chip" and s.ui()["collapsed"]:
                s.set_ui(collapsed=False)
            s.set_ui(selected=arg.upper())
        elif action == "pin":
            s.toggle_flag(arg, "pinned")
        elif action == "vis":
            s.toggle_flag(arg, "visible")
        elif action == "bell":
            s.toggle_flag(arg, "alerts")
        elif action == "del":
            s.remove(arg)
        elif action == "range":
            s.set_ui(range=arg)
        elif action == "ctype":
            order = ["area", "line", "candles"]
            cur = s.ui()["chart_type"]
            s.set_ui(chart_type=order[(order.index(cur) + 1) % 3] if cur in order else "area")
        elif action == "save_pre" and self._preview:
            p = self._preview
            s.add(p["symbol"], p.get("name", ""), p.get("kind", "stock"), alerts=True)
            self._preview = None
        elif action == "news":
            try:
                item = self._news.get(self._selected(), [])[int(arg)]
                if item.get("link"):
                    webbrowser.open(item["link"])
            except Exception:
                pass
        self.update()

    def _on_chosen(self, asset: dict, save: bool):
        if save:
            self._store.add(asset["symbol"], asset["name"], asset.get("kind", "stock"), alerts=True)
            self._preview = None
        else:
            self._preview = {"symbol": asset["symbol"].upper(), "name": asset["name"],
                             "kind": asset.get("kind", "stock")}
        self._store.set_ui(selected=asset["symbol"].upper())
        self._set_page("main")
        self.refresh_now()

    def _zone_at(self, pt: QPointF):
        for r, act, arg in reversed(self._zones):
            if r.contains(pt):
                return r, act, arg
        return None

    def mouseMoveEvent(self, e):
        self._mouse = e.position()
        ui = self._store.ui()
        if self._drag:
            kind, p0, g0 = self._drag
            g = e.globalPosition()
            dx, dy = g.x() - p0.x(), g.y() - p0.y()
            pr = self._panel_rect
            if kind == "move":
                x = max(pr.left() + 2, min(g0.x() + dx, pr.right() - self.width() - 2))
                y = max(pr.top() + 2, min(g0.y() + dy, pr.bottom() - self.height() - 2))
                self.move(int(x), int(y))
            else:
                w = int(max(MIN_W, min(g0.width() + dx, pr.width() - (self.x() - pr.left()) - 2)))
                h = int(max(MIN_H, min(g0.height() + dy, pr.height() - (self.y() - pr.top()) - 2)))
                self.resize(w, h)
            return
        z = self._zone_at(self._mouse)
        self._hover = (z[1], z[2]) if z else None
        grip = QRectF(self.width() - GRIP, self.height() - GRIP, GRIP, GRIP)
        if z:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif grip.contains(self._mouse) and not ui["collapsed"] and not ui["locked"]:
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif self._mouse.y() < HDR_H and not ui["locked"]:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        else:
            self.unsetCursor()
        self.update()

    def leaveEvent(self, e):
        self._mouse = QPointF(-1, -1)
        self._hover = None
        self.update()

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        pt = e.position()
        z = self._zone_at(pt)
        if z:
            self._do(z[1], z[2])
            return
        ui = self._store.ui()
        if ui["locked"]:
            return
        grip = QRectF(self.width() - GRIP, self.height() - GRIP, GRIP, GRIP)
        if grip.contains(pt) and not ui["collapsed"]:
            self._drag = ("size", e.globalPosition(), self.geometry())
        elif pt.y() < HDR_H or ui["collapsed"]:
            self._drag = ("move", e.globalPosition(), self.geometry())

    def mouseReleaseEvent(self, e):
        if not self._drag:
            return
        kind = self._drag[0]
        self._drag = None
        pr = self._panel_rect
        fw = max(1, pr.width() - self.width() - 8)
        fh = max(1, pr.height() - self.height() - 8)
        kw = {"fx": max(0.0, min(1.0, (self.x() - pr.left() - 4) / fw)),
              "fy": max(0.0, min(1.0, (self.y() - pr.top() - 4) / fh))}
        if kind == "size":
            kw.update(w=self.width(), h=self.height())
        self._store.set_ui(**kw)

    def mouseDoubleClickEvent(self, e):
        if e.position().y() < HDR_H and not self._zone_at(e.position()):
            self._do("collapse", "")

    def wheelEvent(self, e):
        if self._page == "main" and self._mouse.x() < 190:
            self._list_off = max(0, self._list_off - (1 if e.angleDelta().y() > 0 else -1))
            self.update()

    # ── dibujo ────────────────────────────────────────────────────────────────
    def paintEvent(self, _e):
        W, H = self.width(), self.height()
        if W < 10 or H < 10:
            return
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._zones = []
        ui = self._store.ui()
        self._frame(p, W, H, ui)
        self._header(p, W, ui)
        if self._page == "main":
            if ui["collapsed"]:
                self._strip(p, W, H)
            else:
                self._body(p, W, H, ui)
            if self._alert and not ui["collapsed"]:
                self._banner(p, W)
            if not ui["collapsed"] and not ui["locked"]:
                self._grip(p, W, H)
        p.end()

    def _frame(self, p, W, H, ui):
        acc = self._accent()
        cut = 10.0
        path = QPainterPath()
        path.moveTo(cut, 0); path.lineTo(W, 0); path.lineTo(W, H - cut)
        path.lineTo(W - cut, H); path.lineTo(0, H); path.lineTo(0, cut)
        path.closeSubpath()
        a = int(float(ui["opacity"]) * 235)
        g = QLinearGradient(0, 0, 0, H)
        g.setColorAt(0, _qc(self._col("PANEL"), a))
        g.setColorAt(1, _qc(self._col("BG"), a))
        p.fillPath(path, QBrush(g))
        p.setPen(QPen(_qc(acc, 40), 4))
        p.drawPath(path)
        p.setPen(QPen(_qc(acc, 190), 1.1))
        p.drawPath(path)

    def _btn(self, p, rect: QRectF, action: str, icon: str, tip_on=False):
        hov = self._hover == (action, "")
        self._zones.append((rect, action, ""))
        col = _qc(self._accent(), 255 if hov or tip_on else 150)
        if hov:
            p.fillRect(rect, _qc(self._accent(), 40))
        p.setPen(QPen(col, 1.3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        c, r = rect.center(), min(rect.width(), rect.height()) / 2 - 4
        if icon == "x":
            p.drawLine(QPointF(c.x() - r, c.y() - r), QPointF(c.x() + r, c.y() + r))
            p.drawLine(QPointF(c.x() - r, c.y() + r), QPointF(c.x() + r, c.y() - r))
        elif icon == "plus":
            p.drawLine(QPointF(c.x() - r, c.y()), QPointF(c.x() + r, c.y()))
            p.drawLine(QPointF(c.x(), c.y() - r), QPointF(c.x(), c.y() + r))
        elif icon == "min":
            p.drawLine(QPointF(c.x() - r, c.y() + 2), QPointF(c.x() + r, c.y() + 2))
        elif icon == "max":
            p.drawRect(QRectF(c.x() - r, c.y() - r + 1, 2 * r, 2 * r - 2))
        elif icon == "gear":
            p.drawEllipse(c, r * 0.55, r * 0.55)
            import math
            for k in range(8):
                a = k * math.pi / 4
                p.drawLine(QPointF(c.x() + math.cos(a) * r * 0.75, c.y() + math.sin(a) * r * 0.75),
                           QPointF(c.x() + math.cos(a) * r * 1.1, c.y() + math.sin(a) * r * 1.1))
        elif icon == "back":
            p.drawLine(QPointF(c.x() + r * .5, c.y() - r), QPointF(c.x() - r * .5, c.y()))
            p.drawLine(QPointF(c.x() - r * .5, c.y()), QPointF(c.x() + r * .5, c.y() + r))

    def _header(self, p, W, ui):
        acc = self._accent()
        # status LED: green = data up to date, amber = recent failures, red = no data
        now = time.time()
        if self._last_ok and now - self._last_ok < 3 * max(15, ui["refresh_secs"]) and self._last_fail < self._last_ok + 1:
            led = self._col("GREEN")
        elif self._last_ok:
            led = self._col("ACC2")
        else:
            led = self._col("RED") if self._last_fail else self._col("TEXT_DIM")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_qc(led, 230))
        p.drawEllipse(QPointF(16, HDR_H / 2), 3.2, 3.2)
        f = QFont("Courier New", 8, QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
        p.setFont(f)
        p.setPen(_qc(acc, 230))
        p.drawText(QRectF(26, 0, 150, HDR_H), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   "RADAR // MERCADOS")
        p.setPen(QPen(_qc(acc, 70), 1))
        p.drawLine(QPointF(8, HDR_H), QPointF(W - 8, HDR_H))
        x = W - 6 - 22
        self._btn(p, QRectF(x, 2, 22, 22), "close", "x")
        x -= 22
        self._btn(p, QRectF(x, 2, 22, 22), "collapse", "max" if ui["collapsed"] else "min")
        x -= 22
        self._btn(p, QRectF(x, 2, 22, 22), "settings", "back" if self._page == "settings" else "gear",
                  self._page == "settings")
        x -= 22
        self._btn(p, QRectF(x, 2, 22, 22), "add", "back" if self._page == "add" else "plus",
                  self._page == "add")

    def _grip(self, p, W, H):
        p.setPen(QPen(_qc(self._accent(), 140), 1))
        for k in (4, 8, 12):
            p.drawLine(QPointF(W - k, H - 3), QPointF(W - 3, H - k))

    # — tira plegada —
    def _strip(self, p, W, H):
        if self._alert:
            d = self._alert[0]
            p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            p.setPen(_qc(self._col("ACC2"), 120 + int(80 * abs(((time.time() * 2) % 2) - 1))))
            txt = f"! {md.pretty_symbol(d.get('symbol', ''))} {_pct(d.get('change_pct'))} {d.get('headline', '')}"
            fm = QFontMetrics(p.font())
            p.drawText(QRectF(10, HDR_H, W - 20, H - HDR_H), Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(txt, Qt.TextElideMode.ElideRight, W - 24))
            return
        f = QFont("Courier New", 8, QFont.Weight.Bold)
        p.setFont(f)
        fm = QFontMetrics(f)
        x = 10.0
        for a in self._strip_chips():
            q = self._quotes.get(a["symbol"])
            sym = md.pretty_symbol(a["symbol"])
            price = md.format_price(q["price"], q["currency"]) if q else "…"
            pct = _pct(q["change_pct"]) if q else ""
            w = fm.horizontalAdvance(f"{sym} {price} {pct}") + 14
            if x + w > W - 6:
                break
            rect = QRectF(x - 4, HDR_H + 1, w + 4, H - HDR_H - 4)
            self._zones.append((rect, "chip", a["symbol"]))
            if self._hover == ("chip", a["symbol"]):
                p.fillRect(rect, _qc(self._accent(), 35))
            up = (q["change_pct"] >= 0) if q else True
            p.setPen(_qc(self._col("TEXT"), 235))
            p.drawText(QPointF(x, H - 8), f"{sym} ")
            x2 = x + fm.horizontalAdvance(f"{sym} ")
            p.setPen(_qc(self._col("WHITE"), 235))
            p.drawText(QPointF(x2, H - 8), price)
            x2 += fm.horizontalAdvance(price + " ")
            p.setPen(_qc(self._col("GREEN") if up else self._col("RED"), 240))
            p.drawText(QPointF(x2, H - 8), pct)
            x += w + 10

    # — cuerpo expandido —
    def _body(self, p, W, H, ui):
        assets = [a for a in self._store.assets() if a.get("visible")]
        sel = self._selected()
        side = ui["show_list"] and W >= 430 and bool(assets)
        top = HDR_H + 4
        list_w = 0
        if side:
            list_w = 176 if W >= 560 else 150
            self._list(p, QRectF(6, top, list_w - 4, H - top - 6), assets, sel, ui, wide=W >= 560)
        elif assets:
            top = self._chips(p, W, assets, sel, top)
        cx = list_w + 6
        area = QRectF(cx, top, W - cx - 8, H - top - 8)
        show_news = ui["show_news"] and area.height() >= 215
        news_h = 44 if show_news else 0
        self._chart_block(p, QRectF(area.left(), area.top(), area.width(), area.height() - news_h), sel, ui)
        if show_news:
            self._news_block(p, QRectF(area.left(), area.bottom() - news_h + 4, area.width(), news_h - 4), sel)

    def _chips(self, p, W, assets, sel, top) -> float:
        f = QFont("Courier New", 8, QFont.Weight.Bold)
        p.setFont(f)
        fm = QFontMetrics(f)
        x = 8.0
        for a in assets:
            sym = md.pretty_symbol(a["symbol"])
            w = fm.horizontalAdvance(sym) + 16
            if x + w > W - 8:
                break
            r = QRectF(x, top, w, 18)
            self._zones.append((r, "sel", a["symbol"]))
            on = a["symbol"].upper() == sel
            if on or self._hover == ("sel", a["symbol"]):
                p.fillRect(r, _qc(self._accent(), 60 if on else 30))
            p.setPen(QPen(_qc(self._accent(), 200 if on else 90), 1))
            p.drawRect(r)
            p.setPen(_qc(self._col("WHITE") if on else self._col("TEXT"), 240))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, sym)
            x += w + 5
        return top + 24

    def _list(self, p, rect: QRectF, assets, sel, ui, wide: bool):
        row_h = 38.0
        fit = max(1, int(rect.height() // row_h))
        self._list_off = max(0, min(self._list_off, max(0, len(assets) - fit)))
        p.save()
        p.setClipRect(rect)
        y = rect.top()
        for a in assets[self._list_off:self._list_off + fit]:
            r = QRectF(rect.left(), y, rect.width(), row_h - 3)
            sym = a["symbol"]
            self._zones.append((r, "sel", sym))
            on = sym.upper() == sel
            hov = r.contains(self._mouse)
            if on:
                p.fillRect(r, _qc(self._accent(), 45))
                p.fillRect(QRectF(r.left(), r.top(), 2, r.height()), _qc(self._accent(), 230))
            elif hov:
                p.fillRect(r, _qc(self._accent(), 22))
            q = self._quotes.get(sym)
            up = (q["change_pct"] >= 0) if q else True
            ccol = self._col("GREEN") if up else self._col("RED")
            x0 = r.left() + 8
            if a.get("pinned"):                                  # chincheta: ancla dibujada
                p.setPen(QPen(_qc(self._col("ACC2"), 230), 1.2))
                p.setBrush(_qc(self._col("ACC2"), 200))
                p.drawEllipse(QPointF(x0 + 2, r.top() + 10), 2.4, 2.4)
                p.drawLine(QPointF(x0 + 2, r.top() + 12), QPointF(x0 + 2, r.top() + 17))
                x0 += 9
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            p.setPen(_qc(self._col("WHITE"), 245))
            p.drawText(QPointF(x0, r.top() + 14), md.pretty_symbol(sym))
            if a.get("alerts"):                                  # small bell = JARVIS alerts you
                self._bell(p, QPointF(r.right() - 8, r.top() + 9), 3.2, _qc(self._col("ACC2"), 200))
            p.setFont(QFont("Courier New", 8))
            price = md.format_price(q["price"], q["currency"]) if q else "…"
            fm = QFontMetrics(p.font())
            p.setPen(_qc(self._col("TEXT"), 235))
            p.drawText(QPointF(r.right() - 20 - fm.horizontalAdvance(price), r.top() + 14), price)
            if hov:                                              # actions on mouse hover
                bx = r.left() + 8
                for act, ic in (("pin", "pin"), ("vis", "eye"), ("bell", "bell"), ("del", "x")):
                    br = QRectF(bx, r.top() + 19, 18, 15)
                    self._zones.append((br, act, sym))
                    h2 = self._hover == (act, sym)
                    if h2:
                        p.fillRect(br, _qc(self._accent(), 60))
                    self._mini(p, br.center(), ic, _qc(self._accent(), 255 if h2 else 170),
                               on=bool(a.get({"pin": "pinned", "vis": "visible", "bell": "alerts"}.get(act, ""))))
                    bx += 22
            else:
                p.setFont(QFont("Courier New", 7))
                p.setPen(_qc(self._col("TEXT_DIM"), 230))
                nm = QFontMetrics(p.font()).elidedText(a.get("name", ""), Qt.TextElideMode.ElideRight,
                                                       int(r.width() * (0.42 if wide and ui["sparklines"] else 0.55)))
                p.drawText(QPointF(r.left() + 8, r.top() + 30), nm)
                if wide and ui["sparklines"] and q and q.get("spark"):
                    self._spark(p, QRectF(r.left() + r.width() * 0.46, r.top() + 18, r.width() * 0.26, 14),
                                q["spark"], ccol)
            p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            p.setPen(_qc(ccol, 245))
            if not hov:
                s = _pct(q["change_pct"]) if q else ""
                p.drawText(QPointF(r.right() - 8 - QFontMetrics(p.font()).horizontalAdvance(s), r.top() + 30), s)
            y += row_h
        p.restore()
        if len(assets) > fit:
            p.setFont(QFont("Courier New", 7))
            p.setPen(_qc(self._col("TEXT_DIM"), 200))
            p.drawText(QPointF(rect.left() + 8, rect.bottom() - 1), f"▾ {len(assets)} activos · rueda")

    def _bell(self, p, c: QPointF, r: float, col: QColor):
        p.setPen(QPen(col, 1.1))
        p.setBrush(col)
        path = QPainterPath()
        path.moveTo(c.x() - r, c.y() + r * .6)
        path.quadTo(c.x() - r, c.y() - r, c.x(), c.y() - r)
        path.quadTo(c.x() + r, c.y() - r, c.x() + r, c.y() + r * .6)
        path.closeSubpath()
        p.drawPath(path)
        p.drawLine(QPointF(c.x() - r - 1, c.y() + r * .6), QPointF(c.x() + r + 1, c.y() + r * .6))
        p.setBrush(Qt.BrushStyle.NoBrush)

    def _mini(self, p, c: QPointF, icon: str, col: QColor, on: bool):
        p.setPen(QPen(col, 1.2))
        p.setBrush(col if on and icon != "x" else Qt.BrushStyle.NoBrush)
        if icon == "pin":
            p.drawEllipse(QPointF(c.x(), c.y() - 2), 2.6, 2.6)
            p.drawLine(QPointF(c.x(), c.y() + 1), QPointF(c.x(), c.y() + 5))
        elif icon == "eye":
            p.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath()
            path.moveTo(c.x() - 5, c.y())
            path.quadTo(c.x(), c.y() - 5, c.x() + 5, c.y())
            path.quadTo(c.x(), c.y() + 5, c.x() - 5, c.y())
            p.drawPath(path)
            p.setBrush(col if on else Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, 1.5, 1.5)
        elif icon == "bell":
            self._bell(p, c, 3.4, col)
        elif icon == "x":
            p.drawLine(QPointF(c.x() - 3, c.y() - 3), QPointF(c.x() + 3, c.y() + 3))
            p.drawLine(QPointF(c.x() - 3, c.y() + 3), QPointF(c.x() + 3, c.y() - 3))
        p.setBrush(Qt.BrushStyle.NoBrush)

    def _spark(self, p, r: QRectF, vals, col):
        if len(vals) < 2:
            return
        lo, hi = min(vals), max(vals)
        span = (hi - lo) or 1.0
        path = QPainterPath()
        for i, v in enumerate(vals):
            x = r.left() + r.width() * i / (len(vals) - 1)
            y = r.bottom() - (v - lo) / span * r.height()
            path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
        p.setPen(QPen(_qc(col, 220), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

    # — large chart —
    def _chart_block(self, p, area: QRectF, sym: str, ui):
        if not sym:
            p.setFont(QFont("Courier New", 9))
            p.setPen(_qc(self._col("TEXT_DIM"), 220))
            p.drawText(area, Qt.AlignmentFlag.AlignCenter, "No assets.\nPress ＋ to add.")
            return
        q = self._quotes.get(sym)
        asset = self._store.get(sym) or self._preview or {"name": md.pretty_symbol(sym), "symbol": sym}
        saved = self._store.get(sym) is not None
        ccol = self._col("GREEN") if (not q or q["change_pct"] >= 0) else self._col("RED")
        y = area.top()
        p.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        p.setPen(_qc(self._col("WHITE"), 250))
        p.drawText(QPointF(area.left(), y + 13), md.pretty_symbol(sym))
        sw = QFontMetrics(p.font()).horizontalAdvance(md.pretty_symbol(sym)) + 8
        if not saved:                                            # vista previa: estrella para guardar
            r = QRectF(area.left() + sw, y, 18, 18)
            self._zones.append((r, "save_pre", ""))
            self._star(p, r.center(), 6, _qc(self._col("ACC2"), 255 if self._hover == ("save_pre", "") else 190))
            sw += 22
        p.setFont(QFont("Courier New", 7))
        p.setPen(_qc(self._col("TEXT_DIM"), 230))
        nm = QFontMetrics(p.font()).elidedText(asset.get("name", ""), Qt.TextElideMode.ElideRight,
                                               max(30, int(area.width() * 0.4) - sw))
        p.drawText(QPointF(area.left() + sw, y + 12), nm)
        if q:
            p.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
            ptxt = md.format_price(q["price"], q["currency"])
            pw = QFontMetrics(p.font()).horizontalAdvance(ptxt)
            p.setPen(_qc(self._col("WHITE"), 250))
            p.drawText(QPointF(area.right() - pw, y + 13), ptxt)
            p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            ctxt = f"{_pct(q['change_pct'])}   {md.format_price(q['change'], q['currency']).replace('$', '')}"
            p.setPen(_qc(ccol, 245))
            p.drawText(QPointF(area.right() - QFontMetrics(p.font()).horizontalAdvance(ctxt), y + 27), ctxt)
        y += 22
        # range tabs + chart type
        x = area.left()
        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        for rg in md.RANGE_ORDER:
            r = QRectF(x, y + 4, 24, 14)
            self._zones.append((r, "range", rg))
            on = ui["range"] == rg
            if on:
                p.fillRect(r, _qc(self._accent(), 70))
            elif self._hover == ("range", rg):
                p.fillRect(r, _qc(self._accent(), 30))
            p.setPen(_qc(self._col("WHITE") if on else self._col("TEXT_MED"), 240))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, rg)
            x += 26
        ct = {"area": "AREA", "line": "LINE", "candles": "CANDLES"}.get(ui["chart_type"], "AREA")
        r = QRectF(area.right() - 48, y + 4, 48, 14)
        self._zones.append((r, "ctype", ""))
        p.setPen(QPen(_qc(self._accent(), 150), 1))
        p.drawRect(r)
        p.setPen(_qc(self._col("TEXT"), 240))
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, ct)
        y += 22
        plot_area = QRectF(area.left(), y, area.width(), max(20, area.bottom() - y))
        s = self._series.get((sym, ui["range"]))
        if s is None:
            p.setFont(QFont("Courier New", 8))
            failed = time.time() - self._failed.get((sym, ui["range"]), 0) < 60
            p.setPen(_qc(self._col("RED") if failed else self._col("TEXT_DIM"), 220))
            p.drawText(plot_area, Qt.AlignmentFlag.AlignCenter,
                       "SIN DATOS · reintentando…" if failed else "CARGANDO…")
            return
        self._plot(p, plot_area, s, ui, ccol)

    def _star(self, p, c: QPointF, r: float, col: QColor):
        import math
        path = QPainterPath()
        for i in range(10):
            ang = -math.pi / 2 + i * math.pi / 5
            rr = r if i % 2 == 0 else r * 0.45
            pt = QPointF(c.x() + math.cos(ang) * rr, c.y() + math.sin(ang) * rr)
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        path.closeSubpath()
        p.setPen(QPen(col, 1.1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

    @staticmethod
    def _aggregate(s: dict, max_n: int) -> dict:
        n = len(s["close"])
        if n <= max_n:
            return s
        g = -(-n // max_n)
        out = {k: [] for k in ("ts", "open", "high", "low", "close", "volume")}
        for i in range(0, n, g):
            sl = slice(i, i + g)
            out["ts"].append(s["ts"][i])
            out["open"].append(s["open"][i])
            out["high"].append(max(s["high"][sl]))
            out["low"].append(min(s["low"][sl]))
            out["close"].append(s["close"][sl][-1])
            out["volume"].append(sum(s["volume"][sl]))
        return out

    def _plot(self, p, area: QRectF, s: dict, ui, ccol: str):
        ctype = ui["chart_type"]
        if s.get("line_only") and ctype == "candles":
            ctype = "area"
        plot = QRectF(area.left(), area.top(), max(30, area.width() - 52), max(20, area.height() - 14))
        d = self._aggregate(s, max(20, int(plot.width() / 5))) if ctype == "candles" else s
        n = len(d["close"])
        if n < 2:
            p.setPen(_qc(self._col("TEXT_DIM"), 220))
            p.drawText(area, Qt.AlignmentFlag.AlignCenter, "Pocos datos en este rango.")
            return
        lows = d["low"] if ctype == "candles" else d["close"]
        highs = d["high"] if ctype == "candles" else d["close"]
        lo, hi = min(lows), max(highs)
        pad = (hi - lo) * 0.08 or abs(hi) * 0.01 or 1.0
        lo, hi = lo - pad, hi + pad
        span = hi - lo

        def X(i):
            return plot.left() + plot.width() * i / (n - 1)

        def Y(v):
            return plot.bottom() - (v - lo) / span * plot.height()

        # rejilla + eje de precios
        p.setFont(QFont("Courier New", 7))
        cur = s.get("currency", "USD")
        for k in range(5):
            v = lo + span * k / 4
            yy = Y(v)
            p.setPen(QPen(_qc(self._accent(), 28), 1, Qt.PenStyle.DotLine))
            p.drawLine(QPointF(plot.left(), yy), QPointF(plot.right(), yy))
            p.setPen(_qc(self._col("TEXT_DIM"), 230))
            p.drawText(QPointF(plot.right() + 4, yy + 3), md.format_price(v, cur).replace("$", ""))
        # eje de tiempo
        rng = ui["range"]
        fmt = "%H:%M" if rng == "1D" else ("%d/%m" if rng in ("5D", "1M", "6M") else "%m/%y")
        for k in range(4):
            i = int((n - 1) * k / 3)
            try:
                lab = datetime.fromtimestamp(d["ts"][i]).strftime(fmt)
            except Exception:
                lab = ""
            tx = X(i)
            fw = QFontMetrics(p.font()).horizontalAdvance(lab)
            p.setPen(_qc(self._col("TEXT_DIM"), 230))
            p.drawText(QPointF(min(max(plot.left(), tx - fw / 2), plot.right() - fw), plot.bottom() + 11), lab)
        # volumen
        if ui["show_volume"] and any(d["volume"]):
            vmax = max(d["volume"]) or 1
            bw = max(1.0, plot.width() / n * 0.7)
            for i, v in enumerate(d["volume"]):
                hgt = plot.height() * 0.18 * (v / vmax)
                p.fillRect(QRectF(X(i) - bw / 2, plot.bottom() - hgt, bw, hgt), _qc(self._accent(), 55))
        # serie
        if ctype == "candles":
            bw = max(1.0, plot.width() / n * 0.65)
            for i in range(n):
                up = d["close"][i] >= d["open"][i]
                col = _qc(self._col("GREEN") if up else self._col("RED"), 235)
                p.setPen(QPen(col, 1))
                p.drawLine(QPointF(X(i), Y(d["high"][i])), QPointF(X(i), Y(d["low"][i])))
                top, bot = Y(max(d["open"][i], d["close"][i])), Y(min(d["open"][i], d["close"][i]))
                p.fillRect(QRectF(X(i) - bw / 2, top, bw, max(1.0, bot - top)), col)
        else:
            path = QPainterPath()
            for i, v in enumerate(d["close"]):
                path.moveTo(X(i), Y(v)) if i == 0 else path.lineTo(X(i), Y(v))
            if ctype == "area":
                fill = QPainterPath(path)
                fill.lineTo(X(n - 1), plot.bottom())
                fill.lineTo(X(0), plot.bottom())
                fill.closeSubpath()
                g = QLinearGradient(0, plot.top(), 0, plot.bottom())
                g.setColorAt(0, _qc(ccol, 90))
                g.setColorAt(1, _qc(ccol, 0))
                p.fillPath(fill, QBrush(g))
            p.setPen(QPen(_qc(ccol, 245), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
        # precio actual
        last = d["close"][-1]
        p.setPen(QPen(_qc(ccol, 140), 1, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(plot.left(), Y(last)), QPointF(plot.right(), Y(last)))
        tag = QRectF(plot.right() + 1, Y(last) - 6, 50, 12)
        p.fillRect(tag, _qc(ccol, 220))
        p.setPen(_qc(self._col("DARK"), 255))
        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        p.drawText(tag, Qt.AlignmentFlag.AlignCenter, md.format_price(last, cur).replace("$", ""))
        # cursor
        m = self._mouse
        if plot.contains(m) and not self._drag:
            i = max(0, min(n - 1, int(round((m.x() - plot.left()) / plot.width() * (n - 1)))))
            p.setPen(QPen(_qc(self._accent(), 120), 1))
            p.drawLine(QPointF(X(i), plot.top()), QPointF(X(i), plot.bottom()))
            p.setBrush(_qc(ccol, 255))
            p.drawEllipse(QPointF(X(i), Y(d["close"][i])), 2.8, 2.8)
            try:
                when = datetime.fromtimestamp(d["ts"][i]).strftime("%d/%m %H:%M" if rng in ("1D", "5D") else "%d/%m/%Y")
            except Exception:
                when = ""
            if ctype == "candles":
                lines = [when, f"O {md.format_price(d['open'][i], cur)}   H {md.format_price(d['high'][i], cur)}",
                         f"L {md.format_price(d['low'][i], cur)}   C {md.format_price(d['close'][i], cur)}"]
            else:
                lines = [when, md.format_price(d["close"][i], cur)]
            p.setFont(QFont("Courier New", 7))
            fm = QFontMetrics(p.font())
            bw2 = max(fm.horizontalAdvance(t) for t in lines) + 10
            bh = 12 * len(lines) + 4
            bx = X(i) + 8 if X(i) + 8 + bw2 < plot.right() else X(i) - 8 - bw2
            by = max(plot.top(), min(m.y() - bh / 2, plot.bottom() - bh))
            p.fillRect(QRectF(bx, by, bw2, bh), _qc(self._col("DARK"), 235))
            p.setPen(QPen(_qc(self._accent(), 160), 1))
            p.drawRect(QRectF(bx, by, bw2, bh))
            p.setPen(_qc(self._col("WHITE"), 245))
            for k, t in enumerate(lines):
                p.drawText(QPointF(bx + 5, by + 12 + 12 * k - 2), t)

    # — noticias —
    def _news_block(self, p, rect: QRectF, sym: str):
        items = self._news.get(sym, [])
        p.setPen(QPen(_qc(self._accent(), 60), 1))
        p.drawLine(QPointF(rect.left(), rect.top()), QPointF(rect.right(), rect.top()))
        a = self._store.get(sym) or {}
        q = self._quotes.get(sym)
        ref = float(self._store.alerts().get("move_pct_crypto" if a.get("kind") == "crypto"
                                             else "move_pct_stock", 4.0))
        p.setFont(QFont("Courier New", 7))
        if not items:
            p.setPen(_qc(self._col("TEXT_DIM"), 220))
            p.drawText(rect, Qt.AlignmentFlag.AlignVCenter, "Sin noticias recientes.")
            return
        fm = QFontMetrics(p.font())
        y = rect.top() + 12
        for i, it in enumerate(items[:2]):
            sc = mr.score_headline(it["title"], symbol=sym, name=a.get("name", ""), ts=it["ts"],
                                   pinned=bool(a.get("pinned")),
                                   move_pct=(q or {}).get("change_pct", 0.0), move_ref=ref)
            hot = sc >= mr.SENSITIVITY.get(self._store.alerts().get("sensitivity", "medium"), 4)
            r = QRectF(rect.left(), y - 10, rect.width(), 14)
            self._zones.append((r, "news", str(i)))
            if self._hover == ("news", str(i)):
                p.fillRect(r, _qc(self._accent(), 28))
            p.setPen(_qc(self._col("ACC2") if hot else self._col("TEXT_DIM"), 250))
            p.drawText(QPointF(rect.left() + 2, y), "!" if hot else "·")
            meta = f"  {it.get('publisher', '')[:14]} {_ago(it['ts'])}".rstrip()
            p.setPen(_qc(self._col("TEXT"), 235))
            title = fm.elidedText(it["title"], Qt.TextElideMode.ElideRight,
                                  int(rect.width() - 12 - fm.horizontalAdvance(meta)))
            p.drawText(QPointF(rect.left() + 11, y), title)
            p.setPen(_qc(self._col("TEXT_DIM"), 220))
            p.drawText(QPointF(rect.left() + 11 + fm.horizontalAdvance(title), y), meta)
            y += 15

    # — aviso en vivo —
    def _banner(self, p, W):
        d, _until = self._alert
        pulse = 0.55 + 0.45 * abs(((time.time() * 1.6) % 2) - 1)
        r = QRectF(8, HDR_H + 2, W - 16, 20)
        amber = self._col("ACC2")
        p.fillRect(r, _qc(self._col("DARK"), 240))
        p.setPen(QPen(_qc(amber, int(255 * pulse)), 1.2))
        p.drawRect(r)
        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        head = f"! {d.get('name') or md.pretty_symbol(d.get('symbol', ''))} {_pct(d.get('change_pct'))}"
        txt = head + (f" — {d['headline']}" if d.get("headline") else " — movimiento fuerte")
        fm = QFontMetrics(p.font())
        p.setPen(_qc(amber, 250))
        p.drawText(r.adjusted(6, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(txt, Qt.TextElideMode.ElideRight, int(r.width() - 12)))
