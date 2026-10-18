"""Step-3 tools: start_project, open_url, download, find_files, run_shell — and the network guard."""
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from bitnet_desk import config
from bitnet_desk.actions import TOOLS, ActionError, Risk, fastpath, handle, permissions, resolve
from bitnet_desk.actions.permissions import Proposal
from bitnet_desk.actions.tools import files as files_tool
from tests.conftest import free_port


def plan_for(tool, **args):
    return TOOLS[tool].make_plan(args)


def run(plan, runner, progress=None):
    return permissions.run(Proposal("test", "fast", plan), approved=True, runner=runner, progress=progress)


@pytest.fixture()
def projects(fake_mac, monkeypatch, reload_config):
    """Write extra config on top of the fake Mac's project."""
    def write(extra=""):
        cfg = fake_mac / "config.toml"
        cfg.write_text('[projects.bitnet-desk]\npath = "~/Projects/bitnet-desk"\naliases = ["bitnet desk"]\n'
                       + extra, encoding="utf-8")
        monkeypatch.setenv("BITNET_CONFIG", str(cfg))
        reload_config()
        assert config.CONFIG_ERRORS == []
    return write


# -- start_project

def test_start_project_opens_editor_app_and_terminal(fake_mac, runner):
    plan = plan_for("start_project", name="bitnet desk")
    folder = str(Path.home() / "Projects" / "bitnet-desk")
    apps = fake_mac / "Applications"
    assert plan.args == {"name": "bitnet-desk"} and plan.risk == Risk.CHANGES
    assert plan.commands == (("open", "-a", str(apps / "Visual Studio Code.app"), folder),
                             ("open", "-a", str(apps / "Utilities" / "Terminal.app"), folder))
    assert "Visual Studio Code and Terminal" in plan.summary
    out = handle("start project bitnet-desk", runner=runner)
    assert out.kind == "ask" and runner.calls == []           # not auto-approved by default


def test_project_editor_and_terminal_are_configurable(projects, fake_mac):
    projects('[projects.notes]\npath = "~/Documents"\neditor = "Notes"\nterminal = false\n')
    plan = plan_for("start_project", name="notes")
    assert plan.commands == (("open", "-a", str(fake_mac / "Applications" / "Notes.app"),
                              str(Path.home() / "Documents")),)


def test_code_cli_only_when_the_app_is_missing(fake_mac, monkeypatch):
    (fake_mac / "Applications" / "Visual Studio Code.app").rmdir()
    monkeypatch.setattr(resolve.shutil, "which", lambda name: "/opt/homebrew/bin/code" if name == "code" else None)
    assert plan_for("start_project", name="bitnet-desk").commands[0] == (
        "/opt/homebrew/bin/code", str(Path.home() / "Projects" / "bitnet-desk"))
    monkeypatch.setattr(resolve.shutil, "which", lambda name: None)
    with pytest.raises(ActionError, match="Visual Studio Code"):
        plan_for("start_project", name="bitnet-desk")


def test_unknown_or_broken_projects_are_refused(projects, monkeypatch, tmp_path, reload_config):
    with pytest.raises(ActionError, match="Your projects: bitnet-desk") as e:
        plan_for("start_project", name="bitnet-dsk")
    assert e.value.suggestions == ("bitnet-desk",)
    projects('[projects.gone]\npath = "~/nowhere"\n')
    with pytest.raises(ActionError, match="isn't a folder"):
        plan_for("start_project", name="gone")
    monkeypatch.setenv("BITNET_CONFIG", str(tmp_path / "empty.toml"))
    reload_config()
    with pytest.raises(ActionError, match="No projects are set up"):
        plan_for("start_project", name="anything")


# -- open_url

@pytest.mark.parametrize("text,url", [("github.com", "https://github.com"),
                                      ("https://news.ycombinator.com", "https://news.ycombinator.com"),
                                      ("<docs.python.org/3/>", "https://docs.python.org/3/"),
                                      ("localhost.dev:8080/x", "https://localhost.dev:8080/x")])
def test_urls_are_normalised(text, url):
    assert resolve.normalise_url(text) == url


@pytest.mark.parametrize("text", ["javascript:alert(1)", "file:///etc/passwd", "mailto:a@b.com", "notes.txt",
                                  "https://bank.com@evil.example", "two words.com", "ftp://x.org/f"])
def test_non_web_or_tricky_urls_are_refused(text):
    with pytest.raises(ActionError):
        resolve.normalise_url(text)


def test_open_url_is_network_and_always_asks(projects, runner):
    projects('[actions]\nauto_approve = ["open_url", "download", "run_shell", "open_app"]\n')
    plan = plan_for("open_url", url="http://example.com")
    assert plan.risk == Risk.NETWORK and plan.commands == (("open", "http://example.com"),)
    assert "not encrypted" in plan.summary
    assert handle("open github.com", runner=runner).kind == "ask" and runner.calls == []


