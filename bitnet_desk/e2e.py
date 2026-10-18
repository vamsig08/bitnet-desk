"""Real-Mac end-to-end harness: python3.12 -m bitnet_desk.e2e

Runs commands through the real pipeline (fast path, router, permission gate, tool) and checks the
real effect on this Mac: volume, Spotify's player state, the screenshot file, project windows, Do Not
Disturb, the command box and menu-bar icon of a real (in-process) app. It never sends synthetic
keystrokes or clicks; the app is driven by calling its own methods, and approving a card is the same
call the Run button makes. Each area saves the state it touches and restores it afterwards. Lock screen
and empty Trash are never run, only planned. Results go to docs/e2e-report.md.

    python3.12 -m bitnet_desk.e2e                     # everything
    python3.12 -m bitnet_desk.e2e --only volume,media # some areas
    python3.12 -m bitnet_desk.e2e --permissions       # which macOS permissions are there / would ask
    python3.12 -m bitnet_desk.e2e --ask-permissions   # show the missing prompts now (click Allow)
    python3.12 -m bitnet_desk.e2e --setup-shortcuts   # make the two Do Not Disturb Shortcuts

Reading state uses Apple events to Spotify/Terminal/System Events and Accessibility (read-only: window
titles, menu-bar items), all granted to the app that started the harness (usually Terminal).
"""
import argparse
import dataclasses
import os
import platform
import plistlib
import re
import struct
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

from . import config as cfg

REPORT = cfg.HERE / "docs" / "e2e-report.md"
SPOTIFY, MUSIC, FINDER = "com.spotify.client", "com.apple.Music", "com.apple.finder"
CODE, TERMINAL, SYSTEM_EVENTS = "com.microsoft.VSCode", "com.apple.Terminal", "com.apple.systemevents"
# Something for Spotify to play when it has no current track (Spotify's public "Deep Focus" list).
SEED_PLAYLIST = "spotify:playlist:37i9dQZF1DWZeKCadgRdKQ"
AREAS = ("permissions", "volume", "media", "screenshot", "project", "dnd", "always_ask", "app", "routine")
NEVER_RUN = ("lock_screen", "empty_trash")


# -- bookkeeping

@dataclasses.dataclass
class Check:
    area: str
    name: str
    status: str             # pass | fail | skip
    detail: str = ""


class Run:
    def __init__(self):
        self.checks, self.restored, self.needs_you = [], [], []

    def check(self, area, name, ok, detail=""):
        self.checks.append(Check(area, name, "pass" if ok else "fail", str(detail)))
        print(f"  {'✓' if ok else '✗'} [{area}] {name}" + (f" — {detail}" if detail else ""), flush=True)
        return ok

    def skip(self, area, name, why):
        self.checks.append(Check(area, name, "skip", why))
        print(f"  – [{area}] {name} — skipped: {why}", flush=True)

    def restore(self, area, what):
        self.restored.append((area, what))

    def counts(self):
        return {s: sum(c.status == s for c in self.checks) for s in ("pass", "fail", "skip")}


# -- reading and setting state

def osa(*lines, timeout=20):
    argv = ["osascript"]
    for line in lines:
        argv += ["-e", line]
    out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if out.returncode:
        raise RuntimeError(out.stderr.strip() or f"osascript exit {out.returncode}")
    return out.stdout.strip()


def volume_state():
    """(output volume 0–100, muted) — Standard Additions, no permission needed."""
    s = osa("get volume settings")
    vol = int(re.search(r"output volume:(\d+)", s).group(1))
    return vol, "output muted:true" in s


def set_volume_state(vol, muted):
    osa(f"set volume output volume {vol}", f"set volume output muted {'true' if muted else 'false'}")


JOURNAL = cfg.APP_SUPPORT / "e2e-restore.json"


class saved_volume:
    """Save the volume, restore it on exit — and leave a note on disk meanwhile, so that a harness run
    that dies (killed, crashed) gets your volume back at the start of the next run."""
    depth = 0

    def __enter__(self):
        import json
        self.state = volume_state()
        if saved_volume.depth == 0:      # the outermost save is your real volume
            JOURNAL.write_text(json.dumps({"volume": self.state[0], "muted": self.state[1]}))
        saved_volume.depth += 1
        return self.state

    def __exit__(self, *exc):
        set_volume_state(*self.state)
        saved_volume.depth -= 1
        if saved_volume.depth == 0:
            JOURNAL.unlink(missing_ok=True)
        return False


def recover_from_crash(run):
    """A previous run died while it had changed the volume: put it back first."""
    import json
    if JOURNAL.exists():
        state = json.loads(JOURNAL.read_text())
        set_volume_state(state["volume"], state["muted"])
        JOURNAL.unlink()
        run.restore("harness", f"a previous run died mid-way: volume put back to {state['volume']}%"
                               f"{' (muted)' if state['muted'] else ''}")


def spotify_state():
    state = osa('tell application "Spotify" to player state as string')
    track = osa('tell application "Spotify"', 'try', 'return id of current track', 'on error', 'return ""',
                'end try', 'end tell')
    pos = osa('tell application "Spotify" to player position')
    try:
        position = float(pos)
    except ValueError:                  # "missing value" right after launch
        position = 0.0
    return {"state": state, "track": track, "position": position}


