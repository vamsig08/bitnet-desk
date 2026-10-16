"""Prompt formatting, context fitting and the streaming /completion client.

All model access goes through this module so the model can be swapped by config.
"""
import functools
import json
import urllib.request
from datetime import datetime
from pathlib import Path

from . import config as cfg

STOP = ["<|eot_id|>", "<|end_of_text|>", "\nUser:"]


def build_prompt(system, history):
    """BitNet b1.58 2B4T chat format. The server adds <|begin_of_text|> itself."""
    parts = [f"System: {system}<|eot_id|>"]
    for role, text in history:
        parts.append(_turn(role, text))
    parts.append("Assistant: ")
    return "".join(parts)


def _now():
    return datetime.now().astimezone()


def system_prompt(now=None):
    """The configured system prompt, what the app can do, and today's date (changes once a day)."""
    from .actions import capabilities
    now = now or _now()
    return (f"{cfg.SYSTEM_PROMPT}\n"
            f"Commands the app carries out itself when the user types them: {capabilities()}.\n"
            f"Today is {now:%A}, {now.day} {now:%B} {now.year}.")


def time_note(now=None):
    """Appended to the newest user message only, so older turns (and the server's cache) don't change."""
    now = now or _now()
    return f"\n\n(Local time: {f'{now:%H:%M} {now:%Z}'.rstrip()})"


def prepare(history):
    """Fit the history and build the prompt; date and time are computed fresh on every call.

    Returns (fitted history without the time note, prompt). The note goes on the newest user turn
    in the prompt only, never into the stored history.
    """
    now = _now()
    system = system_prompt(now)
    h = list(history)
    note = time_note(now) if h and h[-1][0] == "user" else ""
    if note:
        h[-1] = (h[-1][0], h[-1][1] + note)
    fitted = fit_history(system, h)
    prompt = build_prompt(system, fitted)
    if note:
        role, text = fitted[-1]
        fitted[-1] = (role, text[:-len(note)] if text.endswith(note) else text)
    return fitted, prompt


def estimate_tokens(text):
    """Rough count (~3 chars/token) for when the server can't tokenize."""
    return len(text) // 3 + 1


@functools.lru_cache(maxsize=1024)
def _server_tokens(base, text):
    body = json.dumps({"content": text}).encode()
    req = urllib.request.Request(base + "/tokenize", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return len(json.load(r)["tokens"])


def count_tokens(text):
    """Token count from the server's /tokenize; falls back to an estimate if it can't answer."""
    try:
        return _server_tokens(cfg.BASE, text)
    except Exception:
        return estimate_tokens(text)


def _counter():
    """A count_tokens that stops asking the server after its first failure."""
    failed = False

    def count(text):
        nonlocal failed
        if not failed:
            try:
                return _server_tokens(cfg.BASE, text)
            except Exception:
                failed = True
        return estimate_tokens(text)
    return count


def _turn(role, text):
    return f"{'User' if role == 'user' else 'Assistant'}: {text}<|eot_id|>"


def fit_history(system, history):
    """Drop the oldest turns until the prompt plus a full reply fits the context window.

    Pieces are counted separately (each ends in a special token, so the sum matches the whole);
    +1 for the <|begin_of_text|> the server adds.
    """
    count = _counter()
    budget = cfg.CTX - cfg.MAX_NEW_TOKENS
    fixed = 1 + count(build_prompt(system, []))
    h = list(history)
    sizes = [count(_turn(r, t)) for r, t in h]
    while len(h) > 1 and fixed + sum(sizes) > budget:
        h.pop(0)
        sizes.pop(0)
    if h and fixed + sizes[-1] > budget:                  # one huge message: keep its end
        role, text = h[-1]
        room = budget - fixed - count(_turn(role, ""))
        keep = int(len(text) * room / sizes[-1])
        while keep > 0 and fixed + count(_turn(role, text[-keep:])) > budget:
            keep = int(keep * 0.9)
        h[-1] = (role, text[-keep:] if keep > 0 else "")
    return h


def stream_completion(prompt, on_token, stop_event):
    body = json.dumps({
        "prompt": prompt, "n_predict": cfg.MAX_NEW_TOKENS, "temperature": cfg.TEMPERATURE,
        "top_p": 0.9, "repeat_penalty": 1.1, "stop": STOP,
        "stream": True, "cache_prompt": True,
    }).encode()
    req = urllib.request.Request(cfg.BASE + "/completion", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        for raw in r:
            if stop_event.is_set():
                break
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = json.loads(line[5:].strip())
            if data.get("content"):
                on_token(data["content"])
            if data.get("stop"):
                return data.get("timings", {})
    return {}


def read_text_file(path):
    p = Path(path)
    text = p.read_text(encoding="utf-8", errors="replace")
    truncated = len(text) > cfg.MAX_FILE_CHARS
    return p.name, text[:cfg.MAX_FILE_CHARS], truncated
