"""Phase 3 step 3: routines — one card for every step, run in order, stop at the first failure."""
import json
from pathlib import Path

import pytest

from bitnet_desk import config
from bitnet_desk.actions import TOOLS, fastpath, permissions, pipeline, router, routines
from bitnet_desk.ui import cards


def logged():
    return [json.loads(line) for line in config.ACTION_LOG.read_text().splitlines()]


def add_config(text, reload_config):
    f = Path(config.CONFIG_PATH)
    f.write_text(f.read_text() + "\n" + text)
    reload_config()


@pytest.mark.parametrize("text", ["work mode", "Work Mode", "start work mode", "run the work-mode routine",
                                  "routine work mode", "start work", "please start work mode"])
def test_routine_names_and_aliases(fake_mac, text):
    m = fastpath.match(text)
    assert isinstance(m, fastpath.Match) and m.tool == "routine" and m.args["name"] == "work-mode"


@pytest.mark.parametrize("text", ["what's my morning routine", "work", "work mode please explain", "mode"])
def test_not_routines(fake_mac, text):
    m = fastpath.match(text)
    assert m is None or m.tool != "routine"


def test_one_card_lists_every_step_and_always_asks(fake_mac, reload_config, runner):
    add_config('[actions]\nauto_approve = ["routine", "start_project", "playlist", "dnd"]\n', reload_config)
    assert "auto_approve: 'routine' always asks — ignored" in permissions.config_problems()
    out = pipeline.handle("work mode", runner=runner)
    assert out.kind == "ask" and not runner.calls                        # nothing ran before the click
    plan = out.proposal.plan
    assert plan.summary == "Run “work-mode”: 3 steps" and [p.tool for p in plan.steps] == \
        ["start_project", "playlist", "dnd"]
    shown = plan.shown_commands()
    assert shown[0].startswith("1. Start project bitnet-desk") and shown[1].startswith("   open -a ")
    assert "   shortcuts run 'BitNet DND On'" in shown
    card = cards.card_for(out.proposal)
    assert "always asks first" in card.note and "2. Play your focus playlist (Spotify)" in card.commands


def test_steps_run_in_order_and_each_is_logged(fake_mac, runner):
    out = pipeline.handle("work mode", runner=runner)
    r = permissions.run(out.proposal, approved=True, runner=runner)
    assert r.ok and r.message.startswith("Ran all 3 steps:") and "✓ Do Not Disturb is on." in r.message
    assert [c[0] for c in runner.calls] == ["open", "open", "osascript", "shortcuts"]
    entries = logged()
    assert [(e["route"], e["tool"]) for e in entries] == [
        ("routine:work-mode", "start_project"), ("routine:work-mode", "playlist"), ("routine:work-mode", "dnd"),
        ("fast", "routine")]
    assert entries[-1]["args"]["steps"] == ["start bitnet-desk", "play my focus playlist", "dnd on"]


def test_stops_at_the_first_failure_and_says_which_steps_ran(fake_mac, runner):
    out = pipeline.handle("work mode", runner=runner)

    def fail_osascript(argv, **kw):
        runner.returncode = 1 if argv[0] == "osascript" else 0
        runner.stderr = "Spotify got an error" if argv[0] == "osascript" else ""
        return runner(argv, **kw)
    progress = []
    r = permissions.run(out.proposal, approved=True, runner=fail_osascript, progress=progress.append)
    assert not r.ok
    assert r.message.splitlines() == [
        "Ran 1 of 3 steps, then stopped:", "✓ Started project bitnet-desk.",
        "✗ Play your focus playlist (Spotify) failed (exit 1). Spotify got an error",
        "– Turn Do Not Disturb on (not run)"]
    assert "shortcuts" not in [c[0] for c in runner.calls]               # the last step never ran
    assert progress[:2] == ["1/3 Start project bitnet-desk: open ~/Projects/bitnet-desk in Visual Studio Code and "
                            "Terminal…", "2/3 Play your focus playlist (Spotify)…"]
    assert [e["result"] for e in logged()] == ["ok", "error", "error"]   # step, step, the routine


def test_a_step_that_cant_be_done_now_refuses_the_routine(fake_mac, monkeypatch, reload_config, runner):
    monkeypatch.setenv("BITNET_SHORTCUTS", "")                           # no Do Not Disturb Shortcuts
    reload_config()
    out = pipeline.handle("work mode", runner=runner)
    assert out.kind == "refused" and "step 3 “dnd on”" in out.message and "Shortcuts app" in out.message
    assert not runner.calls


def test_unknown_and_nested_steps(fake_mac, reload_config, runner):
    add_config('[routines.weird]\nsteps = ["volume 20", "make me a sandwich"]\n\n'
               '[routines.nested]\nsteps = ["work mode"]\n', reload_config)
    problems = routines.config_problems()
    assert "routine 'weird': step 2 “make me a sandwich” isn't a command I know — the routine won't run" in problems
    assert any("routine 'nested': step 1 “work mode”" in p for p in problems)   # no routines inside routines
    out = pipeline.handle("weird", runner=runner)
    assert out.kind == "refused" and "isn't a command I know" in out.message


def test_risk_is_the_highest_step(fake_mac, reload_config):
    add_config('[routines.deploy]\nsteps = ["volume 10", "$ make deploy"]\n', reload_config)
    out = pipeline.decide_fast("run deploy")
    assert out.proposal.plan.risk.label == "shell" and cards.card_for(out.proposal).badge == "SHELL"


def test_routine_settings(tmp_path, monkeypatch, reload_config):
    f = tmp_path / "c.toml"
    f.write_text('[routines.a]\naliases = ["x"]\n\n[routines.b]\nsteps = []\n\n[routines.c]\nsteps = ["mute"]\nwhen = 9\n')
    monkeypatch.setenv("BITNET_CONFIG", str(f))
    reload_config()
    assert set(config.ROUTINES) == {"c"}
    errors = " ".join(config.CONFIG_ERRORS)
    assert "routine 'a' needs steps" in errors and "routine 'b' needs steps" in errors
    assert "unknown setting 'routines.c.when'" in errors


def test_routines_are_fast_path_only(fake_mac):
    assert TOOLS["routine"].always_ask and "routine" not in router.labels()
