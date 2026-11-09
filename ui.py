from __future__ import annotations

from hud.credits import CREDITS_LINE, PROJECT_NAME

import bisect
import json
import math
import os
import platform
import random
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

if platform.system() == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}

from PyQt6.QtCore import (
    QEasingCurve, QLineF, QMimeData, QObject, QParallelAnimationGroup, QPointF,
    QPoint, QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer, QUrl,
    pyqtProperty, pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush, QColor, QConicalGradient, QDragEnterEvent, QDropEvent, QFont,
    QFontDatabase, QFontMetricsF, QKeySequence, QLinearGradient, QPainter, QPainterPath,
    QPen, QPixmap, QPolygon, QPolygonF, QRadialGradient, QRegion, QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QGraphicsDropShadowEffect,
    QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPushButton, QScrollArea, QSizePolicy, QSplitter,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget, QProgressBar,
)

try:
    from core.avatar import HoloAvatar
except Exception:      # pragma: no cover — HUD must never die over cosmetics
    HoloAvatar = None

try:
    from core.globe_widget import GlobeWidget
except Exception as e:      # pragma: no cover — same rule: optional, never fatal
    print(f"[Globe] Widget unavailable — HUD will run without it: {e}")
    GlobeWidget = None

try:
    # Market radar (stocks + crypto). Optional: if it fails, the HUD carries on unchanged.
    from core.market_radar_widget import MarketRadarWidget
except Exception as e:      # pragma: no cover
    print(f"[Radar] Market module unavailable — the HUD will continue without it: {e}")
    MarketRadarWidget = None

try:
    # Powers the embedded YouTube player inside FloatingVideoWindow. Optional:
    # if PyQt6-WebEngine isn't installed the window falls back to a "open in
    # browser" placeholder instead of taking the app down.
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import (
        QWebEngineProfile, QWebEnginePage, QWebEngineUrlRequestInterceptor,
    )
    _WEBENGINE_OK = True
except Exception as e:      # pragma: no cover — same rule: optional, never fatal
    print(f"[Video] PyQt6-WebEngine unavailable — embedded video windows will "
          f"fall back to 'open in browser': {e}")
    QWebEngineView = None
    QWebEngineProfile = None
    QWebEnginePage = None
    QWebEngineUrlRequestInterceptor = None
    _WEBENGINE_OK = False

if _WEBENGINE_OK:
    class _LoggingVideoPage(QWebEnginePage):
        """Same trick as globe_widget.py's _LoggingPage: prints JS
        console.log/warn/error from inside the embed to the terminal
        (prefixed [Video][JS]), since a plain QWebEngineView gives no other
        way to see why the YouTube embed inside it failed."""

        def javaScriptConsoleMessage(self, level, message, line, source):
            print(f"[Video][JS] {message} (line {line}, {source})")

    class _LoggingVideoInterceptor(QWebEngineUrlRequestInterceptor):
        """Diagnostic only: prints every request the embed makes, so a
        redirect to a cookie-consent page or a blocked domain shows up in
        the terminal instead of just silently changing what gets loaded."""

        def interceptRequest(self, info):
            try:
                print(f"[Video][NET] {bytes(info.requestMethod()).decode(errors='replace')} "
                      f"{info.requestUrl().toString()}")
            except Exception as e:
                print(f"[Video][NET] (log failed: {e})")

# QtWebEngine's default User-Agent string identifies itself as
# "QtWebEngine/x.y ..." — YouTube reads that token as "this is an embedded
# WebView, not a real desktop browser" and, regardless of the video, serves
# its "This video isn't available — Watch on YouTube" hand-off screen
# instead of the player (error code 152-N; the same generic behaviour
# Android WebView apps hit for the same reason). Masquerading as a normal
# desktop Chrome fixes it — this is the standard workaround for embedded
# Chromium WebViews (Qt, Electron, Android) hitting this exact screen.
_DESKTOP_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"


def _read_full_config() -> dict:
    """Read api_keys.json config dict. Returns {} on any error."""
    try:
        return json.loads(API_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


# Single source of truth for the release name — the window title, the header
# badge and the readme must never disagree again.
APP_VERSION  = PROJECT_NAME
APP_PROTOCOL = APP_VERSION.split()[-1]

_DEFAULT_W, _DEFAULT_H = 980, 700
_MIN_W,     _MIN_H     = 900, 600
_LEFT_W  = 164
_RIGHT_W = 340

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"


class C:
    BG        = "#00060a"
    PANEL     = "#010d14"
    PANEL2    = "#010f18"
    BORDER    = "#0d3347"
    BORDER_B  = "#1a5c7a"
    BORDER_A  = "#0f4060"
    PRI       = "#00d4ff"
    PRI_DIM   = "#007a99"
    PRI_GHO   = "#001f2e"
    ACC       = "#ff6b00"
    ACC2      = "#ffcc00"
    GREEN     = "#00ff88"
    GREEN_D   = "#00aa55"
    RED       = "#ff3355"
    MUTED_C   = "#ff3366"
    TEXT      = "#8ffcff"
    TEXT_DIM  = "#3a8a9a"
    TEXT_MED  = "#5ab8cc"
    WHITE     = "#d8f8ff"
    DARK      = "#000d14"
    BAR_BG    = "#011520"


# Keys tied to the accent colour — status colours (ACC, GREEN, RED…) stay fixed
_HUE_LINKED = (
    "BG", "PANEL", "PANEL2", "BORDER", "BORDER_B", "BORDER_A",
    "PRI", "PRI_DIM", "PRI_GHO", "TEXT", "TEXT_DIM", "TEXT_MED",
    "WHITE", "DARK", "BAR_BG",
)
_PALETTE_DEFAULTS: dict[str, str] = {k: getattr(C, k) for k in _HUE_LINKED}

DEFAULT_UI_COLOR = _PALETTE_DEFAULTS["PRI"]


def apply_ui_accent(accent_hex: str) -> bool:
    """
    Re-derives the whole teal-family palette from the chosen accent colour
    (hue shift — brightness/saturation ratios are preserved, design stays intact).
    Painted elements (HUD, waveform, metrics) pick up the new colour on the next
    frame; stylesheet-based panels pick it up when they are rebuilt.
    """
    import colorsys

    accent_hex = (accent_hex or "").strip().lower()
    if not (accent_hex.startswith("#") and len(accent_hex) == 7):
        return False
    try:
        int(accent_hex[1:], 16)
    except ValueError:
        return False

    def _hsv(h: str) -> tuple[float, float, float]:
        r = int(h[1:3], 16) / 255
        g = int(h[3:5], 16) / 255
        b = int(h[5:7], 16) / 255
        return colorsys.rgb_to_hsv(r, g, b)

    base_h            = _hsv(_PALETTE_DEFAULTS["PRI"])[0]
    acc_h, acc_s, _av = _hsv(accent_hex)
    dh   = acc_h - base_h
    grey = acc_s < 0.08   # near-grey accent → the whole theme is desaturated

    for key, hex0 in _PALETTE_DEFAULTS.items():
        h, s, v = _hsv(hex0)
        if grey:
            s *= 0.15
        r, g, b = colorsys.hsv_to_rgb((h + dh) % 1.0, s, v)
        setattr(C, key, "#{:02x}{:02x}{:02x}".format(
            int(r * 255 + 0.5), int(g * 255 + 0.5), int(b * 255 + 0.5)))
    return True


def current_palette() -> dict[str, str]:
    """A snapshot of the accent-linked colours currently on class C."""
    return {k: getattr(C, k) for k in _HUE_LINKED}


def retheme_all_widgets(old: dict[str, str], new: dict[str, str]) -> None:
    """
    LIVE full theme change. Replaces the old palette colours with the new ones
    in EVERY widget's stylesheet across the app and repaints them. This way the
    colour change applies INSTANTLY across the whole interface — panels, buttons,
    borders included — not just the painted elements. No restart needed.
    """
    mapping = {old[k].lower(): new[k].lower()
               for k in old if old[k].lower() != new.get(k, old[k]).lower()}
    if not mapping:
        return
    app = QApplication.instance()
    if app is None:
        return
    for w in app.allWidgets():
        try:
            ss = w.styleSheet()
            if ss:
                s2 = ss
                for o, n in mapping.items():
                    if o in s2:
                        s2 = s2.replace(o, n)
                if s2 != ss:
                    w.setStyleSheet(s2)
            if w.property("hudGlow"):
                eff = w.graphicsEffect()
                if isinstance(eff, QGraphicsDropShadowEffect):
                    eff.setColor(qcol(new["PRI"], 210))
            w.update()
        except Exception:
            pass


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h); c.setAlpha(a); return c


# ══════════════════════════════════════════════════════════════════════════════
#  HUD SKIN — holographic / Stark-style chrome
#  Everything below is pure presentation: chamfered "glass" panels, neon glow,
#  tech lines and a cached circuit backdrop. All colours are read from `C` at
#  paint time, so the live accent-colour picker keeps working unchanged.
# ══════════════════════════════════════════════════════════════════════════════

def _chamfer_points(r: QRectF, cuts) -> list:
    """Octagon-ish outline of `r`. cuts = (top-left, top-right, bottom-right,
    bottom-left) chamfer sizes in px; 0 keeps that corner square."""
    tl, tr, br, bl = (float(c) for c in cuts)
    x0, y0, x1, y1 = r.left(), r.top(), r.right(), r.bottom()
    return [
        QPointF(x0 + tl, y0), QPointF(x1 - tr, y0),
        QPointF(x1, y0 + tr), QPointF(x1, y1 - br),
        QPointF(x1 - br, y1), QPointF(x0 + bl, y1),
        QPointF(x0, y1 - bl), QPointF(x0, y0 + tl),
    ]


def _chamfer_path(r: QRectF, cuts) -> QPainterPath:
    pts = _chamfer_points(r, cuts)
    path = QPainterPath(pts[0])
    for pt in pts[1:]:
        path.lineTo(pt)
    path.closeSubpath()
    return path


def _render_layer(W: int, H: int, dpr: float, fn) -> QPixmap:
    """Draw a static decoration layer once into a transparent pixmap."""
    pm = QPixmap(max(1, int(W * dpr)), max(1, int(H * dpr)))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    try:
        fn(p)
    finally:
        p.end()
    return pm


def _glow_pen(color: str, alpha: int, width: float) -> QPen:
    return QPen(qcol(color, alpha), width, Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)


def _paint_hud_frame(p: QPainter, W: float, H: float, cuts, *, glow: bool = True,
                     tint: float = 0.10, accent: str | None = None,
                     inset: float = 3.0) -> None:
    """Glass panel with bevelled corners, neon edge, corner brackets, tick marks."""
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    acc = accent or C.PRI
    r = QRectF(inset, inset, W - 2 * inset, H - 2 * inset)
    if r.width() < 8 or r.height() < 8:
        return
    path = _chamfer_path(r, cuts)

    # glass body: cool tint at the top fading into the dark, faint scanlines
    g = QLinearGradient(0, 0, 0, H)
    g.setColorAt(0.0, qcol(acc, int(255 * tint * 1.7)))
    g.setColorAt(0.55, qcol(C.DARK, 105))
    g.setColorAt(1.0, qcol(acc, int(255 * tint * 0.7)))
    p.fillPath(path, QBrush(g))
    p.fillPath(path, QBrush(qcol(acc, 12), Qt.BrushStyle.HorPattern))

    # neon bloom (three widening, fading strokes) + crisp edge
    p.setBrush(Qt.BrushStyle.NoBrush)
    if glow:
        for w, a in ((7.0, 9), (4.5, 18), (2.6, 34)):
            p.setPen(_glow_pen(acc, a, w))
            p.drawPath(path)
    p.setPen(QPen(qcol(C.PRI_DIM, 235), 1.1))
    p.drawPath(path)

    # bright corner brackets that hug the chamfers
    pts = _chamfer_points(r, cuts)
    L = max(6.0, min(24.0, r.width() * 0.22, r.height() * 0.22))
    x0, y0, x1, y1 = r.left(), r.top(), r.right(), r.bottom()
    tl, tr, br, bl = (float(c) for c in cuts)
    p.setPen(QPen(qcol(acc, 245), 2.0, Qt.PenStyle.SolidLine))
    if tl:
        p.drawPolyline(QPolygonF([QPointF(x0, y0 + tl + L), pts[7], pts[0],
                                  QPointF(x0 + tl + L, y0)]))
    if br:
        p.drawPolyline(QPolygonF([QPointF(x1, y1 - br - L), pts[3], pts[4],
                                  QPointF(x1 - br - L, y1)]))
    p.setPen(QPen(qcol(acc, 150), 1.4, Qt.PenStyle.SolidLine))
    if tr:
        p.drawPolyline(QPolygonF([QPointF(x1 - tr - L * 0.6, y0), pts[1], pts[2],
                                  QPointF(x1, y0 + tr + L * 0.6)]))
    if bl:
        p.drawPolyline(QPolygonF([QPointF(x0, y1 - bl - L * 0.6), pts[6], pts[5],
                                  QPointF(x0 + bl + L * 0.6, y1)]))

    # tech details: three status squares on the top edge, tick marks bottom-left
    if W > 130:
        sx = x1 - tr - 18
        for i, a in enumerate((230, 150, 80)):
            p.fillRect(QRectF(sx - i * 9, y0 - 2.5, 5, 5), qcol(acc, a))
        p.setPen(QPen(qcol(acc, 110), 1))
        for i in range(6):
            tx = x0 + bl + L + 8 + i * 4
            p.drawLine(QPointF(tx, y1 - 1), QPointF(tx, y1 - (5 if i % 2 == 0 else 3)))


def _paint_hud_bar(p: QPainter, W: float, H: float) -> None:
    """Top bar: flat band with a lowered, slanted centre tab for the title."""
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    cx, tw = W / 2.0, min(380.0, W * 0.5)
    flat, dip, m = H - 13.0, H - 3.0, 3.0
    pts = [QPointF(m, m), QPointF(W - m, m), QPointF(W - m, flat),
           QPointF(cx + tw / 2 + 14, flat), QPointF(cx + tw / 2, dip),
           QPointF(cx - tw / 2, dip), QPointF(cx - tw / 2 - 14, flat),
           QPointF(m, flat)]
    path = QPainterPath(pts[0])
    for pt in pts[1:]:
        path.lineTo(pt)
    path.closeSubpath()

    g = QLinearGradient(0, 0, 0, H)
    g.setColorAt(0.0, qcol(C.PRI, 46))
    g.setColorAt(1.0, qcol(C.DARK, 150))
    p.fillPath(path, QBrush(g))
    p.fillPath(path, QBrush(qcol(C.PRI, 12), Qt.BrushStyle.HorPattern))

    p.setBrush(Qt.BrushStyle.NoBrush)
    for w, a in ((7.0, 9), (4.5, 18), (2.6, 34)):
        p.setPen(_glow_pen(C.PRI, a, w))
        p.drawPath(path)
    p.setPen(QPen(qcol(C.PRI_DIM, 235), 1.1))
    p.drawPath(path)

    # bright segments: both ends of the flat edge + the slanted title tab
    p.setPen(QPen(qcol(C.PRI, 245), 2.0))
    seg = min(130.0, max(20.0, cx - tw / 2 - 40))
    p.drawLine(QPointF(m, flat), QPointF(m + seg, flat))
    p.drawLine(QPointF(W - m - seg, flat), QPointF(W - m, flat))
    p.drawPolyline(QPolygonF([pts[3], pts[4], pts[5], pts[6]]))
    # secondary hairline + status squares at each end
    p.setPen(QPen(qcol(C.PRI, 70), 1))
    p.drawLine(QPointF(m + seg + 10, flat - 4), QPointF(cx - tw / 2 - 30, flat - 4))
    p.drawLine(QPointF(cx + tw / 2 + 30, flat - 4), QPointF(W - m - seg - 10, flat - 4))
    for i, a in enumerate((230, 150, 80)):
        p.fillRect(QRectF(m + 8 + i * 9, m - 2.5, 5, 5), qcol(C.PRI, a))
        p.fillRect(QRectF(W - m - 13 - i * 9, m - 2.5, 5, 5), qcol(C.PRI, a))


class HudFrame(QWidget):
    """Bevelled glass container. Lay children out inside it exactly like in a
    plain QWidget — only the outline / background is custom painted (cached in
    a pixmap, so child animations never re-stroke the border).

    Keep layout margins >= cut + 4 px so square child corners stay inside the
    chamfers.
    """

    def __init__(self, cuts=(14, 0, 14, 0), glow: bool = True, tint: float = 0.10,
                 accent: str | None = None, parent=None):
        super().__init__(parent)
        self._hf_cuts   = tuple(float(c) for c in cuts)
        self._hf_glow   = glow
        self._hf_tint   = tint
        self._hf_accent = accent
        self._hf_pm: QPixmap | None = None
        self._hf_key    = None
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

    def paintEvent(self, _):
        W, H = self.width(), self.height()
        if W < 4 or H < 4:
            return
        dpr = self.devicePixelRatioF()
        key = (W, H, dpr, C.PRI, C.PRI_DIM, C.DARK, self._hf_accent)
        if self._hf_pm is None or self._hf_key != key:
            self._hf_pm = _render_layer(W, H, dpr, lambda p: _paint_hud_frame(
                p, W, H, self._hf_cuts, glow=self._hf_glow,
                tint=self._hf_tint, accent=self._hf_accent))
            self._hf_key = key
        p = QPainter(self)
        if not p.isActive():
            return
        p.drawPixmap(0, 0, self._hf_pm)
        p.end()


class HudHeaderBar(QWidget):
    """Full-width header with the lowered centre tab (see _paint_hud_bar)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hb_pm: QPixmap | None = None
        self._hb_key = None

    def paintEvent(self, _):
        W, H = self.width(), self.height()
        if W < 4 or H < 4:
            return
        dpr = self.devicePixelRatioF()
        key = (W, H, dpr, C.PRI, C.PRI_DIM, C.DARK)
        if self._hb_pm is None or self._hb_key != key:
            self._hb_pm = _render_layer(W, H, dpr, lambda p: _paint_hud_bar(p, W, H))
            self._hb_key = key
        p = QPainter(self)
        if not p.isActive():
            return
        p.drawPixmap(0, 0, self._hb_pm)
        p.end()


class HudBackdrop(QWidget):
    """Window background: deep gradient, faint grid, concentric rings and
    circuit traces (rendered once per size / theme and blitted afterwards)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bd_pm: QPixmap | None = None
        self._bd_key = None

    @staticmethod
    def _draw(p: QPainter, W: int, H: int) -> None:
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(0, 0, W, H, qcol(C.BG))
        cx, cy = W * 0.5, H * 0.50
        rg = QRadialGradient(cx, cy, max(W, H) * 0.62)
        rg.setColorAt(0.0, qcol(C.PRI, 38))
        rg.setColorAt(0.45, qcol(C.PRI, 11))
        rg.setColorAt(1.0, qcol(C.BG, 0))
        p.fillRect(0, 0, W, H, QBrush(rg))

        step = 48
        p.setPen(QPen(qcol(C.PRI, 9), 1))
        for x in range(0, W, step):
            p.drawLine(x, 0, x, H)
        for y in range(0, H, step):
            p.drawLine(0, y, W, y)

        base = min(W, H)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for k, (f, a) in enumerate(((0.26, 26), (0.38, 18), (0.52, 12))):
            pen = QPen(qcol(C.PRI, a), 1.2 if k == 0 else 1.0,
                       Qt.PenStyle.DashLine if k == 1 else Qt.PenStyle.SolidLine)
            p.setPen(pen)
            p.drawEllipse(QPointF(cx, cy), base * f, base * f)

        rnd = random.Random(11)          # fixed seed → stable layout per size
        cols, rows = max(2, W // step), max(2, H // step)
        for _ in range(16):
            gx, gy = rnd.randrange(cols), rnd.randrange(rows)
            x, y = gx * step, gy * step
            pts = [QPointF(x, y)]
            horiz = rnd.random() < 0.5
            for _seg in range(rnd.randint(2, 4)):
                run = rnd.randint(1, 4) * step * rnd.choice((-1, 1))
                x, y = (x + run, y) if horiz else (x, y + run)
                horiz = not horiz
                pts.append(QPointF(x, y))
            p.setPen(QPen(qcol(C.PRI, 30), 1.2))
            p.drawPolyline(QPolygonF(pts))
            p.setBrush(QBrush(qcol(C.PRI, 90)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(pts[-1], 2.5, 2.5)
            p.setBrush(Qt.BrushStyle.NoBrush)

    def paintEvent(self, _):
        W, H = self.width(), self.height()
        if W < 4 or H < 4:
            return
        dpr = self.devicePixelRatioF()
        key = (W, H, dpr, C.PRI, C.BG)
        if self._bd_pm is None or self._bd_key != key:
            self._bd_pm = _render_layer(W, H, dpr, lambda p: self._draw(p, W, H))
            self._bd_key = key
        p = QPainter(self)
        if not p.isActive():
            return
        p.drawPixmap(0, 0, self._bd_pm)
        p.end()


class HudButton(QPushButton):
    """Chamfered neon button. kind: 'pri' (accent) | 'danger' | 'ok'.
    Same API as QPushButton (clicked, setFont, setFixedSize, ...)."""

    _KINDS = {"pri": "PRI", "danger": "MUTED_C", "ok": "GREEN"}

    def __init__(self, text: str = "", kind: str = "pri", parent=None):
        super().__init__(text, parent)
        self._kind  = kind
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        col = getattr(C, self._KINDS.get(self._kind, "PRI"))
        W, H = self.width(), self.height()
        cut = min(9.0, H / 3.0)
        path = _chamfer_path(QRectF(2, 2, W - 4, H - 4), (cut, 0, cut, 0))
        if not self.isEnabled():
            alpha_fill, alpha_line = 10, 70
        elif self.isDown():
            alpha_fill, alpha_line = 95, 255
        elif self._hover:
            alpha_fill, alpha_line = 55, 255
        else:
            alpha_fill, alpha_line = 22, 175
        p.fillPath(path, QBrush(qcol(col, alpha_fill)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self._hover and self.isEnabled():
            p.setPen(_glow_pen(col, 40, 5.0))
            p.drawPath(path)
        p.setPen(QPen(qcol(col, alpha_line), 1.2))
        p.drawPath(path)
        p.setPen(qcol(col, 255 if self.isEnabled() else 110))
        p.setFont(self.font())
        p.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()


def hud_qss() -> str:
    """Slim global stylesheet (set on the central widget so the live re-theme
    pass rewrites it together with every other stylesheet). Selector-based on
    purpose: no blanket `background:` rule, so glass panels stay translucent."""
    return f"""
        QToolTip {{
            background: {C.DARK}; color: {C.TEXT};
            border: 1px solid {C.PRI_DIM}; padding: 3px 6px;
        }}
        QScrollBar:vertical {{
            background: transparent; width: 6px; margin: 2px 0; border: none;
        }}
        QScrollBar::handle:vertical {{
            background: {C.PRI_DIM}; min-height: 24px; border-radius: 0px;
        }}
        QScrollBar::handle:vertical:hover {{ background: {C.PRI}; }}
        QScrollBar:horizontal {{
            background: transparent; height: 6px; margin: 0 2px; border: none;
        }}
        QScrollBar::handle:horizontal {{
            background: {C.PRI_DIM}; min-width: 24px; border-radius: 0px;
        }}
        QScrollBar::handle:horizontal:hover {{ background: {C.PRI}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{
            width: 0px; height: 0px; border: none; background: none;
        }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
        QSplitter::handle {{ background: transparent; }}
        QSplitter::handle:vertical {{ height: 6px; }}
        QSplitter::handle:hover {{ background: {C.PRI_GHO}; }}
    """



# ── Windows GPU via NVML DLL (no subprocess, no console window) ──────────────
_nvml_lib: object = None   # cached ctypes DLL
_nvml_ok:  object = None   # None=untested, True=works, False=unavailable


def _nvml_gpu_windows() -> float:
    """Return NVIDIA GPU utilisation % using nvml.dll directly — zero subprocess."""
    global _nvml_lib, _nvml_ok
    if _nvml_ok is False:
        return -1.0
    try:
        import ctypes

        class _Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        if _nvml_lib is None:
            for dll_name in ("nvml", r"C:\Windows\System32\nvml.dll"):
                try:
                    lib = ctypes.WinDLL(dll_name)
                    lib.nvmlInit_v2()
                    _nvml_lib = lib
                    break
                except Exception:
                    continue

        if _nvml_lib is None:
            import pynvml  # type: ignore
            pynvml.nvmlInit()
            h = pynvml.nvmlDeviceGetHandleByIndex(0)
            _nvml_ok = True
            return float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)

        dev = ctypes.c_void_p()
        _nvml_lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
        util = _Util()
        _nvml_lib.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(util))
        _nvml_ok = True
        return float(util.gpu)
    except Exception:
        _nvml_ok = False
        return -1.0


class _SysMetrics:
    def __init__(self):
        self.cpu  = 0.0
        self.mem  = 0.0
        self.net  = 0.0   
        self.gpu  = -1.0  
        self.tmp  = -1.0  
        self._lock = threading.Lock()
        self._last_net = psutil.net_io_counters()
        self._last_net_t = time.time()
        self._running = True
        # Probe caches — GPU (NVML) and temperature (WMI) are the expensive
        # queries; initialise their handles once and reuse them instead of
        # rebuilding a connection on every poll.
        self._slow_tick = 0            # gpu/temp refreshed every 3rd cycle
        self._pynvml    = None         # cached pynvml module + device handle
        self._pynvml_h  = None
        self._pynvml_ok = None         # None=untested, False=unavailable here
        self._nv_unix   = None         # cached (lib, dev) for Linux/macOS NVML
        self._wmi_conn  = None         # cached WMI connection (creating one is slow)
        self._wmi_ok    = None         # None=untested, False=unavailable here
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        while self._running:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(2.0)

    def _update(self):
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent

        nc  = psutil.net_io_counters()
        now = time.time()
        dt  = now - self._last_net_t
        if dt > 0:
            sent = (nc.bytes_sent - self._last_net.bytes_sent) / dt
            recv = (nc.bytes_recv - self._last_net.bytes_recv) / dt
            net  = (sent + recv) / (1024 * 1024)
        else:
            net = 0.0
        self._last_net   = nc
        self._last_net_t = now

        # GPU and temperature change slowly and are the most expensive probes
        # (NVML / WMI) — refresh them every 3rd cycle (~6 s) instead of every
        # cycle, reusing the previous reading in between.
        self._slow_tick = (self._slow_tick + 1) % 3
        if self._slow_tick == 1:
            gpu = self._get_gpu()
            tmp = self._get_temp()
        else:
            gpu = self.gpu
            tmp = self.tmp

        with self._lock:
            self.cpu = cpu
            self.mem = mem
            self.net = net
            self.gpu = gpu
            self.tmp = tmp

    def _get_gpu(self) -> float:
        # pynvml — subprocess-free; initialise once and reuse the handle.
        # Re-initialising NVML on every poll is slow, so cache it and stop
        # retrying pynvml entirely once it proves unavailable here.
        if self._pynvml_ok is not False:
            try:
                if self._pynvml_h is None:
                    import pynvml  # type: ignore
                    pynvml.nvmlInit()
                    self._pynvml    = pynvml
                    self._pynvml_h  = pynvml.nvmlDeviceGetHandleByIndex(0)
                    self._pynvml_ok = True
                return float(self._pynvml.nvmlDeviceGetUtilizationRates(self._pynvml_h).gpu)
            except Exception:
                self._pynvml_ok = False

        # Windows: nvml.dll via ctypes (already cached in _nvml_gpu_windows)
        if _OS == "Windows":
            return _nvml_gpu_windows()

        # Linux / macOS: libnvidia-ml shared lib via ctypes — init once, reuse
        try:
            import ctypes

            class _Util(ctypes.Structure):
                _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

            if self._nv_unix is None:
                _lib = "libnvidia-ml.so.1" if _OS == "Linux" else "libnvidia-ml.dylib"
                nv = ctypes.CDLL(_lib)
                nv.nvmlInit_v2()
                dev = ctypes.c_void_p()
                nv.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
                self._nv_unix = (nv, dev)

            nv, dev = self._nv_unix
            u = _Util()
            nv.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(u))
            return float(u.gpu)
        except Exception:
            pass

        return -1.0   # N/A — zero subprocess on all platforms

    def _get_temp(self) -> float:
        # psutil — works on Linux; occasionally Windows with driver support
        try:
            temps = psutil.sensors_temperatures()
            for name in ["coretemp", "k10temp", "cpu_thermal", "acpitz",
                         "cpu-thermal", "zenpower", "it8688"]:
                if name in temps and temps[name]:
                    return temps[name][0].current
            for entries in temps.values():
                if entries:
                    return entries[0].current
        except Exception:
            pass

        # Windows: wmi module (pure Python COM, zero subprocess). Reuse a single
        # connection — building a fresh wmi.WMI() on every poll spins up a COM
        # connection each time and is very slow. Give up after one failure.
        if _OS == "Windows" and self._wmi_ok is not False:
            try:
                if self._wmi_conn is None:
                    import wmi  # type: ignore
                    self._wmi_conn = wmi.WMI(namespace="root/wmi")
                tz = self._wmi_conn.MSAcpi_ThermalZoneTemperature()
                if tz:
                    return (tz[0].CurrentTemperature / 10.0) - 273.15
            except Exception:
                self._wmi_ok   = False
                self._wmi_conn = None

        return -1.0   # N/A — zero subprocess on all platforms

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cpu": self.cpu,
                "mem": self.mem,
                "net": self.net,
                "gpu": self.gpu,
                "tmp": self.tmp,
            }


_metrics = _SysMetrics()

class HudCanvas(QWidget):
    def __init__(self, face_path: str, assistant_name: str = "J.A.R.V.I.S", parent=None):
        super().__init__(parent)
        # (no WA_OpaquePaintEvent: the background is painted with feathered edges and lets the frame show through)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.muted    = False
        self.speaking = False
        self.state    = "INITIALISING"
        self._assistant_name = assistant_name
        # last-painted centre of the head/reactor, in this widget's own
        # coordinates — the anchor the image-window wires are drawn from.
        self._core_local_center = QPointF(self.width() / 2.0, self.height() / 2.0)

        # The holographic head that fills the HUD. If it could not be imported
        # we fall back to the old glowing core so the panel is never empty.
        self._avatar = None
        if HoloAvatar is not None:
            try:
                self._avatar = HoloAvatar()
            except Exception:
                self._avatar = None

        # Which centrepiece to draw. Read once here and changed live by the
        # settings toggle; the avatar object is kept either way so switching
        # back is instant and costs no reload.
        try:
            from memory.config_manager import get_hud_style
            self.hud_style = get_hud_style()
        except Exception:
            self.hud_style = "face"
        self._core_phase = 0.0

        self._tick       = 0
        self._scale      = 1.0
        self._tgt_scale  = 1.0
        self._halo       = 55.0
        self._tgt_halo   = 55.0
        self._last_t     = time.time()
        self._step_t     = time.time()
        self._blink      = True
        self._blink_tick = 0

        # Rescaled-face cache: the smooth rescale is expensive, so we keep the
        # last result and only rebuild it when the (quantised) size changes.

        # Static grid-dot layer, pre-rendered once per size/theme into a pixmap
        # so paintEvent blits it in one call instead of thousands of drawPoint()s.
        self._grid_cache: QPixmap | None = None
        self._grid_key = None
        # Repaint throttle counter (idle frames drop to ~20 Hz — see _step()).
        self._paint_tick = 0

        # Live audio reactivity: _live_amp is written from the audio threads
        # (0.0–1.0), _amp_disp is the smoothed value the paint code reads.
        self._live_amp  = 0.0
        self._amp_disp  = 0.0
        # (frames, start_time, hop) posted by the playback thread — see
        # push_visemes(). None means "no schedule; use the plain level".
        self._visemes = None
        self._vis_i = None        # first schedule frame not yet handed to the mouth
        self._base_scale = 1.0    # slow "breathing" target; amp is added per-frame
        self._base_halo  = 55.0

        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)

        # Stock/crypto radar: free panel (draggable, resizable, collapsible).
        # Created before the globe so that the expanded globe covers it.
        self._radar = None
        if MarketRadarWidget is not None:
            try:
                self._radar = MarketRadarWidget(parent=self, theme=C)
                self._radar.reposition(self.rect())
            except Exception as e:
                print(f"[Radar] Could not create the widget: {e}")
                self._radar = None

        # 3D globe (idle: top-right corner of this panel). Optional — needs
        # PyQt6-WebEngine; GlobeWidget itself hides and no-ops if that isn't
        # installed, so this never risks the rest of the HUD.
        self._globe = GlobeWidget(parent=self) if GlobeWidget is not None else None
        if self._globe is not None:
            self._globe.reposition(self.rect())
            print(f"[Globe] Widget created, available={self._globe._available}, "
                  f"initial rect={self.rect()}")
        else:
            print("[Globe] GlobeWidget class unavailable — see import error above, if any.")

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        """Ask the avatar to look somewhere for a moment (see HoloAvatar.glance)."""
        try:
            if self._avatar is not None:
                self._avatar.glance(dx, dy, hold)
        except Exception:
            pass

    def push_visemes(self, frames, hop: float, at: float) -> None:
        """Thread-safe: hand over a schedule of (level, openness, width) frames.

        The playback thread writes up to 200 ms of audio in one go, so a single
        averaged level would only move the mouth five times a second — enough to
        flap, nowhere near enough to articulate. It instead posts the whole
        slice's worth of 20 ms frames here and `_step()` plays them out against
        the wall clock, in step with the audio going to the speakers.

        `at` is the wall-clock time this batch will *begin to sound*, which the
        caller tracks as a playback cursor. It is not the time of the call, and
        the difference is the whole point: `stream.write` returns once the buffer
        accepts the samples, so consecutive batches are handed over far faster
        than they play. Anchoring each one to "now" made every batch start while
        its predecessor was still sounding, so each schedule replaced the last
        after a couple of frames and the mouth only ever played the opening
        instant of every 200 ms — the reason it did not match the words.

        Successive batches are therefore *appended* into one continuous
        timeline, not swapped in. A paragraph is one schedule; the mouth stops
        falling into a gap at every chunk boundary and having to climb back out.
        """
        try:
            if not frames:
                return
            hop = max(1e-3, float(hop))
            at = float(at)
            new = list(frames)
            cur = self._visemes
            if cur is not None:
                old, t0, ohop = cur
                if abs(ohop - hop) < 1e-6:
                    # Where in the existing timeline does this batch land?
                    i = int(round((at - t0) / hop))
                    if 0 <= i <= len(old) + 1:
                        # Continues (or slightly overlaps) what is already
                        # queued: extend rather than restart. Drop whatever has
                        # already been played so the list cannot grow without
                        # bound over a long reply.
                        merged = old[:i] + new
                        played = int((time.time() - t0) / hop) - 2
                        if played > 60:
                            merged = merged[played:]
                            t0 += played * hop
                            if self._vis_i is not None:
                                self._vis_i = max(0, self._vis_i - played)
                        self._visemes = (merged, t0, hop)
                        return
            self._visemes = (new, at, hop)
            self._vis_i = None
        except Exception:
            pass

    def set_audio_level(self, level: float) -> None:
        """Thread-safe entry point for the audio threads. Stores the louder of
        the incoming level and the current value so brief gaps between chunks
        don't make the waveform stutter; _step() decays it back down."""
        try:
            lv = float(level)
        except (TypeError, ValueError):
            return
        if lv < 0.0:
            lv = 0.0
        elif lv > 1.0:
            lv = 1.0
        if lv > self._live_amp:
            self._live_amp = lv

    def _make_grid(self, W: int, H: int) -> QPixmap:
        """Pre-render the static grid-dot background into a transparent pixmap so
        paintEvent can blit it once per frame instead of running a nested
        drawPoint() loop across the whole widget every 16 ms."""
        pm = QPixmap(max(1, W), max(1, H))
        pm.fill(Qt.GlobalColor.transparent)
        gp = QPainter(pm)
        gp.setPen(QPen(qcol(C.PRI_GHO), 1))
        for x in range(0, W, 48):
            for y in range(0, H, 48):
                gp.drawPoint(x, y)
        gp.end()
        return pm

    def _step(self):
        self._tick += 1
        now = time.time()

        # ── Live audio reactivity ────────────────────────────────────────────
        # A viseme schedule, if one is playing, gives both the level and the
        # mouth shape for this exact instant; otherwise fall back to the peak
        # level the audio threads pushed in.
        v_open = v_wide = v_level = None
        v_seq = None
        sched = self._visemes
        if sched is not None:
            frames, t0, hop = sched
            i = int((now - t0) / hop)
            if 0 <= i < len(frames):
                # Hand over *every* frame since the last tick, not just the one
                # under the cursor. This timer runs at 60 Hz but the paint is
                # throttled and the machine may be busy, so a tick can span two
                # or three 20 ms frames — and a consonant closure is only two
                # frames long. Sampling one and discarding the rest is how the
                # closures between words went missing.
                j = self._vis_i if self._vis_i is not None else i
                v_seq = frames[max(0, j):i + 1]
                self._vis_i = max(j, i + 1)
                v_level, v_open, v_wide = frames[i]
                if v_seq:
                    peak = max(f[0] for f in v_seq)
                    if peak > self._live_amp:
                        self._live_amp = peak
            elif i >= len(frames):
                self._visemes = None        # schedule spent
                self._vis_i = None

        # Audio threads push peaks into _live_amp; decay it toward silence so
        # gaps between chunks fade out instead of freezing, then smooth it.
        self._live_amp *= 0.86
        self._amp_disp += (self._live_amp - self._amp_disp) * 0.45
        amp = self._amp_disp

        # The avatar animates off the very same smoothed level the waveform
        # uses — one audio source, so the mouth can never drift out of sync.
        dt = now - self._step_t
        self._step_t = now
        # Integrated, not derived from absolute time: multiplying wall-clock by
        # a rate that changes with state jumps the rings the instant JARVIS
        # starts talking. Same lesson the head's sway taught.
        self._core_phase += min(0.10, max(0.0, dt))

        if self._avatar is not None and self.hud_style == "face":
            self._avatar.step(dt, amp, speaking=self.speaking,
                              muted=self.muted, state=self.state,
                              v_open=v_open, v_wide=v_wide or 0.0,
                              v_level=v_level, v_seq=v_seq,
                              v_hop=(sched[2] if sched is not None else 0.02))
        else:
            # Fallback core: slow "breathing" base target, lifted by the level.
            if now - self._last_t > (0.12 if self.speaking else 0.5):
                if self.speaking:
                    self._base_scale = 1.03
                    self._base_halo  = 122.0
                elif self.muted:
                    self._base_scale = random.uniform(0.998, 1.002)
                    self._base_halo  = random.uniform(15, 28)
                else:
                    self._base_scale = random.uniform(1.001, 1.008)
                    self._base_halo  = random.uniform(48, 68)
                self._last_t = now

            if self.muted:
                self._tgt_scale, self._tgt_halo = self._base_scale, self._base_halo
            elif self.speaking:
                self._tgt_scale = self._base_scale + amp * 0.13
                self._tgt_halo  = self._base_halo  + amp * 95.0
            else:
                self._tgt_scale = self._base_scale + amp * 0.06
                self._tgt_halo  = self._base_halo  + amp * 75.0

            sp = 0.38 if self.speaking else (0.30 if amp > 0.02 else 0.15)
            self._scale += (self._tgt_scale - self._scale) * sp
            self._halo  += (self._tgt_halo  - self._halo)  * sp

        self._blink_tick += 1
        if self._blink_tick >= 38:
            self._blink = not self._blink
            self._blink_tick = 0
            _blinked = True
        else:
            _blinked = False

        # Repaint throttling — advancing the animation state above is cheap at
        # 60 Hz, but the paint is heavy. Active (speaking, audio, thinking) runs
        # at ~30 Hz, which is the frame rate animation has used for talking
        # characters forever and is indistinguishable here; idle drops to ~20 Hz
        # so a sleeping HUD stops pinning a CPU core. The visuals stay smooth
        # either way because the animation state keeps stepping at 60 Hz.
        self._paint_tick = (self._paint_tick + 1) % 6
        active = (self.speaking or amp > 0.02
                  or self.state in ("THINKING", "PROCESSING"))
        if _blinked or (self._paint_tick % 2 == 0 if active
                        else self._paint_tick % 3 == 0):
            # Nothing is on screen when the window is hidden or minimised, so
            # rendering the avatar into it is pure waste — and this app is meant
            # to sit running all day. The animation state above keeps stepping,
            # so it picks up mid-motion instead of snapping when you come back.
            if self._on_screen():
                self.update()

    def _on_screen(self) -> bool:
        """True only when this canvas can actually be seen by the user."""
        try:
            if not self.isVisible():
                return False
            win = self.window()
            return not (win.isMinimized() or win.isHidden())
        except Exception:
            return True      # never let a visibility check stop the HUD drawing

    # ── reactor core ─────────────────────────────────────────────────────────
    # The centrepiece for anyone who did not want a face looking back at them.
    # Built from the same budget as the head — software QPainter, no GPU — and
    # from the same principle: everything on it means something. The rings turn
    # at a rate the state sets, the spectrum ring is the real audio level, and
    # the core brightens with the voice. Nothing here is decoration that moves
    # for its own sake, which is what made the old glowing orb feel dead.

    def _core_colours(self):
        if self.muted:
            return qcol(C.MUTED_C), qcol(C.MUTED_C)
        if self.speaking:
            return qcol(C.PRI), qcol(C.ACC)
        if self.state in ("THINKING", "PROCESSING"):
            return qcol(C.PRI), qcol(C.ACC2)
        if self.state == "LISTENING":
            return qcol(C.PRI), qcol(C.GREEN)
        return qcol(C.PRI), qcol(C.PRI_DIM)

    def _paint_core(self, p: QPainter, cx: float, cy: float, r: float,
                    W: float = 0.0, H: float = 0.0):
        """Draw the reactor at (cx, cy) with outer radius r, using the whole
        canvas (W x H) for the marks that frame it."""
        main, acc = self._core_colours()
        bg = qcol(C.BG)
        amp = self._amp_disp
        t = self._core_phase
        live = (self.speaking or amp > 0.04) and not self.muted

        def blend(col: QColor, a: float) -> QColor:
            """Pre-mix onto the background instead of asking Qt to composite.
            The raster engine's opaque path is several times faster than its
            translucent one, and everything here is a line or an arc."""
            k = max(0.0, min(1.0, a))
            return QColor(int(bg.red()   + (col.red()   - bg.red())   * k),
                          int(bg.green() + (col.green() - bg.green()) * k),
                          int(bg.blue()  + (col.blue()  - bg.blue())  * k))

        p.setBrush(Qt.BrushStyle.NoBrush)

        # 1. The atmosphere. One radial gradient doing what a stack of discs did
        #    badly: a wide, soft body of light that gives the thing presence
        #    before any detail is read. This single element decides whether the
        #    HUD looks vast or looks small, so it is drawn first and drawn big.
        # Concentrated rather than spread: a gradient reaching the outer rim
        # washes the whole disc a flat dim blue and reads as fog. Ending it at
        # two thirds leaves it a body of light with somewhere to fall off to,
        # which is what makes it look lit rather than tinted.
        lift = 1.0 + 0.55 * amp + (0.18 if self.speaking else 0.0)
        p.setPen(Qt.PenStyle.NoPen)
        for gr, a0, a1 in ((r * 0.70, 0.30, 0.0), (r * 0.34, 0.34, 0.0)):
            g = QRadialGradient(cx, cy, gr)
            g.setColorAt(0.00, blend(main, min(0.95, a0 * lift)))
            g.setColorAt(0.45, blend(main, min(0.95, a0 * lift * 0.52)))
            g.setColorAt(0.78, blend(main, min(0.95, a0 * lift * 0.18)))
            g.setColorAt(1.00, blend(main, a1))
            p.setBrush(QBrush(g))
            p.drawEllipse(QRectF(cx - gr, cy - gr, gr * 2, gr * 2))
        p.setBrush(Qt.BrushStyle.NoBrush)

        # 2. Frame marks at the corners of the whole canvas, not of the circle.
        #    They are what set the scale: the eye reads the reactor as filling
        #    the room rather than sitting in the middle of it.
        if W > 40 and H > 40:
            m, arm = min(W, H) * 0.035, min(W, H) * 0.055
            p.setPen(QPen(blend(main, 0.45), 1.4))
            for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
                x = cx + sx * (W / 2 - m)
                y = cy + sy * (H / 2 - m)
                p.drawLine(QLineF(x, y, x - sx * arm, y))
                p.drawLine(QLineF(x, y, x, y - sy * arm))

        # 3. Crosshair across the full canvas, broken around the core so it
        #    frames the reactor rather than crossing it.
        p.setPen(QPen(blend(main, 0.16), 1))
        gap = r * 0.62
        if W > 40:
            p.drawLine(QLineF(cx - W / 2, cy, cx - gap, cy))
            p.drawLine(QLineF(cx + gap, cy, cx + W / 2, cy))
        if H > 40:
            p.drawLine(QLineF(cx, cy - H / 2, cx, cy - gap))
            p.drawLine(QLineF(cx, cy + gap, cx, cy + H / 2))

        # 4. Two thin outer circles. Sparse on purpose — a dense ring reads as a
        #    grey band at this size, and restraint is what made the original
        #    look expensive.
        for rr, a in ((1.00, 0.34), (0.93, 0.16)):
            rad = r * rr
            p.setPen(QPen(blend(main, a), 1))
            p.drawEllipse(QRectF(cx - rad, cy - rad, rad * 2, rad * 2))

        # 5. Long, sparse graduations: 24 majors reaching well in from the rim,
        #    with shorter minors between them.
        major, minor = [], []
        for i in range(72):
            a = math.radians(i * 5.0)
            ca, sa = math.cos(a), math.sin(a)
            if i % 3 == 0:
                major.append(QLineF(cx + ca * r * 0.885, cy + sa * r * 0.885,
                                    cx + ca * r * 0.985, cy + sa * r * 0.985))
            else:
                minor.append(QLineF(cx + ca * r * 0.945, cy + sa * r * 0.945,
                                    cx + ca * r * 0.985, cy + sa * r * 0.985))
        p.setPen(QPen(blend(main, 0.42), 1.3))
        p.drawLines(major)
        p.setPen(QPen(blend(main, 0.18), 1))
        p.drawLines(minor)

        # 6. Sweeping arcs. Long spans, not dashes — the original's grandeur
        #    came from a few big strokes. Speed is the state: idle drifts,
        #    thinking hurries, speaking runs.
        rate = 1.0 + (1.9 if self.state in ("THINKING", "PROCESSING") else 0.0) \
                   + (1.2 if self.speaking else 0.0)
        for k, (rr, span, count, dirn, col, a, wid) in enumerate((
                (0.955, 118, 2, +1, acc,  0.75, 2.0),
                (0.845, 82,  3, -1, main, 0.38, 1.3),
                (0.760, 150, 1, +1, acc,  0.45, 1.6),
                (0.660, 64,  4, -1, main, 0.26, 1.1),
                (0.545, 128, 2, +1, main, 0.30, 1.2))):
            rad = r * rr
            p.setPen(QPen(blend(col, a), wid))
            box = QRectF(cx - rad, cy - rad, rad * 2, rad * 2)
            base = (t * rate * (9 + k * 6) * dirn) % 360.0
            for sgm in range(count):
                p.drawArc(box, int((base + sgm * (360.0 / count)) * 16),
                          int(span * 16))

        # 7. The voice, as a ring of graduations that grow with it. Kept out at
        #    a wide radius so it never crowds the middle.
        n = 60
        ring = r * 0.415
        spikes = []
        for i in range(n):
            a = math.radians(i * (360.0 / n))
            ca, sa = math.cos(a), math.sin(a)
            wob = 0.5 + 0.5 * math.sin(t * 2.3 + i * 0.42)
            idle = 0.018 + 0.012 * math.sin(t * 1.2 + i * 0.7)
            h = r * (idle + (amp * 0.20 * wob if live else 0.0))
            spikes.append(QLineF(cx + ca * ring, cy + sa * ring,
                                 cx + ca * (ring + h), cy + sa * (ring + h)))
        p.setPen(QPen(blend(acc if live else main, 0.25 + 0.5 * amp), 1.6))
        p.drawLines(spikes)

        # 8. The inner ring the name sits in.
        inner = r * 0.355
        p.setPen(QPen(blend(acc, 0.30 + 0.45 * amp), 1.5))
        p.drawEllipse(QRectF(cx - inner, cy - inner, inner * 2, inner * 2))

        # 9. The name, sized from the string rather than from the radius alone:
        #    "J.A.R.V.I.S" and a name someone renamed to "MAX" are very
        #    different widths, and a fixed fraction of r spills one of them past
        #    the ring it is supposed to sit inside.
        name = self._assistant_name or ""
        if name:
            space = max(1.0, r * 0.018)
            fsz = max(8, int(min(r * 0.105,
                                 (inner * 1.75) / max(1, len(name)) * 1.6 - space)))
            f = QFont("Courier New", fsz, QFont.Weight.Bold)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, space)
            p.setFont(f)
            p.setPen(QPen(blend(qcol(C.WHITE), 0.6 + 0.4 * min(1.0, amp * 2)), 1))
            p.drawText(QRectF(cx - r, cy - fsz, r * 2, fsz * 2),
                       Qt.AlignmentFlag.AlignCenter, name)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "_globe", None) is not None:
            self._globe.reposition(self.rect())
        if getattr(self, "_radar", None) is not None:
            self._radar.reposition(self.rect())

    def core_anchor(self) -> QPointF:
        """Centre of the head/reactor core, in this widget's own coordinates —
        used by `_ImageWireLayer` to draw the connector to floating images."""
        return self._core_local_center

    def _make_feathered_bg(self, W: int, H: int):
        """Dark panel fill whose alpha fades to 0 over the outer edge (no visible rectangle)."""
        from PyQt6.QtGui import QPixmap, QLinearGradient
        pm = QPixmap(max(1, W), max(1, H))
        pm.fill(Qt.GlobalColor.transparent)
        q = QPainter(pm)
        q.fillRect(pm.rect(), qcol(C.BG, 165))
        q.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        f = 0.045
        for horiz in (True, False):
            g = QLinearGradient(0, 0, W if horiz else 0, 0 if horiz else H)
            g.setColorAt(0.0, QColor(0, 0, 0, 0)); g.setColorAt(f, QColor(0, 0, 0, 255))
            g.setColorAt(1.0 - f, QColor(0, 0, 0, 255)); g.setColorAt(1.0, QColor(0, 0, 0, 0))
            q.fillRect(pm.rect(), g)
        q.end()
        return pm

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():      # device not ready (e.g. 0-size during layout) — skip cleanly
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # translucent, feathered edges: no hard black rectangle against the panel frame
        _bgk = (self.width(), self.height(), str(C.BG))
        if getattr(self, "_bg_key", None) != _bgk:
            self._bg_key = _bgk
            self._bg_cache = self._make_feathered_bg(self.width(), self.height())
        p.drawPixmap(0, 0, self._bg_cache)

        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        fw = min(W, H)

        # grid dots — blitted from a cached layer; rebuilt only when the size
        # or the theme's ghost colour changes (so live re-theming still works).
        _gkey = (W, H, C.PRI_GHO)
        if self._grid_cache is None or self._grid_key != _gkey:
            self._grid_cache = self._make_grid(W, H)
            self._grid_key   = _gkey
        p.drawPixmap(0, 0, self._grid_cache)

        # ── holographic head ────────────────────────────────────────────────
        # Sized to the band between the top of the canvas and the status line,
        # capped by width, so it fills the HUD at any window size — including
        # fullscreen — without ever colliding with the status text below.
        _sy_status = cy + fw * 0.40
        if self._avatar is not None and self.hud_style == "face":
            _band_t = 12.0
            _band_h = max(60.0, _sy_status - 12.0 - _band_t)
            _r_head = min(fw * 0.355, _band_h / (self._avatar.SPAN + 0.08))
            _head_cy = _band_t + (_band_h - self._avatar.SPAN * _r_head) / 2.0 + _r_head

            if self.muted:
                _main = _acc = qcol(C.MUTED_C)
            else:
                _main = qcol(C.PRI)
                if self.speaking:
                    _acc = qcol(C.ACC)
                elif self.state in ("THINKING", "PROCESSING"):
                    _acc = qcol(C.ACC2)
                elif self.state == "LISTENING":
                    _acc = qcol(C.GREEN)
                else:
                    _acc = qcol(C.PRI)
            self._avatar.paint(p, cx, _head_cy, _r_head, _main, _acc, qcol(C.BG))
            self._core_local_center = QPointF(cx, _head_cy)

        # reactor core — the other centrepiece, and the fallback if the head
        # could not be built. There is no third path: the old face.png branch
        # was unreachable (no such file ships) and the bare orb it fell through
        # to is what this replaces.
        else:
            _band_t = 12.0
            _band_h = max(60.0, _sy_status - 12.0 - _band_t)
            _r = min(W * 0.46, _band_h / 2.0)
            self._paint_core(p, cx, _band_t + _band_h / 2.0, _r, W, _band_h)
            self._core_local_center = QPointF(cx, _band_t + _band_h / 2.0)

        # status text
        sy = _sy_status
        if self.muted:
            txt, col = "⊘  MUTED",     qcol(C.MUTED_C)
        elif self.speaking:
            txt, col = "●  SPEAKING",  qcol(C.ACC)
        elif self.state == "THINKING":
            sym = "◈" if self._blink else "◇"
            txt, col = f"{sym}  THINKING",   qcol(C.ACC2)
        elif self.state == "PROCESSING":
            sym = "▷" if self._blink else "▶"
            txt, col = f"{sym}  PROCESSING", qcol(C.ACC2)
        elif self.state == "LISTENING":
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  LISTENING",  qcol(C.GREEN)
        else:
            sym = "●" if self._blink else "○"
            txt, col = f"{sym}  {self.state}", qcol(C.PRI)

        p.setPen(QPen(col, 1))
        p.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        p.drawText(QRectF(0, sy, W, 26), Qt.AlignmentFlag.AlignCenter, txt)

        # waveform — reacts to the real audio level (mic while listening,
        # JARVIS's own voice while speaking). Falls back to a gentle idle
        # ripple when there's no sound. _amp_disp is the smoothed 0–1 level.
        wy = sy + 30
        N, bw = 36, 8
        wx0 = (W - N * bw) / 2
        amp = self._amp_disp
        mid = (N - 1) / 2.0
        for i in range(N):
            if self.muted:
                hgt, cl = 2, qcol(C.MUTED_C)
            else:
                env     = (1.0 - abs(i - mid) / mid) ** 0.7      # center-weighted hump
                shimmer = 0.55 + 0.45 * math.sin(self._tick * 0.18 + i * 0.7)
                idle    = 3.0 + 2.0 * math.sin(self._tick * 0.09 + i * 0.6)
                hgt     = int(max(2, min(24, idle + amp * 22.0 * env * shimmer)))
                if amp > 0.05:
                    cl = qcol(C.PRI) if hgt > 12 else qcol(C.PRI_DIM)
                else:
                    cl = qcol(C.BORDER_B)
            p.fillRect(QRectF(wx0 + i * bw, wy + 20 - hgt, bw - 1, hgt), cl)

        p.end()   # end deterministically so the backing store never flushes an active painter

class MetricBar(QWidget):

    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label = label
        self._color = color
        self._value = 0.0       # 0–100
        self._text  = "--"
        self.setFixedHeight(38)
        self.setMinimumWidth(80)

    def set_value(self, pct: float, text: str):
        v = max(0.0, min(100.0, pct))
        if v == self._value and text == self._text:
            return          # unchanged — skip the repaint
        self._value = v
        self._text  = text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()

        body = _chamfer_path(QRectF(1.5, 1.5, W - 3, H - 3), (8, 0, 8, 0))
        p.fillPath(body, QBrush(qcol(C.PRI, 14)))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qcol(C.PRI_DIM, 170), 1))
        p.drawPath(body)

        if self._value > 85:
            bar_col = qcol(C.RED)
        elif self._value > 65:
            bar_col = qcol(C.ACC)
        else:
            bar_col = qcol(self._color)

        # segmented LED bar
        bar_h, bar_x = 5.0, 12.0
        bar_y, bar_w = H - bar_h - 6.0, W - 24.0
        seg_n = max(8, int(bar_w // 7))
        gap   = 2.0
        seg_w = (bar_w - gap * (seg_n - 1)) / seg_n
        lit   = int(round(seg_n * self._value / 100.0))
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(seg_n):
            sx = bar_x + i * (seg_w + gap)
            if i < lit:
                bloom = QColor(bar_col); bloom.setAlpha(55)
                p.setBrush(QBrush(bloom))
                p.drawRect(QRectF(sx - 1, bar_y - 1, seg_w + 2, bar_h + 2))   # soft bloom
                p.setBrush(QBrush(bar_col))
            else:
                p.setBrush(QBrush(qcol(C.PRI, 24)))
            p.drawRect(QRectF(sx, bar_y, seg_w, bar_h))

        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(12, 5, 50, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(bar_col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 4, W - 12, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)

        p.end()

class LogWidget(QTextEdit):
    _sig = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        # Cap scrollback so an hours-long session can't grow the document
        # without bound — keeps memory flat and every insert cheap. Oldest
        # lines drop off the top automatically.
        self.document().setMaximumBlockCount(600)
        self.setFont(QFont("Courier New", 9))
        self.setStyleSheet(f"""
            QTextEdit {{
                background: transparent;
                color: {C.TEXT};
                border: none;
                border-left: 2px solid {C.PRI_DIM};
                padding: 4px 6px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 6px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.PRI_DIM};
                border-radius: 0px;
                min-height: 20px;
            }}
        """)
        self._queue: list[str] = []
        self._typing  = False
        self._text    = ""
        self._pos     = 0
        self._tag     = "sys"
        self._ai_name_lc = "jarvis"   # updated when assistant name changes
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._sig.connect(self._enqueue)

    def append_log(self, text: str):
        self._sig.emit(text)

    def _enqueue(self, text: str):
        self._queue.append(text)
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text   = self._queue.pop(0)
        self._pos    = 0
        tl = self._text.lower()
        _ai_pfx = f"{self._ai_name_lc}:"
        if   tl.startswith("you:"):                              self._tag = "you"
        elif tl.startswith(_ai_pfx) or tl.startswith("jarvis:"): self._tag = "ai"
        elif tl.startswith("file:"):                             self._tag = "file"
        elif "err" in tl:                                        self._tag = "err"
        else:                                                    self._tag = "sys"
        self._tmr.start(6)

    def _step(self):
        if self._pos < len(self._text):
            ch  = self._text[self._pos]
            cur = self.textCursor()
            fmt = cur.charFormat()
            col = {
                "you":  qcol(C.WHITE),
                "ai":   qcol(C.PRI),
                "err":  qcol(C.RED),
                "file": qcol(C.GREEN),
                # SYS lines are the bulk of the log. Amber fought the cyan HUD
                # and, being a fixed status colour rather than a hue-linked one,
                # stayed amber even after the accent picker retinted everything
                # else. TEXT_MED follows the theme and drops the contrast to a
                # level you can read past.
                "sys":  qcol(C.TEXT_MED),
            }.get(self._tag, qcol(C.TEXT))
            fmt.setForeground(QBrush(col))
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText(ch, fmt)
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            self._pos += 1
        else:
            self._tmr.stop()
            cur = self.textCursor()
            cur.movePosition(cur.MoveOperation.End)
            cur.insertText("\n")
            self.setTextCursor(cur)
            self.ensureCursorVisible()
            QTimer.singleShot(20, self._next)

_FILE_ICONS = {
    "image":   ("🖼", "#00d4ff"), "video":   ("🎬", "#ff6b00"),
    "audio":   ("🎵", "#cc44ff"), "pdf":     ("📄", "#ff4444"),
    "word":    ("📝", "#4488ff"), "excel":   ("📊", "#44bb44"),
    "code":    ("💻", "#ffcc00"), "archive": ("📦", "#ff8844"),
    "pptx":    ("📊", "#ff6622"), "text":    ("📃", "#aaaaaa"),
    "data":    ("🔧", "#88ddff"), "unknown": ("📎", "#888888"),
}
_EXT_TO_CAT = {
    **dict.fromkeys(["jpg","jpeg","png","gif","webp","bmp","tiff","svg","ico"], "image"),
    **dict.fromkeys(["mp4","avi","mov","mkv","wmv","flv","webm","m4v"],         "video"),
    **dict.fromkeys(["mp3","wav","ogg","m4a","aac","flac","wma","opus"],        "audio"),
    **dict.fromkeys(["pdf"],                                                     "pdf"),
    **dict.fromkeys(["doc","docx"],                                              "word"),
    **dict.fromkeys(["xls","xlsx","ods"],                                        "excel"),
    **dict.fromkeys(["ppt","pptx"],                                              "pptx"),
    **dict.fromkeys(["py","js","ts","jsx","tsx","html","css","java","c","cpp",
                     "cs","go","rs","rb","php","swift","kt","sh","sql","lua"],   "code"),
    **dict.fromkeys(["zip","rar","tar","gz","7z","bz2","xz"],                   "archive"),
    **dict.fromkeys(["txt","md","rst","log"],                                    "text"),
    **dict.fromkeys(["csv","tsv","json","xml"],                                  "data"),
}

def _file_category(path: Path) -> str:
    return _EXT_TO_CAT.get(path.suffix.lower().lstrip("."), "unknown")

def _fmt_size(size: int) -> str:
    if   size < 1024:    return f"{size} B"
    elif size < 1024**2: return f"{size/1024:.1f} KB"
    elif size < 1024**3: return f"{size/1024**2:.1f} MB"
    else:                return f"{size/1024**3:.1f} GB"


class FileDropZone(QWidget):
    file_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self._current_file: str | None = None
        self._hovering  = False
        self._drag_over = False
        self._dash_offset = 0.0
        self._anim_tmr = QTimer(self)
        self._anim_tmr.timeout.connect(self._animate)
        self._anim_tmr.start(40)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._canvas = _DropCanvas(self)
        layout.addWidget(self._canvas)

    def _animate(self):
        # The marching-ants dashed border is only meaningful while the user is
        # hovering or dragging a file over the zone. When idle, skip the repaint
        # entirely instead of redrawing the whole zone 25×/s forever — that idle
        # repaint held the GIL and stole time from the audio/response threads.
        if not (self._hovering or self._drag_over):
            return
        self._dash_offset = (self._dash_offset + 0.8) % 20
        self._canvas.update()

    def dragEnterEvent(self, e: QDragEnterEvent):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._drag_over = True; self._canvas.update()

    def dragLeaveEvent(self, e):
        self._drag_over = False; self._canvas.update()

    def dropEvent(self, e: QDropEvent):
        self._drag_over = False
        urls = e.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if Path(path).is_file():
                self._set_file(path)
        self._canvas.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def enterEvent(self, e):
        self._hovering = True; self._canvas.update()

    def leaveEvent(self, e):
        self._hovering = False; self._canvas.update()

    def current_file(self) -> str | None:
        return self._current_file

    def clear_file(self):
        self._current_file = None; self._canvas.update()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select a file for JARVIS", str(Path.home()),
            "All Files (*.*);;"
            "Images (*.jpg *.jpeg *.png *.gif *.webp *.bmp *.svg);;"
            "Documents (*.pdf *.docx *.txt *.md *.pptx);;"
            "Data (*.csv *.xlsx *.json *.xml);;"
            "Code (*.py *.js *.ts *.html *.css *.java *.cpp *.go);;"
            "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac);;"
            "Video (*.mp4 *.avi *.mov *.mkv *.wmv *.webm);;"
            "Archives (*.zip *.rar *.tar *.gz *.7z)",
        )
        if path:
            self._set_file(path)

    def _set_file(self, path: str):
        self._current_file = path
        self._canvas.update()
        self.file_selected.emit(path)


class _DropCanvas(QWidget):
    def __init__(self, zone: FileDropZone):
        super().__init__(zone)
        self._z = zone

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        z    = self._z
        W, H = self.width(), self.height()
        pad  = 6
        rect = QRectF(pad, pad, W - pad * 2, H - pad * 2)

        bg_col = qcol("#001a24" if z._drag_over else ("#001218" if z._hovering else C.PANEL), 150)
        shape = _chamfer_path(rect, (10, 0, 10, 0))
        p.fillPath(shape, QBrush(bg_col))

        if z._current_file:   border_col = qcol(C.GREEN, 200)
        elif z._drag_over:    border_col = qcol(C.PRI, 230)
        elif z._hovering:     border_col = qcol(C.BORDER_B, 200)
        else:                 border_col = qcol(C.PRI_DIM, 170)

        pen = QPen(border_col, 1.5, Qt.PenStyle.DashLine)
        pen.setDashOffset(z._dash_offset)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(shape)

        if z._current_file:   self._paint_file(p, W, H)
        elif z._drag_over:    self._paint_drag_over(p, W, H)
        else:                 self._paint_idle(p, W, H, z._hovering)

        p.end()

    def _paint_idle(self, p, W, H, hover):
        cx, cy = W / 2, H / 2
        col = qcol(C.PRI_DIM if not hover else C.PRI)
        p.setPen(QPen(col, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(QPointF(cx, cy - 14), QPointF(cx, cy + 4))
        p.drawLine(QPointF(cx - 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx + 8, cy - 6), QPointF(cx, cy - 14))
        p.drawLine(QPointF(cx - 14, cy + 4), QPointF(cx + 14, cy + 4))
        p.setFont(QFont("Courier New", 8))
        p.setPen(QPen(qcol(C.PRI_DIM if not hover else C.TEXT), 1))
        p.drawText(QRectF(0, cy + 8, W, 16), Qt.AlignmentFlag.AlignCenter,
                   "Drop file here  or  Click to Browse")
        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol("#1a4a5a"), 1))
        p.drawText(QRectF(0, cy + 24, W, 14), Qt.AlignmentFlag.AlignCenter,
                   "Images · Video · Audio · PDF · Docs · Code · Data")

    def _paint_drag_over(self, p, W, H):
        cx, cy = W / 2, H / 2
        p.setFont(QFont("Courier New", 20))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy - 24, W, 32), Qt.AlignmentFlag.AlignCenter, "⬇")
        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, cy + 12, W, 16), Qt.AlignmentFlag.AlignCenter, "Release to load")

    def _paint_file(self, p, W, H):
        path = Path(self._z._current_file)
        cat  = _file_category(path)
        icon, icon_col = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size_str = _fmt_size(path.stat().st_size)
        ext_str  = path.suffix.upper().lstrip(".") or "FILE"

        block_x, block_w = 10, 60
        p.setFont(QFont("Segoe UI Emoji", 22) if _OS == "Windows" else QFont("Arial", 22))
        p.setPen(QPen(qcol(icon_col), 1))
        p.drawText(QRectF(block_x, 0, block_w, H), Qt.AlignmentFlag.AlignCenter, icon)

        tx = block_x + block_w + 6
        tw = W - tx - 38

        p.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.WHITE), 1))
        name = path.name if len(path.name) <= 34 else path.name[:31] + "..."
        p.drawText(QRectF(tx, H * 0.18, tw, 16),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)

        p.setFont(QFont("Courier New", 7))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(tx, H * 0.18 + 18, tw, 14),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f"{ext_str}  ·  {size_str}")

        p.setFont(QFont("Courier New", 6))
        p.setPen(QPen(qcol("#1e5c6a"), 1))
        par = str(path.parent)
        if len(par) > 42: par = "…" + par[-41:]
        p.drawText(QRectF(tx, H * 0.18 + 34, tw, 12),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, par)

        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.RED, 180), 1))
        p.drawText(QRectF(W - 34, 0, 28, H), Qt.AlignmentFlag.AlignCenter, "✕")

    def mousePressEvent(self, e):
        z = self._z
        if z._current_file and e.pos().x() > self.width() - 34:
            z.clear_file()
        else:
            z.mousePressEvent(e)


class _CameraPreview(QWidget):
    """Floating overlay that briefly shows what the camera captured."""

    _W, _H = 244, 188

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            _CameraPreview {{
                background: rgba(0, 6, 10, 242);
                border: 1px solid {C.PRI};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._W)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 5, 6, 6)
        lay.setSpacing(4)

        hdr = QHBoxLayout()
        title = QLabel("◈  VISUAL INPUT")
        title.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setFixedSize(16, 16)
        close_btn.setFont(QFont("Courier New", 8))
        close_btn.setStyleSheet(
            f"color: {C.TEXT_DIM}; background: transparent; border: none;"
        )
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.hide)
        hdr.addWidget(close_btn)
        lay.addLayout(hdr)

        self._img_lbl = QLabel()
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setStyleSheet("background: transparent;")
        lay.addWidget(self._img_lbl)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

        self.hide()

    def show_frame(self, img_bytes: bytes) -> None:
        px = QPixmap()
        px.loadFromData(img_bytes)
        if not px.isNull():
            max_w = self._W - 12
            scaled = px.scaled(
                max_w, 160,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._img_lbl.setPixmap(scaled)
            self._img_lbl.setFixedSize(scaled.width(), scaled.height())
            self.adjustSize()
        self.show()
        self.raise_()
        self._timer.start(6_000)   # auto-dismiss after 6 s


class _NeonFlash(QWidget):
    """The subtle neon glow that sweeps the window open/closed — a soft
    vertical bloom around a thin bright core, not a flat white bar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), max(1, self.height())
        cy = h / 2.0

        glow = QLinearGradient(0, 0, 0, h)
        pri = qcol(C.PRI)
        transparent = QColor(pri.red(), pri.green(), pri.blue(), 0)
        soft = QColor(pri.red(), pri.green(), pri.blue(), 90)
        glow.setColorAt(0.00, transparent)
        glow.setColorAt(0.40, transparent)
        glow.setColorAt(0.50, soft)
        glow.setColorAt(0.60, transparent)
        glow.setColorAt(1.00, transparent)
        p.fillRect(self.rect(), QBrush(glow))

        core = QPen(QColor(235, 250, 255, 235), 1.4)
        p.setPen(core)
        p.drawLine(0, int(cy), w, int(cy))


class _CrtRevealWindow(QWidget):
    """Shared base for every floating HUD window that opens/closes with the
    CRT power on/off reveal: draggable by its header, closed manually (✕),
    never times out on its own, and exposes the anchor point `_ImageWireLayer`
    draws its connector wire to. `FloatingImageWindow` and
    `FloatingVideoWindow` both build on this — only their content differs.
    """

    closed = pyqtSignal(object)     # emits self, once the close animation finishes
    moved  = pyqtSignal()

    _WIDTH = 260

    def __init__(self, parent=None):
        super().__init__(parent)
        self._reveal_h = 0
        self._full_h = 1
        self._drag_offset: QPointF | None = None
        self._closing = False

        self.setFixedWidth(self._WIDTH)

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(1, 1, 1, 1)
        self._outer.setSpacing(0)
        self._head: QWidget | None = None
        self._title_lbl: QLabel | None = None

    def _build_header(self, title: str, extra_buttons=()) -> QWidget:
        """`extra_buttons` is a list of (glyph, tooltip, callback) shown
        before the close button — e.g. download for images, open-in-browser
        for videos."""
        head = QWidget()
        head.setCursor(Qt.CursorShape.OpenHandCursor)
        head.setStyleSheet(f"background: transparent; border-bottom: 1px solid {C.BORDER};")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(8, 5, 6, 5)
        hl.setSpacing(6)

        self._title_lbl = QLabel(title.upper())
        self._title_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._title_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hl.addWidget(self._title_lbl, stretch=1)

        for glyph, tooltip, callback in extra_buttons:
            btn = QPushButton(glyph)
            btn.setFixedSize(20, 20)
            btn.setFont(QFont("Courier New", 9))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(tooltip)
            btn.setStyleSheet(f"""
                QPushButton {{ color: {C.PRI}; background: transparent;
                    border: 1px solid {C.PRI_DIM}; border-radius: 2px; }}
                QPushButton:hover {{ background: {C.PRI_GHO}; }}
            """)
            btn.clicked.connect(callback)
            hl.addWidget(btn)

        x_btn = QPushButton("✕")
        x_btn.setFixedSize(20, 20)
        x_btn.setFont(QFont("Courier New", 9))
        x_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        x_btn.setToolTip("Close")
        x_btn.setStyleSheet(f"""
            QPushButton {{ color: {C.TEXT_DIM}; background: transparent;
                border: 1px solid {C.BORDER}; border-radius: 2px; }}
            QPushButton:hover {{ color: {C.RED}; border-color: {C.RED}; }}
        """)
        x_btn.clicked.connect(self.request_close)
        hl.addWidget(x_btn)

        self._head = head
        return head

    def _finalize(self) -> None:
        """Call once all content has been added to `self._outer`."""
        self.adjustSize()
        self._full_h = self.height()   # whole window — header + content + footer
        self._flash = _NeonFlash(self)
        self._flash.hide()
        self._apply_reveal_mask()       # start fully collapsed

    # -- CRT reveal mask ------------------------------------------------------
    def _get_reveal_h(self) -> int:
        return self._reveal_h

    def _set_reveal_h(self, value: int) -> None:
        self._reveal_h = max(0, min(int(value), self._full_h))
        self._apply_reveal_mask()

    revealHeight = pyqtProperty(int, _get_reveal_h, _set_reveal_h)

    _CUT = 10   # chamfer size of the floating window outline (px)

    def _chamfer_region(self) -> QRegion:
        w, h, c = self.width(), self._full_h, self._CUT
        return QRegion(QPolygon([
            QPoint(c, 0), QPoint(w, 0), QPoint(w, h - c),
            QPoint(w - c, h), QPoint(0, h), QPoint(0, c),
        ]))

    def paintEvent(self, _event):
        p = QPainter(self)
        if not p.isActive():
            return
        # dark base so the glass reads over the busy HUD, then the bevelled frame
        base = _chamfer_path(QRectF(1, 1, self.width() - 2, self.height() - 2),
                             (self._CUT, 0, self._CUT, 0))
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillPath(base, QBrush(qcol(C.DARK, 238)))
        _paint_hud_frame(p, self.width(), self.height(), (self._CUT, 0, self._CUT, 0),
                         glow=False, tint=0.16, inset=1.2)
        p.end()

    def _apply_reveal_mask(self) -> None:
        h = self._reveal_h
        top = (self._full_h - h) // 2
        region = QRegion(0, top, self.width(), max(0, h))
        self.setMask(region.intersected(self._chamfer_region()))
        # glow sits right at the growing/shrinking edge, centred on the WHOLE
        # window, tall enough for its gradient to fade out softly.
        flash_h = max(24, min(64, self._full_h))
        self._flash.setGeometry(0, self._full_h // 2 - flash_h // 2, self.width(), flash_h)

    def open_animate(self) -> None:
        self._flash.show()
        anim = QPropertyAnimation(self, b"revealHeight", self)
        anim.setStartValue(0)
        anim.setEndValue(self._full_h)
        anim.setDuration(420)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(self._flash.hide)
        anim.finished.connect(lambda: setattr(self, "_open_anim", None))
        self._open_anim = anim   # keep a reference alive
        anim.start()

    def request_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._flash.show()
        anim = QPropertyAnimation(self, b"revealHeight", self)
        anim.setStartValue(self._reveal_h)
        anim.setEndValue(0)
        anim.setDuration(320)
        anim.setEasingCurve(QEasingCurve.Type.InCubic)
        anim.finished.connect(lambda: self.closed.emit(self))
        self._close_anim = anim
        anim.start()

    # -- dragging ---------------------------------------------------------------
    def mousePressEvent(self, e):
        if self._head is not None and (
            self._head.geometry().contains(e.pos()) or e.pos().y() <= self._head.height()
        ):
            self._drag_offset = e.position()
            self._head.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_offset is not None:
            parent = self.parentWidget()
            new_pos = self.mapToParent(e.position().toPoint()) - self._drag_offset.toPoint()
            if parent is not None:
                new_pos.setX(max(0, min(parent.width()  - self.width(),  new_pos.x())))
                new_pos.setY(max(0, min(parent.height() - self.height(), new_pos.y())))
            self.move(new_pos)
            self.moved.emit()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_offset = None
        if self._head is not None:
            self._head.setCursor(Qt.CursorShape.OpenHandCursor)
        super().mouseReleaseEvent(e)

    def anchor_point(self) -> QPointF:
        """Where the connector wire should touch this window — its top edge, centred."""
        return QPointF(self.x() + self.width() / 2.0, self.y())


class FloatingImageWindow(_CrtRevealWindow):
    """A single image, floating independently over the HUD — see
    `_CrtRevealWindow` for the open/close/drag/wire behaviour they all share.
    Adds a ⬇ button to save the picture to disk.
    """

    def __init__(self, source, title: str = "IMAGE", parent=None):
        super().__init__(parent)
        self._pixmap = self._load_pixmap(source)

        self._outer.addWidget(self._build_header(title, extra_buttons=[
            ("⬇", "Save image", self._download),
        ]))

        # ── screen: the image itself ────────────────────────────────────
        self._screen = QWidget()
        self._screen.setStyleSheet("background: #000305;")
        slay = QVBoxLayout(self._screen)
        slay.setContentsMargins(0, 0, 0, 0)

        self._img_lbl = QLabel()
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setStyleSheet("background: transparent;")
        if not self._pixmap.isNull():
            scaled = self._pixmap.scaledToWidth(
                self._WIDTH, Qt.TransformationMode.SmoothTransformation)
            self._img_lbl.setPixmap(scaled)
            img_h = scaled.height()
        else:
            self._img_lbl.setText("NO IMAGE")
            self._img_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            img_h = 120
        slay.addWidget(self._img_lbl)
        self._screen.setFixedSize(self._WIDTH, img_h)
        self._outer.addWidget(self._screen)

        foot = QLabel(f"{self._pixmap.width()}×{self._pixmap.height()}"
                      if not self._pixmap.isNull() else "—")
        foot.setFont(QFont("Courier New", 6))
        foot.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; "
                           f"border-top: 1px solid {C.BORDER}; padding: 2px 8px;")
        self._outer.addWidget(foot)

        self._finalize()

    @staticmethod
    def _load_pixmap(source) -> QPixmap:
        if isinstance(source, QPixmap):
            return source
        if isinstance(source, (bytes, bytearray)):
            px = QPixmap()
            px.loadFromData(bytes(source))
            return px
        px = QPixmap(str(source))
        return px

    def _download(self) -> None:
        if self._pixmap.isNull():
            return
        default_name = "".join(
            ch if ch.isalnum() or ch in "_- " else "_" for ch in self._title_lbl.text()
        ).strip() or "jarvis_image"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save image", f"{default_name}.png", "PNG (*.png);;JPEG (*.jpg)"
        )
        if path:
            self._pixmap.save(path)


class _VideoResizeGrip(QWidget):
    """Small diagonal-lines handle in the bottom-right corner of a
    `FloatingVideoWindow`, drag it to resize — the same interaction as a
    normal Windows window's corner grip. Owns its own mouse handling so it
    doesn't interfere with the window's existing header-drag logic."""

    _SIZE = 14

    def __init__(self, owner: "FloatingVideoWindow"):
        super().__init__(owner)
        self._owner = owner
        self.setFixedSize(self._SIZE, self._SIZE)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self._drag_start: QPointF | None = None
        self._start_width = 0

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        pen = QPen(QColor(C.PRI_DIM))
        pen.setWidth(1)
        p.setPen(pen)
        s = self._SIZE
        for off in (3, 7, 11):
            p.drawLine(off, s - 1, s - 1, off)

    def mousePressEvent(self, e) -> None:
        self._drag_start = e.globalPosition()
        self._start_width = self._owner.width()
        e.accept()

    def mouseMoveEvent(self, e) -> None:
        if self._drag_start is None:
            return
        delta = e.globalPosition() - self._drag_start
        # Either axis can drive it — take whichever the user is dragging
        # further, so the grip feels natural from any diagonal.
        d = delta.x() if abs(delta.x()) >= abs(delta.y()) else delta.y()
        self._owner._apply_resize(self._start_width + d)
        e.accept()

    def mouseReleaseEvent(self, e) -> None:
        self._drag_start = None
        self._owner.moved.emit()
        e.accept()


class FloatingVideoWindow(_CrtRevealWindow):
    """A YouTube video, playing live inside its own floating window — same
    CRT reveal, drag, close and core-connector behaviour as
    `FloatingImageWindow`. Needs PyQt6-WebEngine; without it, shows a "open in
    browser" placeholder instead of crashing. Resizable from its bottom-right
    corner, like a normal desktop window — see `_VideoResizeGrip`.
    """

    _ID_RE = re.compile(r"(?:v=|/v/|youtu\.be/|/embed/|/shorts/)([A-Za-z0-9_-]{11})")
    _MIN_W = 180
    _MAX_W = 640

    def __init__(self, source: str, title: str = "VIDEO", parent=None):
        super().__init__(parent)
        self._video_id = self._extract_id(source)

        self._outer.addWidget(self._build_header(title, extra_buttons=[
            ("🌐", "Open in browser", self._open_browser),
        ]))

        self._video_h = int(self._WIDTH * 9 / 16)
        self._web = None
        self._placeholder: QLabel | None = None
        if _WEBENGINE_OK and self._video_id:
            try:
                self._web = QWebEngineView()
                self._web.setFixedSize(self._WIDTH, self._video_h)
                # Route this view through the logging page so any real JS
                # error/warning from inside the embed shows up in the
                # terminal as [Video][JS] instead of vanishing silently.
                try:
                    self._web.setPage(_LoggingVideoPage(self._web))
                except Exception as e:
                    print(f"[Video] ⚠️ Could not attach logging page: {e}")
                # Force a normal desktop-Chrome UA on this view's profile —
                # see the _DESKTOP_CHROME_UA comment above for why. Must be
                # set before the first load.
                try:
                    self._web.page().profile().setHttpUserAgent(_DESKTOP_CHROME_UA)
                except Exception as e:
                    print(f"[Video] ⚠️ Could not override user agent: {e}")
                # Diagnostic: log every request the embed makes (helps spot
                # a redirect to a consent screen or a blocked domain). Keep
                # a reference on self so the interceptor isn't garbage
                # collected out from under the profile.
                try:
                    self._net_logger = _LoggingVideoInterceptor(self._web)
                    self._web.page().profile().setUrlRequestInterceptor(self._net_logger)
                except Exception as e:
                    print(f"[Video] ⚠️ Could not attach network logger: {e}")
                # YouTube's embed endpoint returns "Error 153" if it's loaded
                # as a top-level page — it only accepts requests that arrive
                # genuinely wrapped in an <iframe>. So we load a tiny local
                # HTML shell that puts it inside one, with a real (non-
                # Google) base URL so YouTube's embedder-identity check
                # passes — see the setHtml call below for why it can't be
                # youtube.com itself.
                #
                # Some specific videos (official trailers etc. with embedding
                # turned off by the uploader) will still refuse to play no
                # matter how the embed is built — that's a restriction set by
                # the uploader, not something any embedding page can work
                # around, and YouTube's own player shows its native "watch on
                # YouTube" card for it right here. The 🌐 header button above
                # opens the same video in the real browser for that case.
                embed_url = (
                    f"https://www.youtube-nocookie.com/embed/{self._video_id}"
                    f"?autoplay=1&rel=0&modestbranding=1"
                )
                shell_html = f"""<!DOCTYPE html><html><head><style>
                    html,body {{ margin:0; padding:0; width:100%; height:100%;
                                 background:#000305; overflow:hidden; }}
                    iframe {{ display:block; width:100%; height:100%; border:0; }}
                </style></head><body>
                    <iframe src="{embed_url}" allowfullscreen
                        allow="accelerometer; autoplay; clipboard-write;
                               encrypted-media; gyroscope; picture-in-picture">
                    </iframe>
                </body></html>"""
                # Base URL for the shell page: this becomes the Referer/
                # Origin YouTube's embedder-identity check sees. It must
                # NOT be youtube.com/google.com itself — a page that claims
                # to *be* YouTube while embedding YouTube reads as spoofing
                # to their new identity check and gets a hard "denied"
                # (confirmed via the network logger: the player's own qoe
                # ping reported `error=...:embedder.identity.denied`).
                # A neutral, real, non-Google domain passes it instead.
                self._web.setHtml(shell_html, QUrl("https://www.example.com/"))
                self._web.loadFinished.connect(
                    lambda ok: print(f"[Video] shell loadFinished ok={ok}")
                )
                self._outer.addWidget(self._web)
            except Exception as e:
                print(f"[Video] Embedded player failed, falling back: {e}")
                self._web = None
        if self._web is None:
            self._placeholder = self._build_video_placeholder()
            self._outer.addWidget(self._placeholder)

        foot = QLabel("YOUTUBE · EMBED EN VIVO")
        foot.setFont(QFont("Courier New", 6))
        foot.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; "
                           f"border-top: 1px solid {C.BORDER}; padding: 2px 8px;")
        self._outer.addWidget(foot)

        self._finalize()

        # Unlock the width the base class fixed at self._WIDTH: from here
        # on it can range within [_MIN_W, _MAX_W], dragged via the corner
        # grip below. Height is never fixed by the base class, so it's
        # already free to follow the layout as content resizes.
        self.setMinimumWidth(self._MIN_W)
        self.setMaximumWidth(self._MAX_W)
        self._grip = _VideoResizeGrip(self)
        self._grip.raise_()
        self._position_grip()

    def _position_grip(self) -> None:
        self._grip.move(self.width() - self._grip.width(),
                         self.height() - self._grip.height())

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if getattr(self, "_grip", None) is not None:
            self._position_grip()

    def _apply_resize(self, new_width) -> None:
        """Resize the window to `new_width`, keeping the video/placeholder
        at a 16:9 ratio — called continuously while the corner grip is
        being dragged."""
        new_width = max(self._MIN_W, min(self._MAX_W, int(new_width)))
        new_video_h = int(new_width * 9 / 16)
        if new_width == self.width() and new_video_h == self._video_h:
            return
        self._video_h = new_video_h
        content = self._web if self._web is not None else self._placeholder
        if content is not None:
            content.setFixedSize(new_width, new_video_h)
        self.resize(new_width, self._outer.sizeHint().height())
        # Keep the CRT-reveal mask matched to the new size, fully open —
        # otherwise the old mask would clip/blank part of the new content.
        self._full_h = self.height()
        self._reveal_h = self._full_h
        self._apply_reveal_mask()
        self._position_grip()

    def _build_video_placeholder(self) -> QLabel:
        placeholder = QLabel(
            "VIDEO CANNOT BE EMBEDDED HERE\n(press 🌐 to open it)"
            if self._video_id else
            "INVALID VIDEO URL"
        )
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder.setWordWrap(True)
        placeholder.setFixedSize(self._WIDTH, self._video_h)
        placeholder.setStyleSheet(f"color: {C.TEXT_DIM}; background: #000305;")
        placeholder.setFont(QFont("Courier New", 8))
        return placeholder

    @classmethod
    def _extract_id(cls, source: str) -> str | None:
        s = str(source or "").strip()
        m = cls._ID_RE.search(s)
        if m:
            return m.group(1)
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", s):
            return s
        return None

    def _open_browser(self) -> None:
        import webbrowser
        if self._video_id:
            webbrowser.open(f"https://www.youtube.com/watch?v={self._video_id}")

    def request_close(self) -> None:
        # stop playback/audio immediately, before the close animation starts
        if self._web is not None:
            try:
                self._web.setUrl(QUrl("about:blank"))
            except Exception:
                pass
        super().request_close()


class _CamStreamBridge(QObject):
    """Thread → interface bridge: the reader thread emits, the window receives on the Qt thread."""
    frame = pyqtSignal(int, object)      # (generation, QImage)
    failed = pyqtSignal(int, str)        # (generation, reason)


class _CamVideoView(QWidget):
    """Paints the frames of the live stream, centred and without distortion."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._img = None
        self.setMinimumSize(320, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_frame(self, img) -> None:
        self._img = img
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#000000"))
        if self._img is not None and not self._img.isNull():
            sz = self._img.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            r = QRect(0, 0, sz.width(), sz.height())
            r.moveCenter(self.rect().center())
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage(r, self._img)
        p.end()


class _CamWireOverlay(QWidget):
    """The core → camera window cable, drawn in a transparent window that receives no
    clicks and sits ABOVE the Earth (a normal widget would end up below: the Earth is a
    WebEngine view and is painted on top of all normal widgets). It sits below the
    camera windows, so the cable reaches their edge and stops there.
    """

    def __init__(self, owner: QWidget, central: QWidget, anchor_getter):
        super().__init__(owner)
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowTransparentForInput
                            | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._central = central
        self._anchor_getter = anchor_getter
        self._windows: list = []
        self._t0 = time.time()
        self._timer = QTimer(self)
        self._timer.setInterval(45)
        self._timer.timeout.connect(self._tick)

    def add_window(self, w) -> None:
        self._windows.append(w)
        self._tick()
        if not self._timer.isActive():
            self._timer.start()

    def remove_window(self, w) -> None:
        if w in self._windows:
            self._windows.remove(w)
        if not self._windows:
            self._timer.stop()
            self.hide()

    def _tick(self) -> None:
        # same area as the central region of the main window: so the coordinates of the
        # core anchor (which are in that system) are valid here as they are
        rect = QRect(self._central.mapToGlobal(QPoint(0, 0)), self._central.size())
        if self.geometry() != rect:
            self.setGeometry(rect)
        self.update()

    def paintEvent(self, _event) -> None:
        if not self._windows or self._anchor_getter is None:
            return
        try:
            core = self._anchor_getter()
        except Exception:  # noqa: BLE001
            return
        if core is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pri = qcol(C.PRI)
        dash_off = -((time.time() - self._t0) * 16.0) % 18.0
        for w in self._windows:
            if not w.isVisible():
                continue
            target = QPointF(self.mapFromGlobal(w.anchor_global().toPoint()))
            mid_x = (core.x() + target.x()) / 2.0
            path = QPainterPath(core)
            path.lineTo(mid_x, core.y())
            path.lineTo(mid_x, target.y())
            path.lineTo(target.x(), target.y())
            pen = QPen(pri, 1.6)
            pen.setDashPattern([5, 4])
            pen.setDashOffset(dash_off)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)
            p.setPen(QPen(pri, 2))
            p.setBrush(QBrush(qcol(C.BG)))
            p.drawEllipse(core, 5, 5)
            p.drawEllipse(target, 5, 5)
        p.end()


class LiveCamWindow(_CrtRevealWindow):
    """Public live webcam. Opened from the CAMERAS layer of the globe.

    · Same look and CRT open/close effect as the rest of the floating windows.
    · It is a frameless top-level window owned by the main window: it sits above
      the Earth and the whole HUD, but NOT above other programs (it is not
      "always on top"; if the main window goes to the background, so does it). The cable to the
      core is drawn by _CamWireOverlay.
    · It is dragged by the header and resized with the grip in the bottom-right corner.
    · Playback is ALWAYS direct, inside the window itself and without an embedded browser
      (core/cam_stream.py: the .m3u8 stream is located — on the page, in an iframe, on YouTube
      or in its city listing if the path changed — and decoded with OpenCV; no sound).
      If there is no way to open it, the window says so and offers ⟳ (retry) or 🌐 (original website).
    """

    _WIDTH = 560
    _MIN_W, _MIN_H = 380, 330
    _START_H = 470
    _STREAM_TIMEOUT_MS = 40000

    def __init__(self, cam: dict, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.cam = dict(cam)
        self.cam_id = str(cam.get("id", ""))
        self._page_url = str(cam.get("page", ""))
        self._mode = None            # None | "stream"
        self._gen = 0
        self._stop_evt = None
        self._restarts = 0
        self._ready = False          # the opening animation has finished

        self._outer.addWidget(self._build_header(cam.get("name", "CAMERA"), extra_buttons=[
            ("⟳", "Reload the stream", self._reload),
            ("🌐", "Open the original page of the broadcast", self._open_original),
        ]))

        # ── stage: 0 = status/message · 1 = live stream ─────────────────────────
        from PyQt6.QtWidgets import QStackedLayout
        self._stage = QWidget()
        self._stage.setStyleSheet("background: #000;")
        self._stack = QStackedLayout(self._stage)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._status = QLabel("CONECTANDO…")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setWordWrap(True)
        self._status.setFont(QFont("Courier New", 9))
        self._status.setStyleSheet(f"color: {C.TEXT_DIM}; background: #000305; padding: 12px;")
        self._stack.addWidget(self._status)                     # 0
        self._video = _CamVideoView()
        self._stack.addWidget(self._video)                      # 1
        self._outer.addWidget(self._stage, stretch=1)

        # ── place information ─────────────────────────────────────────────────
        place = " · ".join(x for x in (cam.get("place"), cam.get("country")) if x)
        desc = str(cam.get("desc", ""))
        if len(desc) > 170:
            desc = desc[:167].rstrip() + "…"
        info = QLabel(
            f"<span style='color:{C.PRI};'><b>{self._esc(place)}</b></span><br>"
            f"<span style='color:{C.TEXT_DIM};'>{self._esc(desc)}</span><br>"
            f"<span style='color:{C.TEXT_DIM};'>POS {float(cam.get('lat', 0)):.3f}°, "
            f"{float(cam.get('lon', 0)):.3f}° · EMITS {self._esc(str(cam.get('source', '')).upper())}</span>"
        )
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setWordWrap(True)
        info.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        info.setFixedHeight(78)
        info.setFont(QFont("Courier New", 8))
        info.setStyleSheet("background: transparent; border: none; padding: 6px 10px 0 10px;")
        self._outer.addWidget(info)

        # ── button to the original page ─────────────────────────────────────
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        rl = QHBoxLayout(row)
        rl.setContentsMargins(10, 2, 10, 6)
        btn = QPushButton("▶  OPEN ORIGINAL STREAM  ↗")
        btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedHeight(26)
        btn.setStyleSheet(f"""
            QPushButton {{ color: {C.PRI}; background: transparent;
                border: 1px solid {C.PRI_DIM}; border-radius: 2px; padding: 0 10px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; }}
        """)
        btn.clicked.connect(self._open_original)
        rl.addWidget(btn, stretch=1)
        self._outer.addWidget(row)

        # ── footer + resize handle ──────────────────────────────────────────
        foot = QWidget()
        foot.setStyleSheet(f"background: transparent; border-top: 1px solid {C.BORDER};")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(10, 0, 14, 0)      # 14 px on the right: outside the chamfer
        fl.setSpacing(0)
        self._foot = QLabel("CONECTANDO…")
        self._foot.setFont(QFont("Courier New", 6))
        self._foot.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        fl.addWidget(self._foot, stretch=1)
        from PyQt6.QtWidgets import QSizeGrip
        grip = QSizeGrip(self)
        grip.setFixedSize(16, 16)
        fl.addWidget(grip, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        foot.setFixedHeight(18)
        self._outer.addWidget(foot)

        # Free size (the base fixes it to _WIDTH) and initial CRT mask: collapsed.
        self._finalize()
        self.setMinimumSize(self._MIN_W, self._MIN_H)
        self.setMaximumSize(16777215, 16777215)
        self.resize(self._WIDTH, self._START_H)
        self._full_h = self.height()
        self._reveal_h = 0
        self._apply_reveal_mask()

        self._bridge = _CamStreamBridge(self)
        self._bridge.frame.connect(self._on_frame)
        self._bridge.failed.connect(self._on_stream_failed)
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._on_stream_timeout)

    # -- utilidades -------------------------------------------------------------
    @staticmethod
    def _esc(t: str) -> str:
        return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def anchor_global(self) -> QPointF:
        """Where the cable touches: centre of the top edge, in screen coordinates."""
        return QPointF(self.x() + self.width() / 2.0, self.y())

    # -- opening / size --------------------------------------------------------
    def open_animate(self) -> None:
        super().open_animate()
        # the stream starts when the CRT effect has finished (the web view would cover the flash)
        QTimer.singleShot(480, self._after_open)

    def _after_open(self) -> None:
        self._ready = True
        if not self._closing:
            self._load()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._full_h = self.height()
        if self._ready:
            self._reveal_h = self._full_h
        self._apply_reveal_mask()

    # -- dragging (top-level window: screen coordinates) -----------------------
    def mousePressEvent(self, e) -> None:
        if (self._head is not None and e.button() == Qt.MouseButton.LeftButton
                and e.position().y() <= self._head.height() + 1):
            self._drag_offset = e.globalPosition() - QPointF(self.frameGeometry().topLeft())
            self._head.setCursor(Qt.CursorShape.ClosedHandCursor)
            e.accept()
            return
        QWidget.mousePressEvent(self, e)

    def mouseMoveEvent(self, e) -> None:
        if self._drag_offset is not None and (e.buttons() & Qt.MouseButton.LeftButton):
            self.move((e.globalPosition() - self._drag_offset).toPoint())
            self.moved.emit()
            e.accept()
            return
        QWidget.mouseMoveEvent(self, e)

    def mouseReleaseEvent(self, e) -> None:
        self._drag_offset = None
        if self._head is not None:
            self._head.setCursor(Qt.CursorShape.OpenHandCursor)
        QWidget.mouseReleaseEvent(self, e)

    # -- stream loading ------------------------------------------------------
    def _load(self) -> None:
        self._shutdown_stream()
        self._mode = None
        self._stack.setCurrentIndex(0)
        self._start_stream()

    # mode 1: live stream ----------------------------------------------------------
    def _start_stream(self) -> None:
        self._foot.setText("LOOKING FOR THE STREAM…")
        self._status.setText("CONECTANDO…")
        self._gen += 1
        gen = self._gen
        stop = threading.Event()
        self._stop_evt = stop
        bridge = self._bridge
        direct = str(self.cam.get("stream") or "").strip() or None
        yt = str(self.cam.get("youtube") or "").strip()
        hint = " ".join(str(x) for x in (self.cam.get("name"), self.cam.get("place")) if x)

        def on_frame(data: bytes, w: int, h: int) -> None:
            from PyQt6.QtGui import QImage
            bridge.frame.emit(gen, QImage(data, w, h, 3 * w, QImage.Format.Format_RGB888).copy())

        def on_fail(msg: str) -> None:
            bridge.failed.emit(gen, msg)

        def work() -> None:
            try:
                from core.cam_stream import run_stream
                run_stream(self._page_url, direct, stop, on_frame, on_fail, youtube=yt, hint=hint)
            except Exception as e:  # noqa: BLE001  (e.g. opencv or requests missing)
                on_fail(f"{type(e).__name__}: {e}")

        threading.Thread(target=work, name="live-cam", daemon=True).start()
        self._watchdog.start(self._STREAM_TIMEOUT_MS)

    def _on_frame(self, gen: int, img) -> None:
        if gen != self._gen or self._closing:
            return
        if self._mode != "stream":
            self._mode = "stream"
            self._watchdog.stop()
            self._stack.setCurrentIndex(1)
            self._foot.setText(f"LIVE · {str(self.cam.get('source', '')).upper()} · NO SOUND")
        self._video.set_frame(img)

    def _on_stream_failed(self, gen: int, msg: str) -> None:
        if gen != self._gen or self._closing:
            return
        self._watchdog.stop()
        if self._mode == "stream" and self._restarts < 3:
            # it was playing and was cut off (usually the expiring token): request a new one
            self._restarts += 1
            self._foot.setText("RECONECTANDO…")
            self._start_stream()
            return
        print(f"[Cam] {self.cam_id}: live stream unavailable ({msg})")
        self._mode = None
        self._status.setText(f"THE STREAM COULD NOT BE OPENED\n\n{msg}\n\n⟳ retry · 🌐 original website")
        self._stack.setCurrentIndex(0)
        self._foot.setText("NO STREAM · PRESS ⟳ TO RETRY")

    def _on_stream_timeout(self) -> None:
        if self._mode is None and not self._closing:
            self._on_stream_failed(self._gen, "no image after 40 s")

    def _shutdown_stream(self) -> None:
        self._gen += 1                       # frames that still arrive are ignored
        self._watchdog.stop()
        if self._stop_evt is not None:
            self._stop_evt.set()
            self._stop_evt = None

    # -- botones -------------------------------------------------------------------
    def _reload(self) -> None:
        self._restarts = 0
        self._load()

    def _open_original(self) -> None:
        import webbrowser
        if self._page_url.startswith(("http://", "https://")):
            webbrowser.open(self._page_url)

    # -- cierre --------------------------------------------------------------------
    def request_close(self) -> None:
        if self._closing:
            return
        self._shutdown_stream()
        super().request_close()             # CRT closing effect → emits `closed`

    def closeEvent(self, e) -> None:
        if not self._closing:
            e.ignore()
            self.request_close()
        else:
            e.accept()


class MailReaderWindow(_CrtRevealWindow):
    """Holographic mail reader. Opens when Jarvis offers to put a mail on screen
    ("Would you like me to open a window so you can read it?") and the user says yes.

    One window holds every mail from the last check/read, with ◀ ▶ to move
    between them. Plain text only and read-only: no HTML is rendered, so nothing
    remote is ever loaded. Same CRT reveal / drag / ✕ behaviour as the image and
    video windows, and the same wire to the core.
    """

    _WIDTH  = 500
    _BODY_H = 290

    def __init__(self, messages: list, parent=None):
        super().__init__(parent)
        self._msgs: list[dict] = []
        self._i = 0

        self._outer.addWidget(self._build_header("✉  CORREO", extra_buttons=[
            ("◀", "Previous", self._prev),
            ("▶", "Next", self._next),
        ]))

        info = QWidget()
        info.setStyleSheet("background: transparent;")
        il = QVBoxLayout(info)
        il.setContentsMargins(12, 8, 12, 4)
        il.setSpacing(2)

        def _mk(size, color, bold=False, wrap=False):
            lb = QLabel()
            lb.setFont(QFont("Courier New", size,
                             QFont.Weight.Bold if bold else QFont.Weight.Normal))
            lb.setStyleSheet(f"color: {color}; background: transparent;")
            lb.setWordWrap(wrap)
            lb.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            return lb

        self._counter = _mk(7, C.TEXT_DIM)
        self._from    = _mk(9, C.PRI, bold=True)
        self._subject = _mk(9, C.WHITE, wrap=True)
        self._subject.setFixedHeight(36)
        self._subject.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._meta    = _mk(7, C.TEXT_DIM)
        for w in (self._counter, self._from, self._subject, self._meta):
            il.addWidget(w)
        self._outer.addWidget(info)

        self._body = QTextEdit()
        self._body.setReadOnly(True)
        self._body.setAcceptRichText(False)
        self._body.setFixedHeight(self._BODY_H)
        self._body.setFont(QFont("Courier New", 9))
        self._body.setStyleSheet(f"""
            QTextEdit {{ background: #000305; color: {C.TEXT}; border: none;
                border-top: 1px solid {C.BORDER}; padding: 8px 12px;
                selection-background-color: {C.PRI_DIM}; }}
            QScrollBar:vertical {{ width: 6px; background: transparent; margin: 0; }}
            QScrollBar::handle:vertical {{ background: {C.PRI_DIM}; border-radius: 3px; min-height: 22px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
        """)
        self._outer.addWidget(self._body)

        foot = QLabel("READ ONLY  ·  NO REMOTE CONTENT")
        foot.setFont(QFont("Courier New", 6))
        foot.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; "
                           f"border-top: 1px solid {C.BORDER}; padding: 2px 8px;")
        self._outer.addWidget(foot)

        self._finalize()
        self.set_messages(messages)

    def _elide(self, lbl: QLabel, text: str) -> None:
        fm = lbl.fontMetrics()
        lbl.setText(fm.elidedText(text, Qt.TextElideMode.ElideRight, self._WIDTH - 30))

    def set_messages(self, messages: list) -> None:
        self._msgs = [m for m in (messages or []) if isinstance(m, dict)]
        self._i = 0
        self._show()

    def _prev(self) -> None:
        if self._i > 0:
            self._i -= 1
            self._show()

    def _next(self) -> None:
        if self._i < len(self._msgs) - 1:
            self._i += 1
            self._show()

    def _show(self) -> None:
        if not self._msgs:
            self._counter.setText("0/0")
            self._from.setText("")
            self._subject.setText("")
            self._meta.setText("")
            self._body.setPlainText("(no emails)")
            return
        m = self._msgs[self._i]
        n = len(self._msgs)
        self._counter.setText(f"{self._i + 1}/{n}  ·  {str(m.get('account', '')).upper()}")
        sender = f"{m.get('from_name', '')} <{m.get('from_addr', '')}>" \
            if m.get("from_addr") and m.get("from_name") != m.get("from_addr") \
            else str(m.get("from_addr") or m.get("from_name") or "")
        self._elide(self._from, "FROM  " + sender)
        subj = str(m.get("subject", ""))
        self._subject.setText(subj if len(subj) <= 130 else subj[:127] + "…")
        atts = m.get("attachments") or []
        self._meta.setText(str(m.get("date", "")) + (f"  ·  📎 {len(atts)}" if atts else ""))
        body = str(m.get("body") or "(email without text)")
        if atts:
            body += "\n\n— Adjuntos: " + ", ".join(atts)
        self._body.setPlainText(body)
        self._body.verticalScrollBar().setValue(0)


class _ImageWireLayer(QWidget):
    """Transparent overlay that draws the cyber-arrow connectors between the
    JARVIS core and every open floating window (images and videos alike) — a
    dashed, glowing, right-angled trace with a circle terminal at each end.
    Repaints on a light timer only while at least one window is open, so it
    costs nothing the rest of the time.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._windows: list[_CrtRevealWindow] = []
        self._anchor_getter = None
        self._t0 = time.time()
        self._timer = QTimer(self)
        self._timer.setInterval(45)
        self._timer.timeout.connect(self.update)

    def set_anchor_getter(self, fn) -> None:
        self._anchor_getter = fn

    def add_window(self, w: _CrtRevealWindow) -> None:
        self._windows.append(w)
        w.moved.connect(self.update)
        if not self._timer.isActive():
            self._timer.start()

    def remove_window(self, w: _CrtRevealWindow) -> None:
        if w in self._windows:
            self._windows.remove(w)
        if not self._windows:
            self._timer.stop()
        self.update()

    def paintEvent(self, _event):
        if not self._windows or self._anchor_getter is None:
            return
        try:
            core = self._anchor_getter()
        except Exception:
            return
        if core is None:
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pri = qcol(C.PRI)
        dash_off = -((time.time() - self._t0) * 16.0) % 18.0

        for w in self._windows:
            if not w.isVisible():
                continue
            target = w.anchor_point()
            mid_x = (core.x() + target.x()) / 2.0

            path = QPainterPath(core)
            path.lineTo(mid_x, core.y())
            path.lineTo(mid_x, target.y())
            path.lineTo(target.x(), target.y())

            pen = QPen(pri, 1.6)
            pen.setDashPattern([5, 4])
            pen.setDashOffset(dash_off)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)

            p.setPen(QPen(pri, 2))
            p.setBrush(QBrush(qcol(C.BG)))
            p.drawEllipse(core, 5, 5)
            p.drawEllipse(target, 5, 5)


class HudIconButton(QPushButton):
    """Control button for the Spotify mini-player: chamfered HUD plate with a
    thin-line icon painted by QPainter (no emoji / no font glyphs, so it looks
    identical on every OS and re-tints with the HUD accent colour).

    kind: "prev" | "play" | "pause" | "next" | "shuffle"
    Colours are read from `C` at paint time → apply_ui_accent() is honoured.
    """

    _CUT = 5.0     # chamfer size of the plate corners (top-left / bottom-right)

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self._kind  = kind
        self._hover = False
        self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)

    # ── public ───────────────────────────────────────────────────────────────
    def set_kind(self, kind: str):
        if kind != self._kind:
            self._kind = kind
            self.update()

    # ── hover tracking ───────────────────────────────────────────────────────
    def enterEvent(self, e):
        self._hover = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    # ── icon geometry (unit square, y down) ──────────────────────────────────
    @staticmethod
    def _poly(*pts) -> QPainterPath:
        path = QPainterPath()
        path.moveTo(*pts[0])
        for x, y in pts[1:]:
            path.lineTo(x, y)
        path.closeSubpath()
        return path

    @staticmethod
    def _line(*pts) -> QPainterPath:
        path = QPainterPath()
        path.moveTo(*pts[0])
        for x, y in pts[1:]:
            path.lineTo(x, y)
        return path

    def _icon_paths(self) -> tuple[list[QPainterPath], list[QPainterPath]]:
        """(closed shapes — outlined + faint fill, open strokes — outline only)"""
        P, L, k = self._poly, self._line, self._kind
        if k == "play":
            return [P((0.20, 0.04), (0.94, 0.50), (0.20, 0.96))], []
        if k == "pause":
            return [P((0.12, 0.06), (0.38, 0.06), (0.38, 0.94), (0.12, 0.94)),
                    P((0.62, 0.06), (0.88, 0.06), (0.88, 0.94), (0.62, 0.94))], []
        if k == "next":
            return [P((0.04, 0.10), (0.70, 0.50), (0.04, 0.90))], \
                   [L((0.92, 0.08), (0.92, 0.92))]
        if k == "prev":
            return [P((0.96, 0.10), (0.30, 0.50), (0.96, 0.90))], \
                   [L((0.08, 0.08), (0.08, 0.92))]
        if k == "close":
            return [], [L((0.12, 0.12), (0.88, 0.88)), L((0.88, 0.12), (0.12, 0.88))]
        if k == "pin":
            return [P((0.50, 0.02), (0.90, 0.40), (0.50, 0.74), (0.10, 0.40))], \
                   [L((0.50, 0.74), (0.50, 1.00))]
        if k == "shuffle":
            return [], [
                L((0.00, 0.78), (0.28, 0.78), (0.64, 0.24), (0.88, 0.24)),
                L((0.76, 0.10), (0.94, 0.24), (0.76, 0.38)),
                L((0.00, 0.24), (0.28, 0.24), (0.64, 0.78), (0.88, 0.78)),
                L((0.76, 0.64), (0.94, 0.78), (0.76, 0.92)),
            ]
        return [], []

    # ── painting ─────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        W, H = self.width(), self.height()
        if W < 6 or H < 6:
            return
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        lit     = self._hover or self.isDown() or self.isChecked()
        pri     = QColor(C.PRI)
        line    = pri if lit else QColor(C.TEXT_MED)
        border  = pri if (self._hover or self.isChecked()) else QColor(C.BORDER_B)
        c       = min(self._CUT, H * 0.30)

        # chamfered plate: cut top-left and bottom-right corners
        x0, y0, x1, y1 = 0.5, 0.5, W - 0.5, H - 0.5
        plate = QPolygonF([
            QPointF(x0 + c, y0), QPointF(x1, y0), QPointF(x1, y1 - c),
            QPointF(x1 - c, y1), QPointF(x0, y1), QPointF(x0, y0 + c),
        ])
        fill = QColor(pri)
        fill.setAlpha(70 if self.isDown() else (34 if lit else 10))
        pen_b = QPen(border, 1.0)
        pen_b.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        p.setPen(pen_b)
        p.setBrush(fill)
        p.drawPolygon(plate)

        # bright corner ticks on the two chamfers (HUD bracket feel)
        tick = QPen(pri if lit else QColor(C.PRI_DIM), 1.4)
        tick.setCapStyle(Qt.PenCapStyle.FlatCap)
        p.setPen(tick)
        p.drawLine(QPointF(x0, y0 + c), QPointF(x0 + c, y0))
        p.drawLine(QPointF(x1, y1 - c), QPointF(x1 - c, y1))

        # icon, centred, ~45 % of the plate height
        s = max(6.0, min(H * 0.50, W * 0.50, 14.0))
        p.save()
        p.translate(W / 2 - s / 2, H / 2 - s / 2)
        p.scale(s, s)

        shapes, strokes = self._icon_paths()
        face = QColor(line)
        face.setAlpha(60 if lit else 28)

        if lit:                                  # soft neon glow underlay
            glow = QColor(pri)
            glow.setAlpha(55)
            g = QPen(glow, 3.6)
            g.setCosmetic(True)
            g.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            g.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(g)
            p.setBrush(Qt.BrushStyle.NoBrush)
            for path in shapes + strokes:
                p.drawPath(path)

        pen = QPen(line, 1.25)
        pen.setCosmetic(True)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        pen.setCapStyle(Qt.PenCapStyle.SquareCap)
        p.setPen(pen)
        p.setBrush(face)
        for path in shapes:
            p.drawPath(path)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for path in strokes:
            p.drawPath(path)
        p.restore()

        # shuffle ON indicator: short neon bar under the icon
        if self._kind == "shuffle" and self.isChecked():
            p.setPen(QPen(pri, 1.6))
            p.drawLine(QPointF(W / 2 - 5, H - 3.5), QPointF(W / 2 + 5, H - 3.5))
        p.end()


class _ElideLabel(QLabel):
    """Single-line label: long text is cut with '…' instead of wrapping or
    spilling over neighbouring widgets (full text stays in the tooltip)."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full  = text or ""
        self._color = QColor(C.TEXT)
        self.setWordWrap(False)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)

    def set_color(self, color: str):
        self._color = QColor(color)
        self.update()

    def setText(self, text: str):                      # noqa: N802 (Qt naming)
        text = text or ""
        if text != self._full:
            self._full = text
            self.setToolTip(text)
            self.updateGeometry()
            self.update()

    def text(self) -> str:
        return self._full

    def sizeHint(self) -> QSize:
        fm = self.fontMetrics()
        return QSize(fm.horizontalAdvance(self._full), fm.height() + 2)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, self.fontMetrics().height() + 2)

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setFont(self.font())
        p.setPen(self._color)
        shown = self.fontMetrics().elidedText(
            self._full, Qt.TextElideMode.ElideRight, max(0, self.width()))
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignVCenter | self.alignment(), shown)
        p.end()


class SpotifyMiniPlayer(HudFrame):
    """Compact Spotify panel that lives inside the left sidebar.

    • COMPACT  (height < _EXPAND_AT): 36 px cover + title/artist in one row.
    • EXPANDED (height ≥ _EXPAND_AT): centred cover that grows with the panel,
      title/artist below.
    • The bottom edge is a drag handle: grab it (cursor turns into ↕) and drag
      to make the player taller/shorter; double-click it to go back to the
      automatic height. The chosen height is remembered between sessions.

    Both modes are separate child pages (no widgets are re-parented), titles
    and artists are single-line with '…' elision, so nothing can overlap.
    """

    play_pause_clicked = pyqtSignal()
    next_clicked       = pyqtSignal()
    previous_clicked   = pyqtSignal()
    shuffle_clicked    = pyqtSignal()

    _COMPACT_ART = 36
    _EXPAND_AT   = 176      # px of height from which the expanded page is used
    _MIN_H       = 112
    _MAX_H       = 380
    _GRIP_H      = 10       # height of the draggable zone at the bottom edge
    _UI_NS       = "spotify_ui"

    # MAXIMUM button sizes per mode: (side width, height). The play/pause
    # button is _PP_RATIO × wider. Actual sizes shrink with the panel width
    # (see _apply_btn_sizes) so the four buttons can never touch or overlap.
    _BTN_COMPACT  = (24, 18)
    _BTN_EXPANDED = (28, 22)
    _PP_RATIO     = 1.3
    _BTN_MIN_W    = 8

    def __init__(self, parent=None):
        super().__init__(cuts=(12, 0, 12, 0), glow=True, tint=0.09, parent=parent)

        self._art_px: QPixmap | None = None
        self._art_url   = ""
        self._expanded  = False
        self._playing   = False
        self._drag_y: float | None = None
        self._drag_h    = 0
        self._grip_hot  = False
        self.setMouseTracking(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 14)     # 14 px bottom = grip zone
        root.setSpacing(4)

        self._hdr = QLabel("◈  NOW PLAYING  //  SPOTIFY")
        self._hdr.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        self._hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        root.addWidget(self._hdr)

        self._sep = QFrame(); self._sep.setFrameShape(QFrame.Shape.HLine)
        self._sep.setStyleSheet(f"color: {C.BORDER}; margin: 0;")
        root.addWidget(self._sep)

        # ── compact page: [cover] [title / artist] ──────────────────────────
        self._page_c = QWidget()
        self._page_c.setStyleSheet("background: transparent;")
        row = QHBoxLayout(self._page_c)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self._art_c    = self._mk_art(self._COMPACT_ART, 3, C.BORDER)
        self._title_c  = self._mk_text(7, True,  C.TEXT,     Qt.AlignmentFlag.AlignLeft)
        self._artist_c = self._mk_text(6, False, C.TEXT_DIM, Qt.AlignmentFlag.AlignLeft)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addStretch()
        col.addWidget(self._title_c)
        col.addWidget(self._artist_c)
        col.addStretch()
        row.addWidget(self._art_c, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addLayout(col, 1)
        root.addWidget(self._page_c, 1)

        # ── expanded page: cover on top, text below ─────────────────────────
        self._page_e = QWidget()
        self._page_e.setStyleSheet("background: transparent;")
        # Ignored vertically → the page never forces the (fixed-height) player
        # taller; the cover is sized from the player height instead.
        self._page_e.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Ignored)
        colx = QVBoxLayout(self._page_e)
        colx.setContentsMargins(0, 2, 0, 2)
        colx.setSpacing(4)
        self._art_e    = self._mk_art(60, 6, C.BORDER_B)
        self._title_e  = self._mk_text(8, True,  C.TEXT,     Qt.AlignmentFlag.AlignCenter)
        self._artist_e = self._mk_text(7, False, C.TEXT_DIM, Qt.AlignmentFlag.AlignCenter)
        colx.addStretch()
        colx.addWidget(self._art_e, 0, Qt.AlignmentFlag.AlignHCenter)
        colx.addWidget(self._title_e)
        colx.addWidget(self._artist_e)
        colx.addStretch()
        root.addWidget(self._page_e, 1)
        self._page_e.hide()

        # ── progress bar ────────────────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setFixedHeight(3)
        self._progress.setTextVisible(False)
        self._progress.setRange(0, 1000)
        self._progress.setStyleSheet(f"""
            QProgressBar {{ background: {C.BORDER}; border: none; border-radius: 1px; }}
            QProgressBar::chunk {{ background: {C.PRI}; border-radius: 1px; }}
        """)
        root.addWidget(self._progress)

        # ── controls (fixed-size HUD buttons, centred, with a gap) ──────────
        self._ctrl_row = QHBoxLayout()
        self._ctrl_row.setSpacing(0)                # gaps are explicit spacers
        self._ctrl_row.setContentsMargins(0, 2, 0, 0)

        self._prev_btn    = HudIconButton("prev")
        self._pp_btn      = HudIconButton("pause")
        self._next_btn    = HudIconButton("next")
        self._shuffle_btn = HudIconButton("shuffle")
        self._shuffle_btn.setCheckable(True)

        self._prev_btn.clicked.connect(self.previous_clicked.emit)
        self._pp_btn.clicked.connect(self.play_pause_clicked.emit)
        self._next_btn.clicked.connect(self.next_clicked.emit)
        self._shuffle_btn.clicked.connect(self.shuffle_clicked.emit)

        # layout: stretch, btn, gap, btn, gap, btn, gap, btn, stretch
        self._ctrl_row.addStretch(1)
        self._ctrl_row.addWidget(self._prev_btn)
        self._ctrl_row.addSpacing(6)
        self._ctrl_row.addWidget(self._pp_btn)
        self._ctrl_row.addSpacing(6)
        self._ctrl_row.addWidget(self._next_btn)
        self._ctrl_row.addSpacing(6)
        self._ctrl_row.addWidget(self._shuffle_btn)
        self._ctrl_row.addStretch(1)
        self._gap_idx = (2, 4, 6)                   # item indices of the 3 gaps
        root.addLayout(self._ctrl_row)

        self._apply_btn_sizes(False)
        self._load_height()
        self.hide()

    # ── small factories ──────────────────────────────────────────────────────
    @staticmethod
    def _mk_art(size: int, radius: int, border: str) -> QLabel:
        lbl = QLabel()
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setFixedSize(size, size)
        lbl.setStyleSheet(
            f"background: #000d12; border: 1px solid {border}; border-radius: {radius}px;")
        return lbl

    @staticmethod
    def _mk_text(pt: int, bold: bool, color: str, align) -> _ElideLabel:
        lbl = _ElideLabel("")
        lbl.setFont(QFont("Courier New", pt,
                          QFont.Weight.Bold if bold else QFont.Weight.Normal))
        lbl.set_color(color)
        lbl.setAlignment(align)
        return lbl

    def _apply_btn_sizes(self, expanded: bool):
        """Size the 4 buttons from the width actually available, so they fit
        with a visible gap at ANY panel width (they shrink instead of
        overlapping). Total = 3·side + pp + 3·gap  ≤  available width."""
        base_w, base_h = self._BTN_EXPANDED if expanded else self._BTN_COMPACT
        avail = max(40, self.width() - 28)          # root margins 14 + 14
        units = 3 + self._PP_RATIO                  # 3 side buttons + 1 wide
        gap   = 6
        side  = (avail - 3 * gap) / units
        if side < 12:                               # very narrow → tighter gaps
            gap  = 3
            side = (avail - 3 * gap) / units
        if side < self._BTN_MIN_W + 2:
            gap  = 2
            side = (avail - 3 * gap) / units
        side = int(max(self._BTN_MIN_W, min(base_w, side)))
        pp_w = int(side * self._PP_RATIO)
        h    = int(max(12, min(base_h, side * 0.8)))

        for b in (self._prev_btn, self._next_btn, self._shuffle_btn):
            b.setFixedSize(side, h)
        self._pp_btn.setFixedSize(pp_w, h)

        for i in self._gap_idx:
            it = self._ctrl_row.itemAt(i)
            sp = it.spacerItem() if it is not None else None
            if sp is not None:
                sp.changeSize(gap, 1, QSizePolicy.Policy.Fixed,
                              QSizePolicy.Policy.Minimum)
        self._ctrl_row.invalidate()

    # ── mode switching ───────────────────────────────────────────────────────
    def _set_mode(self, expanded: bool):
        self._expanded = expanded
        self._page_c.setVisible(not expanded)
        self._page_e.setVisible(expanded)
        self._apply_btn_sizes(expanded)
        self._fit_expanded_art()
        self._refresh_art()

    def _fit_expanded_art(self):
        """Cover grows/shrinks with the player height (computed from the
        player's own size, not from the page, so it can shrink again)."""
        if not self._expanded:
            return
        btn_h  = self._BTN_EXPANDED[1]
        chrome = (12 + 14                                   # root margins
                  + self._hdr.sizeHint().height()
                  + self._sep.sizeHint().height()
                  + 3 + btn_h + 2                           # progress + buttons
                  + 4 * 4)                                  # root spacings
        text   = self._title_e.sizeHint().height() + self._artist_e.sizeHint().height() + 8 + 4
        avail_h = self.height() - chrome - text
        avail_w = self.width() - 28
        sz = int(max(40, min(avail_h, avail_w, 190)))
        if sz != self._art_e.width():
            self._art_e.setFixedSize(sz, sz)
            self._refresh_art()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        want = self.height() >= self._EXPAND_AT
        if want != self._expanded:
            self._set_mode(want)
        else:
            self._fit_expanded_art()
        self._apply_btn_sizes(self._expanded)

    # ── cover ────────────────────────────────────────────────────────────────
    def _refresh_art(self):
        for lbl in (self._art_c, self._art_e):
            if self._art_px is None or self._art_px.isNull():
                lbl.clear()
                continue
            d = max(8, min(lbl.width(), lbl.height()) - 4)   # inside the 1 px border
            lbl.setPixmap(self._art_px.scaled(
                d, d,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))

    # ── public API (unchanged names) ─────────────────────────────────────────
    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def art_url_loaded(self) -> str | None:
        """URL of the cover currently on screen, or None if it has not been
        (successfully) loaded yet — the poller uses this to know when to
        (re)download."""
        return self._art_url if self._art_px is not None else None

    def update_track(self, info: dict):
        title  = info.get("title") or "—"
        artist = info.get("artist") or ""
        for lbl, txt in ((self._title_c, title), (self._title_e, title),
                         (self._artist_c, artist), (self._artist_e, artist)):
            lbl.setText(txt)

        self._playing = bool(info.get("playing"))
        self._pp_btn.set_kind("pause" if self._playing else "play")
        self._shuffle_btn.setChecked(bool(info.get("shuffle")))

        dur = max(1, info.get("duration_ms") or 1)
        pos = min(info.get("progress_ms") or 0, dur)
        self._progress.setValue(int(pos / dur * 1000))

        # Drop the cover ONLY when the album art actually changes — two songs
        # from the same album share one image and must not blank it.
        url = info.get("album_art") or ""
        if url != self._art_url:
            self._art_url = url
            self._art_px  = None
            self._refresh_art()
        self.show()

    def set_art(self, img_bytes: bytes):
        px = QPixmap()
        if img_bytes and px.loadFromData(img_bytes):
            self._art_px = px
        else:
            self._art_px = None
        self._refresh_art()

    # ── manual resize via the bottom edge ────────────────────────────────────
    def _in_grip(self, y: float) -> bool:
        return y >= self.height() - self._GRIP_H

    def _apply_user_height(self, h: int):
        h = int(max(self._MIN_H, min(self._MAX_H, h)))
        self.setFixedHeight(h)

    def _reset_height(self):
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)      # QWIDGETSIZE_MAX → automatic again
        self.updateGeometry()

    def _load_height(self):
        try:
            from memory.config_manager import get_plugin_config
            h = int(get_plugin_config(self._UI_NS).get("height") or 0)
        except Exception:
            h = 0
        if h:
            self._apply_user_height(h)

    def _save_height(self, h: int):
        try:
            from memory.config_manager import save_plugin_config
            save_plugin_config(self._UI_NS, {"height": int(h)})
        except Exception:
            pass

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._in_grip(e.position().y()):
            self._drag_y = e.globalPosition().y()
            self._drag_h = self.height()
            self.update()
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_y is not None:
            self._apply_user_height(self._drag_h + (e.globalPosition().y() - self._drag_y))
            e.accept()
            return
        hot = self._in_grip(e.position().y())
        if hot != self._grip_hot:
            self._grip_hot = hot
            self.setCursor(Qt.CursorShape.SizeVerCursor if hot else Qt.CursorShape.ArrowCursor)
            self.update()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._drag_y is not None:
            self._drag_y = None
            self._save_height(self.height())
            self.update()
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if self._in_grip(e.position().y()):
            self._reset_height()
            self._save_height(0)
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def leaveEvent(self, e):
        if self._drag_y is None and self._grip_hot:
            self._grip_hot = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()
        super().leaveEvent(e)

    def paintEvent(self, e):
        super().paintEvent(e)                       # HUD plate (cached)
        W, H = self.width(), self.height()
        if W < 40 or H < 40:
            return
        p = QPainter(self)
        if not p.isActive():
            return
        hot = self._grip_hot or self._drag_y is not None
        p.setPen(QPen(QColor(C.PRI if hot else C.PRI_DIM), 1.2))
        cx, y = W / 2, H - 5
        p.drawLine(QPointF(cx - 9, y),     QPointF(cx + 9, y))
        p.drawLine(QPointF(cx - 5, y - 3), QPointF(cx + 5, y - 3))
        p.end()


class LyricsView(QWidget):
    """Apple-Music-style lyrics canvas, painted with QPainter.

    • the line being sung is large + bright (with a faint neon halo), the rest
      are smaller and dimmer, fading out towards the top/bottom edges;
    • the list glides so the current line stays at ~38 % of the height;
    • mouse wheel scrolls freely (auto-follow resumes after 3.5 s);
    • click a line → seek_requested(ms).
    Unsynced lyrics ("plain") are shown as a static, scrollable text.
    """

    seek_requested = pyqtSignal(int)

    _PAD     = 22
    _GAP     = 12
    _LEAD_MS = 120          # highlight a line slightly before its timestamp
    _FOCUS   = 0.38         # vertical position of the active line (0..1)
    _FLAGS   = (Qt.TextFlag.TextWordWrap.value
                | Qt.AlignmentFlag.AlignLeft.value
                | Qt.AlignmentFlag.AlignVCenter.value)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(80)

        self._font = QFont()
        self._font.setFamilies(["Segoe UI", "SF Pro Display", "Helvetica Neue",
                                "Noto Sans", "Arial"])
        self._font.setBold(True)

        self._state = "idle"      # idle|loading|synced|plain|instrumental|notfound|error
        self._msg   = ""
        self._lines: list[tuple[int, str]] = []
        self._times: list[int] = []
        self._hs: list[float] = []
        self._ys: list[float] = []
        self._total = 0.0
        self._layout_key = None
        self._emph: list[float] = []
        self._active = -1
        self._scroll = 0.0
        self._snap   = True
        self._override: float | None = None
        self._override_until = 0.0
        self._last_t = time.monotonic()

    # ── content ──────────────────────────────────────────────────────────────
    def set_state(self, state: str, msg: str = "", lines=None, plain: str = ""):
        self._state = state
        self._msg   = msg
        if state == "synced" and lines:
            self._lines = [(int(t), str(x)) for t, x in lines]
        elif state == "plain" and plain:
            self._lines = [(0, x.strip()) for x in plain.splitlines()]
        else:
            self._lines = []
        self._times  = [t for t, _ in self._lines]
        self._emph   = [0.0] * len(self._lines)
        self._active = -1
        self._scroll = 0.0
        self._snap   = True
        self._override = None
        self._layout_key = None
        self.update()

    # ── geometry ─────────────────────────────────────────────────────────────
    def _font_pt(self) -> float:
        return max(12.0, min(30.0, self.width() / 17.0))

    def _ensure_layout(self):
        key = (self.width(), len(self._lines), self._state)
        if key == self._layout_key:
            return
        self._layout_key = key
        iw = max(40, self.width() - 2 * self._PAD)
        f = QFont(self._font)
        f.setPointSizeF(self._font_pt())
        fm = QFontMetricsF(f)
        synced = self._state == "synced"
        self._hs, self._ys = [], []
        y = 8.0
        for _, text in self._lines:
            if not text:
                h = 30.0 if synced else 12.0
            else:
                h = fm.boundingRect(QRectF(0, 0, iw, 100000), self._FLAGS, text).height() + 8
            self._hs.append(h)
            self._ys.append(y)
            y += h + self._GAP
        self._total = y

    def _clamp_scroll(self, v: float) -> float:
        H = self.height()
        if self._state == "synced":
            lo, hi = -H * self._FOCUS, max(-H * self._FOCUS, self._total - H * 0.5)
        else:
            lo, hi = 0.0, max(0.0, self._total - H)
        return max(lo, min(hi, v))

    # ── animation step (called by the window's timer) ────────────────────────
    def tick(self, pos_ms: float):
        now = time.monotonic()
        dt = min(0.1, max(0.001, now - self._last_t))
        self._last_t = now
        if self._state not in ("synced", "plain") or not self._lines:
            return
        self._ensure_layout()
        H = self.height()

        if self._state == "synced":
            idx = bisect.bisect_right(self._times, pos_ms + self._LEAD_MS) - 1
            self._active = idx
            k = min(1.0, dt * 9.0)
            for i in range(len(self._emph)):
                tgt = 1.0 if i == idx else 0.0
                e = self._emph[i]
                if e != tgt:
                    e += (tgt - e) * k
                    self._emph[i] = tgt if abs(tgt - e) < 0.01 else e
            if idx >= 0:
                follow = self._ys[idx] + self._hs[idx] / 2 - H * self._FOCUS
            else:
                follow = -H * self._FOCUS + 8
        else:
            follow = self._scroll

        if self._override is not None and now < self._override_until:
            tgt = self._override
        else:
            self._override = None
            tgt = follow
        tgt = self._clamp_scroll(tgt)

        if self._snap:
            self._scroll, self._snap = tgt, False
        else:
            self._scroll += (tgt - self._scroll) * min(1.0, dt * 6.5)
        self.update()

    # ── input ────────────────────────────────────────────────────────────────
    def wheelEvent(self, e):
        if self._state not in ("synced", "plain") or not self._lines:
            return
        now = time.monotonic()
        base = (self._override if (self._override is not None and now < self._override_until)
                else self._scroll)
        self._override = self._clamp_scroll(base - e.angleDelta().y() * 0.8)
        self._override_until = now + (1e9 if self._state == "plain" else 3.5)
        e.accept()

    def _line_at(self, y: float) -> int:
        yc = y + self._scroll
        for i, (top, h) in enumerate(zip(self._ys, self._hs)):
            if top - self._GAP / 2 <= yc < top + h + self._GAP / 2:
                return i
        return -1

    def mouseMoveEvent(self, e):
        if self._state == "synced":
            i = self._line_at(e.position().y())
            self.setCursor(Qt.CursorShape.PointingHandCursor if i >= 0
                           else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._state == "synced":
            i = self._line_at(e.position().y())
            if i >= 0:
                self._override = None
                self.seek_requested.emit(self._times[i])
        super().mouseReleaseEvent(e)

    # ── painting ─────────────────────────────────────────────────────────────
    @staticmethod
    def _mix(a: QColor, b: QColor, t: float) -> QColor:
        return QColor(int(a.red() + (b.red() - a.red()) * t),
                      int(a.green() + (b.green() - a.green()) * t),
                      int(a.blue() + (b.blue() - a.blue()) * t))

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        W, H = self.width(), self.height()
        pad = self._PAD

        if not self._lines:
            f = QFont(self._font)
            f.setPointSizeF(max(10.0, min(15.0, W / 28.0)))
            f.setBold(False)
            p.setFont(f)
            p.setPen(QColor(C.TEXT_DIM))
            msg = self._msg or ""
            p.drawText(QRectF(pad, 0, max(10, W - 2 * pad), H),
                       Qt.AlignmentFlag.AlignCenter.value | Qt.TextFlag.TextWordWrap.value, msg)
            p.end()
            return

        self._ensure_layout()
        iw = max(40, W - 2 * pad)
        f = QFont(self._font)
        f.setPointSizeF(self._font_pt())
        p.setFont(f)

        synced  = self._state == "synced"
        c_act   = QColor(C.WHITE)
        c_dim   = QColor(C.TEXT_MED)
        c_pri   = QColor(C.PRI)
        now     = time.monotonic()

        for i, (_, text) in enumerate(self._lines):
            top, h = self._ys[i] - self._scroll, self._hs[i]
            if top + h < -10 or top > H + 10:
                continue
            e  = self._emph[i] if synced else 0.62
            yc = top + h / 2
            edge = max(0.0, min(1.0, min(yc, H - yc) / (H * 0.16)))
            opacity = (0.38 + 0.62 * e) * (0.22 + 0.78 * edge)

            if not text:                                   # instrumental break → dots
                if synced:
                    for k in range(3):
                        pulse = 0.35 + 0.65 * abs(math.sin(now * 2.2 + k * 0.7)) if e > 0.5 else 0.35
                        col = QColor(c_pri)
                        p.setOpacity(opacity * pulse)
                        p.setPen(Qt.PenStyle.NoPen)
                        p.setBrush(col)
                        p.drawEllipse(QPointF(pad + 8 + k * 14, yc), 3.2, 3.2)
                    p.setBrush(Qt.BrushStyle.NoBrush)
                continue

            s = 0.86 + 0.14 * e
            p.save()
            p.setOpacity(opacity)
            p.translate(pad, yc)
            p.scale(s, s)
            rect = QRectF(0, -h / 2, iw, h)
            if e > 0.05:                                    # faint neon halo
                halo = QColor(c_pri)
                halo.setAlpha(int(70 * e))
                p.setPen(halo)
                for dx, dy in ((-1.6, 0), (1.6, 0), (0, -1.6), (0, 1.6)):
                    p.drawText(rect.translated(dx, dy), self._FLAGS, text)
            p.setPen(self._mix(c_dim, c_act, e))
            p.drawText(rect, self._FLAGS, text)
            p.restore()
        p.end()


class LyricsWindow(QWidget):
    """Independent, frameless, resizable window with synced lyrics.

    • drag it by the header; resize from any edge/corner; 📌-style pin button
      (drawn, no emoji) toggles always-on-top; ✕ hides it;
    • it follows whatever Spotify is playing: MainWindow feeds it every poll
      via on_playback(info) and it interpolates the position locally, so the
      highlight moves smoothly between polls;
    • lyrics come from plugins/_lyrics_client.py (LRCLIB), fetched off-thread.
    """

    seek_requested = pyqtSignal(int)      # ms — MainWindow forwards to Spotify
    closed         = pyqtSignal()
    _lyrics_ready  = pyqtSignal(object, object)   # (track_key, result)

    _EDGE  = 7
    _CUT   = 10
    _MIN_W = 260
    _MIN_H = 240

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("J.A.R.V.I.S. — Lyrics")
        self.setWindowFlags(Qt.WindowType.Window
                            | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setMinimumSize(self._MIN_W, self._MIN_H)
        self.resize(420, 560)

        self._placed    = False
        self._track_key = None
        self._err_t     = 0.0
        self._last_info: dict = {}
        self._base_ms   = 0.0
        self._base_t    = time.monotonic()
        self._playing   = False
        self._drag: QPointF | None = None
        self._resize: tuple | None = None
        self._bg_pm: QPixmap | None = None
        self._bg_key = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 10)
        lay.setSpacing(2)

        # ── header (drag handle) ────────────────────────────────────────────
        self._head = QWidget()
        self._head.setStyleSheet("background: transparent;")
        hv = QVBoxLayout(self._head)
        hv.setContentsMargins(2, 0, 2, 4)
        hv.setSpacing(1)

        top = QHBoxLayout()
        top.setSpacing(6)
        tag = QLabel("◈  LYRICS")
        tag.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        tag.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        top.addWidget(tag, 1)
        self._pin_btn   = HudIconButton("pin")
        self._pin_btn.setCheckable(True)
        self._pin_btn.setChecked(True)
        self._pin_btn.setToolTip("Siempre visible")
        self._close_btn = HudIconButton("close")
        self._close_btn.setToolTip("Close")
        for b in (self._pin_btn, self._close_btn):
            b.setFixedSize(24, 17)
            top.addWidget(b)
        self._pin_btn.toggled.connect(self._set_on_top)
        self._close_btn.clicked.connect(self.close_window)
        hv.addLayout(top)

        self._title_lbl = _ElideLabel("")
        tf = QFont(); tf.setFamilies(["Segoe UI", "SF Pro Display", "Helvetica Neue", "Arial"])
        tf.setPointSize(11); tf.setBold(True)
        self._title_lbl.setFont(tf)
        self._title_lbl.set_color(C.WHITE)
        self._artist_lbl = _ElideLabel("")
        af = QFont(tf); af.setPointSize(9); af.setBold(False)
        self._artist_lbl.setFont(af)
        self._artist_lbl.set_color(C.TEXT_DIM)
        hv.addWidget(self._title_lbl)
        hv.addWidget(self._artist_lbl)
        lay.addWidget(self._head)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};")
        lay.addWidget(sep)

        # ── lyrics canvas + status line ─────────────────────────────────────
        self._view = LyricsView()
        self._view.set_state("idle", "Play a song on Spotify\nto see its lyrics.")
        self._view.seek_requested.connect(self._on_seek)
        lay.addWidget(self._view, 1)

        self._status = _ElideLabel("")
        sf = QFont("Courier New", 6)
        self._status.setFont(sf)
        self._status.set_color(C.TEXT_DIM)
        lay.addWidget(self._status)

        self._lyrics_ready.connect(self._on_lyrics_ready)
        self._tmr = QTimer(self)
        self._tmr.setInterval(25)
        self._tmr.timeout.connect(self._on_tick)

    # ── window chrome ────────────────────────────────────────────────────────
    def _set_on_top(self, on: bool):
        was_visible = self.isVisible()
        geo = self.geometry()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(on))
        if was_visible:
            self.show()
            self.setGeometry(geo)

    def show_window(self):
        if not self._placed:
            self._placed = True
            try:
                scr = QApplication.primaryScreen().availableGeometry()
                self.move(scr.right() - self.width() - 40, scr.top() + 80)
            except Exception:
                pass
        self.show()
        self.raise_()
        self.activateWindow()

    def close_window(self):
        self.hide()
        self.closed.emit()

    def closeEvent(self, e):
        e.ignore()
        self.close_window()

    def showEvent(self, e):
        self._tmr.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._tmr.stop()
        super().hideEvent(e)

    def paintEvent(self, _):
        W, H = self.width(), self.height()
        if W < 8 or H < 8:
            return
        dpr = self.devicePixelRatioF()
        key = (W, H, dpr, C.PRI, C.PRI_DIM, C.DARK)
        if self._bg_pm is None or self._bg_key != key:
            def _draw(p: QPainter):
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                base = _chamfer_path(QRectF(1, 1, W - 2, H - 2), (self._CUT, 0, self._CUT, 0))
                p.fillPath(base, QBrush(qcol(C.DARK, 238)))
                _paint_hud_frame(p, W, H, (self._CUT, 0, self._CUT, 0),
                                 glow=True, tint=0.14, inset=1.5)
                # resize grip hint (bottom-right)
                p.setPen(QPen(QColor(C.PRI_DIM), 1.0))
                for k in (4, 8, 12):
                    p.drawLine(QPointF(W - 5 - k, H - 5), QPointF(W - 5, H - 5 - k))
            self._bg_pm = _render_layer(W, H, dpr, _draw)
            self._bg_key = key
        p = QPainter(self)
        if not p.isActive():
            return
        p.drawPixmap(0, 0, self._bg_pm)
        p.end()

    # ── move / resize by hand (works the same on every OS) ───────────────────
    def _edges_at(self, pos) -> int:
        x, y, E = pos.x(), pos.y(), self._EDGE
        ed = 0
        if x <= E:                 ed |= 1
        if x >= self.width() - E:  ed |= 2
        if y <= E:                 ed |= 4
        if y >= self.height() - E: ed |= 8
        return ed

    _CURSORS = {
        1: Qt.CursorShape.SizeHorCursor,  2: Qt.CursorShape.SizeHorCursor,
        4: Qt.CursorShape.SizeVerCursor,  8: Qt.CursorShape.SizeVerCursor,
        5: Qt.CursorShape.SizeFDiagCursor, 10: Qt.CursorShape.SizeFDiagCursor,
        6: Qt.CursorShape.SizeBDiagCursor, 9: Qt.CursorShape.SizeBDiagCursor,
    }

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            ed = self._edges_at(e.position())
            if ed:
                self._resize = (ed, e.globalPosition(), QRect(self.geometry()))
                e.accept()
                return
            if self._head.geometry().contains(e.position().toPoint()):
                self._drag = e.globalPosition() - QPointF(self.frameGeometry().topLeft())
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                e.accept()
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._resize is not None:
            ed, start, geo = self._resize
            d = e.globalPosition() - start
            dx, dy = int(d.x()), int(d.y())
            g = QRect(geo)
            if ed & 1: g.setLeft(min(geo.left() + dx, geo.right() - self._MIN_W + 1))
            if ed & 2: g.setRight(max(geo.right() + dx, geo.left() + self._MIN_W - 1))
            if ed & 4: g.setTop(min(geo.top() + dy, geo.bottom() - self._MIN_H + 1))
            if ed & 8: g.setBottom(max(geo.bottom() + dy, geo.top() + self._MIN_H - 1))
            self.setGeometry(g)
            e.accept()
            return
        if self._drag is not None:
            self.move((e.globalPosition() - self._drag).toPoint())
            e.accept()
            return
        ed = self._edges_at(e.position())
        self.setCursor(self._CURSORS.get(ed, Qt.CursorShape.ArrowCursor))
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._resize is not None or self._drag is not None:
            self._resize = None
            self._drag = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            e.accept()
            return
        super().mouseReleaseEvent(e)

    # ── playback sync ────────────────────────────────────────────────────────
    def _pos_ms(self) -> float:
        if not self._playing:
            return self._base_ms
        return self._base_ms + (time.monotonic() - self._base_t) * 1000.0

    def _on_tick(self):
        self._view.tick(self._pos_ms())

    def _on_seek(self, ms: int):
        self._base_ms, self._base_t = float(ms), time.monotonic()
        self.seek_requested.emit(int(ms))

    def on_playback(self, info: dict | None):
        """Fed by MainWindow on every Spotify poll (info=None → nothing playing)."""
        now = time.monotonic()
        if not info:
            self._base_ms = self._pos_ms()
            self._base_t  = now
            self._playing = False
            return

        playing = bool(info.get("playing"))
        got = float(info.get("progress_ms") or 0)
        age = max(0.0, now - float(info.get("fetched_at") or now))
        new_now = got + (age * 1000.0 if playing else 0.0)
        est = self._pos_ms()
        key = (info.get("id") or "", info.get("title") or "", info.get("artist") or "")

        if key != self._track_key:
            self._track_key = key
            self._base_ms, self._base_t = new_now, now
            self._playing = playing
            self._start_fetch(info, key)
            return

        if abs(new_now - est) > 450 or playing != self._playing:
            self._base_ms = new_now                     # seek / pause / resume → snap
        else:
            self._base_ms = est + (new_now - est) * 0.35  # tiny drift → ease in
        self._base_t  = now
        self._playing = playing

        # lyrics service was unreachable → try again every ~8 s
        if self._view._state == "error" and now - self._err_t > 8.0:
            self._start_fetch(info, key)

    # ── lyrics fetching ──────────────────────────────────────────────────────
    def _start_fetch(self, info: dict, key):
        title, artist = info.get("title") or "", info.get("artist") or ""
        album, dur = info.get("album") or "", int(info.get("duration_ms") or 0)
        self._title_lbl.setText(title)
        self._artist_lbl.setText(artist)
        self._view.set_state("loading", "Buscando letra…")
        self._status.setText("BUSCANDO  //  LRCLIB")

        def _worker():
            try:
                from plugins import _lyrics_client
                res = _lyrics_client.fetch(title, artist, album, dur)
            except Exception as ex:                       # never kill the thread silently
                res = {"status": "error", "error": str(ex)}
            self._lyrics_ready.emit(key, res)

        threading.Thread(target=_worker, daemon=True).start()

    def _on_lyrics_ready(self, key, res):
        if key != self._track_key:
            return                                        # track changed meanwhile
        st = (res or {}).get("status")
        if st == "synced":
            self._view.set_state("synced", lines=res["synced"])
            self._status.setText("SYNCHRONISED  //  LRCLIB  //  click a line to jump")
        elif st == "plain":
            self._view.set_state("plain", plain=res.get("plain", ""))
            self._status.setText("SIN SINCRONIZAR  //  LRCLIB")
        elif st == "instrumental":
            self._view.set_state("instrumental", "Instrumental")
            self._status.setText("")
        elif st == "notfound":
            self._view.set_state("notfound", "I could not find the lyrics for this song.")
            self._status.setText("SIN RESULTADOS  //  LRCLIB")
        else:
            self._err_t = time.monotonic()
            self._view.set_state("error", "No connection to the lyrics service.\nRetrying…")
            self._status.setText("ERROR  //  LRCLIB")


class SetupOverlay(QWidget):
    done = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            SetupOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)

        detected = {"darwin": "mac", "windows": "windows"}.get(
            _OS.lower(), "linux"
        )
        self._sel_os = detected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 22, 30, 22)
        layout.setSpacing(8)

        def _lbl(txt, font_size=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", font_size,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        layout.addWidget(_lbl("◈  INITIALISATION REQUIRED", 13, True))
        layout.addWidget(_lbl("Configure J.A.R.V.I.S. before first boot.", 9, color=C.PRI_DIM))
        layout.addSpacing(6)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep)
        layout.addSpacing(4)

        layout.addWidget(_lbl("GEMINI API KEY", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        self._key_input = QLineEdit()
        self._key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_input.setPlaceholderText("AIza…")
        self._key_input.setFont(QFont("Courier New", 10))
        self._key_input.setFixedHeight(32)
        self._key_input.setStyleSheet(f"""
            QLineEdit {{
                background: #000d12; color: {C.TEXT};
                border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px;
            }}
            QLineEdit:focus {{ border: 1px solid {C.PRI}; }}
        """)
        layout.addWidget(self._key_input)
        layout.addSpacing(12)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER};"); layout.addWidget(sep2)
        layout.addSpacing(4)

        layout.addWidget(_lbl("OPERATING SYSTEM", 8, color=C.TEXT_DIM,
                               align=Qt.AlignmentFlag.AlignLeft))
        det_name = {"windows": "Windows", "mac": "macOS", "linux": "Linux"}[detected]
        layout.addWidget(_lbl(f"Auto-detected: {det_name}", 8, color=C.ACC2,
                               align=Qt.AlignmentFlag.AlignLeft))

        os_row = QHBoxLayout(); os_row.setSpacing(6)
        self._os_btns: dict[str, QPushButton] = {}
        for key, label in [("windows","⊞  Windows"),("mac","  macOS"),("linux","🐧  Linux")]:
            btn = QPushButton(label)
            btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            btn.setFixedHeight(32)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, k=key: self._sel(k))
            os_row.addWidget(btn)
            self._os_btns[key] = btn
        layout.addLayout(os_row)
        self._sel(detected)
        layout.addSpacing(12)

        init_btn = QPushButton("▸  INITIALISE SYSTEMS")
        init_btn.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        init_btn.setFixedHeight(36)
        init_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        init_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{
                background: {C.PRI_GHO}; border: 1px solid {C.PRI};
            }}
        """)
        init_btn.clicked.connect(self._submit)
        layout.addWidget(init_btn)

    def _sel(self, key: str):
        self._sel_os = key
        pal = {"windows":(C.PRI,"#001a22"),"mac":(C.ACC2,"#1a1400"),"linux":(C.GREEN,"#001a0d")}
        for k, btn in self._os_btns.items():
            if k == key:
                fg, bg = pal[k]
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {fg}; color: {bg};
                        border: none; border-radius: 3px; font-weight: bold;
                    }}
                """)
            else:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: #000d12; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px;
                    }}
                    QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
                """)

    def _submit(self):
        key = self._key_input.text().strip()
        if not key:
            self._key_input.setStyleSheet(
                self._key_input.styleSheet() +
                f" QLineEdit {{ border: 1px solid {C.RED}; }}"
            )
            return
        self.done.emit(key, self._sel_os)


class HueWheel(QWidget):
    """
    Circular colour picker. The user drags the handle (small white circle)
    around the wheel to choose from ALL hues. The filled circle in the centre
    is a live preview of the selected colour.
    """

    hue_picked    = pyqtSignal(str)   # while dragging (live)
    hue_committed = pyqtSignal(str)   # when the handle is released

    _RING = 16   # ring thickness (px)

    def __init__(self, initial_hex: str = DEFAULT_UI_COLOR, parent=None):
        super().__init__(parent)
        self.setFixedSize(148, 148)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hue  = 0.53
        self._drag = False
        self.set_color(initial_hex)

    # ── API ──────────────────────────────────────────────────────────────────
    def color(self) -> str:
        return QColor.fromHsvF(self._hue, 1.0, 1.0).name()

    def set_color(self, hex_str: str):
        c = QColor((hex_str or "").strip())
        if c.isValid() and c.hsvHueF() >= 0:
            self._hue = c.hsvHueF()
            self.update()

    # ── geometry helpers ─────────────────────────────────────────────────────
    def _ring_rect(self) -> QRectF:
        m = self._RING / 2 + 3
        return QRectF(self.rect()).adjusted(m, m, -m, -m)

    def _hue_from_pos(self, pos: QPointF) -> float:
        c  = QRectF(self.rect()).center()
        dx = pos.x() - c.x()
        dy = c.y() - pos.y()          # screen y goes down — flip to math axis
        ang = math.atan2(dy, dx)      # [-π, π], counter-clockwise
        return (ang / (2 * math.pi)) % 1.0

    # ── drawing ──────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect   = self._ring_rect()
        center = rect.center()

        grad = QConicalGradient(center, 0)
        for i in range(0, 361, 20):
            grad.setColorAt(i / 360.0, QColor.fromHsvF((i % 360) / 360.0, 1.0, 1.0))
        p.setPen(QPen(QBrush(grad), self._RING))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(rect)

        # centre preview circle
        preview = QColor.fromHsvF(self._hue, 1.0, 1.0)
        inner   = rect.adjusted(30, 30, -30, -30)
        p.setPen(QPen(qcol(C.BORDER_B), 1))
        p.setBrush(QBrush(preview))
        p.drawEllipse(inner)

        # draggable handle
        r   = rect.width() / 2
        ang = self._hue * 2 * math.pi
        hx  = center.x() + r * math.cos(ang)
        hy  = center.y() - r * math.sin(ang)
        p.setPen(QPen(QColor("#00060a"), 2))
        p.setBrush(QBrush(QColor("#ffffff")))
        p.drawEllipse(QPointF(hx, hy), 7.5, 7.5)
        p.end()

    # ── fare ─────────────────────────────────────────────────────────────────
    def mousePressEvent(self, e):
        self._drag = True
        self._hue  = self._hue_from_pos(e.position())
        self.update()
        self.hue_picked.emit(self.color())

    def mouseMoveEvent(self, e):
        if self._drag:
            self._hue = self._hue_from_pos(e.position())
            self.update()
            self.hue_picked.emit(self.color())

    def mouseReleaseEvent(self, e):
        if self._drag:
            self._drag = False
            self.hue_committed.emit(self.color())


class CustomizeOverlay(QWidget):
    """Floating overlay — change assistant name, user name, UI colour and voice."""

    saved = pyqtSignal(str, str, str, str)   # assistant_name, user_name, ui_color, voice
    _OW, _OH = 400, 588

    def __init__(self, assistant_name="JARVIS", user_name="",
                 ui_color=DEFAULT_UI_COLOR, voice="", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            CustomizeOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 18, 24, 18)
        lay.setSpacing(8)

        def _lbl(txt, fs=9, bold=False, color=C.PRI, align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt); w.setAlignment(align)
            w.setFont(QFont("Courier New", fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            return w

        _fs = (f"QLineEdit {{ background: #000d12; color: {C.TEXT}; "
               f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
               f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")

        lay.addWidget(_lbl("⚙  CUSTOMISE ASSISTANT", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        lay.addWidget(_lbl("ASSISTANT NAME", 8, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._name_input = QLineEdit(assistant_name)
        self._name_input.setFont(QFont("Courier New", 10))
        self._name_input.setFixedHeight(32)
        self._name_input.setStyleSheet(_fs)
        lay.addWidget(self._name_input)

        lay.addSpacing(4)
        lay.addWidget(_lbl("YOUR NAME  (leave blank for default sir / efendim)", 8,
                            color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        self._user_input = QLineEdit(user_name)
        self._user_input.setPlaceholderText("e.g.  Tony   (leave blank for auto)")
        self._user_input.setFont(QFont("Courier New", 10))
        self._user_input.setFixedHeight(32)
        self._user_input.setStyleSheet(_fs)
        lay.addWidget(self._user_input)

        # ── Assistant voice — Gemini prebuilt voices ─────────────────────────
        # Names are language-neutral proper nouns, so the row reads the same in
        # every locale. Selecting one and applying rebuilds the Live session.
        from memory.config_manager import AVAILABLE_VOICES, DEFAULT_VOICE
        lay.addSpacing(4)
        lay.addWidget(_lbl("ASSISTANT VOICE", 8, color=C.TEXT_DIM,
                            align=Qt.AlignmentFlag.AlignLeft))
        self._sel_voice   = (voice or DEFAULT_VOICE)
        if self._sel_voice not in AVAILABLE_VOICES:
            self._sel_voice = DEFAULT_VOICE
        self._voice_btns: dict[str, QPushButton] = {}
        voice_row = QHBoxLayout(); voice_row.setSpacing(4)
        for _v in AVAILABLE_VOICES:
            b = QPushButton(_v)
            b.setCheckable(True)
            b.setFixedHeight(28)
            b.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, name=_v: self._on_voice_pick(name))
            self._voice_btns[_v] = b
            voice_row.addWidget(b)
        lay.addLayout(voice_row)
        self._refresh_voice_btns()

        # ── UI colour — colour wheel ─────────────────────────────────────────
        lay.addSpacing(4)
        clr_hdr = QHBoxLayout()
        clr_hdr.addWidget(_lbl("UI COLOUR  —  drag the handle", 8,
                               color=C.TEXT_DIM, align=Qt.AlignmentFlag.AlignLeft))
        clr_hdr.addStretch()
        df_btn = QPushButton("DEFAULT")
        df_btn.setFixedSize(64, 20)
        df_btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        df_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        df_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        df_btn.clicked.connect(lambda: self._set_color(DEFAULT_UI_COLOR))
        clr_hdr.addWidget(df_btn)
        lay.addLayout(clr_hdr)

        self._initial_color = (ui_color or DEFAULT_UI_COLOR).strip().lower()
        self._sel_color     = self._initial_color
        self.on_preview     = None   # callable(hex) — live preview; MainWindow wires it

        self._wheel = HueWheel(self._sel_color)
        wheel_row = QHBoxLayout()
        wheel_row.addStretch(); wheel_row.addWidget(self._wheel); wheel_row.addStretch()
        lay.addLayout(wheel_row)
        self._wheel.hue_picked.connect(self._on_wheel_pick)
        self._wheel.hue_committed.connect(self._on_wheel_commit)

        self._hex_input = QLineEdit(self._sel_color)
        self._hex_input.setPlaceholderText("#00d4ff   (custom hex colour)")
        self._hex_input.setFont(QFont("Courier New", 10))
        self._hex_input.setFixedHeight(28)
        self._hex_input.setStyleSheet(_fs)
        self._hex_input.textEdited.connect(self._on_hex_edited)
        lay.addWidget(self._hex_input)

        lay.addSpacing(6)
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)

        save_btn = QPushButton("▸  APPLY CHANGES")
        save_btn.setFixedHeight(34)
        save_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(save_btn)

        cancel_btn = QPushButton("CANCEL")
        cancel_btn.setFixedHeight(34)
        cancel_btn.setFont(QFont("Courier New", 9))
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)

    # ── voice selection ──────────────────────────────────────────────────────
    def _on_voice_pick(self, name: str):
        self._sel_voice = name
        self._refresh_voice_btns()

    def _refresh_voice_btns(self):
        """Highlight the selected voice pill; dim the rest."""
        for name, b in self._voice_btns.items():
            on = (name == self._sel_voice)
            b.setChecked(on)
            if on:
                b.setStyleSheet(f"""
                    QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI};
                        border: 1px solid {C.PRI}; border-radius: 3px; }}
                """)
            else:
                b.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.TEXT_MED};
                        border: 1px solid {C.BORDER}; border-radius: 3px; }}
                    QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
                """)

    # ── colour flow ──────────────────────────────────────────────────────────
    def _set_color(self, hx: str, update_wheel: bool = True, preview: bool = True):
        """Updates the selected colour; hex box + wheel stay in sync, theme is live-previewed."""
        self._sel_color = hx.strip().lower()
        self._hex_input.blockSignals(True)
        self._hex_input.setText(self._sel_color)
        self._hex_input.blockSignals(False)
        if update_wheel:
            self._wheel.set_color(self._sel_color)
        if preview and self.on_preview:
            self.on_preview(self._sel_color)

    def _on_wheel_pick(self, hx: str):
        # While dragging: update the hex box, don't apply the theme yet
        self._sel_color = hx
        self._hex_input.blockSignals(True)
        self._hex_input.setText(hx)
        self._hex_input.blockSignals(False)

    def _on_wheel_commit(self, hx: str):
        # Handle released → live-preview the whole interface
        self._set_color(hx, update_wheel=False)

    def _on_hex_edited(self, text: str):
        t = text.strip().lower()
        if t.startswith("#") and len(t) == 7:
            try:
                int(t[1:], 16)
            except ValueError:
                return
            self._set_color(t, update_wheel=True, preview=True)

    def _cancel(self):
        # If a preview was applied, revert to the colour from launch
        if self.on_preview and self._sel_color != self._initial_color:
            self.on_preview(self._initial_color)
        self.hide()

    def _save(self):
        name = self._name_input.text().strip() or "JARVIS"
        user = self._user_input.text().strip()
        self.saved.emit(name, user, self._sel_color or DEFAULT_UI_COLOR, self._sel_voice)
        self.hide()


class PluginManagerOverlay(QWidget):
    """Floating overlay — lists discovered plugins with per-plugin ON/OFF toggles."""

    _OW = 420

    def __init__(self, plugins: list[dict], parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            PluginManagerOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("🧩  PLUGIN MANAGER")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        if not plugins:
            empty = QLabel("No plugins found in /plugins.")
            empty.setFont(QFont("Courier New", 8))
            empty.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(empty)

        for p in plugins:
            lay.addLayout(self._build_row(p))

        lay.addSpacing(4)
        close_btn = QPushButton("CLOSE")
        close_btn.setFixedHeight(30)
        close_btn.setFont(QFont("Courier New", 9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self.hide)
        lay.addWidget(close_btn)
        self.adjustSize()

    def _build_row(self, p: dict) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(6)

        label_text = p["name"] if p["valid"] else f"{p['name']}  (⚠ {p['file']})"
        lbl = QLabel(label_text)
        lbl.setFont(QFont("Courier New", 8))
        lbl.setStyleSheet(f"color: {C.TEXT if p['valid'] else C.TEXT_DIM}; background: transparent;")
        lbl.setToolTip(p["description"] if p["valid"] else p["error"])
        lbl.setWordWrap(False)
        row.addWidget(lbl, stretch=1)

        btn = QPushButton()
        btn.setFixedSize(72, 24)
        btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        if not p["valid"]:
            btn.setText("BROKEN")
            btn.setEnabled(False)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
            """)
        else:
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self._style_toggle(btn, p["enabled"])
            btn.clicked.connect(lambda _, name=p["name"], b=btn: self._toggle(name, b))
        row.addWidget(btn)
        return row

    def _style_toggle(self, btn: QPushButton, enabled: bool):
        if enabled:
            btn.setText("ON")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            btn.setText("OFF")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
            """)

    def _toggle(self, name: str, btn: QPushButton):
        from memory.config_manager import get_plugin_enabled, save_plugin_enabled
        new_val = not get_plugin_enabled(name)
        save_plugin_enabled(name, new_val)
        self._style_toggle(btn, new_val)


class _HudOverlay(QWidget):
    """Base for the floating panels placed by hand over the HUD.

    They are children of the central widget but sit in no layout, so Qt never
    invalidates the region they occupy when they hide or shrink: the HUD keeps
    painting around them and their last frame stays on screen as a ghost. Any
    overlay positioned with _centre_overlay needs this."""

    def hideEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            # Repaint exactly what we were covering, before we stop covering it.
            p.update(self.geometry())
        super().hideEvent(e)

    def closeEvent(self, e):
        p = self.parentWidget()
        if p is not None:
            p.update(self.geometry())
        super().closeEvent(e)


class ConfirmBanner(_HudOverlay):
    """The gate in front of an action that cannot be taken back.

    The old confirmation was a tool parameter the model filled in itself, which
    means it confirmed its own shutdown requests. This is the interface asking,
    and the answer travels from a human finger to core/confirm.py without the
    model in the loop. Nothing blocks while it is up: the assistant keeps
    talking, so this costs no latency — unlike the old gate, which spent two
    tool round trips on every power command."""

    answered = pyqtSignal(bool)
    _OW = 430

    def __init__(self, title: str, detail: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ConfirmBanner {{
                background: rgba(14, 3, 0, 250);
                border: 1px solid {C.ACC};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(8)

        hdr = QLabel("⚠  CONFIRM")
        hdr.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        lay.addWidget(hdr)

        ttl = QLabel(title)
        ttl.setWordWrap(True)
        ttl.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        ttl.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lay.addWidget(ttl)

        if detail:
            dtl = QLabel(detail)
            dtl.setWordWrap(True)
            dtl.setFont(QFont("Courier New", 8))
            dtl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            lay.addWidget(dtl)

        row = QHBoxLayout(); row.setSpacing(8)

        yes = QPushButton("▸  CONFIRM")
        yes.setFixedHeight(32)
        yes.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        yes.setCursor(Qt.CursorShape.PointingHandCursor)
        yes.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.ACC};
                border: 1px solid {C.ACC}; border-radius: 3px; }}
            QPushButton:hover {{ background: rgba(255,107,0,40); }}
        """)
        yes.clicked.connect(lambda: self.answered.emit(True))
        row.addWidget(yes)

        no = QPushButton("CANCEL")
        no.setFixedHeight(32)
        no.setFont(QFont("Courier New", 9))
        no.setCursor(Qt.CursorShape.PointingHandCursor)
        no.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        no.clicked.connect(lambda: self.answered.emit(False))
        row.addWidget(no)
        lay.addLayout(row)

        # Default focus on CANCEL: if someone hits Enter without reading, the
        # safe answer wins.
        no.setDefault(True)
        no.setFocus()


class AudioDeviceOverlay(_HudOverlay):
    """Choose which microphone JARVIS listens to and which speakers it uses.

    Both audio streams used to open with no `device=` at all, so they always
    took the OS default — which on Windows moves by itself the moment a headset
    is plugged in. 'JARVIS can't hear me' is usually 'JARVIS is listening to the
    webcam'."""

    picked = pyqtSignal()      # emitted after Apply, when something changed
    _OW = 460

    def __init__(self, parent=None):
        super().__init__(parent)
        from core.audio_devices import list_devices, DEFAULT_LABEL
        from memory.config_manager import get_input_device, get_output_device

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            AudioDeviceOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(6)

        hdr = QLabel("🎧  AUDIO DEVICES")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        lay.addWidget(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        _combo_css = (
            f"QComboBox {{ background: #000d12; color: {C.TEXT}; "
            f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
            f"QComboBox:hover {{ border-color: {C.BORDER_B}; }}"
            f"QComboBox QAbstractItemView {{ background: #000d12; color: {C.TEXT}; "
            f"selection-background-color: {C.PRI_GHO}; border: 1px solid {C.BORDER}; }}"
        )

        def _row(label: str, kind: str, current: str) -> QComboBox:
            cap = QLabel(label)
            cap.setFont(QFont("Courier New", 8))
            cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            lay.addWidget(cap)

            box = QComboBox()
            box.setFont(QFont("Courier New", 9))
            box.setFixedHeight(30)
            box.setStyleSheet(_combo_css)
            # The list is served from a cache warmed on a background thread at
            # startup, so opening this panel never blocks the Qt thread on the
            # host audio API.
            box.addItem(DEFAULT_LABEL, "")
            for name in list_devices(kind):
                box.addItem(name, name)
            idx = box.findData(current) if current else 0
            box.setCurrentIndex(idx if idx >= 0 else 0)
            if current and idx < 0:
                # Saved device is not plugged in right now. Show it rather than
                # silently resetting the user's choice to default.
                box.addItem(f"{current}  (not connected)", current)
                box.setCurrentIndex(box.count() - 1)
            lay.addWidget(box)
            return box

        self._in_box  = _row("MICROPHONE — what JARVIS hears you with",
                             "input", get_input_device())
        lay.addSpacing(4)
        self._out_box = _row("SPEAKERS — what JARVIS talks through",
                             "output", get_output_device())

        note = QLabel("Applying reconnects the session. Your conversation is kept.")
        note.setWordWrap(True)
        note.setFont(QFont("Courier New", 7))
        note.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        lay.addSpacing(6)
        lay.addWidget(note)

        row = QHBoxLayout(); row.setSpacing(8)
        ok = QPushButton("▸  APPLY")
        ok.setFixedHeight(32)
        ok.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        ok.setCursor(Qt.CursorShape.PointingHandCursor)
        ok.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """)
        ok.clicked.connect(self._apply)
        row.addWidget(ok)

        cancel = QPushButton("CLOSE")
        cancel.setFixedHeight(32)
        cancel.setFont(QFont("Courier New", 9))
        cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        cancel.clicked.connect(self.hide)
        row.addWidget(cancel)
        lay.addLayout(row)

    def _apply(self):
        from memory.config_manager import (
            get_input_device, get_output_device,
            save_input_device, save_output_device,
        )
        new_in  = self._in_box.currentData()  or ""
        new_out = self._out_box.currentData() or ""
        changed = (new_in != get_input_device()) or (new_out != get_output_device())
        save_input_device(new_in)
        save_output_device(new_out)
        self.hide()
        # Only rebuild the session if something actually moved — a no-op Apply
        # should not cost a reconnect.
        if changed:
            self.picked.emit()


class MemoryOverlay(_HudOverlay):
    """Everything JARVIS has stored about you, and when it learned it.

    Memory used to be a 2200-character store that deleted its oldest entries
    when full and mentioned it only on stdout. The cap is gone; this panel is
    the other half of that change — a memory you cannot inspect is a memory you
    cannot trust, and 'delete' has to be something the person can do."""

    _OW = 520

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            MemoryOverlay {{
                background: rgba(0, 6, 10, 246);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._OW)

        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(20, 16, 20, 16)
        self._lay.setSpacing(5)
        self._rebuild()

    def _clear_layout(self):
        """Take every item out of the layout and detach it from the widget tree
        in this call.

        deleteLater() on its own is not enough: it queues destruction for the
        next event-loop pass, and until then the old rows are still children of
        this widget and still paint — which is what drew half of the previous
        panel over the new one. setParent(None) removes them from the tree now;
        deleteLater() then frees them safely."""
        while self._lay.count():
            item = self._lay.takeAt(0)
            w = item.widget()
            if w is not None:
                # hide() stops it painting in this frame; deleteLater() frees it
                # safely afterwards. setParent(None) would also stop the paint,
                # but it turns the widget into a top-level window for the moment
                # between the two calls, which is not something to leave lying
                # around inside a click handler.
                w.hide()
                w.deleteLater()
                continue
            sub = item.layout()
            if sub is not None:
                while sub.count():
                    si = sub.takeAt(0)
                    sw = si.widget()
                    if sw is not None:
                        sw.hide()
                        sw.deleteLater()
                sub.deleteLater()

    def _settle(self, before):
        """Size the panel to its content, re-centre it, and repaint what the old
        size covered.

        The re-size has to happen here rather than at the end of _rebuild
        because Qt has not polished the freshly-created children at that point,
        so the size hint it would read is the empty-layout one. Measured: a
        first adjustSize() returned 32 px for a panel whose content needed 155,
        and a second call — after the same widgets had been through the event
        loop — returned 155. So this runs twice: once now, once on the next
        turn, from _rebuild.

        The re-centre and the repaint are needed because the overlay is placed
        by hand and is in no layout: shrinking it leaves it off-centre and
        leaves its former pixels on screen, since nothing tells the parent that
        region changed. The repaint has to cover the union of the old and new
        rectangles."""
        self._lay.invalidate()
        self._lay.activate()
        self.updateGeometry()
        self.adjustSize()

        p = self.parentWidget()
        if p is None:
            self.update()
            return
        self.move(max(0, (p.width()  - self.width())  // 2),
                  max(0, (p.height() - self.height()) // 2))
        p.update(before.united(self.geometry()))
        self.update()

    def _rebuild(self):
        before = self.geometry()
        self._clear_layout()

        from memory.memory_manager import all_entries_for_ui

        hdr = QLabel("🧠  WHAT JARVIS REMEMBERS")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._lay.addWidget(hdr)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        self._lay.addWidget(sep)

        rows = all_entries_for_ui()

        cap = QLabel(f"{len(rows)} stored facts — newest first. "
                     f"Nothing here is sent anywhere; it lives in "
                     f"memory/long_term.json on this machine.")
        cap.setWordWrap(True)
        cap.setFont(QFont("Courier New", 7))
        cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._lay.addWidget(cap)

        if not rows:
            empty = QLabel("Nothing stored yet.")
            empty.setFont(QFont("Courier New", 9))
            empty.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            self._lay.addWidget(empty)
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFixedHeight(min(420, 34 * len(rows) + 10))
            scroll.setStyleSheet(
                f"QScrollArea {{ border: 1px solid {C.BORDER}; border-radius: 3px; "
                f"background: transparent; }}"
            )
            inner = QWidget()
            ilay  = QVBoxLayout(inner)
            ilay.setContentsMargins(6, 6, 6, 6)
            ilay.setSpacing(3)

            for r in rows:
                line = QHBoxLayout(); line.setSpacing(6)
                txt = QLabel(f"<b>{r['key'].replace('_', ' ')}</b> "
                             f"<span style='color:{C.TEXT_MED}'>— {r['value']}</span>")
                txt.setWordWrap(True)
                txt.setFont(QFont("Courier New", 8))
                txt.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
                line.addWidget(txt, 1)

                meta = QLabel(f"{r['category'][:4]} · {r['updated'] or '—'}")
                meta.setFont(QFont("Courier New", 7))
                meta.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
                line.addWidget(meta)

                rm = QPushButton("✕")
                rm.setFixedSize(20, 20)
                rm.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
                rm.setCursor(Qt.CursorShape.PointingHandCursor)
                rm.setToolTip("Forget this")
                rm.setStyleSheet(f"""
                    QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                        border: 1px solid {C.BORDER}; border-radius: 3px; }}
                    QPushButton:hover {{ color: {C.RED}; border-color: {C.RED}; }}
                """)
                rm.clicked.connect(
                    lambda _=False, c=r["category"], k=r["key"]: self._forget(c, k))
                line.addWidget(rm)

                holder = QWidget()
                holder.setLayout(line)
                ilay.addWidget(holder)

            ilay.addStretch()
            scroll.setWidget(inner)
            self._lay.addWidget(scroll)

        close = QPushButton("CLOSE")
        close.setFixedHeight(30)
        close.setFont(QFont("Courier New", 9))
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close.clicked.connect(self.hide)
        self._lay.addWidget(close)

        self._settle(before)
        # …and again once Qt has polished the new children, because the size
        # hint is not final until then. Harmless when the first pass already
        # got it right: _settle is idempotent.
        QTimer.singleShot(0, lambda g=before: self._settle(g))

    def _forget(self, category: str, key: str):
        from memory.memory_manager import forget
        forget(key, category)
        # Rebuild on the NEXT event-loop turn, not inside this click handler.
        # The rebuild destroys the very ✕ button that emitted this signal, and
        # Qt is entitled to touch the sender after a slot returns; tearing it
        # down mid-emission is how a widget ends up half-alive on screen.
        QTimer.singleShot(0, self._rebuild)


class ClipboardPanel(QWidget):
    """Floating panel shown when text is copied — offers quick Jarvis actions."""

    action_requested = pyqtSignal(str)
    _W, _H = 326, 112

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            ClipboardPanel {{
                background: rgba(0, 8, 14, 248);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self.setFixedWidth(self._W)
        self._clip_text = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 7)
        lay.setSpacing(4)

        hdr = QHBoxLayout(); hdr.setSpacing(4)
        icon_lbl = QLabel("◈  CLIPBOARD DETECTED")
        icon_lbl.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        icon_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent;")
        hdr.addWidget(icon_lbl); hdr.addStretch()
        x_btn = QPushButton("✕")
        x_btn.setFixedSize(16, 16)
        x_btn.setFont(QFont("Courier New", 8))
        x_btn.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent; border: none;")
        x_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        x_btn.clicked.connect(self.hide)
        hdr.addWidget(x_btn)
        lay.addLayout(hdr)

        self._preview = QLabel()
        self._preview.setFont(QFont("Courier New", 8))
        self._preview.setStyleSheet(f"""
            color: {C.TEXT}; background: {C.PANEL2};
            border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 6px;
        """)
        self._preview.setWordWrap(False)
        self._preview.setFixedHeight(28)
        lay.addWidget(self._preview)

        btn_row = QHBoxLayout(); btn_row.setSpacing(4)
        _bs = (f"QPushButton {{ background: {C.PANEL2}; color: {C.TEXT_MED}; "
               f"border: 1px solid {C.BORDER}; border-radius: 2px; }}"
               f"QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}")
        for label, cmd_fmt in [
            ("TRANSLATE", "Translate this text to English: {text}"),
            ("SUMMARISE", "Summarise this: {text}"),
            ("EXPLAIN",   "Explain this: {text}"),
            ("FIX",       "Fix grammar and spelling: {text}"),
        ]:
            b = QPushButton(label)
            b.setFixedHeight(22)
            b.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(_bs)
            b.clicked.connect(lambda _, c=cmd_fmt: self._trigger(c))
            btn_row.addWidget(b)
        lay.addLayout(btn_row)

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.hide)
        self.hide()

    def _trigger(self, cmd_fmt: str):
        if self._clip_text:
            self.action_requested.emit(cmd_fmt.format(text=self._clip_text[:800]))
        self.hide()

    def show_clipboard(self, text: str):
        self._clip_text = text
        preview = text[:58].replace('\n', ' ')
        if len(text) > 58:
            preview += "…"
        self._preview.setText(f'"{preview}"')
        self.show(); self.raise_()
        self._dismiss_timer.start(8000)


class PluginSettingsOverlay(QWidget):
    """Floating overlay — renders per-plugin settings forms.

    Fully generic: it iterates the settings schemas a plugin declared via its
    PLUGIN_SETTINGS constant (delivered by PluginRegistry.settings_schemas) and
    builds a form for each. It knows NOTHING about any specific plugin, so the
    core stays clean and plugins remain pure drop-in — install a plugin that
    declares fields (e.g. the 3D-printer suite) and its section appears here;
    install none and this panel simply says there's nothing to configure.
    """

    _test_done = pyqtSignal(str, bool, str)   # namespace, ok, message
    _OW = 460

    def __init__(self, sections: list[dict], parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            PluginSettingsOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self._sections = sections or []
        self._widgets: dict[tuple, object] = {}    # (namespace, key) -> input widget
        self._types:   dict[tuple, str]    = {}     # (namespace, key) -> field type
        self._status_labels: dict[str, QLabel] = {} # namespace -> status QLabel
        self._test_done.connect(self._on_test_done)

        self._fs = (f"QLineEdit {{ background: #000d12; color: {C.TEXT}; "
                    f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
                    f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 16)
        root.setSpacing(8)

        root.addWidget(self._lbl("⚙  PLUGIN SETTINGS", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        root.addWidget(sep)

        if not self._sections:
            root.addWidget(self._lbl(
                "No configurable plugins are installed.\nDrop a plugin that needs "
                "settings (like the 3D-printer suite) into the plugins folder and "
                "it will show up here.", 9, color=C.TEXT_DIM))
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setStyleSheet("QScrollArea { background: transparent; }")
            inner = QWidget()
            inner.setStyleSheet("background: transparent;")
            form = QVBoxLayout(inner)
            form.setContentsMargins(0, 0, 6, 0)
            form.setSpacing(6)
            for sec in self._sections:
                self._build_section(form, sec)
            form.addStretch(1)
            scroll.setWidget(inner)
            root.addWidget(scroll, 1)

        # ── bottom buttons ───────────────────────────────────────────────────
        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        if self._sections:
            save_btn = QPushButton("▸  SAVE")
            save_btn.setFixedHeight(34)
            save_btn.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            save_btn.setStyleSheet(f"""
                QPushButton {{ background: transparent; color: {C.PRI};
                    border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
                QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
            """)
            save_btn.clicked.connect(self._save_all)
            btn_row.addWidget(save_btn)

        close_btn = QPushButton("CLOSE")
        close_btn.setFixedHeight(34)
        close_btn.setFont(QFont("Courier New", 9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self.hide)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ── helpers ───────────────────────────────────────────────────────────────
    def _lbl(self, txt, fs=9, bold=False, color=C.PRI,
             align=Qt.AlignmentFlag.AlignLeft):
        w = QLabel(txt); w.setAlignment(align); w.setWordWrap(True)
        w.setFont(QFont("Courier New", fs,
                        QFont.Weight.Bold if bold else QFont.Weight.Normal))
        w.setStyleSheet(f"color: {color}; background: transparent;")
        return w

    def _build_section(self, form: QVBoxLayout, sec: dict):
        ns     = sec.get("namespace") or sec.get("plugin") or "plugin"
        title  = sec.get("title") or ns
        fields = sec.get("fields") or []
        values = sec.get("values") or {}

        form.addSpacing(4)
        form.addWidget(self._lbl(title, 10, True, C.PRI))

        for field in fields:
            if not isinstance(field, dict) or not field.get("key"):
                continue
            key   = field["key"]
            ftype = (field.get("type") or "text").lower()
            label = field.get("label") or key
            default = field.get("default")
            stored  = values.get(key, default)

            form.addWidget(self._lbl(label.upper(), 8, color=C.TEXT_DIM))

            if ftype == "choice":
                w = QComboBox()
                w.addItems([str(o) for o in field.get("options", [])])
                w.setFont(QFont("Courier New", 9))
                w.setFixedHeight(30)
                w.setStyleSheet(
                    f"QComboBox {{ background: #000d12; color: {C.TEXT}; "
                    f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 2px 8px; }}"
                    f"QComboBox QAbstractItemView {{ background: #000d12; color: {C.TEXT}; "
                    f"selection-background-color: {C.PRI_GHO}; }}")
                if stored is not None:
                    w.setCurrentText(str(stored))
            elif ftype == "toggle":
                w = QPushButton()
                w.setCheckable(True)
                w.setChecked(bool(stored))
                w.setFixedHeight(28)
                w.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
                w.setCursor(Qt.CursorShape.PointingHandCursor)
                self._style_toggle(w)
                w.toggled.connect(lambda _=False, b=w: self._style_toggle(b))
            else:  # text / password
                w = QLineEdit("" if stored is None else str(stored))
                w.setFont(QFont("Courier New", 10))
                w.setFixedHeight(30)
                w.setStyleSheet(self._fs)
                if field.get("placeholder"):
                    w.setPlaceholderText(str(field["placeholder"]))
                if ftype == "password":
                    w.setEchoMode(QLineEdit.EchoMode.Password)

            self._widgets[(ns, key)] = w
            self._types[(ns, key)]   = ftype
            form.addWidget(w)

        # optional test/connect action button + status line
        action = sec.get("action")
        if isinstance(action, dict) and callable(action.get("run")):
            form.addSpacing(2)
            ab = QPushButton(str(action.get("label") or "TEST"))
            ab.setFixedHeight(30)
            ab.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
            ab.setCursor(Qt.CursorShape.PointingHandCursor)
            ab.setStyleSheet(f"""
                QPushButton {{ background: #00091a; color: {C.PRI};
                    border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
                QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
            """)
            ab.clicked.connect(lambda _=False, n=ns: self._run_action(n))
            form.addWidget(ab)

        status = self._lbl("", 8, color=C.TEXT_DIM)
        self._status_labels[ns] = status
        form.addWidget(status)

        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"color: {C.BORDER}; margin: 4px 0;")
        form.addWidget(line)

    def _style_toggle(self, btn: QPushButton):
        on = btn.isChecked()
        btn.setText("ON" if on else "OFF")
        if on:
            btn.setStyleSheet(f"QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI}; "
                              f"border: 1px solid {C.PRI}; border-radius: 3px; }}")
        else:
            btn.setStyleSheet(f"QPushButton {{ background: transparent; color: {C.TEXT_MED}; "
                              f"border: 1px solid {C.BORDER}; border-radius: 3px; }}")

    # ── data ──────────────────────────────────────────────────────────────────
    def _gather(self, ns: str) -> dict:
        out = {}
        for (n, key), w in self._widgets.items():
            if n != ns:
                continue
            t = self._types.get((n, key), "text")
            if t == "choice":
                out[key] = w.currentText()
            elif t == "toggle":
                out[key] = w.isChecked()
            else:
                out[key] = w.text().strip()
        return out

    def _save_ns(self, ns: str):
        from memory.config_manager import save_plugin_config
        save_plugin_config(ns, self._gather(ns))

    def _save_all(self):
        for sec in self._sections:
            ns = sec.get("namespace") or sec.get("plugin")
            if ns:
                self._save_ns(ns)
                lbl = self._status_labels.get(ns)
                if lbl:
                    lbl.setText("Saved ✓")
                    lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")

    def _run_action(self, ns: str):
        sec = next((s for s in self._sections
                    if (s.get("namespace") or s.get("plugin")) == ns), None)
        if not sec:
            return
        run_fn = (sec.get("action") or {}).get("run")
        if not callable(run_fn):
            return
        self._save_ns(ns)                 # persist what the user typed before testing
        values = self._gather(ns)
        lbl = self._status_labels.get(ns)
        if lbl:
            lbl.setText("Testing…")
            lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")

        def worker():
            try:
                res = run_fn(values)
                if isinstance(res, tuple) and len(res) == 2:
                    ok, msg = bool(res[0]), str(res[1])
                else:
                    ok, msg = bool(res), str(res)
            except Exception as e:
                ok, msg = False, str(e)
            self._test_done.emit(ns, ok, msg)

        threading.Thread(target=worker, daemon=True).start()

    def _on_test_done(self, ns: str, ok: bool, msg: str):
        lbl = self._status_labels.get(ns)
        if not lbl:
            return
        lbl.setText(msg)
        color = C.PRI if ok else "#ff6b6b"
        lbl.setStyleSheet(f"color: {color}; background: transparent;")


class MailAccountsOverlay(QWidget):
    """Settings panel for mail: lists the connected accounts (with delete),
    adds new ones after testing the connection, and holds the switch that puts
    unread mail into the morning briefing. Talks to core/mail_store.py and
    core/mail_client.py directly — no plugin schema involved, because the number
    of accounts is dynamic."""

    _test_done = pyqtSignal(bool, str, object)   # ok, message, payload (add args)
    _OW = 480

    def __init__(self, parent=None):
        super().__init__(parent)
        from core import mail_store
        self._store = mail_store
        self._provider_keys = ["gmail", "icloud", "outlook", "other"]
        self._busy = False
        self._confirm_del: str | None = None     # account id awaiting a second click
        self._last_payload: dict | None = None   # args of the last FAILED test, for "save anyway"

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            MailAccountsOverlay {{
                background: rgba(0, 6, 10, 245);
                border: 1px solid {C.BORDER_B};
                border-radius: 6px;
            }}
        """)
        self._test_done.connect(self._on_test_done)

        self._fs = (f"QLineEdit {{ background: #000d12; color: {C.TEXT}; "
                    f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 4px 8px; }}"
                    f"QLineEdit:focus {{ border: 1px solid {C.PRI}; }}")
        self._cs = (f"QComboBox {{ background: #000d12; color: {C.TEXT}; "
                    f"border: 1px solid {C.BORDER}; border-radius: 3px; padding: 2px 8px; }}"
                    f"QComboBox QAbstractItemView {{ background: #000d12; color: {C.TEXT}; "
                    f"selection-background-color: {C.PRI_GHO}; }}")

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 16, 22, 16)
        root.setSpacing(8)
        root.addWidget(self._lbl("✉  CORREO · CUENTAS", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        root.addWidget(sep)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        form = QVBoxLayout(inner)
        form.setContentsMargins(0, 0, 6, 0)
        form.setSpacing(6)

        # ── connected accounts ────────────────────────────────────────────
        form.addWidget(self._lbl("CUENTAS CONECTADAS", 10, True))
        self._list_box = QVBoxLayout()
        self._list_box.setSpacing(4)
        form.addLayout(self._list_box)
        self._line(form)

        # ── add account ───────────────────────────────────────────────────
        form.addWidget(self._lbl("ADD ACCOUNT", 10, True))

        form.addWidget(self._lbl("NAME", 8, color=C.TEXT_DIM))
        self._f_label = QLineEdit()
        self._f_label.setFont(QFont("Courier New", 10)); self._f_label.setFixedHeight(30)
        self._f_label.setStyleSheet(self._fs)
        self._f_label.setPlaceholderText("e.g. Personal, School, iCloud")
        form.addWidget(self._f_label)

        form.addWidget(self._lbl("PROVEEDOR", 8, color=C.TEXT_DIM))
        self._f_prov = QComboBox()
        self._f_prov.addItems([self._store.PROVIDERS[k]["name"] for k in self._provider_keys])
        self._f_prov.setFont(QFont("Courier New", 9)); self._f_prov.setFixedHeight(30)
        self._f_prov.setStyleSheet(self._cs)
        self._f_prov.currentIndexChanged.connect(self._on_provider)
        form.addWidget(self._f_prov)
        self._hint = self._lbl("", 8, color=C.TEXT_DIM)
        form.addWidget(self._hint)

        form.addWidget(self._lbl("EMAIL ADDRESS", 8, color=C.TEXT_DIM))
        self._f_addr = QLineEdit()
        self._f_addr.setFont(QFont("Courier New", 10)); self._f_addr.setFixedHeight(30)
        self._f_addr.setStyleSheet(self._fs)
        form.addWidget(self._f_addr)

        form.addWidget(self._lbl("APP PASSWORD", 8, color=C.TEXT_DIM))
        self._f_pass = QLineEdit()
        self._f_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self._f_pass.setFont(QFont("Courier New", 10)); self._f_pass.setFixedHeight(30)
        self._f_pass.setStyleSheet(self._fs)
        form.addWidget(self._f_pass)

        # servers: only for "Otro"
        self._adv = QWidget(); self._adv.setStyleSheet("background: transparent;")
        al = QVBoxLayout(self._adv); al.setContentsMargins(0, 0, 0, 0); al.setSpacing(4)

        def _row(label, widget):
            al.addWidget(self._lbl(label, 8, color=C.TEXT_DIM)); al.addWidget(widget)

        def _edit(ph=""):
            e = QLineEdit(); e.setFont(QFont("Courier New", 10)); e.setFixedHeight(28)
            e.setStyleSheet(self._fs); e.setPlaceholderText(ph); return e

        self._f_imap_host = _edit("imap.tuproveedor.com"); self._f_imap_port = _edit("993")
        self._f_smtp_host = _edit("smtp.tuproveedor.com"); self._f_smtp_port = _edit("465")
        self._f_sec = QComboBox(); self._f_sec.addItems(["ssl", "starttls"])
        self._f_sec.setFont(QFont("Courier New", 9)); self._f_sec.setFixedHeight(28)
        self._f_sec.setStyleSheet(self._cs)
        _row("SERVIDOR IMAP", self._f_imap_host); _row("PUERTO IMAP", self._f_imap_port)
        _row("SERVIDOR SMTP", self._f_smtp_host); _row("PUERTO SMTP", self._f_smtp_port)
        _row("SEGURIDAD SMTP", self._f_sec)
        form.addWidget(self._adv)

        self._add_btn = self._button("▸  TEST AND ADD", self._add, strong=True)
        form.addWidget(self._add_btn)
        self._force_btn = self._button("SAVE ANYWAY", self._save_anyway)
        self._force_btn.hide()
        form.addWidget(self._force_btn)
        self._status = self._lbl("", 8, color=C.TEXT_DIM)
        form.addWidget(self._status)
        form.addWidget(self._lbl(
            "To change an account's password, add it again with the same name.",
            7, color=C.TEXT_DIM))
        self._line(form)

        # ── options ───────────────────────────────────────────────────────
        form.addWidget(self._lbl("MORNING SUMMARY", 10, True))
        form.addWidget(self._lbl("Include unread emails when Jarvis is opened in the morning", 8,
                                 color=C.TEXT_DIM))
        self._brief_btn = QPushButton()
        self._brief_btn.setCheckable(True)
        self._brief_btn.setChecked(self._store.get_brief_mail_enabled())
        self._brief_btn.setFixedHeight(28)
        self._brief_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._brief_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._style_toggle(self._brief_btn)
        self._brief_btn.toggled.connect(self._on_brief_toggled)
        form.addWidget(self._brief_btn)

        where = ("system keyring" if self._store.keyring_available()
                 else "config/mail_accounts.json (install «keyring» to use the system keyring)")
        form.addWidget(self._lbl(f"Passwords are stored in: {where}", 7, color=C.TEXT_DIM))

        form.addStretch(1)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        close_btn = QPushButton("CLOSE")
        close_btn.setFixedHeight(34)
        close_btn.setFont(QFont("Courier New", 9))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{ background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px; }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self.hide)
        root.addWidget(close_btn)

        self._on_provider(0)
        self._refresh_list()

    # ── small builders ────────────────────────────────────────────────────
    def _lbl(self, txt, fs=9, bold=False, color=C.PRI):
        w = QLabel(txt); w.setWordWrap(True)
        w.setFont(QFont("Courier New", fs, QFont.Weight.Bold if bold else QFont.Weight.Normal))
        w.setStyleSheet(f"color: {color}; background: transparent;")
        return w

    def _line(self, layout):
        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"color: {C.BORDER}; margin: 4px 0;")
        layout.addWidget(line)

    def _button(self, text, slot, strong=False):
        b = QPushButton(text)
        b.setFixedHeight(32 if strong else 28)
        b.setFont(QFont("Courier New", 9 if strong else 8, QFont.Weight.Bold))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(f"""
            QPushButton {{ background: {'transparent' if strong else '#00091a'}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px; }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
            QPushButton:disabled {{ color: {C.TEXT_DIM}; border-color: {C.BORDER}; }}
        """)
        b.clicked.connect(slot)
        return b

    def _style_toggle(self, btn: QPushButton):
        on = btn.isChecked()
        btn.setText("ON" if on else "OFF")
        if on:
            btn.setStyleSheet(f"QPushButton {{ background: {C.PRI_GHO}; color: {C.PRI}; "
                              f"border: 1px solid {C.PRI}; border-radius: 3px; }}")
        else:
            btn.setStyleSheet(f"QPushButton {{ background: transparent; color: {C.TEXT_MED}; "
                              f"border: 1px solid {C.BORDER}; border-radius: 3px; }}")

    def _set_status(self, text: str, ok: bool | None = None):
        color = C.TEXT_DIM if ok is None else (C.PRI if ok else "#ff6b6b")
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {color}; background: transparent;")

    # ── accounts list ─────────────────────────────────────────────────────
    def _refresh_list(self):
        while self._list_box.count():
            item = self._list_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        accts = self._store.list_accounts()
        if not accts:
            self._list_box.addWidget(self._lbl("No account connected yet.", 9, color=C.TEXT_DIM))
            return
        for a in accts:
            row = QWidget()
            row.setStyleSheet(f"background: #000d12; border: 1px solid {C.BORDER}; border-radius: 3px;")
            hl = QHBoxLayout(row); hl.setContentsMargins(8, 6, 8, 6); hl.setSpacing(8)
            prov = self._store.PROVIDERS.get(a.get("provider", "other"), {}).get("name", "")
            info = QLabel(f"{a['label']}\n{a['address']}  ·  {prov}")
            info.setFont(QFont("Courier New", 8))
            info.setStyleSheet(f"color: {C.TEXT}; background: transparent; border: none;")
            hl.addWidget(info, 1)
            armed = (self._confirm_del == a["id"])
            btn = QPushButton("SURE? DELETE" if armed else "DELETE")
            btn.setFixedHeight(26)
            btn.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            col = C.RED
            btn.setStyleSheet(f"""
                QPushButton {{ background: {'#2a0610' if armed else 'transparent'}; color: {col};
                    border: 1px solid {col if armed else C.BORDER}; border-radius: 3px; padding: 0 8px; }}
                QPushButton:hover {{ border-color: {col}; }}
            """)
            btn.clicked.connect(lambda _=False, i=a["id"]: self._on_delete(i))
            hl.addWidget(btn)
            self._list_box.addWidget(row)

    def _on_delete(self, account_id: str):
        if self._confirm_del != account_id:
            self._confirm_del = account_id          # first click: arm
            self._set_status("Press again to delete the account and its password.", None)
            self._refresh_list()
            return
        self._confirm_del = None
        ok = self._store.remove_account(account_id)
        self._set_status("Account deleted." if ok else "Could not delete the account.", ok)
        self._refresh_list()

    # ── add flow ──────────────────────────────────────────────────────────
    def _provider_key(self) -> str:
        return self._provider_keys[max(0, self._f_prov.currentIndex())]

    def _on_provider(self, _idx: int):
        key = self._provider_key()
        self._hint.setText(self._store.PROVIDERS[key]["hint"])
        self._adv.setVisible(key == "other")

    def _payload(self) -> dict:
        def _int(e, default):
            try:
                return int(e.text().strip() or default)
            except ValueError:
                return default
        key = self._provider_key()
        d = self._store.PROVIDERS[key]
        return {
            "label": self._f_label.text().strip(),
            "provider": key,
            "address": self._f_addr.text().strip(),
            "password": self._f_pass.text(),
            "imap_host": self._f_imap_host.text().strip() if key == "other" else d["imap_host"],
            "imap_port": _int(self._f_imap_port, d["imap_port"]) if key == "other" else d["imap_port"],
            "smtp_host": self._f_smtp_host.text().strip() if key == "other" else d["smtp_host"],
            "smtp_port": _int(self._f_smtp_port, d["smtp_port"]) if key == "other" else d["smtp_port"],
            "smtp_security": self._f_sec.currentText() if key == "other" else d["smtp_security"],
        }

    def _add(self):
        if self._busy:
            return
        p = self._payload()
        pw = p["password"].strip().replace(" ", "")
        if not p["label"]:
            return self._set_status("Enter an account name.", False)
        if "@" not in p["address"]:
            return self._set_status("The email address does not look valid.", False)
        if not pw:
            return self._set_status("The app password is missing.", False)
        if not p["imap_host"] or not p["smtp_host"]:
            return self._set_status("IMAP/SMTP servers are missing.", False)

        self._busy = True
        self._force_btn.hide()
        self._add_btn.setEnabled(False)
        self._add_btn.setText("TESTING CONNECTION…")
        self._set_status("Connecting to the server…", None)

        def worker():
            from core import mail_client
            try:
                ok, msg = mail_client.test_connection(p, pw)
            except Exception as e:
                ok, msg = False, str(e)
            self._test_done.emit(ok, msg, p)

        threading.Thread(target=worker, daemon=True, name="mail-test").start()

    def _commit(self, p: dict) -> bool:
        try:
            self._store.add_account(
                p["label"], p["provider"], p["address"], p["password"],
                p["imap_host"], p["imap_port"], p["smtp_host"], p["smtp_port"], p["smtp_security"])
        except ValueError as e:
            self._set_status(str(e), False)
            return False
        self._f_label.clear(); self._f_addr.clear(); self._f_pass.clear()
        self._confirm_del = None
        self._last_payload = None
        self._force_btn.hide()
        self._refresh_list()
        return True

    def _on_test_done(self, ok: bool, msg: str, payload):
        self._busy = False
        self._add_btn.setEnabled(True)
        self._add_btn.setText("▸  TEST AND ADD")
        if ok:
            if self._commit(payload):
                self._set_status("✓ Account added. " + msg, True)
        else:
            self._last_payload = payload
            self._force_btn.show()
            self._set_status("✗ Not added. " + msg, False)

    def _save_anyway(self):
        if self._last_payload and self._commit(self._last_payload):
            self._set_status("Account saved without verification. If it fails when used, check the details.", None)

    def _on_brief_toggled(self, on: bool):
        self._style_toggle(self._brief_btn)
        self._store.save_brief_mail_enabled(on)


class RemoteKeyOverlay(QWidget):
    """Floating overlay — QR code for instant phone pairing + manual key fallback."""

    closed = pyqtSignal()

    _OW, _OH = 400, 465

    def __init__(self, url: str, key: str, auto_login_url: str = "",
                 manual_url: str = "", expiry_secs: int = 600, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            RemoteKeyOverlay {{
                background: rgba(0, 4, 12, 0.95);
                border: 1px solid {C.BORDER_B};
                border-radius: 14px;
            }}
        """)
        self._expiry          = time.time() + expiry_secs
        self._on_new_key      = None
        self._auto_login_url  = auto_login_url
        self._manual_url      = manual_url or url

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 16, 24, 16)
        lay.setSpacing(5)

        def _lbl(txt, fs=9, bold=False, color=C.PRI,
                 align=Qt.AlignmentFlag.AlignCenter):
            w = QLabel(txt)
            w.setAlignment(align)
            w.setFont(QFont("Courier New", fs,
                            QFont.Weight.Bold if bold else QFont.Weight.Normal))
            w.setStyleSheet(f"color: {color}; background: transparent;")
            w.setWordWrap(True)
            return w

        lay.addWidget(_lbl("◈  REMOTE ACCESS", 12, True))
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep)

        # ── QR code ───────────────────────────────────────────────────────────
        self._qr_label = QLabel()
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setFixedSize(176, 176)
        self._qr_label.setStyleSheet(
            "background: white; border-radius: 10px; padding: 4px;"
        )
        qr_row = QHBoxLayout()
        qr_row.addStretch()
        qr_row.addWidget(self._qr_label)
        qr_row.addStretch()
        lay.addLayout(qr_row)

        self._update_qr(auto_login_url)

        lay.addWidget(_lbl("Scan with phone camera to connect instantly", 8, color=C.TEXT_DIM))

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 1px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_lbl("Or enter manually:", 7, color=C.TEXT_DIM,
                           align=Qt.AlignmentFlag.AlignLeft))

        self._url_lbl = QLabel(self._manual_url)
        self._url_lbl.setFont(QFont("Courier New", 8))
        self._url_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        self._url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._url_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self._url_lbl)

        self._key_lbl = QLabel(key)
        self._key_lbl.setFont(QFont("Courier New", 28, QFont.Weight.Bold))
        self._key_lbl.setStyleSheet(f"""
            color: {C.ACC};
            background: {C.PANEL2};
            border: 1px solid {C.BORDER_B};
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 10px;
        """)
        self._key_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._key_lbl)

        self._timer_lbl = QLabel()
        self._timer_lbl.setFont(QFont("Courier New", 8))
        self._timer_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._timer_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._timer_lbl)

        btn_row = QHBoxLayout(); btn_row.setSpacing(8)
        new_btn = QPushButton("NEW KEY")
        new_btn.setFixedHeight(32)
        new_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        new_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        new_btn.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL}; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 5px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border: 1px solid {C.PRI}; }}
        """)
        new_btn.clicked.connect(self._refresh_key)
        btn_row.addWidget(new_btn)

        close_btn = QPushButton("DISMISS")
        close_btn.setFixedHeight(32)
        close_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
        """)
        close_btn.clicked.connect(self._do_close)
        btn_row.addWidget(close_btn)
        lay.addLayout(btn_row)

        self._ctimer = QTimer(self)
        self._ctimer.timeout.connect(self._tick)
        self._ctimer.start(1000)
        self._tick()

    def set_new_key_callback(self, fn) -> None:
        self._on_new_key = fn

    def _update_qr(self, url: str) -> None:
        if not url:
            self._qr_label.setText("—")
            return
        try:
            import qrcode as _qrmod
            from io import BytesIO
            qr = _qrmod.QRCode(
                box_size=5, border=2,
                error_correction=_qrmod.constants.ERROR_CORRECT_M,
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = BytesIO()
            img.save(buf, format="PNG")
            px = QPixmap()
            px.loadFromData(buf.getvalue())
            self._qr_label.setPixmap(
                px.scaled(170, 170,
                          Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )
        except ImportError:
            self._qr_label.setText("pip install\nqrcode[pil]")
            self._qr_label.setFont(QFont("Courier New", 8))
            self._qr_label.setStyleSheet(
                "color: #888; background: white; border-radius: 10px; padding: 4px;"
            )
        except Exception:
            self._qr_label.setText(url[:28])
            self._qr_label.setFont(QFont("Courier New", 7))
            self._qr_label.setStyleSheet(
                f"color: {C.PRI}; background: white; border-radius: 10px; padding: 4px;"
            )

    def _tick(self):
        remaining = max(0, int(self._expiry - time.time()))
        m, s = divmod(remaining, 60)
        self._timer_lbl.setText(f"Key expires in  {m:02d}:{s:02d}")
        if remaining == 0:
            self._do_close()

    def mark_connected(self) -> None:
        """Call from any thread when a phone successfully connects."""
        self._ctimer.stop()
        self._key_lbl.setText("CONNECTED")
        self._key_lbl.setStyleSheet(f"""
            color: {C.GREEN};
            background: rgba(34,197,94,0.08);
            border: 2px solid rgba(34,197,94,0.4);
            border-radius: 8px;
            padding: 6px 4px;
            letter-spacing: 4px;
        """)
        self._qr_label.setText("✓")
        self._qr_label.setFont(QFont("Courier New", 54, QFont.Weight.Bold))
        self._qr_label.setStyleSheet(
            "color: #00ff88; background: #001a0d; border-radius: 10px;"
        )
        self._timer_lbl.setText("Phone connected — JARVIS ready")
        self._timer_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent;")

    def _refresh_key(self):
        if self._on_new_key:
            result = self._on_new_key()
            if result:
                url    = result[0]
                key    = result[1]
                auto   = result[2] if len(result) >= 3 else ""
                manual = result[3] if len(result) >= 4 else url
                self._manual_url     = manual or url
                self._url_lbl.setText(self._manual_url)
                self._key_lbl.setText(key)
                self._auto_login_url = auto
                self._update_qr(auto or url)
                self._expiry = time.time() + 600
                self._key_lbl.setStyleSheet(f"""
                    color: {C.ACC};
                    background: {C.PANEL2};
                    border: 1px solid {C.BORDER_B};
                    border-radius: 8px;
                    padding: 6px 4px;
                    letter-spacing: 10px;
                """)
                self._timer_lbl.setStyleSheet(
                    f"color: {C.TEXT_MED}; background: transparent;"
                )
                self._ctimer.start(1000)
                self._tick()

    def _do_close(self):
        self._ctimer.stop()
        self.hide()
        self.closed.emit()


class MainWindow(QMainWindow):
    _log_sig        = pyqtSignal(str)
    _state_sig      = pyqtSignal(str)
    _content_sig    = pyqtSignal(str, str)   # (title, text) — thread-safe content display
    _reconfig_sig   = pyqtSignal()           # trigger setup overlay from any thread
    _camera_sig     = pyqtSignal(bytes)      # show camera frame preview (small overlay)
    _cam_stream_sig = pyqtSignal(bool)       # True=start live stream, False=stop
    _cam_frame_sig  = pyqtSignal(bytes)      # live camera frame → HUD area
    _clipboard_sig  = pyqtSignal(str)        # clipboard text changed (thread-safe)
    _confirm_sig    = pyqtSignal(str, str)   # (title, detail) — irreversible-action gate
    _confirm_hide_sig = pyqtSignal()
    _wake_dl_sig    = pyqtSignal(bool, str)  # wake-word install finished (ok, message)
    _quiz_sig       = pyqtSignal(str, object, object)  # (topic, questions, grader)
    _quiz_hide_sig  = pyqtSignal()
    _review_sig     = pyqtSignal(str, str, object, object)  # document review payload
    _spotify_sig    = pyqtSignal(object)     # now_playing() dict, or None → mini-player update
    _globe_flyto_sig = pyqtSignal(float, float, str)   # voice-triggered globe navigation
    _show_images_sig = pyqtSignal(object, object)      # (sources, titles) — thread-safe image display
    _show_videos_sig = pyqtSignal(object, object)      # (sources, titles) — thread-safe video display
    _show_lyrics_sig = pyqtSignal()                    # thread-safe: open the synced-lyrics window
    _show_mail_sig   = pyqtSignal(object)              # (messages) — thread-safe mail reader window
    _show_osint_sig  = pyqtSignal(object)              # (prefill dict) — thread-safe OSINT HUD panel
    _radar_sig       = pyqtSignal(str, object)         # (command, payload) — market radar, thread-safe

    def __init__(self, face_path: str):
        super().__init__()
        self._face_path = face_path

        # Load customization from config
        _cfg = _read_full_config()
        self._assistant_name: str = (_cfg.get("assistant_name") or "JARVIS").strip()
        _display = self._assistant_name.upper()

        # Apply the saved UI colour BEFORE panels/stylesheets are built
        _ui_color = (_cfg.get("ui_color") or "").strip()
        if _ui_color and _ui_color.lower() != DEFAULT_UI_COLOR:
            apply_ui_accent(_ui_color)

        self.setWindowTitle(f"{_display} — {APP_VERSION}")
        self.setMinimumSize(_MIN_W, _MIN_H)
        self.resize(_DEFAULT_W, _DEFAULT_H)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(
            (screen.width()  - _DEFAULT_W) // 2,
            (screen.height() - _DEFAULT_H) // 2,
        )

        self.on_text_command   = None
        self.on_remote_clicked = None   # callable: () -> (url, key) | None
        self.on_interrupt      = None   # callable: () -> None — stop JARVIS mid-speech
        self.on_voice_change   = None   # callable: () -> None — rebuild session with new voice
        self.on_audio_device_change = None  # callable: () -> None — reopen audio streams
        self._confirm_overlay  = None   # live ConfirmBanner, if one is on screen
        self.get_plugins       = None   # callable: () -> list[dict], set by JarvisLive
        self.get_plugin_settings = None # callable: () -> list[dict] settings schemas, set by JarvisLive
        self.on_wake_toggle    = None   # callable: (enable: bool) -> str, set by JarvisLive
        self.on_wake_manual    = None   # callable: () -> None — manual sleep/wake
        self.on_push_to_talk   = None   # callable: (enable: bool) -> str scope
        self.ptt_hold          = None   # callable: (held: bool) -> None — windowed chord
        self.wake_get_state    = None   # callable: () -> dict {enabled, awake, ready}
        self._muted            = False
        self._current_file: str | None = None
        self._remote_overlay: RemoteKeyOverlay | None = None
        self._customize_overlay: CustomizeOverlay | None = None
        self._image_windows: list[FloatingImageWindow] = []
        self._video_windows: list[FloatingVideoWindow] = []
        self._mail_reader: MailReaderWindow | None = None
        self._osint_win = None
        self._mail_accounts_overlay = None

        central = HudBackdrop()
        central.setStyleSheet(hud_qss())
        self.setCentralWidget(central)

        # Connector layer for floating images — sits above the HUD, below any
        # floating image window (those get raised above it as they're opened).
        self._wire_layer = _ImageWireLayer(central)
        self._wire_layer.setGeometry(central.rect())
        self._wire_layer.show()

        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(10, 0, 10, 4)
        body.setSpacing(8)

        self._left_panel = self._build_left_panel()
        body.addWidget(self._left_panel, stretch=0)

        # Center column: HUD + resizable content panel via QSplitter
        self.hud = HudCanvas(face_path, _display)
        self.hud.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._wire_layer.set_anchor_getter(
            lambda: QPointF(self.hud.mapTo(self.centralWidget(), self.hud.core_anchor().toPoint()))
        )
        self._content_panel = self._build_content_panel()
        self._quiz_panel = self._build_quiz_panel()

        # Live camera container — replaces HUD when camera stream is active
        _cam_cont = QWidget()
        _cam_cont.setStyleSheet("background: #000308;")
        _cam_v = QVBoxLayout(_cam_cont)
        _cam_v.setContentsMargins(0, 0, 0, 0)
        _cam_v.setSpacing(0)
        _cam_hdr = QHBoxLayout()
        _cam_hdr.setContentsMargins(8, 5, 8, 5)
        _cam_title = QLabel("◈  CAMERA FEED")
        _cam_title.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        _cam_title.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        _cam_hdr.addWidget(_cam_title)
        _cam_hdr.addStretch()
        _cam_x = QPushButton("✕  CLOSE")
        _cam_x.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        _cam_x.setCursor(Qt.CursorShape.PointingHandCursor)
        _cam_x.setStyleSheet(f"""
            QPushButton {{
                color: {C.TEXT_DIM}; background: transparent;
                border: none; padding: 2px 6px;
            }}
            QPushButton:hover {{ color: {C.PRI}; }}
        """)
        _cam_x.clicked.connect(self.stop_camera_stream)
        _cam_hdr.addWidget(_cam_x)
        _cam_v.addLayout(_cam_hdr)
        self._cam_live_lbl = QLabel()
        self._cam_live_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cam_live_lbl.setStyleSheet("background: transparent;")
        self._cam_live_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        _cam_v.addWidget(self._cam_live_lbl, stretch=1)

        # Stack: 0 = animated HUD, 1 = live camera
        self._hud_cam_stack = QStackedWidget()
        self._hud_cam_stack.addWidget(self.hud)
        self._hud_cam_stack.addWidget(_cam_cont)

        self._center_split = QSplitter(Qt.Orientation.Vertical)
        self._center_split.setStyleSheet(f"""
            QSplitter::handle {{
                background: transparent;
                height: 6px;
            }}
            QSplitter::handle:hover {{
                background: {C.PRI_GHO};
            }}
        """)
        self._center_split.addWidget(self._hud_cam_stack)
        self._center_split.addWidget(self._content_panel)
        self._center_split.addWidget(self._quiz_panel)
        self._center_split.setStretchFactor(0, 3)
        self._center_split.setStretchFactor(1, 1)
        self._center_split.setCollapsible(0, False)
        # Centre column framed in a bevelled glass container. Margins >= chamfer
        # so the square HUD / globe / video corners stay inside the outline.
        self._center_frame = HudFrame(cuts=(22, 8, 22, 8), glow=True, tint=0.05)
        _cf = QVBoxLayout(self._center_frame)
        _cf.setContentsMargins(14, 14, 14, 14)
        _cf.setSpacing(0)
        _cf.addWidget(self._center_split)
        body.addWidget(self._center_frame, stretch=5)

        self._right_panel = self._build_right_panel()
        body.addWidget(self._right_panel, stretch=0)

        root.addLayout(body, stretch=1)
        root.addWidget(self._build_footer())

        # Quick-access drawer (floating overlay, built after central widget layout is done)
        self._quick_drawer = self._build_quick_drawer()
        self._update_autostart_btn(self._check_autostart())
        from memory.config_manager import get_brief_enabled as _gbe
        self._update_brief_btn(_gbe())

        self._clock_tmr = QTimer(self)
        self._clock_tmr.timeout.connect(self._tick_clock)
        self._clock_tmr.start(1000)
        self._tick_clock()

        # Metric update timer
        self._metric_tmr = QTimer(self)
        self._metric_tmr.timeout.connect(self._update_metrics)
        self._metric_tmr.start(2000)
        self._update_metrics()

        self._log_sig.connect(self._log.append_log)
        self._state_sig.connect(self._apply_state)
        self._content_sig.connect(self._show_content)
        self._reconfig_sig.connect(self._show_setup)
        self._camera_sig.connect(self._show_camera_frame)
        self._confirm_sig.connect(self._show_confirm_banner)
        self._confirm_hide_sig.connect(self._hide_confirm_banner)
        self._cam_stream_sig.connect(self._on_cam_stream)
        self._cam_frame_sig.connect(self._on_cam_frame)
        self._clipboard_sig.connect(self._show_clipboard_panel)
        self._wake_dl_sig.connect(self._on_wake_install_done)
        self._quiz_sig.connect(self._show_quiz)
        self._quiz_hide_sig.connect(self._hide_quiz)
        self._review_sig.connect(self._show_review)
        self._cam_stop = threading.Event()

        # Camera preview overlay (child of central widget, positioned in resizeEvent)
        self._cam_preview = _CameraPreview(self.centralWidget())

        # Clipboard panel (child of central widget, bottom-center)
        self._clipboard_panel = ClipboardPanel(self.centralWidget())
        self._clipboard_panel.action_requested.connect(self._on_clipboard_action)
        QApplication.clipboard().dataChanged.connect(self._on_clipboard_changed)

        # Spotify signals + background poll wired to the sidebar widget
        # (_spotify_player itself is created inside _build_left_panel)
        self._spotify_sig.connect(self._on_spotify_polled)
        self._spotify_art_url = None
        self._spotify_last_info = None
        self._lyrics_win: LyricsWindow | None = None
        self._spotify_tmr = QTimer(self)
        self._spotify_tmr.timeout.connect(self._poll_spotify)
        self._spotify_tmr.start(3000)

        self._globe_flyto_sig.connect(self._on_globe_flyto)
        self._radar_sig.connect(self._on_radar_cmd)
        _radar = getattr(self.hud, "_radar", None)
        if _radar is not None:
            try:
                from memory.config_manager import get_market_radar_enabled
                _radar.apply_enabled(get_market_radar_enabled())
                _radar.enabledChanged.connect(self._on_radar_hidden)
            except Exception as e:
                print(f"[Radar] Initial state: {e}")
        self._show_images_sig.connect(self.show_floating_images)
        self._show_videos_sig.connect(self.show_floating_videos)
        self._show_lyrics_sig.connect(self._open_lyrics)
        self._show_mail_sig.connect(self.show_mail_reader)
        self._show_osint_sig.connect(self.show_osint_panel)

        self._overlay: SetupOverlay | None = None
        self._ready = self._check_config()
        if not self._ready:
            self._show_setup()

        sc_mute = QShortcut(QKeySequence("F4"), self)
        sc_mute.activated.connect(self._toggle_mute)
        sc_full = QShortcut(QKeySequence("F11"), self)
        sc_full.activated.connect(self._toggle_fullscreen)
        sc_intr = QShortcut(QKeySequence("Escape"), self)
        sc_intr.activated.connect(self._do_interrupt)

    def _show_camera_frame(self, img_bytes: bytes):
        """Slot — display camera preview overlay (main thread)."""
        self._cam_preview.show_frame(img_bytes)
        cw = self.centralWidget()
        pw = _CameraPreview._W
        ph = self._cam_preview.height()
        self._cam_preview.setGeometry(
            cw.width() - _RIGHT_W - pw - 12,
            cw.height() - ph - 28,
            pw, ph,
        )

    # --- Live camera stream in HUD area ------------------------------------
    def _on_cam_stream(self, start: bool) -> None:
        if start:
            self._hud_cam_stack.setCurrentIndex(1)
        else:
            self._hud_cam_stack.setCurrentIndex(0)
            self._cam_live_lbl.clear()

    def _on_cam_frame(self, data: bytes) -> None:
        px = QPixmap()
        px.loadFromData(data)
        if not px.isNull():
            w, h = self._cam_live_lbl.width(), self._cam_live_lbl.height()
            if w > 1 and h > 1:
                self._cam_live_lbl.setPixmap(
                    px.scaled(w, h,
                              Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
                )

    def start_camera_stream(self) -> None:
        self._cam_stop.clear()
        self._cam_stream_sig.emit(True)
        t = threading.Thread(target=self._cam_loop, daemon=True, name="cam-stream")
        t.start()

    def _cam_loop(self) -> None:
        try:
            import cv2
            # Reuse camera index detected by screen_processor (cached in api_keys.json)
            cam_idx = 0
            try:
                import json as _j
                cfg = _j.loads((CONFIG_DIR / "api_keys.json").read_text())
                cam_idx = int(cfg.get("camera_index", 0))
            except Exception:
                pass
            try:
                backend = cv2.CAP_DSHOW if _OS == "Windows" else cv2.CAP_ANY
            except AttributeError:
                backend = 0
            cap = cv2.VideoCapture(cam_idx, backend)
            if not cap.isOpened():
                cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                return
            # warm-up frames
            for _ in range(5):
                cap.read()
            while not self._cam_stop.wait(0.033) and cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 65])
                    self._cam_frame_sig.emit(buf.tobytes())
            cap.release()
        except Exception as e:
            print(f"[Camera] Stream error: {e}")
        finally:
            self._cam_stream_sig.emit(False)

    def stop_camera_stream(self) -> None:
        self._cam_stop.set()

    # ------------------------------------------------------------------
    # Icon generation — arc-reactor style, rendered with Pillow
    # ------------------------------------------------------------------
    @staticmethod
    def _build_jarvis_icon(out_path: Path) -> bool:
        """
        Render a JARVIS arc-reactor icon at 4× resolution and downsample
        for crisp results at all sizes. Saves a multi-res .ico to out_path.
        Returns True on success.
        """
        try:
            import math
            import PIL.Image
            import PIL.ImageDraw
            import PIL.ImageFilter
        except ImportError:
            return False

        CYAN   = (0, 212, 255)
        DIM    = (0, 100, 140)
        DARK   = (0, 6, 10)
        GLOW   = (0, 160, 200)
        WHITE  = (220, 240, 255)

        def _render(sz: int) -> PIL.Image.Image:
            S  = sz * 4                     # draw at 4× then downscale
            img = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            d   = PIL.ImageDraw.Draw(img)
            cx = cy = S // 2

            # ── filled background circle ──────────────────────────────────
            R = S // 2 - 2
            d.ellipse([cx-R, cy-R, cx+R, cy+R], fill=(*DARK, 255))

            # ── outer border ring ─────────────────────────────────────────
            lw = max(2, S // 40)
            d.ellipse([cx-R, cy-R, cx+R, cy+R],
                      outline=(*CYAN, 220), width=lw)

            # ── mid decorative ring ───────────────────────────────────────
            R2 = int(R * 0.72)
            d.ellipse([cx-R2, cy-R2, cx+R2, cy+R2],
                      outline=(*DIM, 180), width=max(1, lw // 2))

            # ── 6 radial spokes (hex bolt) ────────────────────────────────
            R_inner = int(R * 0.30)
            R_outer = int(R * 0.62)
            spoke_w = max(1, S // 80)
            for i in range(6):
                angle = math.radians(i * 60 - 30)
                x1 = cx + int(R_inner * math.cos(angle))
                y1 = cy + int(R_inner * math.sin(angle))
                x2 = cx + int(R_outer * math.cos(angle))
                y2 = cy + int(R_outer * math.sin(angle))
                d.line([x1, y1, x2, y2], fill=(*GLOW, 200), width=spoke_w)

            # ── 6 tick marks on outer ring ────────────────────────────────
            for i in range(6):
                angle = math.radians(i * 60)
                for dr in range(lw * 2):
                    rx = (R - lw - dr)
                    d.point(
                        [cx + int(rx * math.cos(angle)),
                         cy + int(rx * math.sin(angle))],
                        fill=(*WHITE, 220),
                    )

            # ── inner glowing ring ────────────────────────────────────────
            Ri = int(R * 0.26)
            d.ellipse([cx-Ri, cy-Ri, cx+Ri, cy+Ri],
                      outline=(*CYAN, 255), width=max(2, lw))

            # ── bright glow soft blur applied before core ─────────────────
            # (draw a slightly larger cyan circle on a separate layer)
            glow_layer = PIL.Image.new("RGBA", (S, S), (0, 0, 0, 0))
            gd = PIL.ImageDraw.Draw(glow_layer)
            Rc = int(R * 0.13)
            gd.ellipse([cx-Rc*2, cy-Rc*2, cx+Rc*2, cy+Rc*2],
                       fill=(*CYAN, 110))
            glow_layer = glow_layer.filter(PIL.ImageFilter.GaussianBlur(S // 14))
            img = PIL.Image.alpha_composite(img, glow_layer)
            d   = PIL.ImageDraw.Draw(img)

            # ── core dot ──────────────────────────────────────────────────
            d.ellipse([cx-Rc, cy-Rc, cx+Rc, cy+Rc], fill=(*WHITE, 255))

            # ── downscale to target size ──────────────────────────────────
            return img.resize((sz, sz), PIL.Image.LANCZOS)

        try:
            sizes  = [256, 128, 64, 48, 32, 16]
            frames = [_render(s) for s in sizes]
            frames[0].save(
                out_path,
                format="ICO",
                append_images=frames[1:],
                sizes=[(s, s) for s in sizes],
            )
            return True
        except Exception as e:
            print(f"[Shortcut] ⚠️  Icon generation failed: {e}")
            return False

    @staticmethod
    def _create_lnk_windows(lnk: str, target: str, args: str,
                             work_dir: str, icon_loc: str) -> None:
        """
        Create a Windows .lnk shortcut WITHOUT launching PowerShell or cmd.
        Tries win32com (pywin32) first; falls back to wscript.exe + VBScript.
        wscript.exe is a GUI-mode host — it never opens a console window.
        """
        # ── Option 1: pywin32 (pure Python COM, zero subprocess) ──────────
        try:
            from win32com.client import Dispatch   # type: ignore
            sh = Dispatch("WScript.Shell")
            sc = sh.CreateShortCut(lnk)
            sc.TargetPath       = target
            sc.Arguments        = f'"{args}"'
            sc.WorkingDirectory = work_dir
            sc.Description      = "J.A.R.V.I.S AI Assistant"
            sc.IconLocation     = icon_loc
            sc.save()
            return
        except ImportError:
            pass

        # ── Option 2: wscript.exe + VBScript (always available on Windows,
        #    GUI-mode executable — never opens a console window) ────────────
        vbs = "\n".join([
            'Set ws = CreateObject("WScript.Shell")',
            f'Set sc = ws.CreateShortcut("{lnk}")',
            f'sc.TargetPath = "{target}"',
            f'sc.Arguments = Chr(34) & "{args}" & Chr(34)',
            f'sc.WorkingDirectory = "{work_dir}"',
            'sc.Description = "J.A.R.V.I.S AI Assistant"',
            f'sc.IconLocation = "{icon_loc}"',
            'sc.Save',
        ])
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=".vbs")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(vbs)
            proc = subprocess.Popen(
                ["wscript.exe", "/nologo", tmp],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
            proc.wait(timeout=10)
        finally:
            try:
                os.unlink(tmp)
            except Exception:
                pass

    @staticmethod
    def _get_desktop_dir() -> Path:
        """
        Resolve the user's REAL desktop directory instead of assuming
        ~/Desktop, which breaks when:
          • OneDrive "Known Folder Move" relocates the desktop
            (C:/Users/x/OneDrive/Desktop) — very common on Win 10/11;
          • the XDG desktop is localized on Linux (~/Masaüstü,
            ~/Schreibtisch, ~/Bureau, …).
        Falls back to ~/Desktop only as a last resort.
        """
        home = Path.home()
        _os = platform.system()

        if _os == "Windows":
            # ── 1) SHGetKnownFolderPath(FOLDERID_Desktop) — the canonical
            #       answer; follows OneDrive redirection. No dependencies. ──
            try:
                import ctypes
                from ctypes import wintypes

                class _GUID(ctypes.Structure):
                    _fields_ = [("Data1", wintypes.DWORD),
                                ("Data2", wintypes.WORD),
                                ("Data3", wintypes.WORD),
                                ("Data4", ctypes.c_ubyte * 8)]

                # FOLDERID_Desktop {B4BFCC3A-DB2C-424C-B029-7FE99A87C641}
                fid = _GUID(0xB4BFCC3A, 0xDB2C, 0x424C,
                            (ctypes.c_ubyte * 8)(0xB0, 0x29, 0x7F, 0xE9,
                                                 0x9A, 0x87, 0xC6, 0x41))
                buf = ctypes.c_wchar_p()
                if ctypes.windll.shell32.SHGetKnownFolderPath(
                        ctypes.byref(fid), 0, None, ctypes.byref(buf)) == 0:
                    p = Path(buf.value)
                    ctypes.windll.ole32.CoTaskMemFree(buf)
                    if p.is_dir():
                        return p
            except Exception:
                pass

            # ── 2) Registry: User Shell Folders (may contain %VARS%) ──────
            try:
                import winreg
                with winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        r"Software\Microsoft\Windows\CurrentVersion"
                        r"\Explorer\User Shell Folders") as key:
                    val, _t = winreg.QueryValueEx(key, "Desktop")
                p = Path(os.path.expandvars(val))
                if p.is_dir():
                    return p
            except Exception:
                pass

        elif _os == "Linux":
            # ── xdg-user-dir honours localized names (~/Masaüstü, …) ──────
            try:
                out = subprocess.run(["xdg-user-dir", "DESKTOP"],
                                     capture_output=True, text=True, timeout=5)
                p = Path(out.stdout.strip())
                if out.stdout.strip() and p != home and p.is_dir():
                    return p
            except Exception:
                pass
            try:
                cfg = home / ".config" / "user-dirs.dirs"
                for line in cfg.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("XDG_DESKTOP_DIR"):
                        val = line.split("=", 1)[1].strip().strip('"')
                        p = Path(val.replace("$HOME", str(home)))
                        if p != home and p.is_dir():
                            return p
            except Exception:
                pass

        # macOS: ~/Desktop is always the real path (localization is
        # display-only). Everything else lands here as a last resort.
        return home / "Desktop"

    def _create_desktop_shortcut(self):
        """
        Create a desktop shortcut on Windows / macOS / Linux.
        Never opens a terminal, console, or PowerShell window on any platform.
        """
        import stat as _stat
        script  = Path(__file__).resolve().parent / "main.py"
        python  = Path(sys.executable)
        desktop = self._get_desktop_dir()

        # Arc-reactor icon (.ico — also exported as .png for Linux/macOS)
        ico_path = Path(__file__).resolve().parent / "config" / "jarvis.ico"
        if not ico_path.exists():
            self._build_jarvis_icon(ico_path)

        try:
            _os = platform.system()

            # ── Windows ───────────────────────────────────────────────────────
            if _os == "Windows":
                pythonw  = python.parent / "pythonw.exe"
                target   = str(pythonw if pythonw.exists() else python)
                lnk      = str(desktop / "J.A.R.V.I.S.lnk")
                icon_loc = str(ico_path) if ico_path.exists() else f"{target},0"
                self._create_lnk_windows(lnk, target, str(script),
                                         str(script.parent), icon_loc)

            # ── macOS — proper .app bundle (no Terminal window) ───────────────
            elif _os == "Darwin":
                app     = desktop / "J.A.R.V.I.S.app"
                mac_dir = app / "Contents" / "MacOS"
                res_dir = app / "Contents" / "Resources"
                mac_dir.mkdir(parents=True, exist_ok=True)
                res_dir.mkdir(exist_ok=True)

                # Launcher executable (bash — runs as background process,
                # macOS does NOT open Terminal for executables inside .app bundles)
                launcher = mac_dir / "JARVIS"
                launcher.write_text(
                    "#!/usr/bin/env bash\n"
                    f'cd "{script.parent}"\n'
                    f'exec "{python}" "{script}"\n'
                )
                launcher.chmod(launcher.stat().st_mode
                               | _stat.S_IEXEC | _stat.S_IXGRP | _stat.S_IXOTH)

                # Minimal Info.plist (required for .app recognition)
                (app / "Contents" / "Info.plist").write_text(
                    '<?xml version="1.0" encoding="UTF-8"?>\n'
                    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                    '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                    '<plist version="1.0"><dict>\n'
                    '  <key>CFBundleExecutable</key><string>JARVIS</string>\n'
                    '  <key>CFBundleIdentifier</key>'
                    '<string>com.jarvis.assistant</string>\n'
                    '  <key>CFBundleName</key><string>J.A.R.V.I.S</string>\n'
                    '  <key>CFBundlePackageType</key><string>APPL</string>\n'
                    '  <key>CFBundleVersion</key><string>1.0</string>\n'
                    '</dict></plist>\n'
                )

                # Optional: copy icon as .icns (skip silently if Pillow is missing)
                try:
                    import PIL.Image
                    icns = res_dir / "AppIcon.icns"
                    PIL.Image.open(ico_path).save(icns, format="ICNS")
                    # Inject icon reference into plist
                    plist = app / "Contents" / "Info.plist"
                    txt = plist.read_text()
                    plist.write_text(
                        txt.replace(
                            '</dict></plist>',
                            '  <key>CFBundleIconFile</key>'
                            '<string>AppIcon</string>\n</dict></plist>\n',
                        )
                    )
                except Exception:
                    pass  # icon is optional

            # ── Linux — .desktop file (Terminal=false, no console) ────────────
            else:
                # Export .ico → .png for better desktop integration
                png_path = ico_path.with_suffix(".png")
                if not png_path.exists() and ico_path.exists():
                    try:
                        import PIL.Image
                        PIL.Image.open(ico_path).resize(
                            (256, 256), PIL.Image.LANCZOS
                        ).save(png_path, format="PNG")
                    except Exception:
                        png_path = ico_path  # fallback to .ico

                icon_line = f"Icon={png_path}\n" if png_path.exists() else ""
                desk = desktop / "J.A.R.V.I.S.desktop"
                desk.write_text(
                    "[Desktop Entry]\n"
                    "Name=J.A.R.V.I.S\n"
                    f"Exec={python} {script}\n"
                    f"Path={script.parent}\n"
                    "Type=Application\n"
                    "Terminal=false\n"
                    "Categories=Utility;\n"
                    + icon_line
                )
                desk.chmod(desk.stat().st_mode | 0o755)

            self._log.append_log("SYS: Desktop shortcut created.")
        except Exception as e:
            self._log.append_log(f"ERR: Shortcut failed — {e}")

    def _toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cw = self.centralWidget()
        self._wire_layer.setGeometry(cw.rect())
        if self._overlay and self._overlay.isVisible():
            ow, oh = 460, 390
            self._overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._remote_overlay and self._remote_overlay.isVisible():
            ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
            self._remote_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        if self._customize_overlay and self._customize_overlay.isVisible():
            ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
            self._customize_overlay.setGeometry(
                (cw.width()  - ow) // 2,
                (cw.height() - oh) // 2,
                ow, oh,
            )
        # Camera preview — bottom-right corner of the center/HUD area
        pw = _CameraPreview._W
        ph = self._cam_preview.height() or _CameraPreview._H
        self._cam_preview.setGeometry(
            cw.width() - _RIGHT_W - pw - 12,
            cw.height() - ph - 28,
            pw, ph,
        )
        # Clipboard panel — bottom-center
        if hasattr(self, '_clipboard_panel') and self._clipboard_panel.isVisible():
            self._position_clipboard_panel()
        # Quick drawer — reposition if open
        if hasattr(self, '_quick_drawer') and self._quick_drawer.isVisible():
            self._position_quick_drawer()

    def _update_metrics(self):
        snap = _metrics.snapshot()

        # CPU
        cpu = snap["cpu"]
        self._bar_cpu.set_value(cpu, f"{cpu:.0f}%")

        # MEM
        mem = snap["mem"]
        self._bar_mem.set_value(mem, f"{mem:.0f}%")

        # NET
        net = snap["net"]
        if net < 1.0:
            net_str = f"{net*1024:.0f}KB/s"
        else:
            net_str = f"{net:.1f}MB/s"
        net_pct = min(100, net * 10)  # 10 MB/s = %100
        self._bar_net.set_value(net_pct, net_str)

        # GPU
        gpu = snap["gpu"]
        if gpu >= 0:
            self._bar_gpu.set_value(gpu, f"{gpu:.0f}%")
        else:
            self._bar_gpu.set_value(0, "N/A")

        # TMP
        tmp = snap["tmp"]
        if tmp >= 0:
            tmp_pct = min(100, (tmp / 100) * 100)
            self._bar_tmp.set_value(tmp_pct, f"{tmp:.0f}°C")
        else:
            self._bar_tmp.set_value(0, "N/A")

        try:
            boot_t  = psutil.boot_time()
            elapsed = time.time() - boot_t
            h = int(elapsed // 3600)
            m = int((elapsed % 3600) // 60)
            self._uptime_lbl.setText(f"UP  {h:02d}:{m:02d}")
        except Exception:
            self._uptime_lbl.setText("UP  --:--")

        try:
            proc_count = len(psutil.pids())
            self._proc_lbl.setText(f"PROC  {proc_count}")
        except Exception:
            self._proc_lbl.setText("PROC  --")


    def _build_header(self) -> QWidget:
        w = HudHeaderBar()
        w.setFixedHeight(64)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(26, 4, 26, 14)

        def _badge(txt, color=C.TEXT_MED):
            l = QLabel(txt)
            l.setFont(QFont("Courier New", 8))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_badge(APP_VERSION, C.PRI_DIM))
        lay.addSpacing(8)
        self._drawer_btn = QPushButton("⚙")
        self._drawer_btn.setFixedSize(26, 26)
        self._drawer_btn.setFont(QFont("Courier New", 11))
        self._drawer_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._drawer_btn.setToolTip("Settings & Controls")
        self._drawer_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 4px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.PRI_DIM}; }}
            QPushButton:checked {{ color: {C.PRI}; border-color: {C.PRI}; background: {C.PRI_GHO}; }}
        """)
        self._drawer_btn.setCheckable(True)
        self._drawer_btn.clicked.connect(self._toggle_drawer)
        lay.addWidget(self._drawer_btn)
        lay.addStretch()

        mid = QVBoxLayout(); mid.setSpacing(1)
        _disp = self._assistant_name.upper()
        self._title_lbl = QLabel(_disp)
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_lbl.setFont(QFont("Courier New", 17, QFont.Weight.Bold))
        self._title_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        _glow = QGraphicsDropShadowEffect(self._title_lbl)
        _glow.setBlurRadius(22); _glow.setOffset(0, 0); _glow.setColor(qcol(C.PRI, 210))
        self._title_lbl.setGraphicsEffect(_glow)
        self._title_lbl.setProperty("hudGlow", True)
        mid.addWidget(self._title_lbl)
        _sub_text = ("A Friendly Assistant"
                     if _disp in ("JARVIS", "J.A.R.V.I.S")
                     else "Personal AI Assistant")
        self._sub_lbl = QLabel(_sub_text)
        self._sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub_lbl.setFont(QFont("Courier New", 7))
        self._sub_lbl.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent;")
        mid.addWidget(self._sub_lbl)
        lay.addLayout(mid)
        lay.addStretch()

        right_col = QVBoxLayout(); right_col.setSpacing(2)
        self._clock_lbl = QLabel("00:00:00")
        self._clock_lbl.setFont(QFont("Courier New", 14, QFont.Weight.Bold))
        self._clock_lbl.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._clock_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._clock_lbl)
        self._date_lbl = QLabel("")
        self._date_lbl.setFont(QFont("Courier New", 7))
        self._date_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._date_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        right_col.addWidget(self._date_lbl)
        lay.addLayout(right_col)
        return w

    def _tick_clock(self):
        self._clock_lbl.setText(time.strftime("%H:%M:%S"))
        self._date_lbl.setText(time.strftime("%a %d %b %Y"))

    def _build_left_panel(self) -> QWidget:
        w = HudFrame(cuts=(20, 0, 20, 0), glow=True, tint=0.08)
        w.setFixedWidth(_LEFT_W)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 20, 14, 20)
        lay.setSpacing(6)

        hdr = QLabel("◈ SYS MONITOR")
        hdr.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        lay.addWidget(hdr)
        lay.addSpacing(2)

        self._bar_cpu = MetricBar("CPU", C.PRI)
        self._bar_mem = MetricBar("MEM", C.ACC2)
        self._bar_net = MetricBar("NET", C.GREEN)
        self._bar_gpu = MetricBar("GPU", C.ACC)
        self._bar_tmp = MetricBar("TMP", "#ff6688")

        for bar in [self._bar_cpu, self._bar_mem, self._bar_net,
                    self._bar_gpu, self._bar_tmp]:
            lay.addWidget(bar)

        lay.addSpacing(4)

        info_panel = HudFrame(cuts=(9, 0, 9, 0), glow=False, tint=0.07)
        ip_lay = QVBoxLayout(info_panel)
        ip_lay.setContentsMargins(12, 9, 12, 9)
        ip_lay.setSpacing(3)

        self._uptime_lbl = QLabel("UP  --:--")
        self._uptime_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._uptime_lbl.setStyleSheet(f"color: {C.GREEN}; background: transparent; border: none;")
        ip_lay.addWidget(self._uptime_lbl)

        self._proc_lbl = QLabel("PROC  --")
        self._proc_lbl.setFont(QFont("Courier New", 8))
        self._proc_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent; border: none;")
        ip_lay.addWidget(self._proc_lbl)

        os_name = {"Windows": "WIN", "Darwin": "macOS", "Linux": "LINUX"}.get(_OS, _OS.upper())
        os_lbl = QLabel(f"OS  {os_name}")
        os_lbl.setFont(QFont("Courier New", 8))
        os_lbl.setStyleSheet(f"color: {C.ACC2}; background: transparent; border: none;")
        ip_lay.addWidget(os_lbl)

        lay.addWidget(info_panel)
        lay.addSpacing(4)

        # ── Spotify mini-player (integrated, hidden until playing) ───────────
        self._spotify_player = SpotifyMiniPlayer()
        self._spotify_player.play_pause_clicked.connect(self._on_spotify_play_pause)
        self._spotify_player.next_clicked.connect(self._on_spotify_next)
        self._spotify_player.previous_clicked.connect(self._on_spotify_previous)
        self._spotify_player.shuffle_clicked.connect(self._on_spotify_shuffle)
        lay.addWidget(self._spotify_player)

        lay.addStretch()

        for txt, col in [
            ("AI CORE\nACTIVE",  C.GREEN),
            ("SEC\nCLEARED",     C.PRI),
            ("PROTOCOL\n" + APP_PROTOCOL,   C.TEXT_DIM),
        ]:
            lbl = QLabel(txt)
            lbl.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet(f"color: {col}; background: transparent; border: none;")
            chip = HudFrame(cuts=(8, 0, 8, 0), glow=False, tint=0.07)
            _cl = QVBoxLayout(chip)
            _cl.setContentsMargins(10, 8, 10, 8)
            _cl.addWidget(lbl)
            lay.addWidget(chip)

        return w
    def _build_right_panel(self) -> QWidget:
        w = HudFrame(cuts=(0, 20, 0, 20), glow=True, tint=0.08)
        w.setFixedWidth(_RIGHT_W)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 18, 16, 22)
        lay.setSpacing(6)

        def _sec(txt):
            l = QLabel(f"▸ {txt}")
            l.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            l.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            return l

        lay.addWidget(_sec("ACTIVITY LOG"))
        self._log = LogWidget()
        lay.addWidget(self._log, stretch=1)

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep)

        lay.addWidget(_sec("FILE UPLOAD"))
        self._drop_zone = FileDropZone()
        self._drop_zone.file_selected.connect(self._on_file_selected)
        lay.addWidget(self._drop_zone)

        self._file_hint = QLabel("No file loaded — drop or click above to upload")
        self._file_hint.setFont(QFont("Courier New", 7))
        self._file_hint.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._file_hint.setWordWrap(True)
        lay.addWidget(self._file_hint)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"color: {C.BORDER}; margin: 2px 0;")
        lay.addWidget(sep2)

        lay.addWidget(_sec("COMMAND INPUT"))
        lay.addLayout(self._build_input_row())

        self._interrupt_btn = HudButton("✋  INTERRUPT  [ESC]", kind="danger")
        self._interrupt_btn.setFixedHeight(34)
        self._interrupt_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._interrupt_btn.clicked.connect(self._do_interrupt)
        lay.addWidget(self._interrupt_btn)

        self._mute_btn = QPushButton("🎙  MICROPHONE ACTIVE")
        self._mute_btn.setFixedHeight(30)
        self._mute_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._mute_btn.clicked.connect(self._toggle_mute)
        self._style_mute_btn()
        lay.addWidget(self._mute_btn)

        return w

    def _build_quick_drawer(self) -> QWidget:
        """Floating overlay panel shown when the ⚙ header button is toggled."""
        _BTN_STYLE_PRI = f"""
            QPushButton {{
                background: #00091a; color: {C.PRI};
                border: 1px solid {C.PRI_DIM}; border-radius: 3px;
                text-align: left; padding: 0 8px;
            }}
            QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
        """
        _BTN_STYLE_DIM = f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_MED};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px;
            }}
            QPushButton:hover {{ color: {C.PRI}; border-color: {C.BORDER_B}; }}
        """

        w = QWidget(self.centralWidget())
        w.setObjectName("QuickDrawer")
        w.setStyleSheet(f"""
            QWidget#QuickDrawer {{
                background: {C.DARK};
                border: 1px solid {C.BORDER_B};
                border-top: none;
                border-radius: 0 0 6px 6px;
            }}
        """)
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(5)

        hdr = QLabel("◈ CONTROLS")
        hdr.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI_DIM}; background: transparent; "
                          f"border-bottom: 1px solid {C.BORDER}; padding-bottom: 4px;")
        lay.addWidget(hdr)

        remote_btn = QPushButton("◉  REMOTE CONTROL")
        remote_btn.setFixedHeight(30)
        remote_btn.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        remote_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remote_btn.setStyleSheet(_BTN_STYLE_PRI)
        remote_btn.clicked.connect(self._open_remote)
        lay.addWidget(remote_btn)

        fs_btn = QPushButton("⛶  FULLSCREEN  [F11]")
        fs_btn.setFixedHeight(26)
        fs_btn.setFont(QFont("Courier New", 7))
        fs_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        fs_btn.setStyleSheet(_BTN_STYLE_DIM)
        fs_btn.clicked.connect(self._toggle_fullscreen)
        lay.addWidget(fs_btn)

        sc_btn = QPushButton("⊞  CREATE DESKTOP SHORTCUT")
        sc_btn.setFixedHeight(26)
        sc_btn.setFont(QFont("Courier New", 7))
        sc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        sc_btn.setStyleSheet(_BTN_STYLE_DIM)
        sc_btn.clicked.connect(self._create_desktop_shortcut)
        lay.addWidget(sc_btn)

        self._autostart_btn = QPushButton("◉  AUTO-START: OFF")
        self._autostart_btn.setFixedHeight(26)
        self._autostart_btn.setFont(QFont("Courier New", 7))
        self._autostart_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._autostart_btn.clicked.connect(self._toggle_autostart)
        lay.addWidget(self._autostart_btn)

        cust_btn = QPushButton("⚙  CUSTOMISE ASSISTANT")
        cust_btn.setFixedHeight(26)
        cust_btn.setFont(QFont("Courier New", 7))
        cust_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cust_btn.setStyleSheet(_BTN_STYLE_DIM)
        cust_btn.clicked.connect(self._open_customize)
        lay.addWidget(cust_btn)

        self._brief_btn = QPushButton()
        self._brief_btn.setFixedHeight(26)
        self._brief_btn.setFont(QFont("Courier New", 7))
        self._brief_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._brief_btn.clicked.connect(self._toggle_brief)
        lay.addWidget(self._brief_btn)

        # ── Wake word ──────────────────────────────────────────────────────────
        self._wake_btn = QPushButton()
        self._wake_btn.setFixedHeight(26)
        self._wake_btn.setFont(QFont("Courier New", 7))
        self._wake_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._wake_btn.clicked.connect(self._toggle_wake_word)
        lay.addWidget(self._wake_btn)

        self._wake_sleep_btn = QPushButton()
        self._wake_sleep_btn.setFixedHeight(26)
        self._wake_sleep_btn.setFont(QFont("Courier New", 7))
        self._wake_sleep_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._wake_sleep_btn.clicked.connect(self._tap_wake_manual)
        lay.addWidget(self._wake_sleep_btn)
        # Neutral placeholder now; the real state (which may load the model to
        # check readiness) is resolved lazily the first time the drawer opens.
        self._wake_btn.setText("🎙  WAKE WORD")
        self._wake_btn.setStyleSheet(_BTN_STYLE_DIM)
        self._wake_sleep_btn.hide()

        self._ptt_btn = QPushButton()
        self._ptt_btn.setFixedHeight(26)
        self._ptt_btn.setFont(QFont("Courier New", 7))
        self._ptt_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._ptt_btn.clicked.connect(self._toggle_ptt)
        lay.addWidget(self._ptt_btn)

        self._refresh_talk_btns()

        self._hud_btn = QPushButton()
        self._hud_btn.setFixedHeight(26)
        self._hud_btn.setFont(QFont("Courier New", 7))
        self._hud_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hud_btn.clicked.connect(self._toggle_hud_style)
        lay.addWidget(self._hud_btn)
        self._refresh_hud_btn()

        # Market radar: module switch + access to its customisation
        self._radar_btn = QPushButton()
        self._radar_btn.setFixedHeight(26)
        self._radar_btn.setFont(QFont("Courier New", 7))
        self._radar_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._radar_btn.clicked.connect(self._toggle_radar)
        lay.addWidget(self._radar_btn)
        self._radar_cfg_btn = QPushButton("⚙  RADAR: Favorites")
        self._radar_cfg_btn.setFixedHeight(26)
        self._radar_cfg_btn.setFont(QFont("Courier New", 7))
        self._radar_cfg_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._radar_cfg_btn.setStyleSheet(_BTN_STYLE_DIM)
        self._radar_cfg_btn.clicked.connect(self._open_radar_settings)
        lay.addWidget(self._radar_cfg_btn)
        self._refresh_radar_btn()

        audio_btn = QPushButton("🎧  AUDIO DEVICES")
        audio_btn.setFixedHeight(26)
        audio_btn.setFont(QFont("Courier New", 7))
        audio_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        audio_btn.setStyleSheet(_BTN_STYLE_DIM)
        audio_btn.clicked.connect(self._open_audio_devices)
        lay.addWidget(audio_btn)

        mem_btn = QPushButton("🧠  MEMORY")
        mem_btn.setFixedHeight(26)
        mem_btn.setFont(QFont("Courier New", 7))
        mem_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        mem_btn.setStyleSheet(_BTN_STYLE_DIM)
        mem_btn.clicked.connect(self._open_memory_panel)
        lay.addWidget(mem_btn)

        plugin_btn = QPushButton("🧩  PLUGINS")
        plugin_btn.setFixedHeight(26)
        plugin_btn.setFont(QFont("Courier New", 7))
        plugin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        plugin_btn.setStyleSheet(_BTN_STYLE_DIM)
        plugin_btn.clicked.connect(self._open_plugin_manager)
        lay.addWidget(plugin_btn)

        settings_btn = QPushButton("⚙  PLUGIN SETTINGS")
        settings_btn.setFixedHeight(26)
        settings_btn.setFont(QFont("Courier New", 7))
        settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_btn.setStyleSheet(_BTN_STYLE_DIM)
        settings_btn.clicked.connect(self._open_plugin_settings)
        lay.addWidget(settings_btn)

        mail_btn = QPushButton("✉  CORREO")
        mail_btn.setFixedHeight(26)
        mail_btn.setFont(QFont("Courier New", 7))
        mail_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        mail_btn.setStyleSheet(_BTN_STYLE_DIM)
        mail_btn.clicked.connect(self._open_mail_accounts)
        lay.addWidget(mail_btn)

        osint_btn = QPushButton("Search")
        osint_btn.setFixedHeight(26)
        osint_btn.setFont(QFont("Courier New", 7))
        osint_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        osint_btn.setStyleSheet(_BTN_STYLE_DIM)
        osint_btn.clicked.connect(lambda: self.show_osint_panel())
        lay.addWidget(osint_btn)

        images_btn = QPushButton("🖼  DEMO: FLOATING IMAGES")
        images_btn.setFixedHeight(26)
        images_btn.setFont(QFont("Courier New", 7))
        images_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        images_btn.setStyleSheet(_BTN_STYLE_DIM)
        images_btn.clicked.connect(self._demo_floating_images)
        lay.addWidget(images_btn)

        video_btn = QPushButton("▶  DEMO: FLOATING VIDEO")
        video_btn.setFixedHeight(26)
        video_btn.setFont(QFont("Courier New", 7))
        video_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        video_btn.setStyleSheet(_BTN_STYLE_DIM)
        video_btn.clicked.connect(self._demo_floating_video)
        lay.addWidget(video_btn)

        w.adjustSize()
        return w

    def _toggle_drawer(self, checked: bool):
        if checked:
            self._refresh_wake_btns()   # resolve wake state on open (lazy)
            self._position_quick_drawer()
            self._quick_drawer.show()
            self._quick_drawer.raise_()
        else:
            self._quick_drawer.hide()

    def _position_quick_drawer(self):
        if not hasattr(self, '_quick_drawer'):
            return
        _W = 220
        self._quick_drawer.setFixedWidth(_W)
        self._quick_drawer.adjustSize()
        self._quick_drawer.setGeometry(12, 54, _W, self._quick_drawer.sizeHint().height())

    def _build_input_row(self) -> QHBoxLayout:
        row = QHBoxLayout(); row.setSpacing(5)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Type a command or question…")
        self._input.setFont(QFont("Courier New", 9))
        self._input.setFixedHeight(30)
        self._input.setStyleSheet(f"""
            QLineEdit {{
                background: {C.PRI_GHO}; color: {C.WHITE};
                border: none; border-left: 2px solid {C.PRI_DIM};
                border-bottom: 1px solid {C.PRI_DIM}; border-radius: 0px;
                padding: 3px 8px;
            }}
            QLineEdit:focus {{
                border-left: 2px solid {C.PRI}; border-bottom: 1px solid {C.PRI};
            }}
        """)
        self._input.returnPressed.connect(self._send)
        row.addWidget(self._input)

        send = HudButton("▸")
        send.setFixedSize(34, 30)
        send.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        send.clicked.connect(self._send)
        row.addWidget(send)
        return row

    def _build_content_panel(self) -> QWidget:
        """
        Collapsible panel below the HUD — shows search results, news, briefings.
        Hidden by default; appears when show_content() is called.
        """
        w = HudFrame(cuts=(14, 0, 14, 0), glow=True, tint=0.08)
        w.setObjectName("ContentPanel")
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(18, 12, 18, 12)
        lay.setSpacing(5)

        # ── header row ───────────────────────────────────────────────────────
        hdr = QHBoxLayout(); hdr.setSpacing(6)

        dot = QLabel("◈")
        dot.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(dot)

        self._content_title_lbl = QLabel("BRIEFING")
        self._content_title_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._content_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 1px;"
        )
        hdr.addWidget(self._content_title_lbl)
        hdr.addStretch()

        self._content_ts_lbl = QLabel("")
        self._content_ts_lbl.setFont(QFont("Courier New", 7))
        self._content_ts_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._content_ts_lbl)

        dismiss = QPushButton("DISMISS  ✕")
        dismiss.setFont(QFont("Courier New", 7))
        dismiss.setFixedHeight(18)
        dismiss.setCursor(Qt.CursorShape.PointingHandCursor)
        dismiss.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 2px; padding: 0 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        dismiss.clicked.connect(w.hide)
        hdr.addWidget(dismiss)
        lay.addLayout(hdr)

        # ── separator ─────────────────────────────────────────────────────────
        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet(f"color: {C.BORDER};"); lay.addWidget(sep)

        # ── text display ──────────────────────────────────────────────────────
        self._content_display = QTextEdit()
        self._content_display.setReadOnly(True)
        self._content_display.setFont(QFont("Courier New", 8))
        self._content_display.setMinimumHeight(60)
        self._content_display.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._content_display.setStyleSheet(f"""
            QTextEdit {{
                background: transparent;
                color: {C.TEXT};
                border: none;
                border-left: 2px solid {C.PRI_DIM};
                padding: 6px 8px;
                selection-background-color: {C.PRI_GHO};
            }}
            QScrollBar:vertical {{
                background: transparent; width: 6px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {C.PRI_DIM}; border-radius: 0px; min-height: 16px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0; border: none;
            }}
        """)
        lay.addWidget(self._content_display)

        return w

    def _show_content(self, title: str, text: str):
        """Slot — runs on Qt main thread. Updates and shows the content panel."""
        import time as _time
        # The panel opens below the head, so the head looks down at it. It is a
        # tiny thing that answers "did that land?" before you read a word.
        self.hud.glance(0.0, -0.85, hold=1.3)
        self._content_title_lbl.setText(title.upper()[:48])
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))
        self._content_display.setPlainText(text)
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start
        )
        first_show = not self._content_panel.isVisible()
        self._content_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 220, 120), 220])

    # ── document review ──────────────────────────────────────────────────────
    # Rendered as rich text into the content panel that already exists, rather
    # than into a panel of its own. A review is read, not clicked, so QTextEdit
    # gives scrolling, selection and copy for nothing, and the HUD gains no
    # widget it has to lay out. Severity decides colour and order here because
    # that is presentation; the plugin supplies no styling and knows no palette,
    # which is also what lets a re-theme repaint a review correctly.

    # Severity is marked by a symbol and a colour, not by a word. The findings
    # themselves are in the user's language, and "[SERIOUS]" sitting inside a
    # Turkish sentence is the kind of seam this project tries not to have —
    # while translating the tag would mean a table per language, which is worse.
    # A shape carries it in every language, and shape plus colour still reads
    # for someone who cannot separate red from amber. What the marks mean
    # arrives the way everything else does: JARVIS says it out loud.
    _REVIEW_MARKS = {"serious": ("RED", "▲"), "caution": ("ACC2", "●"), "note": ("PRI_DIM", "·")}

    @staticmethod
    def _esc(s) -> str:
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace("\n", "<br>"))

    def _show_review(self, title: str, summary: str, findings, unclear):
        """Slot — Qt main thread. Lays a document review into the content panel."""
        e = self._esc
        parts = [f'<div style="color:{C.TEXT}; font-family:Courier New;">']

        if summary:
            parts.append(
                f'<div style="color:{C.WHITE}; border-left:2px solid {C.PRI};'
                f' padding-left:8px; margin-bottom:10px;">{e(summary)}</div>')

        for f in (findings or []):
            key, mark = self._REVIEW_MARKS.get(f.get("severity"), ("PRI_DIM", "·"))
            colour = getattr(C, key)
            parts.append(f'<div style="margin-bottom:11px;">')
            parts.append(
                f'<span style="color:{colour}; font-weight:bold;">{mark}</span> '
                f'<span style="color:{C.WHITE}; font-weight:bold;">'
                f'{e(f.get("heading"))}</span>')
            if f.get("detail"):
                parts.append(f'<div style="margin-left:12px;">{e(f["detail"])}</div>')
            if f.get("quote"):
                # The document's own wording, visually separated from the
                # explanation so the two are never mistaken for each other.
                parts.append(
                    f'<div style="margin-left:12px; color:{C.TEXT_DIM};'
                    f' border-left:1px solid {C.BORDER}; padding-left:7px;">'
                    f'&ldquo;{e(f["quote"])}&rdquo;</div>')
            if f.get("suggestion"):
                parts.append(
                    f'<div style="margin-left:12px; color:{C.PRI};">'
                    f'&rarr; {e(f["suggestion"])}</div>')
            parts.append('</div>')

        if unclear:
            parts.append(
                f'<div style="margin-top:6px; border-top:1px solid {C.BORDER};'
                f' padding-top:7px; color:{C.TEXT_MED};">'
                'The document does not settle:</div>')
            for u in unclear:
                parts.append(
                    f'<div style="margin-left:12px; color:{C.TEXT_MED};">'
                    f'&middot; {e(u)}</div>')
        parts.append('</div>')

        import time as _time
        self.hud.glance(0.0, -0.85, hold=1.3)
        # Left as written, not upper-cased. The other content-panel titles are
        # the app's own English labels, but this one is the document's name in
        # the user's language, and str.upper() applies English casing rules to
        # it: Turkish "Sözleşmesi" comes back "SÖZLEŞMESI", having lost the
        # dotted capital İ. Python has no locale-aware upper to reach for, and
        # imposing one language's rules on all of them is the bug, not the fix.
        self._content_title_lbl.setText((title or "Document")[:48])
        self._content_ts_lbl.setText(_time.strftime("%H:%M:%S"))
        self._content_display.setHtml("".join(parts))
        self._content_display.moveCursor(
            self._content_display.textCursor().MoveOperation.Start)
        first_show = not self._content_panel.isVisible()
        self._content_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 260, 120), 260, 0])

    # ── quiz panel ───────────────────────────────────────────────────────────
    # An interactive twin of the content panel. The plugin only ever hands over
    # questions; everything about asking, marking and reporting happens here,
    # and the finished result is pushed back into the conversation the same way
    # a dropped file is — as a message JARVIS reads and responds to. That keeps
    # the tool call short (it returns the moment the board is up) and leaves the
    # talking to the assistant, in the user's own language.

    def _quiz_btn(self, text: str, primary: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setFont(QFont("Courier New", 8))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setMinimumHeight(24)
        edge = C.BORDER_B if primary else C.BORDER
        col = C.PRI if primary else C.TEXT_MED
        b.setStyleSheet(f"""
            QPushButton {{
                background: {C.PANEL2}; color: {col};
                border: 1px solid {edge}; border-radius: 2px;
                padding: 3px 9px; text-align: left;
            }}
            QPushButton:hover {{ color: {C.WHITE}; border-color: {C.PRI_DIM}; }}
            QPushButton:disabled {{ color: {C.TEXT_DIM}; border-color: {C.BORDER}; }}
        """)
        return b

    def _build_quiz_panel(self) -> QWidget:
        w = HudFrame(cuts=(14, 0, 14, 0), glow=True, tint=0.08)
        w.setObjectName("QuizPanel")
        w.hide()

        lay = QVBoxLayout(w)
        lay.setContentsMargins(18, 12, 18, 12)
        lay.setSpacing(6)

        hdr = QHBoxLayout(); hdr.setSpacing(6)
        dot = QLabel("◈")
        dot.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        dot.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        hdr.addWidget(dot)

        self._quiz_title_lbl = QLabel("QUIZ")
        self._quiz_title_lbl.setFont(QFont("Courier New", 8, QFont.Weight.Bold))
        self._quiz_title_lbl.setStyleSheet(
            f"color: {C.PRI}; background: transparent; letter-spacing: 1px;")
        hdr.addWidget(self._quiz_title_lbl)
        hdr.addStretch()

        self._quiz_count_lbl = QLabel("")
        self._quiz_count_lbl.setFont(QFont("Courier New", 7))
        self._quiz_count_lbl.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        hdr.addWidget(self._quiz_count_lbl)

        quit_btn = QPushButton("DISMISS  ✕")
        quit_btn.setFont(QFont("Courier New", 7))
        quit_btn.setFixedHeight(18)
        quit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        quit_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 2px; padding: 0 5px;
            }}
            QPushButton:hover {{ color: {C.TEXT}; border-color: {C.BORDER_B}; }}
        """)
        quit_btn.clicked.connect(self._hide_quiz)
        hdr.addWidget(quit_btn)
        lay.addLayout(hdr)

        rule = QFrame(); rule.setFixedHeight(1)
        rule.setStyleSheet(f"background: {C.BORDER};")
        lay.addWidget(rule)

        self._quiz_q_lbl = QLabel("")
        self._quiz_q_lbl.setWordWrap(True)
        self._quiz_q_lbl.setFont(QFont("Courier New", 9))
        self._quiz_q_lbl.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        lay.addWidget(self._quiz_q_lbl)

        self._quiz_answers = QWidget()
        self._quiz_answers.setStyleSheet("background: transparent;")
        self._quiz_answers_lay = QVBoxLayout(self._quiz_answers)
        self._quiz_answers_lay.setContentsMargins(0, 2, 0, 0)
        self._quiz_answers_lay.setSpacing(4)
        lay.addWidget(self._quiz_answers)

        self._quiz_note_lbl = QLabel("")
        self._quiz_note_lbl.setWordWrap(True)
        self._quiz_note_lbl.setFont(QFont("Courier New", 8))
        self._quiz_note_lbl.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
        self._quiz_note_lbl.hide()
        lay.addWidget(self._quiz_note_lbl)

        foot = QHBoxLayout()
        foot.addStretch()
        self._quiz_next_btn = self._quiz_btn("NEXT  →", primary=True)
        self._quiz_next_btn.setFixedWidth(110)
        self._quiz_next_btn.clicked.connect(self._quiz_next)
        self._quiz_next_btn.hide()
        foot.addWidget(self._quiz_next_btn)
        lay.addLayout(foot)

        self._quiz = None
        return w

    def _show_quiz(self, topic: str, questions, grader=None):
        """Slot — Qt main thread. Puts a fresh quiz on the board."""
        if not questions:
            return
        self._quiz = {
            "topic": topic or "",
            "questions": list(questions),
            "grader": grader,
            "i": 0,
            "results": [],
            "answered": False,
        }
        self._quiz_title_lbl.setText((topic or "quiz").upper()[:48])
        self.hud.glance(0.0, -0.85, hold=1.3)
        first_show = not self._quiz_panel.isVisible()
        self._quiz_panel.show()
        if first_show:
            total = self._center_split.height()
            self._center_split.setSizes([max(total - 250, 120), 0, 250])
        self._quiz_render()

    def _hide_quiz(self):
        self._quiz = None
        self._quiz_panel.hide()

    def _quiz_clear_answers(self):
        while self._quiz_answers_lay.count():
            item = self._quiz_answers_lay.takeAt(0)
            child = item.widget()
            if child is not None:
                child.setParent(None)
                child.deleteLater()

    def _quiz_render(self):
        q = self._quiz["questions"][self._quiz["i"]]
        n, total = self._quiz["i"] + 1, len(self._quiz["questions"])
        self._quiz_count_lbl.setText(f"{n} / {total}")
        self._quiz_q_lbl.setText(q.get("question", ""))
        self._quiz_note_lbl.hide()
        self._quiz_next_btn.hide()
        self._quiz["answered"] = False
        self._quiz_clear_answers()

        opts = q.get("options") or []
        if opts:
            for text in opts:
                b = self._quiz_btn("   " + text)
                b.clicked.connect(lambda _=False, t=text: self._quiz_submit(t))
                self._quiz_answers_lay.addWidget(b)
        else:
            row = QWidget(); row.setStyleSheet("background: transparent;")
            h = QHBoxLayout(row); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
            field = QLineEdit()
            field.setFont(QFont("Courier New", 9))
            field.setPlaceholderText("your answer")
            field.setStyleSheet(f"""
                QLineEdit {{
                    background: {C.PANEL2}; color: {C.WHITE};
                    border: 1px solid {C.BORDER}; border-radius: 2px; padding: 4px 7px;
                }}
                QLineEdit:focus {{ border-color: {C.PRI_DIM}; }}
            """)
            send = self._quiz_btn("ANSWER", primary=True)
            send.setFixedWidth(90)
            field.returnPressed.connect(lambda: self._quiz_submit(field.text()))
            send.clicked.connect(lambda: self._quiz_submit(field.text()))
            h.addWidget(field, stretch=1)
            h.addWidget(send)
            self._quiz_answers_lay.addWidget(row)
            field.setFocus()

    def _quiz_submit(self, given: str):
        if self._quiz is None or self._quiz["answered"]:
            return
        self._quiz["answered"] = True
        q = self._quiz["questions"][self._quiz["i"]]
        grader = self._quiz.get("grader")
        verdict = None
        if callable(grader):
            try:
                verdict = grader(q, given)
            except Exception:
                verdict = None
        self._quiz["results"].append({
            "question": q.get("question", ""),
            "type": q.get("type", ""),
            "given": str(given or "").strip(),
            "answer": q.get("answer", ""),
            "correct": verdict,
        })

        for i in range(self._quiz_answers_lay.count()):
            wdg = self._quiz_answers_lay.itemAt(i).widget()
            if wdg is not None:
                wdg.setEnabled(False)

        if verdict is True:
            mark, colour = "✓  correct", C.GREEN
        elif verdict is False:
            mark, colour = "✕  " + str(q.get("answer", "")), C.RED
        else:
            # Open answers and near-miss gap-fills are JARVIS's to judge. Saying
            # so is honest; marking it wrong here would be a guess.
            mark, colour = "…  noted — I'll go over this one with you", C.ACC2
        note = q.get("note") or ""
        self._quiz_note_lbl.setText(mark + (("\n" + note) if note else ""))
        self._quiz_note_lbl.setStyleSheet(f"color: {colour}; background: transparent;")
        self._quiz_note_lbl.show()

        last = self._quiz["i"] >= len(self._quiz["questions"]) - 1
        self._quiz_next_btn.setText("FINISH  →" if last else "NEXT  →")
        self._quiz_next_btn.show()
        self._quiz_next_btn.setFocus()

    def _quiz_next(self):
        if self._quiz is None:
            return
        if self._quiz["i"] >= len(self._quiz["questions"]) - 1:
            self._quiz_finish()
        else:
            self._quiz["i"] += 1
            self._quiz_render()

    def _quiz_finish(self):
        if self._quiz is None:
            return
        topic = self._quiz["topic"]
        results = self._quiz["results"]
        right = sum(1 for r in results if r["correct"] is True)
        unsure = sum(1 for r in results if r["correct"] is None)
        total = len(results)
        self._quiz_panel.hide()
        self._quiz = None

        self._log.append_log(f"QUIZ: {topic or 'quiz'} — {right}/{total} correct")

        # Hand it back to JARVIS as a message, not as a tool return: the tool
        # call ended minutes ago. This is the same channel a dropped file uses.
        lines = [f"[QUIZ_DONE] topic={topic or 'general'} | "
                 f"auto-marked {right}/{total} correct"
                 + (f", {unsure} still need your marking" if unsure else "")]
        for i, r in enumerate(results, 1):
            state = ("correct" if r["correct"] is True
                     else "wrong" if r["correct"] is False else "NEEDS MARKING")
            lines.append(
                f"{i}. [{r['type']}] {r['question']} | they answered: "
                f"{r['given'] or '(blank)'} | expected: {r['answer']} | {state}")
        lines.append(
            "Mark every question flagged NEEDS MARKING yourself — accept an answer "
            "that means the same thing. Then tell them how they did in their own "
            "language: the score, what they got wrong and why, in a couple of "
            "sentences. Offer another round only if it fits. "
            "Remember something only if it would still matter next week — that they "
            "are working through a subject, or keep missing the same thing. A score "
            "from one session is not worth a memory, and a memory per quiz would "
            "bury the things that are.")
        msg = "\n".join(lines)
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def _build_footer(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(22)
        w.setStyleSheet(f"background: transparent; border-top: 1px solid {C.PRI_DIM};")
        lay = QHBoxLayout(w); lay.setContentsMargins(24, 0, 24, 0)

        def _fl(txt, color=C.TEXT_MED):
            l = QLabel(txt); l.setFont(QFont("Courier New", 7))
            l.setStyleSheet(f"color: {color}; background: transparent;")
            return l

        lay.addWidget(_fl("[F4] Mute  ·  [F11] Fullscreen"))
        lay.addStretch()
        lay.addWidget(_fl(CREDITS_LINE, C.PRI_DIM))
        return w

    def _on_file_selected(self, path: str):
        self._current_file = path
        p    = Path(path)
        cat  = _file_category(p)
        icon, _ = _FILE_ICONS.get(cat, _FILE_ICONS["unknown"])
        size = _fmt_size(p.stat().st_size)
        self._file_hint.setText(f"{icon}  {p.name}  ·  {size}  ·  Tell {self._assistant_name} what to do with it")
        self._log.append_log(f"FILE: {p.name} ({size}) loaded")
        if self.on_text_command:
            msg = (
                f"[FILE_UPLOADED] path={path} | name={p.name} | "
                f"type={p.suffix.lstrip('.')} | size={size} | "
                f"Briefly tell the user you can see the file '{p.name}' "
                f"({size}) has been uploaded and ask what they'd like to do with it."
            )
            threading.Thread(target=self.on_text_command, args=(msg,), daemon=True).start()

    def notify_phone_connected(self) -> None:
        if self._remote_overlay and self._remote_overlay.isVisible():
            self._remote_overlay.mark_connected()

    def _open_remote(self):
        if not self.on_remote_clicked:
            self._log.append_log("SYS: Dashboard not running — remote unavailable.")
            return
        result = self.on_remote_clicked()
        if not result:
            self._log.append_log("SYS: Could not generate remote key.")
            return
        url    = result[0]
        key    = result[1]
        auto   = result[2] if len(result) >= 3 else ""
        manual = result[3] if len(result) >= 4 else url
        if self._remote_overlay:
            self._remote_overlay._do_close()
        cw  = self.centralWidget()
        ow, oh = RemoteKeyOverlay._OW, RemoteKeyOverlay._OH
        ov  = RemoteKeyOverlay(url, key, auto_login_url=auto, manual_url=manual,
                               expiry_secs=600, parent=cw)
        ov.set_new_key_callback(self.on_remote_clicked)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.closed.connect(lambda: setattr(self, '_remote_overlay', None))
        ov.show()
        self._remote_overlay = ov
        self._log.append_log(f"SYS: Remote key generated — manual: {manual or url}")

    # ── Auto-start ──────────────────────────────────────────────────────────────

    def _check_autostart(self) -> bool:
        """Returns True if auto-start is currently registered on this OS."""
        try:
            if _OS == "Windows":
                import winreg
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
                try:
                    winreg.QueryValueEx(key, "JARVIS_AI")
                    return True
                except FileNotFoundError:
                    return False
                finally:
                    winreg.CloseKey(key)
            elif _OS == "Darwin":
                return (Path.home() / "Library" / "LaunchAgents"
                        / "com.jarvis.assistant.plist").exists()
            else:
                return (Path.home() / ".config" / "autostart" / "jarvis.desktop").exists()
        except Exception:
            return False

    def _toggle_autostart(self):
        currently_on = self._check_autostart()
        try:
            script = str(Path(__file__).resolve().parent / "main.py")
            if _OS == "Windows":
                import winreg
                reg = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_ALL_ACCESS)
                if currently_on:
                    winreg.DeleteValue(reg, "JARVIS_AI")
                else:
                    pythonw = Path(sys.executable).parent / "pythonw.exe"
                    exe = str(pythonw if pythonw.exists() else sys.executable)
                    winreg.SetValueEx(reg, "JARVIS_AI", 0, winreg.REG_SZ,
                                      f'"{exe}" "{script}"')
                winreg.CloseKey(reg)
            elif _OS == "Darwin":
                plist_dir = Path.home() / "Library" / "LaunchAgents"
                plist_dir.mkdir(parents=True, exist_ok=True)
                plist = plist_dir / "com.jarvis.assistant.plist"
                if currently_on:
                    plist.unlink(missing_ok=True)
                else:
                    plist.write_text(
                        '<?xml version="1.0" encoding="UTF-8"?>\n'
                        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                        '<plist version="1.0"><dict>\n'
                        '  <key>Label</key><string>com.jarvis.assistant</string>\n'
                        '  <key>ProgramArguments</key><array>\n'
                        f'    <string>{sys.executable}</string>\n'
                        f'    <string>{script}</string>\n'
                        '  </array>\n'
                        '  <key>RunAtLoad</key><true/>\n'
                        '</dict></plist>\n'
                    )
            else:
                desk_dir = Path.home() / ".config" / "autostart"
                desk_dir.mkdir(parents=True, exist_ok=True)
                desk = desk_dir / "jarvis.desktop"
                if currently_on:
                    desk.unlink(missing_ok=True)
                else:
                    desk.write_text(
                        "[Desktop Entry]\n"
                        f"Name={self._assistant_name}\n"
                        f"Exec={sys.executable} {script}\n"
                        "Type=Application\nTerminal=false\n"
                        "X-GNOME-Autostart-enabled=true\n"
                    )
            enabled = not currently_on
            self._update_autostart_btn(enabled)
            self._log.append_log(
                f"SYS: Auto-start {'enabled' if enabled else 'disabled'}.")
        except Exception as e:
            self._log.append_log(f"ERR: Auto-start failed — {e}")

    def _update_autostart_btn(self, enabled: bool):
        if not hasattr(self, '_autostart_btn'):
            return
        if enabled:
            self._autostart_btn.setText("◉  AUTO-START: ON")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            self._autostart_btn.setText("◉  AUTO-START: OFF")
            self._autostart_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    def _toggle_brief(self):
        from memory.config_manager import get_brief_enabled, save_brief_enabled
        new_val = not get_brief_enabled()
        save_brief_enabled(new_val)
        self._update_brief_btn(new_val)

    # ── Wake word settings ───────────────────────────────────────────────────

    def _wake_state(self) -> dict:
        """Combined state for the two wake-word buttons. Readiness is a cheap,
        deterministic on-disk check now (see core.wake_word.is_ready), so there
        is nothing to cache — the button never flickers to a stale value."""
        if self.wake_get_state:
            try:
                s = self.wake_get_state()
                return {"ready": bool(s.get("ready")),
                        "enabled": bool(s.get("enabled")),
                        "awake": bool(s.get("awake"))}
            except Exception:
                pass
        # Before JarvisLive has wired its callback (drawer built at startup).
        ready, enabled = False, False
        try:
            from core.wake_word import is_ready
            from memory.config_manager import get_wake_word_enabled
            ready, enabled = is_ready(), get_wake_word_enabled()
        except Exception:
            pass
        return {"ready": ready, "enabled": enabled, "awake": True}

    def _refresh_wake_btns(self):
        if not hasattr(self, '_wake_btn'):
            return
        st = self._wake_state()
        _on = f"""
            QPushButton {{ background: #001a08; color: {C.GREEN};
                border: 1px solid {C.GREEN_D}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ background: #002010; }}"""
        _off = f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}"""
        self._wake_btn.setEnabled(True)
        if not st["ready"]:
            self._wake_btn.setText("⬇  WAKE WORD: DOWNLOAD")
            self._wake_btn.setStyleSheet(_off)
            self._wake_sleep_btn.hide()
        elif st["enabled"]:
            self._wake_btn.setText("🎙  WAKE WORD: ON")
            self._wake_btn.setStyleSheet(_on)
            self._wake_sleep_btn.show()
            self._wake_sleep_btn.setText("😴  SLEEP NOW" if st["awake"] else "👂  WAKE NOW")
            self._wake_sleep_btn.setStyleSheet(_off)
        else:
            self._wake_btn.setText("🎙  WAKE WORD: OFF")
            self._wake_btn.setStyleSheet(_off)
            self._wake_sleep_btn.hide()

    def _refresh_talk_btns(self):
        """Repaint the push-to-talk row from the saved setting."""
        if not hasattr(self, "_ptt_btn"):
            return
        from core.hotkey import chord_label
        from memory.config_manager import get_push_to_talk_enabled
        _on = f"""
            QPushButton {{ background: #001a08; color: {C.GREEN};
                border: 1px solid {C.GREEN_D}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ background: #002010; }}"""
        _off = f"""
            QPushButton {{ background: transparent; color: {C.TEXT_DIM};
                border: 1px solid {C.BORDER}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}"""

        ptt = get_push_to_talk_enabled()
        self._ptt_btn.setText(f"🎚  PUSH-TO-TALK: {chord_label()}" if ptt
                              else "🎚  PUSH-TO-TALK: OFF")
        self._ptt_btn.setStyleSheet(_on if ptt else _off)
        self._ptt_btn.setToolTip(
            "Microphone stays closed until you hold the key — nothing is sent "
            "while you are not holding it." if ptt
            else "Hold a key to talk instead of streaming the mic continuously.")


    # ── Market radar (core/market_radar_widget.py) ─────────────────────────
    def _radar_widget(self):
        return getattr(self.hud, "_radar", None)

    def _refresh_radar_btn(self):
        from memory.config_manager import get_market_radar_enabled
        radar = self._radar_widget()
        if radar is None:
            self._radar_btn.setText("📈  STOCK/CRYPTO RADAR: UNAVAILABLE")
            self._radar_btn.setEnabled(False)
            self._radar_cfg_btn.setEnabled(False)
            self._radar_btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {C.TEXT_DIM}; "
                f"border: 1px solid {C.BORDER}; border-radius: 3px; text-align: left; padding: 0 8px; }}")
            return
        on = get_market_radar_enabled()
        accent, border = (C.GREEN, C.GREEN_D) if on else (C.TEXT_DIM, C.BORDER)
        self._radar_btn.setStyleSheet(f"""
            QPushButton {{ background: {C.PANEL2}; color: {accent};
                border: 1px solid {border}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.WHITE}; border: 1px solid {C.BORDER_B}; }}""")
        self._radar_btn.setText("📈  STOCK/CRYPTO RADAR: " + ("ON" if on else "OFF"))
        self._radar_btn.setToolTip(
            "HUD module with stocks and cryptocurrencies, pinned favourites and JARVIS alerts "
            "for important news. When off, it is hidden and does not query data "
            "or alert.")
        self._radar_cfg_btn.setEnabled(True)

    def _set_radar_enabled(self, on: bool):
        from memory.config_manager import save_market_radar_enabled
        save_market_radar_enabled(on)
        radar = self._radar_widget()
        if radar is not None:
            radar.apply_enabled(on)
        self._refresh_radar_btn()
        self._log.append_log("SYS: Market radar " + ("enabled." if on else "disabled."))

    def _toggle_radar(self):
        from memory.config_manager import get_market_radar_enabled
        self._set_radar_enabled(not get_market_radar_enabled())

    def _on_radar_hidden(self, on: bool):
        """The user closed the radar with the ✕ of its own header."""
        from memory.config_manager import save_market_radar_enabled
        save_market_radar_enabled(bool(on))
        self._refresh_radar_btn()

    def _open_radar_settings(self):
        radar = self._radar_widget()
        if radar is None:
            return
        from memory.config_manager import get_market_radar_enabled, save_market_radar_enabled
        if not get_market_radar_enabled():
            save_market_radar_enabled(True)
            self._refresh_radar_btn()
        radar.open_settings()

    def _on_radar_cmd(self, cmd: str, payload):
        """Commands from the voice plugin and the alert loop (they arrive by signal: thread-safe)."""
        radar = self._radar_widget()
        if radar is None:
            return
        try:
            if cmd == "enable":
                self._set_radar_enabled(True)
            elif cmd == "disable":
                self._set_radar_enabled(False)
            elif cmd == "preview" and isinstance(payload, dict):
                radar.show_preview(payload)
            elif cmd == "select" and isinstance(payload, dict):
                from memory.config_manager import get_market_radar_enabled
                if not get_market_radar_enabled():
                    self._set_radar_enabled(True)
                radar.select_symbol(payload.get("symbol", ""), payload.get("range"))
            elif cmd == "alert" and isinstance(payload, dict):
                radar.notify_alert(payload)
            elif cmd == "refresh":
                radar.refresh_now()
        except Exception as e:
            print(f"[Radar] command {cmd!r} failed: {e}")

    def _refresh_hud_btn(self):
        from memory.config_manager import get_hud_style
        face = get_hud_style() == "face"
        # Neither state is "off", so both read as active — this is a choice
        # between two things, not a switch with a disabled side.
        style = f"""
            QPushButton {{ background: {C.PANEL2}; color: {C.PRI};
                border: 1px solid {C.BORDER_A}; border-radius: 3px;
                text-align: left; padding: 0 8px; }}
            QPushButton:hover {{ color: {C.WHITE}; border: 1px solid {C.BORDER_B}; }}"""
        self._hud_btn.setText("🧑  HUD: ANIMATED FACE" if face
                              else "◉  HUD: REACTOR CORE")
        self._hud_btn.setStyleSheet(style)
        self._hud_btn.setToolTip(
            "An animated head that speaks your words and shows what JARVIS is "
            "doing. Tap to switch to the reactor core."
            if face else
            "A reactor core that turns with the state and moves with your voice. "
            "Tap to switch to the animated head.")

    def _toggle_hud_style(self):
        """Swap the centrepiece. Both objects stay in memory, so the change is
        instant and switching back costs nothing."""
        from memory.config_manager import get_hud_style, save_hud_style
        want = "core" if get_hud_style() == "face" else "face"
        save_hud_style(want)
        try:
            self.hud.hud_style = want
            self.hud.update()
        except Exception:
            pass
        self._refresh_hud_btn()
        self._log.append_log(
            "SYS: HUD switched to the animated face." if want == "face"
            else "SYS: HUD switched to the reactor core.")

    def _toggle_ptt(self):
        from memory.config_manager import (get_push_to_talk_enabled,
                                           save_push_to_talk_enabled)
        want = not get_push_to_talk_enabled()
        save_push_to_talk_enabled(want)
        scope = None
        if self.on_push_to_talk:
            try:
                scope = self.on_push_to_talk(want)
            except Exception as e:
                self._log.append_log(f"ERR: Push-to-talk failed — {e}")
                save_push_to_talk_enabled(False)
                want = False
        self._apply_ptt_shortcut(want and scope != "global")
        self._refresh_talk_btns()

    def _apply_ptt_shortcut(self, needed: bool):
        """Bind the chord inside the window when no global hook is available.

        On macOS and Linux there is no dependency-free way to read global key
        state, so the chord is at least live whenever this window has focus.
        Qt gives no key-release for a QShortcut, so a press latches the mic open
        and a short timer closes it; held down, auto-repeat keeps pushing that
        timer out, which behaves like holding a key.
        """
        from PyQt6.QtGui import QKeySequence, QShortcut
        from core.hotkey import qt_sequence

        if not needed:
            sc = getattr(self, "_ptt_sc", None)
            if sc is not None:
                sc.setEnabled(False)
                self._ptt_sc = None
            self._ptt_hold(False)
            return
        if getattr(self, "_ptt_sc", None) is not None:
            return

        self._ptt_release = QTimer(self)
        self._ptt_release.setSingleShot(True)
        self._ptt_release.setInterval(420)
        self._ptt_release.timeout.connect(lambda: self._ptt_hold(False))

        def _press():
            self._ptt_hold(True)
            self._ptt_release.start()

        self._ptt_sc = QShortcut(QKeySequence(qt_sequence()), self)
        self._ptt_sc.setAutoRepeat(True)
        self._ptt_sc.activated.connect(_press)

    def _ptt_hold(self, held: bool):
        """Report a windowed press/release to whoever owns the microphone."""
        cb = getattr(self, "ptt_hold", None)
        if cb:
            try:
                cb(bool(held))
            except Exception:
                pass

    def _toggle_wake_word(self):
        st = self._wake_state()
        if not st["ready"]:
            # First time: download openwakeword + model in a worker thread.
            self._wake_btn.setText("⬇  DOWNLOADING… (one-time)")
            self._wake_btn.setEnabled(False)
            def _work():
                try:
                    from core.wake_word import install_and_download
                    ok, msg = install_and_download(
                        logger=lambda m: self._log_sig.emit(f"SYS: {m}"))
                except Exception as e:
                    ok, msg = False, str(e)
                if ok and self.on_wake_toggle:
                    try:
                        self.on_wake_toggle(True)   # auto-enable after a successful download
                    except Exception:
                        pass
                self._wake_dl_sig.emit(ok, msg)
            threading.Thread(target=_work, daemon=True).start()
            return
        # Already downloaded → just flip enabled/disabled through JarvisLive.
        if self.on_wake_toggle:
            try:
                self.on_wake_toggle(not st["enabled"])
            except Exception:
                pass
        self._refresh_wake_btns()

    def _on_wake_install_done(self, ok: bool, msg: str):
        self._log_sig.emit(f"SYS: {'Wake word ready.' if ok else 'Wake word setup failed: ' + msg}")
        self._refresh_wake_btns()

    def _tap_wake_manual(self):
        if self.on_wake_manual:
            try:
                self.on_wake_manual()
            except Exception:
                pass
        self._refresh_wake_btns()

    def _update_brief_btn(self, enabled: bool):
        if not hasattr(self, '_brief_btn'):
            return
        if enabled:
            self._brief_btn.setText("☀  MORNING BRIEF: ON")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #001a08; color: {C.GREEN};
                    border: 1px solid {C.GREEN_D}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ background: #002010; }}
            """)
        else:
            self._brief_btn.setText("☀  MORNING BRIEF: OFF")
            self._brief_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; color: {C.TEXT_DIM};
                    border: 1px solid {C.BORDER}; border-radius: 3px;
                    text-align: left; padding: 0 8px;
                }}
                QPushButton:hover {{ color: {C.TEXT}; border: 1px solid {C.BORDER_B}; }}
            """)

    # ── Customization ────────────────────────────────────────────────────────────

    def _open_customize(self):
        cfg = _read_full_config()
        if self._customize_overlay:
            self._customize_overlay.hide()
        cw = self.centralWidget()
        ov = CustomizeOverlay(
            cfg.get("assistant_name", "JARVIS") or "JARVIS",
            cfg.get("user_name", ""),
            cfg.get("ui_color", "") or DEFAULT_UI_COLOR,
            cfg.get("voice_name", ""),
            parent=cw,
        )
        ow, oh = CustomizeOverlay._OW, CustomizeOverlay._OH
        oh = min(oh, cw.height() - 16)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.on_preview = self._preview_ui_color
        ov.saved.connect(self._apply_name_update)
        ov.show()
        self._customize_overlay = ov

    def _preview_ui_color(self, hex_color: str):
        """Live preview — paints the whole interface the new colour (does NOT write to config)."""
        old = current_palette()
        if apply_ui_accent(hex_color):
            retheme_all_widgets(old, current_palette())

    def _apply_name_update(self, name: str, user_name: str, ui_color: str = "",
                           voice: str = ""):
        """Update all name/theme-dependent UI elements and persist to config."""
        self._assistant_name = name.strip() or "JARVIS"
        display = self._assistant_name.upper()
        self.setWindowTitle(f"{display} — {APP_VERSION}")
        self._title_lbl.setText(display)
        if display in ("JARVIS", "J.A.R.V.I.S"):
            self._sub_lbl.setText("Just A Rather Very Intelligent System")
        else:
            self._sub_lbl.setText("Personal AI Assistant")
        self._log._ai_name_lc = self._assistant_name.lower()
        self.hud._assistant_name = display

        color_changed = False
        if ui_color:
            old = current_palette()
            if apply_ui_accent(ui_color):
                # Live-paint the whole interface (panels, buttons, borders, HUD)
                retheme_all_widgets(old, current_palette())
                color_changed = old["PRI"] != C.PRI

        # Voice change → persist and, if it actually changed, rebuild the Live
        # session so the new voice takes effect (it's fixed at connect time).
        voice_changed = False
        if voice:
            from memory.config_manager import get_voice, save_voice
            if voice != get_voice():
                save_voice(voice)
                voice_changed = True

        try:
            data = _read_full_config()
            data["assistant_name"] = self._assistant_name
            data["user_name"] = user_name.strip()
            if ui_color:
                data["ui_color"] = ui_color.strip().lower()
            API_FILE.write_text(json.dumps(data, indent=4), encoding="utf-8")
            self._log.append_log(f"SYS: Identity updated — {display}")
            if color_changed:
                self._log.append_log(f"SYS: UI colour applied — {ui_color}")
            if voice_changed:
                self._log.append_log(f"SYS: Voice set — {voice}")
        except Exception as e:
            self._log.append_log(f"ERR: Config save failed — {e}")

        if voice_changed and self.on_voice_change:
            self.on_voice_change()

    def _centre_overlay(self, ov) -> None:
        """Place a floating overlay in the middle of the HUD and show it."""
        cw = self.centralWidget()
        ov.adjustSize()
        ov.setGeometry(
            max(0, (cw.width()  - ov.width())  // 2),
            max(0, (cw.height() - ov.height()) // 2),
            ov.width(), ov.height(),
        )
        ov.show()
        ov.raise_()

    # ── Floating images ──────────────────────────────────────────────────────
    # Public entry point: any action/plugin that produces images to show the
    # user (search results, a screenshot, a generated picture…) calls this.
    # Each image opens as its own independent, closable, downloadable window,
    # linked back to the JARVIS core by a wire drawn in `_wire_layer`, with a
    # CRT power-on reveal — and stays open until the user closes it by hand.

    def show_floating_images(self, sources: list, titles: list[str] | None = None) -> None:
        """`sources` items may be a file path, raw image bytes, or a QPixmap."""
        for i, src in enumerate(sources):
            title = titles[i] if titles and i < len(titles) else f"IMG_{i + 1:03d}"
            QTimer.singleShot(i * 160, lambda s=src, t=title: self._spawn_floating_image(s, t))

    def _spawn_floating_image(self, source, title: str) -> None:
        cw = self.centralWidget()
        win = FloatingImageWindow(source, title, parent=cw)

        x = random.randint(16, max(16, cw.width() - win.width() - 16))
        y = random.randint(int(cw.height() * 0.16),
                            max(int(cw.height() * 0.16) + 1, int(cw.height() * 0.6)))
        win.move(x, y)
        win.closed.connect(self._on_floating_image_closed)

        self._image_windows.append(win)
        self._wire_layer.add_window(win)
        win.show()
        win.raise_()
        win.open_animate()

    def _on_floating_image_closed(self, win: "FloatingImageWindow") -> None:
        if win in self._image_windows:
            self._image_windows.remove(win)
        self._wire_layer.remove_window(win)
        win.deleteLater()

    def _make_placeholder_images(self, n: int, labels: list[str] | None = None) -> list[QPixmap]:
        """Generates n on-brand placeholder pixmaps (no bundled sample images
        ship with the app) — used only by the drawer's demo button."""
        pixmaps = []
        base_hues = [190, 178, 200, 165, 210]
        for i in range(n):
            w, h = 560, 360
            px = QPixmap(w, h)
            px.fill(qcol("#020608"))
            p = QPainter(px)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            grad = QLinearGradient(0, 0, w, h)
            hue = base_hues[i % len(base_hues)]
            grad.setColorAt(0.0, QColor.fromHsv(hue, 200, 70))
            grad.setColorAt(1.0, QColor(2, 6, 8))
            p.fillRect(px.rect(), QBrush(grad))
            p.setPen(QPen(qcol(C.PRI_DIM), 1))
            for gx in range(0, w, 70):
                p.drawLine(gx, 0, gx, h)
            for gy in range(0, h, 72):
                p.drawLine(0, gy, w, gy)
            p.setPen(QPen(qcol(C.PRI), 2))
            p.drawEllipse(QPointF(w / 2, h / 2), 70, 70)
            p.setPen(QPen(qcol(C.PRI), 1))
            p.drawEllipse(QPointF(w / 2, h / 2), 42, 42)
            label = (labels[i] if labels and i < len(labels) else f"SAMPLE {i + 1}")
            p.setFont(QFont("Courier New", 16))
            p.setPen(QPen(qcol(C.TEXT), 1))
            p.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, label)
            p.end()
            pixmaps.append(px)
        return pixmaps

    def _demo_floating_images(self) -> None:
        """Wired to the drawer's 'DEMO: FLOATING IMAGES' button."""
        labels = ["J.A.R.V.I.S.", "3D GLOBE", "HUD REF"]
        pixmaps = self._make_placeholder_images(3, labels)
        self.show_floating_images(pixmaps, titles=labels)

    # ── Floating videos ──────────────────────────────────────────────────────
    # Same idea as floating images, but the content is a live embedded YouTube
    # player (see FloatingVideoWindow) instead of a picture. Public entry
    # point: actions/youtube_video.py calls this instead of opening a browser
    # tab. Falls back to an "open in browser" placeholder if PyQt6-WebEngine
    # isn't installed — see the _WEBENGINE_OK guard near the top of this file.

    def show_floating_videos(self, sources: list, titles: list[str] | None = None) -> None:
        """`sources` items may be a full YouTube URL or a bare 11-char video id."""
        for i, src in enumerate(sources):
            title = titles[i] if titles and i < len(titles) else f"VIDEO_{i + 1:03d}"
            QTimer.singleShot(i * 200, lambda s=src, t=title: self._spawn_floating_video(s, t))

    def _spawn_floating_video(self, source, title: str) -> None:
        cw = self.centralWidget()
        win = FloatingVideoWindow(source, title, parent=cw)

        x = random.randint(16, max(16, cw.width() - win.width() - 16))
        y = random.randint(int(cw.height() * 0.16),
                            max(int(cw.height() * 0.16) + 1, int(cw.height() * 0.55)))
        win.move(x, y)
        win.closed.connect(self._on_floating_video_closed)

        self._video_windows.append(win)
        self._wire_layer.add_window(win)
        win.show()
        win.raise_()
        win.open_animate()

    def _on_floating_video_closed(self, win: "FloatingVideoWindow") -> None:
        if win in self._video_windows:
            self._video_windows.remove(win)
        self._wire_layer.remove_window(win)
        win.deleteLater()

    # ── Live cameras (globe layer) ───────────────────────────────────────────────
    # Opened by the "▶ WATCH LIVE" button on the card of a globe camera
    # (core/globe_widget.py → GlobeWidget._on_title_changed → here). Each camera is an
    # independent CRT window, above the Earth and with a cable to the core (see
    # LiveCamWindow and _CamWireOverlay). Requesting the same camera again just brings it to the front.

    def _ensure_cam_wire(self):
        if getattr(self, "_cam_wire", None) is None:
            self._cam_wire = _CamWireOverlay(
                self, self.centralWidget(), lambda: self._wire_layer._anchor_getter()
                if self._wire_layer._anchor_getter else None)
        return self._cam_wire

    def show_live_cam(self, cam: dict) -> None:
        if not hasattr(self, "_live_cam_windows"):
            self._live_cam_windows = []
        for w in self._live_cam_windows:
            if w.cam_id == cam.get("id") and not w._closing:
                w.show()
                w.raise_()
                w.activateWindow()
                return
        win = LiveCamWindow(cam, parent=self)
        g = self.frameGeometry()
        n = len(self._live_cam_windows)
        x = max(g.left() + 8, g.right() - win.width() - 28 - n * 26)
        y = g.top() + int(g.height() * 0.14) + n * 26
        win.move(x, y)
        win.closed.connect(self._on_live_cam_closed)
        self._live_cam_windows.append(win)
        wire = self._ensure_cam_wire()      # shown BEFORE the window: it ends up below it
        wire.add_window(win)
        wire.show()
        win.show()
        win.raise_()
        win.activateWindow()
        win.open_animate()

    def _on_live_cam_closed(self, win) -> None:
        if win in self._live_cam_windows:
            self._live_cam_windows.remove(win)
        if getattr(self, "_cam_wire", None) is not None:
            self._cam_wire.remove_window(win)
        win.deleteLater()

    # ── Mail reader window ───────────────────────────────────────────────────
    # Opened by plugins/mail_control.py (action=open_reader) after Jarvis has read
    # or summarised mail and the user said yes to "shall I open a window?". One
    # window holds all the mails, ◀ ▶ to move between them; asking again while it
    # is open just swaps its contents instead of stacking windows.

    def show_mail_reader(self, messages: list) -> None:
        if not messages:
            return
        win = self._mail_reader
        if win is not None and not win._closing:
            win.set_messages(messages)
            win.raise_()
            return
        cw = self.centralWidget()
        win = MailReaderWindow(messages, parent=cw)
        x = max(16, cw.width() - win.width() - 24)
        y = max(8, min(int(cw.height() * 0.12), cw.height() - win.height() - 8))
        win.move(x, y)
        win.closed.connect(self._on_mail_reader_closed)
        self._mail_reader = win
        self._wire_layer.add_window(win)
        win.show()
        win.raise_()
        win.open_animate()

    def _on_mail_reader_closed(self, win: "MailReaderWindow") -> None:
        if self._mail_reader is win:
            self._mail_reader = None
        self._wire_layer.remove_window(win)
        win.deleteLater()

    # ── Panel OSINT (core/osint_hud_widget.py) ───────────────────────────────
    def show_osint_panel(self, prefill=None) -> None:
        """Opens (or reuses) the OSINT HUD window. Optional `prefill`:
        {username, full_name, image_path, auto_start}."""
        try:
            from core.osint_hud_widget import OsintHudWindow
        except Exception as e:
            self._log_sig.emit(f"SYS: could not load the OSINT module: {e}")
            return
        win = self._osint_win
        if win is None or not win.isVisible():
            win = OsintHudWindow()
            win.closed.connect(lambda _w: setattr(self, "_osint_win", None))
            geo = self.geometry()
            win.move(geo.x() + max(20, (geo.width() - win.width()) // 2),
                     geo.y() + max(20, (geo.height() - win.height()) // 2))
            self._osint_win = win
            win.show()
        win.raise_()
        win.activateWindow()
        if prefill:
            win.prefill(**{k: v for k, v in dict(prefill).items()
                           if k in ("username", "full_name", "image_path", "auto_start",
                                    "open_library")})

    def _open_mail_accounts(self) -> None:
        old = self._mail_accounts_overlay
        if old is not None:
            try:
                old.hide()
                old.deleteLater()
            except Exception:
                pass
        cw = self.centralWidget()
        ov = MailAccountsOverlay(parent=cw)
        ow = MailAccountsOverlay._OW
        oh = min(660, cw.height() - 16)
        ov.setGeometry((cw.width() - ow) // 2, (cw.height() - oh) // 2, ow, oh)
        ov.show()
        ov.raise_()
        self._mail_accounts_overlay = ov    # keep a reference so it isn't GC'd

    def _demo_floating_video(self) -> None:
        """Wired to the drawer's 'DEMO: FLOATING VIDEO' button — a known-good
        public video id, so the effect can be tested without a search."""
        if not _WEBENGINE_OK:
            self._log.append_log(
                "SYS: PyQt6-WebEngine is not installed — pip install PyQt6-WebEngine"
            )
        self.show_floating_videos(["aqz-KE-bpKQ"], titles=["BIG BUCK BUNNY"])

    # ── Audio devices ────────────────────────────────────────────────────────

    def _open_audio_devices(self):
        ov = AudioDeviceOverlay(parent=self.centralWidget())
        ov.picked.connect(self._on_audio_devices_applied)
        self._centre_overlay(ov)
        self._audio_overlay = ov            # keep a reference so it isn't GC'd

    def _on_audio_devices_applied(self):
        self._log.append_log("SYS: Audio devices updated.")
        if self.on_audio_device_change:
            self.on_audio_device_change()

    # ── Memory panel ─────────────────────────────────────────────────────────

    def _open_memory_panel(self):
        ov = MemoryOverlay(parent=self.centralWidget())
        self._centre_overlay(ov)
        self._memory_overlay = ov

    # ── Irreversible-action confirmation ─────────────────────────────────────

    def _show_confirm_banner(self, title: str, detail: str):
        self._hide_confirm_banner()
        ov = ConfirmBanner(title, detail, parent=self.centralWidget())
        ov.answered.connect(self._on_confirm_answered)
        self._centre_overlay(ov)
        self._confirm_overlay = ov

    def _hide_confirm_banner(self):
        ov = getattr(self, "_confirm_overlay", None)
        if ov is not None:
            ov.hide()
            ov.deleteLater()
            self._confirm_overlay = None

    def _on_confirm_answered(self, accepted: bool):
        # Tear the banner down first: core.confirm.resolve() may be about to
        # shut the machine down, and a live widget mid-callback is not where you
        # want to be when that happens.
        self._hide_confirm_banner()
        try:
            from core.confirm import resolve
            resolve(bool(accepted))
        except Exception as e:
            self._log.append_log(f"ERR: Confirmation failed — {e}")

    def _open_plugin_manager(self):
        plugins = self.get_plugins() if self.get_plugins else []
        cw = self.centralWidget()
        ov = PluginManagerOverlay(plugins, parent=cw)
        ov.adjustSize()
        ov.setGeometry(
            (cw.width()  - ov.width())  // 2,
            (cw.height() - ov.height()) // 2,
            ov.width(), ov.height(),
        )
        ov.show()
        ov.raise_()
        self._plugin_manager_overlay = ov   # keep a reference so it isn't GC'd

    def _open_plugin_settings(self):
        sections = self.get_plugin_settings() if self.get_plugin_settings else []
        cw = self.centralWidget()
        ov = PluginSettingsOverlay(sections, parent=cw)
        ow = PluginSettingsOverlay._OW
        oh = min(560, cw.height() - 16)
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.show()
        ov.raise_()
        self._plugin_settings_overlay = ov   # keep a reference so it isn't GC'd

    # ── Clipboard intelligence ───────────────────────────────────────────────────

    def _on_clipboard_changed(self):
        try:
            text = QApplication.clipboard().text().strip()
            if len(text) >= 10:
                self._clipboard_sig.emit(text)
        except Exception:
            pass

    def _show_clipboard_panel(self, text: str):
        self._clipboard_panel.show_clipboard(text)
        self._position_clipboard_panel()

    def _position_clipboard_panel(self):
        cw = self.centralWidget()
        pw = ClipboardPanel._W
        ph = self._clipboard_panel.sizeHint().height() or ClipboardPanel._H
        x = (cw.width() - pw) // 2
        y = cw.height() - ph - 6
        self._clipboard_panel.setGeometry(x, y, pw, ph)
        self._clipboard_panel.raise_()

    def _on_clipboard_action(self, cmd: str):
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(cmd,), daemon=True).start()

    # ── Spotify mini-player ──────────────────────────────────────────────────

    def _poll_spotify(self):
        """Runs every 3 s on the Qt timer, but the actual Spotify Web API call
        happens on a background thread — this must never block the UI."""
        if getattr(self, "_spotify_polling", False):
            return                       # previous poll still running → skip
        self._spotify_polling = True

        def _worker():
            try:
                try:
                    from plugins import _spotify_client
                    info = _spotify_client.now_playing()
                except Exception:
                    info = None

                art_bytes = None
                url = (info or {}).get("album_art") or ""
                # download whenever the cover for this URL is not on screen
                # (new album OR a previous download failed) — retried each poll
                if url and url != self._spotify_player.art_url_loaded:
                    try:
                        import requests
                        r = requests.get(url, timeout=6)
                        if r.ok and r.content:
                            art_bytes = r.content
                    except Exception:
                        pass
                self._spotify_sig.emit((info, art_bytes) if info else None)
            finally:
                self._spotify_polling = False

        threading.Thread(target=_worker, daemon=True).start()

    def _on_spotify_polled(self, payload):
        if payload is None:
            self._spotify_player.hide()
            self._spotify_art_url = None
            self._spotify_last_info = None
            if self._lyrics_win is not None:
                self._lyrics_win.on_playback(None)
            return
        info, art_bytes = payload
        self._spotify_last_info = info
        if self._lyrics_win is not None:
            self._lyrics_win.on_playback(info)
        self._spotify_player.update_track(info)
        if art_bytes:
            self._spotify_player.set_art(art_bytes)

    def _spotify_action(self, fn_name: str):
        """Fire-and-forget a Spotify control call; the next poll (≤3 s) picks
        up whatever actually changed, so the button itself does not wait on
        the network."""
        def _worker():
            try:
                from plugins import _spotify_client
                getattr(_spotify_client, fn_name)()
            except Exception as e:
                self._log.append_log(f"ERR: Spotify control failed — {e}")
        threading.Thread(target=_worker, daemon=True).start()

    def _on_spotify_play_pause(self):
        # Trust the icon currently shown rather than re-querying — it reflects
        # the last poll, which is at most 3 s stale.
        playing = self._spotify_player.is_playing
        self._spotify_action("pause" if playing else "resume")

    def _on_spotify_next(self):
        self._spotify_action("next_track")

    def _on_spotify_previous(self):
        self._spotify_action("previous_track")

    def _on_spotify_shuffle(self):
        self._spotify_action("toggle_shuffle")

    # ── synced lyrics window ─────────────────────────────────────────────────
    def _open_lyrics(self):
        """GUI thread. Creates (once) and shows the floating lyrics window,
        then polls Spotify faster while it is open so the sync stays tight."""
        if self._lyrics_win is None:
            self._lyrics_win = LyricsWindow(self)
            self._lyrics_win.seek_requested.connect(self._on_lyrics_seek)
            self._lyrics_win.closed.connect(self._on_lyrics_closed)
        self._lyrics_win.show_window()
        self._spotify_tmr.setInterval(1500)
        if self._spotify_last_info:
            self._lyrics_win.on_playback(self._spotify_last_info)
        self._poll_spotify()

    def _on_lyrics_closed(self):
        self._spotify_tmr.setInterval(3000)

    def _on_lyrics_seek(self, ms: int):
        def _worker():
            try:
                from plugins import _spotify_client
                _spotify_client.seek(ms)
            except Exception as e:
                self._log.append_log(f"ERR: Spotify seek failed — {e}")
        threading.Thread(target=_worker, daemon=True).start()

    # ── 3D globe ──────────────────────────────────────────────────────────────

    def _on_globe_flyto(self, lat: float, lon: float, label: str):
        if getattr(self.hud, "_globe", None) is not None:
            self.hud._globe.fly_to(lat, lon, label)


    # ────────────────────────────────────────────────────────────────────────────

    def _do_interrupt(self):
        if self.on_interrupt:
            self.on_interrupt()

    def _toggle_mute(self):
        self._muted = not self._muted
        self.hud.muted = self._muted
        self._style_mute_btn()
        if self._muted:
            self._apply_state("MUTED")
            self._log.append_log("SYS: Microphone muted.")
        else:
            self._apply_state("LISTENING")
            self._log.append_log("SYS: Microphone active.")

    def _style_mute_btn(self):
        if self._muted:
            self._mute_btn.setText("🔇  MICROPHONE MUTED")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #140006; color: {C.MUTED_C};
                    border: 1px solid {C.MUTED_C}; border-radius: 3px;
                }}
            """)
        else:
            self._mute_btn.setText("🎙  MICROPHONE ACTIVE")
            self._mute_btn.setStyleSheet(f"""
                QPushButton {{
                    background: #00140a; color: {C.GREEN};
                    border: 1px solid {C.GREEN}; border-radius: 3px;
                }}
                QPushButton:hover {{ background: #001f10; }}
            """)

    def _send(self):
        txt = self._input.text().strip()
        if not txt: return
        self._input.clear()
        self._log.append_log(f"You: {txt}")
        if self.on_text_command:
            threading.Thread(target=self.on_text_command, args=(txt,), daemon=True).start()

    def _apply_state(self, state: str):
        self.hud.state    = state
        self.hud.speaking = (state == "SPEAKING")

    def _check_config(self) -> bool:
        if not API_FILE.exists(): return False
        try:
            d = json.loads(API_FILE.read_text(encoding="utf-8"))
            return bool(d.get("gemini_api_key")) and bool(d.get("os_system"))
        except Exception:
            return False

    def _show_setup(self):
        ov = SetupOverlay(self.centralWidget())
        cw = self.centralWidget()
        ow, oh = 460, 390
        ov.setGeometry(
            (cw.width()  - ow) // 2,
            (cw.height() - oh) // 2,
            ow, oh,
        )
        ov.done.connect(self._on_setup_done)
        ov.show()
        self._overlay = ov

    def _on_setup_done(self, key: str, os_name: str):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        API_FILE.write_text(
            json.dumps({"gemini_api_key": key, "os_system": os_name}, indent=4),
            encoding="utf-8",
        )
        self._ready = True
        if self._overlay:
            self._overlay.hide()
            self._overlay = None
        self._apply_state("LISTENING")
        self._assistant_name = _read_full_config().get("assistant_name", "JARVIS") or "JARVIS"
        self._log.append_log(f"SYS: Initialised. OS={os_name.upper()}. {self._assistant_name} online.")


class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_):
        pass


class JarvisUI:
    def __init__(self, face_path: str, size=None):
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        self._win = MainWindow(face_path)
        self.root = _RootShim(self._app)
        self._win.show()

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._toggle_mute()

    @property
    def current_file(self) -> str | None:
        return self._win._drop_zone.current_file()

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    @property
    def on_remote_clicked(self):
        return self._win.on_remote_clicked

    @on_remote_clicked.setter
    def on_remote_clicked(self, cb):
        self._win.on_remote_clicked = cb

    @property
    def on_interrupt(self):
        return self._win.on_interrupt

    @on_interrupt.setter
    def on_interrupt(self, cb):
        self._win.on_interrupt = cb

    @property
    def on_voice_change(self):
        return self._win.on_voice_change

    @on_voice_change.setter
    def on_voice_change(self, cb):
        self._win.on_voice_change = cb

    @property
    def on_audio_device_change(self):
        return self._win.on_audio_device_change

    @on_audio_device_change.setter
    def on_audio_device_change(self, cb):
        self._win.on_audio_device_change = cb

    def show_confirm(self, title: str, detail: str) -> None:
        """Thread-safe: raise the irreversible-action gate. Called from action
        handlers running in executor threads, so it goes through a signal."""
        self._win._confirm_sig.emit(str(title)[:120], str(detail)[:300])

    def hide_confirm(self) -> None:
        """Thread-safe: take the gate down."""
        self._win._confirm_hide_sig.emit()

    @property
    def get_plugins(self):
        return self._win.get_plugins

    @get_plugins.setter
    def get_plugins(self, cb):
        self._win.get_plugins = cb

    @property
    def get_plugin_settings(self):
        return self._win.get_plugin_settings

    @get_plugin_settings.setter
    def get_plugin_settings(self, cb):
        self._win.get_plugin_settings = cb

    @property
    def on_wake_toggle(self):
        return self._win.on_wake_toggle

    @on_wake_toggle.setter
    def on_wake_toggle(self, cb):
        self._win.on_wake_toggle = cb

    @property
    def on_wake_manual(self):
        return self._win.on_wake_manual

    @on_wake_manual.setter
    def on_wake_manual(self, cb):
        self._win.on_wake_manual = cb

    @property
    def wake_get_state(self):
        return self._win.wake_get_state

    @wake_get_state.setter
    def wake_get_state(self, cb):
        self._win.wake_get_state = cb

    def set_audio_level(self, level: float) -> None:
        """Thread-safe: feed a 0.0–1.0 live audio level to the HUD waveform.
        Called from the audio threads; a plain float store is atomic under the
        GIL, so no signal/lock is needed for this cosmetic value."""
        try:
            self._win.hud.set_audio_level(level)
        except Exception:
            pass

    def glance(self, dx: float, dy: float, hold: float = 1.1) -> None:
        """Ask the avatar to look somewhere for a moment (see HoloAvatar.glance)."""
        try:
            if self._avatar is not None:
                self._avatar.glance(dx, dy, hold)
        except Exception:
            pass

    @property
    def ptt_hold(self):
        return self._win.ptt_hold

    @ptt_hold.setter
    def ptt_hold(self, cb):
        self._win.ptt_hold = cb

    @property
    def on_push_to_talk(self):
        return self._win.on_push_to_talk

    @on_push_to_talk.setter
    def on_push_to_talk(self, cb):
        self._win.on_push_to_talk = cb

    def push_visemes(self, frames, hop: float, at: float) -> None:
        """Thread-safe: post a schedule of (level, openness, width) mouth frames
        for JARVIS's own speech. `at` is the wall-clock time the batch begins to
        sound, not the time of the call. See HudCanvas.push_visemes()."""
        try:
            self._win.hud.push_visemes(frames, hop, at)
        except Exception:
            pass

    def notify_phone_connected(self) -> None:
        self._win.notify_phone_connected()

    def radar_command(self, cmd: str, payload=None) -> None:
        """Thread-safe: controls the market radar (plugins/market_radar.py and
        the alert loop in main.py). cmd: enable|disable|select|preview|alert|refresh."""
        try:
            self._win._radar_sig.emit(str(cmd), payload)
        except Exception:
            pass

    def show_place(self, lat: float, lon: float, label: str = "") -> None:
        """Thread-safe: expand the HUD's 3D globe and orbit the given
        coordinates. Called from action handlers running off the Qt thread —
        see actions/world_globe.py."""
        try:
            self._win._globe_flyto_sig.emit(float(lat), float(lon), str(label or ""))
        except Exception:
            pass

    def show_floating_images(self, sources: list, titles: list | None = None) -> None:
        """Thread-safe: opens each image as its own independent floating
        window over the HUD (draggable, closable, downloadable), wired to
        the core by the cyber-arrow connector, with the CRT power-on/off
        reveal. `sources` items may be file paths, raw image bytes, or
        QPixmap. Called from action handlers running off the Qt thread —
        see actions/show_images.py."""
        try:
            self._win._show_images_sig.emit(list(sources), list(titles) if titles else None)
        except Exception:
            pass

    def show_floating_videos(self, sources: list, titles: list | None = None) -> None:
        """Thread-safe: opens each YouTube video as its own independent
        floating window over the HUD, playing live via an embedded player
        (draggable, closable, 🌐 opens it in the real browser too), wired to
        the core by the cyber-arrow connector, with the same CRT power-on/off
        reveal as the image windows. `sources` items may be a full YouTube
        URL or a bare video id. Called from action handlers running off the
        Qt thread — see actions/youtube_video.py."""
        try:
            self._win._show_videos_sig.emit(list(sources), list(titles) if titles else None)
        except Exception:
            pass

    def show_lyrics(self) -> None:
        """Thread-safe: opens the floating, resizable synced-lyrics window for
        whatever Spotify is playing. Called from plugins/spotify_control.py,
        which runs off the Qt thread."""
        try:
            self._win._show_lyrics_sig.emit()
        except Exception:
            pass

    def show_mail_reader(self, messages: list) -> None:
        """Thread-safe: opens (or refreshes) the holographic mail reader window
        with `messages` — dicts from core.mail_client.get_message(). Called from
        plugins/mail_control.py, which runs off the Qt thread."""
        try:
            self._win._show_mail_sig.emit(list(messages))
        except Exception:
            pass

    def show_osint_panel(self, prefill: dict | None = None) -> None:
        """Thread-safe: opens the OSINT HUD window. Called from
        plugins/osint_search.py, which runs off the Qt thread."""
        try:
            self._win._show_osint_sig.emit(dict(prefill or {}))
        except Exception:
            pass

    def set_state(self, state: str):
        self._win._state_sig.emit(state)

    def write_log(self, text: str):
        self._win._log_sig.emit(text)

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def show_content(self, title: str, text: str):
        """Thread-safe: display content in the panel below the HUD."""
        self._win._content_sig.emit(title[:48], text[:4000])

    def show_quiz(self, topic: str, questions, grade=None) -> None:
        """Thread-safe: put an interactive quiz on the board.

        `grade(question, given)` decides each answer — the plugin supplies it so
        the marking rules live with the questions rather than being duplicated
        here. Returning None from it means "JARVIS should judge this one", which
        is how open answers and near-miss gap-fills are handled.

        Returns immediately: the user answers at their own pace and the finished
        result is delivered back through on_text_command.
        """
        self._win._quiz_sig.emit(str(topic or ""), list(questions or []), grade)

    def hide_quiz(self) -> None:
        """Thread-safe: clear any quiz currently on the board."""
        self._win._quiz_hide_sig.emit()

    def show_review(self, title: str, summary: str, findings, unclear=None) -> None:
        """Thread-safe: lay a document review into the panel below the HUD.

        `findings` is a list of {heading, detail, severity, quote, suggestion};
        severity is one of 'serious' / 'caution' / 'note' and decides colour and
        order here, so the caller supplies no styling of its own.
        """
        self._win._review_sig.emit(str(title or ""), str(summary or ""),
                                   list(findings or []), list(unclear or []))

    def prompt_reconfig(self):
        """Thread-safe: show the API key setup overlay (e.g. after an auth error)."""
        self._win._ready = False
        self._win._reconfig_sig.emit()

    def show_camera_frame(self, img_bytes: bytes):
        """Thread-safe: show a webcam frame in the small overlay (screen captures)."""
        self._win._camera_sig.emit(img_bytes)

    def start_camera_stream(self) -> None:
        """Thread-safe: start live camera feed in the full HUD area."""
        self._win.start_camera_stream()

    def stop_camera_stream(self) -> None:
        """Thread-safe: stop the live camera feed."""
        self._win.stop_camera_stream()

    @property
    def assistant_name(self) -> str:
        return self._win._assistant_name

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")
