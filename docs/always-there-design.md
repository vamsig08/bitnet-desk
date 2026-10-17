# Always there: hotkey, menu bar, system tools, routines — design

Goal: something you reach for all day. Press ⌥Space in any app, type "pause", "volume 30",
"work mode", and it's done — behind the same permission gate as every other action.

Built in three steps, each with tests and a commit, stopping for a try-out after each:

1. Global hotkey + command box + menu-bar icon + background server + launch at login
2. System and media tools
3. Routines

---

## macOS permissions — what each part needs, and why

| Part | Permission | Why |
|---|---|---|
| Global hotkey | **none** | Uses Carbon `RegisterEventHotKey`: macOS tells us when *that one key combo* is pressed and nothing else. We never see other keystrokes, so it needs neither Accessibility nor Input Monitoring. |
| Menu-bar icon | none | Tk 9's built-in `tk systray` (an `NSStatusItem`). |
| Launch at login | none | A LaunchAgent plist in `~/Library/LaunchAgents`. macOS shows a "Background item added" notice once and lists it (as `python3.12`) in System Settings › General › Login Items, where you can switch it off too. |
| Bring the box to front | none | `NSApp activateIgnoringOtherApps` via ctypes, on our own app. |
| Volume, mute | none | AppleScript `set volume …` (Standard Additions) only touches our own process. |
| Play/pause/next/previous, playlists | **Automation → Spotify / Music** (step 2) | Sent as AppleScript commands to the app. macOS asks once per app ("… wants to control Spotify"). |
| Empty Trash | **Automation → Finder** (step 2) | `tell application "Finder" to empty trash`. Asked once. |
| Screenshot | **Screen Recording** (step 2) | `screencapture` needs it to capture other apps' windows (without it you get only the wallpaper and our own windows). Asked once. |
| Do Not Disturb | none from us (step 2) | macOS has no public Focus API; we run two Shortcuts you create once (Shortcuts app › "Set Focus"). |
| Lock screen | none (step 2) | `SACLockScreenImmediate` in `login.framework`. It's a private API, but it's the one that actually locks, and it's present on macOS 26.6. |

**Who gets the permission?** macOS gives the permission to whichever app started us: **Terminal**
when you run `python3.12 -m bitnet_desk` from Terminal, or **python3.12** when it starts at login.
So the Automation and Screen Recording prompts may appear twice, once per launch method.

**Deliberately avoided:** keyboard listeners such as `pynput`, CGEventTap or NSEvent global
monitors. They need Input Monitoring or Accessibility because they see *every* key you type,
which is the wrong trade for a hotkey. We also avoid sending fake media keys or `keystroke`
commands (both need Accessibility) and new packages (`pyobjc`, `rumps`): everything uses the
standard library (ctypes) and the Tk we already ship.

The permission-free approach is verified: a test app registered ⌃⌥⌘F12 through ctypes inside
Tk's event loop, and the handler fired when another process pressed it.

## Step 1 — hotkey, command box, menu bar, background, login

**Hotkey.** `[ui] hotkey = "alt+space"` (also accepts `⌥Space`, `cmd+shift+k`, `ctrl+alt+b`, …),
parsed into a Carbon key code and modifiers (pure function, tested). Pressing it toggles the command box.
- **Clash:** if another app already holds the combo, registration fails. The menu then shows
  "⌥Space is taken by another app — set [ui] hotkey" instead of failing silently.
- ⚠️ **ChatGPT is installed and its launcher uses ⌥Space.** When it's running, one of the two
  loses. Either change ChatGPT's shortcut (Settings › Keyboard shortcut) or set ours to something
  else. I'm keeping ⌥Space as the default, as you asked.

**Command box.** A small, borderless, always-on-top window near the top of the screen: one input
line, plus a short output area that appears only when there's something to show.
- **Enter** runs the message through the same pipeline as the main window: fast path, then the
  command-verb check, then the router, then chat.
- **Auto-approved actions:** the box hides right away and the action runs ("open safari" → Safari
  comes forward). If the action fails, the box reappears with the error. If it has an answer (the
  time), the box stays and shows it.
- **Actions that ask:** the approval card appears in the box. ⌘↩ runs it; Esc denies it.
- **Chat:** the reply streams into the box, which grows up to about 12 lines. "Open in window"
  (⌘O) shows the main window.