def wait_for(pred, timeout=8.0, every=0.3):
    end = time.monotonic() + timeout
    while True:
        try:
            value = pred()
        except Exception:                # noqa: BLE001 — the app may still be starting
            value = None
        if value or time.monotonic() >= end:
            return value
        time.sleep(every)


def terminal_windows():
    """{window id: name} of Terminal's windows."""
    ids = osa('tell application "Terminal" to get id of every window')
    out = {}
    for i in [x.strip() for x in ids.split(",") if x.strip()]:
        out[i] = osa(f'tell application "Terminal" to get name of window id {i}')
    return out


def app_window_names(bundle_id):
    """Window titles of a running app on every Space, from the window server (CGWindowListCopyWindowInfo;
    titles need Screen Recording). Accessibility only sees the current Space, so it misses a VS Code
    window you've moved to another one. [] if the app isn't running."""
    import ctypes
    from ctypes import byref, c_int32, c_uint32, c_void_p
    from .ui import mac
    pids = set(mac.running_pids(bundle_id))
    if not pids:
        return []
    cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    cg.CGWindowListCopyWindowInfo.restype, cg.CGWindowListCopyWindowInfo.argtypes = c_void_p, [c_uint32, c_uint32]
    cf.CFArrayGetCount.restype, cf.CFArrayGetCount.argtypes = ctypes.c_long, [c_void_p]
    cf.CFArrayGetValueAtIndex.restype, cf.CFArrayGetValueAtIndex.argtypes = c_void_p, [c_void_p, ctypes.c_long]
    cf.CFDictionaryGetValue.restype, cf.CFDictionaryGetValue.argtypes = c_void_p, [c_void_p, c_void_p]
    cf.CFStringCreateWithCString.restype = c_void_p
    cf.CFStringCreateWithCString.argtypes = [c_void_p, ctypes.c_char_p, c_uint32]
    cf.CFStringGetCString.argtypes = [c_void_p, ctypes.c_char_p, ctypes.c_long, c_uint32]
    cf.CFNumberGetValue.argtypes = [c_void_p, ctypes.c_long, c_void_p]
    cf.CFRelease.argtypes = [c_void_p]
    utf8 = 0x08000100
    k_pid = cf.CFStringCreateWithCString(None, b"kCGWindowOwnerPID", utf8)
    k_name = cf.CFStringCreateWithCString(None, b"kCGWindowName", utf8)
    windows = cg.CGWindowListCopyWindowInfo(0, 0)          # kCGWindowListOptionAll: every Space
    titles = []
    try:
        for i in range(cf.CFArrayGetCount(windows)):
            w = cf.CFArrayGetValueAtIndex(windows, i)
            pid = c_int32()
            cf.CFNumberGetValue(cf.CFDictionaryGetValue(w, k_pid), 3, byref(pid))
            name = cf.CFDictionaryGetValue(w, k_name)
            if pid.value in pids and name:
                buf = ctypes.create_string_buffer(1024)
                if cf.CFStringGetCString(name, buf, 1024, utf8) and buf.value:
                    titles.append(buf.value.decode())
    finally:
        for obj in (windows, k_pid, k_name):
            cf.CFRelease(obj)
    return titles


def menu_bar_items(process="ControlCenter"):
    s = osa(f'tell application "System Events" to tell process "{process}" to get description of every '
            f'menu bar item of menu bar 1')
    return [x.strip() for x in s.split(",")]


FOCUS_ITEM = re.compile(r"focus|do not disturb", re.I)


def focus_on():
    """Is a Focus (Do Not Disturb) on? Control Center shows a Focus item in the menu bar only then."""
    return any(FOCUS_ITEM.search(x) for x in menu_bar_items())


def screen_locked():
    """Is the screen locked (or the login window up)? Focus and window titles can't be checked then."""
    out = subprocess.run(["ioreg", "-n", "Root", "-d1", "-a"], capture_output=True)
    try:
        users = plistlib.loads(out.stdout).get("IOConsoleUsers", [])
    except Exception:                    # noqa: BLE001
        return False
    return any(u.get("CGSSessionScreenIsLocked") for u in users)


LOCKED = "the screen is locked (macOS won't move focus or show window titles then)"


def png_size(path):
    data = Path(path).read_bytes()[:32]
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", data[16:24])


# -- the pipeline, as the app runs it

def say(text, approve=False, use_model=False):
    """One message through the real pipeline. approve=True does what the card's Run button does."""
    from .actions import permissions, pipeline
    out = pipeline.handle(text, use_model=use_model)
    if out.kind == "ask" and approve:
        return out, permissions.run(out.proposal, approved=True)
    return out, out.result


def guard_never_run():
    """Swap lock screen and empty Trash for a tool that refuses to run, for the whole harness."""
    from .actions import registry

    def never(plan, runner, progress):
        raise RuntimeError(f"the e2e harness never runs {plan.tool}")
    for name in NEVER_RUN:
        registry.TOOLS[name] = dataclasses.replace(registry.TOOLS[name], run=never)


