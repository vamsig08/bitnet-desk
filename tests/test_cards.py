"""Approval cards: what they show, and that the widget's buttons call back."""
from pathlib import Path

import pytest

from bitnet_desk.actions import TOOLS, Result, Risk
from bitnet_desk.actions.permissions import Proposal
from bitnet_desk.ui import cards


def test_card_shows_the_exact_command_and_risk(fake_mac):
    plan = TOOLS["open_app"].make_plan({"name": "Spotify"})
    card = cards.card_for(Proposal("open spotify", "fast", plan))
    assert card.title == "Open Spotify" and card.badge == "CHANGES"
    assert card.commands == plan.shown_commands()[0] and "Spotify.app" in card.commands
    assert card.note == "" and "no model" in card.source


def test_card_warns_when_something_runs_code(fake_mac):
    script = Path.home() / "Desktop" / "deploy.command"
    script.write_text("echo hi\n")
    plan = TOOLS["open_path"].make_plan({"path": str(script)})
    card = cards.card_for(Proposal(f"open {script}", "fast", plan))
    assert card.badge == "SHELL" and card.colour == cards.RISK_COLOURS[Risk.SHELL]
    assert "runs code" in card.note and "runs code" in card.title


def test_model_suggestions_say_so_with_confidence(fake_mac):
    plan = TOOLS["open_app"].make_plan({"name": "Music"})
    card = cards.card_for(Proposal("play some music", "model", plan, confidence=0.82))
    assert "check it" in card.source and "82%" in card.source


def test_outcome_line():
    assert cards.outcome_line(Result(True, "Opened Notes.")) == "✓ Opened Notes."
    assert cards.outcome_line(Result(False, "It failed.")) == "✗ It failed."


def test_card_widget_buttons(fake_mac):
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    try:
        plan = TOOLS["open_app"].make_plan({"name": "Notes"})
        clicked = []
        frame, run_btn, deny_btn, status = cards.build_card(
            root, cards.card_for(Proposal("open notes", "fast", plan)),
            on_run=lambda: clicked.append("run"), on_deny=lambda: clicked.append("deny"))
        assert cards.APPROVE_KEY in run_btn.cget("text") and "Esc" in deny_btn.cget("text")
        run_btn.invoke()
        deny_btn.invoke()
        assert clicked == ["run", "deny"]
        texts = [w.cget("text") for w in frame.winfo_children() if isinstance(w, tk.Label)]
        assert any("Notes.app" in t for t in texts)
    finally:
        root.destroy()
