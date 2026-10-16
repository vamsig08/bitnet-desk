"""Action core: resolution, tools, the permission gate, the action log and the fast path.

Nothing is launched: a fake runner stands in for subprocess.run, and apps/folders live in a fake Mac.
"""
import json
import os
import shlex
from pathlib import Path

import pytest

from bitnet_desk import config
from bitnet_desk.actions import TOOLS, ActionError, Risk, fastpath, handle, log, permissions, resolve
from bitnet_desk.actions.__main__ import main as actions_cli
from bitnet_desk.actions.permissions import Proposal
from bitnet_desk.actions.registry import Plan, Tool


def plan_for(tool, **args):
    return TOOLS[tool].make_plan(args)


def logged():
    return [json.loads(line) for line in config.ACTION_LOG.read_text().splitlines()]


# -- resolving apps and paths

def test_find_app_exact_alias_and_close_spelling(fake_mac):
    assert resolve.find_app("Safari").name == "Safari.app"
    assert resolve.find_app("spotify.app").name == "Spotify.app"
    assert resolve.find_app("vs code").name == "Visual Studio Code.app"
    assert resolve.find_app("spotfy").name == "Spotify.app"          # one letter off
    assert resolve.find_app("Terminal").parent.name == "Utilities"   # one folder level down


def test_unknown_app_is_refused_with_suggestions(fake_mac):
    with pytest.raises(ActionError) as e:
        resolve.find_app("Spot")
    assert "Spot" in e.value.message and "Spotify" in e.value.suggestions


def test_config_aliases_add_to_builtin_ones(fake_mac, tmp_path, monkeypatch, reload_config):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[aliases.apps]\n"Tunes" = "Spotify"\n')
    monkeypatch.setenv("BITNET_CONFIG", str(cfg))
    reload_config()
    assert resolve.find_app("tunes").name == "Spotify.app"
    assert resolve.find_app("vs code").name == "Visual Studio Code.app"


def test_find_path_known_folders_and_paths(fake_mac):
    home = Path.home()
    assert resolve.find_path("downloads") == home / "Downloads"
    assert resolve.find_path("~/Documents") == home / "Documents"
    assert resolve.tilde(home / "Desktop") == "~/Desktop"
    with pytest.raises(ActionError):
        resolve.find_path("~/Nope")
    with pytest.raises(ActionError):
        resolve.find_path("my tax stuff")          # not a path; open_path never guesses


# -- tools and plans

def test_open_app_plan_shows_the_exact_command(fake_mac):
    plan = plan_for("open_app", name="spotify")
    assert plan.args == {"name": "Spotify"} and plan.risk == Risk.CHANGES
    assert plan.commands == (("open", "-a", str(fake_mac / "Applications" / "Spotify.app")),)
    assert plan.shown_commands() == [shlex.join(plan.commands[0])]


def test_open_path_escalates_to_shell_for_things_that_run_code(fake_mac):
    script = Path.home() / "Desktop" / "deploy.command"
    script.write_text("echo hi\n")
    tool_bin = Path.home() / "Desktop" / "tool"
    tool_bin.write_text("#!/bin/sh\n")
    os.chmod(tool_bin, 0o755)
    assert plan_for("open_path", path="~/Documents").risk == Risk.CHANGES
    for p in (script, tool_bin):
        plan = plan_for("open_path", path=str(p))
        assert plan.risk == Risk.SHELL and "runs code" in plan.summary


def test_now_answers_without_commands(fake_mac):
    plan = plan_for("now")
    assert plan.risk == Risk.READ_ONLY and plan.commands == ()
    assert plan.answer.startswith("It's ")


def test_a_plan_cannot_lower_its_tools_risk(monkeypatch):
    sneaky = Tool("sneaky", "sneaky", "", {}, Risk.NETWORK,
                  lambda a: Plan("sneaky", {}, Risk.READ_ONLY, "nothing to see"))
    with pytest.raises(ValueError):
        sneaky.make_plan({})


# -- the permission gate

def test_read_only_runs_without_asking(fake_mac, runner):
    out = handle("what time is it", runner=runner)
    assert out.kind == "done" and out.result.ok and out.result.message.startswith("It's ")
    assert logged()[-1]["decision"] == "auto"


def test_default_auto_approve_opens_apps_and_folders(fake_mac, runner):
    out = handle("open spotify", runner=runner)
    assert out.kind == "done" and out.result.message == "Opened Spotify."
    assert runner.calls == [["open", "-a", str(fake_mac / "Applications" / "Spotify.app")]]
    entry = logged()[-1]
    assert entry["decision"] == "auto" and entry["result"] == "ok" and entry["route"] == "fast"
    assert entry["commands"] == [runner.calls[0]] and entry["risk"] == "changes"


def test_tools_not_in_auto_approve_ask_first(fake_mac, runner, tmp_path, monkeypatch, reload_config):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[actions]\nauto_approve = ["now"]\n')
    monkeypatch.setenv("BITNET_CONFIG", str(cfg))
    reload_config()
    out = handle("open spotify", runner=runner)
    assert out.kind == "ask" and runner.calls == []
    assert not config.ACTION_LOG.exists()                    # nothing happened yet
    with pytest.raises(PermissionError):
        permissions.run(out.proposal, runner=runner)         # no sneaking past the gate
    assert runner.calls == []
    result = permissions.run(out.proposal, approved=True, runner=runner)
    assert result.ok and len(runner.calls) == 1
    assert logged()[-1]["decision"] == "approved"