def model_up():
    from . import server
    return server.server_up()


# -- areas

def area_permissions(run, args):
    from .ui import mac
    for name, bundle in (("Spotify", SPOTIFY), ("Music", MUSIC), ("Terminal", TERMINAL),
                         ("System Events", SYSTEM_EVENTS), ("Finder (empty Trash; never used here)", FINDER)):
        run.check("permissions", f"Automation → {name}", True, mac.automation_status(bundle))
    run.check("permissions", "Screen Recording", True, "allowed" if mac.screen_capture_allowed() else "not allowed")
    run.check("permissions", "Accessibility (read window titles / menu bar)", True,
              "allowed" if mac.accessibility_allowed() else "not allowed")


def area_volume(run, args):
    with saved_volume() as before:
        for text, want, tol in (("volume 30", 30, 1), ("turn it down", 20, 2), ("louder", 30, 2)):
            out, res = say(text)
            vol, _ = volume_state()
            run.check("volume", f"“{text}” → {want}%", out.kind == "done" and res.ok and abs(vol - want) <= tol,
                      f"now {vol}% ({out.kind})")
        out, _ = say("mute")
        run.check("volume", "“mute” → muted", out.kind == "done" and volume_state()[1])
        out, _ = say("unmute")
        run.check("volume", "“unmute” → not muted", out.kind == "done" and not volume_state()[1])
        if model_up():
            start = volume_state()[0]
            out, res = say("/make it quieter", use_model=True)
            vol = volume_state()[0]
            run.check("volume", "model: “/make it quieter” → down 10", out.kind == "done" and abs(vol - (start - 10)) <= 2,
                      f"{start}% → {vol}% ({out.kind}, {out.route.tool if out.route else '-'})")
        else:
            run.skip("volume", "model: “/make it quieter”", "model server not running")
    run.restore("volume", f"volume back to {before[0]}%{' (muted)' if before[1] else ''}")


def _probe(script):
    """Send one harmless Apple event: 'allowed', 'denied', or None (no answer yet)."""
    try:
        osa(script, timeout=15)
        return "allowed"
    except RuntimeError as e:
        return "denied" if "-1743" in str(e) else None
    except subprocess.TimeoutExpired:
        return None


def _ask_or_skip(run, area, bundle, name, args, probe=None):
    from .ui import mac
    status = mac.automation_status(bundle, timeout=5)
    if status == "no answer" and probe:  # macOS's own check sometimes never answers; ask the app instead
        status = wait_for(lambda: _probe(probe), 45, 2) or "no answer"
    if status == "would ask" and args.ask_permissions:
        print(f"  … macOS is asking whether Terminal may control {name}: click Allow", flush=True)
        status = mac.automation_status(bundle, ask=True)
    if status != "allowed":
        run.skip(area, f"control {name}", f"Automation permission is “{status}”")
        run.needs_you.append(f"Allow Terminal to control {name} (System Settings › Privacy & Security › "
                             f"Automation), or run `python3.12 -m bitnet_desk.e2e --ask-permissions`.")
        return False
    return True


def area_media(run, args):
    from .actions.resolve import installed_apps
    from .ui import mac
    if "spotify" not in installed_apps():
        run.skip("media", "Spotify", "not installed")
        return
    was_running = bool(mac.running_pids(SPOTIFY))
    with saved_volume() as vol:
        set_volume_state(vol[0], True)                 # the music stays silent while we test it
        _media_spotify(run, args, was_running)
    _media_music(run)


def _spotify_ready():
    """Spotify answers Apple events (it takes a few seconds after launch)."""
    try:
        return osa('tell application "Spotify" to player state as string', timeout=15) in ("playing", "paused", "stopped")
    except (RuntimeError, subprocess.TimeoutExpired):
        return False