- **Esc** hides the box (denying an open card first); the hotkey toggles it.
- **One conversation:** the box and the main window share one history and one transcript, so a
  question asked in the box can be followed up in the window. Exchanges from the box are copied into
  the main transcript too.
- **⚑ that was wrong** and `/wrong` work in the box too.

To do this without duplicating the 450-line window, the window's output code moves behind a
small "view" (write text, write links, show card, offer ⚑) that the main window and the box both
implement. Everything else (pipeline, permission gate, log, streaming) stays shared.

**Menu-bar icon.** A click opens a menu with:
- Show command box (⌥Space)
- Open window
- the hotkey's status (only if it's taken)
- Launch at login ✓
- Quit BitNet Desk

**Background.** Closing the main window now *hides* it. The model server and hotkey keep running.
Quit (in the menu, ⌘Q, or the Dock's Quit) stops the server we started and exits. Clicking the Dock
icon reopens the window. `python3.12 -m bitnet_desk --background` starts with only the menu bar
and hotkey, no window; launch at login uses this. Only one copy runs at a time: a lock file in
Application Support. A second launch says "already running — press ⌥Space" and exits.

**Launch at login.** The menu toggle (or `python3.12 -m bitnet_desk --login on|off`) writes or
removes `~/Library/LaunchAgents/com.bitnetdesk.app.plist`.
- The agent runs `<this python> -m bitnet_desk --background` from the project folder.
  `RunAtLoad`, no `KeepAlive`, output goes to a log file in Application Support.
- Turning it on doesn't start a second copy now; it takes effect at the next login.
- If you move the repo, toggle it off and on again.
- Toggling is logged in the action log, like any change to the Mac.

**Tests (no real hotkeys or menus):**
- hotkey parsing, including bad strings
- plist contents and turning launch at login on and off (temporary home folder)
- the single-instance lock
- the command box's decision logic as plain functions: hide after an auto action, stay on answers
  and errors, card in box
- the background flag

Tk-level wiring is checked by hand in the try-out.

## Step 2 — system and media tools

| Tool | Says | Risk | Default | Command shown (and run) |
|---|---|---|---|---|
| `media` | play, pause, resume, next, previous (Spotify or Music) | changes | auto ⁽¹⁾ | `osascript -e 'tell application "Spotify" to playpause'` |
| `playlist` | play my focus playlist | changes | auto ⁽¹⁾ | `osascript -e 'tell application "Spotify" to play track "spotify:playlist:…"'` |
| `volume` | volume up / down / 30 / mute / unmute | changes | auto ⁽¹⁾ | `osascript -e 'set volume output volume 30'` |
| `dnd` | Do Not Disturb on / off | changes | auto ⁽¹⁾ | `shortcuts run "BitNet DND On"` |
| `screenshot` | take a screenshot (screen / window / area) | changes (writes a file) | auto ⁽¹⁾ ⁽²⁾ | `screencapture -x ~/Desktop/Screenshot 2026-10-17 at 11.43.13.png` (`-W` window, `-s` area) |
| `lock_screen` | lock the screen | changes | **always asks** | `python3.12 -c "…SACLockScreenImmediate()"` |
| `empty_trash` | empty the Trash | changes | **always asks** | `osascript -e 'tell application "Finder" to empty trash'` |

⁽¹⁾ **Decision for you:** a card for "pause" or "volume down" would be more annoying than useful,
so I propose adding `media`, `playlist`, `volume`, `dnd` and `screenshot` to the *default*
`auto_approve` list. They're easily undone and local. You can remove any of them in config.toml.

⁽²⁾ An approval card would show up in the screenshot itself. The box hides before the
shot either way.

- **always asks:** a new `always_ask` flag on the tool. It ignores `auto_approve`, and listing such a
  tool there produces a config warning. The model can't change it, the same as risk levels.
- **Which player:** `[media] player = "auto"` means Spotify if it's running, else Music if it's
  running, else Spotify. "play" with neither open launches it. "on spotify" / "in music" in the
  message wins. The running check (`pgrep`) is read-only and happens when the card is built, so the
  command shown names the real app.
- **Playlists:** `[playlists] focus = "spotify:playlist:37i9…"` (the URI from Spotify's Share ›
  Copy link). Music playlists are played by name. We never search the web for one.
- **Do Not Disturb:** you make two Shortcuts once ("BitNet DND On" / "BitNet DND Off", each a single
  "Set Focus" action; the names are configurable). If they're missing, the tool says how to make them
  instead of failing quietly. `shortcuts list` is read-only.
- **Screenshot:** saves where macOS saves screenshots (`com.apple.screencapture location`, else the
  Desktop). The result is a clickable path.

**Fast-path rules (examples):**
- `pause` · `play` · `resume` · `play music` · `next` · `next song` · `skip this track` ·
  `previous track` · `pause spotify`
- `volume up` · `turn it down` · `louder` · `set volume to 30` · `volume 30%` · `mute` · `unmute`
- `turn on do not disturb` · `dnd off` · `focus mode on`
- `lock my mac` · `lock the screen`
- `take a screenshot` · `screenshot this window`
- `empty the trash`
- `play my focus playlist` · `play playlist focus`

**Eval traps (must stay chat):** "how do I take a screenshot on a Mac", "what's the volume of a
cylinder", "help me lock in on this essay", "should I empty the trash", "play a game with me",
"next, explain recursion", "pause and think before answering", "turn up the heat on this intro".
About 30 new cases in all (positives, traps, and polite or casual wrappers such as "can you pause
the music").

**The model router** may offer `media`, `playlist` and `volume` (grounded by their keywords in the
message, as find_files is). It **never** offers `lock_screen`, `empty_trash`, `dnd` or `screenshot`:
those are fast path only, so a misread can't lock your Mac or wipe the Trash. The command-verb check
stays as you set it: "pause", "mute" and "lock" aren't command verbs, so the fast path must catch
them. The eval checks it does.

## Step 3 — routines

```toml
[routines.work-mode]
aliases = ["work mode", "start work"]
steps = ["start bitnet-desk", "play my focus playlist", "dnd on"]
```

- **Steps are plain commands** that must each match the **fast path**: no model, no ambiguity.
  They're checked at startup; a step that doesn't match is a config error shown in the window,
  and that routine is disabled. Routines can't contain routines.
- **Trigger:** "work mode", "start work mode", "run work mode", "routine work mode", or any alias
  (fast path).
- **One card lists every step** with its exact commands. The card's risk is the highest of the
  steps (with a shell step it shows "shell"). A routine **always asks**, even when every step is
  auto-approved: one click, then it all runs. An always-asks step (lock, empty Trash) is covered by
  that same click, and the card marks it.
- **Runs in order and stops at the first failure.** Progress shows "2/3 Playing focus…". The result
  lists every step: `✓ Started project bitnet-desk · ✗ Play focus: Spotify isn't installed · – DND on (not run)`.
- **Log:** each step gets its own entry (route `routine:work-mode`), plus one summary entry.
- **Plans are frozen** when the card is built, the same as single actions. What you approve is
  what runs.
- **Eval cases:** routine triggers, an unknown routine, and the trap "what's my morning routine".

## Changes made while building step 1

- **Clashes aren't refused.** Two apps can register the same combination, and *both* get every press
  (tested: two processes on ⌃⌥⌘F12, one press, both fired). So "taken" never happens for app clashes.
  We check the likely ones ourselves: macOS's own shortcuts (`com.apple.symbolichotkeys`, with the
  defaults for Spotlight, input sources and screenshots) and ChatGPT's launcher
  (`com.openai.chat`). ⌥Space currently clashes with ChatGPT on the dev Mac, and the app says so at
  startup and in the menu.
- **The hotkey callback can't touch Tk.** It runs inside Tcl's event processing, and calling Tk from
  there aborts Python ("PyEval_RestoreThread … GIL is released"). The callback puts a message on
  the app's queue, which the 30 ms poll already drains.
- **The box has a thin title bar.** On macOS, borderless Tk windows (`overrideredirect`, or
  `-stylemask {}`) never get keyboard focus; only titled windows do. The box is a titled window with no
  buttons and an empty title.
- **Activation:** `[NSApp activate]` (macOS 14+) is called before `activateIgnoringOtherApps:`. The
  older call alone didn't bring the box forward when System Settings was the front app.
- **Giving focus back:** Esc reactivates the app you were in. After an auto action ("open Notes"),
  the box only hands focus back if we're still in front 400 ms later. Otherwise the opened app keeps
  it, and so does a box you've reopened in the meantime.

## Not in scope here

Voice, "ask about selection", custom icons or themes for the box, and a Dock-less (menu-bar-only)
mode. That last one is one ctypes call (`setActivationPolicy:`) if you want it later, but it also
removes the app's ⌘-menu.
