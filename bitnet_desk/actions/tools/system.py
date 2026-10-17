"""System and media tools: music, playlists, volume, Do Not Disturb, screenshot, lock screen, Trash.

Everything is a fixed command (osascript, shortcuts, screencapture) built from a small set of
choices, so the card shows exactly what runs. Planning only reads state (is Spotify running? which
Shortcuts exist?) and never sends an Apple event, so no permission prompt appears before you approve.
Lock screen and empty Trash always ask, and the model is never offered them.
"""
import re
import sys
from datetime import datetime
from pathlib import Path

from ... import config as cfg
from ..registry import ActionError, Plan, Result, Risk, Tool, register
from ..resolve import installed_apps, installed_shortcuts, is_running, screenshot_dir, tilde, unique_path

PLAYERS = ("Spotify", "Music")
PLAYER_NAMES = {"spotify": "Spotify", "music": "Music", "apple music": "Music", "itunes": "Music"}
# action → (AppleScript verb, card wording, done message)
MEDIA = {"play": ("play", "Play", "Playing"),
         "pause": ("pause", "Pause", "Paused"),
         "toggle": ("playpause", "Play/pause", "Toggled"),
         "next": ("next track", "Skip to the next track", "Skipped to the next track"),
         "previous": ("previous track", "Go back to the previous track", "Went back a track")}
VOLUME_STEP = 10
LOGIN_FRAMEWORK = "/System/Library/PrivateFrameworks/login.framework/Versions/Current/login"
DND_SETUP = ('To switch Do Not Disturb I run two Shortcuts you make once in the Shortcuts app: '
             '“{on}” (one action: Set Focus → Do Not Disturb → Turn On) and “{off}” (Set Focus → '
             'Turn Off). Make them, then try again.')


def _osascript(*lines):
    argv = ["osascript"]
    for line in lines:
        argv += ["-e", line]
    return tuple(argv)


def _installed_players():
    apps = installed_apps()
    return [p for p in PLAYERS if p.lower() in apps]


def pick_player(requested=None, launch=True):
    """Spotify or Music. Asked-for wins; otherwise [media] player, else the one that's running
    (Spotify first), else Spotify if installed. With launch=False (pause, skip), it must be running."""
    installed = _installed_players()
    if not installed:
        raise ActionError("Neither Spotify nor Music is installed.")
    if requested:
        name = PLAYER_NAMES.get(" ".join(str(requested).lower().split()))
        if name not in installed:
            raise ActionError(f"{name or requested} isn't installed." if name else
                              f"I can only control Spotify and Music, not “{requested}”.")
        candidates = [name]
    elif cfg.MEDIA_PLAYER in installed:
        candidates = [cfg.MEDIA_PLAYER]
    else:
        candidates = installed
    for name in candidates:
        if is_running(name):
            return name
    if not launch:
        raise ActionError("Nothing is playing — " + (f"{candidates[0]} isn't open." if len(candidates) == 1
                                                      else "Spotify and Music aren't open."))
    return candidates[0]


def _media(args):
    action = args.get("action")
    if action not in MEDIA:
        raise ActionError(f"Unknown music control “{action}”.")
    player = pick_player(args.get("player"), launch=action in ("play", "toggle"))
    verb, what, done = MEDIA[action]
    if action == "toggle" and not is_running(player):
        verb, what, done = MEDIA["play"][0], "Play", "Playing"     # nothing to pause yet: just play
    return Plan(tool="media", args={"action": action, "player": player}, risk=Risk.CHANGES,
                summary=f"{what} ({player})", done=f"{done} ({player}).",
                commands=(_osascript(f'tell application "{player}" to {verb}'),))


def spotify_uri(value):
    """'spotify:playlist:ID' or an open.spotify.com link → the URI; anything else → None."""
    if value.startswith("spotify:"):
        return value
    m = re.match(r"https?://open\.spotify\.com/(?:intl-[a-z-]+/)?(playlist|album|artist|track)/([A-Za-z0-9]+)", value)
    return f"spotify:{m.group(1)}:{m.group(2)}" if m else None


def _playlist_key(name):
    key = " ".join(str(name).lower().split())
    key = re.sub(r"^(?:my|the)\s+", "", key)
    return re.sub(r"\s+playlist$", "", key).strip()


def _playlist(args):
    name = _playlist_key(args.get("name", ""))
    if name not in cfg.PLAYLISTS:
        import difflib
        close = difflib.get_close_matches(name, cfg.PLAYLISTS, n=3, cutoff=0.6)
        known = (f" Your playlists: {', '.join(cfg.PLAYLISTS)}." if cfg.PLAYLISTS else
                 " Add it to the settings file under [playlists], e.g. focus = \"spotify:playlist:…\".")
        raise ActionError(f"There's no playlist called “{name}” in your settings.{known}", close)
    value = cfg.PLAYLISTS[name]
    uri = spotify_uri(value)
    player = "Spotify" if uri else "Music"
    if player not in _installed_players():
        raise ActionError(f"Playlist “{name}” needs {player}, which isn't installed.")
    line = (f'tell application "Spotify" to play track "{uri}"' if uri
            else f'tell application "Music" to play playlist "{value}"')
    return Plan(tool="playlist", args={"name": name, "player": player}, risk=Risk.CHANGES,
                summary=f"Play your {name} playlist ({player})", done=f"Playing {name} ({player}).",
                commands=(_osascript(line),))


