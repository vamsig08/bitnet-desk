"""start_project: open a configured project folder in the editor and a terminal."""
from pathlib import Path

from ... import config as cfg
from ..registry import ActionError, Plan, Risk, Tool, register
from ..resolve import editor_command, find_app, find_project, tilde


def _start_project(args):
    name, proj = find_project(args["name"])
    folder = Path(proj["path"]).expanduser()
    if not folder.is_dir():
        raise ActionError(f"Project {name}: {tilde(folder)} isn't a folder.")
    editor = proj.get("editor", cfg.EDITOR)
    terminal = proj.get("terminal", cfg.TERMINAL)
    commands = [editor_command(editor, folder)]
    apps = [editor]
    if terminal is not False:
        commands.append(("open", "-a", str(find_app(terminal)), str(folder)))
        apps.append(terminal)
    return Plan(tool="start_project", args={"name": name}, risk=Risk.CHANGES,
                summary=f"Start project {name}: open {tilde(folder)} in {' and '.join(apps)}",
                done=f"Started project {name}.", commands=tuple(commands))


register(Tool("start_project", "project", "open a configured project in the editor and a terminal",
              {"name": {"type": "string", "maxLength": 60}}, Risk.CHANGES, _start_project,
              'start a project from config.toml ("start project myapp")'))
