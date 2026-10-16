""""That was wrong": real-use mistakes saved locally, then turned into action-eval cases.

The misses file (private JSONL next to the action log) holds what the user typed, what the app did,
and an optional note. `python -m bitnet_desk.actions misses` walks through new ones and appends eval
cases to evals/actions.jsonl, so everyday use keeps growing the eval.
"""
import json
import os
from datetime import datetime

from .. import config as cfg
from .registry import TOOLS

MAX_REPLY = 1000


def describe_chat(reply):
    return {"kind": "chat", "reply": reply[:MAX_REPLY]}


def describe_action(proposal, decision, result=None):
    plan = proposal.plan
    return {"kind": "action", "route": proposal.route, "tool": plan.tool, "args": plan.args,
            "summary": plan.summary, "decision": decision, "confidence": proposal.confidence,
            "result": None if result is None else result.message}


def describe_refusal(message):
    return {"kind": "refused", "message": message}


def record(text, what, note=""):
    """Save one miss. `what` comes from describe_*()."""
    entry = {"ts": datetime.now().astimezone().isoformat(timespec="seconds"), "text": text,
             "did": what, "note": note.strip(), "converted": False}
    path = cfg.MISSES
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def read():
    try:
        lines = cfg.MISSES.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def _save(entries):
    fd = os.open(cfg.MISSES, os.O_WRONLY | os.O_TRUNC | os.O_CREAT, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(e, ensure_ascii=False) + "\n" for e in entries)


def expected_route(text, tool):
    """Where the pipeline should handle it: the fast path if it already gets it right, else the
    model if the command-verb check lets it through, else chat (a known gate miss for actions)."""
    from . import fastpath
    from .pipeline import wants_router
    m = fastpath.match(text)
    if m and m.tool == tool:
        return "fast"
    return "model" if wants_router(text) else "chat"


def did_line(did):
    if did.get("kind") == "action":
        return f"{did.get('decision')} via {did.get('route')}: {did.get('summary')}"
    if did.get("kind") == "refused":
        return f"refused: {did.get('message')}"
    return f"chat reply: {did.get('reply', '')[:120]!r}"


def convert(ask=input, say=print, cases_path=None):
    """Interactive: for each new miss, ask for the right tool and arguments and append an eval case.

    Answers: a tool name (or "chat"), Enter to skip for now, "d" to drop the miss.
    Returns the number of cases added.
    """
    from . import evalkit
    cases_path = cases_path or evalkit.CASES
    entries = read()
    todo = [e for e in entries if not e.get("converted")]
    if not todo:
        say(f"No new misses in {cfg.MISSES}.")
        return 0
    cases = evalkit.load_cases(cases_path)
    next_id = max((c["id"] for c in cases), default=0) + 1
    names = ["chat"] + list(TOOLS)
    added = []
    for e in todo:
        say(f"\n“{e['text']}”\n  the app: {did_line(e['did'])}" + (f"\n  your note: {e['note']}" if e["note"] else ""))
        while True:
            tool = ask(f"  right answer? [{' / '.join(names)}] (Enter = skip, d = drop): ").strip()
            if tool in names or tool in ("", "d"):
                break
            say(f"  unknown: {tool!r}")
        if tool == "":
            continue
        if tool == "d":
            e["converted"] = "dropped"
            continue
        args = {}
        if tool != "chat":
            did = e["did"]
            for arg in TOOLS[tool].args:
                default = did.get("args", {}).get(arg, "") if did.get("tool") == tool else ""
                hint = f" [{default}]" if default else ""
                optional = " (optional)" if arg in TOOLS[tool].optional else ""
                value = ask(f"  {arg}{optional}{hint}: ").strip() or default
                if value:
                    args[arg] = value
        case = {"id": next_id, "text": e["text"], "tool": tool, "args": args,
                "route": expected_route(e["text"], tool),
                "note": f"from real use {e['ts'][:10]}" + (f": {e['note']}" if e["note"] else "")}
        added.append(case)
        next_id += 1
        e["converted"] = case["id"]
        say(f"  → case #{case['id']} ({case['route']})")
    if added:
        with open(cases_path, "a", encoding="utf-8") as f:
            f.writelines(json.dumps(c, ensure_ascii=False) + "\n" for c in added)
    _save(entries)
    say(f"\nAdded {len(added)} case(s) to {cases_path}. Run the tests and "
        "`python -m bitnet_desk.actions eval` to see how the app does on them.")
    return len(added)