def _media_spotify(run, args, was_running):
    from .ui import mac
    snapshot = None
    try:
        if not was_running:
            subprocess.run(["open", "-g", "-a", "Spotify"], check=True)
            if not wait_for(lambda: mac.running_pids(SPOTIFY), 30):
                run.check("media", "Spotify starts", False, "didn't start in 30 s")
                return
        if not _ask_or_skip(run, "media", SPOTIFY, "Spotify", args,
                            probe='tell application "Spotify" to player state as string'):
            return
        if not wait_for(_spotify_ready, 45, 1):
            run.check("media", "Spotify answers", False, "no answer to “player state” in 45 s")
            return
        snapshot = wait_for(spotify_state, 20)
        if not snapshot["track"]:                      # nothing to play yet: give it something (setup)
            osa(f'tell application "Spotify" to play track "{SEED_PLAYLIST}"')
            wait_for(lambda: spotify_state()["state"] == "playing", 15)
        osa('tell application "Spotify" to pause')
        wait_for(lambda: spotify_state()["state"] == "paused", 5)

        def state():
            return spotify_state()
        out, res = say("play")
        run.check("media", "“play” → playing", out.kind == "done" and wait_for(lambda: state()["state"] == "playing"),
                  f"{state()['state']} ({res.message if res else out.kind})")
        t0 = state()["track"]
        out, res = say("next song")
        moved = wait_for(lambda: state()["track"] != t0)
        run.check("media", "“next song” → another track", out.kind == "done" and moved, f"{t0} → {state()['track']}")
        t1 = state()["track"]
        out, res = say("go back a song")
        back = wait_for(lambda: state()["track"] != t1)
        run.check("media", "“go back a song” → the previous track", out.kind == "done" and back,
                  f"{t1} → {state()['track']} (first was {t0})")
        out, res = say("pause spotify")
        run.check("media", "“pause spotify” → paused", out.kind == "done" and wait_for(lambda: state()["state"] == "paused"))
        out, res = say("play/pause")
        run.check("media", "“play/pause” → playing", out.kind == "done" and wait_for(lambda: state()["state"] == "playing"))
        out, res = say("can you pause the music")
        run.check("media", "“can you pause the music” → paused",
                  out.kind == "done" and wait_for(lambda: state()["state"] == "paused"))
        if "focus" in cfg.PLAYLISTS:
            out, res = say("play my focus playlist")
            run.check("media", "“play my focus playlist” → playing", out.kind == "done" and
                      wait_for(lambda: state()["state"] == "playing"), res.message if res else out.kind)
            _pause_for_sure()
        else:
            out, _ = say("play my focus playlist")
            run.check("media", "“play my focus playlist” with none configured → says how",
                      out.kind == "refused" and "[playlists]" in out.message, out.message[:90])
    finally:
        try:
            if was_running and snapshot and snapshot["track"]:
                osa(f'tell application "Spotify" to play track "{snapshot["track"]}"')
                wait_for(lambda: spotify_state()["track"] == snapshot["track"], 8)
                osa(f'tell application "Spotify" to set player position to {snapshot["position"]}')
                if snapshot["state"] != "playing":
                    _pause_for_sure()
                run.restore("media", f"Spotify back on its track ({snapshot['state']}); the queue around it may differ")
            elif was_running:                # open, but nothing loaded: leave it open and paused, as found
                _pause_for_sure()
                run.restore("media", "Spotify left open and paused, as it was (it had nothing loaded)")
            elif not was_running and mac.running_pids(SPOTIFY):
                _pause_for_sure()
                _quit_spotify()
                run.restore("media", "Spotify paused and quit (it wasn't open before)" if not mac.running_pids(SPOTIFY)
                            else "Spotify paused, but it didn't quit")
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            run.restore("media", f"couldn't put Spotify back: {e}")


def _pause_for_sure():
    """Pause and wait until Spotify says so: a pause right after a play can be overtaken by it.
    Runs while the sound is still muted."""
    for _ in range(3):
        try:
            osa('tell application "Spotify" to pause')
        except RuntimeError:
            return
        if wait_for(lambda: spotify_state()["state"] != "playing", 4):
            return


def _quit_spotify():
    """Quit Spotify and make sure it stays quit: it ignores a quit while starting up, and after
    installing a pending update on quit it relaunches itself."""
    from .ui import mac
    for _ in range(3):
        mac.quit_app(SPOTIFY)
        if wait_for(lambda: not mac.running_pids(SPOTIFY), 10):
            time.sleep(5)
            if not mac.running_pids(SPOTIFY):
                return True
            _pause_for_sure()
    return False


def _media_music(run):
    """Music: pause/skip must never launch it."""
    from .ui import mac
    if not mac.running_pids(MUSIC):
        out, _ = say("pause music in apple music")
        time.sleep(1)
        run.check("media", "“pause music in apple music” with Music closed → refused, Music not opened",
                  out.kind == "refused" and not mac.running_pids(MUSIC), out.message)
    else:
        run.skip("media", "Music", "Music is open; not touching your library")


def area_screenshot(run, args):
    from .ui import mac
    if not mac.screen_capture_allowed():
        run.skip("screenshot", "take a screenshot", "Screen Recording isn't allowed for Terminal")
        run.needs_you.append("Allow Screen Recording for Terminal (System Settings › Privacy & Security).")
        return
    out, res = say("take a screenshot")
    path = res.items[0] if res and res.items else None
    try:
        size = png_size(path) if path else None
        run.check("screenshot", "“take a screenshot” → a PNG of the screen",
                  out.kind == "done" and res.ok and size and size[0] >= 800 and size[1] >= 500,
                  f"{path} {size[0]}×{size[1]}" if size else (res.message if res else out.kind))
    finally:
        if path and Path(path).exists():
            Path(path).unlink()
            run.restore("screenshot", "deleted the test screenshot")
    for text, flags in (("screenshot this window", ("-i", "-W")), ("screenshot of an area", ("-i", "-s"))):
        from .actions import pipeline
        o = pipeline.decide_fast(text)
        cmd = o.proposal.plan.commands[0] if o and o.proposal else ()
        run.check("screenshot", f"“{text}” plans {' '.join(flags)} (waits for your click, so not run)",
                  o and o.kind == "auto" and tuple(cmd[2:4]) == flags, " ".join(cmd))


