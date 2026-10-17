"""Fast path: deterministic rules for common commands. No model call.

Precision over coverage: a rule only matches when its target resolves to something real (an
installed app, an existing folder). Anything else falls through to the model router or to chat.
"""
import re
from dataclasses import dataclass

from .registry import TOOLS, ActionError, Plan
from .resolve import KNOWN_FOLDERS, looks_like_url
from .tools.files import KINDS

_I = re.IGNORECASE
POLITE_START = re.compile(
    r"^(?:(?:please|pls|kindly|hey|hi|ok|okay|so|can you|could you|would you|will you|can u|could u)"
    r"\b[\s,]*)+", _I)
POLITE_END = re.compile(r"(?:[\s,]+(?:please|for me|thanks|thank you|thx|if you can|if you could))+$", _I)

# Questions about the current date/time. Answered from the clock, never by the model.
_WHEN = r"(?:\s+(?:today|now|right now|currently|at the moment|here|in here|over here))*"
_WHAT = (r"time|date|day|year|month|day of the week|weekday|date and time|time and date|"
         r"day and date|date and day")
_DAYS = r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|the weekend|a weekday"
NOW = re.compile("|".join([
    rf"what(?:'?s| is)?(?: the)?(?: current| exact)? (?:{_WHAT})(?: is it| it is| is today)?{_WHEN}",
    rf"(?:what|which) (?:{_WHAT}) (?:is it|it is|is today){_WHEN}",
    rf"(?:what'?s|what is) (?:today|today'?s date){_WHEN}",
    rf"(?:today'?s |the |current )?(?:{_WHAT})(?: today| now| right now)?|today",
    rf"(?:tell me|give me|show me|do you know|got|have you got) (?:the (?:{_WHAT})|what (?:{_WHAT}) it is){_WHEN}",
    rf"is it (?:{_DAYS})(?: today| yet| already)?",
    r"how late is it",
]), _I)
HELP = re.compile(
    r"help|what can you do(?: for me)?|what (?:else )?can (?:you|i ask you to) do|what do you do"
    r"|what are your (?:commands|abilities|skills)|what commands (?:do you know|are there|can i use)"
    r"|(?:list|show) (?:your |the )?commands", _I)
OPEN = re.compile(r"(?:open|launch|start|show me|show)\s+(.+)", _I)
GO = re.compile(r"(?:go to|goto|visit|browse to|navigate to)\s+(\S+)", _I)
_URL = r"(https?://\S+)"
DOWNLOAD = re.compile(rf"(?:download|save|grab|fetch|get)\s+{_URL}"
                      r"(?:\s+(?:to|into|in)\s+(?:my\s+|the\s+)?downloads(?:\s+folder)?)?", _I)
PROJECT = re.compile(r"(?:start|open|launch|load|work on|switch to|resume|continue)\s+"
                     r"(?:(?:the|my)\s+)?(?:project\s+)?(.+?)(?:\s+project)?", _I)
# Literal shell commands only: "$ cmd", or run/execute with the command in `backticks` or quotes.
SHELL = re.compile(r"\$\s+(.+)|(?:run|execute)(?:\s+(?:the\s+)?command)?:?\s+(?:`([^`]+)`|\"([^\"]+)\"|'([^']+)')", _I)
_FILE_WORDS = r"(?:files?|documents?|docs?|folders?|pdfs?|photos?|images?|pictures?)"
FIND = re.compile(rf"(?:find|search for|search|locate|look for)\s+(?:all\s+)?(?:my\s+)?{_FILE_WORDS}\s+"
                  r"(?:named|called|about|with|containing|for|matching)\s+(.+)"
                  r"|(?:find|search for|locate|look for)\s+my\s+(.+)"
                  r"|(?:where is|where's|wheres|where are)\s+my\s+(.+)", _I)


