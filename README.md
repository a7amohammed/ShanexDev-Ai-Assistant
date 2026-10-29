# Shanex

A voice-first CLI AI assistant. Say **"wake up"** and it answers **"Wake up"**.

## Features

### 🎭 **Persona**
- Dark, clinical, measured tone
- "Sir" address
- No exclamation points - precise, deliberate speech
- Voice: `en-GB-RyanNeural` (Deep British male)
- Default speed: `-15%` | Pitch: `-10Hz`

### 🎤 **Voice Control**
- **Speech-to-Text**: offline, streaming [Moonshine Voice](https://pypi.org/project/moonshine-voice/) (no word/time limit - it listens until you stop talking)
- **Text-to-Speech**:
  - Primary: `edge-tts` (Microsoft neural voices)
  - Fallback: `gTTS` + `pygame`
- **Voice Speed Control**:
  - `speed +20%` / `speed -30%` / `speed 0%`
  - `voice faster` / `voice slower` / `voice normal`

### 🗣️ **Wake Phrase**
- Say **"wake up"** (no name needed) to wake the assistant from standby
- The spoken reply is exactly **`Wake up`** - nothing before or after it
- Only short phrases (up to 5 words) count, so "I have to wake up early tomorrow" does not trigger it
- Say "sleep" or "standby" to return to standby mode
- The microphone is muted while Shanex speaks, so it never hears itself

### 🖥️ **Screen Control & Automation**
- **OCR**: `screen read` - captures and reads screen text via Tesseract
- **Type**: `screen write <text>` / `type <text>` - types at cursor position
- **Click**: `screen click <x> <y>` / `click <x> <y>` - clicks coordinates
- **Mouse**: `mouse position` - shows current cursor location
- **Scroll**: `scroll <amount>` - positive=up, negative=down
- **Keys**: `press <key>` (enter, tab, escape, etc.)
- **Hotkeys**: `hotkey ctrl c` / `hotkey alt tab` / etc.

### ⚡ **System Commands**
- `open <url>` - Opens browser (Google searches if not a URL)
- `run <command>` - Executes shell command with output (asks for confirmation)
- `sleep` / `standby` - Returns to standby (waiting for the wake phrase)
- `exit` / `quit` - Terminates Shanex

### 🧬 **Personality & Long-Term Memory (`shanex_personality_memory.json`)**
- One JSON file next to `main.py` defines Shanex's name, style rules, behavior rules, facts about the user and background facts. Edit it by hand - no code changes needed.
- It is **configuration, not chat history**: the app only reads it and never writes to it. Chat history stays in `jarvis_memory.json`.
- It is validated at startup. If the file is missing or invalid, Shanex starts with the built-in personality and prints why. The startup line `Personality: ... loaded` confirms the file is in use.
- `memory.prompt_char_budget` caps the prompt size (long-term facts are dropped first), and `memory.history_turns_sent` sets how many past messages are sent to the model. Lower both for small local models.
- Use another file with `SHANEX_PERSONALITY=other.json` in `.env`.
- The wake-up reply is fixed in code as exactly `Wake up` and cannot be changed from this file.
- Do not put API keys or passwords in it.
- A history file that cannot be read is copied to `jarvis_memory.json.corrupt-<timestamp>` before it is replaced, so conversations are not lost.

### 🧠 **AI Brain**
- Ollama (local, or Ollama Cloud with `OLLAMA_HOST=https://ollama.com` + `OLLAMA_API_KEY`)
- Persistent conversation memory (`jarvis_memory.json` - file name kept so existing memory keeps working)
- Context-aware responses

## Installation

```bash
git clone https://github.com/a7amohammed/shanexdev-ai-assistant
cd shanexdev-ai-assistant
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# System packages (Ubuntu/Debian)
sudo apt-get install tesseract-ocr
```

Create a `.env` file next to `main.py`:

```bash
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.2:1b
# OLLAMA_API_KEY=...        # only for Ollama Cloud
# STT_LANGUAGE=en           # en or ar
# MOONSHINE_ARCH=TINY_STREAMING
# STT_SAFETY_TIMEOUT=120    # hard cap in seconds so a stuck mic cannot hang
# AUDIO_DEVICE=             # index or name substring
# SHANEX_PERSONALITY=shanex_personality_memory.json
# SHANEX_DRY_RUN=1          # (the old JARVIS_DRY_RUN name is still accepted)
```

The first voice run downloads the Moonshine model (needs internet once), then it works offline.

## Usage

```bash
source venv/bin/activate
python3 main.py            # voice mode
python3 main.py --text     # keyboard mode
python3 main.py --selftest # self-check
```

### Interaction Flow

1. **Startup**: Shanex enters standby and waits for the wake phrase
2. **Wake**: say "wake up" → Shanex says "Wake up"
3. **Active**: Speak commands or questions
4. **Sleep**: say "sleep" → "Standing by, Sir. Say wake up."
5. **Exit**: say "exit" or press Ctrl+C

### Example Commands

```
# Voice control
"speed -20%"
"voice faster"
"voice normal"

# Screen automation
"screen read"
"screen read 100 100 800 600"  # Region: x y width height
"screen write Hello world"
"click 500 300"
"mouse position"
"scroll 5"
"press enter"
"hotkey ctrl c"

# System
"open youtube.com"
"run ls -la"
"sleep"
"exit"
```

## Architecture

```
main.py
├── Memory            - Persistent JSON conversation history
├── load_personality  - Reads + validates shanex_personality_memory.json
├── MoonshineSTT      - Offline streaming speech recognition
├── is_wake_phrase()  - "wake up" matcher
├── TTS               - Edge-TTS + gTTS fallback
├── ScreenControl     - pyautogui + pytesseract automation
├── OllamaLLM         - Ollama client wrapper
├── Command Handler   - Rule-based + LLM routing
└── JarvisController  - State machine (standby/awake); class name kept for compatibility
```

## Tests

```bash
pip install pytest
python3 -m pytest tests -q
```

Tests use mocks - no microphone, speakers, network or real screen control are needed.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Wake phrase not detected | Speak closer to the mic; check the `Heard:` line in the terminal to see what was recognised |
| Moonshine model download times out | Re-run; or pre-download with `get_model_for_language("en", ModelArch.TINY_STREAMING)` on a better connection |
| Falls back to keyboard prompt | Moonshine failed to load - fix the error shown, then restart |
| Tesseract not found | `sudo apt-get install tesseract-ocr` |
| Edge-TTS pitch error | Use Hz format: `-10Hz` not `-10%` |
| Audio loop warnings | Normal ALSA/JACK noise, harmless |
| Assistant still calls itself JARVIS | Old chats are in `jarvis_memory.json`; back it up and delete it to start fresh |

## Customization

Edit constants at the top of `main.py`:
- `OLLAMA_MODEL` / `OLLAMA_HOST` - Change AI model / server (or use `.env`)
- `DEXTER_VOICE` - Any edge-tts voice
- `DEXTER_SPEED` / `DEXTER_PITCH` - Voice parameters
- `WAKE_RESPONSE` - What Shanex says on wake (keep it exactly `Wake up`)
- `WAKE_MAX_WORDS` - Longest utterance that can count as the wake phrase

## License

MIT - Use freely, modify as needed.