def area_project(run, args):
    from .ui import mac
    if "bitnet-desk" not in cfg.PROJECTS:
        run.skip("project", "start bitnet-desk", "bitnet-desk isn't in [projects]")
        return
    term_before = terminal_windows() if mac.running_pids(TERMINAL) else {}
    code_before = app_window_names(CODE)
    out, res = say("start bitnet-desk", approve=True)
    try:
        run.check("project", "“start bitnet-desk” shows a card (asks first)", out.kind == "ask",
                  out.proposal.plan.summary if out.proposal else out.kind)
        run.check("project", "approved → both commands ran", bool(res and res.ok), res.message if res else "-")
        new = wait_for(lambda: {i: n for i, n in terminal_windows().items() if i not in term_before
                                and "bitnet-desk" in n}, 10) or {}
        run.check("project", "a Terminal window opened in bitnet-desk", bool(new), "; ".join(new.values()))
        if screen_locked():
            run.skip("project", "VS Code shows bitnet-desk", LOCKED)
        else:
            code = wait_for(lambda: [w for w in app_window_names(CODE) if "bitnet-desk" in w], 40, 1) or []
            run.check("project", "VS Code shows bitnet-desk", bool(code), "; ".join(code))
    finally:
        for i in [i for i in terminal_windows() if i not in term_before]:
            osa(f'tell application "Terminal" to close window id {i}')
            run.restore("project", f"closed the test Terminal window {i}")
        if not any("bitnet-desk" in w for w in code_before) and any("bitnet-desk" in w for w in app_window_names(CODE)):
            run.restore("project", "VS Code window for bitnet-desk left open (closing it would need a click)")


def area_dnd(run, args):
    from .actions.resolve import installed_shortcuts
    have = installed_shortcuts() or []
    if cfg.DND_ON_SHORTCUT not in have or cfg.DND_OFF_SHORTCUT not in have:
        out, _ = say("turn on do not disturb")
        run.check("dnd", "without the Shortcuts → refused with how to make them",
                  out.kind == "refused" and "Shortcuts app" in out.message, out.message[:100])
        run.skip("dnd", "Do Not Disturb on/off", "the two Shortcuts aren't installed")
        run.needs_you.append("Add the two Do Not Disturb Shortcuts: `python3.12 -m bitnet_desk --setup-shortcuts`, "
                             "then click “Add Shortcut” in each preview (or double-click the two files in "
                             "~/Library/Application Support/BitNetDesk/shortcuts/).")
        return
    was_on = focus_on()
    try:
        out, res = say("turn on do not disturb")
        on = wait_for(focus_on, 8)
        run.check("dnd", "“turn on do not disturb” → Focus on", out.kind == "done" and res.ok and on,
                  f"menu bar: {[x for x in menu_bar_items() if FOCUS_ITEM.search(x)]}")
        out, res = say("dnd off")
        off = wait_for(lambda: not focus_on(), 8)
        run.check("dnd", "“dnd off” → Focus off", out.kind == "done" and res.ok and off)
    finally:
        if was_on and not focus_on():
            subprocess.run(["shortcuts", "run", cfg.DND_ON_SHORTCUT], timeout=30)
            run.restore("dnd", "Do Not Disturb back on (it was on before)")
        elif not was_on:
            run.restore("dnd", "Do Not Disturb left off, as before")


def area_always_ask(run, args):
    from .actions import TOOLS, permissions, pipeline, router
    from .ui import cards
    for text, tool in (("lock my mac", "lock_screen"), ("empty the trash", "empty_trash")):
        o = pipeline.decide_fast(text)
        plan = o.proposal.plan if o and o.proposal else None
        run.check("always_ask", f"“{text}” → a card, never automatic", bool(o and o.kind == "ask" and plan and
                  plan.tool == tool and permissions.needs_approval(plan)), " ; ".join(plan.shown_commands()) if plan else "-")
        run.check("always_ask", f"{tool}: card says it always asks", bool(plan) and "always asks" in cards.card_for(o.proposal).note)
        run.check("always_ask", f"{tool}: never offered to the model", TOOLS[tool].label not in router.labels())
        try:
            TOOLS[tool].run(plan, None, None)
            guarded = False
        except RuntimeError:
            guarded = True
        run.check("always_ask", f"{tool}: the harness can't run it (guard on)", guarded)
    from .actions.tools import system
    run.check("always_ask", "lock screen uses the real lock call (not display sleep)", system.can_lock_directly())


def _pump(app, until=lambda: False, timeout=5.0):
    end = time.monotonic() + timeout
    while True:
        app.root.update()
        value = until()
        if value or time.monotonic() >= end:
            return value
        time.sleep(0.02)


def _icon_help(pump, me):
    """The help text of our menu-bar icons, asked through Accessibility from a worker thread while
    `pump` keeps our event loop running (we're the process being asked, so we must keep answering)."""
    import threading
    box = {}

    def ask():
        try:
            box["v"] = osa(f'tell application "System Events" to tell (first process whose unix id is {me}) '
                           f'to get help of every menu bar item of menu bar 2', timeout=10)
        except Exception as e:           # noqa: BLE001 — no menu bar 2 at all: no icon
            box["v"] = f"(none: {e})"
    t = threading.Thread(target=ask, daemon=True)
    t.start()
    end = time.monotonic() + 12
    while t.is_alive() and time.monotonic() < end:
        pump()
        time.sleep(0.02)
    return box.get("v", "")


