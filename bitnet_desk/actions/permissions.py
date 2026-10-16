"""The permission gate — the only code that runs a plan.

Read-only plans run at once. "Changes things" plans run at once only if their tool is in
`[actions] auto_approve`. Network and shell plans always need approval, whatever the config says.
"""
import subprocess
import time
from dataclasses import dataclass

from .. import config as cfg
from . import log
from .registry import TOOLS, Result, Risk

COMMAND_TIMEOUT = 30


@dataclass(frozen=True)
class Proposal:
    """A plan offered to the user, with where it came from."""
    text: str                   # what the user typed
    route: str                  # "fast" or "model"
    plan: object
    confidence: float | None = None


def auto_approved():
    return {n for n in cfg.AUTO_APPROVE if n in TOOLS and TOOLS[n].risk <= Risk.CHANGES}


def config_problems():
    """Messages about auto_approve entries that are ignored."""
    out = []
    for name in cfg.AUTO_APPROVE:
        if name not in TOOLS:
            out.append(f"auto_approve: unknown action '{name}' — ignored")
        elif TOOLS[name].risk > Risk.CHANGES:
            out.append(f"auto_approve: '{name}' is a {TOOLS[name].risk.label} action and always asks — ignored")
    return out


def needs_approval(plan):
    if plan.risk == Risk.READ_ONLY:
        return False
    return not (plan.risk == Risk.CHANGES and plan.tool in auto_approved())


def run(proposal, approved=False, runner=subprocess.run, progress=None):
    """Execute the plan exactly as shown. Raises PermissionError if it needs an approval it didn't get.

    `progress(text)` is called from long-running tools (downloads) with a status line.
    """
    plan = proposal.plan
    ask = needs_approval(plan)
    if ask and not approved:
        raise PermissionError(f"{plan.tool} needs the user's approval")
    t0 = time.monotonic()
    tool = TOOLS[plan.tool]
    try:
        result = tool.run(plan, runner, progress) if tool.run else run_commands(plan, runner)
    except Exception as e:                       # a tool bug is a failed action, not a crash
        result = Result(False, f"{plan.summary} failed: {e}")
    log.record(proposal.text, proposal.route, plan, "approved" if ask else "auto", result,
               ms=round((time.monotonic() - t0) * 1000), confidence=proposal.confidence)
    return result


def deny(proposal):
    log.record(proposal.text, proposal.route, proposal.plan, "denied", confidence=proposal.confidence)


def refuse(text, route, tool, args, error):
    """Record a request that couldn't become a plan (unknown app, missing file, …)."""
    log.record(text, route, None, "refused", tool=tool, args=args, error=error)


def run_commands(plan, runner, timeout=COMMAND_TIMEOUT):
    for argv in plan.commands:
        try:
            proc = runner(list(argv), capture_output=True, text=True, timeout=timeout,
                          stdin=subprocess.DEVNULL, cwd=plan.cwd or None)
        except (OSError, subprocess.SubprocessError) as e:
            return Result(False, f"Couldn't run {argv[0]}: {e}")
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            return Result(False, f"{plan.summary} failed (exit {proc.returncode}).", err[-2000:])
    return Result(True, plan.answer or plan.done or plan.summary)
