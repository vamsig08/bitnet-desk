"""Prompt format, streaming, stop, token counting, context fitting, date/time note."""
import threading
from datetime import datetime, timedelta, timezone

import pytest

from bitnet_desk import config, llm
from conftest import mock_tokens


def test_prompt_format():
    assert llm.build_prompt("sys", [("user", "hi")]) == "System: sys<|eot_id|>User: hi<|eot_id|>Assistant: "
    assert llm.build_prompt("sys", [("user", "a"), ("assistant", "b"), ("user", "c")]) == (
        "System: sys<|eot_id|>User: a<|eot_id|>Assistant: b<|eot_id|>User: c<|eot_id|>Assistant: ")


def test_streaming_and_timings(mock_server):
    toks = []
    t = llm.stream_completion(llm.build_prompt("sys", [("user", "hi")]), toks.append, threading.Event())
    assert "".join(toks) == "Hello there"
    assert t["predicted_per_second"] == 31.4
    body = mock_server.seen["body"]
    assert body["stream"] is True and body["cache_prompt"] is True
    assert {"<|eot_id|>", "<|end_of_text|>"} <= set(body["stop"])
    assert body["n_predict"] == config.MAX_NEW_TOKENS and body["temperature"] == config.TEMPERATURE


def test_stop_event_ends_stream_early(mock_server):
    mock_server.reply = ["one", " two", " three", " four"]
    stop, toks = threading.Event(), []

    def on_token(tok):
        toks.append(tok)
        if len(toks) == 2:
            stop.set()                                    # user pressed Stop

    assert llm.stream_completion("p", on_token, stop) == {}    # no timings: never reached the end
    assert toks == ["one", " two"]


def test_count_tokens_uses_server_tokenizer(mock_server):
    assert llm.count_tokens("x" * 40) == 10               # 4 chars/token, not the 3-char estimate
    assert llm.count_tokens("x" * 40) == 10 and mock_server.seen["tokenize_calls"] == 1   # cached


def test_count_tokens_falls_back_to_estimate_when_server_down():
    assert llm.count_tokens("x" * 30) == llm.estimate_tokens("x" * 30) == 11


@pytest.mark.parametrize("use_server", [True, False])
def test_history_trimming_keeps_latest_and_fits(request, use_server):
    if use_server:
        request.getfixturevalue("mock_server")
    count = mock_tokens if use_server else llm.estimate_tokens
    budget = config.CTX - config.MAX_NEW_TOKENS

    def prompt_tokens(history):
        return 1 + count(llm.build_prompt("sys", history))

    history = [("user", "x" * 3000), ("assistant", "y" * 3000)] * 3 + [("user", "latest")]
    fitted = llm.fit_history("sys", history)
    assert fitted[-1] == ("user", "latest")
    assert prompt_tokens(fitted) <= budget
    assert 2 < len(fitted) < len(history)                 # trimmed, but the budget is used
    huge = llm.fit_history("sys", [("user", "a" * 25000 + "TAIL")])
    assert huge[0][1].endswith("TAIL")
    assert budget - 50 <= prompt_tokens(huge) <= budget


def test_short_history_is_untouched(mock_server):
    history = [("user", "hi"), ("assistant", "hello"), ("user", "how are you?")]
    assert llm.fit_history("sys", history) == history


def test_read_text_file_truncates(tmp_path):
    f = tmp_path / "notes.md"
    f.write_text("z" * (config.MAX_FILE_CHARS + 10), encoding="utf-8")
    name, text, truncated = llm.read_text_file(f)
    assert name == "notes.md" and len(text) == config.MAX_FILE_CHARS and truncated


def test_system_prompt_has_date_and_weekday_but_not_time():
    now = datetime(2026, 10, 16, 9, 5, tzinfo=timezone(timedelta(hours=1), "BST"))
    assert llm.system_prompt(now) == config.SYSTEM_PROMPT + "\nToday is Friday, 16 October 2026."
    assert llm.time_note(now) == "\n\n(Local time: 09:05 BST)"


def _clock(monkeypatch, *times):
    it = iter(times)
    monkeypatch.setattr(llm, "_now", lambda: next(it))


def test_time_goes_on_newest_message_only_and_not_into_history(monkeypatch):
    _clock(monkeypatch, datetime(2026, 10, 16, 23, 59), datetime(2026, 10, 17, 0, 1))
    history = [("user", "hi"), ("assistant", "hello"), ("user", "what time is it?")]
    fitted, first = llm.prepare(history)
    assert "Today is Friday, 16 October 2026." in first
    assert first.endswith("User: what time is it?\n\n(Local time: 23:59)<|eot_id|>Assistant: ")
    assert first.count("Local time") == 1
    assert fitted == history                              # stored history stays clean
    _, second = llm.prepare(history)                      # fresh on every call
    assert "Today is Saturday, 17 October 2026." in second and "(Local time: 00:01)" in second


def test_cached_prefix_is_identical_between_sends_a_minute_apart(monkeypatch):
    _clock(monkeypatch, datetime(2026, 10, 16, 9, 0), datetime(2026, 10, 16, 9, 1))
    history = [("user", "hi"), ("assistant", "hello")] * 3 + [("user", "first question")]
    history, first = llm.prepare(history)
    history += [("assistant", "an answer"), ("user", "follow-up")]
    _, second = llm.prepare(history)
    # Everything before the message the first send stamped is byte-identical in the second send,
    # so llama-server's cache_prompt can reuse it.
    old_turns = first[:first.rindex("User: first question")]
    assert second.startswith(old_turns)
    assert "first question<|eot_id|>" in second           # unstamped once it is no longer newest
    assert second.count("Local time") == 1 and "(Local time: 09:01)" in second


def test_time_note_survives_trimming_of_a_huge_message(monkeypatch):
    _clock(monkeypatch, datetime(2026, 10, 16, 9, 0))
    fitted, prompt = llm.prepare([("user", "a" * 25000 + "TAIL")])
    assert prompt.endswith("TAIL\n\n(Local time: 09:00)<|eot_id|>Assistant: ")
    assert fitted[0][1].endswith("TAIL")
