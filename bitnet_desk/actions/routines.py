"""Routines: named lists of commands in config.toml, run behind one approval card.

    [routines.work-mode]
    aliases = ["work mode", "start work"]
    steps = ["start bitnet-desk", "play my focus playlist", "dnd on"]

Every step is a plain command that must match the fast path (no model, no guessing), and is planned
and frozen when the card is built, like any other action. The card lists every step's exact commands.
A routine always asks, even when each step would run by itself; one click covers all of them. Steps
run in order; the first failure stops the routine and the result says which steps ran.
"""
import re
from dataclasses import dataclass

from .. import config as cfg
from .registry import ActionError, Plan, Result, Risk, Tool, register

VERBS = r"(?:start|run|begin|do|activate|enter|switch to|go into|turn on|launch)"


@dataclass(frozen=True)
class Routine:
    name: str
    steps: tuple
    aliases: tuple = ()


def _key(text):
    return " ".join(re.sub(r"[-_]", " ", text.lower()).split())


def configured():
    return {name: Routine(name, tuple(r["steps"]), tuple(r.get("aliases", ())))
            for name, r in cfg.ROUTINES.items()}


def find(text):
    """The routine `text` names ("work mode", "start work mode", "run the work-mode routine"), or None."""
    t = _key(text)
    bare = re.sub(rf"^{VERBS}\s+", "", t)
    bare = re.sub(r"^(?:the|my)\s+", "", bare)
    bare = re.sub(r"^routine\s+|\s+routine$", "", bare)
    for r in configured().values():
        if {t, bare} & {_key(r.name), *(_key(a) for a in r.aliases)}:     # an alias may start with a verb
            return r
    return None


def plan_steps(routine):
    """[(step text, Plan)] — or ActionError naming the first step that can't be done right now."""
    from . import fastpath
    out = []
    for i, step in enumerate(routine.steps, 1):
        m = fastpath.match(step, routines=False)            # routines can't contain routines
        if m is None:
            raise ActionError(f"Routine “{routine.name}”, step {i} “{step}” isn't a command I know.")
        if isinstance(m, fastpath.Refusal):
            raise ActionError(f"Routine “{routine.name}”, step {i} “{step}”: {m.message}")
        out.append((step, m.plan))
    return out


def plan_routine(routine):
    from .registry import TOOLS
    steps = plan_steps(routine)
    shown = []
    for i, (_, plan) in enumerate(steps, 1):
        note = "  (always asks — covered by this card)" if TOOLS[plan.tool].always_ask else ""
        shown.append(f"{i}. {plan.summary}{note}")
        shown += [f"   {line}" for line in plan.shown_commands()]
    return Plan(tool="routine", args={"name": routine.name, "steps": [s for s, _ in steps]},
                risk=max((p.risk for _, p in steps), default=Risk.CHANGES),
                summary=f"Run “{routine.name}”: {len(steps)} step{'s' if len(steps) != 1 else ''}",
                shown=tuple(shown), steps=tuple(p for _, p in steps), done=f"Ran all {len(steps)} steps.")


def _plan(args):
    r = configured().get(args.get("name", ""))
    if r is None:
        raise ActionError(f"There's no routine called “{args.get('name')}” in your settings.",
                          tuple(configured()))
    return plan_routine(r)


def _run(plan, runner, progress):
    """Run the frozen steps in order; stop at the first failure. Each step is logged on its own."""
    from . import log
    from .permissions import run_commands
    from .registry import TOOLS
    name = plan.args["name"]
    lines, ok_count = [], 0
    for i, (text, step) in enumerate(zip(plan.args["steps"], plan.steps), 1):
        if progress:
            progress(f"{i}/{len(plan.steps)} {step.summary}…")
        tool = TOOLS[step.tool]
        try:
            result = tool.run(step, runner, progress) if tool.run else run_commands(step, runner)
        except Exception as e:           # noqa: BLE001 — a broken step is a failed step
            result = Result(False, f"{step.summary} failed: {e}")
        log.record(text, f"routine:{name}", step, "approved", result)
        if not result.ok:
            lines.append(f"✗ {result.message}" + (f" {result.output}" if result.output else ""))
            lines += [f"– {p.summary} (not run)" for p in plan.steps[i:]]
            return Result(False, f"Ran {ok_count} of {len(plan.steps)} steps, then stopped:\n" + "\n".join(lines))
        ok_count += 1
        lines.append(f"✓ {result.message}")
    return Result(True, f"Ran all {len(plan.steps)} steps:\n" + "\n".join(lines))


def config_problems():
    """Steps that aren't commands at all (checked at startup; whether they can run is checked when used)."""
    from . import fastpath
    out = []
    for r in configured().values():
        for i, step in enumerate(r.steps, 1):
            if fastpath.match(step, routines=False) is None:
                out.append(f"routine '{r.name}': step {i} “{step}” isn't a command I know — the routine won't run")
    return out


register(Tool("routine", "routine", "run one of the user's routines", {"name": {"type": "string"}}, Risk.CHANGES,
              _plan, 'run a routine from your settings ("work mode")', run=_run, always_ask=True, model=False))
