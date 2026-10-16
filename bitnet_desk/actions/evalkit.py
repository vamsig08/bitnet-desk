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
PLANNED_TOOLS = {"now", "open_app", "open_path", "open_url", "start_project", "download",
                 "find_files", "run_shell", "chat"}
ROUTES = {"fast", "model", "chat"}

FIXTURE_APPS = ["Safari", "Spotify", "Music", "Notes", "Visual Studio Code", "Utilities/Terminal"]
FIXTURE_DIRS = ["Documents", "Downloads", "Desktop", "Projects/bitnet-desk"]


def load_cases(path=CASES):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def fake_mac(root):
    """Build the eval's fake Mac under `root`; returns the env vars that point the app at it."""
    root = Path(root)
    for app in FIXTURE_APPS:
        (root / "Applications" / f"{app}.app").mkdir(parents=True, exist_ok=True)
    for d in FIXTURE_DIRS:
        (root / "home" / d).mkdir(parents=True, exist_ok=True)
    return {"HOME": str(root / "home"), "BITNET_APP_DIRS": str(root / "Applications")}


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
