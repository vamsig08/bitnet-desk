"""Model path: the model picks one tool and fills its arguments as JSON.

The server's `json_schema` grammar guarantees valid JSON and a real tool label; it can't stop the
model inventing values, so ground.py checks arguments afterwards. Confidence is the probability of
the chosen tool label at the token where the model picked it.
"""
import json
import re
import time
from dataclasses import dataclass, field

from .. import config as cfg
from .. import llm
from .registry import TOOLS
from .resolve import KNOWN_FOLDERS, looks_like_url

THRESHOLD = 0.7          # below this the model's pick is shown as a question, never auto-run
CHAT = "chat"

INTRO = ("You turn the user's message into exactly one tool call, written as JSON. "
         "If the message is a question, asks for writing or an explanation, or no tool does it, "
         'answer {"tool": "chat"}. Only use web addresses, file names and commands that the user '
         "wrote — never make them up.\nTools:\n")

# Few-shot turns. Deliberately not copies of eval cases.
EXAMPLES = [
    ("launch Safari", {"tool": "app", "args": {"name": "Safari"}}),
    ("show me the Pictures folder", {"tool": "path", "args": {"path": "~/Pictures"}}),
    ("open the page example.org", {"tool": "url", "args": {"url": "example.org"}}),
    ("start working on the website project", {"tool": "project", "args": {"name": "website"}}),
    ("grab https://example.org/data.zip", {"tool": "download", "args": {"url": "https://example.org/data.zip"}}),
    ("find the slides about the budget", {"tool": "find", "args": {"query": "budget slides"}}),
    ("run `ls -la`", {"tool": "shell", "args": {"command": "ls -la"}}),
    ("pause the song", {"tool": "media", "args": {"action": "pause"}}),
    ("mute the speakers", {"tool": "volume", "args": {"action": "mute"}}),
    ("run a virus scan", {"tool": "chat"}),
    ("show me how to bake bread", {"tool": "chat"}),
    ("what day is it", {"tool": "now", "args": {}}),
]

