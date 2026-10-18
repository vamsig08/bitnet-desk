# BitNet Desk — Roadmap

Vision: a Jarvis-style assistant that controls your Mac, running a 1-bit LLM on the CPU. The model
never sends your data anywhere; the only network traffic is an action you asked for and approved.

Strategy: be exceptionally good at a small number of useful local tasks first, all behind one
consistent permission system, then add capabilities over time. UI polish and saved conversations wait.

Each phase ends with something usable. Tick boxes as items land; keep the log at the bottom.

---

## Phase 0 — Get v0 running on the Mac
- [x] `./setup.sh` completes on Apple Silicon (fix script issues found along the way) — pinned BitNet `765741d`, see `docs/setup-notes.md`
- [x] `python3 assistant.py` opens, loads the model, answers a question
- [x] Record baseline: Mac model, chip, RAM, threads, tokens/s, load time, RAM use → `docs/benchmarks.md` 

**Done when:** you can chat with the model offline (Wi-Fi off). — everything above works; the Wi-Fi-off check is still to do by hand.

## Phase 1 — Solid foundation (done; remaining polish moved to Phase 7)
- [x] Split `assistant.py` into the `bitnet_desk/` package (config, server, llm, ui, app) with no behaviour change
- [x] Current local date and weekday in the system prompt, local time on the newest message only — computed on every request, never stored in history
- [x] `tests/conftest.py` mock llama-server fixture; tests for prompt format, streaming, stop, context fitting
- [x] Real token counting via `/tokenize` instead of the chars/3 estimate
- [x] Config file at `~/Library/Application Support/BitNetDesk/config.toml` (threads, ctx, temp, system prompt)

## Phase 2 — Actions (done; log window moved to Phase 4) — design: `docs/actions-design.md`
- [x] Action core: tool registry with risk levels (read-only / changes / network / shell), frozen `Plan` objects, permission gate — with `[actions] auto_approve`, JSONL log + `python -m bitnet_desk.actions log`
- [x] Approval card in the chat window showing the exact command; nothing non-read-only runs without a click
- [ ] Action log (local JSONL) + a window to review it
- [x] Tools: current date/time, find files (`mdfind`), open app, open file/folder, open URL, start project (`[projects]` in config.toml), download to ~/Downloads, run shell command
- [x] Fast path: rule-based matching for common commands, no model call
- [x] Model path: model picks one tool + JSON args under the server's `json_schema` grammar; confidence from token probabilities; grounding checks on arguments
- [x] Failure handling: unsure / no tool / bad arguments / server down (see design)
- [x] Action eval (65 commands): fast path scored in pytest, model path by `python -m bitnet_desk.actions eval` → `docs/evals.md`

- [x] "That was wrong" loop: ⚑ link / `/wrong` save misses locally; `python -m bitnet_desk.actions misses` turns them into eval cases

**Done when:** "open Safari", "start project bitnet-desk", "download https://…", "find my resume" and
"what time is it" all work from one chat box; non-read-only actions wait for one approval click; the
action log shows everything; eval numbers are recorded.

## Phase 3 — Reach for it all day (next) — design: `docs/always-there-design.md`
Step 1 — always one keypress away (no macOS permissions needed):
- [x] Global hotkey (`[ui] hotkey`, default ⌥Space; clash detected and shown) toggles a compact command box from any app
- [x] Command box: same pipeline and permission gate as the window; auto actions hide it, cards and chat replies show in it; shares history with the main window
- [x] Menu-bar icon (show box, open window, launch at login, quit); closing the window hides it, the model server keeps running; single instance
- [x] Launch at login (LaunchAgent, `--background`), toggled from the menu or `--login on|off`

Step 2 — system and media tools (each with fast-path rules and eval cases):
- [x] Media: play/pause/next/previous for Spotify and Music; play a configured playlist
- [x] Volume up/down/set/mute; Do Not Disturb on/off (via two Shortcuts); take screenshot
- [x] Lock screen and empty Trash — `always_ask`, fast path only (never offered to the model)