# -- system and media (docs/always-there-design.md)
_PLAYER = r"(?:\s+(?:on|in)\s+(spotify|music|apple music|itunes))?"
_WHAT_PLAYS = r"(?:\s+(?:the\s+|my\s+|this\s+)?(?:music|song|track|playback|tunes|spotify|apple music|itunes))?"
MEDIA = [
    ("toggle", re.compile(rf"(?:play\s*/\s*pause|playpause|toggle(?:\s+the)?\s+(?:music|playback)){_PLAYER}", _I)),
    ("play", re.compile(rf"(?:play|resume|unpause){_WHAT_PLAYS}{_PLAYER}"
                        rf"|play\s+(?:some\s+)?music{_PLAYER}", _I)),
    ("pause", re.compile(rf"pause{_WHAT_PLAYS}{_PLAYER}|stop\s+(?:the\s+)?(?:music|song|playback|spotify){_PLAYER}", _I)),
    ("next", re.compile(rf"(?:(?:play\s+(?:the\s+)?)?next|skip)(?:\s+(?:this\s+|the\s+)?(?:song|track|one))?"
                        rf"(?:\s+(?:song|track))?{_PLAYER}", _I)),
    ("previous", re.compile(rf"(?:(?:play\s+(?:the\s+)?)?(?:previous|prev|last)(?:\s+(?:song|track))?"
                            rf"|go\s+back\s+(?:a|one)\s+(?:song|track)){_PLAYER}", _I)),
]
PLAYLIST = re.compile(rf"(?:play|start|put on)\s+(?:my\s+|the\s+)?(.+?)\s+playlist{_PLAYER}"
                      rf"|(?:play|start|put on)\s+(?:the\s+)?playlist\s+(.+?){_PLAYER}", _I)
_SOUND = r"(?:the\s+)?(?:volume|sound|music|audio)"
VOLUME = [
    ("up", re.compile(rf"(?:turn\s+(?:it|{_SOUND})\s+up|turn\s+up\s+{_SOUND}|(?:volume|sound)\s+up"
                      rf"|louder|(?:increase|raise)\s+{_SOUND})(?:\s+a\s+(?:bit|little))?", _I)),
    ("down", re.compile(rf"(?:turn\s+(?:it|{_SOUND})\s+down|turn\s+down\s+{_SOUND}|(?:volume|sound)\s+down"
                        rf"|quieter|softer|(?:decrease|lower|reduce)\s+{_SOUND})(?:\s+a\s+(?:bit|little))?", _I)),
    ("mute", re.compile(r"mute(?:\s+(?:the\s+|my\s+)?(?:sound|audio|volume|mac|computer|speakers))?", _I)),
    ("unmute", re.compile(r"unmute(?:\s+(?:the\s+|my\s+)?(?:sound|audio|volume|mac|computer|speakers))?", _I)),
]
VOLUME_SET = re.compile(r"(?:(?:set|turn|put)\s+(?:the\s+)?(?:volume|sound)\s+(?:to|at)|volume(?:\s+(?:to|at))?)"
                        r"\s+(\d{1,3})\s*(?:%|percent)?", _I)
_DND = r"(?:do not disturb|don't disturb|dnd|focus(?: mode)?)"
DND = re.compile(rf"(?:turn|switch|set|put)\s+(on|off)\s+{_DND}|(?:turn|switch|set|put)\s+{_DND}\s+(on|off)"
                 rf"|{_DND}\s+(on|off)|(enable|disable|start|stop|end)\s+{_DND}", _I)
LOCK = re.compile(r"lock(?:\s+(?:the\s+|my\s+)?(?:screen|mac|computer|laptop))?", _I)
SCREENSHOT = re.compile(
    r"(?:(?:take|grab|capture|make|do)\s+(?:a\s+)?)?(?:screenshot|screen shot|screen capture|screengrab)"
    r"(?:\s+(?:of\s+)?(?:the\s+|my\s+|this\s+|an?\s+)?(screen|window|area|selection|region|part of the screen))?"
    r"|screenshot\s+(?:this|the|a)\s+(window|area|screen)", _I)
SHOT_KINDS = {"screen": "screen", "window": "window", "area": "area", "selection": "area", "region": "area",
              "part of the screen": "area"}
EMPTY_TRASH = re.compile(r"(?:empty|clear)\s+(?:out\s+)?(?:the\s+|my\s+)?(?:trash|bin|trash can|trash bin|recycle bin)", _I)


@dataclass(frozen=True)
class Match:
    tool: str
    args: dict          # canonical arguments (from the plan)
    plan: Plan


@dataclass(frozen=True)
class Refusal:
    """A clear command whose target isn't there ("play my gym playlist" with no such playlist).
    Saying why beats handing it to chat."""
    tool: str
    message: str
    suggestions: tuple = ()
    args: dict = None

    def __post_init__(self):
        object.__setattr__(self, "args", self.args or {})


