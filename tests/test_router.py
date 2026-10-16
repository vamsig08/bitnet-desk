"""Model path: router prompt/schema, confidence from token probabilities, grounding, and decisions.

Uses the mock llama-server or hand-made router answers — never the real model.
"""
import json
from pathlib import Path

import pytest

from bitnet_desk import config
from bitnet_desk.actions import TOOLS, ground, pipeline, router
from bitnet_desk.actions.router import Route


def logged():
    if not config.ACTION_LOG.exists():
        return []
    return [json.loads(line) for line in config.ACTION_LOG.read_text().splitlines()]


# -- prompt and schema

def test_schema_offers_every_tool_and_chat(fake_mac):
    consts = [alt["properties"]["tool"]["const"] for alt in router.schema()["oneOf"]]
    assert set(consts) == {t.label for t in TOOLS.values()} | {"chat"}
    find = next(a for a in router.schema()["oneOf"] if a["properties"]["tool"]["const"] == "find")
    assert find["properties"]["args"]["required"] == ["query"]          # kind is optional
    url = next(a for a in router.schema()["oneOf"] if a["properties"]["tool"]["const"] == "url")
    assert url["properties"]["args"]["properties"]["url"]["pattern"] == '^[^ "]+$'


def test_project_tool_lists_projects_and_disappears_without_them(fake_mac, monkeypatch, tmp_path, reload_config):
    assert "the user's projects are: bitnet-desk" in router.system_text()
    monkeypatch.setenv("BITNET_CONFIG", str(tmp_path / "none.toml"))
    reload_config()
    assert "project" not in router.labels() and "project(" not in router.system_text()


def test_labels_are_distinct_and_none_prefixes_another():
    labels = list(router.labels())
    assert len(labels) == len(set(labels))
    assert not [(a, b) for a in labels for b in labels if a != b and b.startswith(a)]


def test_prompt_has_examples_then_the_message(fake_mac):
    p = router.prompt("open spotify")
    assert p.startswith("System: You turn the user's message into exactly one tool call")
    assert '{"tool": "chat"}' in p and p.endswith("User: open spotify<|eot_id|>Assistant: ")


# -- confidence

def tok(content, *cands):
    return {"content": content, "probs": [{"tok_str": s, "prob": p} for s, p in cands]}


def test_confidence_renormalises_over_tool_labels():
    content = '{"tool": "app", "args": {"name": "Music"}}'
    probs = [tok('{"'), tok("tool"), tok('":'), tok(' "'),
             tok("app", ("app", 0.6), ("url", 0.2), ("spotify", 0.2)), tok('",')]
    p, alts = router.tool_confidence(content, probs, "app")
    assert p == pytest.approx(0.75) and alts == {"app": pytest.approx(0.75), "url": pytest.approx(0.25)}


def test_confidence_when_the_token_spans_the_quote_and_prefixes():
    content = '{"tool": "download", "args": {}}'
    # one token ' "down' covers the opening quote; candidates that don't share the quote are ignored
    probs = [tok('{"tool":'), tok(' "down', (' "down', 0.5), (' "find', 0.3), (' "chat"', 0.1), ("xyz", 0.1)),
             tok('load"')]
    p, alts = router.tool_confidence(content, probs, "download")
    assert p == pytest.approx(0.5 / 0.9) and set(alts) == {"download", "find", "chat"}


def test_confidence_unknown_without_probabilities():
    assert router.tool_confidence('{"tool": "app"}', [], "app") == (None, {})


# -- talking to the (mock) server

