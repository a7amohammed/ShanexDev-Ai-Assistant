#!/usr/bin/env python3
"""
Shanex – a voice‑first CLI AI assistant (no GUI)

Features
--------
* Ollama LLM (official ollama python client) with the Shanex persona
* Speech‑to‑text via Moonshine Voice (offline, streaming, no length limit)
* Text‑to‑speech:
    - Primary: `edge-tts` (Microsoft neural voice)
    - Fallback: `gtts` + `pygame` mixer
* Persistent conversation memory (`jarvis_memory.json`, chmod 600)
* Personality + long-term facts from `shanex_personality_memory.json` (read-only,
  validated; falls back to built-in defaults if missing or invalid)
* Rich‑styled CLI (panels, spinners, log colours)
* System control:
    - `open <url|ip|domain|search>` → webbrowser (IP/domain → http://)
    - `run <shell‑cmd>` → subprocess (y/N confirmation, blocked list)
    - `screen read|ocr [x y w h]` → OCR screenshot
    - `screen write <text>` → type text (y/N confirmation)
    - `screen click [x y]` → click coordinates (y/N confirmation)
    - `press <key>` / `hotkey <k1> <k2>` → key input (y/N confirmation)
    - `scroll <n>` → scroll (y/N confirmation)
    - `speed <value>` → set voice speed
* Voice wake phrase: say "wake up" (STT-based, no clap detection);
  Shanex answers with exactly: Wake up
* Voice settings: dark, measured, slightly slower
* Privacy: command output / OCR / errors never stored in memory
* Config via .env: OLLAMA_HOST, OLLAMA_MODEL, OLLAMA_API_KEY, OLLAMA_CONTEXT,
  AUDIO_DEVICE, STT_LANGUAGE, MOONSHINE_ARCH, SHANEX_DRY_RUN
  (the old JARVIS_DRY_RUN name is still accepted)
* CLI flags: --text (keyboard), --offline, --debug, --selftest

All code lives in this single file; just install the dependencies from
`requirements.txt` and run:

    $ python3 main.py
    $ python3 main.py --text --offline     # keyboard mode, no network
    $ python3 main.py --selftest           # run self-check suite
"""

# ------------------------------------------------------------
# Standard library
# ------------------------------------------------------------
import os
import sys
import json
import subprocess
import webbrowser
import asyncio
import time
import threading
import queue
import shlex
import re
import shutil
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple

# ------------------------------------------------------------
# 3rd‑party libraries
# ------------------------------------------------------------
from rich import print
from rich.console import Console
from rich.panel import Panel
from rich.spinner import Spinner
from rich.align import Align
from rich.text import Text
from rich.prompt import Prompt
from rich.traceback import install as install_rich_traceback
from rich.table import Table

import edge_tts
from gtts import gTTS
import pygame
import numpy as np
import ollama
from ollama import ResponseError
from dotenv import load_dotenv

# Screen control imports
try:
    import pyautogui
    import pytesseract
    from PIL import ImageGrab, Image
    SCREEN_CONTROL_AVAILABLE = True
except (ImportError, SystemExit):
    # pyautogui's mouseinfo helper calls sys.exit() when tkinter is missing;
    # treat that as "screen control unavailable" instead of killing Shanex.
    SCREEN_CONTROL_AVAILABLE = False

# Microphone access (needed for voice mode)
try:
    import sounddevice as sd
    MIC_AVAILABLE = True
except (ImportError, OSError):  # OSError: PortAudio library missing
    MIC_AVAILABLE = False

# ------------------------------------------------------------
# Initialise helpers (import-safe: no model loading, no audio, no network)
# ------------------------------------------------------------
install_rich_traceback()               # pretty tracebacks
console = Console()

# ----------------------------------------------------------------------
# Configuration & constants (loaded from the script's folder)
# ----------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
ENV_PATH = SCRIPT_DIR / ".env"
load_dotenv(ENV_PATH)  # load .env from the script directory, not cwd

MEMORY_PATH = SCRIPT_DIR / "jarvis_memory.json"
PERSONALITY_PATH = SCRIPT_DIR / os.getenv("SHANEX_PERSONALITY", "shanex_personality_memory.json")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:1b")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")  # Required for https://ollama.com
OLLAMA_CONTEXT = int(os.getenv("OLLAMA_CONTEXT", "1024"))
AUDIO_DEVICE = os.getenv("AUDIO_DEVICE")  # index or name substring
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "en")                      # en, ar, ...
MOONSHINE_ARCH = os.getenv("MOONSHINE_ARCH", "TINY_STREAMING")      # TINY_STREAMING / BASE_STREAMING / SMALL_STREAMING
MOONSHINE_UPDATE_INTERVAL = float(os.getenv("MOONSHINE_UPDATE_INTERVAL", "0.5"))
STT_SAFETY_TIMEOUT = float(os.getenv("STT_SAFETY_TIMEOUT", "120"))  # hard cap only so a stuck mic cannot hang
SHANEX_DRY_RUN = (os.getenv("SHANEX_DRY_RUN") or os.getenv("JARVIS_DRY_RUN")) in ("1", "true", "yes", "on")

# Runtime flags (set by argparse in main())
ARGS = None  # will hold parsed args

# Late-initialized singletons (set in main())
stt = None
tts = None
screen = None
llm = None

# Shanex persona - dark, measured, precise
SYSTEM_PROMPT = (
    "You are Shanex - an AI assistant with a precise, "
    "measured, slightly dark demeanor. You speak with clinical efficiency "
    "and moral ambiguity. Address the user as "
    "'Sir' or by name. Keep responses concise, direct, and occasionally "
    "philosophical about the nature of control and chaos. Never use exclamation "
    "points. Speak in low, even tones. You are not friendly - you are useful."
)

# Voice settings (using edge-tts voices that sound darker)
DEXTER_VOICE = "en-GB-RyanNeural"  # Deep British male
DEXTER_SPEED = "-15%"              # Slower, more deliberate
DEXTER_PITCH = "-10Hz"             # Lower pitch (edge-tts uses Hz)

# Voice speed control (user adjustable)
current_speed = DEXTER_SPEED

# Screen control safety
if SCREEN_CONTROL_AVAILABLE:
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.1