def normalise(text):
    """Collapse spaces, straighten quotes, drop polite openers/closers and trailing punctuation."""
    t = " ".join(text.replace("’", "'").replace("‘", "'").split()).rstrip("?!. ")
    t = POLITE_END.sub("", POLITE_START.sub("", t))
    return t.rstrip("?!., ")


def _try(tool, args, refuse=False):
    """A Match, or None — or, with refuse=True (the wording leaves no doubt), a Refusal saying why."""
    try:
        plan = TOOLS[tool].make_plan(args)
    except ActionError as e:
        return Refusal(tool, e.message, e.suggestions) if refuse else None
    return Match(tool, plan.args, plan)


def _target(x):
    """'the terminal app' → 'terminal', 'my downloads folder' → 'downloads'."""
    x = re.sub(r"^(?:the|my)\s+", "", x.strip(), flags=_I)
    x = re.sub(r"\s+(?:app|application|folder|directory)$", "", x, flags=_I)
    return x.strip().strip("\"'")


def _open(text):
    m = OPEN.fullmatch(text)
    if not m:
        return None
    x = _target(m.group(1))
    if x.startswith(("~", "/")):
        return _try("open_path", {"path": x})
    return (_try("open_app", {"name": x})
            or (_try("open_path", {"path": x}) if x.lower() in KNOWN_FOLDERS else None)
            or (_try("open_url", {"url": x}) if looks_like_url(x) else None))


def _go(text):
    m = GO.fullmatch(text)
    return _try("open_url", {"url": m.group(1)}) if m and looks_like_url(m.group(1)) else None


def _download(text):
    m = DOWNLOAD.fullmatch(text)
    return _try("download", {"url": m.group(1)}) if m else None


def _project(text):
    m = PROJECT.fullmatch(text)
    return _try("start_project", {"name": m.group(1)}) if m else None


def _shell(text):
    m = SHELL.fullmatch(text)
    if not m:
        return None
    return _try("run_shell", {"command": next(g for g in m.groups() if g)})


def _find(text):
    m = FIND.fullmatch(text)
    if not m:
        return None
    words = next(g for g in m.groups() if g).split()
    kind = KINDS.get(words[-1].lower()) if len(words) > 1 else None
    if kind:
        words = words[:-1]
    return _try("find_files", {"query": " ".join(words), **({"kind": kind} if kind else {})})


def _player(m):
    return {"player": m.group(m.lastindex)} if m.lastindex and m.group(m.lastindex) else {}


def _media(text):
    for action, rx in MEDIA:
        m = rx.fullmatch(text)
        if m:
            return _try("media", {"action": action, **_player(m)}, refuse=True)
    return None


def _playlist(text):
    m = PLAYLIST.fullmatch(text)
    if not m:
        return None
    name = m.group(1) or m.group(3)
    if re.fullmatch(r"(?:a|some|any)(?:\s+.*)?", name, _I):        # "play a chill playlist": not one of yours
        return None
    return _try("playlist", {"name": name}, refuse=True)


def _volume(text):
    m = VOLUME_SET.fullmatch(text)
    if m:
        return _try("volume", {"action": "set", "level": int(m.group(1))}, refuse=True)
    for action, rx in VOLUME:
        if rx.fullmatch(text):
            return _try("volume", {"action": action}, refuse=True)
    return None


def _dnd(text):
    m = DND.fullmatch(text)
    if not m:
        return None
    word = next(g for g in m.groups() if g).lower()
    return _try("dnd", {"state": "on" if word in ("on", "enable", "start") else "off"}, refuse=True)


def _screenshot(text):
    m = SCREENSHOT.fullmatch(text)
    if not m:
        return None
    kind = next((g for g in m.groups() if g), "screen").lower()
    return _try("screenshot", {"what": SHOT_KINDS[kind]}, refuse=True)


def _lock(text):
    return _try("lock_screen", {}, refuse=True) if LOCK.fullmatch(text) else None


def _empty_trash(text):
    return _try("empty_trash", {}, refuse=True) if EMPTY_TRASH.fullmatch(text) else None


def _now(text):
    return _try("now", {}) if NOW.fullmatch(text) else None


def _help(text):
    return _try("help", {}) if HELP.fullmatch(text) else None


RULES = [_help, _now, _shell, _download, _playlist, _media, _volume, _dnd, _screenshot, _lock, _empty_trash,
         _project, _go, _find, _open]


def match(text):
    t = normalise(text)
    for rule in RULES:
        m = rule(t)
        if m:
            return m
    return None
