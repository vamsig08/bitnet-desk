"""Fast path: deterministic rules for common commands. No model call.

Precision over coverage: a rule only matches when its target resolves to something real (an
installed app, an existing folder). Anything else falls through to the model router or to chat.
"""
import re
from dataclasses import dataclass

from .registry import TOOLS, ActionError, Plan
from .resolve import KNOWN_FOLDERS

_I = re.IGNORECASE
POLITE_START = re.compile(r"^(?:(?:please|pls|hey|ok|okay|can you|could you|would you|will you)\b[\s,]*)+", _I)
POLITE_END = re.compile(r"[\s,]*\bplease$", _I)

NOW = re.compile(
    r"(?:what(?:'s| is) the (?:time|date|day)(?: today| now| right now)?"
    r"|what time is it(?: now| right now)?"
    r"|what(?:'s| is) today(?:'s date)?"
    r"|what (?:day|date) is it(?: today)?"
    r"|time|date)", _I)
OPEN = re.compile(r"(?:open|launch|start|show me|show)\s+(.+)", _I)


@dataclass(frozen=True)
class Match:
    tool: str
    args: dict          # canonical arguments (from the plan)
    plan: Plan


def normalise(text):
    """Collapse spaces, straighten quotes, drop polite openers/closers and trailing punctuation."""
    t = " ".join(text.replace("’", "'").replace("‘", "'").split())
    t = POLITE_END.sub("", POLITE_START.sub("", t))
    return t.rstrip("?!. ")


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


RULES = [_now, _open]


def match(text):
    t = normalise(text)
    for rule in RULES:
        m = rule(t)
        if m:
            return m
    return None
