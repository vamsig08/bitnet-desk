"""Starter tests for v0's model plumbing. Uses a mock llama-server — no real model needed."""
import http.server
import importlib.util
import json
import os
import socket
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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
            seen["body"] = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
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
    os.environ["BITNET_PORT"] = str(port)
    spec = importlib.util.spec_from_file_location("assistant", ROOT / "assistant.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


def test_history_trimming_keeps_latest_and_fits():
    a = load_app(_free_port())
    history = [("user", "x" * 5000), ("assistant", "y" * 5000)] * 3 + [("user", "latest")]
    fitted = a.fit_history("sys", history)
    assert fitted[-1] == ("user", "latest")
    assert len(a.build_prompt("sys", fitted)) <= a.MAX_PROMPT_CHARS
    huge = a.fit_history("sys", [("user", "z" * 50000)])
    assert len(a.build_prompt("sys", huge)) <= a.MAX_PROMPT_CHARS


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
