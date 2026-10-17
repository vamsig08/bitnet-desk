"""macOS glue via ctypes (no extra packages): a global hotkey and app activation.

The hotkey uses Carbon's RegisterEventHotKey: macOS reports only that one key combination, so it
needs no Accessibility or Input Monitoring permission (unlike keyboard listeners, which see every key).
"""
import ctypes
import json
import os
import re
import sys
from ctypes import CFUNCTYPE, POINTER, Structure, byref, c_bool, c_int32, c_uint32, c_void_p
from dataclasses import dataclass

# Carbon virtual key codes (positions on an ANSI keyboard)
KEY_CODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12,
    "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23,
    "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34,
    "p": 35, "return": 36, "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44,
    "n": 45, "m": 46, ".": 47, "tab": 48, "space": 49, "`": 50, "escape": 53,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100, "f9": 101,
    "f10": 109, "f11": 103, "f12": 111, "left": 123, "right": 124, "down": 125, "up": 126,
}
KEY_NAMES = {"space": "Space", "return": "↩", "tab": "⇥", "escape": "Esc", "left": "←", "right": "→",
             "down": "↓", "up": "↑"}
KEY_ALIASES = {"enter": "return", "esc": "escape", "spacebar": "space"}
# Carbon modifier bits, in the order macOS displays them
MODIFIERS = (("ctrl", 0x1000, "⌃"), ("alt", 0x0800, "⌥"), ("shift", 0x0200, "⇧"), ("cmd", 0x0100, "⌘"))
MOD_ALIASES = {"ctrl": "ctrl", "control": "ctrl", "⌃": "ctrl", "alt": "alt", "opt": "alt", "option": "alt",
               "⌥": "alt", "shift": "shift", "⇧": "shift", "cmd": "cmd", "command": "cmd", "⌘": "cmd"}
SYMBOLS = "⌃⌥⇧⌘"
HOTKEY_TAKEN = -9878                    # eventHotKeyExistsErr: another app holds this combination


@dataclass(frozen=True)
class HotKey:
    code: int
    modifiers: int
    label: str          # e.g. "⌥Space"


def parse_hotkey(text):
    """'alt+space', '⌥Space', 'cmd+shift+k', 'ctrl-alt-b', 'f5' → HotKey. Raises ValueError."""
    s = text.strip()
    parts = []
    while s and s[0] in SYMBOLS:            # "⌥⇧Space"
        parts.append(s[0])
        s = s[1:].lstrip("+ ")
    # "+" separates; so does "-" between words ("ctrl-alt-b"), but "-" alone is a key
    parts += [p for p in re.split(r"\s*\+\s*|(?<=\w)-(?=\w)", s) if p.strip()] if len(s) > 1 else [s] * bool(s)
    if not parts:
        raise ValueError("empty hotkey")
    *mods, key = [p.strip().lower() for p in parts]
    key = KEY_ALIASES.get(key, key)
    if key not in KEY_CODES:
        raise ValueError(f"unknown key {key!r} in hotkey {text!r}")
    names = set()
    for m in mods:
        if m not in MOD_ALIASES:
            raise ValueError(f"unknown modifier {m!r} in hotkey {text!r}")
        names.add(MOD_ALIASES[m])
    if not names and not key.startswith("f"):
        raise ValueError(f"hotkey {text!r} needs a modifier (⌥, ⌘, ⌃ or ⇧) — a bare key would eat your typing")
    if names == {"shift"} and not key.startswith("f"):
        raise ValueError(f"hotkey {text!r} would eat capital letters — add ⌥, ⌘ or ⌃")
    bits = sum(bit for name, bit, _ in MODIFIERS if name in names)
    label = "".join(sym for name, _, sym in MODIFIERS if name in names)
    label += KEY_NAMES.get(key, key.upper())
    return HotKey(KEY_CODES[key], bits, label)


# -- clashes. macOS doesn't refuse a combination another app registered — both get every press —
# so we look for the likely ones ourselves: macOS's own shortcuts and ChatGPT's launcher.

# macOS defaults (id in com.apple.symbolichotkeys → combination); listed there only if changed
SYSTEM_SHORTCUTS = {64: ("cmd+space", "Spotlight"), 65: ("alt+cmd+space", "Finder search"),
                    60: ("ctrl+space", "previous input source"), 61: ("ctrl+alt+space", "next input source"),
                    28: ("shift+cmd+3", "screenshot"), 30: ("shift+cmd+4", "screenshot of an area"),
                    184: ("shift+cmd+5", "screenshot options")}
# Cocoa modifier flags (symbolichotkeys) → Carbon bits
COCOA_MODS = {0x20000: 0x0200, 0x40000: 0x1000, 0x80000: 0x0800, 0x100000: 0x0100}


def clashes(hotkey, symbolic=None, chatgpt=None):
    """Names of things that already use `hotkey`. `symbolic`: the AppleSymbolicHotKeys dict;
    `chatgpt`: ChatGPT's preferences (both as read by read_prefs)."""
    found = []
    symbolic = symbolic or {}
    for sid, (combo, name) in SYSTEM_SHORTCUTS.items():
        entry = symbolic.get(str(sid))
        if entry is not None and not entry.get("enabled"):
            continue
        if entry is not None and entry.get("value", {}).get("parameters"):
            _, code, flags = entry["value"]["parameters"][:3]
            key = HotKey(code, sum(c for f, c in COCOA_MODS.items() if flags & f), "")
        else:
            key = parse_hotkey(combo)
        if (key.code, key.modifiers) == (hotkey.code, hotkey.modifiers):
            found.append(f"macOS ({name})")
    for pref in ("KeyboardShortcuts_toggleLauncher", "KeyboardShortcuts_toggleAttachedLauncher"):
        try:
            value = json.loads((chatgpt or {}).get(pref, "null")) or {}
        except (TypeError, ValueError):
            continue
        if (value.get("carbonKeyCode"), value.get("carbonModifiers")) == (hotkey.code, hotkey.modifiers):
            found.append("ChatGPT (its launcher shortcut)")
    return found


