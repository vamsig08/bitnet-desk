"""config.toml loading and precedence (env > file > defaults)."""
import os
from pathlib import Path

import pytest

from bitnet_desk import config, llm


@pytest.fixture()
def write_config(tmp_path, monkeypatch, reload_config):
    def write(text):
        path = tmp_path / "config.toml"
        path.write_text(text, encoding="utf-8")
        monkeypatch.setenv("BITNET_CONFIG", str(path))
        reload_config()
        return path
    return write


def test_config_file_sets_values(write_config):
    write_config('''
threads = 3
ctx = 2048
max_tokens = 256
temperature = 1          # ints are fine for floats
system_prompt = "You are terse."
''')
    assert (config.THREADS, config.CTX, config.MAX_NEW_TOKENS) == (3, 2048, 256)
    assert config.TEMPERATURE == 1.0 and isinstance(config.TEMPERATURE, float)
    assert llm.system_prompt().startswith("You are terse.\nToday is ")
    assert config.CONFIG_ERRORS == []


def test_env_vars_override_config_file(write_config, monkeypatch):
    monkeypatch.setenv("BITNET_THREADS", "6")
    write_config("threads = 3\ntemperature = 0.2\n")
    assert config.THREADS == 6 and config.TEMPERATURE == 0.2


def test_missing_config_file_uses_defaults():
    assert (config.CTX, config.MAX_NEW_TOKENS, config.TEMPERATURE) == (4096, 768, 0.6)
    assert config.CONFIG_ERRORS == []


def test_bad_keys_are_skipped_and_reported(write_config):
    write_config('threads = "four"\nctx = 2048\ncolour = "blue"\ntemperature = -1\n')
    assert config.CTX == 2048 and config.TEMPERATURE == 0.6
    assert config.THREADS == max(1, (os.cpu_count() or 4) // 2)
    errs = " ".join(config.CONFIG_ERRORS)
    assert "'threads'" in errs and "'colour'" in errs and "'temperature'" in errs and "'ctx'" not in errs


def test_unparseable_config_file_falls_back_to_defaults(write_config):
    write_config("threads = = 3\n")
    assert config.CTX == 4096 and len(config.CONFIG_ERRORS) == 1


def test_default_config_path_on_macos(monkeypatch):
    monkeypatch.setattr(config.sys, "platform", "darwin")
    assert config.default_config_path() == (
        Path.home() / "Library" / "Application Support" / "BitNetDesk" / "config.toml")


def test_actions_and_aliases_tables(write_config):
    write_config('''
[actions]
auto_approve = ["now"]
log = false

[aliases.apps]
"VS  Code" = "Visual Studio Code"
''')
    assert config.AUTO_APPROVE == ("now",) and config.ACTION_LOG_ENABLED is False
    assert config.APP_ALIASES == {"vs code": "Visual Studio Code"}
    assert config.CONFIG_ERRORS == []


def test_actions_defaults():
    assert config.AUTO_APPROVE == ("open_app", "open_path", "now") and config.ACTION_LOG_ENABLED


def test_bad_actions_settings_are_skipped(write_config):
    write_config('''
[actions]
auto_approve = "open_app"
colour = 1

[aliases.apps]
x = 3

[aliases.files]
y = "z"
''')
    assert config.AUTO_APPROVE == config.DEFAULT_AUTO_APPROVE
    errs = " ".join(config.CONFIG_ERRORS)
    assert "'actions.auto_approve'" in errs and "'actions.colour'" in errs
    assert "'aliases.apps.x'" in errs and "'aliases.files'" in errs
