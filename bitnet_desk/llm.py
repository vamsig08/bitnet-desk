"""Prompt formatting, context fitting and the streaming /completion client.

All model access goes through this module so the model can be swapped by config.
"""
import json
import urllib.request
from pathlib import Path

from . import config as cfg

STOP = ["<|eot_id|>", "<|end_of_text|>", "\nUser:"]


def build_prompt(system, history):
    """BitNet b1.58 2B4T chat format. The server adds <|begin_of_text|> itself."""
    parts = [f"System: {system}<|eot_id|>"]
    for role, text in history:
        parts.append(f"{'User' if role == 'user' else 'Assistant'}: {text}<|eot_id|>")
    parts.append("Assistant: ")
    return "".join(parts)


def fit_history(system, history):
    """Drop the oldest turns until the prompt fits the context window."""
    limit = cfg.MAX_PROMPT_CHARS
    h = list(history)
    while len(h) > 1 and len(build_prompt(system, h)) > limit:
        h.pop(0)
    if h and len(build_prompt(system, h)) > limit:   # one huge message
        role, text = h[-1]
        h[-1] = (role, text[-limit // 2:])
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
