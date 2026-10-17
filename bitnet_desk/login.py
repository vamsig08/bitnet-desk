"""Launch at login: a LaunchAgent in ~/Library/LaunchAgents that starts the app in the background.

No macOS permission is needed. macOS shows a one-time "Background item added" notice and lists the
item (as python3.12) under System Settings › General › Login Items. Turning it on takes effect at the
next login; it doesn't start a second copy now.
"""
import os
import plistlib
import sys
from pathlib import Path

from . import config as cfg

LABEL = "com.bitnetdesk.app"


def plist_path():
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def agent(python=None):
    """The LaunchAgent: this Python, run from the project folder, with only the menu bar and hotkey."""
    return {
        "Label": LABEL,
        "ProgramArguments": [python or sys.executable, "-m", "bitnet_desk", "--background"],
        "WorkingDirectory": str(cfg.HERE),
        "RunAtLoad": True,
        "ProcessType": "Interactive",
        # launchd's own PATH is minimal (/usr/bin:/bin:…); keep the one you have now for shell commands
        "EnvironmentVariables": {"PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")},
        "StandardOutPath": str(cfg.LOGIN_LOG),
        "StandardErrorPath": str(cfg.LOGIN_LOG),
    }


def is_enabled():
    return plist_path().exists()


def set_enabled(on):
    """Write or remove the LaunchAgent; logged like any other change to the Mac. Returns a message."""
    from .actions import Result
    from .actions import log
    path = plist_path()
    if on:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            plistlib.dump(agent(), f)
        message = "BitNet Desk will start in the background when you log in."
    else:
        path.unlink(missing_ok=True)
        message = "BitNet Desk won't start at login any more."
    log.record(f"launch at login {'on' if on else 'off'}", "menu", None, "approved",
               Result(True, message), tool="launch_at_login", args={"enabled": on, "plist": str(path)})
    return message
