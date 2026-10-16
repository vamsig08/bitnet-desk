"""Fast path: deterministic rules for common commands. No model call.

Precision over coverage: a rule only matches when its target resolves to something real (an
installed app, an existing folder). Anything else falls through to the model router or to chat.
"""
import re
from dataclasses import dataclass

from .registry import TOOLS, ActionError, Plan
from .resolve import KNOWN_FOLDERS

_I = re.IGNORECASE
POLITE_START = re.compile(
    r"^(?:(?:please|pls|kindly|hey|hi|ok|okay|so|can you|could you|would you|will you|can u|could u)"
    r"\b[\s,]*)+", _I)
POLITE_END = re.compile(r"(?:[\s,]+(?:please|for me|thanks|thank you|thx|if you can|if you could))+$", _I)

# Questions about the current date/time. Answered from the clock, never by the model.
_WHEN = r"(?:\s+(?:today|now|right now|currently|at the moment|here))*"
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


@dataclass(frozen=True)
class Match:
    tool: str
    args: dict          # canonical arguments (from the plan)
    plan: Plan


def normalise(text):
    """Collapse spaces, straighten quotes, drop polite openers/closers and trailing punctuation."""
    t = " ".join(text.replace("’", "'").replace("‘", "'").split()).rstrip("?!. ")
    t = POLITE_END.sub("", POLITE_START.sub("", t))
    return t.rstrip("?!., ")


def _try(tool, args):
    try:
        plan = TOOLS[tool].make_plan(args)
    except ActionError:
        return None
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
    return _try("open_app", {"name": x}) or (
        _try("open_path", {"path": x}) if x.lower() in KNOWN_FOLDERS else None)


def _now(text):
    return _try("now", {}) if NOW.fullmatch(text) else None


def _help(text):
    return _try("help", {}) if HELP.fullmatch(text) else None


RULES = [_help, _now, _open]


def match(text):
    t = normalise(text)
    for rule in RULES:
        m = rule(t)
        if m:
            return m
    return None