Step 3 — routines:
- [x] `[routines.<name>]` in config.toml: steps are fast-path commands; one approval card lists every step; runs in order, stops at the first failure and reports which steps ran

Testing:
- [x] Real-Mac end-to-end harness (`python3.12 -m bitnet_desk.e2e` → `docs/e2e-report.md`): full pipeline, real effects verified and restored, no synthetic input; lock screen and empty Trash planned only

**Done when:** from any app, ⌥Space → "work mode" → one click starts the project, the playlist and
Do Not Disturb; "pause", "volume 20" and "lock my mac" work the same way; the eval covers every new tool.

## Phase 4 — More actions (grow one tool at a time, each with eval cases)
- [ ] Action log window (review, filter, re-run) — moved from Phase 2
- [ ] Notes and files: create note, append to file, move file to folder (show a preview/diff)
- [ ] Open & summarise a file (model reads supplied text — its strength)
- [ ] Mac control: dark mode, quit app, list running apps (volume and Do Not Disturb moved to Phase 3)
- [ ] Clipboard: "summarise / rewrite what I copied"
- [ ] Reminders / Calendar via AppleScript (read-only first, then create with approval)
- [ ] Two-step actions (e.g. find a file, then open it) — only after single actions are reliable

**Done when:** a typical day's small Mac chores can be done from the chat box.

## Phase 5 — It knows your stuff (local RAG)
- [ ] Choose a CPU embedding model (small, ARM-friendly, runs offline) and record why in `docs/decisions.md`
- [ ] Indexer: user picks folders → chunk .md/.txt/.pdf/code → embed → SQLite (vectors + text + source path)
- [ ] Incremental re-index on file changes (mtime-based; no background daemon needed at first)
- [ ] Retrieval in chat: top-k chunks injected into the prompt, with **source citations** shown in the UI
- [ ] Toggle per chat: "use my files" on/off
- [ ] Mini eval: 20 questions about your own notes, scored with vs. without retrieval → `docs/evals.md`

**Done when:** "What did I write about X?" gets a correct answer with clickable sources.

## Phase 6 — Voice and selection (hotkey, menu bar and launch at login moved to Phase 3)
- [ ] Voice in: `whisper.cpp` (small/base model, CPU) push-to-talk
- [ ] Voice out: `piper` TTS, toggleable
- [ ] "Ask about selection": send selected text from any app (clipboard-based)

**Done when:** you can press a hotkey, speak a command, and have it done — offline.

## Phase 7 — Polish
- [ ] Better UI: Markdown-ish rendering (code blocks, bold), copy button on replies, dark mode
- [ ] Persistent conversations (local SQLite) with a sidebar to reopen them
- [ ] Benchmark script: `python -m bitnet_desk.bench` → tokens/s, first-token latency, peak RAM

## Phase 8 — Shareable app
- [ ] macOS `.app` bundle (py2app or similar) with the built server + model download on first run
- [ ] First-run wizard: checks CPU/RAM, downloads model, picks folders to index
- [ ] Signed/notarised build or clear instructions for Gatekeeper
- [ ] README written for non-developers; short demo video/GIF
- [ ] Write-up: architecture, benchmarks, evals, lessons learned

**Done when:** a friend can install it on their Mac without a terminal.

---

## Stretch ideas
- Swap in larger/newer 1-bit models as they're released; compare in `docs/benchmarks.md` and the action eval
- Speculative decoding or prompt caching tweaks for faster first token
- LoRA-style personalisation is out of scope for ternary weights for now — revisit if tooling appears

