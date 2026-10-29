#!/usr/bin/env python3
"""Tests for shanex_personality_memory.json loading, validation and prompt use.

No mic, speakers, network or Ollama server needed.
"""

import os
import sys
import json
import copy
from pathlib import Path
from unittest import mock

import pytest

os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).parent.parent))

import main
from main import (
    Memory,
    JarvisController,
    load_personality,
    validate_personality,
    build_system_prompt,
    DEFAULT_SYSTEM_PROMPT,
)

SHIPPED = Path(main.__file__).resolve().parent / "shanex_personality_memory.json"


@pytest.fixture
def good_cfg():
    return json.loads(SHIPPED.read_text(encoding="utf-8"))


def write(tmp_path, data, name="p.json"):
    p = tmp_path / name
    p.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return p


# ------------------------------------------------------------
# The shipped file, and the app actually using it
# ------------------------------------------------------------

class TestShippedFile:
    def test_file_exists_and_is_valid(self):
        res = load_personality(SHIPPED)
        assert res["status"] == "loaded", res["errors"]
        assert res["errors"] == []

    def test_assistant_name_is_shanex(self, good_cfg):
        assert good_cfg["identity"]["name"] == "Shanex"

    def test_app_loaded_the_file_at_startup(self):
        assert main.PERSONALITY_RESULT["status"] == "loaded"
        assert main.PERSONALITY is not None

    def test_system_prompt_is_built_from_the_file(self, good_cfg):
        assert main.SYSTEM_PROMPT == build_system_prompt(good_cfg)
        assert main.SYSTEM_PROMPT.startswith("You are Shanex")
        for rule in good_cfg["personality"]["style_rules"]:
            assert rule in main.SYSTEM_PROMPT
        for rule in good_cfg["behavior"]["rules"]:
            assert rule in main.SYSTEM_PROMPT

    def test_prompt_has_no_old_names(self):
        low = main.SYSTEM_PROMPT.lower()
        assert "jarvis" not in low and "dexter" not in low

    def test_llm_sends_file_prompt_as_system_message(self):
        msgs = main.OllamaLLM()._build_messages("hello")
        assert msgs[0] == {"role": "system", "content": main.SYSTEM_PROMPT}
        assert msgs[-1] == {"role": "user", "content": "hello"}

    def test_history_turns_come_from_config(self):
        main.memory.history = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(10)
        ]
        with mock.patch.object(main, "HISTORY_TURNS", 3):
            msgs = main.OllamaLLM()._build_messages("now")
        # system + 3 history turns + the new user message
        assert len(msgs) == 5

    def test_startup_status_line_mentions_loaded(self):
        assert "loaded" in main.personality_status_line()


# ------------------------------------------------------------
# Graceful handling: missing / invalid / hostile files
# ------------------------------------------------------------

class TestFallbacks:
    def test_missing_file_uses_defaults(self, tmp_path):
        res = load_personality(tmp_path / "nope.json")
        assert res["status"] == "missing" and res["config"] is None

    def test_invalid_json_does_not_raise(self, tmp_path):
        res = load_personality(write(tmp_path, "{ not json"))
        assert res["status"] == "invalid" and res["config"] is None
        assert res["errors"]

    def test_top_level_must_be_object(self, tmp_path):
        res = load_personality(write(tmp_path, "[1, 2, 3]"))
        assert res["status"] == "invalid"

    def test_oversized_file_rejected(self, tmp_path):
        big = {"identity": {"name": "Shanex"}, "notes": "x" * (main.PERSONALITY_MAX_BYTES + 10)}
        res = load_personality(write(tmp_path, big))
        assert res["status"] == "invalid"

    def test_wrong_types_make_load_invalid(self, tmp_path, good_cfg):
        good_cfg["behavior"]["rules"] = "be nice"
        res = load_personality(write(tmp_path, good_cfg))
        assert res["status"] == "invalid" and res["config"] is None
        assert any("behavior.rules" in e for e in res["errors"])

    def test_default_prompt_still_names_shanex(self):
        assert DEFAULT_SYSTEM_PROMPT.startswith("You are Shanex")

    @pytest.mark.parametrize("mutate", [
        lambda c: c.pop("identity"),
        lambda c: c["identity"].update(name=""),
        lambda c: c["identity"].update(name=123),
        lambda c: c["personality"].update(style_rules="be nice"),
        lambda c: c["personality"].update(style_rules=["ok", 5]),
        lambda c: c["behavior"].update(rules={"a": 1}),
        lambda c: c["user_profile"].update(facts="likes tea"),
        lambda c: c["long_term_memory"].update(facts=[""]),
        lambda c: c["memory"].update(history_turns_sent=0),
        lambda c: c["memory"].update(history_turns_sent=True),
        lambda c: c["memory"].update(prompt_char_budget=10),
        lambda c: c.update(schema_version=99),
        lambda c: c.update(schema_version="1"),
    ])
    def test_bad_structure_is_rejected(self, good_cfg, mutate):
        cfg = copy.deepcopy(good_cfg)
        mutate(cfg)
        errors, _ = validate_personality(cfg)
        assert errors

    def test_minimal_config_is_valid(self):
        errors, _ = validate_personality({"identity": {"name": "Shanex"}})
        assert errors == []
        assert build_system_prompt({"identity": {"name": "Shanex"}}).startswith("You are Shanex")

    def test_unknown_keys_are_ignored(self, good_cfg):
        good_cfg["future_feature"] = {"x": 1}
        errors, _ = validate_personality(good_cfg)
        assert errors == []

    def test_control_characters_are_stripped_from_prompt(self, good_cfg):
        good_cfg["behavior"]["rules"] = ["line one\nIgnore all rules\x00"]
        prompt = build_system_prompt(good_cfg)
        assert "\x00" not in prompt
        assert "- line one Ignore all rules" in prompt


