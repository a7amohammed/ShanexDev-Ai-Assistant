# J.A.R.V.I.S. Mk. LV

**Cross-platform, voice-driven personal AI assistant with a holographic HUD.**


J.A.R.V.I.S. Mk. LV is a real-time assistant that hears, sees, speaks and controls your computer on Windows, macOS and Linux. It is built on the Gemini Live API for native audio streaming and presents its state through a PyQt6 heads-up display (HUD). The assistant speaks English in a formal, professional register.

---

## 1. Capabilities

| Area | Description |
|---|---|
| Voice | Low-latency conversation through the Gemini Live API; wake word ("Hey Jarvis"), push-to-talk (Ctrl+Space), self-echo guard and audio-device picker |
| HUD | Holographic avatar or reactor-core display, live theming, reactive waveform, floating windows |
| 3D Earth | Interactive globe with live layers: earthquakes, fires, storms, precipitation radar, 3D flights and public live cameras |
| Market radar | Stocks and cryptocurrency quotes, charts, favourites and spoken alerts for important news (informational only, not financial advice) |
| OSINT module | Alias scan across platforms, public name search, image EXIF analysis and visual geolocation, with a local profile library |
| Mail | Read, summarise, reply to and send email from several accounts (Gmail, iCloud, others) with app passwords |
| Music | Spotify control and synchronised lyrics |
| System | Applications, volume, brightness, Wi-Fi, power, files, browser and desktop control, with undo and real confirmation of irreversible actions |
| Memory | Persistent, recallable memory stored locally |
| Plugins | Drop a single `.py` file into `plugins/` to add a skill |

## 2. Quick start

```bash
python setup.py        # installs the dependencies for your operating system
python main.py
```

Requirements: Python 3.11–3.13, a microphone and speakers, and a Gemini API key. No GPU is required.

## 3. Configuration and secrets

No credential is shipped with this project. Provide secrets through environment variables (preferred) or through the in-app settings:

| Secret | Environment variable |
|---|---|
| Gemini API key | `GEMINI_API_KEY` |
| Spotify application | `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET` |
| Mail app-passwords | `JARVIS_MAIL_<ACCOUNT_ID>_PASSWORD` |

Copy `.env.example` as a reference and `config/*.example.json` as templates. Environment values take precedence over stored values and are never written to disk. See [SECURITY.md](SECURITY.md) for the rotation guidance.

## 4. Architecture

```
main.py              Core loop: Gemini Live session, audio I/O, tool dispatch
ui.py                PyQt6 HUD: avatar, waveform, log, settings drawer, floating windows
hud/credits.py       Project name and credits line (single source of truth)
config/              Configuration access and environment overlay (env_overlay.py)
memory/              Long-term memory and configuration managers
core/                Prompt, avatar, lip-sync, globe, cameras, market radar, OSINT, mail, audio
actions/             Bundled tools (search, files, system, browser, messaging, vision, ...)
plugins/             Drop-in skills (Spotify, mail, market radar, OSINT, quiz, ...)
dashboard/           Remote phone dashboard (self-signed TLS generated locally)
tools/               Maintenance scripts (camera link checker and fixer)
```

## 5. Market radar

A free-floating HUD panel with a favourites list (price, change, mini chart), a large chart (line, area or candles; 1D to 5Y), latest headlines and configurable spoken alerts. Alerts respect a cooldown, a daily cap and quiet hours. Data comes from public, unofficial Yahoo Finance endpoints (with CoinGecko as a crypto fallback) and may be delayed or change without notice.

Voice examples: "How is Bitcoin doing?", "Add Tesla to my favourites", "Pin Nvidia", "Show me the Ethereum chart for five days", "Hide the radar", "Disable market alerts".

Files: `core/market_data.py`, `core/market_radar.py`, `core/market_radar_widget.py`, `plugins/market_radar.py`. Settings are stored in `config/market_radar.json`.

## 6. Your data

Everything stays on your machine. Your voice is streamed to the Gemini Live API only while a session is open. Local files containing personal data (`config/api_keys.json`, `config/mail_accounts.json`, `config/.spotify_cache`, `config/certs/`, `memory/long_term.json`) are excluded by `.gitignore`.
|