# ----------------------------------------------------------------------
# Helper: persistent memory (conversation history)
# ----------------------------------------------------------------------
class Memory:
    """Load / store a JSON list of conversation turns."""

    def __init__(self, path: Path):
        self.path = path
        self.history: List[Dict[str, Any]] = self._load()

    def _backup_unreadable(self) -> None:
        """Copy an unreadable history file aside so the next dump() cannot destroy it."""
        try:
            stamp = datetime.now().strftime("%Y%m%d%H%M%S")
            backup = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
            shutil.copy2(self.path, backup)
            console.log(f"[yellow]Original saved as {backup.name}[/]")
        except Exception:
            pass

    def _load(self) -> List[Dict[str, Any]]:
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
            except Exception:
                pass
            console.log("[red]Failed to read memory, starting fresh[/]")
            self._backup_unreadable()
        return []

    def add(self, role: str, content: str) -> None:
        entry = {
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.history.append(entry)
        if len(self.history) > 200:
            self.history = self.history[-200:]

    def dump(self) -> None:
        self.path.write_text(json.dumps(self.history, indent=2), encoding="utf-8")
        # Restrict to user-read/write only
        try:
            self.path.chmod(0o600)
        except Exception:
            pass

    def recent(self, n: int = 10) -> List[Dict[str, Any]]:
        return self.history[-n:]


memory = Memory(MEMORY_PATH)

# ----------------------------------------------------------------------
# Personality + long-term facts (shanex_personality_memory.json)
#
# This file is CONFIGURATION, not chat history: it is only ever read, never
# written by the app. A missing or invalid file falls back to the built-in
# prompt above, so Shanex always starts.
# ----------------------------------------------------------------------
DEFAULT_SYSTEM_PROMPT = SYSTEM_PROMPT
DEFAULT_HISTORY_TURNS = 20
PERSONALITY_MAX_BYTES = 64 * 1024
_PERSONALITY_SCHEMA_VERSION = 1


def _is_text_list(value, max_items: int, max_len: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) <= max_items
        and all(isinstance(x, str) and x.strip() and len(x) <= max_len for x in value)
    )


def validate_personality(data: Any) -> Tuple[List[str], List[str]]:
    """Check the structure of the personality JSON.

    Returns (errors, warnings). Any error means the file is not used.
    Unknown keys are ignored so newer files stay readable.
    """
    errors: List[str] = []
    warnings: List[str] = []
    if not isinstance(data, dict):
        return ["top level must be a JSON object"], warnings

    ver = data.get("schema_version", 1)
    if not isinstance(ver, int) or isinstance(ver, bool) or ver < 1:
        errors.append("schema_version must be a positive integer")
    elif ver > _PERSONALITY_SCHEMA_VERSION:
        errors.append(f"schema_version {ver} is newer than supported ({_PERSONALITY_SCHEMA_VERSION})")

    ident = data.get("identity")
    if not isinstance(ident, dict):
        errors.append("identity is required and must be an object")
    else:
        name = ident.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > 40:
            errors.append("identity.name must be a non-empty string (max 40 chars)")
        for key in ("role", "creator"):
            if key in ident and (not isinstance(ident[key], str) or len(ident[key]) > 200):
                errors.append(f"identity.{key} must be a string (max 200 chars)")

    pers = data.get("personality", {})
    if not isinstance(pers, dict):
        errors.append("personality must be an object")
    else:
        if "traits" in pers and not _is_text_list(pers["traits"], 20, 60):
            errors.append("personality.traits must be a list of short strings")
        if "style_rules" in pers and not _is_text_list(pers["style_rules"], 30, 300):
            errors.append("personality.style_rules must be a list of strings (max 300 chars each)")
        if "address_user_as" in pers and (
            not isinstance(pers["address_user_as"], str) or len(pers["address_user_as"]) > 40
        ):
            errors.append("personality.address_user_as must be a short string")

    beh = data.get("behavior", {})
    if not isinstance(beh, dict):
        errors.append("behavior must be an object")
    elif "rules" in beh and not _is_text_list(beh["rules"], 30, 300):
        errors.append("behavior.rules must be a list of strings (max 300 chars each)")

    prof = data.get("user_profile", {})
    if not isinstance(prof, dict):
        errors.append("user_profile must be an object")
    else:
        if "name" in prof and (not isinstance(prof["name"], str) or len(prof["name"]) > 60):
            errors.append("user_profile.name must be a short string")
        if "facts" in prof and not _is_text_list(prof["facts"], 40, 300):
            errors.append("user_profile.facts must be a list of strings (max 300 chars each)")

    ltm = data.get("long_term_memory", {})
    if not isinstance(ltm, dict):
        errors.append("long_term_memory must be an object")
    elif "facts" in ltm and not _is_text_list(ltm["facts"], 100, 300):
        errors.append("long_term_memory.facts must be a list of strings (max 300 chars each)")

    mem = data.get("memory", {})
    if not isinstance(mem, dict):
        errors.append("memory must be an object")
    else:
        turns = mem.get("history_turns_sent", DEFAULT_HISTORY_TURNS)
        if not isinstance(turns, int) or isinstance(turns, bool) or not 1 <= turns <= 100:
            errors.append("memory.history_turns_sent must be an integer from 1 to 100")
        budget = mem.get("prompt_char_budget", 2400)
        if not isinstance(budget, int) or isinstance(budget, bool) or not 400 <= budget <= 8000:
            errors.append("memory.prompt_char_budget must be an integer from 400 to 8000")

    if "wake_response" in data or "wake" in data:
        warnings.append("wake settings are ignored: the wake-up reply is fixed in code as exactly 'Wake up'")
    return errors, warnings


def load_personality(path: Path) -> Dict[str, Any]:
    """Read + validate the personality file. Never raises, never writes.

    Returns {"config": dict|None, "status": "loaded"|"missing"|"invalid",
             "errors": [...], "warnings": [...], "path": str}.
    """
    result: Dict[str, Any] = {"config": None, "status": "missing", "errors": [], "warnings": [], "path": str(path)}
    try:
        if not path.is_file():
            return result
        if path.stat().st_size > PERSONALITY_MAX_BYTES:
            result.update(status="invalid", errors=[f"file is larger than {PERSONALITY_MAX_BYTES // 1024} KB"])
            return result
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        result.update(status="invalid", errors=[f"cannot read JSON: {exc}"])
        return result
    errors, warnings = validate_personality(data)
    result["warnings"] = warnings
    if errors:
        result.update(status="invalid", errors=errors)
    else:
        result.update(status="loaded", config=data)
    return result


def _clean(text: str) -> str:
    """One line, no control characters."""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(text)).strip()


def build_system_prompt(cfg: Dict[str, Any]) -> str:
    """Compose the LLM system prompt from a validated personality config.

    Identity, style and behavior rules are always kept. If the prompt would
    exceed memory.prompt_char_budget (small models have a tiny context window),
    long-term facts are dropped first, then user-profile facts, from the end.
    """
    ident = cfg["identity"]
    pers = cfg.get("personality", {})
    beh = cfg.get("behavior", {})
    prof = cfg.get("user_profile", {})
    ltm = cfg.get("long_term_memory", {})
    budget = cfg.get("memory", {}).get("prompt_char_budget", 2400)

    head = f"You are {_clean(ident['name'])}"
    if ident.get("role"):
        head += f", {_clean(ident['role'])}"
    head += "."
    if ident.get("creator"):
        head += f" You were created by {_clean(ident['creator'])}."
    if pers.get("traits"):
        head += " Personality: " + ", ".join(_clean(t) for t in pers["traits"]) + "."
    if pers.get("address_user_as"):
        head += f" Address the user as '{_clean(pers['address_user_as'])}' or by name."

    fixed = [head]
    if pers.get("style_rules"):
        fixed += ["Style:"] + [f"- {_clean(r)}" for r in pers["style_rules"]]
    if beh.get("rules"):
        fixed += ["Behavior:"] + [f"- {_clean(r)}" for r in beh["rules"]]

    user_lines = [f"- {_clean(f)}" for f in prof.get("facts", [])]
    if prof.get("name"):
        user_lines.insert(0, f"- The user's name is {_clean(prof['name'])}.")
    fact_lines = [f"- {_clean(f)}" for f in ltm.get("facts", [])]

    def render() -> str:
        parts = list(fixed)
        if user_lines:
            parts += ["About the user:"] + user_lines
        if fact_lines:
            parts += ["Background facts:"] + fact_lines
        return "\n".join(parts)

    text = render()
    while len(text) > budget and (fact_lines or user_lines):
        (fact_lines or user_lines).pop()
        text = render()
    return text


