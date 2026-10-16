"""Turn names the user typed into real things on this Mac (apps, folders, files) — or refuse."""
import difflib
import os
from pathlib import Path

from .. import config as cfg
from .registry import ActionError, Risk

DEFAULT_APP_ALIASES = {
    "vs code": "Visual Studio Code",
    "vscode": "Visual Studio Code",
    "code": "Visual Studio Code",
    "chrome": "Google Chrome",
    "settings": "System Settings",
    "system preferences": "System Settings",
    "zoom": "zoom.us",
    "word": "Microsoft Word",
    "teams": "Microsoft Teams",
}

KNOWN_FOLDERS = {
    "home": "~",
    "desktop": "~/Desktop",
    "documents": "~/Documents",
    "downloads": "~/Downloads",
    "pictures": "~/Pictures",
    "music": "~/Music",
    "movies": "~/Movies",
    "applications": "/Applications",
}

# Opening these runs code, so open_path treats them as shell risk.
RUNS_CODE = {".app", ".command", ".sh", ".tool", ".terminal", ".workflow", ".action", ".scpt",
             ".scptd", ".applescript", ".pkg", ".mpkg", ".jar"}


def _key(text):
    return " ".join(text.lower().split())


def installed_apps():
    """{lowercase name: path} for .app bundles in cfg.APP_DIRS and one folder level below them."""
    apps = {}
    for d in cfg.APP_DIRS:
        d = Path(d).expanduser()
        try:
            entries = sorted(d.iterdir())
        except OSError:
            continue
        for e in entries:
            if e.suffix == ".app":
                apps.setdefault(e.stem.lower(), e)
            elif e.is_dir() and not e.name.startswith("."):
                try:
                    apps.update({s.stem.lower(): s for s in sorted(e.iterdir())
                                 if s.suffix == ".app" and s.stem.lower() not in apps})
                except OSError:
                    pass
    return apps


def find_app(name, cutoff=0.9):
    """Path of the installed app called `name` (alias, exact, or a very close spelling)."""
    apps = installed_apps()
    key = _key(name).removesuffix(".app")
    aliases = {**DEFAULT_APP_ALIASES, **cfg.APP_ALIASES}
    key = _key(aliases.get(key, key))
    if key in apps:
        return apps[key]
    close = difflib.get_close_matches(key, apps, n=3, cutoff=0.6)
    if close and difflib.SequenceMatcher(None, key, close[0]).ratio() >= cutoff:
        return apps[close[0]]
    raise ActionError(f"I can't find an app called “{name}”.", [apps[c].stem for c in close])


def find_path(text):
    """An existing file or folder: a known folder name ("downloads") or a ~ / absolute path."""
    raw = text.strip().strip("\"'“”‘’")
    key = _key(raw)
    if key in KNOWN_FOLDERS:
        p = Path(KNOWN_FOLDERS[key]).expanduser()
    elif raw.startswith(("~", "/")):
        p = Path(raw).expanduser()
    else:
        raise ActionError(f"“{text}” isn't a path I can open.")
    if not p.exists():
        raise ActionError(f"{tilde(p)} doesn't exist.")
    return p


def path_risk(p):
    if p.suffix.lower() in RUNS_CODE or (p.is_file() and os.access(p, os.X_OK)):
        return Risk.SHELL
    return Risk.CHANGES


def tilde(p):
    """/Users/me/Downloads → ~/Downloads, for display."""
    p, home = Path(p), Path.home()
    try:
        rel = p.relative_to(home)
    except ValueError:
        return str(p)
    return "~" if rel == Path(".") else f"~/{rel}"
