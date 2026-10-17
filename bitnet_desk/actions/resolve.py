"""Turn names the user typed into real things on this Mac (apps, folders, files) — or refuse."""
import difflib
import os
import re
import shutil
from pathlib import Path
from urllib.parse import unquote, urlsplit

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
    m = re.match(r"/Users/([^/]+)/(.+)", str(p))
    if not p.exists() and m and not Path("/Users", m.group(1)).exists():
        p = Path.home() / m.group(2)          # "/Users/username/Documents" (the model's placeholder)
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


# -- projects

def _project_key(text):
    return re.sub(r"[\s_-]+", "-", text.strip().lower())


def find_project(name):
    """(name, settings) of the configured project called `name` (or one of its aliases)."""
    key = _project_key(name)
    for pname, proj in cfg.PROJECTS.items():
        if key in {_project_key(pname)} | {_project_key(a) for a in proj.get("aliases", [])}:
            return pname, proj
    names = list(cfg.PROJECTS)
    if not names:
        raise ActionError("No projects are set up yet. Add one to config.toml, e.g. "
                          '[projects.myapp] with path = "~/code/myapp".')
    close = difflib.get_close_matches(key, [_project_key(n) for n in names], n=3, cutoff=0.6)
    raise ActionError(f"There's no project called “{name}”. Your projects: {', '.join(names)}.",
                      [n for n in names if _project_key(n) in close])


def editor_command(editor, folder):
    """`open -a <editor app> <folder>`; VS Code's `code` CLI only if the app itself isn't found."""
    try:
        return ("open", "-a", str(find_app(editor)), str(folder))
    except ActionError:
        code = shutil.which("code")
        if code and _key(editor) in ("visual studio code", "vs code", "vscode", "code"):
            return (code, str(folder))
        raise


# -- web addresses

# Things that look like domains but are file names ("notes.txt" is not a website).
FILE_EXTENSIONS = {"txt", "md", "pdf", "py", "js", "ts", "json", "csv", "doc", "docx", "xls", "xlsx",
                   "ppt", "pptx", "png", "jpg", "jpeg", "gif", "heic", "zip", "dmg", "pkg", "app", "sh",
                   "html", "htm", "log", "toml", "yaml", "yml", "rtf", "pages", "key", "numbers", "mov",
                   "mp3", "mp4", "wav", "c", "h", "cpp", "rs", "go", "java", "swift", "command"}
DOMAIN = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+([a-z]{2,})(?::\d+)?(?:[/?#]\S*)?", re.I)
SCHEME = re.compile(r"[a-z][a-z0-9+.-]*:", re.I)


def looks_like_url(text):
    t = text.strip().strip("<>\"'“”")
    if re.match(r"https?://", t, re.I):
        return True
    m = DOMAIN.fullmatch(t)
    return bool(m) and m.group(1).lower() not in FILE_EXTENSIONS


def normalise_url(text):
    """An http(s) URL, with https:// added to bare domains; anything else is refused."""
    t = text.strip().strip("<>\"'“”")
    if any(c.isspace() for c in t):
        raise ActionError(f"“{text}” isn't a web address.")
    if not re.match(r"https?://", t, re.I):
        if SCHEME.match(t) and not DOMAIN.fullmatch(t):
            raise ActionError("Only http and https web addresses can be opened.")
        if not looks_like_url(t):
            raise ActionError(f"“{text}” isn't a web address.")
        t = "https://" + t
    parts = urlsplit(t)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise ActionError(f"“{text}” isn't a web address.")
    if parts.username or parts.password:
        raise ActionError("I don't open web addresses with a user name or password in them "
                          "(a common phishing trick).")
    return t


def download_name(url):
    """A safe file name from the URL's path ('report.pdf'), or 'download'."""
    name = Path(unquote(urlsplit(url).path)).name
    name = re.sub(r"[\x00-\x1f/:\\]", "_", name).lstrip(".").strip()
    return name[:200] or "download"


def unique_path(p):
    """p, or 'name (1).ext', 'name (2).ext', … — never an existing file."""
    p = Path(p)
    n = 1
    candidate = p
    while candidate.exists() or candidate.with_name(candidate.name + ".part").exists():
        candidate = p.with_name(f"{p.stem} ({n}){p.suffix}")
        n += 1
    return candidate


# -- read-only lookups for the system tools (each has a test hook in config)

def is_running(app_name):
    """Is an app with this process name running? (`pgrep -x`, read-only.)"""
    if cfg.RUNNING_APPS is not None:
        return app_name in {a.strip() for a in cfg.RUNNING_APPS.split(",") if a.strip()}
    import subprocess
    try:
        return subprocess.run(["pgrep", "-xq", app_name], timeout=3).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def installed_shortcuts():
    """Names of the user's Shortcuts (`shortcuts list`, read-only), or None if that can't be asked."""
    if cfg.SHORTCUTS is not None:
        return [s for s in cfg.SHORTCUTS.split("\n") if s.strip()]
    import subprocess
    try:
        out = subprocess.run(["shortcuts", "list"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.splitlines() if out.returncode == 0 else None


def screenshot_dir():
    """Where macOS saves screenshots (com.apple.screencapture location), else the Desktop."""
    if cfg.SCREENSHOT_DIR is not None:
        return Path(cfg.SCREENSHOT_DIR)
    import subprocess
    try:
        out = subprocess.run(["defaults", "read", "com.apple.screencapture", "location"],
                             capture_output=True, text=True, timeout=5)
        loc = Path(out.stdout.strip()).expanduser() if out.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        loc = None
    return loc if loc and loc.is_dir() else Path.home() / "Desktop"
