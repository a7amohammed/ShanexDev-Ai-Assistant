#!/usr/bin/env python3
"""
JARVIS – a voice‑first CLI AI assistant (no GUI)

Features
--------
* Ollama LLM (official ollama python client) with Dexter Morgan persona
* Speech‑to‑text via `speech_recognition` (mic → text)
* Text‑to‑speech:
    - Primary: `edge-tts` (Microsoft neural voice, Dexter style)
    - Fallback: `gtts` + `pygame` mixer
* Persistent conversation memory (`jarvis_memory.json`)
* Rich‑styled CLI (panels, spinners, log colours)
* System control:
    - `open <url>` → webbrowser
    - `run <shell‑cmd>` → subprocess
    - `screen read` → OCR screenshot
    - `screen write <text>` → type text
    - `screen click <x> <y>` → click coordinates
    - `speed <value>` → set voice speed
* Double-clap wake detection
* Voice settings: Dexter Morgan style (dark, measured, slightly slower)

All code lives in this single file; just install the dependencies from
`requirements.txt` and run:

    $ python3 main.py
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

import speech_recognition as sr
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
except ImportError:
    SCREEN_CONTROL_AVAILABLE = False

# Audio analysis for clap detection
try:
    import sounddevice as sd
    CLAP_DETECTION_AVAILABLE = True
except ImportError:
    CLAP_DETECTION_AVAILABLE = False

# ------------------------------------------------------------
# Initialise helpers
# ------------------------------------------------------------
install_rich_traceback()               # pretty tracebacks
console = Console()
load_dotenv()                           # loads OLLAMA_API_KEY from .env (if present)

# ----------------------------------------------------------------------
# Configuration & constants
# ----------------------------------------------------------------------
MEMORY_PATH = Path("jarvis_memory.json")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:1b")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY")  # Optional: only used for https://ollama.com
# Reduce context size to lower RAM usage (default 1024 tokens)
OLLAMA_CONTEXT = int(os.getenv("OLLAMA_CONTEXT", "1024"))

# Dexter Morgan persona - dark, measured, precise
SYSTEM_PROMPT = (
    "You are Dexter Morgan's inner voice - an AI assistant with his precise, "
    "measured, slightly dark demeanor. You speak with clinical efficiency, "
    "moral ambiguity, and a hint of the Dark Passenger. Address the user as "
    "'Sir' or by name. Keep responses concise, direct, and occasionally "
    "philosophical about the nature of control and chaos. Never use exclamation "
    "points. Speak in low, even tones. You are not friendly - you are useful."
)

# Dexter-style voice settings (using edge-tts voices that sound darker)
DEXTER_VOICE = "en-GB-RyanNeural"  # Deep British male - closest to Dexter
DEXTER_SPEED = "-15%"              # Slower, more deliberate
DEXTER_PITCH = "-10Hz"             # Lower pitch (edge-tts uses Hz)

# Voice speed control (user adjustable)
current_speed = DEXTER_SPEED

# Clap detection settings
CLAP_THRESHOLD = 0.35          # RMS threshold for clap detection (higher = less sensitive)
CLAP_TIME_WINDOW = 0.6         # Max time between two claps (seconds)
CLAP_COOLDOWN = 2.0            # Cooldown after detection (seconds)

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

    def _load(self) -> List[Dict[str, Any]]:
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
            except Exception:
                console.log("[red]Failed to read memory, starting fresh[/]")
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

    def recent(self, n: int = 10) -> List[Dict[str, Any]]:
        return self.history[-n:]


memory = Memory(MEMORY_PATH)

# ----------------------------------------------------------------------
# Clap Detection (runs in background thread)
# ----------------------------------------------------------------------
class ClapDetector:
    """Detects double-clap pattern to wake JARVIS."""

    def __init__(self, callback):
        self.callback = callback
        self.running = False
        self.last_clap_time = 0
        self.clap_count = 0
        self.audio_queue = queue.Queue()
        self.stream = None
        self.tts_ref = None  # Will be set after TTS is created

    def audio_callback(self, indata, frames, time_info, status):
        """Called by sounddevice for each audio block."""
        if status:
            pass
        # Calculate RMS energy
        rms = np.sqrt(np.mean(indata**2))
        self.audio_queue.put(rms)

    def process_audio(self):
        """Process audio queue for clap detection."""
        # Track if we're currently in a loud period to avoid multiple triggers
        in_clap = False
        clap_start_time = 0

        while self.running:
            try:
                rms = self.audio_queue.get(timeout=0.1)
                current_time = time.time()

                # Skip clap detection if TTS is speaking
                if self.tts_ref and self.tts_ref.speaking:
                    continue

                if rms > CLAP_THRESHOLD:
                    if not in_clap:
                        # Start of a new clap event
                        in_clap = True
                        clap_start_time = current_time

                        # Check if this is a new clap (not part of same event)
                        if current_time - self.last_clap_time > 0.25:
                            # Store previous clap time BEFORE updating, for window check
                            prev_clap_time = self.last_clap_time
                            self.clap_count += 1
                            self.last_clap_time = current_time

                            if self.clap_count == 2:
                                # Double clap detected! Check window against PREVIOUS clap time
                                if current_time - prev_clap_time < CLAP_TIME_WINDOW:
                                    console.print("[bold yellow]⚡ Double clap detected - JARVIS waking[/]")
                                    self.callback()
                                    self.clap_count = 0
                                    time.sleep(CLAP_COOLDOWN)
                                else:
                                    # Too slow, reset count
                                    self.clap_count = 1
                else:
                    # Below threshold - end of clap event
                    if in_clap and (current_time - clap_start_time) > 0.05:
                        in_clap = False
            except queue.Empty:
                continue
            except Exception:
                pass

    def start(self):
        """Start the clap detector."""
        if not CLAP_DETECTION_AVAILABLE:
            console.log("[yellow]Clap detection unavailable - sounddevice not installed[/]")
            return False

        self.running = True
        try:
            self.stream = sd.InputStream(
                callback=self.audio_callback,
                channels=1,
                samplerate=16000,
                blocksize=1024,
            )
            self.stream.start()

            # Start processing thread
            self.process_thread = threading.Thread(target=self.process_audio, daemon=True)
            self.process_thread.start()

            console.log("[green]Clap detection active - double clap to wake[/]")
            return True
        except Exception as e:
            console.log(f"[red]Failed to start clap detection: {e}[/]")
            return False

    def stop(self):
        """Stop the clap detector."""
        self.running = False
        if self.stream:
            self.stream.stop()
            self.stream.close()


# ----------------------------------------------------------------------
# Speech‑to‑Text (microphone → text)
# ----------------------------------------------------------------------
class STT:
    """Wrap `speech_recognition` with a tiny fallback to manual input.
    Microphone is initialized lazily on first use to avoid import-time crashes
    when no microphone is available.
    """

    def __init__(self):
        self.recognizer = sr.Recognizer()
        self._microphone = None
        self._adjusted = False

    def _ensure_mic(self):
        """Lazy-initialize microphone and adjust for ambient noise once."""
        if self._microphone is None:
            try:
                self._microphone = sr.Microphone()
                with self._microphone as source:
                    # Longer calibration for better threshold
                    self.recognizer.adjust_for_ambient_noise(source, duration=2.0)
                # Don't override the calibrated threshold; let dynamic_energy_threshold handle it
                self.recognizer.dynamic_energy_threshold = True
            except Exception as e:
                console.log(f"[yellow]No microphone available: {e}[/]")
                self._microphone = False  # Mark as unavailable
        return self._microphone is not False

    def listen(self, timeout: int = 10, phrase_limit: int = 15) -> str:
        """Listen for a phrase; on error or timeout, ask for typed input."""
        if not self._ensure_mic():
            return Prompt.ask("[bold]No microphone — type command[/]")

        with self._microphone as source:
            console.print(Panel("[cyan]Listening...[/] (say 'sleep' to return to standby)", style="dim"))
            try:
                audio = self.recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_limit)
            except sr.WaitTimeoutError:
                return ""

        try:
            text = self.recognizer.recognize_google(audio)
            console.print(f"[green]Heard:[/green] {text}")
            return text
        except sr.UnknownValueError:
            console.print("[red]Unintelligible.[/]")
            return ""
        except sr.RequestError as e:
            console.print(f"[red]STT service error: {e}[/]")
            return Prompt.ask("[bold]Type command[/]")


stt = STT()

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
        self.speaking = False  # Flag to prevent clap detection during speech

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
        """Public wrapper - runs async Edge-TTS."""
        try:
            asyncio.run(self._edge_speak(text))
        except RuntimeError:
            pass


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


screen = ScreenControl()

# ----------------------------------------------------------------------
# Ollama LLM wrapper (local or cloud)
# ----------------------------------------------------------------------
class OllamaLLM:
    """Wrapper around the official ollama python client with same interface as before."""

    def __init__(self):
        # Build headers only if API key is set AND host is Ollama Cloud
        headers = {}
        if OLLAMA_API_KEY and OLLAMA_HOST.rstrip("/") == "https://ollama.com":
            headers["Authorization"] = f"Bearer {OLLAMA_API_KEY}"

        # Long timeout for CPU inference (60s should be enough for llama3.2:1b)
        self.client = ollama.Client(host=OLLAMA_HOST, headers=headers or None, timeout=60.0)
        self.model_name = OLLAMA_MODEL
        self.system_prompt = SYSTEM_PROMPT
        self._retried_5xx = False  # Track if we've retried on 5xx/429

    def _build_messages(self, user_msg: str) -> List[Dict[str, str]]:
        """Build the message list for Ollama chat API.

        Reads recent history fresh on every call. The main loop adds the
        user turn to memory BEFORE calling ask(), so we exclude the last
        entry if it matches the current user message to avoid duplication.
        """
        recent = memory.recent(20)
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
        messages = self._build_messages(user_msg)
        options = {
            "num_ctx": OLLAMA_CONTEXT,  # Reduced context to save RAM
            "num_predict": 200,
            "temperature": 0.7,
        }

        try:
            response = self.client.chat(
                model=self.model_name,
                messages=messages,
                options=options,
                keep_alive="10m",
            )
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
                console.print(f"[yellow]Run: ollama pull {self.model_name}[/]")
                raise RuntimeError(f"Model '{self.model_name}' not found. Run: ollama pull {self.model_name}")

            # 429 or 5xx - Rate limit / server error
            if status_code == 429 or (status_code and 500 <= status_code < 600):
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

    def close(self) -> None:
        """Close the underlying HTTP client."""
        try:
            self.client.close()
        except Exception:
            pass


llm = OllamaLLM()

# ----------------------------------------------------------------------
# Command interpreter (enhanced with screen control, speed, etc.)
# ----------------------------------------------------------------------
def handle_command(text: str) -> Optional[str]:
    """Detect special commands; return a string response if handled."""
    lowered = text.lower().strip()
    original = text.strip()

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
    if lowered.startswith("screen read") or lowered.startswith("read screen"):
        # Optional region: screen read x y w h
        parts = lowered.split()
        if len(parts) >= 5:
            try:
                x, y, w, h = map(int, parts[2:6])
                return screen.read_screen((x, y, w, h))
            except ValueError:
                pass
        return screen.read_screen()

    if lowered.startswith("screen write ") or lowered.startswith("type "):
        if lowered.startswith("screen write "):
            content = original[13:]
        else:
            content = original[5:]
        return screen.write_text(content)

    if lowered.startswith("screen click ") or lowered.startswith("click "):
        parts = lowered.split()
        if len(parts) >= 3:
            try:
                x, y = int(parts[-2]), int(parts[-1])
                return screen.click(x, y)
            except ValueError:
                pass
        # Use current mouse position
        x, y = screen.get_mouse_position()
        return screen.click(x, y)

    if lowered.startswith("scroll "):
        try:
            amount = int(lowered.split()[1])
            return screen.scroll(amount)
        except (IndexError, ValueError):
            return "Usage: scroll <amount> (positive = up)"

    if lowered.startswith("press "):
        key = lowered[6:].strip()
        return screen.press_key(key)

    if lowered.startswith("hotkey "):
        keys = lowered[7:].split()
        return screen.hotkey(*keys)

    if lowered == "mouse position" or lowered == "where is mouse":
        x, y = screen.get_mouse_position()
        return f"Mouse at ({x}, {y})"

    # 3. Open URL / search
    if lowered.startswith("open "):
        target = lowered[5:].strip()
        if not (target.startswith("http://") or target.startswith("https://")):
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
        if any(first == d or first.startswith(d + ".") for d in dangerous):
            return f"Blocked: '{first}' is not allowed."
        if first == "rm" and any(a in ("-r", "-rf", "-fr") for a in parts):
            return "Blocked: recursive rm is not allowed."

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
        self.awake = False
        self.clap_detector = ClapDetector(self.wake_up)

    def wake_up(self):
        """Called when double-clap detected."""
        if not self.awake:
            self.awake = True
            console.print(Panel("[bold green]▶ JARVIS ONLINE[/]", style="green"))
            tts.speak("Systems online, Sir.")

    def sleep(self):
        """Put JARVIS to sleep."""
        if self.awake:
            self.awake = False
            console.print(Panel("[bold yellow]⏸ JARVIS STANDBY[/]", style="yellow"))
            tts.speak("Standing by, Sir. Double clap to wake.")

    def run(self):
        """Main loop."""
        console.clear()

        # Show startup banner
        banner = Table.grid(padding=1)
        banner.add_column(style="bold magenta", justify="center")
        banner.add_row("╔══════════════════════════════════════╗")
        banner.add_row("║       JARVIS - DEXTER PROTOCOL       ║")
        banner.add_row("║     Voice: en-GB-RyanNeural          ║")
        banner.add_row("║     Speed: -15% | Pitch: -10Hz       ║")
        if CLAP_DETECTION_AVAILABLE:
            banner.add_row("║     Wake: Double clap                ║")
        if SCREEN_CONTROL_AVAILABLE:
            banner.add_row("║     Screen: OCR + Input Control      ║")
        banner.add_row(f"║     Model: {OLLAMA_MODEL:<27}║")
        banner.add_row("╚══════════════════════════════════════╝")
        console.print(Panel(banner, expand=False))

        # Warm up Ollama model so first response is fast
        try:
            with console.status("[bold green]Warming up model...[/]", spinner="dots"):
                llm.ask("ping")
        except Exception:
            pass  # Warm-up is best-effort; real errors handled at runtime

        # Start clap detection
        if CLAP_DETECTION_AVAILABLE:
            self.clap_detector.start()
            self.clap_detector.tts_ref = tts  # Link TTS for speaking detection
            console.print("[dim]Double clap to activate...[/dim]\n")
        else:
            self.awake = True  # Start awake if no clap detection
            console.print("[dim]Clap detection unavailable. Starting active. Say 'sleep' for standby.[/dim]\n")

        try:
            while True:
                if not self.awake:
                    # In sleep mode - just wait for clap
                    time.sleep(0.5)
                    continue

                # 1. Capture user input (voice -> text)
                user_input = stt.listen(timeout=8, phrase_limit=12).strip()
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

                    console.print(Panel(command_reply, title="[cyan]JARVIS (command)[/]", subtitle="[OK]"))
                    tts.speak(command_reply)
                    memory.add("assistant", command_reply)
                    memory.dump()
                    continue

                # 5. Ask Ollama for a response
                with console.status("[bold green]Processing...[/]", spinner="dots"):
                    try:
                        response = llm.ask(user_input)
                    except Exception as exc:
                        response = f"System error: {exc}"
                        console.log(f"[red]Ollama error: {exc}[/]")

                # 6. Show + speak the answer
                console.print(Panel(response, title="[cyan]JARVIS[/]", subtitle="[IDEA]"))
                tts.speak(response)

                # 7. Persist turn
                memory.add("assistant", response)
                memory.dump()

        except KeyboardInterrupt:
            console.print("\n[bold red]Terminated, Sir.[/]")
        finally:
            self.clap_detector.stop()


def main():
    controller = JarvisController()
    controller.run()


if __name__ == "__main__":
    main()