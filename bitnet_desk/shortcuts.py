"""The two Do Not Disturb Shortcuts: python3.12 -m bitnet_desk --setup-shortcuts

macOS has no public switch for Do Not Disturb, so the app runs two Shortcuts, each a single Set Focus
action. They're written here, signed with Apple's `shortcuts sign` (which asks Apple's signing service)
and opened; macOS only adds a shortcut after you click “Add Shortcut” in its preview — there's no
silent way. The signed files stay in Application Support, not the repo.
"""
import plistlib
import subprocess
import time

from . import config as cfg


def _shortcut(enabled):
    return {
        "WFWorkflowClientVersion": "2302.0.4", "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {"WFWorkflowIconStartColor": 463140863, "WFWorkflowIconGlyphNumber": 59511},
        "WFWorkflowImportQuestions": [], "WFWorkflowTypes": [], "WFWorkflowInputContentItemClasses": [],
        "WFWorkflowOutputContentItemClasses": [], "WFWorkflowHasOutputFallback": False,
        "WFWorkflowHasShortcutInputVariables": False, "WFQuickActionSurfaces": [],
        "WFWorkflowActions": [{
            "WFWorkflowActionIdentifier": "is.workflow.actions.dnd.set",
            "WFWorkflowActionParameters": {
                "Enabled": 1 if enabled else 0,
                "FocusModes": {"Identifier": "com.apple.donotdisturb.mode.default", "DisplayString": "Do Not Disturb"},
            },
        }],
    }


def _wait_for(pred, timeout, every):
    end = time.monotonic() + timeout
    while not pred():
        if time.monotonic() >= end:
            return False
        time.sleep(every)
    return True


def shortcut_files():
    """Write and sign the Shortcuts that are still missing; returns {name: signed file}. Signed files
    stay in Application Support (not the repo). Signing uses Apple's `shortcuts sign`."""
    from .actions.resolve import installed_shortcuts
    out_dir = cfg.APP_SUPPORT / "shortcuts"
    out_dir.mkdir(parents=True, exist_ok=True)
    have = installed_shortcuts() or []
    files = {}
    for name, on in ((cfg.DND_ON_SHORTCUT, True), (cfg.DND_OFF_SHORTCUT, False)):
        if name in have:
            continue
        signed = out_dir / f"{name}.shortcut"
        if not signed.exists():
            raw = out_dir / f"{name}.unsigned.shortcut"
            with open(raw, "wb") as f:
                plistlib.dump(_shortcut(on), f, fmt=plistlib.FMT_BINARY)
            for attempt in range(3):     # Apple's signing service sometimes answers "error 500"
                out = subprocess.run(["shortcuts", "sign", "--mode", "anyone", "--input", str(raw),
                                      "--output", str(signed)], capture_output=True, text=True, timeout=120)
                if out.returncode == 0 and signed.exists():
                    break
                time.sleep(3)
            raw.unlink()
            if not signed.exists():
                raise RuntimeError(f"couldn't sign {name}: {(out.stderr or out.stdout).strip()}")
        files[name] = signed
    return files


def setup_shortcuts(wait=180, open_files=True):
    """Make the two Do Not Disturb Shortcuts. macOS only adds a shortcut after you click “Add Shortcut”
    in its preview, so each file is opened and we wait for that click."""
    from .actions.resolve import installed_shortcuts
    files = shortcut_files()
    if not files:
        print("Both Do Not Disturb Shortcuts are already installed.")
        return True
    for name, path in files.items():
        print(f"Opening {path.name}: click “Add Shortcut”.", flush=True)
        if open_files:
            subprocess.run(["open", str(path)], check=True)
        if not _wait_for(lambda n=name: n in (installed_shortcuts() or []), wait, 2):
            print(f"“{name}” wasn't added within {wait} s — double-click {path} and click “Add Shortcut”.")
    missing = [n for n in files if n not in (installed_shortcuts() or [])]
    print("Both Do Not Disturb Shortcuts are installed." if not missing else f"Still missing: {', '.join(missing)}")
    return not missing
