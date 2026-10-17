"""Phase 3 step 2: media, playlists, volume, Do Not Disturb, screenshot, lock screen, empty Trash.

Nothing here talks to Spotify, Finder or macOS: commands go to the `runner` fixture, and which apps
are running / which Shortcuts exist come from the test hooks in config.
"""
import json
import subprocess
from pathlib import Path

import pytest

from bitnet_desk import config
from bitnet_desk.actions import TOOLS, ActionError, fastpath, permissions, pipeline
from bitnet_desk.actions.permissions import Proposal
from bitnet_desk.actions.tools import system
from bitnet_desk.ui import cards


def plan(tool, **args):
    return TOOLS[tool].make_plan(args)


def osa(*lines):
    return system._osascript(*lines)


@pytest.fixture()
def running(monkeypatch, reload_config):
    """running("Music") — pretend exactly these apps are running."""
    def set_running(*apps):
        monkeypatch.setenv("BITNET_RUNNING_APPS", ",".join(apps))
        reload_config()
    return set_running


def logged():
    return [json.loads(line) for line in config.ACTION_LOG.read_text().splitlines()]


# -- media

def test_media_commands(fake_mac):
    assert plan("media", action="pause").commands == (osa('tell application "Spotify" to pause'),)
    assert plan("media", action="next").commands == (osa('tell application "Spotify" to next track'),)
    assert plan("media", action="previous").summary == "Go back to the previous track (Spotify)"
    assert plan("media", action="toggle").commands == (osa('tell application "Spotify" to playpause'),)


def test_player_choice(fake_mac, running, monkeypatch, reload_config):
    running("Music")
    assert plan("media", action="pause").args["player"] == "Music"          # the one that's playing
    running("Music", "Spotify")
    assert plan("media", action="pause").args["player"] == "Spotify"        # both: Spotify first
    assert plan("media", action="pause", player="apple music").args["player"] == "Music"
    running()
    assert plan("media", action="play").args["player"] == "Spotify"         # nothing open: start Spotify
    cfg_file = Path(config.CONFIG_PATH)
    cfg_file.write_text(cfg_file.read_text() + '\n[media]\nplayer = "Music"\n')
    reload_config()
    assert plan("media", action="play").args["player"] == "Music"           # your choice in settings
    with pytest.raises(ActionError, match="only control Spotify and Music"):
        plan("media", action="play", player="vlc")


def test_pause_never_launches_a_player_just_to_pause_it(fake_mac, running):
    running()
    for action in ("pause", "next", "previous"):
        with pytest.raises(ActionError, match="Nothing is playing"):
            plan("media", action=action)
    p = plan("media", action="toggle")                                     # play/pause with nothing open:
    assert p.commands == (osa('tell application "Spotify" to play'),)     # just play


def test_a_clear_command_with_nothing_to_act_on_is_refused_not_chatted(fake_mac, running, runner):
    running()
    out = pipeline.handle("pause the music", runner=runner)
    assert out.kind == "refused" and "Nothing is playing" in out.message and not runner.calls
    assert logged()[-1]["decision"] == "refused" and logged()[-1]["tool"] == "media"


def test_missing_players(fake_mac, monkeypatch, reload_config):
    apps = Path(fake_mac / "Applications")
    for name in ("Spotify", "Music"):
        (apps / f"{name}.app").rename(apps / f"{name}.app.gone")
    reload_config()
    with pytest.raises(ActionError, match="Neither Spotify nor Music is installed"):
        plan("media", action="play")


# -- playlists

def test_playlists(fake_mac):
    focus = plan("playlist", name="my focus playlist")
    assert focus.args == {"name": "focus", "player": "Spotify"}
    assert focus.commands == (osa('tell application "Spotify" to play track "spotify:playlist:37i9dQZF1DWZeKCadgRdKQ"'),)
    deep = plan("playlist", name="Deep Work")                               # a Spotify link in settings
    assert 'play track "spotify:playlist:37i9dQZF1DX8NTLI2TtZa6"' in deep.commands[0][-1]
    chill = plan("playlist", name="chill")                                  # a Music playlist by name
    assert chill.commands == (osa('tell application "Music" to play playlist "Chill Mix"'),)
    with pytest.raises(ActionError, match="no playlist called “foucs”.*Your playlists: focus") as e:
        plan("playlist", name="foucs")
    assert e.value.suggestions == ("focus",)


