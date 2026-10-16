"""Approval cards: what a pending action shows (pure, testable) and the Tk widget that shows it."""
import sys
from dataclasses import dataclass

from ..actions.registry import Risk

RISK_COLOURS = {Risk.READ_ONLY: "#666666", Risk.CHANGES: "#1f4e8c", Risk.NETWORK: "#b35900",
                Risk.SHELL: "#b00020"}
RISK_NOTES = {Risk.NETWORK: "This uses the network.",
              Risk.SHELL: "This runs code with your user's permissions."}
SOURCES = {"fast": "Matched a built-in command (no model).", "click": "You clicked this path.",
           "model": "Suggested by the model — check it before running."}
APPROVE_KEY, APPROVE_EVENTS = (("⌘↩", ("<Command-Return>",)) if sys.platform == "darwin"
                               else ("Ctrl+↩", ("<Control-Return>",)))
CARD_BG = "#f3f5f9"


@dataclass(frozen=True)
class Card:
    title: str
    badge: str
    colour: str
    commands: str       # exactly what will run, one command per line
    note: str
    source: str


def card_for(proposal):
    plan = proposal.plan
    source = SOURCES.get(proposal.route, proposal.route)
    if proposal.confidence is not None:
        source += f" Confidence {proposal.confidence:.0%}."
    return Card(title=plan.summary, badge=plan.risk.label.upper(), colour=RISK_COLOURS[plan.risk],
                commands="\n".join(plan.shown_commands()), note=RISK_NOTES.get(plan.risk, ""),
                source=source)


def outcome_line(result):
    return ("✓ " if result.ok else "✗ ") + result.message


def build_card(parent, card, on_run, on_deny):
    """The card widget. Returns (frame, run_button, deny_button, status_label)."""
    import tkinter as tk

    f = tk.Frame(parent, bg=CARD_BG, bd=1, relief="solid", padx=10, pady=8)
    top = tk.Frame(f, bg=CARD_BG)
    top.pack(fill="x")
    tk.Label(top, text=card.title, bg=CARD_BG, font=("Helvetica", 12, "bold")).pack(side="left")
    tk.Label(top, text=f" {card.badge} ", fg="white", bg=card.colour,
             font=("Helvetica", 9, "bold")).pack(side="left", padx=8)
    if card.commands:
        tk.Label(f, text=card.commands, bg="white", fg="#111", font=("Menlo", 11), justify="left",
                 anchor="w", wraplength=620, padx=6, pady=4).pack(fill="x", pady=(6, 2))
    if card.note:
        tk.Label(f, text=card.note, bg=CARD_BG, fg=card.colour, anchor="w").pack(fill="x")
    tk.Label(f, text=card.source, bg=CARD_BG, fg="#888", anchor="w",
             font=("Helvetica", 10, "italic")).pack(fill="x")
    btns = tk.Frame(f, bg=CARD_BG)
    btns.pack(fill="x", pady=(6, 0))
    run_btn = tk.Button(btns, text=f"Run ({APPROVE_KEY})", command=on_run)
    run_btn.pack(side="right")
    deny_btn = tk.Button(btns, text="Deny (Esc)", command=on_deny)
    deny_btn.pack(side="right", padx=6)
    status = tk.Label(f, text="", bg=CARD_BG, anchor="w", justify="left", wraplength=620)
    status.pack(fill="x")
    return f, run_btn, deny_btn, status
