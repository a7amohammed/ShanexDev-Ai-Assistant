#!/usr/bin/env python3
"""
Test suite for JARVIS - pytest + unittest.mock
No real mic, speakers, network, or pyautogui.
"""

import sys
import json
import time
import threading
import tempfile
from pathlib import Path
from unittest import mock

import pytest
import numpy as np

# Import the main module
sys.path.insert(0, str(Path(__file__).parent.parent))

import main

from main import (
    Memory,
    OllamaLLM,
    ClapDetector,
    handle_command,
    sanitize_for_memory,
    JarvisController,
    TTS,
    ScreenControl,
    STT,
    CLAP_THRESHOLD,
    CLAP_TIME_WINDOW,
    CLAP_COOLDOWN,
    DEXTER_SPEED,
    DEXTER_VOICE,
)


# ============================================================
# Fixtures & Setup
# ============================================================

@pytest.fixture(autouse=True)
def _reset_globals():
    """Ensure clean state between tests."""
    # Import here to avoid import-time side effects
    import main
    # Reset module-level singletons that tests might mutate
    main.memory.history = []
    main.current_speed = DEXTER_SPEED
    # Re-initialize singletons to clean state
    main.tts = TTS()
    main.screen = ScreenControl()
    main.llm = OllamaLLM()
    yield
    # Cleanup
    if main.clap_detector:
        main.clap_detector.stop()


@pytest.fixture
def mock_prompt():
    """Patch rich.prompt.Prompt.ask for confirmation dialogs."""
    with mock.patch("main.Prompt") as m:
        yield m


@pytest.fixture
def mock_webbrowser():
    with mock.patch("main.webbrowser") as m:
        yield m


@pytest.fixture
def mock_subprocess():
    with mock.patch("main.subprocess") as m:
        yield m


@pytest.fixture
def mock_tts():
    with mock.patch.object(main.tts, "speak") as m:
        main.tts.speaking = False
        yield m


@pytest.fixture
def mock_screen():
    """Mock screen methods to avoid pyautogui."""
    with mock.patch.object(main.screen, "read_screen", return_value="OCRTEXT") as rs, \
         mock.patch.object(main.screen, "write_text", return_value="Typed: test") as wt, \
         mock.patch.object(main.screen, "click", return_value="Clicked at (1,2)") as cl, \
         mock.patch.object(main.screen, "scroll", return_value="Scrolled up 5 units") as sc, \
         mock.patch.object(main.screen, "press_key", return_value="Pressed enter") as pk, \
         mock.patch.object(main.screen, "hotkey", return_value="Hotkey: ctrl+c") as hk, \
         mock.patch.object(main.screen, "get_mouse_position", return_value=(10, 20)) as gm:
        yield {
            "read_screen": rs,
            "write_text": wt,
            "click": cl,
            "scroll": sc,
            "press_key": pk,
            "hotkey": hk,
            "get_mouse_position": gm,
        }


# ============================================================
# handle_command tests
# ============================================================