def test_playlist_settings_reject_quotes(tmp_path, monkeypatch, reload_config):
    f = tmp_path / "c.toml"
    f.write_text('[playlists]\nbad = "x\\" to quit"\nok = "Road Trip"\n')
    monkeypatch.setenv("BITNET_CONFIG", str(f))
    reload_config()
    assert config.PLAYLISTS == {"ok": "Road Trip"}
    assert any("playlists.bad" in e for e in config.CONFIG_ERRORS)


@pytest.mark.parametrize("value,uri", [
    ("spotify:album:1", "spotify:album:1"),
    ("https://open.spotify.com/intl-de/playlist/AbC123?si=x", "spotify:playlist:AbC123"),
    ("Chill Mix", None), ("https://example.com/playlist/1", None)])
def test_spotify_uri(value, uri):
    assert system.spotify_uri(value) == uri


# -- volume

def test_volume_commands(fake_mac):
    up = plan("volume", action="up").commands[0]
    assert up == osa("set v to (output volume of (get volume settings)) + 10", "if v > 100 then set v to 100",
                     "set volume output volume v", "set volume output muted false")
    assert plan("volume", action="down").commands[0][-1] == "set volume output volume v"
    assert plan("volume", action="set", level=30).commands == (osa("set volume output volume 30",
                                                                    "set volume output muted false"),)
    assert plan("volume", action="set", level=0).commands == (osa("set volume output volume 0"),)
    assert plan("volume", action="mute").commands == (osa("set volume output muted true"),)
    for bad in (101, -1, True, "30"):
        with pytest.raises(ActionError, match="0 to 100"):
            plan("volume", action="set", level=bad)


# -- Do Not Disturb (two Shortcuts you make once)

def test_dnd_runs_your_shortcuts(fake_mac):
    assert plan("dnd", state="on").commands == (("shortcuts", "run", "BitNet DND On"),)
    assert plan("dnd", state="off").commands == (("shortcuts", "run", "BitNet DND Off"),)


def test_dnd_says_how_to_set_it_up(fake_mac, monkeypatch, reload_config, runner):
    monkeypatch.setenv("BITNET_SHORTCUTS", "Open Spotify")
    reload_config()
    out = pipeline.handle("turn on do not disturb", runner=runner)
    assert out.kind == "refused" and "Shortcuts app" in out.message and "“BitNet DND On”" in out.message
    assert not runner.calls


def test_dnd_shortcut_names_are_configurable(fake_mac, monkeypatch, reload_config):
    cfg_file = Path(config.CONFIG_PATH)
    cfg_file.write_text('[actions]\ndnd_on_shortcut = "Open Spotify"\n' + cfg_file.read_text())
    reload_config()
    assert plan("dnd", state="on").commands == (("shortcuts", "run", "Open Spotify"),)


# -- screenshot

def test_screenshot_plan(fake_mac):
    p = plan("screenshot")
    dest = Path(p.args["dest"])
    assert dest.parent == fake_mac / "home" / "Desktop" and dest.name.startswith("Screenshot ")
    assert p.commands == (("screencapture", "-x", str(dest)),)
    assert plan("screenshot", what="window").commands[0][1:4] == ("-x", "-i", "-W")
    assert plan("screenshot", what="area").commands[0][1:4] == ("-x", "-i", "-s")
    dest.write_bytes(b"png")
    assert Path(plan("screenshot").args["dest"]) != dest                    # never overwrites


def test_screenshot_result_is_the_file_or_a_clear_failure(fake_mac, runner):
    p = plan("screenshot")
    proposal = Proposal("take a screenshot", "fast", p)
    r = permissions.run(proposal, runner=runner)                           # screencapture made no file
    assert not r.ok and "cancelled" in r.message

    def capture(argv, **kw):
        Path(argv[-1]).write_bytes(b"png")
        return runner(argv, **kw)
    r = permissions.run(proposal, runner=capture)
    assert r.ok and r.items == (p.args["dest"],) and r.message.startswith("Saved ~/Desktop/Screenshot")


# -- lock screen and empty Trash: always ask, never offered to the model

def test_lock_screen_command(fake_mac, monkeypatch):
    import sys
    cmd = plan("lock_screen").commands[0]
    if sys.platform == "darwin":                                          # loading it doesn't lock
        assert system.can_lock_directly()
        assert cmd[:2] == (sys.executable, "-c") and "SACLockScreenImmediate()" in cmd[2]
    monkeypatch.setattr(system, "LOGIN_FRAMEWORK", "/nonexistent/login")
    assert plan("lock_screen").commands == (("pmset", "displaysleepnow"),)


