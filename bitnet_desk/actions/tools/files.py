"""find_files (Spotlight, with a bounded name search when Spotlight has nothing) and run_shell."""
import os
import subprocess
import time
from pathlib import Path

from ..registry import ActionError, Plan, Result, Risk, Tool, register
from ..resolve import tilde

MAX_RESULTS = 20
WALK_SECONDS = 3.0
SKIP_DIRS = {"Library", "node_modules", "__pycache__", "venv", ".venv", "site-packages"}
KINDS = {"pdf": "pdf", "pdfs": "pdf", "folder": "folder", "folders": "folder", "image": "image",
         "images": "image", "photo": "image", "photos": "image", "picture": "image", "pictures": "image"}
KIND_SUFFIXES = {"pdf": {".pdf"}, "image": {".png", ".jpg", ".jpeg", ".heic", ".gif", ".tiff", ".webp"}}
SHELL_TIMEOUT = 60


def _find_plan(args):
    query = " ".join(str(args.get("query", "")).split())
    kind = KINDS.get(str(args.get("kind") or "").lower())
    if not query:
        raise ActionError("What should I look for?")
    home = str(Path.home())
    if kind:
        commands = (("mdfind", "-onlyin", home, f"kind:{kind} {query}"),)
    else:
        commands = (("mdfind", "-onlyin", home, "-name", query), ("mdfind", "-onlyin", home, query))
    what = f"{kind}s" if kind else "files"
    return Plan(tool="find_files", args={"query": query, **({"kind": kind} if kind else {})},
                risk=Risk.READ_ONLY, summary=f"Search your {what} for “{query}”", commands=commands)


def _visible(path, home):
    try:
        rel = Path(path).relative_to(home)
    except ValueError:
        return False
    return bool(rel.parts) and rel.parts[0] not in SKIP_DIRS and not any(p.startswith(".") for p in rel.parts)


def _walk(home, query, kind):
    """Name search when Spotlight returns nothing (index off or excluded). Bounded in time."""
    words = query.lower().split()
    deadline = time.monotonic() + WALK_SECONDS
    found = []
    for root, dirs, files in os.walk(home):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        names = dirs if kind == "folder" else files
        for n in names:
            low = n.lower()
            if not n.startswith(".") and all(w in low for w in words) and (kind not in KIND_SUFFIXES or
                                                 Path(n).suffix.lower() in KIND_SUFFIXES[kind]):
                found.append(os.path.join(root, n))
        if len(found) >= MAX_RESULTS * 5 or time.monotonic() > deadline:
            break
    return found


def _find_run(plan, runner, progress):
    home = Path.home()
    found = []
    for argv in plan.commands:
        proc = runner(list(argv), capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        if proc.returncode != 0:
            return Result(False, "Spotlight search failed.", (proc.stderr or "").strip()[-2000:])
        found = [p for p in dict.fromkeys(proc.stdout.splitlines()) if p and _visible(p, home)]
        if found:
            break
    note = ""
    if not found:
        found = _walk(home, plan.args["query"], plan.args.get("kind"))
        note = " (Spotlight found nothing, so I searched file names)"
    query = plan.args["query"]
    if not found:
        return Result(True, f"No files found for “{query}”{note}.")
    shown = found[:MAX_RESULTS]
    more = f", showing the first {MAX_RESULTS}" if len(found) > MAX_RESULTS else ""
    return Result(True, f"Found {len(found)} for “{query}”{more}{note}:",
                  "\n".join(tilde(p) for p in shown), items=tuple(shown))


def _shell_plan(args):
    command = str(args.get("command", "")).strip()
    if not command:
        raise ActionError("Which command should I run?")
    home = str(Path.home())
    return Plan(tool="run_shell", args={"command": command}, risk=Risk.SHELL,
                summary="Run a shell command", commands=(("/bin/zsh", "-c", command),), cwd=home,
                shown=(f"$ {command}", f"(zsh, in ~, stops after {SHELL_TIMEOUT} s)"))


def _shell_run(plan, runner, progress):
    try:
        proc = runner(list(plan.commands[0]), capture_output=True, text=True, timeout=SHELL_TIMEOUT,
                      stdin=subprocess.DEVNULL, cwd=plan.cwd)
    except subprocess.TimeoutExpired:
        return Result(False, f"Stopped after {SHELL_TIMEOUT} s.")
    except OSError as e:
        return Result(False, f"Couldn't run it: {e}")
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    output = output[-4000:] if output else ""
    if proc.returncode == 0:
        return Result(True, "Done.", output)
    return Result(False, f"The command exited with code {proc.returncode}.", output)


register(Tool("find_files", "find", "search the user's files by name (Spotlight)",
              {"query": {"type": "string", "maxLength": 80},
               "kind": {"type": "string", "enum": ["pdf", "folder", "image"]}},
              Risk.READ_ONLY, _find_plan, 'find files ("find files named invoice", "where\'s my resume")',
              run=_find_run))
register(Tool("run_shell", "shell", "run a shell command the user typed literally",
              {"command": {"type": "string", "maxLength": 300}}, Risk.SHELL, _shell_plan,
              'run a shell command you type, always with your OK ("run `git status`")', run=_shell_run))
