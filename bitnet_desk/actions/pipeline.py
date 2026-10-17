"""From a chat message to an action: fast path → command-verb check → model router.

decide_* return what *would* happen without running anything (the eval uses them); handle*()
also runs plans the permission gate lets through on its own.
"""
import subprocess
from dataclasses import dataclass

from . import fastpath, ground, permissions, router
from .permissions import Proposal
from .registry import TOOLS, ActionError, Result

COMMAND_VERBS = {"open", "start", "launch", "download", "find", "run", "play", "show"}


@dataclass(frozen=True)
class Outcome:
    kind: str                           # "chat" | "ask" | "auto" (not run yet) | "done" | "refused"
    proposal: Proposal | None = None
    result: Result | None = None
    message: str = ""                   # refused: what was wrong
    suggestions: tuple = ()             # refused: e.g. close app names
    route: router.Route | None = None   # model path: what the model said


def wants_router(text):
    """Only messages that start with a command verb (after polite openers) or "/" reach the model."""
    t = text.strip()
    if t.startswith("/"):
        return True
    words = fastpath.normalise(t).lower().split()
    return bool(words) and words[0] in COMMAND_VERBS


def _offer(proposal, route=None):
    kind = "ask" if permissions.proposal_needs_approval(proposal) else "auto"
    return Outcome(kind, proposal, route=route)


def decide_fast(text):
    m = fastpath.match(text)
    if isinstance(m, fastpath.Refusal):                 # clear command, missing target: say why
        permissions.refuse(text, "fast", m.tool, {}, m.message)
        return Outcome("refused", message=m.message, suggestions=m.suggestions)
    return _offer(Proposal(text, "fast", m.plan)) if m else None


def decide_model(text, route=None):
    """Ask the model (unless `route` is given), ground its arguments, plan. Runs nothing."""
    query = text.strip().removeprefix("/").strip()
    r = route or router.route(query)
    if r.tool == router.CHAT:
        return Outcome("chat", route=r)
    try:
        how = ground.check(r.tool, r.args, query)
    except ground.Ungrounded as e:                      # the model made the value up: just chat
        permissions.refuse(text, "model", r.tool, r.args, f"ungrounded: {e}")
        return Outcome("chat", route=r)
    try:
        plan = TOOLS[r.tool].make_plan(r.args)
    except (ActionError, KeyError, TypeError) as e:
        message = getattr(e, "message", f"bad arguments: {e}")
        permissions.refuse(text, "model", r.tool, r.args, message)
        if how == "literal":                            # the user named it, it just doesn't exist
            return Outcome("refused", message=message, suggestions=getattr(e, "suggestions", ()), route=r)
        return Outcome("chat", route=r)
    if r.confidence is None or r.confidence < router.THRESHOLD:   # unsure picks were all wrong in the eval
        permissions.refuse(text, "model", r.tool, r.args, f"unsure (p={r.confidence})")
        return Outcome("chat", route=r)
    return _offer(Proposal(text, "model", plan, r.confidence, must_ask=how == "inferred"), r)


def _run_auto(outcome, runner):
    if outcome.kind != "auto":
        return outcome
    result = permissions.run(outcome.proposal, runner=runner)
    return Outcome("done", outcome.proposal, result, route=outcome.route)


def handle(text, runner=subprocess.run, use_model=False):
    """Fast path only by default (instant). With use_model, falls through to the router when the
    command-verb check allows it (blocks ~1–3 s)."""
    outcome = decide_fast(text)
    if outcome is None:
        if not (use_model and wants_router(text)):
            return Outcome("chat")
        outcome = decide_model(text)
    return _run_auto(outcome, runner)


def handle_model(text, runner=subprocess.run):
    """The router step on its own, for the UI's background thread."""
    return _run_auto(decide_model(text), runner)
