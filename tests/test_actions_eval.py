"""The action eval file is well-formed, and the fast path scores perfectly on what it covers."""
from bitnet_desk.actions import evalkit


def test_eval_cases_are_well_formed():
    cases = evalkit.load_cases()
    assert [c["id"] for c in cases] == list(range(1, len(cases) + 1))
    assert len(cases) >= 40
    for c in cases:
        assert c["tool"] in evalkit.PLANNED_TOOLS, c
        assert c["route"] in evalkit.ROUTES, c
        assert isinstance(c["args"], dict) and c["text"].strip()
        if c["tool"] == "chat":
            assert c["args"] == {}


def test_matchers():
    assert evalkit.args_match({"name": "Safari"}, {"name": "safari"})
    assert evalkit.args_match({"q": {"contains": "tax"}}, {"q": "Taxes 2025"})
    assert evalkit.args_match({"n": {"any_of": ["Music", "Spotify"]}}, {"n": "Spotify"})
    assert not evalkit.args_match({"name": "Safari"}, {})


def test_fast_path_eval(fake_mac):
    score = evalkit.score_fast(evalkit.load_cases())
    print(score.report())
    assert score.precision == 1.0 and not score.false_triggers, score.report()
    assert score.coverage == 1.0, score.report()       # every route=fast case for tools built so far
    assert score.in_scope > 0
