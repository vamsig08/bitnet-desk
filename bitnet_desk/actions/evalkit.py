"""Action eval: cases, a fixed fake Mac to resolve against, and scoring.

Nothing is executed — scoring stops at the plan. The fast-path score runs in pytest; the model-path
runner (step 4) reuses the same cases, fixture and matchers.
"""
import json
from dataclasses import dataclass
from pathlib import Path

from .. import config as cfg
from . import fastpath
from .registry import TOOLS

CASES = cfg.HERE / "evals" / "actions.jsonl"
PLANNED_TOOLS = {"help", "now", "open_app", "open_path", "open_url", "start_project", "download",
                 "find_files", "run_shell", "media", "playlist", "volume", "dnd", "screenshot",
                 "lock_screen", "empty_trash", "routine", "chat"}
ROUTES = {"fast", "model", "chat"}

FIXTURE_APPS = ["Safari", "Spotify", "Music", "Notes", "Calculator", "Visual Studio Code", "Utilities/Terminal"]
FIXTURE_DIRS = ["Documents", "Downloads", "Desktop", "Projects/bitnet-desk"]
FIXTURE_SHORTCUTS = ["BitNet DND On", "BitNet DND Off", "Open Spotify"]


def load_cases(path=CASES):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def fake_mac(root):
    """Build the eval's fake Mac under `root`; returns the env vars that point the app at it."""
    root = Path(root)
    for app in FIXTURE_APPS:
        (root / "Applications" / f"{app}.app").mkdir(parents=True, exist_ok=True)
    for d in FIXTURE_DIRS:
        (root / "home" / d).mkdir(parents=True, exist_ok=True)
    config = root / "config.toml"
    config.write_text('[projects.bitnet-desk]\npath = "~/Projects/bitnet-desk"\n'
                      'aliases = ["bitnet desk", "bitnet"]\n\n'
                      '[playlists]\nfocus = "spotify:playlist:37i9dQZF1DWZeKCadgRdKQ"\n'
                      '"deep work" = "https://open.spotify.com/playlist/37i9dQZF1DX8NTLI2TtZa6?si=abc"\n'
                      'chill = "Chill Mix"\n\n'
                      '[routines.work-mode]\naliases = ["work mode", "start work"]\n'
                      'steps = ["start bitnet-desk", "play my focus playlist", "dnd on"]\n', encoding="utf-8")
    return {"HOME": str(root / "home"), "BITNET_APP_DIRS": str(root / "Applications"),
            "BITNET_CONFIG": str(config), "BITNET_RUNNING_APPS": "Spotify",   # something is playing
            "BITNET_SHORTCUTS": "\n".join(FIXTURE_SHORTCUTS),
            "BITNET_SCREENSHOT_DIR": str(root / "home" / "Desktop")}


def _norm(v):
    v = str(v).strip()
    if v.startswith("~"):
        v = str(Path(v).expanduser())
    return v.rstrip("/").lower()


def value_match(expected, actual):
    if isinstance(expected, dict):
        if "contains" in expected:
            return expected["contains"].lower() in str(actual).lower()
        if "any_of" in expected:
            return any(value_match(e, actual) for e in expected["any_of"])
    if isinstance(expected, str):
        return _norm(expected) == _norm(actual)
    return expected == actual


def args_match(expected, actual):
    return all(k in actual and value_match(v, actual[k]) for k, v in expected.items())


