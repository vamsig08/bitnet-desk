"""Tests for the model plumbing. Uses a mock llama-server — no real model needed."""
import http.server
import importlib
import json
import os
import socket
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from bitnet_desk import config, llm, server


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Never read the developer's real config file or BITNET_* settings."""
    monkeypatch.setenv("BITNET_CONFIG", str(tmp_path / "no-config.toml"))
    for env, *_ in config.FILE_KEYS.values():
        monkeypatch.delenv(env, raising=False)
    yield
    monkeypatch.undo()
    importlib.reload(config)


def mock_tokens(text):
    return -(-len(text) // 4)


@pytest.fixture()
def mock_server():
    port = _free_port()
    seen = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/tokenize":        # mock tokenizer: one token per 4 chars
                seen["tokenize_calls"] = seen.get("tokenize_calls", 0) + 1
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"tokens": [0] * mock_tokens(body["content"])}).encode())
                return
            seen["body"] = body
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for tok in ["Hel", "lo", " there"]:
                self.wfile.write(f"data: {json.dumps({'content': tok, 'stop': False})}\n\n".encode())
            final = {"content": "", "stop": True, "timings": {"predicted_per_second": 31.4}}
            self.wfile.write(f"data: {json.dumps(final)}\n\n".encode())

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield port, seen
    srv.shutdown()


def load_app(port):
    """Re-read config from the environment; server/llm look settings up at call time."""
    os.environ["BITNET_PORT"] = str(port)
    importlib.reload(config)
    return SimpleNamespace(**{n: getattr(m, n) for m in (config, server, llm)
                              for n in dir(m) if not n.startswith("_")})


def test_prompt_format():
    a = load_app(_free_port())
    assert a.build_prompt("sys", [("user", "hi")]) == "System: sys<|eot_id|>User: hi<|eot_id|>Assistant: "


def test_streaming_and_timings(mock_server):
    port, seen = mock_server
    a = load_app(port)
    assert a.server_up()
    toks = []
    t = a.stream_completion(a.build_prompt("sys", [("user", "hi")]), toks.append, threading.Event())
    assert "".join(toks) == "Hello there"
    assert t["predicted_per_second"] == 31.4
    assert seen["body"]["stream"] is True and "<|eot_id|>" in seen["body"]["stop"]


def test_server_binds_localhost_only():
    a = load_app(_free_port())
    assert a.HOST == "127.0.0.1"


def _prompt_tokens(count, system, history):
    return 1 + count(llm.build_prompt(system, history))


def test_count_tokens_uses_server_tokenizer(mock_server):
    port, seen = mock_server
    load_app(port)
    assert llm.count_tokens("x" * 40) == 10              # mock: 4 chars/token, not the 3-char estimate
    assert llm.count_tokens("x" * 40) == 10 and seen["tokenize_calls"] == 1   # cached


def test_count_tokens_falls_back_to_estimate_when_server_down():
    load_app(_free_port())
    assert llm.count_tokens("x" * 30) == llm.estimate_tokens("x" * 30) == 11


@pytest.mark.parametrize("use_server", [True, False])
def test_history_trimming_keeps_latest_and_fits(request, use_server):
    port = request.getfixturevalue("mock_server")[0] if use_server else _free_port()
    load_app(port)
    count = (lambda t: mock_tokens(t)) if use_server else llm.estimate_tokens
    budget = config.CTX - config.MAX_NEW_TOKENS
    history = [("user", "x" * 3000), ("assistant", "y" * 3000)] * 3 + [("user", "latest")]
    fitted = llm.fit_history("sys", history)
    assert fitted[-1] == ("user", "latest")
    assert _prompt_tokens(count, "sys", fitted) <= budget
    assert 2 < len(fitted) < len(history)                 # trimmed, but the budget is used
    huge = llm.fit_history("sys", [("user", "a" * 25000 + "TAIL")])
    assert huge[0][1].endswith("TAIL")
    assert budget - 50 <= _prompt_tokens(count, "sys", huge) <= budget


def test_short_history_is_untouched(mock_server):
    load_app(mock_server[0])
    history = [("user", "hi"), ("assistant", "hello"), ("user", "how are you?")]
    assert llm.fit_history("sys", history) == history


def _fake_server(tmp_path, help_text):
    exe = tmp_path / "llama-server"
    exe.write_text(f"#!/bin/sh\ncat <<'EOF'\n{help_text}\nEOF\n")
    exe.chmod(0o755)
    return exe


def test_bitnet_server_env_selects_binary(tmp_path, monkeypatch):
    exe = _fake_server(tmp_path, "usage")
    monkeypatch.setenv("BITNET_SERVER", str(exe))
    a = load_app(_free_port())
    assert a.find_server_bin() == exe


def test_missing_bitnet_server_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("BITNET_SERVER", str(tmp_path / "nope"))
    a = load_app(_free_port())
    assert a.find_server_bin() is None


def test_server_cmd_is_cpu_only_and_local(tmp_path, monkeypatch):
    monkeypatch.setenv("BITNET_MODEL", "/m.gguf")
    a = load_app(_free_port())
    new = a.server_cmd(_fake_server(tmp_path, "-dev,  --device <dev1,..>   (none = don't offload)"))
    assert new[new.index("-ngl") + 1] == "0" and new[new.index("--host") + 1] == "127.0.0.1"
    assert new[new.index("-m") + 1] == "/m.gguf"
    assert new[-2:] == ["--device", "none"]
    old = a.server_cmd(_fake_server(tmp_path, "-ngl N  number of layers"))   # bitnet.cpp's server
    assert "--device" not in old and old[old.index("-ngl") + 1] == "0"


