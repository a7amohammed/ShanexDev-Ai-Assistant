# Shanex Phase checklist

## Phase 1 — import-safety & bug fixes
- [x] 1.1 Backup main.py → main.py.bak
- [ ] 1.2 main.py import-safe (no model/audio/network at import); argparse --text/--selftest/--offline/--debug
- [x] 1.4 wake_up: tts.speak first, awake=True after; wait for tts.speaking False + 0.6s before listen()
- [x] 1.5 STT.listen: q.get(timeout=0.1), drain+Reset at start, FinalResult at timeout, phrase_limit, "Heard: <text>", missing-model fallback
- [x] 1.6 Audio device: auto-detect probe with ALSA stderr silenced (dup2), AUDIO_DEVICE override, banner shows device
- [x] 1.7 Ollama: cloud requires API_KEY, no num_ctx/keep_alive for cloud, skip warm-up on cloud, 429 no-retry, 404 localhost=ollama pull / cloud=list models
- [ ] 1.8 Commands: open (original text; IP/domain→http, else Google), screen ocr/ocr aliases, type/press/click/hotkey/scroll y/N confirm
- [x] 1.9 Privacy: no screen/cmd/error output in memory (placeholder), generic error sentence, chmod 600
- [x] 1.10 Cleanup imports/dup json/docstring; requirements.txt + requirements-dev.txt

## Phase 2 — tests (gate)
- [x] 2.1 tests/test_jarvis.py (pytest + mock, no real mic/speaker/net/pyautogui)
- [x] 2.2 `python main.py --selftest` PASS/FAIL/SKIP table, exit 0 only if no FAIL
- [ ] 2.3 pytest green; `JARVIS_DRY_RUN=1 timeout 90 python main.py --selftest` green
- [ ] 2.4 Smoke test keyboard mode: printf 'hello\nspeed -10\nopen 192.168.1.1\nexit\n' | JARVIS_DRY_RUN=1 timeout 90 python main.py --text --offline
- [ ] 2.5 STOP + report

## Phase 3 — features (only after Phase 2 green)
- [ ] 3a status command (RAM/CPU/disk/battery/temp)
- [ ] 3b timer / remind me (background thread, JSON file)
- [ ] 3c volume up/down/mute/<0-100> via pactl/amixer
- [ ] 3d stream Ollama reply, speak sentence by sentence
- [ ] 3e jarvis.log rotating log via --debug
- [ ] 3f help command; Arabic text → ar-EG-ShakirNeural voice
- [ ] 3g Update docstring + banner per feature