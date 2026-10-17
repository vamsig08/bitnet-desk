"""Shared fixtures. A mock llama-server stands in for the real model; nothing touches the network."""
import http.server
import importlib
import json
import socket
import threading
from types import SimpleNamespace

import pytest

from bitnet_desk import config, llm


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def mock_tokens(text):
    """The mock tokenizer: one token per 4 chars (deliberately not the 3-char fallback estimate)."""
    return -(-len(text) // 4)


@pytest.fixture()
def reload_config():
    """Re-read config after changing env vars; server/llm look settings up at call time."""
    def reload():
        importlib.reload(config)
        llm._server_tokens.cache_clear()
        llm._total_slots.cache_clear()
    return reload


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch, reload_config):
    """Never read the developer's real config file or BITNET_* settings; no server by default."""
    monkeypatch.setenv("BITNET_CONFIG", str(tmp_path / "no-config.toml"))
    for env, *_ in config.FILE_KEYS.values():
        monkeypatch.delenv(env, raising=False)
    for env in ("BITNET_SERVER", "BITNET_MODEL", "BITNET_DIR", "BITNET_HOTKEY"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("BITNET_PORT", str(free_port()))   # nothing listens here
    monkeypatch.setenv("BITNET_ACTION_LOG", str(tmp_path / "actions.jsonl"))
    monkeypatch.setenv("BITNET_MISSES", str(tmp_path / "misses.jsonl"))
    monkeypatch.setenv("BITNET_APP_DIRS", str(tmp_path / "no-apps"))   # never scan the real /Applications
    monkeypatch.setenv("BITNET_RUNNING_APPS", "")                        # never ask macOS what's running,
    monkeypatch.setenv("BITNET_SHORTCUTS", "")                           # which Shortcuts exist,
    monkeypatch.setenv("BITNET_SCREENSHOT_DIR", str(tmp_path / "shots"))  # or where screenshots go
    reload_config()
    yield
    monkeypatch.undo()
    reload_config()


@pytest.fixture()
def mock_server(monkeypatch, reload_config):
    """A fake llama-server on 127.0.0.1 with /health, /tokenize and streaming /completion.

    Set `.reply` to change the streamed tokens, `.json_reply` (a dict, or a function of the request
    body) for non-streamed router calls, `.slots` for /props. `.seen` records /completion bodies and
    how many /tokenize calls were made.
    """
    ns = SimpleNamespace(port=free_port(), seen={"tokenize_calls": 0, "bodies": []},
                         reply=["Hel", "lo", " there"], timings={"predicted_per_second": 31.4},
                         slots=2, json_reply=None)

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            if self.path == "/props":
                self.wfile.write(json.dumps({"total_slots": ns.slots}).encode())
            else:
                self.wfile.write(b'{"status":"ok"}')

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/tokenize":
                ns.seen["tokenize_calls"] += 1
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"tokens": [0] * mock_tokens(body["content"])}).encode())
                return
            ns.seen["body"] = body
            ns.seen["bodies"].append(body)
            if not body.get("stream"):         # the action router: one JSON answer
                reply = ns.json_reply(body) if callable(ns.json_reply) else ns.json_reply
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(reply).encode())
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for tok in ns.reply:
                self.wfile.write(f"data: {json.dumps({'content': tok, 'stop': False})}\n\n".encode())
            final = {"content": "", "stop": True, "timings": ns.timings}
            self.wfile.write(f"data: {json.dumps(final)}\n\n".encode())

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", ns.port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("BITNET_PORT", str(ns.port))
    reload_config()
    yield ns
    srv.shutdown()
    srv.server_close()


@pytest.fixture()
def fake_mac(tmp_path, monkeypatch, reload_config):
    """The action eval's fake Mac: a few apps, a home folder with Documents/Downloads/Desktop."""
    from bitnet_desk.actions import evalkit
    for key, value in evalkit.fake_mac(tmp_path / "mac").items():
        monkeypatch.setenv(key, value)
    reload_config()
    return tmp_path / "mac"


@pytest.fixture()
def runner():
    """Stands in for subprocess.run: records argv lists, launches nothing. Set .returncode to fail."""
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=run.returncode, stdout="", stderr=run.stderr)
    run.calls, run.returncode, run.stderr = calls, 0, ""
    return run