def area_app(run, args):
    """Step 1 on the real app, in this process: hotkey, menu bar, command box, window, login."""
    from . import login
    from .ui import mac, window
    if not model_up():
        run.skip("app", "real app", "model server not running (start BitNet Desk first)")
        return
    with saved_volume():
        _app_checks(run, args)
    _single_instance(run)


def _app_checks(run, args):
    from . import login
    from .ui import mac, window
    login_before = login.is_enabled()
    app = window.run_app(background=True, mainloop=False)
    me = os.getpid()
    try:
        run.check("app", "model server ready", bool(_pump(app, lambda: app.ready, 30)))
        run.check("app", f"hotkey {cfg.HOTKEY} registered", bool(app.hotkey and app.hotkey.ref))
        if app.hotkey:
            clashes = mac.find_clashes(app.hotkey.hotkey)
            if clashes:                  # your settings, not a bug: report it, don't fail on it
                run.skip("app", f"{app.hotkey.hotkey.label} is free", f"also used by {', '.join(clashes)}")
                run.needs_you.append(f"{app.hotkey.hotkey.label} is also {', '.join(clashes)}: both open when you press "
                                     "it. Change ChatGPT's shortcut (ChatGPT › Settings › Keyboard shortcut), or set "
                                     "[ui] hotkey in config.toml.")
            else:
                run.check("app", f"{app.hotkey.hotkey.label} clashes with nothing we know of", True,
                          "macOS shortcuts and ChatGPT checked")
        run.check("app", "menu-bar icon created", app.menubar.ok)
        icon = _icon_help(app.root.update, me)
        run.check("app", "menu-bar icon is in the menu bar", "BitNet Desk" in icon, icon)

        # the command box, shown the way the hotkey shows it, from Finder (so "back" has a known answer)
        finder = mac.running_pids(FINDER)
        if finder:
            subprocess.run(["open", "-a", "Finder"])
            _pump(app, lambda: mac.frontmost_pid() == finder[0], 3)
        prev = mac.frontmost_pid()
        app.box.show()
        shown = _pump(app, lambda: app.box.visible() and mac.frontmost_pid() == me, 4)
        locked = screen_locked()
        if locked:
            run.skip("app", "box shows in front, typing goes to it", LOCKED)
        else:
            run.check("app", "box shows in front, typing goes to it",
                      bool(shown and app.root.focus_get() == app.box.entry), f"front pid {mac.frontmost_pid()}, us {me}")
        box_text = lambda: app.box.out.get("1.0", "end")       # noqa: E731
        app.box_submit("what time is it")
        _pump(app, timeout=0.5)
        run.check("app", "box: “what time is it” answered in the box, box stays", "local time" in box_text()
                  and app.box.visible(), box_text().strip()[-80:])
        app.box_submit("volume 25")
        hid = _pump(app, lambda: not app.box.visible(), 2)
        run.check("app", "box: “volume 25” runs and the box gets out of the way",
                  bool(hid) and abs(volume_state()[0] - 25) <= 1, f"volume {volume_state()[0]}%")
        app.box.show()
        _pump(app, timeout=0.5)
        app.box_submit("empty the trash")
        _pump(app, timeout=0.5)
        card = bool(app.pending and app.pending[5]) and bool(app.box.out.window_names())
        run.check("app", "box: “empty the trash” shows its card in the box", card)
        app.box_escape(None)
        _pump(app, timeout=0.3)
        from .actions import log
        last = log.read(1)[-1]
        run.check("app", "box: Esc denies the card; nothing ran", not app.pending and
                  (last["tool"], last["decision"]) == ("empty_trash", "denied"), f"{last['tool']} {last['decision']}")
        if mac.screen_capture_allowed():
            app.box_submit("take a screenshot")
            done = _pump(app, lambda: "Saved" in box_text() or "✗" in box_text(), 15)
            m = re.search(r"(~/\S.*?\.png|/\S.*?\.png)", box_text())
            shot = Path(m.group(1)).expanduser() if m else None
            run.check("app", "box: screenshot taken with the box out of the way, box back with the file",
                      bool(done) and app.box.visible() and shot is not None and shot.exists(), str(shot))
            if shot and shot.exists():
                shot.unlink()
                run.restore("app", "deleted the box's test screenshot")
        app.box_submit("Reply with one short friendly sentence.")
        replied = _pump(app, lambda: not app.busy and "Assistant:" in box_text() and app.reply.strip(), 90)
        mirrored = "Reply with one short friendly sentence." in app.chat.get("1.0", "end")
        run.check("app", "box: a chat reply streams into the box and the window keeps the record",
                  bool(replied) and mirrored, app.reply.strip()[:80])
        if finder:                       # the chat test took a while: start Esc from Finder again
            app.box.hide()
            subprocess.run(["open", "-a", "Finder"])
            _pump(app, lambda: mac.frontmost_pid() == finder[0], 3)
            app.box.show()
            _pump(app, lambda: mac.frontmost_pid() == me, 3)
        app.box_escape(None)
        back = _pump(app, lambda: not app.box.visible() and (prev is None or mac.frontmost_pid() == prev), 3)
        if screen_locked():
            run.skip("app", "box: Esc hides it and gives the keyboard back", LOCKED)
        else:
            run.check("app", "box: Esc hides it and gives the keyboard back", bool(back),
                      f"front pid {mac.frontmost_pid()}, before {prev}")

        # main window: close hides, the app stays
        app.show_window()
        _pump(app, timeout=0.5)
        app.on_close()
        _pump(app, timeout=0.5)
        run.check("app", "closing the window hides it; hotkey and icon stay",
                  not app.root.winfo_viewable() and app.hotkey is not None and app.menubar.ok)

        # launch at login, through the menu's own toggle
        app.login_var.set(not login_before)
        app.toggle_login()
        plist_ok = False
        if not login_before:
            with open(login.plist_path(), "rb") as f:
                agent = plistlib.load(f)
            plist_ok = (Path(agent["ProgramArguments"][0]).exists() and agent["ProgramArguments"][1:] ==
                        ["-m", "bitnet_desk", "--background"] and
                        subprocess.run(["plutil", "-lint", str(login.plist_path())], capture_output=True).returncode == 0)
        run.check("app", "launch at login toggles the LaunchAgent (valid plist, this Python)",
                  login.is_enabled() != login_before and (plist_ok or login_before))
    finally:
        if login.is_enabled() != login_before:
            login.set_enabled(login_before)
        run.restore("app", f"launch at login back {'on' if login_before else 'off'}; volume back as it was")
        hotkey = app.hotkey
        app.quit()
        import tkinter as tk
        scratch = tk.Tk()                # keeps the event loop running so we can answer the question
        scratch.withdraw()
        gone = "BitNet Desk" not in _icon_help(scratch.update, me)
        scratch.destroy()
        run.check("app", "Quit removes the icon and the hotkey", bool(gone) and (hotkey is None or hotkey.ref is None))



