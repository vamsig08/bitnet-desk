# BitNet Desk — a zero-GPU, fully private desktop assistant

A small desktop chat app that runs **Microsoft's BitNet b1.58 2B4T** — a natively 1.58-bit
(ternary: −1, 0, +1) LLM — on your **CPU only**, via the official `bitnet.cpp` runtime.
The model server listens on `127.0.0.1` only, and the model never sends anything you type anywhere. The only network traffic is an action you asked for and approved, such as a download.

```
┌──────────────┐  HTTP (localhost)  ┌──────────────────────────────┐
│ bitnet_desk/ │ ─────────────────▶ │ bitnet.cpp llama-server       │
│ Tkinter UI   │ ◀── token stream ─ │ BitNet-b1.58-2B-4T i2_s GGUF  │
└──────────────┘                    └──────────────────────────────┘
```

## What you get
- Streaming chat with a Stop button, Enter to send / Shift+Enter for a newline
- **Attach…** a text/code file to ask questions about it (first 6,000 chars)
- Rolling context: oldest turns are dropped automatically to fit the window
- **Save…** the conversation as Markdown; **Clear** to start over
- Auto-starts the model server and shuts it down when you close the window
- Standard-library Python only — no extra pip packages for the app itself

## Prerequisites
| | macOS | Linux | Windows |
|---|---|---|---|
| Python | 3.9+ with Tk (`brew install python-tk` if needed) | 3.9+ and `python3-tk` | 3.9+ (Tk included) |
| Compiler | Xcode CLT (`xcode-select --install`) | clang ≥ 18 (`bash -c "$(wget -O - https://apt.llvm.org/llvm.sh)"`) | VS 2022 with *Desktop C++*, *C++ Clang Compiler*, *MSBuild support for LLVM* |
| Other | git, cmake ≥ 3.22 | git, cmake ≥ 3.22 | git, cmake |

Disk: ~1.2 GB for the model plus the build. RAM: ~1–2 GB while running.

## Setup (one time, needs internet)
```bash
# macOS / Linux
chmod +x setup.sh && ./setup.sh
```
```powershell
# Windows — from "Developer PowerShell for VS 2022"
.\setup.ps1
```
This clones `microsoft/BitNet`, downloads the `i2_s` GGUF weights from Hugging Face, and builds
the ternary kernels plus `llama-server`.

## Run (fully offline from here on)
```bash
python3.12 -m bitnet_desk
```
(`python3.12 assistant.py` still works too.) First launch takes a few seconds while the model loads. You can now disconnect from the internet.

### From any app: ⌥Space
Press **⌥Space** (Option-Space) anywhere and a small command box appears near the top of the
screen. Type a command or a question and press Return:
- **A command that runs by itself** ("open Safari"): it happens and the box gets out of the way.
- **A command that needs approval:** the card shows up in the box. ⌘↩ runs it, Esc cancels it.
- **Something to read** (the time, found files, a chat reply): the box stays open. **Open in window**
  (⌘O) shows the full conversation.
- **Esc** (or ⌥Space again) hides the box and puts you back where you were.

The box and the main window share one conversation: what you do in the box shows up in the window
too.

There's also a **menu-bar icon** (a rounded square with three dots). Its menu has Show command box,
Open window, Launch at login, and Quit. Closing the main window only hides it: the model stays loaded
in the background, so ⌥Space answers instantly. To stop everything, use **Quit** in the menu (or ⌘Q).
Only one copy runs at a time.

**Launch at login:** tick it in the menu, or run `python3.12 -m bitnet_desk --login on` (`off`,
`status`). It starts quietly in the background (menu bar and hotkey only) the next time you log in.
macOS shows a one-time "Background item added" notice, and the item appears as *python3.12* in System
Settings › General › Login Items. If you move this folder, turn it off and on again.
`python3.12 -m bitnet_desk --background` starts that way by hand.

**Permissions:** none, for the hotkey, the box and the menu bar. The hotkey uses the macOS API that
reports only that one key combination, so the app never sees anything else you type (no
Accessibility or Input Monitoring access needed).

**Hotkey clashes:** macOS lets two apps use the same shortcut, and then both react. The app warns you
(in the window and the menu) when ⌥Space is also a macOS shortcut or ChatGPT's launcher shortcut. To
use a different combination, set it in the settings file:
```toml
[ui]
hotkey = "ctrl+alt+space"     # or "⌃⌥Space", "cmd+shift+k", "f5"…; false for no hotkey
```

## Commands
Type a command in the chat box and it happens right away, without asking the model:

