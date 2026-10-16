"""Append-only action log (local JSONL). `python -m bitnet_desk.actions log` prints it."""
import json
import os
from datetime import datetime

from .. import config as cfg


def record(text, route, plan, decision, result=None, ms=None, confidence=None, tool=None, args=None,
           error=None):
    """decision: auto | approved | denied | refused (refused = couldn't plan; nothing ran)."""
    if not cfg.ACTION_LOG_ENABLED:
        return
    entry = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "text": text,
        "route": route,
        "tool": plan.tool if plan else tool,
        "args": plan.args if plan else (args or {}),
        "risk": plan.risk.label if plan else None,
        "commands": [list(c) for c in plan.commands] if plan else [],
        "confidence": confidence,
        "decision": decision,
        "result": None if result is None else ("ok" if result.ok else "error"),
        "message": result.message if result else error,
        "ms": ms,
    }
    path = cfg.ACTION_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)   # private to this user
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read(n=50):
    """The last n entries, oldest first. Unreadable lines are skipped."""
    try:
        lines = cfg.ACTION_LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-n:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def format_entry(e):
    what = " ; ".join(" ".join(c) for c in e.get("commands") or []) or json.dumps(e.get("args") or {})
    outcome = e.get("result") or ""
    line = f"{e.get('ts', '')[:19].replace('T', ' ')}  {e.get('decision', ''):8}  {e.get('tool') or '-':13}  {what}"
    if outcome:
        line += f"  → {outcome}"
    if e.get("decision") == "refused" or outcome == "error":
        line += f"  ({e.get('message')})"
    return line