TOOL_VALUE = re.compile(r'"tool"\s*:\s*"')
URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>`]+", re.I)
LITERAL_COMMAND = re.compile(r"`([^`]+)`|\"([^\"]+)\"|^\s*\$\s+(.+)$")


def literals(text):
    """Values the model may copy from the message: {arg kind: [exact strings]}."""
    urls = [u.rstrip(".,;:!?)") for u in URL_IN_TEXT.findall(text)]
    words = [w.strip(".,;:!?()\"'") for w in text.split()]
    domains = [w for w in words if w and looks_like_url(w) and not any(w in u for u in urls)]
    from .ground import WELL_KNOWN
    lowered = text.lower()
    sites = [site for name, site in WELL_KNOWN.items() if re.search(rf"\b{re.escape(name)}\b", lowered)]
    commands = [next(g for g in m.groups() if g).strip() for m in LITERAL_COMMAND.finditer(text)]
    lw = {w.lower() for w in words}
    paths = [w for w in words if w.startswith(("~/", "/"))] + [p for k, p in KNOWN_FOLDERS.items() if k in lw]
    return {"download": urls, "open_url": list(dict.fromkeys(urls + domains + sites)),
            "run_shell": commands, "open_path": list(dict.fromkeys(paths))}


# For these tools the grammar only allows values that appear in the message; without any, the
# tool isn't offered at all. The model can't invent a URL, a command or a path.
COPY_ARG = {"download": "url", "open_url": "url", "run_shell": "command", "open_path": "path"}


@dataclass
class Route:
    tool: str                       # code name, or "chat"
    args: dict
    confidence: float | None        # p(chosen tool) among tool labels; None if unknown
    alternatives: dict = field(default_factory=dict)    # label -> probability
    ms: int = 0
    raw: str = ""


def tools():
    """The tools offered to the model: not the fast-path-only ones (lock screen, empty Trash, Do Not
    Disturb, screenshot), and projects/playlists only when some are configured."""
    return [t for t in TOOLS.values() if t.model
            and (t.name != "start_project" or cfg.PROJECTS) and (t.name != "playlist" or cfg.PLAYLISTS)]


def labels():
    return {t.label: t.name for t in tools()} | {CHAT: CHAT}


def schema(text=None):
    """The json_schema for the grammar. With `text`, copy-only arguments are limited to the values in it."""
    alts = []
    found = literals(text) if text is not None else {}
    for t in tools():
        props = {k: dict(v) for k, v in t.args.items()}
        if t.name == "playlist":                         # only the user's own playlists
            props["name"] = {"enum": list(cfg.PLAYLISTS)}
        if text is not None and t.name in ("media", "volume"):
            _copy_only_media(t.name, props, text)
        if t.name in COPY_ARG and text is not None:
            if not found[t.name]:
                continue
            props[COPY_ARG[t.name]] = {"enum": found[t.name]}
        required = [k for k in t.args if k not in t.optional]
        alts.append({"type": "object", "additionalProperties": False, "required": ["tool", "args"],
                     "properties": {"tool": {"const": t.label},
                                    "args": {"type": "object", "properties": props, "required": required,
                                             "additionalProperties": False}}})
    alts.append({"type": "object", "additionalProperties": False, "required": ["tool"],
                 "properties": {"tool": {"const": CHAT}}})
    return {"oneOf": alts}


def _copy_only_media(tool, props, text):
    """A player only if the message names one; a volume level only if it has that number."""
    if tool == "media":
        from .ground import player_named
        named = [p for p in props["player"]["enum"] if player_named(p, text)]
        if named:
            props["player"] = {"enum": named}
        else:
            del props["player"]
    else:
        levels = sorted({int(n) for n in re.findall(r"\b(\d{1,3})\b", text) if int(n) <= 100})
        if levels:
            props["level"] = {"enum": levels}
        else:
            del props["level"]
            props["action"] = {"type": "string", "enum": [a for a in props["action"]["enum"] if a != "set"]}


def system_text():
    lines = []
    for t in tools():
        args = ", ".join(t.args)
        extra = {"start_project": f" — the user's projects are: {', '.join(cfg.PROJECTS)}",
                 "playlist": f" — the user's playlists are: {', '.join(cfg.PLAYLISTS)}"}.get(t.name, "")
        lines.append(f"- {t.label}({args}): {t.summary}{extra}")
    lines.append(f"- {CHAT}: anything else — the assistant just replies")
    return INTRO + "\n".join(lines)


def prompt(text):
    history = []
    for user, answer in EXAMPLES:
        history += [("user", user), ("assistant", json.dumps(answer))]
    return llm.build_prompt(system_text(), history + [("user", text)])


def tool_confidence(content, probs, chosen):
    """(p(chosen label), {label: p}) from the token where the tool name starts, renormalised over
    tokens that could begin some label — the server reports probabilities before the grammar."""
    m = TOOL_VALUE.search(content)
    if not m or not probs:
        return None, {}
    start, pos = m.end(), 0
    for tok in probs:
        text = tok.get("content", "")
        if pos + len(text) > start:
            k = start - pos
            per_label = {}
            for c in tok.get("probs", []):
                s = c.get("tok_str", "")
                if s[:k] != text[:k] or len(s) <= k:
                    continue
                piece = s[k:]
                closed = '"' in piece
                piece = piece.split('"')[0]
                fits = [lab for lab in labels() if (lab == piece if closed else lab.startswith(piece))]
                for lab in fits:
                    per_label[lab] = per_label.get(lab, 0.0) + c.get("prob", 0.0) / len(fits)
            total = sum(per_label.values())
            if not total:
                return 0.0, {}
            norm = {lab: p / total for lab, p in sorted(per_label.items(), key=lambda kv: -kv[1])}
            return norm.get(chosen, 0.0), norm
        pos += len(text)
    return None, {}


def route(text):
    """Ask the model which tool fits `text`. Raises if the server can't answer."""
    t0 = time.monotonic()
    out = llm.complete_json(prompt(text), schema(text))
    ms = round((time.monotonic() - t0) * 1000)
    content = out.get("content", "")
    obj = json.loads(content)
    label = obj.get("tool", CHAT)
    conf, alts = tool_confidence(content, out.get("completion_probabilities") or [], label)
    return Route(tool=labels().get(label, CHAT), args=obj.get("args") or {}, confidence=conf,
                 alternatives=alts, ms=ms, raw=content)
