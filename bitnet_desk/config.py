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

# keys of the [actions] table -> (type, check)
ACTION_KEYS = {
    "auto_approve": (list, lambda v: all(isinstance(x, str) for x in v)),
    "log": (bool, lambda v: True),
}
DEFAULT_AUTO_APPROVE = ("open_app", "open_path", "now")


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
        if key == "actions" and isinstance(value, dict):
            settings["actions"] = _load_actions(path, value, errors)
            continue
        if key == "aliases" and isinstance(value, dict):
            settings["aliases"] = _load_aliases(path, value, errors)
            continue
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


def _load_actions(path, table, errors):
    out = {}
    for key, value in table.items():
        if key not in ACTION_KEYS:
            errors.append(f"{path}: unknown setting 'actions.{key}' — ignored")
            continue
        typ, ok = ACTION_KEYS[key]
        if type(value) is not typ or not ok(value):
            errors.append(f"{path}: invalid value for 'actions.{key}': {value!r} — ignored")
            continue
        out[key] = value
    return out


def _load_aliases(path, table, errors):
    """[aliases.apps] "vs code" = "Visual Studio Code" → {"apps": {"vs code": "Visual Studio Code"}}"""
    out = {}
    for kind, mapping in table.items():
        if kind != "apps" or not isinstance(mapping, dict):
            errors.append(f"{path}: unknown setting 'aliases.{kind}' — ignored")
            continue
        apps = {}
        for alias, name in mapping.items():
            if isinstance(name, str) and name.strip():
                apps[" ".join(alias.lower().split())] = name.strip()
            else:
                errors.append(f"{path}: invalid value for 'aliases.apps.{alias}': {name!r} — ignored")
        out["apps"] = apps
    return out


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

# Actions (see docs/actions-design.md)
_ACTIONS = _FILE.get("actions", {})
AUTO_APPROVE = tuple(_ACTIONS.get("auto_approve", DEFAULT_AUTO_APPROVE))
ACTION_LOG_ENABLED = _ACTIONS.get("log", True)
ACTION_LOG = Path(os.environ.get("BITNET_ACTION_LOG") or CONFIG_PATH.parent / "actions.jsonl")
APP_ALIASES = _FILE.get("aliases", {}).get("apps", {})
# Where installed apps are looked up (one folder level deep, e.g. /Applications/Utilities).
APP_DIRS = tuple(os.environ["BITNET_APP_DIRS"].split(os.pathsep) if os.environ.get("BITNET_APP_DIRS")
                 else ("/Applications", "/System/Applications", "~/Applications"))

MAX_FILE_CHARS = 6000
SERVER_LOG = HERE / "server.log"