def _volume(args):
    action, level = args.get("action"), args.get("level")
    current = "(output volume of (get volume settings))"
    if action in ("up", "down"):
        sign, bound, cmp = ("+", 100, ">") if action == "up" else ("-", 0, "<")
        lines = [f"set v to {current} {sign} {VOLUME_STEP}", f"if v {cmp} {bound} then set v to {bound}",
                 "set volume output volume v"]
        if action == "up":
            lines.append("set volume output muted false")
        summary, done = f"Turn the volume {action} ({sign}{VOLUME_STEP}%)", f"Volume {action}."
    elif action == "set":
        if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 100:
            raise ActionError("The volume goes from 0 to 100.")
        lines = [f"set volume output volume {level}"] + (["set volume output muted false"] if level else [])
        summary, done = f"Set the volume to {level}%", f"Volume set to {level}%."
    elif action in ("mute", "unmute"):
        lines = [f"set volume output muted {'true' if action == 'mute' else 'false'}"]
        summary, done = f"{action.capitalize()} the sound", "Muted." if action == "mute" else "Unmuted."
    else:
        raise ActionError(f"Unknown volume change “{action}”.")
    out = {"action": action} | ({"level": level} if action == "set" else {})
    return Plan(tool="volume", args=out, risk=Risk.CHANGES, summary=summary, done=done,
                commands=(_osascript(*lines),))


def _dnd(args):
    state = args.get("state")
    if state not in ("on", "off"):
        raise ActionError("Do Not Disturb can be turned on or off.")
    name = cfg.DND_ON_SHORTCUT if state == "on" else cfg.DND_OFF_SHORTCUT
    have = installed_shortcuts()
    if have is not None and name not in have:
        raise ActionError(DND_SETUP.format(on=cfg.DND_ON_SHORTCUT, off=cfg.DND_OFF_SHORTCUT))
    return Plan(tool="dnd", args={"state": state}, risk=Risk.CHANGES,
                summary=f"Turn Do Not Disturb {state}", done=f"Do Not Disturb is {state}.",
                commands=(("shortcuts", "run", name),))


SHOTS = {"screen": ((), "the whole screen"),
         "window": (("-i", "-W"), "a window (click the one you want)"),
         "area": (("-i", "-s"), "an area (drag to select it)")}


def _screenshot(args):
    what = args.get("what") or "screen"
    if what not in SHOTS:
        raise ActionError(f"I can take a screenshot of the screen, a window or an area, not “{what}”.")
    flags, words = SHOTS[what]
    dest = unique_path(screenshot_dir() / f"Screenshot {datetime.now():%Y-%m-%d at %H.%M.%S}.png")
    return Plan(tool="screenshot", args={"what": what, "dest": str(dest)}, risk=Risk.CHANGES,
                summary=f"Take a screenshot of {words}", done=f"Saved {tilde(dest)}.",
                commands=(("screencapture", "-x", *flags, str(dest)),))


def _run_screenshot(plan, runner, progress):
    from ..permissions import run_commands
    result = run_commands(plan, runner, timeout=120)        # window/area wait for your click
    if not result.ok:
        return result
    dest = Path(plan.args["dest"])
    if not dest.exists():
        return Result(False, "No screenshot was taken (cancelled, or Screen Recording isn't allowed).")
    return Result(True, plan.done, items=(str(dest),))


def _lock(args):
    if Path(LOGIN_FRAMEWORK).exists():   # what Control Center's Lock Screen uses (private, but it locks)
        cmd = (sys.executable, "-c", f"import ctypes; ctypes.CDLL({LOGIN_FRAMEWORK!r}).SACLockScreenImmediate()")
    else:                                # locks only if "require password" is set for display sleep
        cmd = ("pmset", "displaysleepnow")
    return Plan(tool="lock_screen", args={}, risk=Risk.CHANGES, summary="Lock the screen",
                done="Locked.", commands=(cmd,))


def _empty_trash(args):
    return Plan(tool="empty_trash", args={}, risk=Risk.CHANGES,
                summary="Empty the Trash — permanently deletes everything in it; can't be undone",
                done="Emptied the Trash.", commands=(_osascript('tell application "Finder" to empty trash'),))


def _enum(*values):
    return {"type": "string", "enum": list(values)}


register(Tool("media", "media", "play, pause, skip or go back in Spotify or Music",
              {"action": _enum(*MEDIA), "player": _enum(*PLAYERS)}, Risk.CHANGES, _media,
              'control music ("pause", "next song")', optional=("player",)))
register(Tool("playlist", "playlist", "play one of the user's playlists",
              {"name": {"type": "string", "maxLength": 60}}, Risk.CHANGES, _playlist,
              'play a playlist from your settings ("play my focus playlist")'))
register(Tool("volume", "volume", "turn the sound up, down, to a level, or mute it",
              {"action": _enum("up", "down", "set", "mute", "unmute"), "level": {"type": "integer"}},
              Risk.CHANGES, _volume, 'change the volume ("volume 30", "mute")', optional=("level",)))
register(Tool("dnd", "dnd", "turn Do Not Disturb on or off", {"state": _enum("on", "off")}, Risk.CHANGES,
              _dnd, 'Do Not Disturb ("turn on do not disturb")', model=False))
register(Tool("screenshot", "screenshot", "take a screenshot", {"what": _enum(*SHOTS)}, Risk.CHANGES,
              _screenshot, 'take a screenshot ("screenshot this window")', run=_run_screenshot,
              optional=("what",), model=False, hides_ui=True))
register(Tool("lock_screen", "lock", "lock the screen", {}, Risk.CHANGES, _lock, 'lock the screen ("lock my Mac")',
              always_ask=True, model=False))
register(Tool("empty_trash", "trash", "empty the Trash", {}, Risk.CHANGES, _empty_trash,
              'empty the Trash ("empty the trash")', always_ask=True, model=False))