def read_prefs(domain):
    """A preferences domain as a dict (via `defaults export`, read-only), or {}."""
    import plistlib
    import subprocess
    if sys.platform != "darwin":
        return {}
    try:
        out = subprocess.run(["defaults", "export", domain, "-"], capture_output=True, timeout=5)
        return plistlib.loads(out.stdout) if out.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, plistlib.InvalidFileException, ValueError):
        return {}


def find_clashes(hotkey):
    return clashes(hotkey, read_prefs("com.apple.symbolichotkeys").get("AppleSymbolicHotKeys", {}),
                   read_prefs("com.openai.chat"))


# -- Carbon hotkey (macOS only)

class _Spec(Structure):
    _fields_ = [("cls", c_uint32), ("kind", c_uint32)]


class _HotKeyID(Structure):
    _fields_ = [("sig", c_uint32), ("id", c_uint32)]


_HANDLER = CFUNCTYPE(c_int32, c_void_p, c_void_p, c_void_p)
_live = []                              # keep ctypes callbacks alive as long as the process


def _fourcc(s):
    return int.from_bytes(s.encode(), "big")


class GlobalHotKey:
    """Calls `callback()` whenever the hotkey is pressed, in any app.

    The callback runs inside Tk's event loop while Tcl is busy: it must not call Tk (that aborts
    Python with a GIL error). Hand the press on instead, e.g. put it on a queue the app polls."""

    def __init__(self, hotkey, callback):
        self.hotkey, self.callback = hotkey, callback
        self.ref = None

    def register(self):
        """'ok', 'taken' (refused by macOS), 'unsupported' (not macOS), or 'error <code>'.
        'ok' doesn't mean nobody else uses it — see find_clashes."""
        if sys.platform != "darwin":
            return "unsupported"
        try:
            carbon = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/Carbon.framework/Carbon")
        except OSError:
            return "unsupported"
        carbon.GetApplicationEventTarget.restype = c_void_p
        target = carbon.GetApplicationEventTarget()

        @_HANDLER
        def handler(call, event, data):
            try:
                self.callback()
            except Exception:           # never let a Python error unwind into Carbon
                pass
            return 0
        _live.append(handler)
        carbon.InstallEventHandler.argtypes = [c_void_p, _HANDLER, c_uint32, POINTER(_Spec), c_void_p,
                                               POINTER(c_void_p)]
        spec = _Spec(_fourcc("keyb"), 5)                      # kEventHotKeyPressed
        err = carbon.InstallEventHandler(target, handler, 1, byref(spec), None, byref(c_void_p()))
        if err:
            return f"error {err}"
        ref = c_void_p()
        carbon.RegisterEventHotKey.argtypes = [c_uint32, c_uint32, _HotKeyID, c_void_p, c_uint32,
                                               POINTER(c_void_p)]
        err = carbon.RegisterEventHotKey(self.hotkey.code, self.hotkey.modifiers,
                                         _HotKeyID(_fourcc("BNDK"), 1), target, 0, byref(ref))
        if err == HOTKEY_TAKEN:
            return "taken"
        if err:
            return f"error {err}"
        self.ref, self._carbon = ref, carbon
        return "ok"

    def unregister(self):
        if self.ref:
            self._carbon.UnregisterEventHotKey.argtypes = [c_void_p]
            self._carbon.UnregisterEventHotKey(self.ref)
            self.ref = None


# -- app activation (Objective-C runtime via ctypes)

def _objc():
    objc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
    ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AppKit.framework/AppKit")
    objc.objc_getClass.restype = c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    return objc


def _send(objc, obj, selector, *args, restype=c_void_p, argtypes=()):
    fn = ctypes.cast(objc.objc_msgSend, CFUNCTYPE(restype, c_void_p, c_void_p, *argtypes))
    return fn(obj, objc.sel_registerName(selector.encode()), *args)


def frontmost_pid():
    """PID of the app in front, or None."""
    if sys.platform != "darwin":
        return None
    try:
        objc = _objc()
        ws = _send(objc, objc.objc_getClass(b"NSWorkspace"), "sharedWorkspace")
        app = _send(objc, ws, "frontmostApplication")
        return _send(objc, app, "processIdentifier", restype=c_int32) if app else None
    except (OSError, AttributeError):
        return None


def activate(pid=None):
    """Bring an app to the front: ours by default (so the command box gets the keyboard)."""
    if sys.platform != "darwin":
        return
    try:
        objc = _objc()
        if pid is None or pid == os.getpid():
            app = _send(objc, objc.objc_getClass(b"NSApplication"), "sharedApplication")
            # macOS 14+: `activate`; the older call is often ignored there under "cooperative activation"
            if _send(objc, app, "respondsToSelector:", objc.sel_registerName(b"activate"),
                     restype=c_bool, argtypes=(c_void_p,)):
                _send(objc, app, "activate", restype=None)
            _send(objc, app, "activateIgnoringOtherApps:", True, restype=None, argtypes=(c_bool,))
            return
        other = _send(objc, objc.objc_getClass(b"NSRunningApplication"),
                      "runningApplicationWithProcessIdentifier:", pid, argtypes=(c_int32,))
        if other:
            _send(objc, other, "activateWithOptions:", 0, restype=c_bool, argtypes=(ctypes.c_ulong,))
    except (OSError, AttributeError):
        pass