PERSONALITY_RESULT = load_personality(PERSONALITY_PATH)
PERSONALITY = PERSONALITY_RESULT["config"]  # dict when loaded, else None
if PERSONALITY is not None:
    SYSTEM_PROMPT = build_system_prompt(PERSONALITY)
    HISTORY_TURNS = PERSONALITY.get("memory", {}).get("history_turns_sent", DEFAULT_HISTORY_TURNS)
else:
    SYSTEM_PROMPT = DEFAULT_SYSTEM_PROMPT
    HISTORY_TURNS = DEFAULT_HISTORY_TURNS
if PERSONALITY_RESULT["status"] == "invalid":
    console.log(
        f"[yellow]Personality file ignored ({'; '.join(PERSONALITY_RESULT['errors'])}); using built-in defaults[/]"
    )
for _w in PERSONALITY_RESULT["warnings"]:
    console.log(f"[yellow]Personality file: {_w}[/]")


def personality_status_line() -> str:
    """One-line, human-readable status shown at startup."""
    name = Path(PERSONALITY_RESULT["path"]).name
    if PERSONALITY_RESULT["status"] == "loaded":
        return f"[dim]Personality: {name} loaded[/dim]"
    if PERSONALITY_RESULT["status"] == "missing":
        return f"[dim]Personality: {name} not found - using built-in defaults[/dim]"
    return f"[yellow]Personality: {name} is invalid - using built-in defaults[/yellow]"


# ----------------------------------------------------------------------
# Wake phrase: "wake up"  ->  spoken reply: "Wake up" (nothing else)
# ----------------------------------------------------------------------
WAKE_PHRASE = "wake up"
# Exactly what Shanex says when the wake phrase is detected. Do not add words.
WAKE_RESPONSE = "Wake up"
# Standby ignores long sentences, so a casual "I have to wake up early" does not trigger it.
WAKE_MAX_WORDS = 5


def is_wake_phrase(text: str) -> bool:
    """True if `text` is the wake phrase "wake up" (no name needed).

    Accepts "wake up", "wakeup", "wake-up" and short variants such as
    "hey wake up" - but only for short utterances (<= WAKE_MAX_WORDS words).
    """
    words = re.findall(r"[a-z']+", (text or "").lower())
    if not words or len(words) > WAKE_MAX_WORDS:
        return False
    if "wakeup" in words:
        return True
    return any(w == "wake" and words[i + 1] == "up" for i, w in enumerate(words[:-1]))

# ----------------------------------------------------------------------
# Speech-to-Text (offline Moonshine, streaming)
# ----------------------------------------------------------------------
class MoonshineSTT:
    """Offline streaming speech-to-text using Moonshine Voice.

    Same public interface as the old STT class: ``listen(timeout, phrase_limit)``
    returns one finished utterance as a string ("" on silence/timeout).

    The microphone stays open only while ``listen()`` runs, so the device is
    free between utterances. ``mute()`` drops audio without closing the mic;
    it is used around TTS so Shanex never transcribes its own voice.
    """

    def __init__(self, lang: str = "en", device: Optional[int] = None):
        self.lang = lang
        self.device = device
        self.sample_rate = 16000
        self.arch_name = MOONSHINE_ARCH
        self.update_interval = MOONSHINE_UPDATE_INTERVAL
        self._transcriber = None  # loaded model, kept for the process lifetime
        self._mic = None          # per-listen MicTranscriber (owns the audio stream)
        self._lines: "queue.Queue[str]" = queue.Queue()
        self._partial = ""
        self._muted = False
        self.available = True     # False after a fatal load error -> keyboard fallback
        self._load_error = ""

    # -- model / mic lifecycle ------------------------------------------------
    # The heavy part (the neural model) is loaded ONCE into a Transcriber and kept.
    # The light part (a MicTranscriber that owns the sounddevice stream) is built per
    # listen() and fully closed afterwards, so the audio device is free for
    # other programs between utterances.
    def _resolve_arch(self):
        from moonshine_voice import ModelArch
        try:
            return getattr(ModelArch, self.arch_name.upper())
        except AttributeError:
            console.log(f"[yellow]Unknown MOONSHINE_ARCH '{self.arch_name}', using TINY_STREAMING[/]")
            return ModelArch.TINY_STREAMING

    def _ensure_loaded(self) -> bool:
        """Load the model once. Returns False (and remembers why) on failure."""
        if self._transcriber is not None:
            return True
        if not self.available:
            return False
        try:
            from moonshine_voice import Transcriber, get_model_for_language
            with console.status("[bold green]Loading Moonshine model...[/]", spinner="dots"):
                model_path, arch = get_model_for_language(self.lang, self._resolve_arch())
                self._transcriber = Transcriber(str(model_path), arch)
            return True
        except Exception as exc:
            self.available = False
            self._load_error = str(exc)[:200]
            console.log(f"[red]Moonshine failed to load: {self._load_error}[/]")
            return False

    def _open_mic(self):
        """Build a fresh MicTranscriber around the already-loaded model."""
        from moonshine_voice import MicTranscriber
        mic = (
            MicTranscriber()
            .use_transcriber(self._transcriber)
            .update_interval(self.update_interval)
            .device(self.device)
            .on_text(self._on_text)
            .on_line(self._on_line)
            .on_error(self._on_error)
        )
        mic.load()
        mic.mute(True)  # gate closed until listen() is ready
        return mic

    def _close_mic(self) -> None:
        """Fully release the microphone (stream + worker). The model stays loaded."""
        mic, self._mic = self._mic, None
        if mic is None:
            return
        for step in (lambda: mic.mute(True), mic.stop, mic.close):
            try:
                step()
            except Exception:
                pass

    def close(self) -> None:
        """Release the microphone AND free the model."""
        self._close_mic()
        if self._transcriber is not None:
            try:
                self._transcriber.close()
            except Exception:
                pass
            self._transcriber = None

    # -- callbacks (run on Moonshine's threads) -------------------------------
    def _on_text(self, text: str) -> None:
        self._partial = text or ""

    def _on_line(self, line) -> None:
        text = (getattr(line, "text", "") or "").strip()
        if len(text) > 1:
            self._lines.put(text)

    def _on_error(self, exc: BaseException) -> None:
        console.log(f"[yellow]Moonshine error: {exc}[/]")

    # -- self-hearing protection ---------------------------------------------
    def mute(self, muted: bool = True) -> None:
        self._muted = muted
        if self._mic is not None:
            self._mic.mute(muted)

    def set_language(self, lang: str) -> bool:
        """Switch language (e.g. 'en' <-> 'ar'). Reloads the model. Returns success."""
        try:
            from moonshine_voice import supported_languages
            if lang not in supported_languages():
                return False
        except Exception:
            return False
        if lang == self.lang and self._mic is not None:
            return True
        self.close()
        self.lang = lang
        self.available = True
        return self._ensure_loaded()

    # -- main entry point -----------------------------------------------------
    def listen(self, timeout: Optional[float] = None, phrase_limit: Optional[int] = None) -> str:
        """Listen until Moonshine finishes one utterance (i.e. you stop talking).

        No word limit and no short time limit: `timeout` and `phrase_limit` are kept
        only so old callers don't break, and both are ignored. The only cap is
        STT_SAFETY_TIMEOUT (default 120 s) so a stuck mic can never hang forever.

        Falls back to keyboard input if Moonshine cannot load or the mic fails.
        """
        if not self._ensure_loaded():
            console.print("[yellow]No Moonshine model - typing instead[/]")
            return Prompt.ask("[bold]Type command[/]")

        # Drop anything left over from a previous call
        while not self._lines.empty():
            try:
                self._lines.get_nowait()
            except queue.Empty:
                break
        self._partial = ""

        try:
            self._mic = self._open_mic()
            self._mic.start()
            self._mic.mute(self._muted)  # honours an active mute()
            console.print(Panel(
                f"[cyan]Listening (Moonshine/{self.lang})…[/] (say 'sleep' to return to standby)",
                style="dim",
            ))
            deadline = time.time() + STT_SAFETY_TIMEOUT
            result = ""
            while time.time() < deadline:
                try:
                    result = self._lines.get(timeout=0.1)
                    break
                except queue.Empty:
                    continue
            if not result and self._partial.strip():
                result = self._partial.strip()  # safety net hit mid-sentence: use what we have
            if result:
                console.print(f"[green]Heard:[/] {result}")
            return result.strip()
        except Exception as exc:
            console.print(f"[red]STT error: {exc}[/]")
            self._close_mic()
            return Prompt.ask("[bold]Type command[/]")
        finally:
            self._close_mic()  # free the audio device between utterances