def test_escalated_plans_ask_even_if_their_tool_is_auto_approved(fake_mac, runner):
    script = Path.home() / "Desktop" / "deploy.command"
    script.write_text("echo hi\n")
    out = handle(f"open {script}", runner=runner)
    assert out.kind == "ask" and out.proposal.plan.risk == Risk.SHELL and runner.calls == []


def test_network_and_shell_tools_are_never_auto_approved(monkeypatch, tmp_path, reload_config):
    for name, risk in (("fetch", Risk.NETWORK), ("sh", Risk.SHELL)):
        monkeypatch.setitem(TOOLS, name, Tool(name, name, "", {}, risk, None))
    cfg = tmp_path / "config.toml"
    cfg.write_text('[actions]\nauto_approve = ["fetch", "sh", "open_app", "bogus"]\n')
    monkeypatch.setenv("BITNET_CONFIG", str(cfg))
    reload_config()
    assert permissions.auto_approved() == {"open_app"}
    assert permissions.needs_approval(Plan("fetch", {}, Risk.NETWORK, "x"))
    problems = " ".join(permissions.config_problems())
    assert "'fetch' is a network action" in problems and "'sh' is a shell action" in problems
    assert "unknown action 'bogus'" in problems


def test_denied_and_refused_are_logged(fake_mac):
    plan = plan_for("open_app", name="Notes")
    permissions.deny(Proposal("open notes", "fast", plan))
    permissions.refuse("open spot", "model", "open_app", {"name": "spot"}, "I can't find an app called “spot”.")
    denied, refused = logged()[-2:]
    assert denied["decision"] == "denied" and denied["result"] is None
    assert refused["decision"] == "refused" and refused["tool"] == "open_app" and "spot" in refused["message"]


def test_failed_command_is_reported_and_logged(fake_mac, runner):
    runner.returncode, runner.stderr = 1, "LSOpenURLsWithRole() failed"
    out = handle("open notes", runner=runner)
    assert not out.result.ok and "LSOpen" in out.result.output
    assert logged()[-1]["result"] == "error"


def test_log_can_be_turned_off(fake_mac, runner, tmp_path, monkeypatch, reload_config):
    cfg = tmp_path / "config.toml"
    cfg.write_text("[actions]\nlog = false\n")
    monkeypatch.setenv("BITNET_CONFIG", str(cfg))
    reload_config()
    handle("open notes", runner=runner)
    assert runner.calls and not config.ACTION_LOG.exists()


def test_log_file_is_private_and_cli_prints_it(fake_mac, runner, capsys):
    handle("open notes", runner=runner)
    assert config.ACTION_LOG.stat().st_mode & 0o077 == 0
    actions_cli(["log", "-n", "5"])
    out = capsys.readouterr().out
    assert "auto" in out and "open_app" in out and "Notes.app" in out and "→ ok" in out


# -- fast path

@pytest.mark.parametrize("text", ["Please open Notes.", "can you open notes?", "open notes please",
                                  "open the notes app", "launch Notes", "start notes"])
def test_fast_path_phrasings(fake_mac, text):
    m = fastpath.match(text)
    assert m and m.tool == "open_app" and m.args == {"name": "Notes"}


@pytest.mark.parametrize("text", ["can you open calculator for me", "could you please launch calculator",
                                  "will you open calculator?", "open calculator please, thanks",
                                  "hey, open calculator for me", "Kindly open Calculator."])
def test_polite_wrappers_around_commands(fake_mac, text):
    m = fastpath.match(text)
    assert m and m.tool == "open_app" and m.args == {"name": "Calculator"}


@pytest.mark.parametrize("text", ["can you explain how to open a file?", "could you write me a poem for my mom",
                                  "please tell me a joke", "can you open up about your feelings?"])
def test_polite_wrappers_dont_make_commands(fake_mac, text):
    assert fastpath.match(text) is None


@pytest.mark.parametrize("text", ["what's the time", "What time is it right now?", "what’s the date today",
                                  "what day is it", "time", "what time is it today", "whats todays date",
                                  "what day of the week is it", "what year is it", "tell me the time",
                                  "do you know what time it is?", "what's the current date and time",
                                  "is it friday yet?", "can you tell me what time it is please",
                                  "what time is it in here"])
def test_fast_path_time(fake_mac, text):
    assert fastpath.match(text).tool == "now"


@pytest.mark.parametrize("text", ["what time is it in Tokyo", "what time is the next SpaceX launch",
                                  "how many days until Christmas", "what day is christmas",
                                  "what date was the moon landing", "tell me about time travel"])
def test_other_time_questions_are_not_the_clock(fake_mac, text):
    assert fastpath.match(text) is None


@pytest.mark.parametrize("text", ["open a can of worms", "start thinking about lunch", "show me how loops work",
                                  "open the report I wrote", "what time is the meeting", "open ~/nope",
                                  "notes", "tell me about Safari"])
def test_fast_path_doesnt_guess(fake_mac, text):
    assert fastpath.match(text) is None
    assert handle(text).kind == "chat"


def test_unknown_names_fall_through_instead_of_failing(fake_mac):
    assert fastpath.match("open spot") is None            # model path/chat decides, not a wrong app


@pytest.mark.parametrize("text", ["what can you do?", "help", "Could you list your commands", "what else can you do"])
def test_help_lists_the_real_commands(fake_mac, text):
    out = handle(text)
    assert out.kind == "done" and 'open an app ("open Safari")' in out.result.message


def test_help_inside_a_request_is_chat(fake_mac):
    assert fastpath.match("can you help me write a cover letter?") is None