def test_entry_points_import():
    import assistant
    from bitnet_desk import app, ui
    assert assistant.main is app.main and callable(ui.run_app)


def test_system_prompt_has_date_and_weekday_but_not_time():
    a = load_app(_free_port())
    now = datetime(2026, 10, 16, 9, 5, tzinfo=timezone(timedelta(hours=1), "BST"))
    assert a.system_prompt(now) == config.SYSTEM_PROMPT + "\nToday is Friday, 16 October 2026."
    assert a.time_note(now) == "\n\n(Local time: 09:05 BST)"


def _clock(monkeypatch, *times):
    it = iter(times)
    monkeypatch.setattr(llm, "_now", lambda: next(it))


def test_time_goes_on_newest_message_only_and_not_into_history(monkeypatch):
    load_app(_free_port())
    _clock(monkeypatch, datetime(2026, 10, 16, 23, 59), datetime(2026, 10, 17, 0, 1))
    history = [("user", "hi"), ("assistant", "hello"), ("user", "what time is it?")]
    fitted, first = llm.prepare(history)
    assert "Today is Friday, 16 October 2026." in first
    assert first.endswith("User: what time is it?\n\n(Local time: 23:59)<|eot_id|>Assistant: ")
    assert first.count("Local time") == 1
    assert fitted == history                              # stored history stays clean
    _, second = llm.prepare(history)                      # fresh on every call
    assert "Today is Saturday, 17 October 2026." in second and "(Local time: 00:01)" in second


def test_cached_prefix_is_identical_between_sends_a_minute_apart(monkeypatch):
    load_app(_free_port())
    _clock(monkeypatch, datetime(2026, 10, 16, 9, 0), datetime(2026, 10, 16, 9, 1))
    history = [("user", "hi"), ("assistant", "hello")] * 3 + [("user", "first question")]
    history, first = llm.prepare(history)
    history += [("assistant", "an answer"), ("user", "follow-up")]
    _, second = llm.prepare(history)
    # Everything before the message the first send stamped is byte-identical in the second send,
    # so llama-server's cache_prompt can reuse it.
    old_turns = first[:first.rindex("User: first question")]
    assert second.startswith(old_turns)
    assert "first question<|eot_id|>" in second           # unstamped once it is no longer newest
    assert second.count("Local time") == 1 and "(Local time: 09:01)" in second


def test_time_note_survives_trimming_of_a_huge_message(monkeypatch):
    load_app(_free_port())
    _clock(monkeypatch, datetime(2026, 10, 16, 9, 0))
    fitted, prompt = llm.prepare([("user", "a" * 25000 + "TAIL")])
    assert prompt.endswith("TAIL\n\n(Local time: 09:00)<|eot_id|>Assistant: ")
    assert fitted[0][1].endswith("TAIL")


def _write_config(tmp_path, monkeypatch, text):
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setenv("BITNET_CONFIG", str(path))
    return path


def test_config_file_sets_values(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, '''
threads = 3
ctx = 2048
max_tokens = 256
temperature = 1          # ints are fine for floats
system_prompt = "You are terse."
''')
    load_app(_free_port())
    assert (config.THREADS, config.CTX, config.MAX_NEW_TOKENS) == (3, 2048, 256)
    assert config.TEMPERATURE == 1.0 and isinstance(config.TEMPERATURE, float)
    assert llm.system_prompt().startswith("You are terse.\nToday is ")
    assert config.CONFIG_ERRORS == []


def test_env_vars_override_config_file(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, "threads = 3\ntemperature = 0.2\n")
    monkeypatch.setenv("BITNET_THREADS", "6")
    load_app(_free_port())
    assert config.THREADS == 6 and config.TEMPERATURE == 0.2


def test_missing_config_file_uses_defaults():
    load_app(_free_port())
    assert (config.CTX, config.MAX_NEW_TOKENS, config.TEMPERATURE) == (4096, 768, 0.6)
    assert config.CONFIG_ERRORS == []


def test_bad_keys_are_skipped_and_reported(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, 'threads = "four"\nctx = 2048\ncolour = "blue"\ntemperature = -1\n')
    load_app(_free_port())
    assert config.CTX == 2048 and config.TEMPERATURE == 0.6
    assert config.THREADS == max(1, (os.cpu_count() or 4) // 2)
    errs = " ".join(config.CONFIG_ERRORS)
    assert "'threads'" in errs and "'colour'" in errs and "'temperature'" in errs and "'ctx'" not in errs


def test_unparseable_config_file_falls_back_to_defaults(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, "threads = = 3\n")
    load_app(_free_port())
    assert config.CTX == 4096 and len(config.CONFIG_ERRORS) == 1


def test_default_config_path_on_macos(monkeypatch):
    monkeypatch.setattr(config.sys, "platform", "darwin")
    assert config.default_config_path() == (
        Path.home() / "Library" / "Application Support" / "BitNetDesk" / "config.toml")
