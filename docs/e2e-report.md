# End-to-end report (real Mac)

`python3.12 -m bitnet_desk.e2e` — 2026-10-17 17:56, commit 0f85640 + uncommitted changes, macOS 26.6.2, areas: permissions, volume, media, screenshot, project, dnd, always_ask, app, routine.

No synthetic keystrokes or clicks: commands go through the real pipeline and permission gate; the app is driven by calling its own methods. Lock screen and empty Trash are only planned, never run.

**56 passed, 0 failed, 3 skipped.**

## permissions

| | check | detail |
|---|---|---|
| ✅ | Automation → Spotify | app not running |
| ✅ | Automation → Music | app not running |
| ✅ | Automation → Terminal | allowed |
| ✅ | Automation → System Events | allowed |
| ✅ | Automation → Finder (empty Trash; never used here) | would ask |
| ✅ | Screen Recording | allowed |
| ✅ | Accessibility (read window titles / menu bar) | allowed |

## volume

| | check | detail |
|---|---|---|
| ✅ | “volume 30” → 30% | now 30% (done) |
| ✅ | “turn it down” → 20% | now 20% (done) |
| ✅ | “louder” → 30% | now 30% (done) |
| ✅ | “mute” → muted |  |
| ✅ | “unmute” → not muted |  |
| ✅ | model: “/make it quieter” → down 10 | 30% → 20% (done, volume) |

## media

| | check | detail |
|---|---|---|
| ✅ | “play” → playing | playing (Playing (Spotify).) |
| ✅ | “next song” → another track | spotify:track:2i6veFyjDIodH3hgpkwxK6 → spotify:track:51vxIDnFJOzotXLQTrwV29 |
| ✅ | “go back a song” → the previous track | spotify:track:51vxIDnFJOzotXLQTrwV29 → spotify:track:2i6veFyjDIodH3hgpkwxK6 (first was spotify:track:2i6veFyjDIodH3hgpkwxK6) |
| ✅ | “pause spotify” → paused |  |
| ✅ | “play/pause” → playing |  |
| ✅ | “can you pause the music” → paused |  |
| ✅ | “play my focus playlist” with none configured → says how | There's no playlist called “focus” in your settings. Add it to the settings file under [pl |
| ✅ | “pause music in apple music” with Music closed → refused, Music not opened | Nothing is playing — Music isn't open. |

## screenshot

| | check | detail |
|---|---|---|
| ✅ | “take a screenshot” → a PNG of the screen | /Users/vamsig/Desktop/Screenshot 2026-10-17 at 17.55.49.png 2880×1800 |
| ✅ | “screenshot this window” plans -i -W (waits for your click, so not run) | screencapture -x -i -W /Users/vamsig/Desktop/Screenshot 2026-10-17 at 17.55.49.png |
| ✅ | “screenshot of an area” plans -i -s (waits for your click, so not run) | screencapture -x -i -s /Users/vamsig/Desktop/Screenshot 2026-10-17 at 17.55.49.png |

## project

| | check | detail |
|---|---|---|
| ✅ | “start bitnet-desk” shows a card (asks first) | Start project bitnet-desk: open ~/Projects/bitnet-desk in Visual Studio Code and Terminal |
| ✅ | approved → both commands ran | Started project bitnet-desk. |
| ✅ | a Terminal window opened in bitnet-desk | bitnet-desk — -bash — 360×99 |
| ✅ | VS Code shows bitnet-desk | Welcome — bitnet-desk |

## dnd

| | check | detail |
|---|---|---|
| ✅ | without the Shortcuts → refused with how to make them | To switch Do Not Disturb I run two Shortcuts you make once in the Shortcuts app: “BitNet DND On” (on |
| ➖ | Do Not Disturb on/off | the two Shortcuts aren't installed |

## always_ask

| | check | detail |
|---|---|---|
| ✅ | “lock my mac” → a card, never automatic | /opt/homebrew/opt/python@3.12/bin/python3.12 -c 'import ctypes; ctypes.CDLL("/System/Library/PrivateFrameworks/login.framework/Versions/Current/login").SACLockScreenImmediate()' |
| ✅ | lock_screen: card says it always asks |  |
| ✅ | lock_screen: never offered to the model |  |
| ✅ | lock_screen: the harness can't run it (guard on) |  |
| ✅ | “empty the trash” → a card, never automatic | osascript -e 'tell application "Finder" to empty trash' |
| ✅ | empty_trash: card says it always asks |  |
| ✅ | empty_trash: never offered to the model |  |
| ✅ | empty_trash: the harness can't run it (guard on) |  |
| ✅ | lock screen uses the real lock call (not display sleep) |  |

## app

| | check | detail |
|---|---|---|
| ✅ | model server ready |  |
| ✅ | hotkey alt+space registered |  |
| ➖ | ⌥Space is free | also used by ChatGPT (its launcher shortcut) |
| ✅ | menu-bar icon created |  |
| ✅ | menu-bar icon is in the menu bar | BitNet Desk |
| ✅ | box shows in front, typing goes to it | front pid 64879, us 64879 |
| ✅ | box: “what time is it” answered in the box, box stays | ssistant: It's 17:55 (local time) on Saturday, 17 October 2026. ⚑ that was wrong |
| ✅ | box: “volume 25” runs and the box gets out of the way | volume 25% |
| ✅ | box: “empty the trash” shows its card in the box |  |
| ✅ | box: Esc denies the card; nothing ran | empty_trash denied |
| ✅ | box: screenshot taken with the box out of the way, box back with the file | /Users/vamsig/Desktop/Screenshot 2026-10-17 at 17.55.56.png |
| ✅ | box: a chat reply streams into the box and the window keeps the record | It's a beautiful day today! |
| ✅ | box: Esc hides it and gives the keyboard back | front pid 1272, before 1272 |
| ✅ | closing the window hides it; hotkey and icon stay |  |
| ✅ | launch at login toggles the LaunchAgent (valid plist, this Python) |  |
| ✅ | Quit removes the icon and the hotkey |  |
| ✅ | a second copy refuses to start | BitNet Desk is already running — use its hotkey or menu-bar icon. |

## routine

| | check | detail |
|---|---|---|
| ✅ | “work mode” without the DND Shortcuts → refused, naming the step | Routine “work-mode”, step 2 “dnd on”: To switch Do Not Disturb I run two Shortcuts you make once in the Shortc |
| ➖ | work mode end to end | the Do Not Disturb Shortcuts aren't installed yet |
| ✅ | stops at the first failure and says which steps ran | Ran 1 of 3 steps, then stopped: · ✓ Volume set to 31%. · ✗ The command exited with code 1. · – Set the volume to 32% (not run) |

## State restored afterwards

- volume: volume back to 67%
- media: Spotify paused and quit (it wasn't open before)
- screenshot: deleted the test screenshot
- project: closed the test Terminal window 106472
- app: deleted the box's test screenshot
- app: launch at login back off; volume back as it was

## Needs you

- Add the two Do Not Disturb Shortcuts: `python3.12 -m bitnet_desk --setup-shortcuts`, then click “Add Shortcut” in each preview (or double-click the two files in ~/Library/Application Support/BitNetDesk/shortcuts/).
- ⌥Space is also ChatGPT (its launcher shortcut): both open when you press it. Change ChatGPT's shortcut (ChatGPT › Settings › Keyboard shortcut), or set [ui] hotkey in config.toml.
