"""Importing this package registers every tool (in the order the system prompt lists them)."""
from . import basic, files, projects, web, system  # noqa: F401
from .. import routines  # noqa: F401,E402  (the routine tool)