@dataclass
class FastScore:
    rows: list          # (case, match or None, correct)
    in_scope: int       # route=fast cases whose tool exists
    covered: int        # … that the fast path got right
    matched: int        # cases the fast path matched at all
    correct: int        # … correctly

    @property
    def coverage(self):
        return self.covered / self.in_scope if self.in_scope else 0.0

    @property
    def precision(self):
        return self.correct / self.matched if self.matched else 1.0

    @property
    def false_triggers(self):
        return [(c, m) for c, m, ok in self.rows if m and not ok]

    def report(self):
        lines = [f"Fast path: coverage {self.covered}/{self.in_scope} ({self.coverage:.0%}), "
                 f"precision {self.correct}/{self.matched} ({self.precision:.0%}), "
                 f"false triggers {len(self.false_triggers)}"]
        for c, m in self.false_triggers:
            lines.append(f"  #{c['id']} {c['text']!r}: expected {c['tool']}, got {m.tool} {m.args}")
        for c, m, ok in self.rows:
            if c["route"] == "fast" and c["tool"] in TOOLS and not ok:
                lines.append(f"  missed #{c['id']} {c['text']!r} → {c['tool']}")
        return "\n".join(lines)


def score_fast(cases):
    rows = []
    for c in cases:
        m = fastpath.match(c["text"])
        ok = m is not None and m.tool == c["tool"] and args_match(c["args"], m.args)
        rows.append((c, m, ok))
    scope = [ok for c, m, ok in rows if c["route"] == "fast" and c["tool"] in TOOLS]
    return FastScore(rows, len(scope), sum(scope),
                     sum(1 for _, m, _ in rows if m), sum(1 for _, m, ok in rows if m and ok))


# -- command-verb check (deterministic) and the model path (needs the real model)

def score_verb_check(cases):
    """For cases the fast path doesn't take: does the verb check send them where `route` says?"""
    from .pipeline import wants_router
    rows = []
    for c in cases:
        if fastpath.match(c["text"]) is not None:
            continue
        sent = "model" if wants_router(c["text"]) else "chat"
        rows.append((c, sent, sent == c["route"] or c["route"] == "fast"))
    return rows


def _final(outcome):
    """(tool, args) the user would be offered, or ("chat", {}) for chat/refused."""
    if outcome.kind in ("ask", "auto", "done"):
        return outcome.proposal.plan.tool, outcome.proposal.plan.args
    return "chat", {}


def _correct(case, tool, args):
    if case["tool"] == "chat":
        return tool == "chat"
    return tool == case["tool"] and args_match(case["args"], args)


@dataclass
class ModelRow:
    case: dict
    route: object           # router.Route
    outcome: object         # pipeline.Outcome (nothing executed)
    error: str = ""

    @property
    def picked_ok(self):    # the router's own choice (before grounding)
        return _correct(self.case, self.route.tool, self.route.args) if self.route else False

    @property
    def final_ok(self):     # what the user would actually be offered
        return _correct(self.case, *_final(self.outcome)) if self.outcome else False

    @property
    def unsafe(self):       # a wrong action offered to the user
        return bool(self.outcome) and self.outcome.kind in ("ask", "auto") and not self.final_ok


def run_model(cases, progress=print):
    from . import pipeline, router
    rows = []
    for c in cases:
        try:
            r = router.route(c["text"].strip().removeprefix("/").strip())
            rows.append(ModelRow(c, r, pipeline.decide_model(c["text"], route=r)))
        except Exception as e:      # noqa: BLE001 — a failed call is a wrong answer, not a crash
            rows.append(ModelRow(c, None, None, f"{type(e).__name__}: {e}"))
        row = rows[-1]
        if progress:
            progress(f"  #{c['id']:>2} {'ok ' if row.final_ok else 'BAD'} {c['text'][:48]!r:52} → "
                     f"{row.route.tool if row.route else row.error} "
                     f"{json.dumps(row.route.args) if row.route and row.route.args else ''} "
                     f"p={row.route.confidence if row.route and row.route.confidence is not None else '-'} "
                     f"[{row.outcome.kind if row.outcome else 'error'}]")
    return rows


def _pct(n, d):
    return f"{n}/{d} ({n / d:.0%})" if d else "–"