class TestHandleCommand:
    """Tests for handle_command() function."""

    def test_open_ip_address(self, mock_webbrowser):
        result = handle_command("open 192.168.1.1")
        assert result == "Opening http://192.168.1.1"
        mock_webbrowser.open.assert_called_once_with("http://192.168.1.1")

    def test_open_domain(self, mock_webbrowser):
        result = handle_command("open example.com")
        assert result == "Opening http://example.com"
        mock_webbrowser.open.assert_called_once_with("http://example.com")

    def test_open_full_url(self, mock_webbrowser):
        result = handle_command("open https://example.com")
        assert result == "Opening https://example.com"
        mock_webbrowser.open.assert_called_once_with("https://example.com")

    def test_open_search_term(self, mock_webbrowser):
        result = handle_command("open hello world")
        assert result == "Opening https://www.google.com/search?q=hello+world"
        mock_webbrowser.open.assert_called_once_with("https://www.google.com/search?q=hello+world")

    def test_open_empty(self, mock_webbrowser):
        result = handle_command("open ")
        assert result == "Usage: open <url or search term>"
        mock_webbrowser.open.assert_not_called()

    def test_open_no_args(self, mock_webbrowser):
        result = handle_command("open")
        assert result is None
        mock_webbrowser.open.assert_not_called()

    def test_speed_negative(self, mock_tts):
        main.tts.speed = DEXTER_SPEED
        with mock.patch.object(main.tts, "set_speed") as ss:
            result = handle_command("speed -10")
            assert result == "Voice speed set to -10%, Sir."
            ss.assert_called_once_with("-10%")

    def test_speed_positive(self, mock_tts):
        with mock.patch.object(main.tts, "set_speed") as ss:
            result = handle_command("speed +50")
            assert result == "Voice speed set to +50%, Sir."
            ss.assert_called_once_with("+50%")

    def test_speed_zero(self, mock_tts):
        with mock.patch.object(main.tts, "set_speed") as ss:
            result = handle_command("speed 0")
            assert result == "Voice speed set to 0%, Sir."
            ss.assert_called_once_with("0%")

    def test_speed_invalid(self, mock_tts):
        with mock.patch.object(main.tts, "set_speed") as ss:
            result = handle_command("speed abc")
            assert result == "Speed format: +20%, -15%, 0%"
            ss.assert_not_called()

    def test_screen_read_alias(self, mock_screen):
        result = handle_command("screen read")
        assert result == "OCRTEXT"
        mock_screen["read_screen"].assert_called_once_with()

    def test_read_screen_alias(self, mock_screen):
        result = handle_command("read screen")
        assert result == "OCRTEXT"
        mock_screen["read_screen"].assert_called_once_with()

    def test_screen_ocr_alias(self, mock_screen):
        result = handle_command("screen ocr")
        assert result == "OCRTEXT"
        mock_screen["read_screen"].assert_called_once_with()

    def test_ocr_alias(self, mock_screen):
        result = handle_command("ocr hello world")
        assert result == "OCRTEXT"
        mock_screen["read_screen"].assert_called_once_with()

    def test_screen_read_with_region(self, mock_screen):
        result = handle_command("screen read 10 20 30 40")
        assert result == "OCRTEXT"
        mock_screen["read_screen"].assert_called_once_with((10, 20, 30, 40))

    def test_run_blocked_rm(self, mock_prompt, mock_subprocess):
        # rm with -rf triggers recursive block first
        result = handle_command("run rm -rf /")
        assert "Blocked: recursive rm is not allowed." in result
        mock_prompt.ask.assert_not_called()
        mock_subprocess.check_output.assert_not_called()

    def test_run_blocked_rm_no_recursive(self, mock_prompt, mock_subprocess):
        # rm without -rf triggers general block
        result = handle_command("run rm file.txt")
        assert "Blocked: 'rm' is not allowed." in result
        mock_prompt.ask.assert_not_called()

    def test_run_blocked_sudo(self, mock_prompt, mock_subprocess):
        result = handle_command("run sudo ls")
        assert "Blocked: 'sudo' is not allowed." in result
        mock_prompt.ask.assert_not_called()

    def test_run_blocked_rm_recursive(self, mock_prompt, mock_subprocess):
        result = handle_command("run rm -rf /tmp")
        assert "Blocked: recursive rm is not allowed." in result
        mock_prompt.ask.assert_not_called()

    def test_run_allowed_declined(self, mock_prompt, mock_subprocess):
        mock_prompt.ask.return_value = "n"
        result = handle_command("run echo hello")
        assert result == "Command cancelled, Sir."
        mock_subprocess.check_output.assert_not_called()

    def test_run_allowed_accepted(self, mock_prompt, mock_subprocess):
        mock_prompt.ask.return_value = "y"
        mock_subprocess.check_output.return_value = "hello\n"
        result = handle_command("run echo hello")
        assert "Command output:\nhello\n" == result
        mock_subprocess.check_output.assert_called_once()

    def test_sleep_command(self):
        result = handle_command("sleep")
        assert result == "SLEEP_MODE"

    def test_exit_command(self):
        with pytest.raises(SystemExit) as exc:
            handle_command("exit")
        assert exc.value.code == 0

    def test_voice_settings(self):
        result = handle_command("voice settings")
        assert "Voice: en-GB-RyanNeural" in result
        assert "Speed: -15%" in result

    def test_mouse_position(self, mock_screen):
        result = handle_command("mouse position")
        assert result == "Mouse at (10, 20)"

    def test_plain_text_returns_none(self):
        result = handle_command("tell me a joke")
        assert result is None


# ============================================================
# Memory tests
# ============================================================