def _single_instance(run):
    """One copy at a time (a private lock file, so your running copy isn't involved)."""
    with tempfile.TemporaryDirectory() as d:
        env = dict(os.environ, BITNET_CONFIG=str(Path(d) / "config.toml"))
        from .app import acquire_lock
        fd = acquire_lock(Path(d) / "app.lock")
        try:
            out = subprocess.run([sys.executable, "-m", "bitnet_desk"], env=env, capture_output=True, text=True,
                                 timeout=20, cwd=cfg.HERE)
            run.check("app", "a second copy refuses to start", out.returncode == 1 and "already running" in out.stdout,
                      out.stdout.strip())
        finally:
            os.close(fd)


def area_routine(run, args):
    from .actions import pipeline, routines
    if "work-mode" not in cfg.ROUTINES:
        run.skip("routine", "work mode", "no [routines.work-mode] in config.toml")
        return
    from .actions.resolve import installed_shortcuts
    from .ui import mac
    o = pipeline.decide_fast("work mode")
    if o and o.kind == "refused" and "Shortcuts" in o.message:
        run.check("routine", "“work mode” without the DND Shortcuts → refused, naming the step", True, o.message[:110])
        run.skip("routine", "work mode end to end", "the Do Not Disturb Shortcuts aren't installed yet")
        _routine_stop(run)
        return
    run.check("routine", "“work mode” → one card listing every step", bool(o and o.kind == "ask"),
              " | ".join(o.proposal.plan.shown_commands()) if o and o.proposal else (o.message if o else "no match"))
    if not o or o.kind != "ask":
        return
    steps = cfg.ROUTINES["work-mode"]["steps"]
    term_before = terminal_windows() if mac.running_pids(TERMINAL) else {}
    was_focus = focus_on()
    spotify_was = bool(mac.running_pids(SPOTIFY))
    with saved_volume() as vol:
        set_volume_state(vol[0], True)
        _routine_run(run, o, steps, term_before, was_focus, spotify_was)
    _routine_stop(run)


def _routine_run(run, o, steps, term_before, was_focus, spotify_was):
    from .actions import permissions
    from .actions.resolve import installed_shortcuts
    from .ui import mac
    try:
        from .actions import permissions
        res = permissions.run(o.proposal, approved=True)
        run.check("routine", "approved → every step ran", res.ok, res.message)
        if any("bitnet" in s for s in steps):
            new = wait_for(lambda: [n for i, n in terminal_windows().items() if i not in term_before and "bitnet-desk" in n], 10)
            run.check("routine", "step: project opened (Terminal in bitnet-desk)", bool(new), "; ".join(new or []))
        if any("dnd" in s or "disturb" in s for s in steps):
            if cfg.DND_ON_SHORTCUT in (installed_shortcuts() or []):
                run.check("routine", "step: Do Not Disturb on", bool(wait_for(focus_on, 8)))
            else:
                run.skip("routine", "step: Do Not Disturb on", "the Shortcuts aren't installed")
        if any("playlist" in s for s in steps):
            run.check("routine", "step: playlist playing", wait_for(lambda: spotify_state()["state"] == "playing", 10) or False)
    finally:
        for i in [i for i in terminal_windows() if i not in term_before]:
            osa(f'tell application "Terminal" to close window id {i}')
        run.restore("routine", "closed the routine's Terminal window")
        if not was_focus and focus_on():
            subprocess.run(["shortcuts", "run", cfg.DND_OFF_SHORTCUT], timeout=30)
            run.restore("routine", "Do Not Disturb off again")
        if mac.running_pids(SPOTIFY):
            _pause_for_sure()
            if not spotify_was:
                _quit_spotify()