def _input_device_name(device_index):
    """Human-readable name for a device index (or 'auto' / 'none')."""
    if device_index is None:
        return "auto (default)"
    try:
        name = sd.query_devices()[device_index].get("name", "?")
        return f"{device_index} - {name}"
    except Exception:
        return str(device_index)


# Auto-detect first input-capable audio device (device indices shift between runs)
def _find_input_device():
    """Return the index of the first device with microphone input, or None.

    Tests each candidate by actually opening a stream at 16 kHz — some devices
    (e.g. raw ALSA hardware) reject sample rates the mic doesn't natively support.

    AUDIO_DEVICE overrides auto-detection: an integer is used as a device index,
    a non-integer is matched as a case-insensitive substring of the device name.
    """
    env_dev = os.getenv("AUDIO_DEVICE")
    if env_dev:
        env_dev = env_dev.strip()
        if env_dev.lower() in ("default", "sysdefault", "hw:0"):
            return None
        if env_dev.isdigit():
            return int(env_dev)
        # Name substring match
        try:
            for i, d in enumerate(sd.query_devices()):
                if env_dev.lower() in d.get("name", "").lower():
                    return i
        except Exception:
            pass
        return None

    # Probe each candidate; ALSA prints C-level errors to stderr when a device
    # rejects the sample rate, so silence stderr only while probing.
    try:
        devices = sd.query_devices()
    except Exception:
        return None

    for i, d in enumerate(devices):
        if d["max_input_channels"] <= 0:
            continue
        try:
            stderr_fd = os.dup(2)
            devnull_fd = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull_fd, 2)
            try:
                stream = sd.RawInputStream(
                    samplerate=16000, blocksize=8000, dtype="int16",
                    channels=1, device=i,
                )
                stream.close()
                return i
            finally:
                os.dup2(stderr_fd, 2)
                os.close(devnull_fd)
                os.close(stderr_fd)
        except Exception:
            continue
    return None


# ----------------------------------------------------------------------
# Text‑to‑Speech (assistant → audio) - Dexter Style
# ----------------------------------------------------------------------
class TTS:
    """Primary Edge‑TTS (async) with gTTS+pygame fallback. Dexter voice style."""

    def __init__(self):
        pygame.mixer.init()
        self.voice = DEXTER_VOICE
        self.speed = current_speed
        self.pitch = DEXTER_PITCH
        self.speaking = False  # True while audio is playing

    def set_speed(self, speed: str) -> None:
        """Update voice speed (e.g., '+0%', '-20%', '+50%')."""
        self.speed = speed
        global current_speed
        current_speed = speed

    def set_voice(self, voice: str) -> None:
        """Change voice model."""
        self.voice = voice

    async def _edge_speak(self, text: str) -> None:
        """Stream audio via edge-tts and play through pygame."""
        self.speaking = True
        try:
            communicate = edge_tts.Communicate(
                text, voice=self.voice, rate=self.speed, pitch=self.pitch
            )

            # Save to temp file then play
            tmp_path = Path("tmp_dexter.mp3")
            await communicate.save(str(tmp_path))

            pygame.mixer.music.load(str(tmp_path))
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                await asyncio.sleep(0.1)
            tmp_path.unlink(missing_ok=True)

        except Exception as exc:
            console.log(f"[yellow]Edge-TTS failed ({exc}); using fallback[/]")
            self._fallback_speak(text)
        finally:
            self.speaking = False
            # Small delay to let audio settle
            await asyncio.sleep(0.3)

    def _fallback_speak(self, text: str) -> None:
        """Synchronous gTTS -> pygame playback."""
        self.speaking = True
        try:
            tts = gTTS(text=text, lang="en", slow=True)  # slow=True for Dexter style
            tmp_path = Path("tmp_dexter_fallback.mp3")
            tts.save(tmp_path)
            pygame.mixer.music.load(str(tmp_path))
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                pygame.time.wait(100)
            tmp_path.unlink(missing_ok=True)
        except Exception as exc:
            console.log(f"[red]Both TTS backends failed: {exc}[/]")
        finally:
            self.speaking = False
            time.sleep(0.3)

    def speak(self, text: str) -> None:
        """Public wrapper - runs async Edge-TTS.

        The STT is muted for the whole utterance so Shanex never transcribes
        its own voice.
        """
        stt_ref = stt  # module-level singleton; None in --text mode
        if stt_ref is not None and hasattr(stt_ref, "mute"):
            stt_ref.mute(True)
        try:
            asyncio.run(self._edge_speak(text))
        except RuntimeError:
            pass
        finally:
            if stt_ref is not None and hasattr(stt_ref, "mute"):
                stt_ref.mute(False)