def model_report(rows, fast, verb_rows, threshold):
    acts = [r for r in rows if r.case["tool"] != "chat"]
    chats = [r for r in rows if r.case["tool"] == "chat"]
    right_tool = [r for r in acts if r.route and r.route.tool == r.case["tool"]]
    lat = sorted(r.route.ms for r in rows if r.route)
    caught = [r for r in rows if r.route and r.route.tool != "chat" and r.case["tool"] == "chat" and r.final_ok]
    lines = ["### Fast path (rules, no model)",
             f"- Coverage of `route: fast` cases: {_pct(fast.covered, fast.in_scope)}",
             f"- Precision when it matches: {_pct(fast.correct, fast.matched)}; false triggers: {len(fast.false_triggers)}",
             f"- Command-verb check on the rest: {_pct(sum(ok for *_, ok in verb_rows), len(verb_rows))} sent where expected",
             "", "### Model path (every case sent to the router, ignoring fast path and verb check)",
             f"- Tool choice, action cases: {_pct(len(right_tool), len(acts))}",
             f"- Tool + arguments, action cases: {_pct(sum(r.picked_ok for r in acts), len(acts))} as picked, "
             f"{_pct(sum(r.final_ok for r in acts), len(acts))} offered after grounding/planning",
             f"- Chat cases kept as chat: {_pct(sum(r.picked_ok for r in chats), len(chats))} by the model, "
             f"{_pct(sum(r.final_ok for r in chats), len(chats))} after grounding ({len(caught)} invented actions caught)",
             f"- Overall (what the user is offered): {_pct(sum(r.final_ok for r in rows), len(rows))}",
             f"- Latency per router call: p50 {lat[len(lat) // 2] if lat else '-'} ms, "
             f"p95 {lat[int(len(lat) * 0.95)] if lat else '-'} ms",
             "", f"Calibration (confidence of the model's pick → tool+args right; threshold {threshold}):", ""]
    lines += ["| confidence | cases | right |", "|---|---|---|"]
    for lo, hi in ((0, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.01)):
        b = [r for r in rows if r.route and r.route.tool != "chat" and r.route.confidence is not None
             and lo <= r.route.confidence < hi]
        lines.append(f"| {lo:.1f}–{min(hi, 1):.1f} | {len(b)} | {sum(r.picked_ok for r in b)} |")
    unsafe = [r for r in rows if r.unsafe]
    lines += ["", f"**Unsafe picks** (a wrong action offered to the user): {len(unsafe)}"]
    for r in unsafe:
        p = r.outcome.proposal
        how = "would run WITHOUT asking" if r.outcome.kind == "auto" else "shown as a card (asks first)"
        lines.append(f"- #{r.case['id']} {r.case['text']!r}: expected {r.case['tool']}, offered "
                     f"`{p.plan.summary}` (p={p.confidence:.2f}) — {how}")
    wrong = [r for r in rows if not r.final_ok and not r.unsafe]
    if wrong:
        lines += ["", "Other misses (no action offered, or chat instead of an action):"]
        lines += [f"- #{r.case['id']} {r.case['text']!r}: expected {r.case['tool']}, model said "
                  f"{r.route.tool if r.route else r.error} {json.dumps(r.route.args) if r.route else ''} "
                  f"→ {r.outcome.kind if r.outcome else 'error'}" for r in wrong]
    return "\n".join(lines)


def pipeline_report(cases, rows):
    """End to end: fast path → verb check → model (reusing the model answers above)."""
    from .pipeline import decide_fast, wants_router
    by_id = {r.case["id"]: r for r in rows}
    ok = gate_miss = unsafe = 0
    for c in cases:
        o = decide_fast(c["text"])
        if o is not None:
            good = _correct(c, *_final(o))
        elif wants_router(c["text"]):
            r = by_id[c["id"]]
            good, unsafe = r.final_ok, unsafe + r.unsafe
        else:
            good = c["tool"] == "chat"
            gate_miss += not good
        ok += good
    return ("### Pipeline (fast path → verb check → model)\n"
            f"- End-to-end right: {_pct(ok, len(cases))}; actions missed because the verb check sent them "
            f"to chat: {gate_miss}; unsafe picks reaching the user: {unsafe}")
