# JARVIS - Dexter Protocol

A voice-first CLI AI assistant inspired by JARVIS (Iron Man) with Dexter Morgan's precise, measured persona.

## Features

### 🎭 **Dexter Morgan Persona**
- Dark, clinical, measured tone
- "Sir" address with philosophical undertones
- No exclamation points - precise, deliberate speech
- Voice: `en-GB-RyanNeural` (Deep British male)
- Default speed: `-15%` | Pitch: `-10Hz`

### 🎤 **Voice Control**
- **Speech-to-Text**: Microphone input via `speech_recognition`
- **Text-to-Speech**: 
  - Primary: `edge-tts` (Microsoft neural voices)
  - Fallback: `gTTS` + `pygame`
- **Voice Speed Control**:
  - `speed +20%` / `speed -30%` / `speed 0%`
  - `voice faster` / `voice slower` / `voice normal`

### 👏 **Double-Clap Wake Detection**
- Runs in background thread using `sounddevice`
- Detects two sharp claps within 0.6 seconds
- Cooldown period prevents retriggering
- Ignores audio while JARVIS is speaking
- Say "sleep" or "standby" to return to sleep mode

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
- `run <command>` - Executes shell command with output
- `sleep` / `standby` - Returns to clap-waiting mode
- `exit` / `quit` - Terminates JARVIS

### 🧠 **AI Brain**
- Google Gemini (configurable model, default: `gemini-3.8-flash`)
- Persistent conversation memory (`jarvis_memory.json`)
- Retry logic for 503/429 errors
- Context-aware responses

### 📦 **Dependencies**

```bash
# Python packages
pip install -r requirements.txt

# System packages (Ubuntu/Debian)
sudo apt-get install tesseract-ocr
```

## Installation

```bash
cd ~/Desktop/jarvis
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
# Add your API key
echo "GEMINI_API_KEY=your_key_here" > .env
```

## Usage

```bash
# Activate venv and run
source venv/bin/activate
python3 main.py
```

### Interaction Flow

1. **Startup**: JARVIS enters standby mode, waiting for double-clap
2. **Wake**: Double-clap → "Systems online, Sir."
3. **Active**: Speak commands or questions
4. **Sleep**: Say "sleep" → "Standing by, Sir. Double clap to wake."
5. **Exit**: Say "exit" or Ctrl+C

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
├── Memory          - Persistent JSON conversation history
├── ClapDetector    - Background thread, double-clap detection
├── STT             - Speech recognition (Google Web Speech)
├── TTS             - Edge-TTS + gTTS fallback, Dexter voice
├── ScreenControl   - pyautogui + pytesseract automation
├── Gemini          - Google GenAI wrapper with retries
├── Command Handler - Rule-based + LLM routing
└── JarvisController - State machine (sleep/awake)
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "Unintelligible" | Speak closer to mic, reduce background noise |
| Tesseract not found | `sudo apt-get install tesseract-ocr` |
| Clap detection false triggers | Increase `CLAP_THRESHOLD` (default 0.35) |
| Edge-TTS pitch error | Use Hz format: `-10Hz` not `-10%` |
| Audio loop warnings | Normal ALSA/JACK noise, harmless |

## Customization

Edit constants at top of `main.py`:
- `GEMINI_MODEL` - Change AI model
- `DEXTER_VOICE` - Any edge-tts voice
- `DEXTER_SPEED` / `DEXTER_PITCH` - Voice parameters
- `CLAP_THRESHOLD` / `CLAP_TIME_WINDOW` / `CLAP_COOLDOWN` - Sensitivity

## License

MIT - Use freely, modify as needed.