## Log
- 2026-10-14: v0 created — single-file Tkinter app, setup scripts, README, CLAUDE.md, roadmap.
- 2026-10-15: Phase 0 on M1: setup.sh pins BitNet `1876a3e`, uses Homebrew clang, CPU-only CMake settings; `BITNET_SERVER` + auto `--device none` in assistant.py; baseline benchmarks. Model output still broken on Apple Silicon for both backends — findings in `docs/setup-notes.md`.
- 2026-10-15: Fixed Apple Silicon output: pin BitNet `765741d` (pre-Nov-2025 i2_s layout matches the HF GGUF) with TL1 forced off; coherent replies at ~27 tok/s on M1. TQ2_0 looping confirmed not a prompt/stop-token issue. setup.sh cleans codegen files out of BitNet/.
- 2026-10-16: Phase 1: split `assistant.py` into `bitnet_desk/` (config, server, llm, ui, app); run with `python3.12 -m bitnet_desk`, `assistant.py` kept as a launcher. No behaviour change.
- 2026-10-16: System prompt now ends with the current local date, time and weekday, computed per request (`llm.prepare`). Note: changes the prompt prefix each minute, so long chats lose llama-server's prompt cache (~20 s re-eval at 2k tokens on M1).
- 2026-10-16: `fit_history` counts real tokens via `/tokenize` (cached per turn, chars/3 fallback); budget is `CTX − MAX_NEW_TOKENS`. Per-turn sums match whole-prompt counts on the real server.
- 2026-10-16: `config.toml` support (threads, ctx, max_tokens, temperature, system_prompt) via stdlib `tomllib`; env vars override it; bad lines are skipped and shown in the chat window.
- 2026-10-16: Time moved from the system prompt to the newest user message (prompt only, not saved history); system prompt keeps date + weekday. Old turns stay byte-identical, so a follow-up a minute later re-evaluates 44 tokens (0.5 s) instead of ~2k (20 s) on M1.
- 2026-10-16: Mock llama-server (/health, /tokenize, streaming /completion) and settings isolation moved to `tests/conftest.py`; tests split into test_llm/test_server/test_config; added a Stop-mid-stream test.
- 2026-10-16: Priorities changed: Actions (one permission system, fast path + model router) is now Phase 2, ahead of RAG and UI polish; privacy rule reworded to "model never sends data; only approved actions use the network". Design in `docs/actions-design.md`. Checked pinned server: `json_schema`, `grammar` and `n_probs` all work on `/completion`.
- 2026-10-16: Actions step 1: `bitnet_desk/actions/` — risk levels, frozen plans, permission gate (`auto_approve`, default open_app/open_path/now; network/shell always ask), private JSONL action log, fast path for open app / open file-folder / date-time, 40-case `evals/actions.jsonl` with fast-path scoring in pytest (9/9, no false triggers). Not wired into the UI yet.
- 2026-10-16: Actions step 2: chat box tries the fast path first (works while the model loads); auto-approved actions run and show a ✓/✗ line, others show an approval card with the exact command, risk badge and source — Run (⌘↩) / Deny (Esc); a new message or Clear denies an open card. README: Commands section.
- 2026-10-16: Try-out fixes: fast path strips polite wrappers (can you / could you / please / for me / thanks…); date/time questions in many phrasings answered from the clock ("what time is it today" had reached the model, which gave a wrong date); `help` lists real commands from the registry. New system prompt: says it's a Mac assistant whose actions are done by the app, lists the commands, forbids claiming it did things (it had "set a timer"). Eval now 65 cases; fast path 28/28, no false triggers.
- 2026-10-16: Actions step 3: start_project (`[projects]`; `open -a "Visual Studio Code"`, `code` CLI only if the app is missing; Terminal, configurable), open_url and download (network: always ask; https added to bare domains; no credentials-in-URL; ~/Downloads, never overwrites, size cap, quarantine flag, progress), find_files (mdfind; bounded name search when Spotlight returns nothing — it returns nothing for ~ on this Mac), run_shell (literal commands only, zsh in ~, 60 s). Clickable result paths go through the gate. Network-guard test. Fast path 39/39, no false triggers.
- 2026-10-16: Actions step 4: model router (grammar-constrained JSON, per-message enums so URLs/commands/paths can only be copied; confidence from token probs, < 0.7 → chat), grounding (invented → chat, inferred → ask), command-verb check, `-np 2` (+356 MB footprint), "Just chat" on model cards, `python -m bitnet_desk.actions eval`. Results (`docs/evals.md`): fast path 39/39 with no false triggers; model path 55/65 (85%), tool choice 87%, args 82%; pipeline 60/65 (92%); 2 unsafe picks, both shown as cards (none auto-run).
- 2026-10-16: "That was wrong" feedback: a ⚑ link under every reply/action and `/wrong [note]` save the message + what the app did to a private `misses.jsonl`; `python -m bitnet_desk.actions misses` asks for the right answer and appends eval cases (route worked out automatically). Projects bitnet-desk and tx-lead-engine added to the user's config; "start bitnet-desk" verified end to end. Spotlight on the dev Mac indexes only apps (findings in the session notes; nothing changed).
- 2026-10-17: Priorities: new Phase 3 "Reach for it all day" — hotkey + command box + menu bar + launch at login, then system/media tools, then routines (design: `docs/always-there-design.md`; Carbon hotkey verified permission-free inside Tk). RAG and the rest shift down one phase.
- 2026-10-17: Phase 3 step 1: ⌥Space (`[ui] hotkey`) toggles a command box from any app via Carbon `RegisterEventHotKey` (ctypes; no Accessibility/Input Monitoring); box and window share one conversation (`ui/transcript.py` views); auto actions hide the box, cards/answers/chat show in it. Menu-bar icon (Tk 9 `tk systray`), closing the window hides it, single-instance lock, `--background`, launch at login (LaunchAgent; `--login on|off|status`). macOS lets two apps hold one hotkey (both fire), so clashes with macOS shortcuts and ChatGPT's launcher are detected from their prefs and shown.
- 2026-10-17: Phase 3 step 2: system and media tools (`tools/system.py`): media (Spotify/Music, the one playing; pause never launches a player), playlists from `[playlists]`, volume, Do Not Disturb via two Shortcuts, screenshot (runs off the UI thread, box steps aside), lock screen and empty Trash (`always_ask`, fast path only). Defaults now auto-approve media/playlist/volume/dnd/screenshot. Fast path refuses clear commands it can't do ("Nothing is playing", unknown playlist, DND not set up) instead of passing them to chat. Model gets media/volume/playlist with copy-only player and level and action-word grounding (it had picked "volume up" for "set the volume to 30"). Planning never sends an Apple event, so no permission prompt appears before approval. Eval 102 cases: fast path 65/65, no false triggers; pipeline 96/102; 3 unsafe picks, all cards.
- 2026-10-17: Real-Mac e2e harness (`bitnet_desk/e2e.py`): drives the pipeline and the real app in-process (no synthetic input), reads effects back (volume, Spotify player state/track, PNG file, Terminal/VS Code window titles via AX, Focus via Control Center's menu bar, menu-bar icon via AX while pumping the event loop), restores everything (volume crash journal), never runs lock/empty Trash. Found and fixed: Esc gave focus to the wrong app (hide before activate; now `activateFromApplication:` first), lock screen fell back to display sleep (login.framework lives in the dyld cache), Spotify restore race (pause overtaken by play), Spotify relaunching itself after an update-on-quit. `AEDeterminePermissionToAutomateTarget` can block forever, so it runs with a timeout and an Apple-event probe as fallback.
- 2026-10-17: Phase 3 step 3: routines (`actions/routines.py`, `[routines.<name>]` with steps + aliases): fast-path steps planned and frozen at card time, one always-ask card lists every step's commands, steps run in order and are logged one by one, first failure stops it with ✓/✗/– per step; risk = highest step; no nesting. `--setup-shortcuts` writes, signs and opens the two DND Shortcuts (macOS requires the Add Shortcut click). User config: work-mode routine (start bitnet-desk, DND on; playlist step commented until a focus playlist is set).
