"""Start, stop and health-check bitnet.cpp's llama-server (subprocess, localhost only)."""
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

from . import config as cfg


def server_up():
    try:
        with urllib.request.urlopen(cfg.BASE + "/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def find_server_bin():
    if cfg.SERVER_BIN:
        found = shutil.which(cfg.SERVER_BIN)
        return Path(found) if found else None
    for p in (cfg.BITNET_DIR / "build/bin/llama-server",
              cfg.BITNET_DIR / "build/bin/Release/llama-server.exe",
              cfg.BITNET_DIR / "build/bin/llama-server.exe"):
        if p.exists():
            return p
    return None


def supports_device_none(exe):
    """Newer llama-server can offload big batches to the GPU even with -ngl 0; `--device none` stops it."""
    try:
        out = subprocess.run([str(exe), "--help"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return any("--device" in line and "none" in line for line in (out.stdout + out.stderr).splitlines())


def server_cmd(exe):
    cmd = [str(exe), "-m", str(cfg.MODEL), "-c", str(cfg.CTX), "-t", str(cfg.THREADS),
           "-ngl", "0", "--host", cfg.HOST, "--port", str(cfg.PORT)]
    if supports_device_none(exe):
        cmd += ["--device", "none"]
    return cmd


def start_server(log_path):
    exe = find_server_bin()
    if exe is None:
        where = cfg.SERVER_BIN if cfg.SERVER_BIN else f"under {cfg.BITNET_DIR}/build/bin"
        raise RuntimeError(f"llama-server not found {where} — run setup first.")
    if not cfg.MODEL.exists():
        raise RuntimeError(f"Model not found: {cfg.MODEL} — run setup first.")
    log = open(log_path, "w")
    cmd = server_cmd(exe)
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, **kwargs)