def test_empty_trash_command(fake_mac):
    p = plan("empty_trash")
    assert p.commands == (osa('tell application "Finder" to empty trash'),)
    assert "can't be undone" in p.summary


def test_always_ask_tools_ask_even_when_listed_as_auto(fake_mac, monkeypatch, reload_config, runner):
    cfg_file = Path(config.CONFIG_PATH)
    cfg_file.write_text('[actions]\nauto_approve = ["empty_trash", "lock_screen", "volume"]\n' + cfg_file.read_text())
    reload_config()
    assert set(permissions.auto_approved()) == {"volume"}
    problems = permissions.config_problems()
    assert "auto_approve: 'empty_trash' always asks — ignored" in problems
    assert "auto_approve: 'lock_screen' always asks — ignored" in problems
    for text in ("empty the trash", "lock my mac"):
        out = pipeline.handle(text, runner=runner)
        assert out.kind == "ask" and not runner.calls
        with pytest.raises(PermissionError):
            permissions.run(out.proposal, runner=runner)
        assert "always asks first" in cards.card_for(out.proposal).note
    assert not runner.calls


def test_new_defaults_run_without_a_card(fake_mac, runner):
    for text in ("pause", "volume 20", "dnd on", "play my focus playlist", "next song"):
        out = pipeline.handle(text, runner=runner)
        assert out.kind == "done" and out.result.ok, text
    assert runner.calls[1] == list(osa("set volume output volume 20", "set volume output muted false"))
    assert [e["decision"] for e in logged()] == ["auto"] * 5


def test_planning_sends_no_apple_events(fake_mac, monkeypatch):
    """Planning only reads state (via the test hooks here); it never runs osascript/shortcuts/
    screencapture, so no permission prompt can appear before you approve."""
    def boom(*a, **k):
        raise AssertionError(f"planning ran a command: {a}")
    monkeypatch.setattr(subprocess, "run", boom)
    for text in ("pause", "play my focus playlist", "volume up", "dnd off", "take a screenshot",
                 "lock the screen", "empty the trash", "mute"):
        assert pipeline.decide_fast(text) is not None, text


# -- fast path: phrasings and traps

@pytest.mark.parametrize("text,tool,args", [
    ("play/pause", "media", {"action": "toggle"}),
    ("resume", "media", {"action": "play"}),
    ("stop the music", "media", {"action": "pause"}),
    ("play the next song", "media", {"action": "next"}),
    ("previous", "media", {"action": "previous"}),
    ("pause spotify", "media", {"action": "pause", "player": "Spotify"}),
    ("turn the volume down a bit", "volume", {"action": "down"}),
    ("quieter", "volume", {"action": "down"}),
    ("volume to 80", "volume", {"action": "set", "level": 80}),
    ("mute the sound", "volume", {"action": "mute"}),
    ("focus mode on", "dnd", {"state": "on"}),
    ("disable do not disturb", "dnd", {"state": "off"}),
    ("lock", "lock_screen", {}),
    ("take a screenshot of the window", "screenshot", {"what": "window"}),
    ("screenshot", "screenshot", {"what": "screen"}),
    ("clear out the trash", "empty_trash", {}),
    ("play the playlist deep work", "playlist", {"name": "deep work"}),
])
def test_fast_path_system_phrasings(fake_mac, text, tool, args):
    m = fastpath.match(text)
    assert isinstance(m, fastpath.Match) and m.tool == tool and {k: m.args[k] for k in args} == args


@pytest.mark.parametrize("text", ["stop", "next week", "lock in", "mute point", "play a chill playlist",
                                  "volume of a sphere", "screenshot tips", "take a break", "play some jazz",
                                  "trash talk", "the next big thing", "pause."])
def test_fast_path_system_traps(fake_mac, text):
    m = fastpath.match(text)
    assert m is None or text == "pause.", (text, m)                       # "pause." is just pause


def test_unknown_playlist_is_refused_with_your_list(fake_mac, runner):
    out = pipeline.handle("play my gym playlist", runner=runner)
    assert out.kind == "refused" and "Your playlists: focus, deep work, chill" in out.message