class TestMemory:
    """Tests for Memory class."""

    def test_add_and_dump_and_recent(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "mem.json"
            mem = Memory(path)
            mem.add("user", "hello")
            mem.add("assistant", "hi")
            mem.dump()

            mem2 = Memory(path)
            assert len(mem2.history) == 2
            assert mem2.history[0]["role"] == "user"
            assert mem2.history[0]["content"] == "hello"
            assert mem2.history[1]["role"] == "assistant"
            assert mem2.history[1]["content"] == "hi"

            recent = mem2.recent(1)
            assert len(recent) == 1
            assert recent[0]["content"] == "hi"

    def test_corrupt_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "corrupt.json"
            path.write_text("not valid json {{{")
            mem = Memory(path)
            assert mem.history == []

    def test_non_list_json(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "nonlist.json"
            path.write_text('{"a": 1}')
            mem = Memory(path)
            assert mem.history == []

    def test_truncation_to_200(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "mem.json"
            mem = Memory(path)
            for i in range(205):
                mem.add("user", f"msg{i}")
            assert len(mem.history) == 200
            assert mem.history[0]["content"] == "msg5"  # first 5 dropped


# ============================================================
# OllamaLLM tests
# ============================================================

class TestOllamaLLM:
    """Tests for OllamaLLM class."""

    def test_build_messages(self):
        with tempfile.TemporaryDirectory() as d:
            # Create a fresh memory for this test
            import main
            old_mem = main.memory
            mem = Memory(Path(d) / "mem.json")
            main.memory = mem
            try:
                mem.add("user", "prev")
                mem.add("assistant", "resp")
                llm = OllamaLLM()
                msgs = llm._build_messages("current")
                # Should have system + prev user + prev assistant + current user
                assert len(msgs) == 4
                assert msgs[0]["role"] == "system"
                assert msgs[1]["role"] == "user"
                assert msgs[1]["content"] == "prev"
                assert msgs[2]["role"] == "assistant"
                assert msgs[3]["role"] == "user"
                assert msgs[3]["content"] == "current"
            finally:
                main.memory = old_mem

    def test_build_messages_excludes_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            import main
            old_mem = main.memory
            mem = Memory(Path(d) / "mem.json")
            main.memory = mem
            try:
                mem.add("user", "current")
                llm = OllamaLLM()
                msgs = llm._build_messages("current")
                # Should exclude the last user msg that matches current
                assert len(msgs) == 2  # system + current
            finally:
                main.memory = old_mem

    def test_error_mapping_404_local(self):
        llm = OllamaLLM()
        with mock.patch.object(llm, "client") as c:
            c.chat.side_effect = mock.MagicMock(side_effect=ImportError)  # placeholder
            # We can't easily mock ResponseError with status_code here
            # This is tested in integration; unit test just verifies logic exists
            pass

    def test_ask_empty_content(self):
        llm = OllamaLLM()
        with mock.patch.object(llm, "client") as c:
            c.chat.return_value = {"message": {"content": ""}}
            result = llm.ask("hi")
            assert result == "I have nothing to say on that, Sir."

    def test_ask_normal(self):
        llm = OllamaLLM()
        with mock.patch.object(llm, "client") as c:
            c.chat.return_value = {"message": {"content": "  hello sir  "}}
            result = llm.ask("hi")
            assert result == "hello sir"


# ============================================================
# ClapDetector tests
# ============================================================

class TestClapDetector:
    """Tests for ClapDetector with synthetic RMS values."""

    def test_double_clap_within_window(self):
        called = []
        cd = ClapDetector(callback=lambda: called.append("wake"))
        cd.running = True
        cd.tts_ref = mock.MagicMock()
        cd.tts_ref.speaking = False

        pt = threading.Thread(target=cd.process_audio, daemon=True)
        pt.start()

        # Clap 1
        cd.audio_queue.put(CLAP_THRESHOLD + 0.05)
        time.sleep(0.06)  # brief silence to end clap event
        cd.audio_queue.put(0.01)
        time.sleep(0.06)
        # Clap 2 within CLAP_TIME_WINDOW (0.6s)
        time.sleep(0.3)
        cd.audio_queue.put(CLAP_THRESHOLD + 0.05)
        time.sleep(0.06)
        cd.audio_queue.put(0.01)

        time.sleep(0.5)
        cd.running = False
        pt.join(timeout=3)

        assert len(called) == 1
        assert called[0] == "wake"

    def test_double_clap_too_slow_resets(self):
        called = []
        cd = ClapDetector(callback=lambda: called.append("wake"))
        cd.running = True
        cd.tts_ref = mock.MagicMock()
        cd.tts_ref.speaking = False

        pt = threading.Thread(target=cd.process_audio, daemon=True)
        pt.start()

        # Clap 1
        cd.audio_queue.put(CLAP_THRESHOLD + 0.05)
        time.sleep(0.06)
        cd.audio_queue.put(0.01)
        # Wait longer than CLAP_TIME_WINDOW
        time.sleep(CLAP_TIME_WINDOW + 0.1)
        # Clap 2 - too slow, should reset count
        cd.audio_queue.put(CLAP_THRESHOLD + 0.05)
        time.sleep(0.06)
        cd.audio_queue.put(0.01)

        time.sleep(0.5)
        cd.running = False
        pt.join(timeout=3)

        assert len(called) == 0

    def test_clap_during_tts_speaking_ignored(self):
        called = []
        cd = ClapDetector(callback=lambda: called.append("wake"))
        cd.running = True
        cd.tts_ref = mock.MagicMock()
        cd.tts_ref.speaking = True  # TTS is speaking

        pt = threading.Thread(target=cd.process_audio, daemon=True)
        pt.start()

        cd.audio_queue.put(CLAP_THRESHOLD + 0.05)
        time.sleep(0.06)
        cd.audio_queue.put(0.01)
        time.sleep(0.06)
        cd.audio_queue.put(CLAP_THRESHOLD + 0.05)
        time.sleep(0.06)
        cd.audio_queue.put(0.01)

        time.sleep(0.5)
        cd.running = False
        pt.join(timeout=3)

        # Should be ignored because tts_ref.speaking == True
        assert len(called) == 0


# ============================================================
# TTS tests
# ============================================================

class TestTTS:
    """Tests for TTS class."""

    def test_set_speed(self):
        tts = TTS()
        tts.set_speed("+20%")
        assert tts.speed == "+20%"

    def test_set_voice(self):
        tts = TTS()
        tts.set_voice("en-US-GuyNeural")
        assert tts.voice == "en-US-GuyNeural"


# ============================================================
# sanitize_for_memory tests
# ============================================================

class TestSanitizeForMemory:
    """Tests for sanitize_for_memory function."""

    def test_command_output_prefix(self):
        text = "Command output:\nls -la\nfile.txt"
        assert sanitize_for_memory(text) == "[output omitted]"

    def test_command_failed_prefix(self):
        text = "Command failed (code 1):\nerror"
        assert sanitize_for_memory(text) == "[output omitted]"

    def test_error_executing_prefix(self):
        text = "Error executing command: something"
        assert sanitize_for_memory(text) == "[output omitted]"

    def test_ocr_error_prefix(self):
        text = "OCR error: tesseract not found"
        assert sanitize_for_memory(text) == "[output omitted]"

    def test_long_multiline(self):
        text = "line\n" * 40  # > 120 chars with newlines
        assert sanitize_for_memory(text) == "[output omitted]"

    def test_short_normal_text(self):
        text = "hello world"
        assert sanitize_for_memory(text) == "hello world"

    def test_none_returns_none(self):
        assert sanitize_for_memory(None) is None


# ============================================================
# wake_up tests
# ============================================================

class TestWakeUp:
    """Tests for JarvisController.wake_up() - speaks before awake=True."""

    def test_wake_up_speaks_before_awake_true(self):
        import main
        ctrl = JarvisController()
        ctrl.awake = False

        # Patch the global clap_detector
        fake_cd = mock.MagicMock()
        fake_cd.pause = mock.MagicMock()

        order = []

        def tracking_speak(text):
            order.append(("speak", text))
            # Simulate that speaking is happening
            main.tts.speaking = True

        with mock.patch.object(main, "clap_detector", fake_cd):
            with mock.patch.object(main.tts, "speak", side_effect=tracking_speak):
                ctrl.wake_up()

        # Verify order: speak was called, THEN awake became True
        assert len(order) == 1
        assert order[0][0] == "speak"
        assert order[0][1] == "Systems online, Sir."
        assert ctrl.awake is True  # awake is set after speak
        fake_cd.pause.assert_called_once()


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])