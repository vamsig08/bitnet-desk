"""Importing this package registers every tool (in the order the system prompt lists them)."""
from . import basic, files, projects, web, system  # noqa: F401