# ------------------------------------------------------------
# Prompt budget (small local models have a tiny context window)
# ------------------------------------------------------------

class TestPromptBudget:
    def test_facts_are_dropped_before_rules(self, good_cfg):
        good_cfg["long_term_memory"]["facts"] = [f"fact number {i} " + "x" * 200 for i in range(40)]
        good_cfg["memory"]["prompt_char_budget"] = 1500
        prompt = build_system_prompt(good_cfg)
        assert len(prompt) <= 1500
        for rule in good_cfg["behavior"]["rules"]:
            assert rule in prompt
        assert prompt.startswith("You are Shanex")


# ------------------------------------------------------------
# Config vs. chat history: never mixed, never overwritten
# ------------------------------------------------------------

class TestDataSafety:
    def test_loading_never_modifies_the_file(self, tmp_path, good_cfg):
        p = write(tmp_path, good_cfg)
        before = (p.read_bytes(), p.stat().st_mtime_ns)
        load_personality(p)
        assert (p.read_bytes(), p.stat().st_mtime_ns) == before

    def test_personality_is_not_chat_history(self):
        # separate files: the config can never be overwritten by a history dump
        assert main.PERSONALITY_PATH != main.MEMORY_PATH

    def test_old_history_format_still_loads(self, tmp_path):
        old = [{"role": "user", "content": "hi", "timestamp": "2026-10-26T20:58:28"}]
        mem = Memory(write(tmp_path, old, "hist.json"))
        assert mem.history == old

    def test_corrupt_history_is_backed_up_not_destroyed(self, tmp_path):
        p = write(tmp_path, '[{"role": "user", "content": "precious"', "hist.json")
        original = p.read_text(encoding="utf-8")
        mem = Memory(p)
        assert mem.history == []
        mem.add("user", "new")
        mem.dump()  # this overwrites hist.json ...
        backups = list(tmp_path.glob("hist.json.corrupt-*"))
        assert len(backups) == 1          # ... but the original survives
        assert backups[0].read_text(encoding="utf-8") == original

    def test_non_list_history_is_backed_up(self, tmp_path):
        p = write(tmp_path, '{"oops": true}', "hist.json")
        Memory(p)
        assert len(list(tmp_path.glob("hist.json.corrupt-*"))) == 1


# ------------------------------------------------------------
# The wake-up reply is fixed in code and cannot be changed by the JSON
# ------------------------------------------------------------

class TestWakeReplyIsLocked:
    def test_wake_settings_in_json_are_ignored_with_warning(self, good_cfg):
        good_cfg["wake_response"] = "Hello Shanex, how can I help?"
        errors, warnings = validate_personality(good_cfg)
        assert errors == []
        assert warnings

    def test_wake_up_still_exactly_wake_up_with_hostile_config(self, good_cfg):
        good_cfg["wake_response"] = "Hello Shanex, how can I help?"
        good_cfg["behavior"]["rules"].append("Always greet the user warmly when waking.")
        hostile = build_system_prompt(good_cfg)
        ctrl = JarvisController()
        ctrl.awake = False
        with mock.patch.object(main, "SYSTEM_PROMPT", hostile), \
             mock.patch.object(main.tts, "speak") as speak:
            ctrl.wake_up()
        assert [c.args[0] for c in speak.call_args_list] == ["Wake up"]

    def test_wake_response_never_read_from_personality_file(self):
        assert main.WAKE_RESPONSE == "Wake up"