# -- download (against a local test server — still 127.0.0.1 only)

@pytest.fixture()
def web():
    ns = SimpleNamespace(port=free_port(), body=b"hello pdf", length=True)

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/moved":
                self.send_response(302)
                self.send_header("Location", f"http://localhost:{ns.port}/report.pdf")
                self.end_headers()
                return
            if self.path == "/missing.pdf":
                self.send_error(404)
                return
            self.send_response(200)
            if ns.length:
                self.send_header("Content-Length", str(len(ns.body)))
            self.end_headers()
            self.wfile.write(ns.body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", ns.port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    ns.base = f"http://127.0.0.1:{ns.port}"
    yield ns
    srv.shutdown()
    srv.server_close()


def test_download_plan_shows_url_and_destination(fake_mac):
    plan = plan_for("download", url="https://example.com/files/Report%20Q3.pdf")
    dest = Path.home() / "Downloads" / "Report Q3.pdf"
    assert plan.risk == Risk.NETWORK and plan.args["dest"] == str(dest) and plan.commands == ()
    assert plan.shown_commands()[0] == "GET https://example.com/files/Report%20Q3.pdf"
    assert "~/Downloads/Report Q3.pdf" in plan.shown_commands()[1]


def test_download_writes_into_downloads_and_never_overwrites(fake_mac, web, runner):
    existing = Path.home() / "Downloads" / "report.pdf"
    existing.write_bytes(b"keep me")
    seen = []
    result = run(plan_for("download", url=f"{web.base}/report.pdf"), runner, progress=seen.append)
    new = Path.home() / "Downloads" / "report (1).pdf"
    assert result.ok and new.read_bytes() == b"hello pdf" and existing.read_bytes() == b"keep me"
    assert result.items == (str(new),) and seen and seen[-1].startswith("Downloading…")
    assert not list((Path.home() / "Downloads").glob("*.part"))
    if sys.platform == "darwin":
        assert runner.calls[-1][:3] == ["xattr", "-w", "com.apple.quarantine"]


def test_download_size_limit_and_errors_leave_nothing_behind(projects, web, runner):
    projects("[actions]\ndownload_max_mb = 1\n")
    web.body = b"x" * (1_048_576 + 10)
    big = run(plan_for("download", url=f"{web.base}/big.bin"), runner)
    assert not big.ok and "limit" in big.message
    web.length = False                                        # no Content-Length: caught while streaming
    streamed = run(plan_for("download", url=f"{web.base}/big2.bin"), runner)
    assert not streamed.ok and "limit" in streamed.message
    missing = run(plan_for("download", url=f"{web.base}/missing.pdf"), runner)
    assert not missing.ok and "Download failed" in missing.message
    assert list((Path.home() / "Downloads").iterdir()) == []


def test_download_reports_redirects(fake_mac, web, runner):
    result = run(plan_for("download", url=f"{web.base}/moved"), runner)
    assert result.ok and "redirected to localhost" in result.message


# -- find_files

def fake_mdfind(results_by_call):
    calls = []

    def run_(argv, **kw):
        calls.append(argv)
        out = results_by_call[len(calls) - 1] if len(calls) <= len(results_by_call) else []
        return SimpleNamespace(returncode=0, stdout="\n".join(out), stderr="")
    run_.calls = calls
    return run_


def test_find_files_is_read_only_and_filters_noise(fake_mac):
    home = Path.home()
    plan = plan_for("find_files", query="tax  return", kind="pdfs")
    assert plan.risk == Risk.READ_ONLY and plan.args == {"query": "tax return", "kind": "pdf"}
    assert plan.commands == (("mdfind", "-onlyin", str(home), "kind:pdf tax return"),)
    runner = fake_mdfind([[f"{home}/Documents/tax return.pdf", f"{home}/Library/Caches/tax return.pdf",
                           f"{home}/.Trash/tax return.pdf", "/System/elsewhere.pdf"]])
    result = run(plan, runner)
    assert result.ok and result.items == (f"{home}/Documents/tax return.pdf",)
    assert "~/Documents/tax return.pdf" in result.output


def test_find_files_falls_back_to_content_then_names(fake_mac):
    home = Path.home()
    plan = plan_for("find_files", query="invoice")
    runner = fake_mdfind([[], [f"{home}/Documents/march.txt"]])
    assert run(plan, runner).items == (f"{home}/Documents/march.txt",) and len(runner.calls) == 2
    (home / "Documents" / "Invoice-2026.pdf").write_text("x")
    (home / "Documents" / ".hidden-invoice").write_text("x")
    result = run(plan, fake_mdfind([[], []]))                 # Spotlight has nothing (index off)
    assert result.items == (str(home / "Documents" / "Invoice-2026.pdf"),)
    assert "Spotlight found nothing" in result.message
    assert "No files found" in run(plan_for("find_files", query="zebra"), fake_mdfind([[], []])).message


def test_find_results_run_without_asking(fake_mac, monkeypatch):
    runner = fake_mdfind([[f"{Path.home()}/Documents/resume.pdf"]])
    out = handle("where's my resume?", runner=runner)
    assert out.kind == "done" and out.result.items and runner.calls[0][-1] == "resume"


# -- run_shell

def test_shell_plan_shows_the_literal_command_and_always_asks(projects, runner):
    projects('[actions]\nauto_approve = ["run_shell"]\n')
    plan = plan_for("run_shell", command="ls -la ~/Desktop")
    assert plan.risk == Risk.SHELL and plan.commands == (("/bin/zsh", "-c", "ls -la ~/Desktop"),)
    assert plan.shown_commands()[0] == "$ ls -la ~/Desktop" and plan.cwd == str(Path.home())
    out = handle("run `git status`", runner=runner)
    assert out.kind == "ask" and out.proposal.plan.args == {"command": "git status"} and runner.calls == []


@pytest.mark.skipif(not Path("/bin/zsh").exists(), reason="needs zsh")
def test_shell_really_runs_in_home_and_reports_exit_codes(fake_mac):
    ok = run(plan_for("run_shell", command="pwd; echo hi"), subprocess.run)
    assert ok.ok and ok.output.splitlines() == [str(Path.home()), "hi"]
    bad = run(plan_for("run_shell", command="echo oops >&2; exit 3"), subprocess.run)
    assert not bad.ok and "code 3" in bad.message and bad.output == "oops"


def test_shell_timeout(fake_mac):
    def slow(argv, **kw):
        raise subprocess.TimeoutExpired(argv, kw["timeout"])
    assert "Stopped after 60 s" in run(plan_for("run_shell", command="sleep 100"), slow).message


# -- fast path for the new tools

@pytest.mark.parametrize("text,tool,args", [
    ("$ git log -3", "run_shell", {"command": "git log -3"}),
    ("run `npm test`", "run_shell", {"command": "npm test"}),
    ('execute the command "make build"', "run_shell", {"command": "make build"}),
    ("download https://x.org/a.zip into my downloads folder", "download", {"url": "https://x.org/a.zip"}),
    ("go to github.com", "open_url", {"url": "https://github.com"}),
    ("visit https://example.com/x?y=1", "open_url", {"url": "https://example.com/x?y=1"}),
    ("switch to the bitnet desk project", "start_project", {"name": "bitnet-desk"}),
    ("search for files containing budget", "find_files", {"query": "budget"}),
    ("find my tax return pdf", "find_files", {"query": "tax return", "kind": "pdf"}),
    ("where are my vacation photos", "find_files", {"query": "vacation", "kind": "image"}),
])
def test_fast_path_new_tools(fake_mac, text, tool, args):
    m = fastpath.match(text)
    assert m and m.tool == tool and {k: m.args[k] for k in args} == args


@pytest.mark.parametrize("text", ["run a check of my disk space", "run git status", "download the latest python",
                                  "go to bed", "open notes.txt", "find me a good recipe",
                                  "start project moonbase", "where is the nearest cafe"])
def test_fast_path_new_tools_dont_guess(fake_mac, text):
    assert fastpath.match(text) is None


# -- the network guard: only approved network actions may leave 127.0.0.1

@pytest.fixture()
def no_network(monkeypatch):
    real_connect = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise AssertionError(f"network access to {address}")
        return real_connect(self, address)
    monkeypatch.setattr(socket.socket, "connect", guarded)


def test_planning_never_touches_the_network_and_only_network_tools_use_it(fake_mac, no_network, runner):
    samples = {"help": {}, "now": {}, "open_app": {"name": "Safari"}, "open_path": {"path": "~/Documents"},
               "open_url": {"url": "https://example.com"}, "start_project": {"name": "bitnet-desk"},
               "download": {"url": "https://example.com/a.pdf"}, "find_files": {"query": "x"},
               "run_shell": {"command": "true"}, "media": {"action": "play"}, "playlist": {"name": "focus"},
               "volume": {"action": "up"}, "dnd": {"state": "on"}, "screenshot": {}, "lock_screen": {},
               "empty_trash": {}, "routine": {"name": "work-mode"}}
    assert set(samples) == set(TOOLS)                         # a new tool must be added here
    for name, args in samples.items():
        plan = plan_for(name, **args)                         # no tool may look anything up online
        if TOOLS[name].risk < Risk.NETWORK:
            run(plan, runner)                                 # running them can't reach the network either
    with pytest.raises(AssertionError, match="network access"):
        socket.create_connection(("example.com", 443), timeout=1)