def _routine_stop(run):
    """Stop at the first failure, for real: a routine whose middle step fails when it runs."""
    from .actions import permissions, routines
    r = routines.Routine("e2e-stop", ("volume 31", "$ false", "volume 32"))   # step 2 fails when it runs
    with saved_volume():
        plan = routines.plan_routine(r)
        res = permissions.run(permissions.Proposal("e2e-stop", "fast", plan), approved=True)
        run.check("routine", "stops at the first failure and says which steps ran",
                  not res.ok and abs(volume_state()[0] - 31) <= 1 and "1 of 3" in res.message,
                  res.message.replace("\n", " · "))


AREA_FUNCS = {name: globals()[f"area_{name}"] for name in AREAS}


# -- report

def write_report(run, areas, path=REPORT):
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                            cwd=cfg.HERE).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], capture_output=True,
                           text=True, cwd=cfg.HERE).stdout.strip()
    c = run.counts()
    locked_note = (["", "⚠ The screen was locked during this run, so checks that need an unlocked screen are "
                    "skipped, not failed."] if any(x.detail == LOCKED for x in run.checks) else [])
    lines = ["# End-to-end report (real Mac)", "",
             f"`python3.12 -m bitnet_desk.e2e` — {datetime.now():%Y-%m-%d %H:%M}, commit {commit}"
             f"{' + uncommitted changes' if dirty else ''}, macOS {platform.mac_ver()[0]}, areas: {', '.join(areas)}.",
             "", "No synthetic keystrokes or clicks: commands go through the real pipeline and permission gate; the app "
             "is driven by calling its own methods. Lock screen and empty Trash are only planned, never run.", "",
             f"**{c['pass']} passed, {c['fail']} failed, {c['skip']} skipped.**"] + locked_note + [""]
    for area in areas:
        rows = [x for x in run.checks if x.area == area]
        if not rows:
            continue
        lines += [f"## {area}", "", "| | check | detail |", "|---|---|---|"]
        icon = {"pass": "✅", "fail": "❌", "skip": "➖"}
        for x in rows:
            detail = x.detail.replace("|", "\\|").replace("\n", " ")
            lines.append(f"| {icon[x.status]} | {x.name} | {detail} |")
        lines.append("")
    if run.restored:
        lines += ["## State restored afterwards", ""] + [f"- {a}: {w}" for a, w in run.restored] + [""]
    if run.needs_you:
        lines += ["## Needs you", ""] + [f"- {w}" for w in dict.fromkeys(run.needs_you)] + [""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    return path


def main(argv=None):
    if sys.platform != "darwin":
        print("The e2e harness runs on macOS only.")
        return 2
    p = argparse.ArgumentParser(prog="python3.12 -m bitnet_desk.e2e")
    p.add_argument("--only", help=f"comma-separated areas: {', '.join(AREAS)}")
    p.add_argument("--permissions", action="store_true", help="show permission status and exit")
    p.add_argument("--ask-permissions", action="store_true", help="let macOS show missing prompts (click Allow)")
    p.add_argument("--setup-shortcuts", action="store_true", help="make the two Do Not Disturb Shortcuts "
                   "(same as python3.12 -m bitnet_desk --setup-shortcuts)")
    p.add_argument("--no-report", action="store_true", help="don't write docs/e2e-report.md")
    args = p.parse_args(argv)
    if args.setup_shortcuts:
        from .shortcuts import setup_shortcuts
        return 0 if setup_shortcuts() else 1
    areas = ["permissions"] if args.permissions else (args.only.split(",") if args.only else list(AREAS))
    unknown = [a for a in areas if a not in AREA_FUNCS]
    if unknown:
        p.error(f"unknown area(s): {', '.join(unknown)}")
    guard_never_run()
    run = Run()
    recover_from_crash(run)
    for area in areas:
        print(f"== {area}", flush=True)
        try:
            AREA_FUNCS[area](run, args)
        except Exception as e:           # noqa: BLE001 — one broken area mustn't hide the others
            run.check(area, "area ran to the end", False, f"{type(e).__name__}: {e}")
            traceback.print_exc()
    c = run.counts()
    print(f"\n{c['pass']} passed, {c['fail']} failed, {c['skip']} skipped")
    if not (args.permissions or args.no_report):
        print(f"Report: {write_report(run, areas)}")
    return 1 if c["fail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
