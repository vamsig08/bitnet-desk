"""Phase 3 step 1: hotkey parsing and clashes, launch at login, one instance, the command box.

No real hotkey is registered and nothing is shown on screen; Tk widgets are built on a hidden root.
"""
import json
import plistlib
import sys

import pytest

from bitnet_desk import app, config, login
from bitnet_desk.actions import TOOLS, Result
from bitnet_desk.ui import commandbox, mac
from bitnet_desk.ui.transcript import Tee, Transcript
from bitnet_desk.ui.window import hotkey_setting


# -- hotkey strings

@pytest.mark.parametrize("text,code,mods,label", [
    ("alt+space", 49, 0x0800, "⌥Space"),
    ("⌥Space", 49, 0x0800, "⌥Space"),
    ("Option + Space", 49, 0x0800, "⌥Space"),
    ("cmd+shift+k", 40, 0x0300, "⇧⌘K"),
    ("ctrl-alt-b", 11, 0x1800, "⌃⌥B"),
    ("⌃⌥⌘F12", 111, 0x1900, "⌃⌥⌘F12"),
    ("f5", 96, 0, "F5"),
    ("cmd+-", 27, 0x0100, "⌘-"),
])
def test_parse_hotkey(text, code, mods, label):
    assert mac.parse_hotkey(text) == mac.HotKey(code, mods, label)


@pytest.mark.parametrize("text,why", [("space", "needs a modifier"), ("shift+k", "capital letters"),
                                      ("hyper+k", "unknown modifier"), ("cmd+zz", "unknown key"),
                                      ("", "empty")])
def test_bad_hotkeys_say_why(text, why):
    with pytest.raises(ValueError, match=why):
        mac.parse_hotkey(text)


def test_hotkey_setting(monkeypatch, tmp_path, reload_config):
    assert hotkey_setting()[0].label == "⌥Space"                      # the default
    for off in ("off", "none"):
        monkeypatch.setenv("BITNET_HOTKEY", off)
        reload_config()
        assert hotkey_setting() == (None, None)
    cfg_file = tmp_path / "c.toml"
    cfg_file.write_text('[ui]\nhotkey = "cmd+zz"\n')
    monkeypatch.delenv("BITNET_HOTKEY")
    monkeypatch.setenv("BITNET_CONFIG", str(cfg_file))
    reload_config()
    key, problem = hotkey_setting()
    assert key is None and "unknown key" in problem
    cfg_file.write_text("[ui]\nhotkey = false\nsize = 3\n")
    reload_config()
    assert hotkey_setting() == (None, None)
    assert any("unknown setting 'ui.size'" in e for e in config.CONFIG_ERRORS)


# -- clashes: macOS doesn't refuse a combination another app holds, so we look ourselves

def test_clashes_with_macos_defaults_and_changes():
    space = mac.parse_hotkey("cmd+space")
    assert mac.clashes(space) == ["macOS (Spotlight)"]
    assert mac.clashes(space, {"64": {"enabled": False}}) == []                     # Spotlight's turned off
    moved = {"64": {"enabled": True, "value": {"parameters": [32, 49, 0x80000]}}}  # moved to ⌥Space
    assert mac.clashes(space, moved) == []
    assert mac.clashes(mac.parse_hotkey("alt+space"), moved) == ["macOS (Spotlight)"]
    assert mac.clashes(mac.parse_hotkey("ctrl+alt+b")) == []


def test_clashes_with_chatgpt_launcher():
    prefs = {"KeyboardShortcuts_toggleLauncher": json.dumps({"carbonModifiers": 2048, "carbonKeyCode": 49}),
             "KeyboardShortcuts_toggleAttachedLauncher": "not json"}
    assert mac.clashes(mac.parse_hotkey("alt+space"), chatgpt=prefs) == ["ChatGPT (its launcher shortcut)"]
    assert mac.clashes(mac.parse_hotkey("alt+shift+space"), chatgpt=prefs) == []


def test_global_hotkey_is_unsupported_off_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert mac.GlobalHotKey(mac.parse_hotkey("alt+space"), lambda: None).register() == "unsupported"
    assert mac.frontmost_pid() is None and mac.activate() is None


# -- launch at login

@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def test_login_agent_runs_this_python_in_the_background(home):
    a = login.agent(python="/opt/py/python3.12")
    assert a["ProgramArguments"] == ["/opt/py/python3.12", "-m", "bitnet_desk", "--background"]
    assert a["WorkingDirectory"] == str(config.HERE) and a["RunAtLoad"] is True
    assert "KeepAlive" not in a and a["StandardOutPath"] == str(config.LOGIN_LOG)
    assert login.plist_path() == home / "Library/LaunchAgents/com.bitnetdesk.app.plist"


def test_login_on_and_off_writes_the_plist_and_logs_it(home):
    assert not login.is_enabled()
    assert "when you log in" in login.set_enabled(True)
    with open(login.plist_path(), "rb") as f:
        assert plistlib.load(f)["Label"] == login.LABEL
    assert login.is_enabled()
    login.set_enabled(False)
    login.set_enabled(False)                                             # already off: fine
    assert not login.is_enabled()
    entries = [json.loads(line) for line in config.ACTION_LOG.read_text().splitlines()]
    assert [(e["tool"], e["args"]["enabled"], e["decision"]) for e in entries] == [
        ("launch_at_login", True, "approved"), ("launch_at_login", False, "approved"),
        ("launch_at_login", False, "approved")]


