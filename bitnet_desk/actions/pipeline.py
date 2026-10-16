"""From a chat message to an action: fast path → (later: command-verb check → model router).

handle() either says "this is chat", returns a proposal that needs the user's approval, or runs a
plan the permission gate lets through on its own and returns the result.
"""
import subprocess
from dataclasses import dataclass

from . import fastpath, permissions
from .permissions import Proposal
from .registry import Result


@dataclass(frozen=True)
class Outcome:
    kind: str                           # "chat" | "ask" | "done"
    proposal: Proposal | None = None
    result: Result | None = None


def handle(text, runner=subprocess.run):
    m = fastpath.match(text)
    if m is None:
        return Outcome("chat")
    proposal = Proposal(text, "fast", m.plan)
    if permissions.needs_approval(m.plan):
        return Outcome("ask", proposal)
    return Outcome("done", proposal, permissions.run(proposal, runner=runner))