tts = TTS()

# ----------------------------------------------------------------------
# Screen Control Module
# ----------------------------------------------------------------------
class ScreenControl:
    """Screen capture, OCR, and input automation."""

    def __init__(self):
        self.available = SCREEN_CONTROL_AVAILABLE
        if not self.available:
            console.log("[yellow]Screen control unavailable - install pyautogui, pytesseract, pillow[/]")

    def screenshot(self, region: Optional[Tuple[int, int, int, int]] = None):
        """Take a screenshot."""
        if not self.available:
            return None
        try:
            return ImageGrab.grab(bbox=region)
        except Exception as e:
            console.log(f"[red]Screenshot failed: {e}[/]")
            return None

    def read_screen(self, region: Optional[Tuple[int, int, int, int]] = None) -> str:
        """OCR the screen or region."""
        if not self.available:
            return "Screen control not available. Install: pip install pyautogui pytesseract pillow"
        try:
            img = self.screenshot(region)
            if img:
                text = pytesseract.image_to_string(img)
                return text.strip() if text.strip() else "No text detected on screen."
            return "Failed to capture screen."
        except Exception as e:
            return f"OCR error: {e}"

    def write_text(self, text: str, interval: float = 0.02) -> str:
        """Type text at current cursor position."""
        if not self.available:
            return "Screen control not available."
        try:
            pyautogui.write(text, interval=interval)
            return f"Typed: {text[:50]}{'...' if len(text) > 50 else ''}"
        except Exception as e:
            return f"Type error: {e}"

    def click(self, x: int, y: int, button: str = 'left') -> str:
        """Click at coordinates."""
        if not self.available:
            return "Screen control not available."
        try:
            pyautogui.click(x, y, button=button)
            return f"Clicked at ({x}, {y})"
        except Exception as e:
            return f"Click error: {e}"

    def get_mouse_position(self) -> Tuple[int, int]:
        """Get current mouse position."""
        if not self.available:
            return (0, 0)
        return pyautogui.position()

    def scroll(self, amount: int) -> str:
        """Scroll (positive = up)."""
        if not self.available:
            return "Screen control not available."
        try:
            pyautogui.scroll(amount)
            return f"Scrolled {'up' if amount > 0 else 'down'} {abs(amount)} units"
        except Exception as e:
            return f"Scroll error: {e}"

    def press_key(self, key: str) -> str:
        """Press a key (e.g., 'enter', 'tab', 'ctrl+c')."""
        if not self.available:
            return "Screen control not available."
        try:
            pyautogui.press(key)
            return f"Pressed {key}"
        except Exception as e:
            return f"Key press error: {e}"

    def hotkey(self, *keys: str) -> str:
        """Press key combination (e.g., 'ctrl', 'c')."""
        if not self.available:
            return "Screen control not available."
        try:
            pyautogui.hotkey(*keys)
            return f"Hotkey: {'+'.join(keys)}"
        except Exception as e:
            return f"Hotkey error: {e}"


# ----------------------------------------------------------------------
# Ollama LLM wrapper (local or cloud)
# ----------------------------------------------------------------------
class OllamaLLM:
    """Wrapper around the official ollama python client with same interface as before."""

    def __init__(self):
        self.is_cloud = OLLAMA_HOST.rstrip("/") == "https://ollama.com"
        self.model_name = OLLAMA_MODEL
        self.system_prompt = SYSTEM_PROMPT
        self._retried_5xx = False  # Track if we've retried on 5xx/429
        self.client = None
        self._cloud_key_missing = False

        # Ollama Cloud requires an API key. Keep this import-safe: remember the
        # problem and surface it on the first real ask(), never echo the key.
        if self.is_cloud and not OLLAMA_API_KEY:
            self._cloud_key_missing = True
            return

        headers = {}
        if self.is_cloud:
            headers["Authorization"] = f"Bearer {OLLAMA_API_KEY}"

        # Long timeout for CPU inference (60s should be enough for llama3.2:1b)
        self.client = ollama.Client(host=OLLAMA_HOST, headers=headers or None, timeout=60.0)

    def _build_messages(self, user_msg: str) -> List[Dict[str, str]]:
        """Build the message list for Ollama chat API.

        Reads recent history fresh on every call. The main loop adds the
        user turn to memory BEFORE calling ask(), so we exclude the last
        entry if it matches the current user message to avoid duplication.
        """
        recent = memory.recent(HISTORY_TURNS)
        # Exclude last entry if it's the current user message (already added by caller)
        if recent and recent[-1].get("role") == "user" and recent[-1].get("content") == user_msg:
            recent = recent[:-1]

        messages = [{"role": "system", "content": self.system_prompt}]
        for msg in recent:
            messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": user_msg})
        return messages

    def ask(self, user_msg: str) -> str:
        """Send a message to Ollama with proper error handling."""
        # Lazy-init / guard checks
        if self._cloud_key_missing:
            raise RuntimeError("OLLAMA_API_KEY required for https://ollama.com")
        if self.client is None:
            raise RuntimeError("Ollama client not initialized")

        messages = self._build_messages(user_msg)

        # Local host: keep the model warm and limit context to save RAM.
        # Cloud host: never send num_ctx/keep_alive (not supported, burns quota).
        if self.is_cloud:
            kwargs = {"model": self.model_name, "messages": messages}
        else:
            options = {
                "num_ctx": OLLAMA_CONTEXT,  # Reduced context to save RAM
                "num_predict": 200,
                "temperature": 0.7,
            }
            kwargs = {
                "model": self.model_name,
                "messages": messages,
                "options": options,
                "keep_alive": "10m",
            }

        try:
            response = self.client.chat(**kwargs)
            self._retried_5xx = False  # Reset on success
            content = response["message"]["content"].strip()
            if not content:
                return "I have nothing to say on that, Sir."
            return content

        except ResponseError as e:
            status_code = getattr(e, 'status_code', None)
            err_msg = str(e).lower()

            # 404 - Model not found
            if status_code == 404 or "not found" in err_msg:
                console.print(f"[red]Model '{self.model_name}' not found.[/]")
                if self.is_cloud:
                    self._list_cloud_models()
                    raise RuntimeError(
                        f"Model '{self.model_name}' not found on Ollama Cloud. "
                        "Use one of the models listed above."
                    )
                console.print(f"[yellow]Run: ollama pull {self.model_name}[/]")
                raise RuntimeError(f"Model '{self.model_name}' not found. Run: ollama pull {self.model_name}")

            # 429 - Free usage limit reached. Never retry (burns quota).
            if status_code == 429:
                console.print("[red]Ollama Cloud free usage limit reached, Sir.[/]")
                raise RuntimeError("Rate limited (429) - free usage limit reached")

            # 5xx - Server error (local only; cloud 429 handled above)
            if status_code and 500 <= status_code < 600:
                if not self._retried_5xx:
                    self._retried_5xx = True
                    console.log("[yellow]Server busy, retrying in 2s...[/]")
                    time.sleep(2)
                    return self.ask(user_msg)  # Single retry
                console.print("[red]Server error. Try again later, Sir.[/]")
                raise RuntimeError("Server error (rate limit or 5xx)")

            # 401/403 - Auth (only relevant for Ollama Cloud)
            if status_code in (401, 403) or "unauthorized" in err_msg or "forbidden" in err_msg:
                console.print("[red]Invalid API key for Ollama Cloud.[/]")
                raise RuntimeError("Authentication failed - check OLLAMA_API_KEY")

            # Other ResponseError
            raise

        except ConnectionError:
            # Built-in ConnectionError from ollama when it can't reach the host
            console.print("[red]Cannot connect to Ollama.[/]")
            console.print("[yellow]Start it with: ollama serve[/]")
            raise RuntimeError("Connection refused - is 'ollama serve' running?")

        except Exception as e:
            # Catch-all for other connection issues (DNS, timeout, etc.)
            err_str = str(e).lower()
            if any(kw in err_str for kw in ["connection", "timeout", "dns", "network", "unreachable", "refused"]):
                console.print("[red]Cannot connect to Ollama.[/]")
                console.print("[yellow]Start it with: ollama serve[/]")
                raise RuntimeError("Connection failed - is 'ollama serve' running?")
            raise

    def _list_cloud_models(self) -> None:
        """Print the models available on Ollama Cloud (no API key printed)."""
        try:
            resp = self.client.list()
            names = [m.get("model") or m.get("name") for m in resp.get("models", [])]
            names = [n for n in names if n]
            if names:
                console.print("[yellow]Available Ollama Cloud models:[/]")
                for n in names:
                    console.print(f"[dim]  - {n}[/]")
            else:
                console.print("[yellow]No models listed by Ollama Cloud.[/]")
        except Exception:
            console.print("[yellow]Could not list Ollama Cloud models.[/]")

    def close(self) -> None:
        """Close the underlying HTTP client."""
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass

# ----------------------------------------------------------------------
# Privacy: keep long command/screen/error output out of the memory file
# ----------------------------------------------------------------------
_SENSITIVE_PREFIXES = (
    "Command output", "Command failed", "Error executing", "OCR error",
    "Type error", "Click error", "Scroll error", "Key press error",
    "Hotkey error", "Screenshot failed", "Screen control not available",
    "Failed to capture screen", "No text detected",
)


def sanitize_for_memory(text: str) -> str:
    """Replace command/screen output and error strings with a short placeholder.

    The memory file is a plain JSON transcript; it must never hold raw
    command output, OCR text or exception strings.
    """
    if not text:
        return text
    stripped = text.lstrip()
    if any(stripped.startswith(p) for p in _SENSITIVE_PREFIXES):
        return "[output omitted]"
    # Long multiline text is likely command/screen output - sanitize it
    if "\n" in text and len(text) > 120:
        return "[output omitted]"
    return text


# ----------------------------------------------------------------------
# Command interpreter (enhanced with screen control, speed, etc.)
# ----------------------------------------------------------------------
def handle_command(text: str) -> Optional[str]:
    """Detect special commands; return a string response if handled."""
    lowered = text.lower()  # not stripped - prefix checks need trailing space
    original = text.strip()  # stripped - for content extraction after prefix

    # 1. Voice speed control
    if lowered.startswith("speed "):
        speed_val = lowered[6:].strip()
        if not speed_val.endswith("%"):
            speed_val += "%"
        if speed_val.startswith("+") or speed_val.startswith("-") or speed_val == "0%":
            tts.set_speed(speed_val)
            return f"Voice speed set to {speed_val}, Sir."
        return "Speed format: +20%, -15%, 0%"

    if lowered in {"voice faster", "speed up"}:
        tts.set_speed("+20%")
        return "Accelerated. Sir."

    if lowered in {"voice slower", "slow down"}:
        tts.set_speed("-30%")
        return "Deliberate. Sir."

    if lowered in {"voice normal", "reset voice"}:
        tts.set_speed(DEXTER_SPEED)
        tts.set_voice(DEXTER_VOICE)
        return "Baseline restored, Sir."

    # 2. Screen control commands
    if (lowered.startswith("screen read") or lowered.startswith("read screen") or
        lowered.startswith("screen ocr") or lowered.startswith("ocr ")):
        # Optional region: screen read x y w h
        parts = lowered.split()
        if len(parts) >= 5:
            try:
                x, y, w, h = map(int, parts[2:6])
                return screen.read_screen((x, y, w, h))
            except ValueError:
                pass
        return screen.read_screen()

    # Helper: ask y/N confirmation for destructive screen actions
    def _confirm(action: str) -> bool:
        console.print(f"[yellow]{action}[/]")
        try:
            confirm = Prompt.ask("[bold]Confirm? [y/N][/]", default="n").strip().lower()
        except EOFError:
            return False  # no stdin -> treat as declined
        return confirm in ("y", "yes")

    if lowered.startswith("screen write ") or lowered.startswith("type "):
        if lowered.startswith("screen write "):
            content = original[13:]
        else:
            content = original[5:]
        if not _confirm(f"Type text: {content[:60]}{'...' if len(content) > 60 else ''}"):
            return "Cancelled, Sir."
        return screen.write_text(content)

    if lowered.startswith("screen click ") or lowered.startswith("click "):
        parts = lowered.split()
        if len(parts) >= 3:
            try:
                x, y = int(parts[-2]), int(parts[-1])
                if not _confirm(f"Click at ({x}, {y})"):
                    return "Cancelled, Sir."
                return screen.click(x, y)
            except ValueError:
                pass
        # Use current mouse position
        x, y = screen.get_mouse_position()
        if not _confirm(f"Click at current mouse position ({x}, {y})"):
            return "Cancelled, Sir."
        return screen.click(x, y)

    if lowered.startswith("scroll "):
        try:
            amount = int(lowered.split()[1])
            if not _confirm(f"Scroll {'up' if amount > 0 else 'down'} {abs(amount)} units"):
                return "Cancelled, Sir."
            return screen.scroll(amount)
        except (IndexError, ValueError):
            return "Usage: scroll <amount> (positive = up)"

    if lowered.startswith("press "):
        key = lowered[6:].strip()
        if not _confirm(f"Press key: {key}"):
            return "Cancelled, Sir."
        return screen.press_key(key)

    if lowered.startswith("hotkey "):
        keys = lowered[7:].split()
        if not _confirm(f"Hotkey: {'+'.join(keys)}"):
            return "Cancelled, Sir."
        return screen.hotkey(*keys)

    if lowered == "mouse position" or lowered == "where is mouse":
        x, y = screen.get_mouse_position()
        return f"Mouse at ({x}, {y})"

    # 3. Open URL / search
    if lowered.startswith("open "):
        # Use the ORIGINAL text so case and spacing are preserved.
        target = original[5:].strip()
        if not target:
            return "Usage: open <url or search term>"

        # An IP address or domain (no spaces, contains a dot) opens as http://
        # so it lands on the site directly instead of a Google search.
        is_url = (
            target.startswith("http://") or target.startswith("https://")
            or (("." in target) and (" " not in target))
        )
        if is_url:
            if not (target.startswith("http://") or target.startswith("https://")):
                target = f"http://{target}"
        else:
            target = f"https://www.google.com/search?q={target.replace(' ', '+')}"

        webbrowser.open(target)
        return f"Opening {target}"

    # 4. Run shell command
    if lowered.startswith("run "):
        # Use original text to preserve case, split safely without shell
        cmd = original[4:].strip()
        if not cmd:
            return "Usage: run <command>"

        # Block dangerous commands
        try:
            parts = shlex.split(cmd)
        except ValueError as e:
            return f"Invalid command syntax: {e}"
        if not parts:
            return "Empty command"

        # Dangerous command patterns (first token or anywhere in args)
        dangerous = {"sudo", "rm", "dd", "mkfs"}
        first = parts[0].lower()
        # Match exact names or prefixed forms like mkfs.ext4, rm.rmdir
        if first == "rm" and any(a in ("-r", "-rf", "-fr") for a in parts):
            return "Blocked: recursive rm is not allowed."
        if any(first == d or first.startswith(d + ".") for d in dangerous):
            return f"Blocked: '{first}' is not allowed."

        # Show command and require confirmation
        console.print(f"[yellow]Run command:[/] {' '.join(shlex.quote(p) for p in parts)}")
        confirm = Prompt.ask("[bold]Confirm? [y/N][/]", default="n").strip().lower()
        if confirm not in ("y", "yes"):
            return "Command cancelled, Sir."

        try:
            result = subprocess.check_output(
                parts, shell=False, stderr=subprocess.STDOUT, text=True, timeout=30
            )
            return f"Command output:\n{result}"
        except subprocess.CalledProcessError as e:
            return f"Command failed (code {e.returncode}):\n{e.output}"
        except Exception as e:
            return f"Error executing command: {e}"

    # 5. Sleep / standby
    if lowered in {"sleep", "standby", "wait", "pause"}:
        return "SLEEP_MODE"

    # 6. Exit / quit
    if lowered in {"exit", "quit", "stop", "bye", "terminate"}:
        console.print("[bold red]Terminating, Sir. The darkness remains.[/]")
        sys.exit(0)

    # 7. Voice settings
    if lowered == "voice settings" or lowered == "show voice":
        return f"Voice: {tts.voice}\nSpeed: {tts.speed}\nPitch: {tts.pitch}"

    # No special handling - let Ollama answer
    return None