| Type | What happens |
|---|---|
| `open Safari`, `launch Spotify`, `open vs code` | opens the app |
| `open my downloads folder`, `open ~/Documents` | opens the folder in Finder |
| `what time is it`, `what's the date today`, `is it friday yet?` | answers from your Mac's clock (never the model) |
| `what can you do?`, `help` | lists the commands |
| `find files named invoice`, `where's my resume?` | searches your home folder (Spotlight); click a result to open it |
| `start project myapp` | opens the project folder in VS Code and a Terminal there (projects are set up in the settings file) |
| `open github.com`, `go to https://…` | opens the page in your browser — **asks first** |
| `download https://…/file.pdf` | saves it to ~/Downloads, never overwriting — **asks first** |
| `` run `git status` `` or `$ git status` | runs exactly that command in your home folder — **always asks** |
| `pause`, `play`, `next song`, `previous track`, `pause spotify` | controls Spotify or Music (whichever is playing; Spotify if neither is open) |
| `play my focus playlist` | plays a playlist you listed in the settings file |
| `volume up`, `turn it down`, `volume 30`, `mute`, `unmute` | changes the Mac's volume |
| `turn on do not disturb`, `dnd off` | switches Do Not Disturb (needs two Shortcuts, see below) |
| `take a screenshot`, `screenshot this window`, `screenshot of an area` | saves a screenshot where macOS saves them (usually the Desktop) |
| `lock my mac` | locks the screen — **always asks** |
| `empty the trash` | permanently empties the Trash — **always asks** |

Polite versions work too ("can you open Calculator for me?"). These work even while the model is
still loading.

If a message starts with *open, start, launch, download, find, run, play* or *show* (or with `/`)
and isn't one of the commands above, the model gets about a second to decide whether it's a command
("play some music", "launch the terminal for me"). It can only pick from the commands above, and
it can't make up web addresses, file paths or shell commands: those must be in your message. When
it guessed what you meant, the card says so, and **Just chat** answers your message normally
instead. Everything else goes straight to the chat. How often the model gets it right is measured
in `docs/evals.md`.

**Permissions.** Every action has a risk level: *read-only*, *changes things*, *network* or *shell*.
Read-only actions just run. Opening apps and files/folders, music, volume, Do Not Disturb and
screenshots run straight away too, because they're on the auto-approve list (you can change that, see
below). Locking the screen and emptying the Trash always ask, whatever the list says. Anything else shows a card with the exact
command, and nothing runs until you click **Run** (or press ⌘↩). **Deny** (or Esc) cancels it. Opening
something that runs code, like a `.command` script, always asks. Network and shell actions will
always ask, whatever the settings say.

**"That was wrong."** Under every reply and action there's a small *⚑ that was wrong* link. Click it,
or type `/wrong` (optionally followed by what you expected, e.g. `/wrong I meant Spotify`), and the
app saves your message and what it did to `misses.jsonl` in the same folder as the settings file,
readable only by you. Later, run
```bash
python3.12 -m bitnet_desk.actions misses          # or: misses --list
```
It shows each miss and asks what the right answer was. Each one becomes a test case in
`evals/actions.jsonl`, so the app's eval grows from how you actually use it.

**Action log.** Every action, including ones you denied, is written to
`~/Library/Application Support/BitNetDesk/actions.jsonl` (only you can read it). To see the latest
entries:
```bash
python3.12 -m bitnet_desk.actions log
```

## Configuration
### Settings file
Create `~/Library/Application Support/BitNetDesk/config.toml` (any of these lines are optional):
```toml
threads = 4             # ≈ performance-core count is usually fastest
ctx = 4096              # context window (tokens)
max_tokens = 768        # max reply length
temperature = 0.6
system_prompt = "You are my terse coding buddy."
```
Actions have their own sections in the same file:
```toml
[actions]
auto_approve = ["open_app", "open_path", "now", "media", "playlist", "volume", "dnd", "screenshot"]
                                                  # ↑ the default; [] makes everything ask
log = true                                        # false turns off the action log

editor = "Visual Studio Code"                      # what "start project" opens the folder in
terminal = "Terminal"                             # or "iTerm"; false for no terminal
download_max_mb = 2048

[aliases.apps]
"music" = "Spotify"      # "open music" opens Spotify

[projects.myapp]
path = "~/code/myapp"
aliases = ["my app"]     # "start my app" works too
# editor = "Xcode"       # optional, per project
```
Music, playlists and Do Not Disturb:
```toml
[media]
player = "auto"          # or "Spotify" / "Music"; auto = whichever is playing, else Spotify

[playlists]              # "play my focus playlist"
focus = "spotify:playlist:37i9dQZF1DWZeKCadgRdKQ"     # Spotify: Share › Copy link works too
"road trip" = "Road Trip"                            # anything else: a playlist in the Music app

[actions]
dnd_on_shortcut = "BitNet DND On"     # the default names
dnd_off_shortcut = "BitNet DND Off"
```
macOS has no public switch for Do Not Disturb, so the app runs two Shortcuts you make once. In the
Shortcuts app, make one called **BitNet DND On** with the single action *Set Focus* → Do Not Disturb →
Turn On, and one called **BitNet DND Off** with *Set Focus* → Turn Off.

