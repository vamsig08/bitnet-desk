"""Settings, read once at import.

Precedence: environment variables > config.toml > built-in defaults. The config file lives at
~/Library/Application Support/BitNetDesk/config.toml on macOS (override with BITNET_CONFIG).
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent      # project root


def default_config_path():
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "BitNetDesk" / "config.toml"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", Path.home())) / "BitNetDesk" / "config.toml"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "bitnet-desk" / "config.toml"


# key in config.toml -> (env var, type, check)
FILE_KEYS = {
    "threads": ("BITNET_THREADS", int, lambda v: v >= 1),
    "ctx": ("BITNET_CTX", int, lambda v: v >= 256),
    "max_tokens": ("BITNET_MAX_TOKENS", int, lambda v: v >= 1),
    "temperature": ("BITNET_TEMP", float, lambda v: v >= 0),
    "system_prompt": ("BITNET_SYSTEM", str, lambda v: bool(v.strip())),
}


def load_file(path):
    """Return (settings, errors). Bad keys are skipped; an unreadable file is ignored entirely."""
    if not path.exists():
        return {}, []
    try:
        import tomllib                                  # Python 3.11+
    except ImportError:
        return {}, [f"{path}: reading config.toml needs Python 3.11 or newer — ignored"]
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        return {}, [f"{path}: {e} — using defaults"]
    settings, errors = {}, []
    for key, value in data.items():
        if key not in FILE_KEYS:
            errors.append(f"{path}: unknown setting '{key}' — ignored")
            continue
        _, typ, ok = FILE_KEYS[key]
        if typ is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if type(value) is not typ or not ok(value):
            errors.append(f"{path}: invalid value for '{key}': {value!r} — ignored")
            continue
        settings[key] = value
    return settings, errors


CONFIG_PATH = Path(os.environ.get("BITNET_CONFIG") or default_config_path())
_FILE, CONFIG_ERRORS = load_file(CONFIG_PATH)


def _setting(key, default):
    env, typ, _ = FILE_KEYS[key]
    if env in os.environ:
        return typ(os.environ[env])
    return _FILE.get(key, default)


BITNET_DIR = Path(os.environ.get("BITNET_DIR", HERE / "BitNet"))
MODEL = Path(os.environ.get(
    "BITNET_MODEL",
    BITNET_DIR / "models" / "BitNet-b1.58-2B-4T" / "ggml-model-i2_s.gguf"))
# Any llama-server binary, e.g. mainline llama.cpp's for a TQ2_0 model. Default: the bitnet.cpp build.
SERVER_BIN = os.environ.get("BITNET_SERVER")
HOST = "127.0.0.1"                      # never 0.0.0.0 — keeps the model local-only
PORT = int(os.environ.get("BITNET_PORT", "8080"))
BASE = f"http://{HOST}:{PORT}"
CTX = _setting("ctx", 4096)
THREADS = _setting("threads", max(1, (os.cpu_count() or 4) // 2))
MAX_NEW_TOKENS = _setting("max_tokens", 768)
TEMPERATURE = _setting("temperature", 0.6)

SYSTEM_PROMPT = _setting("system_prompt", (
    "You are a helpful, concise assistant running entirely offline on the user's computer. "
    "Answer clearly. If you are not sure about a fact, say so instead of guessing."))

MAX_FILE_CHARS = 6000
SERVER_LOG = HERE / "server.log"
