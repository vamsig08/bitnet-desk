"""Actions: tools behind one permission system. Design: docs/actions-design.md"""
from . import tools  # noqa: F401  (registers every tool)
from .pipeline import Outcome, handle
from .registry import TOOLS, ActionError, Plan, Result, Risk

__all__ = ["TOOLS", "ActionError", "Outcome", "Plan", "Result", "Risk", "handle"]