def test_route_sends_a_grammar_request_on_the_router_slot(fake_mac, mock_server):
    mock_server.json_reply = {"content": '{"tool": "app", "args": {"name": "Calculator"}}',
                              "completion_probabilities": [tok('{"tool": "'), tok("app", ("app", 0.9), ("chat", 0.1))]}
    r = router.route("launch calc please")
    body = mock_server.seen["body"]
    assert body["json_schema"] == router.schema("launch calc please")
    assert body["n_probs"] == 10 and body["temperature"] == 0
    assert body["id_slot"] == config.ROUTER_SLOT and body["cache_prompt"] is True
    assert (r.tool, r.args, r.confidence) == ("open_app", {"name": "Calculator"}, pytest.approx(0.9))


def test_route_doesnt_pin_a_slot_an_old_server_lacks(fake_mac, mock_server):
    mock_server.slots = 1
    mock_server.json_reply = {"content": '{"tool": "chat"}', "completion_probabilities": []}
    assert router.route("start thinking").tool == "chat"
    assert "id_slot" not in mock_server.seen["body"]                  # slot 1 would wait forever


def test_chat_uses_slot_zero(mock_server):
    import threading
    from bitnet_desk import llm
    llm.stream_completion("x", lambda t: None, threading.Event())
    assert mock_server.seen["body"]["id_slot"] == config.CHAT_SLOT


# -- the command-verb check

@pytest.mark.parametrize("text,ok", [("open up about feelings", True), ("Please start the thing", True),
                                     ("can you play some music", True), ("/whatever this is", True),
                                     ("show me how loops work", True), ("how do I download files", False),
                                     ("write a poem", False), ("what's 2+2", False), ("", False)])
def test_wants_router(text, ok):
    assert pipeline.wants_router(text) is ok


# -- grounding and decisions (router answers made by hand)

def decide(text, tool, args, p=0.95):
    return pipeline.decide_model(text, route=Route(tool, args, p))


def test_confident_literal_app_runs_like_the_fast_path(fake_mac):
    o = decide("launch the calculator thing", "open_app", {"name": "Calculator"})
    assert o.kind == "auto" and o.proposal.route == "model" and o.proposal.confidence == 0.95


def test_inferred_targets_always_ask(fake_mac):
    o = decide("play some tunes", "open_app", {"name": "Spotify"})
    assert o.kind == "ask" and o.proposal.must_ask                       # open_app is auto-approved, still asks
    o = decide("find a good pancake recipe", "find_files", {"query": "pancake recipe"})
    assert o.kind == "ask"
    o = decide("find my pancake recipe file", "find_files", {"query": "pancake recipe"})
    assert o.kind == "auto"
    o = decide("run a check of my disk space", "find_files", {"query": "disk space"})
    assert o.kind == "ask"                                              # "my" alone isn't about files


def test_unsure_picks_go_to_chat_and_are_logged(fake_mac):
    assert decide("open the thing", "open_app", {"name": "Notes"}, p=0.6).kind == "chat"
    assert decide("open the thing", "open_app", {"name": "Notes"}, p=None).kind == "chat"
    assert logged()[-1]["decision"] == "refused" and "unsure" in logged()[-1]["message"]


@pytest.mark.parametrize("text,tool,args", [
    ("show me the weather in Paris", "open_url", {"url": "https://weather.com/paris"}),
    ("download the latest python installer", "download", {"url": "https://python.org/ftp/python.pkg"}),
    ("run a check of my disk space", "run_shell", {"command": "df -h"}),
    ("find a good restaurant", "find_files", {"query": "sushi"}),
    ("what time is it in Tokyo?", "now", {}),
])
def test_invented_values_become_chat(fake_mac, text, tool, args):
    o = decide(text, tool, args)
    assert o.kind == "chat" and logged()[-1]["decision"] == "refused" and "ungrounded" in logged()[-1]["message"]


def test_literal_but_unknown_targets_are_refused_with_suggestions(fake_mac):
    o = decide("start project bitnet-dsk", "start_project", {"name": "bitnet-dsk"})
    assert o.kind == "refused" and "Your projects: bitnet-desk" in o.message and o.suggestions == ("bitnet-desk",)
    o = decide("open the report I wrote about Q3", "start_project", {"name": "Q3 report"})
    assert o.kind == "refused"                                          # named in the message: say why not


