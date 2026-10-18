"""Entry point: python -m bitnet_desk [--background] [--login on|off|status]"""
import argparse
import os

from . import config as cfg


def acquire_lock(path):
    """Hold an exclusive lock for the life of the process: one copy of the app at a time.
    Returns the open file descriptor, or None if another copy holds it."""
    try:
        import fcntl
    except ImportError:                 # Windows: no lock, as before
        return -1
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def main(argv=None):
    p = argparse.ArgumentParser(prog="python3.12 -m bitnet_desk")
    p.add_argument("--background", action="store_true",
                   help="start hidden: just the menu-bar icon and the hotkey (used at login)")
    p.add_argument("--login", choices=["on", "off", "status"], help="launch at login")
    p.add_argument("--setup-shortcuts", action="store_true",
                   help="make the two Do Not Disturb Shortcuts (you click “Add Shortcut” twice)")
    args = p.parse_args(argv)
    if args.setup_shortcuts:
        from .shortcuts import setup_shortcuts
        return 0 if setup_shortcuts() else 1
    if args.login:
        from . import login
        if args.login == "status":
            print(f"Launch at login is {'on' if login.is_enabled() else 'off'} ({login.plist_path()})")
        else:
            print(login.set_enabled(args.login == "on"))
        return 0
    lock = acquire_lock(cfg.LOCK_FILE)
    if lock is None:
        print("BitNet Desk is already running — use its hotkey or menu-bar icon.")
        return 1
    from .ui import run_app
    run_app(background=args.background)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
