"""help, now, open_app, open_path."""
from datetime import datetime

from ..registry import Plan, Risk, Tool, capabilities, register
from ..resolve import find_app, find_path, path_risk, tilde


def _help(args):
    return Plan(tool="help", args={}, risk=Risk.READ_ONLY, summary="List what I can do",
                answer=f"Commands I carry out on your Mac: {capabilities()}. "
                       "Anything else, just ask — I'll answer in chat (answers, explanations, "
                       "writing, code).")


def _now(args):
    now = datetime.now().astimezone()
    return Plan(tool="now", args={}, risk=Risk.READ_ONLY, summary="Tell the current date and time",
                answer=f"It's {now:%H:%M} on {now:%A}, {now.day} {now:%B} {now.year}.")


def _open_app(args):
    app = find_app(args["name"])
    return Plan(tool="open_app", args={"name": app.stem}, risk=Risk.CHANGES,
                summary=f"Open {app.stem}", done=f"Opened {app.stem}.",
                commands=(("open", "-a", str(app)),))


def _open_path(args):
    p = find_path(args["path"])
    risk = path_risk(p)
    what = "folder" if p.is_dir() and p.suffix.lower() != ".app" else "file"
    summary = f"Open {what} {tilde(p)}"
    if risk >= Risk.SHELL:
        summary += " — this runs code"
    return Plan(tool="open_path", args={"path": str(p)}, risk=risk, summary=summary,
                done=f"Opened {tilde(p)}.", commands=(("open", str(p)),))


def _string(max_len):
    return {"type": "string", "maxLength": max_len}


register(Tool("help", "help", "list what the app can do", {}, Risk.READ_ONLY, _help))
register(Tool("now", "now", "tell the current date and time", {}, Risk.READ_ONLY, _now,
              'tell the date and time ("what time is it")'))
register(Tool("open_app", "app", "launch a Mac application",
              {"name": _string(60)}, Risk.CHANGES, _open_app, 'open an app ("open Safari")'))
register(Tool("open_path", "path", "open a file or folder the user named",
              {"path": _string(200)}, Risk.CHANGES, _open_path,
              'open a file or folder ("open my Downloads folder")'))