def test_inferred_unknown_targets_quietly_go_to_chat(fake_mac):
    assert decide("open the thing for my essay", "open_app", {"name": "Pages"}).kind == "chat"


def test_well_known_sites_and_literal_urls(fake_mac):
    o = decide("browse to wikipedia", "open_url", {"url": "wikipedia.org"})
    assert o.kind == "ask" and o.proposal.plan.args["url"] == "https://wikipedia.org"
    o = decide("download the csv at https://x.org/data.csv", "download", {"url": "https://x.org/data.csv"})
    assert o.kind == "ask" and o.proposal.plan.risk.label == "network"


def test_placeholder_home_paths_resolve_to_the_real_home(fake_mac):
    o = decide("open my documents folder", "open_path", {"path": "/Users/username/Documents"})
    assert o.kind == "auto" and o.proposal.plan.args["path"] == str(Path.home() / "Documents")


def test_shell_from_the_model_only_when_typed_literally(fake_mac):
    o = decide("run the command make test now", "run_shell", {"command": "make test"})
    assert o.kind == "ask" and o.proposal.plan.args == {"command": "make test"}


def test_handle_with_model_end_to_end(fake_mac, mock_server, runner):
    mock_server.json_reply = {"content": '{"tool": "app", "args": {"name": "Calculator"}}',
                              "completion_probabilities": [tok('{"tool": "'), tok("app", ("app", 0.97), ("chat", 0.03))]}
    assert pipeline.handle("open up the calculator thing", runner=runner).kind == "chat"   # fast only
    out = pipeline.handle("open up the calculator thing", runner=runner, use_model=True)
    assert out.kind == "done" and out.result.ok and runner.calls[0][-1].endswith("Calculator.app")
    assert logged()[-1]["route"] == "model" and logged()[-1]["confidence"] == pytest.approx(0.97)
    assert pipeline.handle("what's 2+2", runner=runner, use_model=True).kind == "chat"     # never asks the model
    assert len(mock_server.seen["bodies"]) == 1


def test_ground_rejects_unknown_tools():
    with pytest.raises(ground.Ungrounded):
        ground.check("format_disk", {}, "format my disk")


# -- per-message grammar: copy-only arguments

def offered(text):
    return {a["properties"]["tool"]["const"]: a["properties"]["args"]["properties"]
            for a in router.schema(text)["oneOf"] if "args" in a["properties"]}


def test_urls_commands_and_paths_can_only_be_copied_from_the_message(fake_mac):
    o = offered("download the csv at https://x.org/data.csv.")
    assert o["download"]["url"] == {"enum": ["https://x.org/data.csv"]}
    assert o["url"]["url"] == {"enum": ["https://x.org/data.csv"]}          # no "x.com" from "x.org"
    assert offered("run `git status` now")["shell"]["command"] == {"enum": ["git status"]}
    assert offered("$ ls -la ~/Desktop")["shell"]["command"] == {"enum": ["ls -la ~/Desktop"]}
    assert offered("show me what is on my desktop")["path"]["path"] == {"enum": ["~/Desktop"]}
    assert offered("browse to wikipedia")["url"]["url"] == {"enum": ["wikipedia.org"]}


def test_tools_without_a_value_to_copy_are_not_offered(fake_mac):
    o = offered("run a check of my disk space")
    assert not {"download", "url", "shell", "path"} & set(o) and {"app", "find", "now"} <= set(o)
    assert "download" not in offered("how do I download a file with curl?")


def test_route_sends_the_per_message_schema(fake_mac, mock_server):
    mock_server.json_reply = {"content": '{"tool": "chat"}', "completion_probabilities": []}
    router.route("grab https://x.org/a.zip")
    assert mock_server.seen["body"]["json_schema"] == router.schema("grab https://x.org/a.zip")