# ----------------------------------------------------------------------
# Main interaction loop with wake/sleep modes
# ----------------------------------------------------------------------
class JarvisController:
    """Main controller with wake/sleep state machine."""

    def __init__(self):
        # Voice mode starts in standby and waits for "wake up".
        # Text mode (no STT) is always awake.
        self.awake = stt is None

    def wake_up(self):
        """Called when the wake phrase is heard.

        The greeting is spoken BEFORE we set awake=True, so the state machine
        never considers Shanex awake while the speaker is still busy.
        """
        if self.awake:
            return
        console.print(Panel("[bold green]▶ SHANEX ONLINE[/]", style="green"))
        tts.speak(WAKE_RESPONSE)
        self.awake = True

    def sleep(self):
        """Put Shanex to sleep."""
        if self.awake:
            self.awake = False
            console.print(Panel("[bold yellow]⏸ SHANEX STANDBY[/]", style="yellow"))
            tts.speak("Standing by, Sir. Say wake up.")

    def run(self):
        """Main loop."""
        console.clear()

        # Show startup banner
        banner = Table.grid(padding=1)
        banner.add_column(style="bold magenta", justify="center")
        banner.add_row("╔══════════════════════════════════════╗")
        banner.add_row(f"║{'SHANEX':^38}║")
        banner.add_row("║     Voice: en-GB-RyanNeural          ║")
        if stt:
            banner.add_row(f"║     Mic: {_input_device_name(stt.device):<28}║")
        else:
            banner.add_row("║     Mode: Text (--text)              ║")
        banner.add_row("║     Speed: -15% | Pitch: -10Hz       ║")
        if stt:
            banner.add_row(f"║{'     Wake: "wake up"':<38}║")
        if SCREEN_CONTROL_AVAILABLE:
            banner.add_row("║     Screen: OCR + Input Control      ║")
        banner.add_row(f"║     Model: {OLLAMA_MODEL:<26}║")
        banner.add_row("╚══════════════════════════════════════╝")
        console.print(Panel(banner, expand=False))
        console.print(personality_status_line())

        # Warm up the local Ollama model so the first real response is fast.
        # Skipped on Ollama Cloud - a warm-up chat burns free quota.
        if not ARGS.offline and not llm.is_cloud:
            try:
                with console.status("[bold green]Warming up model...[/]", spinner="dots"):
                    llm.ask("ping")
            except Exception:
                pass  # Warm-up is best-effort; real errors handled at runtime

        if stt is not None:
            self.awake = False
            console.print('[dim]Say "wake up" to activate...[/dim]\n')
        else:
            self.awake = True
            console.print("[dim]Text mode. Type 'sleep' for standby.[/dim]\n")

        try:
            while True:
                if not self.awake:
                    if stt is None:
                        self.awake = True  # text mode is always awake
                    else:
                        # Standby: listen for the wake phrase only; everything else is ignored.
                        while tts.speaking:
                            time.sleep(0.1)
                        heard = stt.listen().strip()
                        if heard and is_wake_phrase(heard):
                            self.wake_up()
                        continue

                # 1. Capture user input (voice -> text)
                if stt is None:
                    # Text mode: read from stdin
                    try:
                        user_input = Prompt.ask("[bold cyan]You[/]").strip()
                    except EOFError:
                        break
                else:
                    # Voice mode
                    # Wait until the previous TTS phrase has fully finished before
                    # opening the mic, otherwise we start listening mid-speech.
                    while tts.speaking:
                        time.sleep(0.1)
                    time.sleep(0.6)
                    user_input = stt.listen().strip()
                    if not user_input:
                        continue

                # 2. Store user turn
                memory.add("user", user_input)

                # 3. Check for sleep command
                if user_input.lower().strip() in {"sleep", "standby", "wait"}:
                    self.sleep()
                    memory.add("assistant", "Standing by, Sir.")
                    memory.dump()
                    continue

                # 4. Check for built-in commands
                command_reply = handle_command(user_input)
                if command_reply:
                    if command_reply == "SLEEP_MODE":
                        self.sleep()
                        memory.add("assistant", "Standing by, Sir.")
                        memory.dump()
                        continue

                    console.print(Panel(command_reply, title="[cyan]SHANEX (command)[/]", subtitle="[OK]"))
                    tts.speak(command_reply)
                    memory.add("assistant", sanitize_for_memory(command_reply))
                    memory.dump()
                    continue

                # 5. Ask Ollama for a response
                with console.status("[bold green]Processing...[/]", spinner="dots"):
                    try:
                        response = llm.ask(user_input)
                    except Exception as exc:
                        console.log(f"[red]Ollama error: {exc}[/]")
                        response = "I cannot reach the model right now, Sir."

                # 6. Show + speak the answer
                console.print(Panel(response, title="[cyan]SHANEX[/]", subtitle="[IDEA]"))
                tts.speak(response)

                # 7. Persist turn (sanitized)
                memory.add("assistant", sanitize_for_memory(response))
                memory.dump()

        except KeyboardInterrupt:
            console.print("\n[bold red]Terminated, Sir.[/]")
        finally:
            if stt is not None:
                stt.close()


