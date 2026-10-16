"""llama-server discovery and command line: CPU-only, localhost-only."""
from bitnet_desk import config, server


def _fake_server(tmp_path, help_text):
    exe = tmp_path / "llama-server"
    exe.write_text(f"#!/bin/sh\ncat <<'EOF'\n{help_text}\nEOF\n")
    exe.chmod(0o755)
    return exe


def test_server_up(mock_server):
    assert server.server_up()


def test_server_down_when_nothing_listens():
    assert not server.server_up()


def test_server_binds_localhost_only():
    assert config.HOST == "127.0.0.1" and config.BASE.startswith("http://127.0.0.1:")


def test_bitnet_server_env_selects_binary(tmp_path, monkeypatch, reload_config):
    exe = _fake_server(tmp_path, "usage")
    monkeypatch.setenv("BITNET_SERVER", str(exe))
    reload_config()
    assert server.find_server_bin() == exe


def test_missing_bitnet_server_is_reported(tmp_path, monkeypatch, reload_config):
    monkeypatch.setenv("BITNET_SERVER", str(tmp_path / "nope"))
    reload_config()
    assert server.find_server_bin() is None


def test_server_cmd_is_cpu_only_and_local(tmp_path, monkeypatch, reload_config):
    monkeypatch.setenv("BITNET_MODEL", "/m.gguf")
    reload_config()
    new = server.server_cmd(_fake_server(tmp_path, "-dev,  --device <dev1,..>   (none = don't offload)"))
    assert new[new.index("-ngl") + 1] == "0" and new[new.index("--host") + 1] == "127.0.0.1"
    assert new[new.index("-m") + 1] == "/m.gguf"
    assert new[-2:] == ["--device", "none"]
    old = server.server_cmd(_fake_server(tmp_path, "-ngl N  number of layers"))   # bitnet.cpp's server
    assert "--device" not in old and old[old.index("-ngl") + 1] == "0"


def test_entry_points_import():
    import assistant
    from bitnet_desk import app, ui
    assert assistant.main is app.main and callable(ui.run_app)
