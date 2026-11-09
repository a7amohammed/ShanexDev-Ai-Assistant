"""
Interactive low-poly 3D Earth for the HUD's central visual panel.

Idle: a small globe pinned to the top-right corner of the JARVIS core panel
(HudCanvas), rotating slowly on its Y axis — a companion piece to the core,
never covering it. Click it and it animates out to the panel's center,
where it can be dragged (rotate, any direction) and scrolled (zoom).
Double-click, the ✕ button, or ~20s without interaction sends it back to
its idle corner.

Voice search (`GlobeWidget.fly_to`) does the same expand-and-center
animation, then orbits slowly around the given coordinates while whatever
called it narrates over TTS — the two are independent (this widget knows
nothing about speech), which is what keeps the animation from ever being
able to block or be blocked by audio.

Requires PyQt6-WebEngine. Exactly like the HoloAvatar import guard already
in ui.py, this is allowed to be entirely absent: GlobeWidget then just
stays hidden and every method on it becomes a no-op, so the rest of the HUD
(Spotify player, file panels, everything) is never at risk over a missing
optional dependency.
"""
from __future__ import annotations

import webbrowser
import json
import math
import threading
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote

try:
    from core.live_cams import load_cams, find_cam
except Exception as _e:  # noqa: BLE001 — the camera layer is optional
    print(f"[Globe] Camera catalogue unavailable: {_e}")
    load_cams = lambda: []  # noqa: E731
    find_cam = lambda _id: None  # noqa: E731

from PyQt6.QtCore import Qt, QTimer, QRect, QRectF, QPropertyAnimation, QEasingCurve, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QPainter, QPixmap, QLinearGradient
from PyQt6.QtWidgets import (QWidget, QPushButton, QLabel, QGraphicsScene, QGraphicsPixmapItem,
                             QGraphicsBlurEffect, QGraphicsOpacityEffect)

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage
    _WEBENGINE_OK = True
except Exception as e:
    print(f"[Globe] PyQt6-WebEngine not available — globe will stay hidden: {e}")
    _WEBENGINE_OK = False


if _WEBENGINE_OK:
    class _LoggingPage(QWebEnginePage):
        """Same as QWebEnginePage, except JS console.log/warn/error and any
        uncaught script error inside the globe's page get printed to the
        terminal. Without this, a JS exception anywhere in _GLOBE_HTML just
        leaves a blank/frozen widget with zero clue why — Chromium's own
        devtools console isn't reachable from inside a plain QWebEngineView,
        so this is the only window into what actually broke."""

        def javaScriptConsoleMessage(self, level, message, line, source):
            print(f"[Globe][JS] {message} (line {line})")



