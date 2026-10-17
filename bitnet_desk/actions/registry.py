"""Tools, risk levels and plans.

A tool turns arguments into a frozen Plan (or raises ActionError). Tools never run anything
themselves: only the permission gate (permissions.py) executes a plan, and it executes exactly the
commands the plan shows.
"""
import shlex
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable


class Risk(IntEnum):
    READ_ONLY = 0   # reads local state only
    CHANGES = 1     # launches/opens things, writes local files
    NETWORK = 2     # causes network traffic (download, opening a URL)
    SHELL = 3       # runs code: a shell command, or opening something executable

    @property
    def label(self):
        return self.name.lower().replace("_", "-")


class ActionError(Exception):
    """A request that can't become a plan (unknown app, missing path, …). Nothing ran."""

    def __init__(self, message, suggestions=()):
        super().__init__(message)
        self.message = message
        self.suggestions = tuple(suggestions)


@dataclass(frozen=True)
class Plan:
    tool: str
    args: dict                  # resolved, canonical arguments
    risk: Risk                  # never lower than the tool's own risk
    summary: str                # what will happen, e.g. "Open Spotify"
    commands: tuple = ()        # argv tuples — shown to the user and executed verbatim
    done: str = ""              # message after success, e.g. "Opened Spotify."
    answer: str = ""            # read-only tools that answer directly (e.g. the time)
    cwd: str = ""               # working directory for commands ("" = inherit)
    shown: tuple = ()           # what the card shows when there are no commands (e.g. a download)

    def shown_commands(self):
        return list(self.shown) or [shlex.join(argv) for argv in self.commands]


@dataclass(frozen=True)
class Result:
    ok: bool
    message: str
    output: str = ""
    items: tuple = ()           # paths the UI can offer to open (find_files)


@dataclass(frozen=True)
class Tool:
    name: str                   # code name: config, log and eval use it
    label: str                  # short router label shown to the model (distinct first token)
    summary: str                # one line for the model and for "what can you do?"
    args: dict                  # JSON schema of the arguments
    risk: Risk                  # base risk
    plan: Callable = field(repr=False)   # args -> Plan, or raise ActionError
    example: str = ""           # for the system prompt: 'open an app ("open Safari")'
    run: Callable = field(default=None, repr=False)   # (plan, runner, progress) -> Result; default runs commands
    optional: tuple = ()        # argument names the model may leave out
    always_ask: bool = False    # never auto-approved, whatever the config says (lock screen, empty Trash)
    model: bool = True          # offered to the model router; False = fast path only
    hides_ui: bool = False      # the command box gets out of the way first (screenshots)

    def make_plan(self, args):
        plan = self.plan(args)
        if plan.risk < self.risk:
            raise ValueError(f"{self.name}: a plan can't lower the tool's risk")
        return plan


TOOLS = {}


def capabilities():
    """What the app can do, for the system prompt and `help`: 'open an app ("open Safari"); …'."""
    return "; ".join(t.example for t in TOOLS.values() if t.example)


def register(tool):
    if tool.name in TOOLS:
        raise ValueError(f"duplicate tool {tool.name}")
    TOOLS[tool.name] = tool
    return tool
