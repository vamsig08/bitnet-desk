"""The e2e harness's own logic (no real Mac effects here): bookkeeping, report, the never-run guard."""
import json

import pytest

from bitnet_desk import e2e
from bitnet_desk.actions import TOOLS, registry


def test_run_bookkeeping_and_report(tmp_path):
    run = e2e.Run()
    run.check("volume", "“volume 30” → 30%", True, "now 30%")
    run.check("volume", "mute", False, "a | pipe")
    run.skip("dnd", "on/off", "no Shortcuts")
    run.restore("volume", "back to 55%")
    run.needs_you += ["Add the Shortcuts.", "Add the Shortcuts."]
    assert run.counts() == {"pass": 1, "fail": 1, "skip": 1}
    text = e2e.write_report(run, ["volume", "dnd", "media"], tmp_path / "r.md").read_text()
    assert "**1 passed, 1 failed, 1 skipped.**" in text and "## media" not in text
    assert "| ❌ | mute | a \\| pipe |" in text and "- volume: back to 55%" in text
    assert text.count("Add the Shortcuts.") == 1


def test_guard_never_runs_lock_or_empty_trash(monkeypatch):
    monkeypatch.setattr(registry, "TOOLS", dict(registry.TOOLS))
    e2e.guard_never_run()
    for name in e2e.NEVER_RUN:
        plan = registry.TOOLS[name].make_plan({})
        with pytest.raises(RuntimeError, match="never runs"):
            registry.TOOLS[name].run(plan, None, None)


def test_volume_journal_survives_a_crash(tmp_path, monkeypatch):
    state = {"v": (40, False)}
    monkeypatch.setattr(e2e, "JOURNAL", tmp_path / "j.json")
    monkeypatch.setattr(e2e, "volume_state", lambda: state["v"])
    monkeypatch.setattr(e2e, "set_volume_state", lambda v, m: state.update(v=(v, m)))
    with e2e.saved_volume():
        state["v"] = (10, True)
        assert json.loads(e2e.JOURNAL.read_text()) == {"volume": 40, "muted": False}
        with e2e.saved_volume():                  # nested: the outer (real) volume stays in the journal
            state["v"] = (5, False)
        assert state["v"] == (10, True) and e2e.JOURNAL.exists()
    assert state["v"] == (40, False) and not e2e.JOURNAL.exists()
    e2e.JOURNAL.write_text(json.dumps({"volume": 33, "muted": True}))   # a run that died
    run = e2e.Run()
    e2e.recover_from_crash(run)
    assert state["v"] == (33, True) and not e2e.JOURNAL.exists() and "died" in run.restored[0][1]


def test_png_size(tmp_path):
    import struct
    f = tmp_path / "x.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + struct.pack(">II", 2880, 1800))
    assert e2e.png_size(f) == (2880, 1800)
    f.write_bytes(b"GIF89a" + b"\0" * 30)
    assert e2e.png_size(f) is None