def test_login_cli(home, capsys):
    assert app.main(["--login", "status"]) == 0
    assert "Launch at login is off" in capsys.readouterr().out
    app.main(["--login", "on"])
    app.main(["--login", "status"])
    assert "is on" in capsys.readouterr().out


# -- one copy at a time

def test_second_copy_is_refused(tmp_path, capsys, monkeypatch):
    lock = tmp_path / "app.lock"
    first = app.acquire_lock(lock)
    assert first is not None
    assert app.acquire_lock(lock) is None
    monkeypatch.setattr(config, "LOCK_FILE", lock)
    assert app.main([]) == 1 and "already running" in capsys.readouterr().out
    import os
    os.close(first)
    second = app.acquire_lock(lock)                                     # free again once the first exits
    assert second is not None
    os.close(second)


# -- the command box: when it gets out of the way

def test_box_hides_after_doing_the_thing_but_not_when_theres_something_to_read(fake_mac):
    app_plan = TOOLS["open_app"].make_plan({"name": "Notes"})
    now_plan = TOOLS["now"].make_plan({})
    assert commandbox.hide_after(Result(True, "Opened Notes."), app_plan)
    assert not commandbox.hide_after(Result(False, "Couldn't open Notes."), app_plan)
    assert not commandbox.hide_after(Result(True, "It's 10:42."), now_plan)          # an answer
    assert not commandbox.hide_after(Result(True, "Found 2.", items=("/a", "/b")), app_plan)
    assert not commandbox.hide_after(Result(True, "Ran it.", output="hello"), app_plan)


@pytest.fixture()
def root():
    tk = pytest.importorskip("tkinter")
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    r.withdraw()
    yield r
    r.destroy()


def text_of(view):
    return view.text.get("1.0", "end-1c")


def test_tee_writes_both_but_keeps_the_card_in_the_box(root):
    import tkinter as tk
    box, window = Transcript(tk.Text(root)), Transcript(tk.Text(root))
    tee = Tee(box, window)
    tee.write("You: ", "user")
    tee.write("pause\n")
    assert text_of(box) == text_of(window) == "You: pause\n"
    clicked = []
    links = tee.link("⚑ that was wrong", lambda: clicked.append(1), style="faint")
    assert len(links) == 2
    for link in links:
        link.add(" — saved")
    assert text_of(box).endswith("⚑ that was wrong — saved") and text_of(window).endswith("— saved")
    card = tk.Frame(tee.card_parent)
    assert tee.card_parent is box.text
    tee.embed(card, summary="⌛ Empty the Trash — waiting for you in the command box")
    assert str(card) in box.text.window_names() and not window.text.window_names()
    assert "waiting for you in the command box" in text_of(window)
    tee.card_status("✓ Done.", "ok")
    assert text_of(window).endswith("✓ Done.\n\n") and "✓ Done." not in text_of(box)
    tee.clear()
    assert text_of(box) == "" and text_of(window)                          # the window keeps the record


def test_command_box_submits_and_grows_with_its_output(root):
    seen = []
    box = commandbox.CommandBox(root, on_submit=lambda t: seen.append(t) or t != "keep", on_approve=lambda e: None,
                                on_escape=lambda e: None, on_open_window=lambda: None,
                                approve_events=("<Command-Return>",), hint="⌥Space hides")
    assert box.out_frame.cget("height") == 1                             # nothing to show yet
    box.entry.insert(0, "  open notes  ")
    box._submit(None)
    assert seen == ["open notes"] and box.entry.get() == ""
    box.entry.insert(0, "keep")
    box._submit(None)
    assert box.entry.get() == "keep"                                     # not taken: stays for a retry
    box.view.write("Assistant: " + "a long answer " * 200)
    box.fit()
    assert int(box.out_frame.cget("height")) == commandbox.MAX_OUTPUT_PX
    box.view.clear()
    box.fit()
    assert int(box.out_frame.cget("height")) == 1


def test_menu_bar_menu(root):
    from bitnet_desk.ui import menubar
    import tkinter as tk
    calls = []
    var = tk.BooleanVar(master=root, value=False)
    bar = menubar.MenuBar(root, "BitNet Desk", [("Show command box (⌥Space)", lambda: calls.append("box")),
                                                 None, ("note", "⚠ ⌥Space is also used by ChatGPT"),
                                                 ("check", "Launch at login", var, lambda: calls.append(var.get())),
                                                 ("Quit BitNet Desk", lambda: calls.append("quit"))],
                         show_icon=False)                                 # never touch the real menu bar
    try:
        m = bar.menu
        assert [m.type(i) for i in range(5)] == ["command", "separator", "command", "checkbutton", "command"]
        assert m.entrycget(2, "state") == "disabled"
        m.invoke(0)
        m.invoke(3)
        m.invoke(4)
        assert calls == ["box", True, "quit"]
        assert len(menubar.ICON) == 18 and all(len(row) == 20 for row in menubar.ICON)
    finally:
        bar.destroy()
