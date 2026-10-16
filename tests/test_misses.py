"""'That was wrong' reports: saving misses and turning them into eval cases."""
import json
import shutil

from bitnet_desk import config
from bitnet_desk.actions import TOOLS, Result, evalkit, misses
from bitnet_desk.actions.__main__ import main as actions_cli
from bitnet_desk.actions.permissions import Proposal


def scripted(*answers):
    it = iter(answers)
    return lambda prompt: next(it)


def test_misses_are_saved_privately(fake_mac):
    plan = TOOLS["open_app"].make_plan({"name": "Music"})
    misses.record("play some tunes", misses.describe_action(Proposal("play some tunes", "model", plan, 0.9),
                                                            "approved", Result(True, "Opened Music.")),
                  note="wanted Spotify")
    misses.record("what is BitNet", misses.describe_chat("x" * 5000))
    first, second = misses.read()
    assert first["text"] == "play some tunes" and first["note"] == "wanted Spotify" and not first["converted"]
    assert first["did"]["tool"] == "open_app" and first["did"]["route"] == "model"
    assert first["did"]["result"] == "Opened Music."
    assert len(second["did"]["reply"]) == misses.MAX_REPLY
    assert config.MISSES.stat().st_mode & 0o077 == 0


def test_convert_appends_cases_and_remembers_what_it_did(fake_mac, tmp_path):
    cases_file = tmp_path / "actions.jsonl"
    shutil.copy(evalkit.CASES, cases_file)
    before = evalkit.load_cases(cases_file)
    plan = TOOLS["open_app"].make_plan({"name": "Music"})
    misses.record("play some tunes", misses.describe_action(Proposal("play some tunes", "model", plan), "approved"),
                  note="wanted Spotify")
    misses.record("is friday a holiday?", misses.describe_chat("No."))
    misses.record("hmm", misses.describe_chat("?"))
    misses.record("later", misses.describe_chat("?"))
    said = []
    added = misses.convert(ask=scripted("open_app", "Spotify",      # case 1: right tool + arg
                                        "nonsense", "chat",          # case 2: retry after a typo
                                        "d",                         # case 3: drop
                                        ""),                         # case 4: skip for now
                           say=said.append, cases_path=cases_file)
    assert added == 2
    new = evalkit.load_cases(cases_file)[len(before):]
    assert [c["id"] for c in new] == [before[-1]["id"] + 1, before[-1]["id"] + 2]
    assert new[0]["tool"] == "open_app" and new[0]["args"] == {"name": "Spotify"} and new[0]["route"] == "model"
    assert "from real use" in new[0]["note"] and "wanted Spotify" in new[0]["note"]
    assert new[1] == {**new[1], "tool": "chat", "args": {}, "route": "chat"}
    assert [e["converted"] for e in misses.read()] == [new[0]["id"], new[1]["id"], "dropped", False]
    assert any("unknown: 'nonsense'" in s for s in said)
    # the skipped one comes back next time; the rest don't
    assert misses.convert(ask=scripted(""), say=said.append, cases_path=cases_file) == 0


def test_argument_defaults_come_from_what_the_app_did(fake_mac, tmp_path):
    cases_file = tmp_path / "actions.jsonl"
    shutil.copy(evalkit.CASES, cases_file)
    plan = TOOLS["open_app"].make_plan({"name": "Notes"})
    misses.record("open notes", misses.describe_action(Proposal("open notes", "fast", plan), "denied"))
    misses.convert(ask=scripted("open_app", ""), say=lambda s: None, cases_path=cases_file)
    case = evalkit.load_cases(cases_file)[-1]
    assert case["args"] == {"name": "Notes"} and case["route"] == "fast"     # fast path already gets it


def test_converted_cases_keep_the_eval_file_valid(fake_mac, tmp_path, monkeypatch):
    cases_file = tmp_path / "actions.jsonl"
    shutil.copy(evalkit.CASES, cases_file)
    misses.record("is it a holiday", misses.describe_chat("No idea."))
    misses.convert(ask=scripted("chat"), say=lambda s: None, cases_path=cases_file)
    monkeypatch.setattr(evalkit, "CASES", cases_file)
    from tests.test_actions_eval import test_eval_cases_are_well_formed
    test_eval_cases_are_well_formed()


def test_cli_lists_misses(fake_mac, capsys):
    actions_cli(["misses", "--list"])
    assert "No misses saved yet" in capsys.readouterr().out
    misses.record("open spot", misses.describe_refusal("I can't find an app called “spot”."))
    actions_cli(["misses", "--list"])
    out = capsys.readouterr().out
    assert "new" in out and "'open spot'" in out and "refused" in out