# ── the 3D page itself ───────────────────────────────────────────────────────
# Self-contained: Three.js loads from a CDN (this is a real Chromium engine
# with normal internet access, not a sandboxed preview, so a <script src>
# works fine). If the earth texture can't be reached, makeFallbackLandTest()
# keeps the globe from ever rendering empty.
#
# Python <-> JS bridge: rather than pull in the full QWebChannel machinery
# for the few one-off signals this needs, JS reports events by setting
# document.title to "GLOBE_EVENT:<kind>:<timestamp>" and Python listens on
# QWebEngineView.titleChanged. Python -> JS calls are plain
# page().runJavaScript(...) one-liners. Both directions are intentionally
# thin: everything that can stay client-side (drag-rotate, scroll-zoom, idle
# spin) does, and Python is only involved for things that are fundamentally
# a Qt-widget-geometry change (expand/collapse) or that start from a voice
# command (fly_to).
_GLOBE_HTML = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://unpkg.com/maplibre-gl@4/dist/maplibre-gl.css">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;600&display=swap">
<style>
  html, body { margin:0; padding:0; overflow:hidden; background:transparent; }
  canvas { display:block; }
  /* Street-level map layer: sits above the 3D globe, fades in when the
     user zooms past the globe's own limit. Always clipped to this frame,
     whatever the widget's own shape (square idle / rectangular expanded). */
  #mapWrap {
    position:absolute; inset:0; opacity:0; pointer-events:none;
    transition: opacity 420ms ease; z-index: 10;
    background:#0a2236;
  }
  #mapEl { position:absolute; inset:0; }
  /* Cyberpunk HUD chrome for the map view */
  #mapHud {
    position:absolute; inset:0; pointer-events:none;
    border: 1px solid rgba(57,232,255,70);
    box-shadow: inset 0 0 30px rgba(57,232,255,55), inset 0 0 2px rgba(57,232,255,140);
  }
  #mapHud::before, #mapHud::after,
  #mapHud .c2, #mapHud .c3 {
    content:''; position:absolute; width:18px; height:18px;
    border: 2px solid #39e8ff;
  }
  #mapHud::before { top:-1px; left:-1px; border-right:none; border-bottom:none; }
  #mapHud::after  { top:-1px; right:-1px; border-left:none; border-bottom:none; }
  #mapHud .c2 { bottom:-1px; left:-1px; border-right:none; border-top:none; }
  #mapHud .c3 { bottom:-1px; right:-1px; border-left:none; border-top:none; }
  #mapCoords {
    position:absolute; left:8px; bottom:20px; pointer-events:none;
    font: 10px/1.4 "Courier New", monospace; color:#39e8ff;
    text-shadow: 0 0 6px rgba(57,232,255,90); letter-spacing:0.5px;
  }
  /* Required OpenFreeMap/OpenStreetMap attribution, shown as our own HUD
     text instead of MapLibre's default control widget. */
  #mapCredits {
    position:absolute; left:8px; bottom:6px; pointer-events:none;
    font: 9px/1.3 "Courier New", monospace; color:rgba(57,232,255,160);
    letter-spacing:0.3px;
  }
  /* Search bar: floats over the top edge of the 3D map. Sits inside
     #mapWrap so it naturally shows/hides and gains/loses click-through
     together with the map itself (pointer-events is inherited from the
     wrap, same trick the rest of the HUD chrome already relies on). */
  #mapSearchWrap {
    position:absolute; top:10px; left:50%; transform:translateX(-50%);
    display:flex; align-items:center; gap:6px;
    background:rgba(10,34,54,0.85); border:1px solid rgba(57,232,255,0.55);
    border-radius:5px; padding:5px 6px;
    box-shadow:0 0 14px rgba(57,232,255,0.22), inset 0 0 8px rgba(57,232,255,0.08);
  }
  #mapSearchInput {
    background:transparent; border:none; outline:none; color:#39e8ff;
    font:12px/1.4 "Courier New", monospace; width:210px; letter-spacing:0.3px;
  }
  #mapSearchInput::placeholder { color:rgba(57,232,255,0.45); }
  #mapSearchBtn {
    background:transparent; border:1px solid rgba(57,232,255,0.55); color:#39e8ff;
    border-radius:4px; width:24px; height:22px; line-height:1; cursor:pointer;
    font-size:13px; flex:none;
  }
  #mapSearchBtn:hover { background:rgba(57,232,255,0.18); }
  #mapSearchStatus {
    position:absolute; top:40px; left:50%; transform:translateX(-50%);
    font:10px/1.3 "Courier New", monospace; letter-spacing:0.3px;
    text-shadow:0 0 6px rgba(57,232,255,90); pointer-events:none;
    opacity:0; transition:opacity 220ms ease;
    max-width:80%; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
  }
  /* ── Holographic HUD: side wings OUTSIDE the map square ── */
  #stage { position:absolute; left:0; top:0; width:100%; height:100%; }
  /* Idle only: radial mask so the square canvas is not noticeable.
     Expanded state and the 3D map are left as they originally were. */
  #stage {
    -webkit-mask-image: radial-gradient(circle closest-side at 50% 50%, #000 86%, transparent 100%);
            mask-image: radial-gradient(circle closest-side at 50% 50%, #000 86%, transparent 100%);
  }
  body:not(.idle) #stage { -webkit-mask-image:none; mask-image:none; }
  .wing {
    position:absolute; top:40px; bottom:12px; width:var(--wingw, 190px); z-index:30; box-sizing:border-box;
    display:none; flex-direction:column; pointer-events:auto; color:#39e8ff;
    font:10px/1.35 Orbitron, Eurostile, "Segoe UI", "Courier New", monospace; letter-spacing:0.6px;
    text-shadow:0 0 6px rgba(57,232,255,0.75);
    background:none; border:none; box-shadow:none;
  }
  body.wings .wing { display:flex; }
  #livePanel { left:2px; padding:0 30px 0 8px; }
  #layerToggles { right:2px; padding:0 8px 0 30px; }
  /* vertical neon bar next to the map, with tick marks, like the news panel in the reference */
  .wing::before { content:''; position:absolute; top:4px; bottom:4px; width:3px; border-radius:2px;
    background:linear-gradient(to bottom, transparent, #39e8ff 12%, #39e8ff 88%, transparent);
    box-shadow:0 0 10px #39e8ff, 0 0 3px #fff; opacity:0.85; }
  #livePanel::before { right:2px; }
  #layerToggles::before { left:2px; }
  .wing::after { content:''; position:absolute; top:4px; bottom:4px; width:9px;
    background:repeating-linear-gradient(to bottom, rgba(57,232,255,0.55) 0 1px, transparent 1px 14px); opacity:0.7; }
  #livePanel::after { right:7px; }
  #layerToggles::after { left:7px; }
  .w-head { display:flex; justify-content:space-between; align-items:center; white-space:nowrap;
    font-size:clamp(7.5px, calc(var(--wingw, 190px) / 21), 9.5px); letter-spacing:1px; padding:2px 0 3px; }
  .w-head .live i { display:inline-block; width:7px; height:7px; border-radius:50%; background:#ff3d3d; margin-right:6px;
    box-shadow:0 0 8px #ff3d3d; animation:lpblink 1.4s infinite; vertical-align:middle; }
  @keyframes lpblink { 50% { opacity:0.2; } }
  .w-rule { height:1px; margin-bottom:6px; position:relative;
    background:linear-gradient(to right, #39e8ff, rgba(57,232,255,0.15)); box-shadow:0 0 6px #39e8ff; }
  #layerToggles .w-rule { background:linear-gradient(to left, #39e8ff, rgba(57,232,255,0.15)); }
  .w-rule::after { content:''; position:absolute; top:-2px; left:0; width:5px; height:5px; background:#39e8ff; transform:rotate(45deg); box-shadow:0 0 6px #39e8ff; }
  #layerToggles .w-rule::after { left:auto; right:0; }
  .gl { background:none; border:none; color:#39e8ff; cursor:pointer; font:inherit; font-size:13px; padding:0 4px; text-shadow:inherit; }
  .gl:hover { color:#fff; }
  #livePanel.min #lpBody { display:none; }
  #lpBody { display:flex; flex-direction:column; min-height:0; flex:1; }
  /* counter rings (RAM/CPU gauge style from the reference) */
  #lpStats { display:grid; grid-template-columns:repeat(3, 1fr); gap:4px 2px; padding-bottom:6px; }
  .ring-tile { position:relative; text-align:center; cursor:pointer; opacity:0.35; transition:opacity 160ms; }
  .ring-tile.on { opacity:1; }
  .ring-tile.static { cursor:default; opacity:1; }
  .ring-tile svg { width:100%; max-width:50px; height:auto; display:block; margin:0 auto; overflow:visible; }
  .ring-tile .bg { fill:none; stroke:rgba(57,232,255,0.2); stroke-width:3; }
  .ring-tile .fg { fill:none; stroke:var(--c); stroke-width:3.4; stroke-linecap:butt; transform:rotate(-90deg); transform-origin:50% 50%;
    filter:drop-shadow(0 0 3px var(--c)); transition:stroke-dasharray 600ms; }
  .ring-tile .in { fill:none; stroke:rgba(57,232,255,0.35); stroke-width:0.8; stroke-dasharray:2 3; }
  .ring-tile b { position:absolute; left:0; right:0; top:0; height:var(--rh, 44px); display:flex; align-items:center; justify-content:center;
    font-size:12px; color:#fff; text-shadow:0 0 6px var(--c); }
  .ring-tile span { display:block; font-size:6.5px; letter-spacing:0.3px; opacity:0.85; margin-top:1px; white-space:nowrap; }
  #lpFeed { overflow-y:auto; min-height:0; flex:1; padding-right:4px;
    -webkit-mask-image:linear-gradient(to bottom, transparent 0, #000 14px, #000 calc(100% - 18px), transparent 100%);
            mask-image:linear-gradient(to bottom, transparent 0, #000 14px, #000 calc(100% - 18px), transparent 100%); }
  #lpFeed::-webkit-scrollbar { width:0; }
  .lp-row { display:flex; gap:7px; padding:4px 2px 4px 0; cursor:pointer; position:relative; }
  .lp-row::after { content:''; position:absolute; left:0; right:0; bottom:0; height:1px;
    background:linear-gradient(to right, rgba(57,232,255,0.5), transparent); }
  .lp-row:hover .t { color:#fff; text-shadow:0 0 8px #39e8ff; }
  .lp-row i { width:6px; height:6px; margin-top:4px; flex:none; background:var(--c); transform:rotate(45deg); box-shadow:0 0 7px var(--c); }
  .lp-row > div { min-width:0; }
  .lp-row .t { color:#bff6ff; font-size:9.5px; line-height:1.3; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; word-break:break-word; }
  .lp-row .m { opacity:0.65; font-size:8px; letter-spacing:0.4px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  #lpFoot { padding-top:4px; font-size:7px; letter-spacing:0.5px; opacity:0.55; }
  /* holographic switches: rotating ring instead of a checkbox */
  .holo { display:flex; align-items:center; gap:8px; background:none; border:none; padding:3px 0; cursor:pointer;
    color:inherit; font:inherit; letter-spacing:inherit; text-shadow:inherit; text-align:left; }
  .holo svg { width:36px; height:36px; flex:none; overflow:visible; }
  .holo .hl { min-width:0; }
  .holo .r1 { fill:none; stroke:rgba(57,232,255,0.5); stroke-width:1.6; stroke-dasharray:7 5; transform-box:fill-box; transform-origin:center; }
  .holo .r2 { fill:none; stroke:rgba(57,232,255,0.35); stroke-width:1; }
  .holo .dot { fill:rgba(57,232,255,0); stroke:rgba(57,232,255,0.6); stroke-width:1.2; transition:fill 200ms; }
  .holo.on .r1 { stroke:#39e8ff; animation:holospin 6s linear infinite; filter:drop-shadow(0 0 4px #39e8ff); }
  .holo.on .r2 { stroke:#39e8ff; }
  .holo.on .dot { fill:#39e8ff; filter:drop-shadow(0 0 6px #39e8ff); }
  @keyframes holospin { to { transform:rotate(360deg); } }
  .holo .hl b { display:block; font-size:9px; letter-spacing:0.7px; font-weight:normal; color:#d7f8ff; white-space:nowrap; }
  .holo .hl em { display:block; font-style:normal; font-size:7.5px; letter-spacing:1px; opacity:0.6; }
  .holo.on .hl em { color:#7dffb0; opacity:1; }
  .holo:hover .hl b { color:#fff; }
  #layerStatus { font-size:8px; opacity:0.75; min-height:10px; margin:2px 0 4px; letter-spacing:0.5px; }
  .legend { font-size:7px; letter-spacing:0.6px; opacity:0.7; display:flex; gap:8px; flex-wrap:wrap; margin-bottom:6px; }
  .legend i { display:inline-block; width:6px; height:6px; border-radius:50%; margin-right:3px; vertical-align:middle; }
  /* detail card, also holographic and outside the map */
  #infoCard { display:none; position:relative; margin-top:4px; padding:6px 4px 4px 10px; min-height:0; overflow:auto; }
  #infoCard::-webkit-scrollbar { width:0; }
  #infoCard.show { display:block; }
  #infoCard::before { content:''; position:absolute; left:0; top:2px; bottom:2px; width:1px; background:linear-gradient(to bottom,#39e8ff,transparent); box-shadow:0 0 6px #39e8ff; }
  #infoCard::after { content:''; position:absolute; left:-2px; top:0; width:7px; height:7px; border-top:2px solid #39e8ff; border-left:2px solid #39e8ff; }
  #icClose { position:absolute; top:2px; right:0; }
  .ic-type { letter-spacing:2px; font-size:9px; }
  .ic-title { color:#fff; font-size:11px; margin:2px 0 4px; padding-right:16px; word-break:break-word; text-shadow:0 0 8px #39e8ff; }
  .ic-row { opacity:0.9; font-size:9px; padding:1px 0; }
  .ic-row b { color:#d7f8ff; font-weight:normal; }
  .ic-actions { display:flex; gap:12px; margin-top:6px; }
  .lp-btn { background:none; border:none; border-bottom:1px solid rgba(57,232,255,0.7); color:#39e8ff; cursor:pointer; font:inherit; font-size:8px;
    letter-spacing:1.2px; padding:1px 0; text-shadow:inherit; }
  .lp-btn:hover { color:#fff; border-bottom-color:#fff; }

  /* ── flight tracking ── */
  .ic-bar { height:3px; background:rgba(57,232,255,0.2); margin:3px 0; }
  .ic-bar i { display:block; height:100%; background:#7dffb0; box-shadow:0 0 6px #7dffb0; }
  .ic-sub { font-size:8px; opacity:0.7; margin:-2px 0 4px; letter-spacing:0.5px; }
  #followHud { position:absolute; left:0; right:0; bottom:10px; z-index:45; display:none; flex-direction:column;
    align-items:center; gap:5px; pointer-events:none; font:9px/1.3 Orbitron, "Courier New", monospace; color:#39e8ff;
    text-shadow:0 0 6px rgba(57,232,255,0.75); letter-spacing:0.6px; }
  body.following #followHud { display:flex; }
  #followHud button { pointer-events:auto; background:rgba(4,20,30,0.55); border:1px solid rgba(57,232,255,0.55);
    color:#39e8ff; font:inherit; font-size:8px; padding:3px 8px; cursor:pointer; letter-spacing:0.8px; }
  #followHud button:hover { background:rgba(57,232,255,0.2); color:#fff; }
  #followHud button.on { background:rgba(57,232,255,0.3); color:#fff; border-color:#39e8ff; box-shadow:0 0 8px #39e8ff; }
  #fhName { color:#fff; font-size:11px; letter-spacing:1.5px; }
  #fhTele { opacity:0.9; text-align:center; max-width:94%; }
  #fhRow { display:flex; gap:4px; flex-wrap:wrap; justify-content:center; }
  #fhExit { border-color:#ff3d3d !important; color:#ff7b7b !important; }
  #cockpitFrame { display:none; position:absolute; inset:0; pointer-events:none; z-index:20;
    background:
      linear-gradient(to top, rgba(2,10,16,0.96) 0, rgba(2,10,16,0.94) 10%, transparent 10.6%),
      linear-gradient(to right, rgba(2,10,16,0.92) 0, rgba(2,10,16,0.92) 3%, transparent 3.8%),
      linear-gradient(to left, rgba(2,10,16,0.92) 0, rgba(2,10,16,0.92) 3%, transparent 3.8%),
      linear-gradient(to bottom, rgba(2,10,16,0.92) 0, rgba(2,10,16,0.92) 3%, transparent 3.8%);
    box-shadow: inset 0 0 90px rgba(0,0,0,0.65); }
  #cockpitFrame::after { content:''; position:absolute; left:0; right:0; bottom:10%; height:1px;
    background:#39e8ff; box-shadow:0 0 8px #39e8ff; opacity:0.8; }
  body.cockpit #cockpitFrame { display:block; }
</style>
</head>
<body class="idle">
<div id="stage">
<div id="mapWrap">
  <div id="mapEl"></div>
  <div id="mapHud"><span class="c2"></span><span class="c3"></span></div>
  <div id="mapSearchWrap">
    <input id="mapSearchInput" type="text" placeholder="Search for a place…" autocomplete="off" spellcheck="false" />
    <button id="mapSearchBtn" type="button" title="Search">⌕</button>
  </div>
  <div id="mapSearchStatus"></div>
  <div id="mapCoords"></div>

  <div id="mapCredits">OpenFreeMap © OpenMapTiles · Data © OpenStreetMap contributors</div>
</div>
<div id="cockpitFrame"></div>
<div id="followHud">
  <div id="fhName">—</div>
  <div id="fhTele"></div>
  <div id="fhRow">
    <button data-fmode="cine">CINEMATIC</button><button data-fmode="cockpit">COCKPIT</button><button data-fmode="third">THIRD PERSON</button>
  </div>
  <div id="fhRow2" class="fhrow" style="display:flex;gap:4px;align-items:center">
    <span style="opacity:0.7">SIM</span>
    <button data-mul="1">×1</button><button data-mul="60">×60</button><button data-mul="200">×200</button><button data-mul="600">×600</button>
    <button id="fhExit">✕ EXIT</button>
  </div>
</div>
</div>
<div class="wing" id="livePanel">
  <div class="w-head"><span class="live"><i></i>GLOBAL ALERTS</span><button id="lpCollapse" class="gl" type="button" title="Collapse / expand">–</button></div>
  <div class="w-rule"></div>
  <div id="lpBody">
    <div id="lpStats"></div>
    <div id="lpFeed"></div>
    <div id="lpFoot">Loading data…</div>
  </div>
</div>
<div class="wing" id="layerToggles">
  <div class="w-head"><span>LAYERS</span><span></span></div>
  <div class="w-rule"></div>
  <button class="holo" id="tgRain" type="button"><svg viewBox="0 0 48 48"><circle class="r1" cx="24" cy="24" r="21"/><circle class="r2" cx="24" cy="24" r="15"/><circle class="dot" cx="24" cy="24" r="6"/></svg><span class="hl"><b>PRECIPITATION</b><em>INACTIVE</em></span></button>
  <button class="holo" id="tgFlights" type="button"><svg viewBox="0 0 48 48"><circle class="r1" cx="24" cy="24" r="21"/><circle class="r2" cx="24" cy="24" r="15"/><circle class="dot" cx="24" cy="24" r="6"/></svg><span class="hl"><b>FLIGHTS</b><em>INACTIVE</em></span></button>
  <button class="holo" id="tgCams" type="button"><svg viewBox="0 0 48 48"><circle class="r1" cx="24" cy="24" r="21"/><circle class="r2" cx="24" cy="24" r="15"/><circle class="dot" cx="24" cy="24" r="6"/></svg><span class="hl"><b>CAMERAS</b><em>INACTIVE</em></span></button>
  <div id="layerStatus"></div>
  <div class="legend"><span><i style="background:#ff4fd8"></i>camera</span><span><i style="background:#7dffb0"></i>&lt;3 km</span><span><i style="background:#ffe36b"></i>3-8 km</span><span><i style="background:#fff"></i>&gt;8 km</span></div>
  <div id="infoCard"><button id="icClose" class="lp-btn" type="button">×</button><div id="icBody"></div></div>
</div>
<script src="https://unpkg.com/three@0.160.0/build/three.min.js"></script>
<script src="https://unpkg.com/maplibre-gl@4/dist/maplibre-gl.js"></script>
<script>
(function(){
  const COLOR_LAND  = 0x39e8ff;
  const COLOR_OCEAN = 0x061a2b;
  const COLOR_GRID  = 0x2fd4f0;
  const COLOR_GLOW  = 0x39e8ff;
  const IDLE_COLLAPSE_MS = 20000;
  const MAP_STYLE_URL = 'https://tiles.openfreemap.org/styles/liberty';

  let scene, camera, renderer, group, glowMesh = null;
  let dragging = false, lastX = 0, lastY = 0;
  let rotY = 0, rotX = 0.15, targetRotY = 0, targetRotX = 0.15;
  let zoomDist = 3.4, targetZoom = 3.4;
  let expanded = false, autoRotate = true, orbiting = false;
  let lastInteract = Date.now();
  let map = null, mapLoading = false, inMapMode = false;

  function latLonToVec3(lat, lon, r){
    const phi = (90 - lat) * Math.PI/180;
    const theta = (lon + 180) * Math.PI/180;
    return new THREE.Vector3(
      -r * Math.sin(phi) * Math.cos(theta),
       r * Math.cos(phi),
       r * Math.sin(phi) * Math.sin(theta)
    );
  }

  function init(){
    scene = new THREE.Scene();
    layoutStage();
    const _sz = stageSize();
    camera = new THREE.PerspectiveCamera(42, _sz[0]/_sz[1], 0.1, 100);
    camera.position.set(0, 0, zoomDist);

    renderer = new THREE.WebGLRenderer({ antialias:true, alpha:true });
    renderer.setSize(_sz[0], _sz[1]);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    document.getElementById('stage').appendChild(renderer.domElement);

    group = new THREE.Group();
    scene.add(group);

    // low-poly faceted core
    const geo = new THREE.IcosahedronGeometry(1.0, 3);
    const mat = new THREE.MeshPhongMaterial({
      color: COLOR_OCEAN, flatShading:true, shininess:14,
      emissive: 0x03323f, emissiveIntensity: 0.6,
    });
    group.add(new THREE.Mesh(geo, mat));

    // cyan wireframe overlay — the "low-poly HUD" read
    const wgeo = new THREE.IcosahedronGeometry(1.012, 3);
    const wmat = new THREE.MeshBasicMaterial({
      color: COLOR_GRID, wireframe:true, transparent:true, opacity:0.5,
    });
    group.add(new THREE.Mesh(wgeo, wmat));

    // continents: a real high-resolution earth texture, recolored by a
    // shader into the flat JARVIS land/ocean palette instead of shown as a
    // photographic/satellite image. Detail comes straight from the texture
    // pixels — the actual coastline raster — so it's fully detailed at all
    // times, with no simplified/low-poly state and nothing that changes
    // with zoom.
    buildContinentLayer();

    // atmosphere glow shell (Fresnel rim light — classic three.js earth trick)
    const agGeo = new THREE.SphereGeometry(1.16, 32, 24);
    const agMat = new THREE.ShaderMaterial({
      transparent:true, blending: THREE.AdditiveBlending, side: THREE.BackSide,
      uniforms: { glowColor: { value: new THREE.Color(COLOR_GLOW) } },
      vertexShader:
        'varying float intensity;' +
        'void main(){' +
        '  vec3 vNormal = normalize(normalMatrix * normal);' +
        '  intensity = pow(0.68 - dot(vNormal, vec3(0.0,0.0,1.0)), 2.5);' +
        '  gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0);' +
        '}',
      fragmentShader:
        'uniform vec3 glowColor; varying float intensity;' +
        'void main(){ gl_FragColor = vec4(glowColor, 1.0) * intensity; }',
    });
    glowMesh = new THREE.Mesh(agGeo, agMat);
    group.add(glowMesh);

    scene.add(new THREE.AmbientLight(0x2a4a55, 1.3));
    const sun = new THREE.DirectionalLight(0x9fe8ff, 1.1);
    sun.position.set(3, 2, 4);
    scene.add(sun);

    window.addEventListener('resize', onResize);
    const el = renderer.domElement;
    el.addEventListener('mousedown', onDown);
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    el.addEventListener('wheel', onWheel, {passive:false});
    el.addEventListener('dblclick', onDblClick);

    setInterval(checkIdle, 2000);
    bindSearchUI();
    initLive();
    animate();
  }

  // ── continents: shader-recolored real earth texture ─────────────────────
  // A smooth sphere carries a real earth texture, but a fragment shader
  // recolors it into flat land/ocean colors instead of showing it as a
  // photo — keeps the "not satellite" JARVIS look while every coastline
  // detail the source raster has is visible at all times.
  //
  // Land/ocean is decided from earth's *specular* map rather than its color
  // photo: oceans are bright there (they reflect), land is dark, cleanly
  // and uniformly — no more false "ocean" reads inside a dark rainforest or
  // a bright desert punching holes in Amazonia/the Sahara.
  let landMaterial = null;

  function buildContinentLayer(){
    Promise.all([
      loadTexture('https://threejs.org/examples/textures/planets/earth_atmos_2048.jpg'),
      loadTexture('https://threejs.org/examples/textures/planets/earth_specular_2048.jpg'),
    ]).then(([colorTex, maskTex]) => {
      landMaterial = makeLandShaderMaterial(colorTex, maskTex);
      group.add(new THREE.Mesh(new THREE.SphereGeometry(1.014, 128, 64), landMaterial));
    }).catch(() => {
      // No network / CDN blocked: procedural continents generated entirely
      // in the shader, so the globe is never left blank.
      landMaterial = makeProceduralLandMaterial();
      group.add(new THREE.Mesh(new THREE.SphereGeometry(1.014, 128, 64), landMaterial));
    });
  }

  function loadTexture(url){
    return new Promise((resolve, reject) => {
      new THREE.TextureLoader().load(url, resolve, undefined, reject);
    });
  }

  // colorTex gives land its subtle shading; maskTex (specular map) decides
  // land vs. ocean. A fine cyan lattice is layered on top of land, its
  // opacity driven by the `detailT` uniform (0 far away .. 1 zoomed in),
  // so up close the continents visibly resolve extra structure — the
  // "gains detail on zoom" read — without swapping geometry or resolution.
  function makeLandShaderMaterial(colorTex, maskTex){
    const c = new THREE.Color(COLOR_LAND);
    return new THREE.ShaderMaterial({
      transparent: true,
      uniforms: {
        earthTex: { value: colorTex },
        maskTex:  { value: maskTex },
        landColor: { value: c },
        detailT: { value: 0 },
      },
      vertexShader: `
        varying vec2 vUv;
        void main(){
          vUv = uv;
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }
      `,
      fragmentShader: `
        uniform sampler2D earthTex;
        uniform sampler2D maskTex;
        uniform vec3 landColor;
        uniform float detailT;
        varying vec2 vUv;
        void main(){
          vec4 mask = texture2D(maskTex, vUv);
          float specBright = (mask.r + mask.g + mask.b) / 3.0;
          if (specBright > 0.5) discard; // bright in the specular map = ocean
          vec4 tex = texture2D(earthTex, vUv);
          float bright = (tex.r + tex.g + tex.b) / 3.0;
          vec3 col = landColor * (0.6 + bright * 0.55);
          // fine lattice that only resolves in as detailT rises with zoom
          vec2 g = vUv * 900.0;
          float cell = min(fract(g.x), fract(g.y));
          float seam = 1.0 - smoothstep(0.0, 0.05, cell);
          col = mix(col, vec3(1.0), seam * detailT * 0.45);
          gl_FragColor = vec4(col, 0.92);
        }
      `,
    });
  }

  // Procedural fallback: generates pseudo-continents purely in the shader
  // (no image needed at all), used only if the CDN textures can't load.
  // Shares the same detailT-driven lattice as the real-texture material.
  function makeProceduralLandMaterial(){
    const c = new THREE.Color(COLOR_LAND);
    return new THREE.ShaderMaterial({
      transparent: true,
      uniforms: { landColor: { value: c }, detailT: { value: 0 } },
      vertexShader: `
        varying vec3 vPos;
        varying vec2 vUv;
        void main(){
          vPos = position;
          vUv = uv;
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }
      `,
      fragmentShader: `
        uniform vec3 landColor;
        uniform float detailT;
        varying vec3 vPos;
        varying vec2 vUv;
        float hash(vec2 p){ return fract(sin(dot(p, vec2(12.9898,78.233))) * 43758.5453); }
        float noise(vec2 p){
          vec2 i = floor(p), f = fract(p);
          float a = hash(i), b = hash(i+vec2(1.0,0.0));
          float c2 = hash(i+vec2(0.0,1.0)), d = hash(i+vec2(1.0,1.0));
          vec2 u = f*f*(3.0-2.0*f);
          return mix(a,b,u.x) + (c2-a)*u.y*(1.0-u.x) + (d-b)*u.x*u.y;
        }
        void main(){
          vec3 n = normalize(vPos);
          float lat = degrees(asin(n.y));
          float lon = degrees(atan(n.z, -n.x));
          float n1 = noise(vec2(lon*0.06, lat*0.06));
          float n2 = noise(vec2(lon*0.16, lat*0.16)) * 0.5;
          float v = n1 + n2;
          if (abs(lat) > 78.0 || v < 0.62) discard;
          vec3 col = landColor;
          vec2 g = vUv * 900.0;
          float cell = min(fract(g.x), fract(g.y));
          float seam = 1.0 - smoothstep(0.0, 0.05, cell);
          col = mix(col, vec3(1.0), seam * detailT * 0.45);
          gl_FragColor = vec4(col, 0.9);
        }
      `,
    });
  }

  // zoomDist ranges ~1.5 (closest) .. 6.0 (farthest); the lattice ramps in
  // below ~2.6, fully resolved by ~1.6.
  function updateLandDetail(){
    if (!landMaterial) return;
    const t = FOLLOW.on ? 1 : 1 - Math.min(1, Math.max(0, (zoomDist - 1.6) / (2.6 - 1.6)));
    landMaterial.uniforms.detailT.value = t;
  }

  function markInteract(){
    lastInteract = Date.now();
    if (orbiting){ orbiting = false; notify('interacted'); }
  }
  function onDown(e){ dragging = true; lastX = e.clientX; lastY = e.clientY; downX = e.clientX; downY = e.clientY; autoRotate = false; markInteract(); }
  function onMove(e){
    if (!dragging) return;
    const dx = e.clientX - lastX, dy = e.clientY - lastY;
    lastX = e.clientX; lastY = e.clientY;
    if (FOLLOW.on){
      if (FOLLOW.mode === 'third'){
        FOLLOW.yaw += dx * 0.006;
        FOLLOW.pitch = Math.max(-0.25, Math.min(1.2, FOLLOW.pitch + dy * 0.006));
      } else if (FOLLOW.mode === 'cockpit'){
        FOLLOW.yaw = Math.max(-2.6, Math.min(2.6, FOLLOW.yaw - dx * 0.005));
        FOLLOW.pitch = Math.max(-1.0, Math.min(1.0, FOLLOW.pitch + dy * 0.005));
      }
      markInteract();
      return;
    }
    targetRotY += dx * 0.005;
    targetRotX = Math.max(-1.3, Math.min(1.3, targetRotX + dy * 0.005));
    markInteract();
  }
  function onUp(e){
    const was = dragging; dragging = false;
    // a click (without dragging) on the globe selects the marker underneath
    if (was && !inMapMode && !FOLLOW.on && e && Math.hypot(e.clientX - downX, e.clientY - downY) < 4) pickGlobe(e);
  }
  function onWheel(e){
    if (inMapMode) return; // maplibre owns wheel-zoom on #mapEl while visible
    e.preventDefault();
    if (FOLLOW.on){
      if (FOLLOW.mode === 'third') FOLLOW.dist = Math.max(0.004, Math.min(0.06, FOLLOW.dist * (1 + e.deltaY * 0.0012)));
      markInteract();
      return;
    }
    const wasAtFloor = targetZoom <= 1.5 + 1e-6;
    targetZoom = Math.max(1.5, Math.min(6.0, targetZoom + e.deltaY * 0.0018));
    markInteract();
    // Already at the globe's closest zoom and still scrolling in further:
    // that's the cue to drop into the real street-level map.
    if (wasAtFloor && e.deltaY < 0) enterMapMode();
  }
  function onDblClick(){ notify('collapse'); }
  function checkIdle(){
    if (expanded && !dragging && (Date.now() - lastInteract) > IDLE_COLLAPSE_MS) notify('collapse');
  }
  function notify(kind){ document.title = 'GLOBE_EVENT:' + kind + ':' + Date.now(); }

  // The map/globe lives in a centred square (#stage); at its sides, outside it,
  // sit the holographic wings. If the widget is not wide enough (idle globe
  // or animating), the stage fills everything and the wings are hidden.
  function stageSize(){
    const st = document.getElementById('stage');
    return [st.clientWidth || window.innerWidth, st.clientHeight || window.innerHeight];
  }
  let _lastLayout = '';
  function layoutStage(){
    const W = window.innerWidth, H = window.innerHeight, st = document.getElementById('stage');
    const GAP  = 22;                                            // gap between each wing and the square
    const wing = Math.max(140, Math.min(200, Math.round(W * 0.17)));
    const S    = Math.min(H, W - 2 * (wing + GAP + 2));         // side of the map square
    const wide = expanded && W > H * 1.12 && S >= H * 0.45;
    if (!wide){
      st.style.left = '0px'; st.style.top = '0px'; st.style.width = '100%'; st.style.height = '100%';
      document.body.classList.remove('wings');
    } else {
      st.style.left = Math.round((W - S) / 2) + 'px'; st.style.top = Math.round((H - S) / 2) + 'px';
      st.style.width = Math.round(S) + 'px'; st.style.height = Math.round(S) + 'px';
      document.documentElement.style.setProperty('--wingw', wing + 'px');
      document.body.classList.add('wings');
    }
    const tag = (expanded ? 'exp ' : 'idle ') + (wide ? 'alas' : 'sin-alas');
    if (tag !== _lastLayout){ _lastLayout = tag; console.log('[Globe] layout ' + tag + ' ' + W + 'x' + H + ' map=' + Math.round(wide ? S : Math.min(W, H))); }
  }
  function onResize(){
    layoutStage();
    const sz = stageSize();
    camera.aspect = sz[0]/sz[1];
    camera.updateProjectionMatrix();
    renderer.setSize(sz[0], sz[1]);
    // The map is a plain absolutely-positioned div clipped to the stage.
    if (map) map.resize();
  }

  // ── street-level map: real OpenStreetMap data (roads, admin borders,
  // country/province/city/town/place labels, squares, POIs) via
  // OpenFreeMap's free public MapLibre tiles — no API key, no request
  // limit. The style is re-themed to the cyan/black JARVIS palette instead
  // of OpenFreeMap's default look. ─────────────────────────────────────────

  function frontLatLon(){
    // Which point on the (rotated) globe is currently facing the camera —
    // i.e. what the user is actually zoomed in on — found by rotating the
    // camera's world-space direction back into the globe's local space.
    const invQuat = group.quaternion.clone().invert();
    const dir = new THREE.Vector3(0, 0, 1).applyQuaternion(invQuat).normalize();
    const y = Math.max(-1, Math.min(1, dir.y));
    const phi = Math.acos(y);
    const theta = Math.atan2(dir.z, -dir.x);
    const lat = 90 - phi * 180 / Math.PI;
    let lon = theta * 180 / Math.PI - 180;
    lon = ((lon + 540) % 360) - 180;
    return { lat, lon };
  }

  function ensureMap(){
    if (map) return Promise.resolve(map);
    if (mapLoading) return mapLoading;
    mapLoading = fetch(MAP_STYLE_URL).then(r => r.json()).then(style => {
      cyberpunkify(style);
      map = new maplibregl.Map({
        container: 'mapEl',
        style: style,
        center: [0, 0],
        zoom: 15,
        pitch: 45,               // tilted, but much cheaper than 62 — at steep
                                  // pitch MapLibre has to load building+DEM
                                  // tiles all the way to the horizon at once
        bearing: 0,
        attributionControl: false, // custom HUD credit shown in #mapCredits instead
        dragRotate: true,        // right-drag: orbit/tilt the 3D scene manually
        touchPitch: true,
        maxPitch: 60,
        maxTileCacheSize: 40,     // cap tile memory instead of unbounded growth
        // Cap the map's own render resolution — on a HiDPI screen this alone
        // roughly halves (or quarters) the pixels MapLibre has to shade.
        pixelRatio: Math.min(window.devicePixelRatio || 1, 1.5),
      });
      // Icons that the style sprite does not include (lift_gate, atm, office…): they are
      // replaced by a transparent pixel instead of warning once per icon.
      map.on('styleimagemissing', ev => {
        try { if (!map.hasImage(ev.id)) map.addImage(ev.id, { width:1, height:1, data:new Uint8Array(4) }); } catch (_) {}
      });
      // Map errors: each is shown only once; and if the 3D relief fails
      // when computing elevations, only that is disabled and the map stays flat
      // instead of repeating the error on every frame.
      const seenMapErr = new Set(); let terrainOff = false;
      map.on('error', ev => {
        const msg = String((ev && ev.error && ev.error.message) || (ev && ev.message) || ev);
        if (/could not be loaded|styleimagemissing/.test(msg)) return;
        if (/elevation maxzoom/.test(msg)){
          if (!terrainOff){
            terrainOff = true;
            try { map.setTerrain(null); } catch (_) {}
            console.warn('[Globe] 3D relief disabled (elevation error); the map stays flat');
          }
          return;
        }
        const key = msg.slice(0, 140);
        if (seenMapErr.has(key)) return;
        seenMapErr.add(key);
        console.warn('[Globe] map: ' + msg + (ev && ev.sourceId ? ' (source ' + ev.sourceId + ')' : ''));
      });
      map.doubleClickZoom.disable();
      map.on('dblclick', exitMapMode);
      map.on('zoomend', () => { if (map.getZoom() < 3) exitMapMode(); });
      map.on('move', () => { lastInteract = Date.now(); updateMapCoordsReadout(); });
      map.on('click', () => { lastInteract = Date.now(); });
      map.on('load', () => { addTerrainAndSky(map); mapReady = true; bindMapLive(map); });
      // GPU got overloaded and the browser yanked the WebGL context: without
      // this the map just goes permanently blank/broken. MapLibre can
      // rebuild everything from its own tile cache once the context is
      // restored, so just let it — no reload needed.
      map.getCanvas().addEventListener('webglcontextlost', (e) => {
        e.preventDefault();
        console.warn('[Globe] map WebGL context lost — will auto-recover');
      }, false);
      return map;
    }).catch((e) => {
      console.error('[Globe] street map style failed to load:', e);
      mapLoading = null;
      throw e;
    });
    return mapLoading;
  }

  // Real 3D terrain — mountains, valleys, elevation — from a free public DEM
  // (AWS's open Terrarium tiles, no key, no request limit) plus a matching
  // atmosphere/sky layer so the horizon reads as HUD haze, not a hard edge.
  // Safe to call once per map instance; MapLibre no-ops a duplicate source.
  function addTerrainAndSky(m){
    try {
      if (!m.getSource('terrain-dem')){
        m.addSource('terrain-dem', {
          type: 'raster-dem',
          tiles: ['https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png'],
          tileSize: 256,
          encoding: 'terrarium',
          maxzoom: 15,
        });
      }
      // Lower exaggeration + a lower maxzoom on the DEM source above both
      // trim how much elevation geometry gets built per frame at a steep
      // pitch, which was the other big GPU cost alongside the buildings.
      m.setTerrain({ source: 'terrain-dem', exaggeration: 1.1 });
      // The 'sky' layer type is Mapbox-only: MapLibre 4 rejects it.
      // Only MapLibre 5+ provides map.setSky(); with version 4 it is simply not used.
      if (typeof m.setSky === 'function'){
        try { m.setSky({ 'sky-color':'#0a2236', 'horizon-color':'#39e8ff', 'fog-color':'#0a2236',
                         'sky-horizon-blend':0.5, 'horizon-fog-blend':0.5, 'fog-ground-blend':0.3 }); } catch (_) {}
      }
    } catch (e) { console.warn('[Globe] terrain/sky unavailable:', e); }
  }

  // Re-themes an OpenFreeMap "liberty" style (roads, borders, buildings,
  // water, place/POI labels — everything real OSM data provides) from its
  // default palette into neon-cyan streets/labels on a navy (not near-black)
  // background, without touching which real-world features it draws.
  function cyberpunkify(style){
    const CYAN = '#39e8ff', BORDER = '#ff3df0';
    const BG = '#0a2236';       // page/background — navy, not near-black
    const WATER = '#0d3355';    // a touch richer than BG, still navy
    const LAND = '#123249';     // building/land fills — a touch lighter than BG
    const DIM = '#2fb9e0';      // glowing cyan for 3D building extrusions
    (style.layers || []).forEach(layer => {
      const p = layer.paint || (layer.paint = {});
      const key = (layer.id || '') + ' ' + (layer['source-layer'] || '');
      if (layer.type === 'background'){
        p['background-color'] = BG;
      } else if (layer.type === 'fill'){
        p['fill-color'] = /water|ocean|sea|lake/i.test(key) ? WATER : LAND;
        if (p['fill-opacity'] === undefined) p['fill-opacity'] = 0.95;
      } else if (layer.type === 'fill-extrusion'){
        // Real building footprints + real heights from OSM (untouched —
        // only the colour/opacity change here), now actually visible now
        // that the camera is pitched instead of looking straight down.
        p['fill-extrusion-color'] = DIM;
        p['fill-extrusion-opacity'] = 0.82;
        p['fill-extrusion-vertical-gradient'] = true;
        p['fill-extrusion-height'] = ['to-number', ['get', 'render_height'], 0];
        p['fill-extrusion-base'] = ['to-number', ['get', 'render_min_height'], 0];
      } else if (layer.type === 'line'){
        const isBoundary = /boundary|admin/i.test(key);
        p['line-color'] = isBoundary ? BORDER : CYAN;
        if (p['line-width'] === undefined) p['line-width'] = isBoundary ? 1.2 : 1;
      } else if (layer.type === 'symbol'){
        p['text-color'] = CYAN;
        p['text-halo-color'] = '#000000';
        p['text-halo-width'] = 1.1;
        if (p['icon-color'] !== undefined) p['icon-color'] = CYAN;
      }
    });
  }

  function updateMapCoordsReadout(){
    if (!map) return;
    const c = map.getCenter();
    const el2 = document.getElementById('mapCoords');
    if (el2) el2.textContent = c.lat.toFixed(4) + '°, ' + c.lng.toFixed(4) + '°  Z' + map.getZoom().toFixed(1);
  }

  // ── search bar: type a place, fly the 3D map straight to it ─────────────
  // Geocoded via Nominatim (OpenStreetMap's free public geocoder — same
  // service actions/world_globe.py already uses server-side for voice
  // requests, just called from the client here instead).
  let searchStatusTimer = null;
  function setSearchStatus(text, isError, autoHideMs){
    const el = document.getElementById('mapSearchStatus');
    if (!el) return;
    clearTimeout(searchStatusTimer);
    el.textContent = text || '';
    el.style.color = isError ? '#ff3df0' : '#39e8ff';
    el.style.opacity = text ? '1' : '0';
    if (text && autoHideMs) searchStatusTimer = setTimeout(() => { el.style.opacity = '0'; }, autoHideMs);
  }

  function geocodeSearch(query){
    const url = 'https://nominatim.openstreetmap.org/search?format=json&limit=1&q=' + encodeURIComponent(query);
    return fetch(url, { headers: { 'Accept': 'application/json' } }).then(r => {
      if (!r.ok) throw new Error('geocode http ' + r.status);
      return r.json();
    }).then(data => {
      if (!data || !data.length) return null;
      return { lat: parseFloat(data[0].lat), lon: parseFloat(data[0].lon), label: data[0].display_name };
    });
  }

  function runMapSearch(){
    const input = document.getElementById('mapSearchInput');
    const query = (input.value || '').trim();
    if (!query) return;
    setSearchStatus('Searching…', false);
    geocodeSearch(query).then(place => {
      if (!place){ setSearchStatus('That place was not found', true, 3000); return; }
      setSearchStatus(place.label, false, 4000);
      input.blur();
      lastInteract = Date.now();
      ensureMap().then(m => {
        m.flyTo({ center: [place.lon, place.lat], zoom: 16.2, pitch: 62, bearing: 0, essential: true });
      });
    }).catch(() => setSearchStatus('Search error', true, 3000));
  }

  function bindSearchUI(){
    const input = document.getElementById('mapSearchInput');
    const btn = document.getElementById('mapSearchBtn');
    const wrap = document.getElementById('mapSearchWrap');
    if (!input || !btn || !wrap) return;
    // Keep the widget's own drag/wheel/dblclick handlers on the canvas
    // beneath from ever seeing these interactions.
    ['mousedown', 'wheel', 'dblclick', 'click'].forEach(evt => {
      wrap.addEventListener(evt, e => e.stopPropagation());
    });
    btn.addEventListener('click', runMapSearch);
    input.addEventListener('keydown', e => {
      e.stopPropagation();
      if (e.key === 'Enter') runMapSearch();
    });
  }


  function enterMapMode(forcedLatLon){
    if (inMapMode) return;
    inMapMode = true;
    autoRotate = false; orbiting = false;
    const { lat, lon } = forcedLatLon || frontLatLon();
    const wrap = document.getElementById('mapWrap');
    wrap.style.opacity = '1';
    wrap.style.pointerEvents = 'auto';
    ensureMap().then(m => {
      m.jumpTo({ center: [lon, lat], zoom: 16.2, pitch: 62, bearing: 0 });
      setTimeout(() => m.resize(), 80);
      updateMapCoordsReadout();
      if (LIVE.flightsOn) scheduleFlights(2500);
    }).catch(() => { exitMapMode(); });
  }

  function exitMapMode(){
    if (!inMapMode) return;
    inMapMode = false;
    const wrap = document.getElementById('mapWrap');
    wrap.style.opacity = '0';
    wrap.style.pointerEvents = 'none';
    targetZoom = 1.9; // land back a little off the globe's own zoom wall
    lastInteract = Date.now();
    if (LIVE.flightsOn) scheduleFlights(1500);
  }

  function animate(){
    requestAnimationFrame(animate);
    if (autoRotate && !dragging) targetRotY += 0.0022;
    if (orbiting) targetRotY += 0.0009;
    rotY += (targetRotY - rotY) * 0.12;
    rotX += (targetRotX - rotX) * 0.12;
    zoomDist += (targetZoom - zoomDist) * 0.12;
    if (FOLLOW.on){
      group.rotation.set(0, 0, 0);
      updateFollow();
    } else {
      group.rotation.y = rotY;
      group.rotation.x = rotX;
      camera.position.z = zoomDist;
    }
    updateLandDetail();
    // Never render the globe while the map is up — it's a second full-screen
    // WebGL context sitting directly beneath MapLibre and drawing at 60fps
    // even though it's fully covered. Running both engines at once (plus
    // 3D terrain/buildings) is what was starving the GPU and killing the
    // map's context. The rAF loop keeps ticking (cheap: just state easing)
    // so rotation/zoom stay in sync for the moment we come back out of map
    // mode, but the expensive draw call is skipped entirely.
    if (!inMapMode) renderer.render(scene, camera);
  }

  // ── called from Python ──────────────────────────────────────────────────
  window.setExpanded = function(v){
    expanded = !!v;
    document.body.classList.toggle('idle', !expanded);
    onResize();
    autoRotate = !expanded;
    if (!expanded){
      orbiting = false; targetRotX = 0.15; targetZoom = 3.4;
      stopFollow(false); hideCard();
      if (inMapMode) exitMapMode();
    }
    lastInteract = Date.now();
  };

  window.flyTo = function(lat, lon, label){
    document.body.classList.remove('idle');
    expanded = true; onResize(); autoRotate = false; orbiting = true;
    lastInteract = Date.now();
    const theta = (lon + 180) * Math.PI/180;
    const phi   = (90 - lat) * Math.PI/180;
    targetRotY = -theta + Math.PI/2;
    targetRotX = Math.max(-1.2, Math.min(1.2, (Math.PI/2 - phi)));
    targetZoom = 1.9;
  };

  // Same voice-search entry point as flyTo, but — per the "show me a
  // place" request — goes all the way to the street-level 3D map instead
  // of just orbiting the low-poly globe from a distance. The globe still
  // swings to face the point first (a hard cut straight into street level
  // reads as broken), then drops into the map a beat later.
  window.flyToMap = function(lat, lon, label){
    lastInteract = Date.now();
    // Map's already open (e.g. a second voice request while it's up, or
    // the user opened it by hand first): enterMapMode() below would just
    // no-op since it only handles globe -> map, not map -> new place, so
    // just re-target the live map straight away instead.
    if (inMapMode){
      ensureMap().then(m => {
        m.flyTo({ center: [lon, lat], zoom: 16.2, pitch: 62, bearing: 0, essential: true });
      });
      if (label) setSearchStatus(label, false, 4000);
      return;
    }
    document.body.classList.remove('idle');
    expanded = true; onResize(); autoRotate = false; orbiting = false;
    const theta = (lon + 180) * Math.PI/180;
    const phi   = (90 - lat) * Math.PI/180;
    targetRotY = -theta + Math.PI/2;
    targetRotX = Math.max(-1.2, Math.min(1.2, (Math.PI/2 - phi)));
    targetZoom = 1.5;
    setTimeout(() => {
      enterMapMode({ lat, lon });
      if (label) setSearchStatus(label, false, 4000);
    }, 550);
  };

  // ══ LIVE EARTH ═══════════════════════════════════════════════════════
  // Real data, no API keys, requested directly from this page:
  //   · Earthquakes ..... USGS (M4.5+, last 24 h)
  //   · Fires, storms/hurricanes, volcanoes, floods .. NASA EONET
  //   · Precipitation ... RainViewer (weather radar mosaic)
  //   · Flights .......... OpenSky Network (public ADS-B, no key)
  const $ = id => document.getElementById(id);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const TYPES = {
    quake:   { label:'QUAKES',    one:'EARTHQUAKE',               color:'#ff9f1c' },
    fire:    { label:'FIRES',     one:'FIRE',            color:'#ff3d3d' },
    storm:   { label:'STORMS',    one:'STORM / HURRICANE',  color:'#b36bff' },
    volcano: { label:'VOLCANOES', one:'VOLCANO',              color:'#ffd23f' },
    flood:   { label:'FLOODS',    one:'FLOOD',          color:'#4d8dff' },
  };
  const LIVE = {
    events: [], flights: [], flightIdx: new Map(),
    filters: new Set(Object.keys(TYPES)),
    rainOn: false, flightsOn: false, camsOn: false, rainFrame: null, selFlight: null,
  };
  // Public live cameras (injected by Python from core/live_cams.py)
  const CAMS = __LIVE_CAMS_JSON__;
  const CAM_COLOR = '#ff4fd8';
  let camPoints = null;
  let downX = 0, downY = 0, mapReady = false, mapLiveBound = false;
  let dotTex = null, evPoints = null, flPoints = null, selPoints = null, rainMesh = null;
  let flightTimer = null, lastFlightFetch = 0, flightBackoff = 0, moveTimer = null;

  function timeAgo(ms){
    if (!ms) return '';
    const s = Math.max(0, (Date.now() - ms) / 1000);
    if (s < 3600)  return Math.max(1, Math.round(s / 60)) + ' min ago';
    if (s < 86400) return Math.round(s / 3600) + ' h ago';
    return Math.round(s / 86400) + ' d ago';
  }
  function visibleEv(e){ return LIVE.filters.has(e.type); }

  // ── 1) events: USGS + NASA EONET ─────────────────────────────────────────
  function fetchQuakes(){
    return fetch('https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson')
      .then(r => { if (!r.ok) throw new Error('usgs ' + r.status); return r.json(); })
      .then(j => (j.features || []).map(f => ({
        id: 'q' + f.id, type: 'quake', title: f.properties.place || 'Earthquake',
        lat: f.geometry.coordinates[1], lon: f.geometry.coordinates[0],
        time: f.properties.time, mag: f.properties.mag || 0,
        detail: 'M' + (f.properties.mag || 0).toFixed(1) + ' · prof. ' + Math.round(f.geometry.coordinates[2]) + ' km',
        url: f.properties.url,
      })));
  }
  function geomCenter(g){
    if (!g || !g.coordinates) return null;
    if (g.type === 'Point') return g.coordinates;
    if (g.type === 'Polygon'){
      const ring = g.coordinates[0]; let x = 0, y = 0;
      ring.forEach(p => { x += p[0]; y += p[1]; });
      return [x / ring.length, y / ring.length];
    }
    return null;
  }
  function fetchEonet(cat, type, limit){
    const url = 'https://eonet.gsfc.nasa.gov/api/v3/events?status=open&limit=' + limit + '&category=' + cat;
    return fetch(url).then(r => { if (!r.ok) throw new Error('eonet ' + r.status); return r.json(); })
      .then(j => (j.events || []).map(ev => {
        const g = ev.geometry && ev.geometry[ev.geometry.length - 1];
        const c = geomCenter(g);
        if (!c) return null;
        let detail = '';
        if (g.magnitudeValue != null) detail = g.magnitudeValue + ' ' + (g.magnitudeUnit || '');
        return {
          id: 'e' + ev.id, type: type, title: ev.title, lat: c[1], lon: c[0],
          time: Date.parse(g.date), detail: detail,
          url: (ev.sources && ev.sources[0] && ev.sources[0].url) || ev.link,
        };
      }).filter(Boolean));
  }
  function refreshEvents(){
    return Promise.allSettled([
      fetchQuakes(),
      fetchEonet('wildfires', 'fire', 60),
      fetchEonet('severeStorms', 'storm', 40),
      fetchEonet('volcanoes', 'volcano', 30),
      fetchEonet('floods', 'flood', 30),
    ]).then(res => {
      const ok = res.filter(r => r.status === 'fulfilled');
      if (!ok.length){ $('lpFoot').textContent = 'No connection to the sources'; return; }
      LIVE.events = ok.flatMap(r => r.value).sort((a, b) => (b.time || 0) - (a.time || 0));
      console.log('[Globe] events loaded: ' + LIVE.events.length + ' (' + ok.length + '/' + res.length + ' fuentes)');
      res.forEach(r => { if (r.status === 'rejected') console.warn('[Globe] source failed: ' + (r.reason && r.reason.message)); });
      const t = new Date();
      $('lpFoot').textContent = 'USGS · NASA EONET · upd. ' + t.toLocaleTimeString('en-GB', { hour:'2-digit', minute:'2-digit' });
      renderStats(); renderFeed(); refreshMarkers();
    });
  }

  function renderStats(){
    const counts = {}; Object.keys(TYPES).forEach(k => counts[k] = 0);
    LIVE.events.forEach(e => counts[e.type]++);
    const mx = Math.max(1, ...Object.values(counts));
    let maxQ = null;
    LIVE.events.forEach(e => { if (e.type === 'quake' && (!maxQ || e.mag > maxQ.mag)) maxQ = e; });
    const CIRC = 106.8;
    const ring = (cls, attrs, color, frac, big, label) =>
      '<div class="ring-tile ' + cls + '" ' + attrs + ' style="--c:' + color + '">' +
      '<svg viewBox="0 0 44 44"><circle class="bg" cx="22" cy="22" r="17"/><circle class="in" cx="22" cy="22" r="12"/>' +
      '<circle class="fg" cx="22" cy="22" r="17" stroke-dasharray="' + (CIRC * frac).toFixed(1) + ' ' + CIRC + '"/></svg>' +
      '<b>' + big + '</b><span>' + label + '</span></div>';
    $('lpStats').innerHTML = Object.keys(TYPES).map(k =>
      ring(LIVE.filters.has(k) ? 'on' : '', 'data-type="' + k + '"', TYPES[k].color, counts[k] / mx, counts[k], TYPES[k].label)
    ).join('') + (maxQ ? ring('static', '', '#ffffff', Math.min(1, maxQ.mag / 9), maxQ.mag.toFixed(1), 'MAX. M') : '');
  }
  function renderFeed(){
    const rows = LIVE.events.filter(visibleEv).slice(0, 80);
    $('lpFeed').innerHTML = rows.length ? rows.map(e =>
      '<div class="lp-row" data-id="' + esc(e.id) + '" style="--c:' + TYPES[e.type].color + '"><i></i><div>' +
      '<div class="t">' + esc(e.title) + '</div>' +
      '<div class="m">' + esc((e.detail ? e.detail + ' · ' : '') + timeAgo(e.time)) + '</div></div></div>').join('')
      : '<div class="lp-row"><div class="m">No events with this filter</div></div>';
  }
  function refreshMarkers(){ updateGlobeMarkers(); syncMapLive(); }

  // ── 2) markers on the 3D globe ───────────────────────────────────────
  function makeDotTexture(){
    const c = document.createElement('canvas'); c.width = c.height = 64;
    const x = c.getContext('2d');
    const g = x.createRadialGradient(32, 32, 2, 32, 32, 30);
    g.addColorStop(0, 'rgba(255,255,255,1)');
    g.addColorStop(0.5, 'rgba(255,255,255,0.9)');
    g.addColorStop(0.62, 'rgba(255,255,255,0.3)');
    g.addColorStop(1, 'rgba(255,255,255,0)');
    x.fillStyle = g; x.fillRect(0, 0, 64, 64);
    return new THREE.CanvasTexture(c);
  }
  function buildPoints(items, radius, size, colorFn, opacity){
    const pos = new Float32Array(items.length * 3), col = new Float32Array(items.length * 3);
    items.forEach((it, i) => {
      const v = latLonToVec3(it.lat, it.lon, radius);
      pos[i*3] = v.x; pos[i*3+1] = v.y; pos[i*3+2] = v.z;
      const c = new THREE.Color(colorFn(it));
      col[i*3] = c.r; col[i*3+1] = c.g; col[i*3+2] = c.b;
    });
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    g.setAttribute('color', new THREE.BufferAttribute(col, 3));
    const m = new THREE.PointsMaterial({ size: size, vertexColors: true, map: dotTex, transparent: true,
                                         alphaTest: 0.05, depthWrite: false, opacity: opacity });
    const p = new THREE.Points(g, m);
    p.userData.items = items;
    return p;
  }
  function dropObj(o){ if (!o) return; group.remove(o); o.geometry.dispose(); o.material.dispose(); }
  function updateGlobeMarkers(){
    dropObj(evPoints); evPoints = null;
    const items = LIVE.events.filter(visibleEv);
    if (items.length){ evPoints = buildPoints(items, 1.03, 0.04, it => TYPES[it.type].color, 1); group.add(evPoints); }
    dropObj(camPoints); camPoints = null;
    if (LIVE.camsOn && CAMS.length){ camPoints = buildPoints(CAMS, 1.032, 0.055, () => CAM_COLOR, 1); group.add(camPoints); }
    updatePlanes();
  }
  function setSelection(lat, lon){
    dropObj(selPoints); selPoints = null;
    if (lat != null){ selPoints = buildPoints([{ lat: lat, lon: lon }], 1.035, 0.085, () => '#ffffff', 0.5); group.add(selPoints); }
    window.__sel = lat == null ? null : [lon, lat];
    syncMapLive();
  }

  // ── 3) go to the place ─────────────────────────────────────────────────────────
  function focusGlobe(lat, lon, zoom){
    autoRotate = false; orbiting = false;
    const theta = (lon + 180) * Math.PI / 180;
    const d = (-theta + Math.PI / 2) - rotY;
    targetRotY = rotY + Math.atan2(Math.sin(d), Math.cos(d)); // short turn, no extra spins
    targetRotX = Math.max(-1.3, Math.min(1.3, lat * Math.PI / 180));
    targetZoom = zoom;
  }
  function focusPlace(lat, lon, mapZoom){
    lastInteract = Date.now();
    if (inMapMode){
      ensureMap().then(m => m.flyTo({ center: [lon, lat], zoom: mapZoom || 7, pitch: 30, bearing: 0, essential: true }));
    } else {
      focusGlobe(lat, lon, 1.75);
    }
  }

  // ── 4) detail card ────────────────────────────────────────────────────
  function hideCard(){ $('infoCard').classList.remove('show'); LIVE.selFlight = null; selFlightObj = null; setSelection(null); if (!FOLLOW.on) clearRoute(); updatePlanes(); }
  function openUrl(u){ if (/^https?:/.test(u || '')) notify('openurl:' + encodeURIComponent(u)); }
  function showEventCard(e){
    const T = TYPES[e.type];
    LIVE.selFlight = null; selFlightObj = null; if (!FOLLOW.on) clearRoute(); updatePlanes();
    $('icBody').innerHTML =
      '<div class="ic-type" style="color:' + T.color + '">● ' + T.one + '</div>' +
      '<div class="ic-title">' + esc(e.title) + '</div>' +
      (e.detail ? '<div class="ic-row">DETAIL: <b>' + esc(e.detail) + '</b></div>' : '') +
      '<div class="ic-row">DATE: <b>' + esc(e.time ? new Date(e.time).toLocaleString('en-GB') : '—') + '</b></div>' +
      '<div class="ic-row">POS: <b>' + e.lat.toFixed(2) + '°, ' + e.lon.toFixed(2) + '°</b></div>' +
      '<div class="ic-actions"><button class="lp-btn" data-go="' + e.lat + ',' + e.lon + '">GO TO PLACE</button>' +
      (e.url ? '<button class="lp-btn" data-url="' + esc(e.url) + '">SOURCE ↗</button>' : '') + '</div>';
    $('infoCard').classList.add('show');
    setSelection(e.lat, e.lon);
  }

  function showCamCard(c){
    LIVE.selFlight = null; selFlightObj = null; if (!FOLLOW.on) clearRoute(); updatePlanes();
    $('icBody').innerHTML =
      '<div class="ic-type" style="color:' + CAM_COLOR + '"><i style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#ff3d3d;box-shadow:0 0 6px #ff3d3d;margin-right:5px;vertical-align:middle"></i>LIVE CAMERA</div>' +
      '<div class="ic-title">' + esc(c.name) + '</div>' +
      '<div class="ic-row">PLACE: <b>' + esc(c.place) + '</b></div>' +
      '<div class="ic-row">COUNTRY: <b>' + esc(c.country) + '</b></div>' +
      '<div class="ic-row">BROADCASTER: <b>' + esc(c.source) + '</b></div>' +
      (c.desc ? '<div class="ic-row" style="margin-top:3px;opacity:0.75">' + esc(c.desc) + '</div>' : '') +
      '<div class="ic-row">POS: <b>' + c.lat.toFixed(2) + '°, ' + c.lon.toFixed(2) + '°</b></div>' +
      '<div class="ic-actions" style="flex-wrap:wrap;row-gap:5px">' +
      '<button class="lp-btn" data-cam="' + esc(c.id) + '">▶ WATCH LIVE</button>' +
      '<button class="lp-btn" data-go="' + c.lat + ',' + c.lon + '">GO TO PLACE</button>' +
      '<button class="lp-btn" data-url="' + esc(c.page) + '">ORIGINAL SITE ↗</button></div>';
    $('infoCard').classList.add('show');
    setSelection(c.lat, c.lon);
  }
  // Click on a camera (3D globe or map): the card is shown as usual and, in addition, the live
  // window is opened directly (if it was already open, Python just brings it to the front).
  function openCamWindow(c){ notify('opencam:' + encodeURIComponent(c.id)); }
  function selectCamById(id){ const c = CAMS.find(x => x.id === id); if (c){ showCamCard(c); openCamWindow(c); return c; } }

  // ══ 3D FLIGHTS: models, route, information and tracking cameras ═══════
  const D2R = Math.PI / 180, R_KM = 6371, R_M = 6371000;
  const FINFO = new Map();
  let selFlightObj = null, planeMesh = null, planeGeo = null, planeMat = null, heroMesh = null;
  const ROUTE = { objs: [], coords: null, doneCoords: null, from: null, to: null, arc: null, doneLine: null };
  const FOLLOW = { on:false, f:null, mode:'third', lat:0, lon:0, hdg:0, alt:0, spd:0, mul:1,
                   yaw:0, pitch:0.35, dist:0.075, t:0, last:0, hudT:0, doneT:0, firstFrame:true, arrived:false,
                   off:new THREE.Vector3(0.0, 0.018, 0.066), look:new THREE.Vector3() };
  const _m = new THREE.Matrix4(), _P = new THREE.Vector3(), _U = new THREE.Vector3(),
        _F = new THREE.Vector3(), _Rv = new THREE.Vector3(), _B = new THREE.Vector3(),
        _eye = new THREE.Vector3(), _tgt = new THREE.Vector3(), _col = new THREE.Color();

  function planeRadius(alt){ return 1.016 + Math.min(Math.max(alt || 0, 0), 13000) / 13000 * 0.004; }
  function planeColor(alt){ return alt > 8000 ? '#ffffff' : (alt > 3000 ? '#ffe36b' : '#7dffb0'); }

  // local frame at a point on the globe: _P position, _U up, _F heading, _Rv right
  function frameAt(lat, lon, hdg, r){
    const phi = (90 - lat) * D2R, th = (lon + 180) * D2R, h = hdg * D2R;
    _P.set(-r * Math.sin(phi) * Math.cos(th), r * Math.cos(phi), r * Math.sin(phi) * Math.sin(th));
    _U.copy(_P).normalize();
    const nx = Math.cos(phi) * Math.cos(th), ny = Math.sin(phi), nz = -Math.cos(phi) * Math.sin(th);
    const ex = Math.sin(th), ez = Math.cos(th);
    _F.set(nx * Math.cos(h) + ex * Math.sin(h), ny * Math.cos(h), nz * Math.cos(h) + ez * Math.sin(h)).normalize();
    _Rv.crossVectors(_F, _U).normalize();
  }
  function planeMatrix(out, scale){
    _B.copy(_F).negate();                 // the nose points to -Z of the model
    out.makeBasis(_Rv, _U, _B);
    if (scale !== 1) out.scale(new THREE.Vector3(scale, scale, scale));
    out.setPosition(_P);
    return out;
  }

  // very simple rounded aircraft: fuselage with oval nose/tail, elliptical wings and stabilisers
  const HERO_SCALE = 0.12, INST_SCALE = 0.067, JARVIS_BLUE = '#39e8ff';
  function getPlaneGeo(){
    if (planeGeo) return planeGeo;
    const parts = [];
    const M4 = () => new THREE.Matrix4();
    const T = (x, y, z) => M4().makeTranslation(x, y, z);
    const S = (x, y, z) => M4().makeScale(x, y, z);
    const RX = a => M4().makeRotationX(a);
    const add = (g, m) => { g.applyMatrix4(m); parts.push(g.index ? g.toNonIndexed() : g); };
    // fuselaje: cilindro + dos medias esferas alargadas (morro y cola)
    add(new THREE.CylinderGeometry(0.0013, 0.0013, 0.011, 14), RX(Math.PI / 2));
    add(new THREE.SphereGeometry(1, 14, 10), T(0, 0, -0.0055).multiply(S(0.0013, 0.0013, 0.0030)));
    add(new THREE.SphereGeometry(1, 14, 10), T(0, 0.0002, 0.0055).multiply(S(0.0013, 0.0012, 0.0040)));
    // elliptical wings with slight sweep
    [-1, 1].forEach(sx => {
      const g = new THREE.SphereGeometry(1, 16, 8);
      g.applyMatrix4(S(0.0050, 0.00028, 0.0020));
      g.applyMatrix4(new THREE.Matrix4().set(1,0,0,0, 0,1,0,0, 0.55*sx,0,1,0, 0,0,0,1));
      add(g, T(sx * 0.0046, -0.0004, 0.0010));
    });
    // fin and stabilisers
    add(new THREE.SphereGeometry(1, 12, 8), T(0, 0.0024, 0.0068).multiply(S(0.00026, 0.0023, 0.0020)));
    add(new THREE.SphereGeometry(1, 14, 8), T(0, 0.0004, 0.0082).multiply(S(0.0034, 0.00022, 0.0011)));
    // motores
    [-1, 1].forEach(sx => add(new THREE.CylinderGeometry(0.0007, 0.0007, 0.0028, 10), T(sx * 0.0035, -0.0013, -0.0004).multiply(RX(Math.PI / 2))));
    let n = 0; parts.forEach(g => n += g.attributes.position.count);
    const pos = new Float32Array(n * 3), nor = new Float32Array(n * 3); let o = 0;
    parts.forEach(g => { pos.set(g.attributes.position.array, o); nor.set(g.attributes.normal.array, o); o += g.attributes.position.array.length; g.dispose(); });
    planeGeo = new THREE.BufferGeometry();
    planeGeo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    planeGeo.setAttribute('normal', new THREE.BufferAttribute(nor, 3));
    return planeGeo;
  }
  function getPlaneMat(){
    if (!planeMat) planeMat = new THREE.MeshPhongMaterial({ flatShading: false, shininess: 40, emissive: 0x1a3a46 });
    return planeMat;
  }

  function updatePlanes(){
    if (planeMesh){ group.remove(planeMesh); planeMesh.dispose(); planeMesh = null; }
    dropObj(flPoints); flPoints = null;
    if (!(LIVE.flightsOn && LIVE.flights.length)) return;
    const stride = Math.max(1, Math.ceil(LIVE.flights.length / 2500));
    const items = LIVE.flights.filter((f, i) => i % stride === 0);
    planeMesh = new THREE.InstancedMesh(getPlaneGeo(), getPlaneMat(), items.length);
    items.forEach((f, i) => {
      if (FOLLOW.on && FOLLOW.f && f.icao === FOLLOW.f.icao){
        _m.makeScale(0, 0, 0);                         // el seguido se dibuja aparte (heroMesh)
      } else {
        frameAt(f.lat, f.lon, f.hdg || 0, planeRadius(f.alt));
        planeMatrix(_m, INST_SCALE);
      }
      planeMesh.setMatrixAt(i, _m);
      planeMesh.setColorAt(i, _col.set(f.icao === LIVE.selFlight ? JARVIS_BLUE : planeColor(f.alt)));
    });
    planeMesh.instanceMatrix.needsUpdate = true;
    if (planeMesh.instanceColor) planeMesh.instanceColor.needsUpdate = true;
    planeMesh.frustumCulled = false;
    group.add(planeMesh);
    // invisible proxy for clicking only (the models are tiny)
    flPoints = buildPoints(items, 1.019, 0.03, () => '#ffffff', 0);
    flPoints.visible = false;
    group.add(flPoints);
  }

  // ── route geometry ─────────────────────────────────────────────────────
  function gcDist(a, b){
    const p1 = a.lat * D2R, p2 = b.lat * D2R, dl = (b.lon - a.lon) * D2R;
    const x = Math.pow(Math.sin((p2 - p1) / 2), 2) + Math.cos(p1) * Math.cos(p2) * Math.pow(Math.sin(dl / 2), 2);
    return 2 * Math.asin(Math.min(1, Math.sqrt(x)));
  }
  function advance(lat, lon, hdg, d){
    const p1 = lat * D2R, l1 = lon * D2R, th = hdg * D2R;
    const p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(th));
    const l2 = l1 + Math.atan2(Math.sin(th) * Math.sin(d) * Math.cos(p1), Math.cos(d) - Math.sin(p1) * Math.sin(p2));
    const y = Math.sin(l1 - l2) * Math.cos(p1);
    const x = Math.cos(p2) * Math.sin(p1) - Math.sin(p2) * Math.cos(p1) * Math.cos(l1 - l2);
    const h2 = (Math.atan2(y, x) / D2R + 180 + 360) % 360;
    let lon2 = (l2 / D2R) % 360; if (lon2 > 180) lon2 -= 360; if (lon2 < -180) lon2 += 360;
    return { lat: p2 / D2R, lon: lon2, hdg: h2 };
  }
  function vecToLL(v){
    const r = v.length();
    let lon = Math.atan2(v.z, -v.x) / D2R - 180;
    while (lon < -180) lon += 360;
    return { lat: Math.asin(v.y / r) / D2R, lon: lon };
  }
  function unwrapLL(list){
    let prev = null;
    return list.map(q => { let lo = q.lon;
      if (prev !== null){ while (lo - prev > 180) lo -= 360; while (lo - prev < -180) lo += 360; }
      prev = lo; return [lo, q.lat]; });
  }
  function arcPts(a, b, n, r0, bump){
    const va = latLonToVec3(a.lat, a.lon, 1).normalize(), vb = latLonToVec3(b.lat, b.lon, 1).normalize();
    const ang = Math.acos(Math.max(-1, Math.min(1, va.dot(vb))));
    const steps = ang < 1e-5 ? 1 : n, pts = [], lls = [];
    for (let i = 0; i <= steps; i++){
      const t = i / steps;
      const v = ang < 1e-5 ? va.clone()
        : va.clone().multiplyScalar(Math.sin((1 - t) * ang) / Math.sin(ang)).add(vb.clone().multiplyScalar(Math.sin(t * ang) / Math.sin(ang)));
      v.normalize(); lls.push(vecToLL(v));
      pts.push(v.multiplyScalar(r0 + bump * Math.sin(Math.PI * t)));
    }
    return { pts: pts, ll: unwrapLL(lls) };
  }
  function curPos(f){
    if (FOLLOW.on && FOLLOW.f && f && f.icao === FOLLOW.f.icao) return { lat: FOLLOW.lat, lon: FOLLOW.lon, hdg: FOLLOW.hdg };
    return { lat: f.lat, lon: f.lon, hdg: f.hdg || 0 };
  }
  function clearRouteObjs(){
    ROUTE.objs.forEach(o => { group.remove(o); if (o.geometry) o.geometry.dispose(); if (o.material) o.material.dispose(); });
    ROUTE.objs = []; ROUTE.doneLine = null;
  }
  function clearRoute(){
    clearRouteObjs(); ROUTE.coords = null; ROUTE.doneCoords = null; ROUTE.from = ROUTE.to = ROUTE.arc = null;
    syncMapLive();
  }
  function makeLine(pts, mat, dashed){
    const l = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), mat);
    if (dashed) l.computeLineDistances();
    l.frustumCulled = false; return l;
  }
  function drawRoute(f){
    clearRouteObjs();
    const info = FINFO.get(f.icao), rt = info && info.route, cur = curPos(f);
    let arc;
    if (rt && rt.from && rt.to){
      ROUTE.from = rt.from; ROUTE.to = rt.to;
      arc = arcPts(rt.from, rt.to, 96, 1.017, 0.005);
      const mk = buildPoints([rt.from, rt.to], 1.02, 0.035, () => '#ffffff', 1);
      group.add(mk); ROUTE.objs.push(mk);
    } else {   // no known route: trajectory projected forward (2500 km)
      ROUTE.from = ROUTE.to = null;
      const pts = [], lls = [];
      for (let k = 0; k <= 40; k++){
        const q = advance(cur.lat, cur.lon, cur.hdg, (k / 40) * 2500 / R_KM);
        lls.push(q); pts.push(latLonToVec3(q.lat, q.lon, 1.017));
      }
      arc = { pts: pts, ll: unwrapLL(lls) };
    }
    ROUTE.arc = arc; ROUTE.coords = arc.ll;
    const full = makeLine(arc.pts, new THREE.LineDashedMaterial({ color: 0x39e8ff, dashSize: 0.014, gapSize: 0.010, transparent: true, opacity: 0.75 }), true);
    group.add(full); ROUTE.objs.push(full);
    refreshRouteDone();
    syncMapLive();
  }
  function refreshRouteDone(){
    if (ROUTE.doneLine){
      group.remove(ROUTE.doneLine); ROUTE.doneLine.geometry.dispose(); ROUTE.doneLine.material.dispose();
      ROUTE.objs = ROUTE.objs.filter(o => o !== ROUTE.doneLine); ROUTE.doneLine = null;
    }
    ROUTE.doneCoords = null;
    const f = selFlightObj || FOLLOW.f;
    if (!ROUTE.from || !ROUTE.to || !ROUTE.arc || !f) return;
    const cur = curPos(f);
    const tot = Math.max(1e-6, gcDist(ROUTE.from, ROUTE.to));
    const frac = Math.max(0, Math.min(1, gcDist(ROUTE.from, cur) / tot));
    const k = Math.round(frac * (ROUTE.arc.pts.length - 1));
    if (k < 1) return;
    ROUTE.doneLine = makeLine(ROUTE.arc.pts.slice(0, k + 1), new THREE.LineBasicMaterial({ color: 0x7dffb0 }), false);
    group.add(ROUTE.doneLine); ROUTE.objs.push(ROUTE.doneLine);
    ROUTE.doneCoords = ROUTE.arc.ll.slice(0, k + 1);
  }

  // ── flight card ────────────────────────────────────────────────────────
  function fmtAp(a){ if (!a) return '—'; const c = a.iata || a.icao || '?', n = a.city || a.name || ''; return c + (n ? ' · ' + n : ''); }
  function fmtDur(h){ if (!isFinite(h) || h < 0) return '—'; const m = Math.round(h * 60); return Math.floor(m / 60) + ' h ' + String(m % 60).padStart(2, '0') + ' min'; }
  function renderFlightCard(f){
    const info = FINFO.get(f.icao), ac = (info && info.aircraft) || {}, rt = info && info.route;
    const cur = curPos(f), cs = f.cs || f.icao;
    const model = ac.model || ac.icao_type || f.type, reg = ac.reg || f.reg;
    let routeTxt = info ? 'unavailable' : 'querying…', prog = '';
    if (rt && rt.from && rt.to){
      routeTxt = esc(fmtAp(rt.from)) + '<br>→ ' + esc(fmtAp(rt.to));
      const done = gcDist(rt.from, cur) * R_KM, rem = gcDist(cur, rt.to) * R_KM;
      const pct = Math.max(0, Math.min(100, done / Math.max(1, done + rem) * 100));
      prog = '<div class="ic-bar"><i style="width:' + pct.toFixed(0) + '%"></i></div>' +
        '<div class="ic-row">TRAVELLED: <b>' + Math.round(done) + ' km</b> · REMAINING: <b>' + Math.round(rem) + ' km</b></div>' +
        '<div class="ic-row">ARRIVAL IN: <b>' + fmtDur(rem / Math.max(1, f.spd * 3.6)) + '</b></div>';
    }
    $('icBody').innerHTML =
      '<div class="ic-type" style="color:#7dffb0">✈ FLIGHT</div>' +
      '<div class="ic-title">' + esc(cs) + '</div>' +
      (ac.owner ? '<div class="ic-sub">' + esc(ac.owner) + '</div>' : '') +
      '<div class="ic-row">ROUTE: <b>' + routeTxt + '</b></div>' + prog +
      (model ? '<div class="ic-row">AIRCRAFT: <b>' + esc([ac.manufacturer, model].filter(Boolean).join(' ')) + '</b></div>' : '') +
      (reg ? '<div class="ic-row">REGISTRATION: <b>' + esc(reg) + '</b></div>' : '') +
      '<div class="ic-row">COUNTRY: <b>' + esc(f.country) + '</b></div>' +
      '<div class="ic-row">ALTITUDE: <b>' + Math.round(f.alt) + ' m (' + Math.round(f.alt * 3.28084) + ' ft)</b></div>' +
      '<div class="ic-row">SPEED: <b>' + Math.round(f.spd * 3.6) + ' km/h (' + Math.round(f.spd * 1.94384) + ' kt)</b></div>' +
      '<div class="ic-row">HEADING: <b>' + Math.round(f.hdg || 0) + '°</b> · V.SPEED: <b>' + (f.vr || 0).toFixed(1) + ' m/s</b></div>' +
      '<div class="ic-row">POS: <b>' + cur.lat.toFixed(2) + '°, ' + cur.lon.toFixed(2) + '°</b></div>' +
      '<div class="ic-row">ICAO24: <b>' + esc(f.icao) + '</b></div>' +
      '<div class="ic-actions" style="flex-wrap:wrap;gap:6px 12px">' +
        '<button class="lp-btn" data-follow="cine">▶ CINEMATIC</button>' +
        '<button class="lp-btn" data-follow="cockpit">▶ COCKPIT</button>' +
        '<button class="lp-btn" data-follow="third">▶ THIRD PERSON</button>' +
        '<button class="lp-btn" data-go="' + f.lat + ',' + f.lon + '">CENTRE</button>' +
        (f.cs ? '<button class="lp-btn" data-url="https://www.flightradar24.com/' + esc(f.cs) + '">FR24 ↗</button>' : '') +
      '</div>';
  }
  function showFlightCard(f){
    LIVE.selFlight = f.icao; selFlightObj = f;
    updatePlanes();
    renderFlightCard(f);
    $('infoCard').classList.add('show');
    if (!FOLLOW.on) setSelection(f.lat, f.lon);
    drawRoute(f);   // projected trajectory until the real route arrives
    if (FINFO.has(f.icao)) return;
    notify('flightinfo:' + encodeURIComponent(JSON.stringify({ icao: f.icao, cs: f.cs || '', lat: f.lat, lon: f.lon })));
  }
  window.__flightInfo = function(res){
    if (!res || !res.icao) return;
    FINFO.set(res.icao, res);
    if (selFlightObj && selFlightObj.icao === res.icao){ renderFlightCard(selFlightObj); drawRoute(selFlightObj); }
  };
  function onFlightsUpdated(){
    if (selFlightObj){
      const nf = LIVE.flightIdx.get(selFlightObj.icao);
      if (nf){ selFlightObj = nf; if (!FOLLOW.on) renderFlightCard(nf); }
    }
  }

  // ── tracking: cinematic / cockpit / third person ────────────────────
  const SHOTS = [
    t => [0.0, 0.03, 0.11],
    t => [Math.sin(t * 0.5) * 0.09, 0.02, Math.cos(t * 0.5) * 0.09],
    t => [0.06, 0.004, -0.015],
    t => [-0.015, 0.012, -0.10],
    t => [0.0, 0.05, 0.085],
    t => [-0.055, 0.006, 0.03],
  ];
  function setFollowMode(mode){
    FOLLOW.mode = mode;
    FOLLOW.yaw = 0; FOLLOW.pitch = mode === 'third' ? 0.35 : 0; FOLLOW.dist = 0.075 * HERO_SCALE;
    camera.fov = mode === 'cockpit' ? 75 : (mode === 'third' ? 50 : 42);
    camera.updateProjectionMatrix();
    document.body.classList.toggle('cockpit', mode === 'cockpit');
    document.querySelectorAll('#followHud [data-fmode]').forEach(b => b.classList.toggle('on', b.getAttribute('data-fmode') === mode));
    document.querySelectorAll('#followHud [data-mul]').forEach(b => b.classList.toggle('on', +b.getAttribute('data-mul') === FOLLOW.mul));
    if (heroMesh) heroMesh.visible = mode !== 'cockpit';
    FOLLOW.firstFrame = true;
  }
  function startFollow(f, mode){
    if (!f || !LIVE.flightsOn) return;
    if (inMapMode) exitMapMode();
    FOLLOW.mul = 1;
    FOLLOW.on = true; FOLLOW.f = f; FOLLOW.lat = f.lat; FOLLOW.lon = f.lon; FOLLOW.hdg = f.hdg || 0;
    FOLLOW.alt = f.alt || 0; FOLLOW.spd = Math.max(120, f.spd || 200); FOLLOW.t = 0; FOLLOW.arrived = false;
    FOLLOW.last = performance.now(); FOLLOW.hudT = 0; FOLLOW.doneT = 0;
    autoRotate = false; orbiting = false;
    setSelection(null);
    if (heroMesh){ group.remove(heroMesh); heroMesh.material.dispose(); }
    heroMesh = new THREE.Mesh(getPlaneGeo(), new THREE.MeshPhongMaterial({ color: JARVIS_BLUE, flatShading: false, shininess: 40, emissive: 0x1a3a46 }));
    heroMesh.matrixAutoUpdate = false; heroMesh.frustumCulled = false;
    group.add(heroMesh);
    camera.near = 0.00008; camera.far = 8;
    if (glowMesh) glowMesh.visible = false;
    document.body.classList.add('following');
    $('fhName').textContent = (f.cs || f.icao) + ' · SIMULATION';
    setFollowMode(mode || 'third');
    updatePlanes();
    if (!ROUTE.arc) drawRoute(f);
    if (!FINFO.has(f.icao) && f.cs) notify('flightinfo:' + encodeURIComponent(JSON.stringify({ icao: f.icao, cs: f.cs, lat: f.lat, lon: f.lon })));
    lastInteract = Date.now();
  }
  function stopFollow(refocus){
    if (!FOLLOW.on) return;
    FOLLOW.on = false;
    if (heroMesh){ group.remove(heroMesh); heroMesh.material.dispose(); heroMesh = null; }
    camera.near = 0.1; camera.far = 100; camera.fov = 42;
    camera.up.set(0, 1, 0); camera.position.set(0, 0, zoomDist); camera.lookAt(0, 0, 0);
    camera.updateProjectionMatrix();
    if (glowMesh) glowMesh.visible = true;
    document.body.classList.remove('following', 'cockpit');
    const lat = FOLLOW.lat, lon = FOLLOW.lon;
    updatePlanes();
    if (refocus !== false) focusGlobe(lat, lon, 2.2);
  }
  function updateFollowHud(){
    const f = FOLLOW.f; if (!f) return;
    const info = FINFO.get(f.icao), rt = info && info.route;
    let t = 'ALT ' + Math.round(FOLLOW.alt * 3.28084) + ' ft · ' + Math.round(FOLLOW.spd * 3.6) + ' km/h · HEADING ' + Math.round(FOLLOW.hdg) + '° · SIM ×' + FOLLOW.mul;
    if (rt && rt.to){
      const rem = gcDist({ lat: FOLLOW.lat, lon: FOLLOW.lon }, rt.to) * R_KM;
      t += FOLLOW.arrived ? ' · ARRIVED ' + (rt.to.iata || rt.to.icao) : ' · TO ' + (rt.to.iata || rt.to.icao) + ' ' + Math.round(rem) + ' km';
    }
    $('fhTele').textContent = t;
  }
  function updateFollow(){
    const now = performance.now();
    const dt = Math.min(0.1, Math.max(0.001, (now - FOLLOW.last) / 1000)); FOLLOW.last = now;
    lastInteract = Date.now();
    if (!FOLLOW.arrived){
      const q = advance(FOLLOW.lat, FOLLOW.lon, FOLLOW.hdg, (FOLLOW.spd * FOLLOW.mul * dt) / R_M);
      FOLLOW.lat = q.lat; FOLLOW.lon = q.lon; FOLLOW.hdg = q.hdg;
      const rt = (FINFO.get(FOLLOW.f.icao) || {}).route;
      if (rt && rt.to && gcDist({ lat: FOLLOW.lat, lon: FOLLOW.lon }, rt.to) < 0.0012) FOLLOW.arrived = true;
    }
    FOLLOW.t += dt;
    frameAt(FOLLOW.lat, FOLLOW.lon, FOLLOW.hdg, planeRadius(FOLLOW.alt));
    if (heroMesh){ planeMatrix(heroMesh.matrix, HERO_SCALE); heroMesh.matrixWorldNeedsUpdate = true; heroMesh.visible = FOLLOW.mode !== 'cockpit'; }
    const mode = FOLLOW.mode;
    if (mode === 'cockpit'){
      _eye.copy(_P).addScaledVector(_U, 0.0012 * HERO_SCALE).addScaledVector(_F, 0.004 * HERO_SCALE);
      const cy = Math.cos(FOLLOW.yaw), sy = Math.sin(FOLLOW.yaw), cp = Math.cos(FOLLOW.pitch), sp = Math.sin(FOLLOW.pitch);
      _tgt.copy(_eye).addScaledVector(_F, cy * cp).addScaledVector(_Rv, sy * cp).addScaledVector(_U, sp - 0.03);
      camera.position.copy(_eye); camera.up.copy(_U); camera.lookAt(_tgt);
    } else if (mode === 'third'){
      const cy = Math.cos(FOLLOW.yaw), sy = Math.sin(FOLLOW.yaw), cp = Math.cos(FOLLOW.pitch), sp = Math.sin(FOLLOW.pitch);
      _eye.copy(_P).addScaledVector(_Rv, sy * cp * FOLLOW.dist).addScaledVector(_F, -cy * cp * FOLLOW.dist).addScaledVector(_U, sp * FOLLOW.dist);
      _tgt.copy(_P).addScaledVector(_F, 0.004 * HERO_SCALE);
      camera.position.copy(_eye); camera.up.copy(_U); camera.lookAt(_tgt);
    } else {   // cinematic: shots that chain automatically
      const o = SHOTS[Math.floor(FOLLOW.t / 7) % SHOTS.length](FOLLOW.t);
      const k = FOLLOW.firstFrame ? 1 : 1 - Math.exp(-dt * 1.3);
      FOLLOW.off.x += (o[0] * HERO_SCALE - FOLLOW.off.x) * k; FOLLOW.off.y += (o[1] * HERO_SCALE - FOLLOW.off.y) * k; FOLLOW.off.z += (o[2] * HERO_SCALE - FOLLOW.off.z) * k;
      _eye.copy(_P).addScaledVector(_Rv, FOLLOW.off.x).addScaledVector(_U, FOLLOW.off.y).addScaledVector(_F, -FOLLOW.off.z);
      _tgt.copy(_P).addScaledVector(_F, 0.006 * HERO_SCALE);
      if (FOLLOW.firstFrame) FOLLOW.look.copy(_tgt); else FOLLOW.look.lerp(_tgt, 1 - Math.exp(-dt * 6));
      camera.position.copy(_eye); camera.up.copy(_U); camera.lookAt(FOLLOW.look);
    }
    FOLLOW.firstFrame = false;
    if (now - FOLLOW.hudT > 250){ FOLLOW.hudT = now; updateFollowHud(); }
    if (now - FOLLOW.doneT > 700){ FOLLOW.doneT = now; refreshRouteDone(); }
  }

  function selectEventById(id){ const e = LIVE.events.find(x => x.id === id); if (e){ showEventCard(e); return e; } }
  function selectFlightById(id){ const f = LIVE.flightIdx.get(id); if (f){ showFlightCard(f); return f; } }

  // click on a globe marker (ray-casting; markers on the hidden side are ignored)
  const raycaster = new THREE.Raycaster();
  raycaster.params.Points.threshold = 0.03;
  function pickGlobe(e){
    const rect = renderer.domElement.getBoundingClientRect();
    const mouse = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    raycaster.setFromCamera(mouse, camera);
    group.updateMatrixWorld(true);
    const targets = [];
    if (evPoints) targets.push(evPoints);
    if (flPoints) targets.push(flPoints);
    if (camPoints) targets.push(camPoints);
    const hits = raycaster.intersectObjects(targets, false).filter(h => {
      const it = h.object.userData.items[h.index];
      const wp = latLonToVec3(it.lat, it.lon, 1).applyMatrix4(group.matrixWorld);
      return camera.position.clone().sub(wp).dot(wp) > 0;
    });
    if (!hits.length) return;
    const h = hits[0], it = h.object.userData.items[h.index];
    if (h.object === evPoints) showEventCard(it);
    else if (h.object === camPoints){ showCamCard(it); openCamWindow(it); }
    else showFlightCard(it);
  }

  // ── 5) precipitation: RainViewer ──────────────────────────────────────────
  function rainTileUrl(f){ return f.host + f.path + '/256/{z}/{x}/{y}/2/1_1.png'; }
  function buildRainCanvas(frame){
    // The tiles arrive in Mercator projection; the globe needs equirectangular,
    // so the mosaic is composed (z=3, 8×8) and resampled row by row.
    const Z = 3, N = 8, T = 256, S = N * T;
    const merc = document.createElement('canvas'); merc.width = merc.height = S;
    const mx = merc.getContext('2d');
    const jobs = [];
    for (let x = 0; x < N; x++) for (let y = 0; y < N; y++){
      jobs.push(new Promise(res => {
        const im = new Image(); im.crossOrigin = 'anonymous';
        im.onload = () => { mx.drawImage(im, x * T, y * T); res(); };
        im.onerror = () => res();
        im.src = frame.host + frame.path + '/256/' + Z + '/' + x + '/' + y + '/2/1_1.png';
      }));
    }
    return Promise.all(jobs).then(() => {
      const W = S, H = S / 2;
      const out = document.createElement('canvas'); out.width = W; out.height = H;
      const ox = out.getContext('2d');
      for (let j = 0; j < H; j++){
        const lat = 90 - (j + 0.5) / H * 180;
        if (Math.abs(lat) >= 85.05) continue;
        const r = lat * Math.PI / 180;
        const my = (1 - Math.log(Math.tan(Math.PI / 4 + r / 2)) / Math.PI) / 2 * S;
        ox.drawImage(merc, 0, Math.min(S - 1, Math.max(0, Math.floor(my))), W, 1, 0, j, W, 1);
      }
      return out;
    });
  }
  function loadRain(){
    return fetch('https://api.rainviewer.com/public/weather-maps.json')
      .then(r => { if (!r.ok) throw new Error('rainviewer ' + r.status); return r.json(); })
      .then(j => {
        const past = (j.radar && j.radar.past) || [];
        if (!past.length) throw new Error('no frames');
        const last = past[past.length - 1];
        LIVE.rainFrame = { host: j.host, path: last.path, time: last.time };
        syncMapLive();
        return buildRainCanvas(LIVE.rainFrame);
      })
      .then(canvas => {
        const tex = new THREE.CanvasTexture(canvas);
        if (!rainMesh){
          rainMesh = new THREE.Mesh(new THREE.SphereGeometry(1.02, 96, 48),
            new THREE.MeshBasicMaterial({ map: tex, transparent: true, opacity: 0.85, depthWrite: false }));
          group.add(rainMesh);
        } else {
          if (rainMesh.material.map) rainMesh.material.map.dispose();
          rainMesh.material.map = tex; rainMesh.material.needsUpdate = true;
        }
        rainMesh.visible = LIVE.rainOn;
        const t = new Date(LIVE.rainFrame.time * 1000).toLocaleTimeString('en-GB', { hour:'2-digit', minute:'2-digit' });
        setLayerStatus('Rain: radar of ' + t);
      })
      .catch(err => { console.warn('[Globe] rain failed', err); setLayerStatus('Rain: unavailable'); });
  }
  function setLayerStatus(t){ $('layerStatus').textContent = t || ''; }
  function refreshLayerStatus(){
    const parts = [];
    if (LIVE.flightsOn) parts.push('Flights: ' + (LIVE.flights.length || '…'));
    if (!LIVE.rainOn && !LIVE.flightsOn) parts.length = 0;
    if (parts.length) setLayerStatus(parts.join(' · '));
  }

  // ── 6) flights: OpenSky ─────────────────────────────────────────────────────
  function scheduleFlights(delay){
    clearTimeout(flightTimer);
    if (!LIVE.flightsOn) return;
    flightTimer = setTimeout(() => {
      fetchFlights().then(() => scheduleFlights(inMapMode && map && map.getZoom() >= 5 ? 60000 : 150000));
    }, delay);
  }
  // Flights are requested from Python (without the browser's CORS restrictions, which is what
  // made 'Failed to fetch' happen). JS signals via notify('flights:…') and Python responds
  // llamando a window.__flightsResult({...}).
  let _flightPending = null, _flightSeq = 0;
  window.__flightsResult = function(res){
    const pend = _flightPending; _flightPending = null;
    if (!pend) return;
    clearTimeout(pend.timer);
    if (!LIVE.flightsOn){ pend.resolve(); return; }
    if (res && res.ok){
      const list = res.flights || [];
      LIVE.flights = list;
      LIVE.flightIdx = new Map(list.map(f => [f.icao, f]));
      onFlightsUpdated();
      refreshLayerStatus();
      refreshMarkers();
    } else {
      if (res && res.backoff) flightBackoff = Date.now() + res.backoff * 1000;
      setLayerStatus('Flights: ' + ((res && res.error) || 'no data') + ' — retrying');
    }
    pend.resolve();
  };
  function fetchFlights(){
    if (!LIVE.flightsOn) return Promise.resolve();
    if (Date.now() < flightBackoff) return Promise.resolve();
    if (Date.now() - lastFlightFetch < 10000) return Promise.resolve();
    if (_flightPending) return Promise.resolve();
    lastFlightFetch = Date.now();
    let bbox = null;
    if (inMapMode && map && map.getZoom() >= 5){
      const b = map.getBounds();
      if (b.getWest() > -180 && b.getEast() < 180)
        bbox = { s: +b.getSouth().toFixed(2), w: +b.getWest().toFixed(2), n: +b.getNorth().toFixed(2), e: +b.getEast().toFixed(2) };
    }
    return new Promise(resolve => {
      const timer = setTimeout(() => {
        _flightPending = null;
        setLayerStatus('Flights: timed out — retrying');
        resolve();
      }, 30000);
      _flightPending = { resolve: resolve, timer: timer };
      notify('flights:' + encodeURIComponent(JSON.stringify({ id: ++_flightSeq, bbox: bbox })));
    });
  }

  // ── 7) layers over the street map (MapLibre) ───────────────────────────
  function fc(features){ return { type: 'FeatureCollection', features: features }; }
  function ensurePlaneImages(m){
    [['plane-lo', '#7dffb0'], ['plane-mid', '#ffe36b'], ['plane-hi', '#ffffff']].forEach(pair => {
      if (m.hasImage(pair[0])) return;
      const c = document.createElement('canvas'); c.width = c.height = 48;
      const x = c.getContext('2d');
      x.fillStyle = pair[1]; x.strokeStyle = '#061a2b'; x.lineWidth = 2;
      x.beginPath();
      [[24,3],[27,16],[44,28],[44,32],[27,27],[26,38],[33,43],[33,46],[24,44],[15,46],[15,43],[22,38],[21,27],[4,32],[4,28],[21,16]]
        .forEach((p, i) => i ? x.lineTo(p[0], p[1]) : x.moveTo(p[0], p[1]));
      x.closePath(); x.fill(); x.stroke();
      m.addImage(pair[0], x.getImageData(0, 0, 48, 48), { pixelRatio: 2 });
    });
  }
  function syncMapLive(){
    if (!map || !mapReady) return;
    const m = map;
    try {
      const evData = fc(LIVE.events.filter(visibleEv).map(e => ({ type:'Feature',
        geometry:{ type:'Point', coordinates:[e.lon, e.lat] }, properties:{ id:e.id, type:e.type } })));
      const colorExpr = ['match', ['get', 'type'], 'quake', TYPES.quake.color, 'fire', TYPES.fire.color,
                         'storm', TYPES.storm.color, 'volcano', TYPES.volcano.color, TYPES.flood.color];
      if (!m.getSource('live-events')){
        m.addSource('live-events', { type:'geojson', data: evData });
        m.addLayer({ id:'live-events-glow', type:'circle', source:'live-events', paint:{
          'circle-radius':['interpolate',['linear'],['zoom'],2,10,10,18], 'circle-color':colorExpr,
          'circle-opacity':0.28, 'circle-blur':0.6 } });
        m.addLayer({ id:'live-events', type:'circle', source:'live-events', paint:{
          'circle-radius':['interpolate',['linear'],['zoom'],2,4,10,7], 'circle-color':colorExpr,
          'circle-stroke-color':'#ffffff', 'circle-stroke-width':1 } });
      } else m.getSource('live-events').setData(evData);

      ensurePlaneImages(m);
      const rtFeats = [];
      if (ROUTE.coords && ROUTE.coords.length > 1) rtFeats.push({ type:'Feature', properties:{ k:'full' }, geometry:{ type:'LineString', coordinates: ROUTE.coords } });
      if (ROUTE.doneCoords && ROUTE.doneCoords.length > 1) rtFeats.push({ type:'Feature', properties:{ k:'done' }, geometry:{ type:'LineString', coordinates: ROUTE.doneCoords } });
      if (!m.getSource('live-route')){
        m.addSource('live-route', { type:'geojson', data: fc(rtFeats) });
        m.addLayer({ id:'live-route-full', type:'line', source:'live-route', filter:['==',['get','k'],'full'],
          paint:{ 'line-color':'#39e8ff', 'line-width':2.5, 'line-opacity':0.85, 'line-dasharray':[2,2] } });
        m.addLayer({ id:'live-route-done', type:'line', source:'live-route', filter:['==',['get','k'],'done'],
          paint:{ 'line-color':'#7dffb0', 'line-width':3, 'line-opacity':0.95 } });
      } else m.getSource('live-route').setData(fc(rtFeats));

      const flData = fc(LIVE.flightsOn ? LIVE.flights.map(f => ({ type:'Feature',
        geometry:{ type:'Point', coordinates:[f.lon, f.lat] }, properties:{ icao:f.icao, alt:f.alt, hdg:f.hdg || 0 } })) : []);
      if (!m.getSource('live-flights')){
        m.addSource('live-flights', { type:'geojson', data: flData });
        m.addLayer({ id:'live-flights', type:'symbol', source:'live-flights', layout:{
          'icon-image':['step',['to-number',['get','alt'],0],'plane-lo',3000,'plane-mid',8000,'plane-hi'],
          'icon-rotate':['to-number',['get','hdg'],0],
          'icon-rotation-alignment':'map', 'icon-pitch-alignment':'map',
          'icon-allow-overlap':true, 'icon-ignore-placement':true,
          'icon-size':['interpolate',['linear'],['zoom'],2,0.35,8,0.6,14,1.0] } });
      } else m.getSource('live-flights').setData(flData);

      const selData = fc(window.__sel ? [{ type:'Feature', geometry:{ type:'Point', coordinates: window.__sel }, properties:{} }] : []);
      if (!m.getSource('live-sel')){
        m.addSource('live-sel', { type:'geojson', data: selData });
        m.addLayer({ id:'live-sel', type:'circle', source:'live-sel', paint:{
          'circle-radius':14, 'circle-color':'rgba(255,255,255,0)', 'circle-stroke-color':'#ffffff', 'circle-stroke-width':2 } });
      } else m.getSource('live-sel').setData(selData);

      const camData = fc(LIVE.camsOn ? CAMS.map(c => ({ type:'Feature',
        geometry:{ type:'Point', coordinates:[c.lon, c.lat] }, properties:{ id:c.id } })) : []);
      if (!m.getSource('live-cams')){
        m.addSource('live-cams', { type:'geojson', data: camData });
        m.addLayer({ id:'live-cams-glow', type:'circle', source:'live-cams', paint:{
          'circle-radius':['interpolate',['linear'],['zoom'],2,10,10,18], 'circle-color':CAM_COLOR,
          'circle-opacity':0.3, 'circle-blur':0.6 } });
        m.addLayer({ id:'live-cams', type:'circle', source:'live-cams', paint:{
          'circle-radius':['interpolate',['linear'],['zoom'],2,4.5,10,8], 'circle-color':CAM_COLOR,
          'circle-stroke-color':'#ffffff', 'circle-stroke-width':1.2 } });
      } else m.getSource('live-cams').setData(camData);

      if (LIVE.rainFrame){
        const tiles = [rainTileUrl(LIVE.rainFrame)];
        if (!m.getSource('live-rain')){
          const firstSymbol = (m.getStyle().layers || []).find(l => l.type === 'symbol');
          m.addSource('live-rain', { type:'raster', tiles: tiles, tileSize:256, maxzoom:7 });
          m.addLayer({ id:'live-rain', type:'raster', source:'live-rain', paint:{ 'raster-opacity':0.7 } },
                     firstSymbol ? firstSymbol.id : undefined);
        } else if (m.getSource('live-rain').setTiles) m.getSource('live-rain').setTiles(tiles);
      }
      if (m.getLayer('live-rain')) m.setLayoutProperty('live-rain', 'visibility', LIVE.rainOn ? 'visible' : 'none');
      if (m.getLayer('live-flights')) m.setLayoutProperty('live-flights', 'visibility', LIVE.flightsOn ? 'visible' : 'none');
      ['live-route-full', 'live-route-done'].forEach(id => { if (m.getLayer(id)) m.setLayoutProperty(id, 'visibility', LIVE.flightsOn ? 'visible' : 'none'); });
    } catch (e) { console.warn('[Globe] live layers:', e); }
  }
  function bindMapLive(m){
    if (mapLiveBound) return;
    mapLiveBound = true;
    syncMapLive();
    m.on('click', 'live-events', e => { if (e.features && e.features[0]) selectEventById(e.features[0].properties.id); });
    m.on('click', 'live-flights', e => { if (e.features && e.features[0]) selectFlightById(e.features[0].properties.icao); });
    m.on('click', 'live-cams', e => { if (e.features && e.features[0]) selectCamById(e.features[0].properties.id); });
    ['live-events', 'live-flights', 'live-cams'].forEach(id => {
      m.on('mouseenter', id, () => { m.getCanvas().style.cursor = 'pointer'; });
      m.on('mouseleave', id, () => { m.getCanvas().style.cursor = ''; });
    });
    m.on('moveend', () => {
      if (!LIVE.flightsOn) return;
      clearTimeout(moveTimer);
      moveTimer = setTimeout(() => scheduleFlights(300), 2000);
    });
  }

  // ── 8) interface ────────────────────────────────────────────────────────────
  function initLive(){
    dotTex = makeDotTexture();
    ['livePanel', 'layerToggles', 'infoCard'].forEach(id => {
      ['mousemove', 'mousedown', 'wheel', 'click'].forEach(evt =>
        $(id).addEventListener(evt, () => { lastInteract = Date.now(); }, { passive: true }));
    });
    $('lpCollapse').addEventListener('click', () => {
      const p = $('livePanel'); p.classList.toggle('min');
      $('lpCollapse').textContent = p.classList.contains('min') ? '+' : '–';
    });
    $('lpStats').addEventListener('click', e => {
      const t = e.target.closest('.ring-tile[data-type]'); if (!t) return;
      const k = t.getAttribute('data-type');
      if (LIVE.filters.has(k)) LIVE.filters.delete(k); else LIVE.filters.add(k);
      renderStats(); renderFeed(); refreshMarkers();
    });
    $('lpFeed').addEventListener('click', e => {
      const r = e.target.closest('.lp-row'); if (!r || !r.getAttribute('data-id')) return;
      const ev = selectEventById(r.getAttribute('data-id'));
      if (ev) focusPlace(ev.lat, ev.lon, 8);
    });
    $('icClose').addEventListener('click', hideCard);
    $('followHud').addEventListener('click', e => {
      const md = e.target.closest('[data-fmode]'); if (md){ setFollowMode(md.getAttribute('data-fmode')); return; }
      const sp = e.target.closest('[data-mul]');
      if (sp){ FOLLOW.mul = +sp.getAttribute('data-mul');
        document.querySelectorAll('#followHud [data-mul]').forEach(b => b.classList.toggle('on', +b.getAttribute('data-mul') === FOLLOW.mul)); return; }
      if (e.target.closest('#fhExit')) stopFollow();
    });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && FOLLOW.on) stopFollow(); });
    $('icBody').addEventListener('click', e => {
      const fw = e.target.closest('[data-follow]');
      if (fw){ startFollow(selFlightObj, fw.getAttribute('data-follow')); return; }
      const cm = e.target.closest('[data-cam]');
      if (cm){ notify('opencam:' + encodeURIComponent(cm.getAttribute('data-cam'))); return; }
      const u = e.target.closest('[data-url]'); if (u){ openUrl(u.getAttribute('data-url')); return; }
      const g = e.target.closest('[data-go]');
      if (g){ const p = g.getAttribute('data-go').split(','); focusPlace(parseFloat(p[0]), parseFloat(p[1]), 9); }
    });
    function setHolo(id, on){
      const b = $(id); b.classList.toggle('on', on);
      b.querySelector('em').textContent = on ? 'ACTIVE' : 'INACTIVE';
    }
    $('tgRain').addEventListener('click', () => {
      LIVE.rainOn = !LIVE.rainOn; setHolo('tgRain', LIVE.rainOn);
      if (LIVE.rainOn){ setLayerStatus('Rain: loading…'); loadRain().then(() => { if (rainMesh) rainMesh.visible = LIVE.rainOn; syncMapLive(); }); }
      else { if (rainMesh) rainMesh.visible = false; syncMapLive(); if (!LIVE.flightsOn) setLayerStatus(''); }
    });
    $('tgFlights').addEventListener('click', () => {
      LIVE.flightsOn = !LIVE.flightsOn; setHolo('tgFlights', LIVE.flightsOn);
      if (LIVE.flightsOn){ setLayerStatus('Flights: loading…'); lastFlightFetch = 0; scheduleFlights(0); }
      else {
        clearTimeout(flightTimer); LIVE.flights = []; LIVE.flightIdx = new Map();
        stopFollow(false); clearRoute();
        refreshMarkers(); if (!LIVE.rainOn) setLayerStatus('');
      }
    });
    $('tgCams').addEventListener('click', () => {
      LIVE.camsOn = !LIVE.camsOn; setHolo('tgCams', LIVE.camsOn);
      if (LIVE.camsOn) $('tgCams').querySelector('em').textContent = 'ACTIVE · ' + CAMS.length;
      refreshMarkers();
    });
    refreshEvents();
    setInterval(refreshEvents, 300000);
    setInterval(() => { if (LIVE.rainOn) loadRain(); }, 600000);
  }

  init();
})();
</script>
</body></html>
"""


class _BlurBackdrop(QLabel):
    """Layer that covers the HUD with a blurred, darkened capture of it while the
    Earth is open, so that the holographic wings and their text read cleanly.
    It is a still of the HUD at the moment of opening (it does not animate behind)."""

    BLUR_RADIUS = 16
    TINT = QColor(0, 0, 0, 0)      # no darkening: blur only
    FEATHER = 0.0                   # fraction of the width/height that fades at each edge

    def __init__(self, parent, on_click):
        super().__init__(parent)
        self._on_click = on_click
        self.setScaledContents(True)
        self._fx = QGraphicsOpacityEffect(self)
        self._fx.setOpacity(0.0)
        self.setGraphicsEffect(self._fx)
        self._fade = QPropertyAnimation(self._fx, b"opacity", self)
        self._fade.setDuration(320)
        self._fade.finished.connect(self._after_fade)
        self.hide()

    @staticmethod
    def _blur(pm: QPixmap, radius: float, tint: QColor) -> QPixmap:
        pm = QPixmap(pm)
        pm.setDevicePixelRatio(1.0)
        scene = QGraphicsScene()
        item = QGraphicsPixmapItem(pm)
        eff = QGraphicsBlurEffect()
        eff.setBlurRadius(radius)
        item.setGraphicsEffect(eff)
        scene.addItem(item)
        out = QPixmap(pm.size())
        out.fill(Qt.GlobalColor.transparent)
        painter = QPainter(out)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        scene.render(painter, QRectF(0, 0, pm.width(), pm.height()), QRectF(0, 0, pm.width(), pm.height()))
        if tint.alpha() > 0:
            painter.fillRect(out.rect(), tint)
        # Softens the outline: multiplies the alpha by a horizontal and a vertical gradient,
        # so the edges and corners blend with the HUD underneath (no visible rectangle).
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        f = _BlurBackdrop.FEATHER
        for horizontal in ((True, False) if f > 0 else ()):
            g = QLinearGradient(0, 0, pm.width() if horizontal else 0, 0 if horizontal else pm.height())
            g.setColorAt(0.0, QColor(0, 0, 0, 0))
            g.setColorAt(f,   QColor(0, 0, 0, 255))
            g.setColorAt(1.0 - f, QColor(0, 0, 0, 255))
            g.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.fillRect(out.rect(), g)
        painter.end()
        return out

    def show_over(self, snapshot: QPixmap, rect: QRect, above: QWidget):
        try:
            self.setPixmap(self._blur(snapshot, self.BLUR_RADIUS, self.TINT))
        except Exception as e:
            print(f"[Globe] backdrop blur failed: {e}")
            return
        self.setGeometry(rect)
        self.stackUnder(above)
        self.show()
        self._fade.stop()
        self._fade.setStartValue(self._fx.opacity())
        self._fade.setEndValue(1.0)
        self._fade.start()

    def fade_out(self):
        if not self.isVisible():
            return
        self._fade.stop()
        self._fade.setStartValue(self._fx.opacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _after_fade(self):
        if self._fx.opacity() < 0.01:
            self.hide()

    def mousePressEvent(self, event):
        # click outside the Earth = close it
        self._on_click()


# ── Flights (Python server: no CORS) ───────────────────────────────────────
_UA = {"User-Agent": "Mozilla/5.0 (JARVIS-HUD globe)", "Accept": "application/json"}


def _http_json(url: str, timeout: float = 15.0):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _flights_opensky(bbox):
    url = "https://opensky-network.org/api/states/all"
    if bbox:
        url += (f"?lamin={bbox['s']}&lomin={bbox['w']}&lamax={bbox['n']}&lomax={bbox['e']}")
    j = _http_json(url, 20)
    out = []
    for s in (j.get("states") or []):
        if s[8] or s[5] is None or s[6] is None:
            continue
        out.append({"icao": s[0], "cs": (s[1] or "").strip(), "country": s[2] or "—",
                    "lon": s[5], "lat": s[6],
                    "alt": s[13] if s[13] is not None else (s[7] or 0),
                    "spd": s[9] or 0, "hdg": s[10] or 0, "vr": s[11] or 0})
    return out


def _adsb_point(host: str, lat: float, lon: float, nm: int):
    j = _http_json(f"https://{host}/v2/point/{lat:.3f}/{lon:.3f}/{nm}", 15)
    out = []
    for a in (j.get("ac") or j.get("aircraft") or []):
        la, lo = a.get("lat"), a.get("lon")
        alt = a.get("alt_baro")
        if la is None or lo is None or alt in (None, "ground"):
            continue
        try:
            alt_m = float(alt) * 0.3048
        except (TypeError, ValueError):
            continue
        out.append({"icao": (a.get("hex") or "").lower(), "cs": (a.get("flight") or "").strip(),
                    "country": "—", "type": a.get("t") or "", "reg": a.get("r") or "",
                    "lon": lo, "lat": la, "alt": alt_m,
                    "spd": (a.get("gs") or 0) * 0.514444, "hdg": a.get("track") or 0,
                    "vr": (a.get("baro_rate") or 0) * 0.00508})
    return out


# High-traffic areas for the global view when OpenSky does not respond (250 nm radius each).
_HUBS = [(40, -100), (35, -85), (45, -120), (50, 5), (45, 20), (52, -2), (40, 40), (25, 55),
         (28, 77), (22, 114), (35, 135), (-33, 151), (-23, -46), (35, -5), (1, 103), (60, 30)]


def _flights_adsb(bbox):
    hosts = ["api.adsb.lol", "api.airplanes.live"]
    if bbox:
        lat = (bbox["s"] + bbox["n"]) / 2
        lon = (bbox["w"] + bbox["e"]) / 2
        # radius covering the box, max. 250 nm
        dlat = abs(bbox["n"] - bbox["s"]) * 30.0
        dlon = abs(bbox["e"] - bbox["w"]) * 30.0 * max(0.2, math.cos(math.radians(lat)))
        nm = int(min(250, max(25, math.hypot(dlat, dlon) / 2)))
        points = [(lat, lon, nm)]
    else:
        points = [(la, lo, 250) for la, lo in _HUBS]
    last_err = None
    for host in hosts:
        try:
            with ThreadPoolExecutor(max_workers=6) as ex:
                results = list(ex.map(lambda p: _adsb_point(host, *p), points))
            merged = {}
            for chunk in results:
                for f in chunk:
                    merged[f["icao"] or id(f)] = f
            if merged:
                return list(merged.values())
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise last_err or RuntimeError("no data")


def _fetch_flights_any(bbox):
    """OpenSky first (global, no key); if it fails or is rate-limited, open ADS-B (adsb.lol / airplanes.live)."""
    errors = []
    backoff = 0
    try:
        fl = _flights_opensky(bbox)
        if fl:
            return {"ok": True, "flights": fl, "src": "opensky"}
        errors.append("OpenSky returned no data")
    except urllib.error.HTTPError as e:
        errors.append(f"OpenSky {e.code}")
        if e.code == 429:
            backoff = 120
    except Exception as e:  # noqa: BLE001
        errors.append(f"OpenSky: {e}")
    try:
        fl = _flights_adsb(bbox)
        if fl:
            return {"ok": True, "flights": fl, "src": "adsb"}
        errors.append("ADS-B returned no data")
    except Exception as e:  # noqa: BLE001
        errors.append(f"ADS-B: {e}")
    msg = " · ".join(errors)
    print(f"[Globe] flights failed: {msg}")
    return {"ok": False, "error": msg[:90], "backoff": 20 if not backoff else backoff}



# ── Flight data (aircraft + route with airport coordinates) ─────────
_INFO_CACHE: dict = {}


def _http_post_json(url: str, payload, timeout: float = 10.0):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={**_UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _ap_norm(a: dict):
    try:
        lat, lon = a.get("lat", a.get("latitude")), a.get("lon", a.get("longitude"))
        if lat is None or lon is None:
            return None
        return {"icao": a.get("icao"), "iata": a.get("iata"),
                "name": a.get("name") or a.get("airport"),
                "city": a.get("location") or a.get("region_name") or "",
                "lat": float(lat), "lon": float(lon)}
    except Exception:  # noqa: BLE001
        return None


def _route_for(cs: str, lat: float, lon: float):
    if not cs:
        return None
    try:   # adsb.lol: route with airports and coordinates in a single call
        j = _http_post_json("https://api.adsb.lol/api/0/routeset",
                            {"planes": [{"callsign": cs, "lat": lat, "lng": lon}]}, 10)
        rows = j if isinstance(j, list) else (j.get("routes") or [])
        for r in rows:
            aps = r.get("_airports") or []
            if len(aps) >= 2 and r.get("plausible", True) is not False:
                a, b = _ap_norm(aps[0]), _ap_norm(aps[-1])
                if a and b:
                    return {"from": a, "to": b}
    except Exception as e:  # noqa: BLE001
        print(f"[Globe] routeset: {e}")
    try:   # fallback: hexdb (ICAO codes) + coordinates of each airport
        j = _http_json(f"https://hexdb.io/api/v1/route/icao/{cs}", 8)
        codes = [c for c in (j.get("route") or "").split("-") if c]
        if len(codes) >= 2:
            pair = []
            for code in (codes[0], codes[-1]):
                ap = _http_json(f"https://hexdb.io/api/v1/airport/icao/{code}", 8)
                n = _ap_norm({"icao": ap.get("icao") or code, "iata": ap.get("iata"),
                              "name": ap.get("airport"), "location": ap.get("region_name"),
                              "latitude": ap.get("latitude"), "longitude": ap.get("longitude")})
                pair.append(n)
            if pair[0] and pair[1]:
                return {"from": pair[0], "to": pair[1]}
    except Exception as e:  # noqa: BLE001
        print(f"[Globe] hexdb route: {e}")
    return None


def _aircraft_for(hexid: str):
    if not hexid:
        return None
    try:
        a = _http_json(f"https://hexdb.io/api/v1/aircraft/{hexid}", 8)
        if not isinstance(a, dict) or a.get("status") == "404":
            return None
        return {"reg": a.get("Registration"), "icao_type": a.get("ICAOTypeCode"),
                "model": a.get("Type"), "manufacturer": a.get("Manufacturer"),
                "owner": a.get("RegisteredOwners")}
    except Exception:  # noqa: BLE001
        return None


def _flight_info(req: dict) -> dict:
    icao = (req.get("icao") or "").lower()
    if icao in _INFO_CACHE:
        return _INFO_CACHE[icao]
    with ThreadPoolExecutor(max_workers=2) as ex:
        fa = ex.submit(_aircraft_for, icao)
        fr = ex.submit(_route_for, (req.get("cs") or "").strip(),
                       float(req.get("lat") or 0), float(req.get("lon") or 0))
        res = {"icao": icao, "aircraft": fa.result(), "route": fr.result()}
    _INFO_CACHE[icao] = res
    return res


class GlobeWidget(QWidget):
    """Overlay child of HudCanvas. The parent only needs to call
    reposition(rect) on resize; everything else is internal state."""

    IDLE_SIZE = 64
    _flights_ready = pyqtSignal(str)
    MARGIN    = 10          # kept as an absolute floor so it never hugs the frame
    MARGIN_FRAC = 0.055     # extra separation from the corner, proportional to panel size
    _ANIM_MS  = 420

    def __init__(self, parent=None):
        super().__init__(parent)
        self._available  = _WEBENGINE_OK
        self._expanded   = False
        self._panel_rect = QRect(0, 0, 300, 300)

        if not self._available:
            # No PyQt6-WebEngine installed — stay invisible and let every
            # public method below no-op. Nothing else in the HUD depends on
            # this widget existing.
            self.hide()
            return

        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("background: transparent;")

        self._view = QWebEngineView(self)
        self._view.setPage(_LoggingPage(self._view))
        self._view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        try:
            self._view.page().setBackgroundColor(Qt.GlobalColor.transparent)
        except Exception:
            pass
        self._view.setStyleSheet("background: transparent;")
        try:
            self._view.settings().setAttribute(
                QWebEngineSettings.WebAttribute.ShowScrollBars, False
            )
        except Exception:
            pass
        try:
            _cams_json = json.dumps(load_cams(), ensure_ascii=False).replace("</", "<\\/")
        except Exception:  # noqa: BLE001
            _cams_json = "[]"
        self._view.setHtml(_GLOBE_HTML.replace("__LIVE_CAMS_JSON__", _cams_json))
        self._view.titleChanged.connect(self._on_title_changed)
        self._flights_ready.connect(lambda js: self._run_js(js))

        # Transparent click-catcher, active only while idle: a single click
        # expands the globe without Chromium ever treating it as a page
        # interaction. Hidden while expanded, so drag/scroll goes straight
        # through to the Three.js canvas. No visible border — idle state
        # should read as just the rotating sphere, nothing framing it.
        parent_w = self.parentWidget()
        self._backdrop = _BlurBackdrop(parent_w, self.collapse) if parent_w is not None else None

        self._catcher = QWidget(self)
        self._catcher.setCursor(Qt.CursorShape.PointingHandCursor)
        self._catcher.setStyleSheet("background: transparent; border: none;")
        self._catcher.mousePressEvent = lambda e: self.expand()

        self._exit_btn = QPushButton("✕", self)
        self._exit_btn.setFixedSize(26, 26)
        self._exit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._exit_btn.setFont(QFont("Courier New", 10, QFont.Weight.Bold))
        self._exit_btn.setStyleSheet("""
            QPushButton { background: rgba(0,10,14,200); color: #39e8ff;
                border: 1px solid #39e8ff; border-radius: 13px; }
            QPushButton:hover { background: rgba(57,232,255,45); }
        """)
        self._exit_btn.clicked.connect(self.collapse)
        self._exit_btn.hide()

        self._anim = QPropertyAnimation(self, b"geometry")
        self._anim.setDuration(self._ANIM_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._anim.finished.connect(self._sync_children)

        self.reposition(self._panel_rect)

    # ── geometry ─────────────────────────────────────────────────────────────

    def reposition(self, panel_rect: QRect):
        """Called by HudCanvas.resizeEvent with its own rect. Only snaps
        geometry directly when not mid-animation, so a window resize during
        the expand/collapse transition doesn't fight the animation."""
        if not self._available:
            return
        self._panel_rect = panel_rect
        if self._backdrop is not None and self._backdrop.isVisible():
            self._backdrop.setGeometry(panel_rect)
        if self._anim.state() != QPropertyAnimation.State.Running:
            self.setGeometry(self._expanded_geometry() if self._expanded
                              else self._idle_geometry())
        self._sync_children()

    def _idle_geometry(self) -> QRect:
        r = self._panel_rect
        s = self.IDLE_SIZE
        # Same proportional pull-away from the corner on both axes, so the
        # globe sits a bit further left and down instead of hugging the edge.
        margin = self.MARGIN + int(min(r.width(), r.height()) * self.MARGIN_FRAC)
        return QRect(r.right() - s - margin, r.top() + margin, s, s)

    def _expanded_geometry(self) -> QRect:
        r = self._panel_rect
        # Wide rectangle: the map/globe is a centred square inside it (decided by the
        # page, see layoutStage) and the holographic wings occupy the free sides.
        h = max(120, int(r.height() * 0.92))
        w = max(h, min(int(r.width() * 0.97), int(h * 2.4)))
        cx, cy = r.center().x(), r.center().y()
        return QRect(cx - w // 2, cy - h // 2, w, h)

    def _animate_to(self, target: QRect):
        self._anim.stop()
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(target)
        self._anim.start()

    def _sync_children(self):
        self._view.setGeometry(0, 0, self.width(), self.height())
        self._catcher.setGeometry(0, 0, self.width(), self.height())
        self._exit_btn.move(max(0, self.width() - 32), 6)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._available:
            self._sync_children()

    # ── expand / collapse / fly-to ───────────────────────────────────────────

    def expand(self):
        if not self._available or self._expanded:
            return
        self._expanded = True
        self._catcher.hide()
        self._exit_btn.show()
        self._show_backdrop()
        self.raise_()
        self._backdrop.stackUnder(self) if self._backdrop is not None else None
        self._animate_to(self._expanded_geometry())
        self._run_js("window.setExpanded && setExpanded(true);")

    def collapse(self):
        if not self._available or not self._expanded:
            return
        self._expanded = False
        self._exit_btn.hide()
        self._hide_backdrop()
        self._animate_to(self._idle_geometry())
        QTimer.singleShot(self._ANIM_MS + 10, self._catcher.show)
        self._run_js("window.setExpanded && setExpanded(false);")

    def fly_to(self, lat: float, lon: float, label: str = ""):
        """Voice-triggered: expand (if idle), swing the globe to face the
        point, then drop straight into the street-level 3D map centered
        there. Safe to call from a background thread's-caller as long as
        it's routed through a Qt signal first — see MainWindow._globe_flyto_sig."""
        if not self._available:
            return
        if not self._expanded:
            self._expanded = True
            self._catcher.hide()
            self._exit_btn.show()
            self._show_backdrop()
            self.raise_()
            self._backdrop.stackUnder(self) if self._backdrop is not None else None
            self._animate_to(self._expanded_geometry())
        safe_label = str(label).replace("\\", "").replace("'", "").replace('"', "")[:80]
        self._run_js(f"window.flyToMap && flyToMap({float(lat)!r}, {float(lon)!r}, '{safe_label}');")

    def _show_backdrop(self):
        """Captures the HUD (without the Earth) and shows it blurred beneath it."""
        if self._backdrop is None:
            return
        parent = self.parentWidget()
        try:
            self._backdrop.hide()
            self.hide()                      # so that the Earth itself does not appear in the capture
            snapshot = parent.grab()
            self.show()
            self._backdrop.show_over(snapshot, parent.rect(), self)
        except Exception as e:
            print(f"[Globe] backdrop failed: {e}")
            self.show()

    def _hide_backdrop(self):
        if self._backdrop is not None:
            self._backdrop.fade_out()

    def _run_js(self, code: str):
        try:
            self._view.page().runJavaScript(code)
        except Exception:
            pass

    # ── JS → Python (see notify() in _GLOBE_HTML) ────────────────────────────

    def _on_title_changed(self, title: str):
        if not title.startswith("GLOBE_EVENT:"):
            return
        parts = title.split(":")
        kind = parts[1] if len(parts) > 1 else ""
        if kind == "collapse":
            self.collapse()
        elif kind == "flights" and len(parts) > 2:
            try:
                req = json.loads(unquote(parts[2]))
            except Exception:
                req = {}
            bbox = req.get("bbox")

            def _work():
                res = _fetch_flights_any(bbox)
                self._flights_ready.emit("window.__flightsResult(" + json.dumps(res) + ");")
            threading.Thread(target=_work, daemon=True).start()
        elif kind == "flightinfo" and len(parts) > 2:
            try:
                req = json.loads(unquote(parts[2]))
            except Exception:
                req = {}

            def _work_info():
                try:
                    res = _flight_info(req)
                except Exception as e:  # noqa: BLE001
                    print(f"[Globe] flight info failed: {e}")
                    res = {"icao": (req.get("icao") or "").lower(), "aircraft": None, "route": None}
                self._flights_ready.emit("window.__flightInfo(" + json.dumps(res) + ");")
            threading.Thread(target=_work_info, daemon=True).start()
        elif kind == "opencam" and len(parts) > 2:
            # "▶ WATCH LIVE": the floating window lives in the main window
            # (ui.py → MainWindow.show_live_cam), not inside this page.
            cam = find_cam(unquote(parts[2]))
            win = self.window()
            if cam is not None and hasattr(win, "show_live_cam"):
                try:
                    win.show_live_cam(cam)
                except Exception as e:  # noqa: BLE001
                    print(f"[Globe] Could not open the camera: {e}")
        elif kind == "openurl" and len(parts) > 2:
            # The page does not navigate by itself (the globe would be lost): it asks Python
            # to open the source link in the system browser.
            url = unquote(parts[2])
            if url.startswith(('http://', 'https://')):
                try:
                    webbrowser.open(url)
                except Exception:
                    pass
        # "interacted" needs no Python-side reaction right now — the JS side
        # already paused its own auto-orbit; kept as a hook for later.