**macOS permission prompts (once each):**
- The first time you control **Spotify** or **Music**, macOS asks whether the app may control it
  (Automation).
- The first time you **empty the Trash**, it asks the same about **Finder**.
- The first **screenshot** asks for **Screen Recording**. Without it, you only get the wallpaper and
  the app's own windows.

The prompt names whatever started BitNet Desk: Terminal, or *python3.12* when it starts at login. So
you may see each prompt once per way of starting it. Volume, Do Not Disturb and lock screen ask for
nothing. You can change these choices later in System Settings › Privacy & Security.

Shell commands are never written by the model: only a command you type in backticks, quotes, or
after `$ ` is run, and only after you approve it.

Restart the app after editing it (reading the file needs Python 3.11+). If a line has a mistake, the app ignores that line and says so
in the chat window. On Windows the file goes in `%APPDATA%\BitNetDesk\`; on Linux in
`~/.config/bitnet-desk/`. Set `BITNET_CONFIG` to use a file somewhere else.

### Environment variables
These override the settings file.

| Variable | Default | Meaning |
|---|---|---|
| `BITNET_THREADS` | half your logical cores | ≈ physical cores is usually fastest |
| `BITNET_CTX` | 4096 | context window (tokens) |
| `BITNET_MAX_TOKENS` | 768 | max reply length |
| `BITNET_TEMP` | 0.6 | sampling temperature |
| `BITNET_SYSTEM` | built-in | your own system prompt |
| `BITNET_PORT` | 8080 | local server port |
| `BITNET_DIR` / `BITNET_MODEL` | `./BitNet/…` | custom install or model path |
| `BITNET_SERVER` | bitnet.cpp's `llama-server` | another `llama-server` binary, e.g. Homebrew llama.cpp's for a TQ2_0 model |

Example: `BITNET_THREADS=8 BITNET_SYSTEM="You are my terse coding buddy." python3.12 -m bitnet_desk`

## Privacy notes
- The server binds to `127.0.0.1`, so other devices on your network can't reach it.
- The model never sends your data anywhere. The app's only network traffic besides `localhost`
  is an action you asked for and approved (opening a web page, a download); the action log records
  each one.
- After setup, the model server itself never needs the network: you can block `llama-server` in
  your firewall or set `HF_HUB_OFFLINE=1` to be extra sure.
- Conversations live only in memory unless you click **Save…**. `server.log` contains
  server diagnostics, not your messages' content beyond what llama-server logs by default.

## Honest expectations
BitNet 2B4T is a 2.4B-parameter model. It's fast and light on CPU and solid for drafting,
rewriting, summarising a pasted file, brainstorming, and simple Q&A. It will be weaker than
large cloud models on niche facts, long multi-step reasoning, and non-trivial code — treat
factual answers as a starting point.

## Troubleshooting
- **Apple Silicon: replies are `!!!!` or `@@@@`** — your `BitNet/` checkout is newer than the pinned
  commit (e.g. you set `BITNET_REF`). Re-run `./setup.sh` without it. Why: `docs/setup-notes.md`.
- **"llama-server not found"** — setup didn't finish; re-run it and check the build output.
- **"Model server exited"** — read `server.log`. Port in use? Try `BITNET_PORT=8090`.
- **Garbled / run-on replies** — make sure you used the `i2_s` GGUF from
  `microsoft/BitNet-b1.58-2B-4T-gguf` (mainline Ollama / llama.cpp can't run `i2_s`).
- **Slow** — set `BITNET_THREADS` to your physical core count; close heavy apps.

## Ideas to extend (all still CPU-only)
- Voice in/out: `whisper.cpp` for speech-to-text, `piper` for text-to-speech
- Local document Q&A: a small CPU embedding model + SQLite for retrieval over your notes
- Global hotkey to summon the window (e.g. `pynput`)