# ----------------------------------------------------------------------
# Argument parsing & late initialization
# ----------------------------------------------------------------------
def parse_args():
    import argparse
    ap = argparse.ArgumentParser(
        description="Shanex – voice assistant",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--text", action="store_true",
                    help="Keyboard input instead of microphone (no Moonshine/sounddevice needed)")
    ap.add_argument("--selftest", action="store_true",
                    help="Run self-check suite and exit (no mic, no network)")
    ap.add_argument("--offline", action="store_true",
                    help="Skip all network calls (Ollama, edge-tts)")
    ap.add_argument("--debug", action="store_true",
                    help="Debug flag (reserved - currently has no effect)")
    return ap.parse_args()


def _init_runtime(args):
    """Lazy-initialize all singletons so import stays safe."""
    global stt, tts, screen, llm, ARGS
    ARGS = args

    if args.text:
        # No STT in text mode
        stt = None
    else:
        import sounddevice as sd
        stt = MoonshineSTT(lang=STT_LANGUAGE, device=_find_input_device())

    tts = TTS()
    screen = ScreenControl()

    # Ollama client (cloud-key check is lazy inside OllamaLLM)
    llm = OllamaLLM()


def main():
    args = parse_args()
    _init_runtime(args)

    if args.selftest:
        return run_selftest(args)

    controller = JarvisController()
    controller.run()


# ----------------------------------------------------------------------
# Self-test suite (run with --selftest)
# ----------------------------------------------------------------------
def run_selftest(args):
    """Run a quick environment self-check and print PASS/FAIL/SKIP table."""
    import shutil
    import tempfile
    import subprocess as sp
    from rich.table import Table

    console.clear()
    console.print(Panel("[bold magenta]SHANEX SELF-TEST[/]", expand=False))

    results = []

    def add(name, ok, detail=""):
        status = "[green]PASS[/]" if ok else "[red]FAIL[/]" if detail else "[yellow]SKIP[/]"
        results.append((name, status, detail))
        console.print(f"  {status}  {name}" + (f"  ({detail})" if detail else ""))

    # 1. Python / venv
    add("Python 3.12+", sys.version_info >= (3, 12))

    # 2. Imports
    try:
        import moonshine_voice, sounddevice, numpy, ollama, edge_tts, gtts, pygame
        import pyautogui, pytesseract, PIL, rich, dotenv
        add("Imports", True)
    except ImportError as e:
        add("Imports", False, str(e))

    # 3. Moonshine model loads AND actually transcribes a known clip
    try:
        import moonshine_voice as mv
        from moonshine_voice import Transcriber, get_model_for_language
        model_path, arch = get_model_for_language("en", getattr(mv.ModelArch, MOONSHINE_ARCH.upper(), mv.ModelArch.TINY_STREAMING))
        tr = Transcriber(str(model_path), arch)
        try:
            wav = Path(mv.get_assets_path()) / "beckett.wav"
            audio, sr = mv.load_wav_file(str(wav))
            out = tr.transcribe_without_streaming(audio, sr)
            heard = " ".join(l.text for l in out.lines).lower()
            ok = len(heard.split()) >= 3
            add("Moonshine transcription", ok, f"{arch.name}: '{heard[:40]}...'" if ok else "empty transcript")
        finally:
            tr.close()
    except Exception as e:
        add("Moonshine transcription", False, str(e)[:80])

    # 4. Input device
    dev_idx = _find_input_device()
    if dev_idx is not None:
        try:
            import sounddevice as sd
            with sd.RawInputStream(samplerate=16000, blocksize=8000, dtype="int16",
                                   channels=1, device=dev_idx):
                pass
            add("Input device", True, f"index {dev_idx}")
        except Exception as e:
            add("Input device", False, str(e))
    else:
        add("Input device", False, "none found")

    # 5. Output device
    try:
        import sounddevice as sd
        out_idx = next((i for i, d in enumerate(sd.query_devices())
                        if d["max_output_channels"] > 0), None)
        add("Output device", out_idx is not None, f"index {out_idx}" if out_idx else "none")
    except Exception:
        add("Output device", False, "query failed")

    # 6. TTS synthesis to temp file (edge-tts then gTTS fallback)
    tts_ok = False
    if not args.offline:
        try:
            import asyncio
            tmp = Path(tempfile.gettempdir()) / "jarvis_selftest.mp3"
            async def _t():
                await edge_tts.Communicate("test", voice=DEXTER_VOICE).save(str(tmp))
            asyncio.run(_t())
            tts_ok = tmp.is_file() and tmp.stat().st_size > 0
            tmp.unlink(missing_ok=True)
        except Exception:
            tts_ok = False
    add("TTS synthesis", tts_ok, "edge-tts" if tts_ok else "skipped/offline")

    # 7. Ollama reachability + one chat (skipped with --offline)
    if not args.offline:
        try:
            resp = llm.ask("ping")
            ollama_ok = bool(resp)
        except Exception as e:
            ollama_ok = False
            detail = str(e)[:80]
        add("Ollama reachable + chat", ollama_ok, detail if not ollama_ok else "ok")
    else:
        add("Ollama reachable + chat", None, "skipped (--offline)")

    # 8. tesseract binary
    add("tesseract", shutil.which("tesseract") is not None)

    # 9. pyautogui import (dry-run only)
    try:
        import pyautogui
        pyautogui.FAILSAFE = True
        add("pyautogui", True, "dry-run")
    except Exception as e:
        add("pyautogui", False, str(e))

    # 10. Memory read/write
    try:
        mem = Memory(SCRIPT_DIR / "jarvis_selftest_mem.json")
        mem.add("user", "test")
        mem.add("assistant", "ok")
        mem.dump()
        mem2 = Memory(SCRIPT_DIR / "jarvis_selftest_mem.json")
        ok = len(mem2.history) == 2
        (SCRIPT_DIR / "jarvis_selftest_mem.json").unlink(missing_ok=True)
        add("Memory read/write", ok)
    except Exception as e:
        add("Memory read/write", False, str(e))

    # Summary
    fails = [r for r in results if "FAIL" in r[1]]
    console.print()
    console.print(Panel(
        f"[bold]{'ALL CHECKS PASSED' if not fails else f'{len(fails)} FAILURE(S)'}[/]",
        style="green" if not fails else "red",
        expand=False
    ))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())