"""open_url and download — the only tools that cause network traffic, so they always ask first.

Nothing here touches the network while planning: the card is built from the URL text alone.
"""
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from ... import config as cfg
from ..registry import Plan, Result, Risk, Tool, register
from ..resolve import download_name, normalise_url, tilde, unique_path

CHUNK = 256 * 1024
URL_ARG = {"type": "string", "maxLength": 300, "pattern": '^[^ "]+$'}   # the model wrote "x. org" without it


def _open_url(args):
    url = normalise_url(args["url"])
    summary = f"Open {url} in your browser"
    if url.lower().startswith("http://"):
        summary += " (not encrypted)"
    return Plan(tool="open_url", args={"url": url}, risk=Risk.NETWORK, summary=summary,
                done=f"Opened {url}.", commands=(("open", url),))


def _download_plan(args):
    url = normalise_url(args["url"])
    dest = unique_path(Path.home() / "Downloads" / download_name(url))
    note = " (not encrypted)" if url.lower().startswith("http://") else ""
    return Plan(tool="download", args={"url": url, "dest": str(dest)}, risk=Risk.NETWORK,
                summary=f"Download {dest.name} to ~/Downloads{note}",
                shown=(f"GET {url}", f"→ {tilde(dest)}  (up to {cfg.DOWNLOAD_MAX_MB} MB; never overwrites)"))


def _mb(n):
    return f"{n / 1_048_576:.1f} MB"


def _download_run(plan, runner, progress):
    url = plan.args["url"]
    dest = unique_path(plan.args["dest"])          # in case a file appeared since the card was shown
    part = dest.with_name(dest.name + ".part")
    limit = cfg.DOWNLOAD_MAX_MB * 1_048_576
    dest.parent.mkdir(parents=True, exist_ok=True)
    got = 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "BitNetDesk"})
        with urllib.request.urlopen(req, timeout=30) as r:
            final = r.geturl()
            size = int(r.headers.get("Content-Length") or 0)
            if size > limit:
                return Result(False, f"Not downloaded: {_mb(size)} is over the {cfg.DOWNLOAD_MAX_MB} MB limit.")
            with open(part, "xb") as f:
                while chunk := r.read(CHUNK):
                    got += len(chunk)
                    if got > limit:
                        raise ValueError(f"stopped at the {cfg.DOWNLOAD_MAX_MB} MB limit")
                    f.write(chunk)
                    if progress:
                        progress(f"Downloading… {_mb(got)}" + (f" of {_mb(size)}" if size else ""))
        part.rename(dest)
    except (urllib.error.URLError, OSError, ValueError) as e:
        part.unlink(missing_ok=True)
        return Result(False, f"Download failed: {getattr(e, 'reason', e)}")
    if sys.platform == "darwin":                   # Gatekeeper treats it like a browser download
        runner(["xattr", "-w", "com.apple.quarantine", f"0081;{int(time.time()):x};BitNetDesk;", str(dest)],
               capture_output=True, text=True, timeout=10)
    msg = f"Downloaded {tilde(dest)} ({_mb(got)})."
    if urlsplit(final).hostname != urlsplit(url).hostname:
        msg += f" The server redirected to {urlsplit(final).hostname}."
    return Result(True, msg, items=(str(dest),))


register(Tool("open_url", "url", "open a web page the user named", {"url": URL_ARG},
              Risk.NETWORK, _open_url, 'open a web page ("open github.com")'))
register(Tool("download", "download", "download a file from a URL the user gave into ~/Downloads",
              {"url": URL_ARG}, Risk.NETWORK, _download_plan,
              'download a file to Downloads ("download https://…")', run=_download_run))
