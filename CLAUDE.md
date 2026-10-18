# BitNet Desk — project guide for Claude Code

## What this is
A **private, zero-GPU personal assistant for macOS** (Apple Silicon first; Windows/Linux kept working).
It runs Microsoft's **BitNet b1.58 2B4T** — a natively 1.58-bit (ternary −1/0/+1) LLM — on the CPU
through the official `bitnet.cpp` runtime. The goal is to grow it from a local chat window into an
assistant that knows the user's files, can take safe actions, and is always one hotkey away.
`ROADMAP.md` has the phases and the current status.

## Guiding principle
The long-term goal is a Jarvis-style assistant that controls the Mac. The strategy: **be exceptionally
good at a small number of useful local tasks first**, all behind one consistent permission system,
then add capabilities over time. A new tool lands only when it's reliable (covered by the action eval)
and goes through the same permission gate as every other tool. Prefer depth over breadth: fewer
actions that work every time beat many that work sometimes. UI polish and saved conversations wait.

## Non-negotiable rules
1. **The model never sends the user's data anywhere.** The only network traffic allowed at runtime is
   an action the user explicitly asked for and approved (e.g. a download, opening a URL). No telemetry,
   no update checks, no cloud APIs, no "optional" online fallbacks, and no background network calls
   of any kind. The llama-server binds to `127.0.0.1`, never `0.0.0.0`. Setup scripts may use the
   network (clone, model download, pip install).
2. **No GPU, ever.** Everything must run acceptably on a CPU-only MacBook. Pass `-ngl 0` to llama-server.
3. **One permission system for every action.** Each tool declares a risk level: read-only, changes
   things, network, or shell. Read-only tools run immediately; everything else shows the user the
   exact command/effect and waits for approval. Every action (approved, denied or automatic) goes
   in the local action log. No tool bypasses this, and the model can't lower a risk level.
4. **Dependencies are a cost.** Prefer the standard library. Add a package only when it clearly earns
   its place, it is pure-Python or has macOS arm64 wheels, it doesn't phone home, and it is pinned in
   `requirements.txt` with a one-line reason in the PR/commit.
5. **Small model, honest design.** The model is 2.4B params: weak on facts and long reasoning, decent at
   reading supplied text. Prefer retrieval and tools over hoping the model "knows". Never present
   model output as verified fact in the UI.
6. **The model layer stays swappable.** All model access goes through one client module, so a future
   1-bit model (or a different GGUF) can be dropped in by config.

## Architecture (target — see ROADMAP for what exists today)
```
bitnet_desk/
  config.py      env vars + ~/Library/Application Support/BitNetDesk/config.toml
  server.py      start/stop/health of bitnet.cpp llama-server (subprocess, localhost only)
  llm.py         prompt formatting, streaming /completion client, context fitting
  actions/       (phase 2) tool registry, fast path, model router, permissions, action log, eval
                 — design in docs/actions-design.md; system/media tools and routines (phase 3)
  login.py, shortcuts.py   launch at login (LaunchAgent); the two Do Not Disturb Shortcuts
  e2e.py         real-Mac end-to-end harness (no synthetic input; restores state) → docs/e2e-report.md
  memory/        (phase 5) local index of user folders: chunking, CPU embeddings, SQLite store
  ui/            Tkinter app (may move to a native wrapper in phase 8)
  app.py         entry point: python -m bitnet_desk
tests/           pytest; uses a mock llama-server, never the real model
BitNet/          upstream microsoft/BitNet clone + model weights (git-ignored, built by setup.sh)
```
`config`, `server`, `llm`, `ui` and `app` exist; `assistant.py` is a thin launcher kept for compatibility.

## Key technical facts
- Model file: `BitNet/models/BitNet-b1.58-2B-4T/ggml-model-i2_s.gguf` (from HF
  `microsoft/BitNet-b1.58-2B-4T-gguf`). The `i2_s` format needs **bitnet.cpp**; mainline llama.cpp
  and Ollama can't run it.
- Server binary: `BitNet/build/bin/llama-server`. API used: `GET /health`, `POST /completion` with
  `"stream": true` (SSE lines `data: {...}`; `stop: true` on the final event, which carries `timings`).
- Chat format (no chat template applied by us server-side):
  `System: …<|eot_id|>User: …<|eot_id|>Assistant: ` and stop on `<|eot_id|>`, `<|end_of_text|>`.
- Context default 4096 tokens; `fit_history` trims oldest turns so prompt + `MAX_NEW_TOKENS` fits,
  counting with the server's `POST /tokenize` (`{"content": …}` → `{"tokens": […]}`, no BOS added,
  special tokens parsed) and falling back to a ~3 chars/token estimate if the server can't answer.
- Threads: default is half the logical cores; on Apple Silicon, performance-core count is usually best.

## macOS setup gotchas
- Tools: `xcode-select --install`, `brew install cmake python@3.12 python-tk@3.12`. Use Python 3.12 (BitNet pins torch 2.2 → Python ≤ 3.12). This Mac also has an old python.org Python 3.7 in /usr/local/bin — never use it; run the app with `python3.12`.
- `bitnet.cpp` wants clang ≥ 18 (Apple clang 17 hangs/miscompiles). `setup.sh` uses Homebrew LLVM
  automatically if installed (`brew install llvm`); `CC=`/`CXX=` don't work because `setup_env.py`
  hardcodes `clang`. Extra CMake settings live in `cmake/bitnet-build.cmake`.
- BitNet is pinned to `765741d` (May 2025) on purpose (newer commits break the HF i2_s GGUF on ARM).
  Why, plus build flags and things tried: `docs/setup-notes.md`. Don't bump the pin without re-testing output.
- Tkinter from Homebrew Python needs the matching `python-tk@3.x` formula.
- If `setup_env.py` fails, read its log under `BitNet/logs/` before changing anything.

## Commands
```bash
./setup.sh                      # one-time: clone, download model, build (needs internet)
python3.12 -m bitnet_desk       # run the app (python3.12 assistant.py also works)
python3.12 -m venv .venv && .venv/bin/pip install pytest   # one-time test setup
.venv/bin/python -m pytest -q   # tests (mock server; no model needed)
python3.12 -m bitnet_desk.e2e   # real-Mac end-to-end checks (needs the model server running)
python3.12 -m bitnet_desk.e2e --permissions   # which macOS permissions are granted (no prompts)
BITNET_THREADS=8 python3.12 -m bitnet_desk
```

## How to work in this repo
- **Test everything end to end yourself; the user only wants to see the finished product.** After unit
  tests, run the real-Mac harness (`python3.12 -m bitnet_desk.e2e`), fix what fails, and report the
  results (`docs/e2e-report.md`). Don't hand testing back to the user.
- **Never send synthetic keystrokes or clicks** to the Mac (System Events `keystroke`/`key code`,
  CGEventPost, AX presses): the user may be using it. Drive the app by calling its own methods, and
  read effects through state queries (volume settings, Spotify's player state, window titles).
- **Say right before a macOS permission prompt (or any dialog needing a click) appears**, so the user
  can click Allow. `python3.12 -m bitnet_desk.e2e --permissions` shows what's granted without prompting.
- The harness must restore whatever it changes (volume, Spotify, windows, Do Not Disturb, login item)
  and must never run lock screen or empty Trash; it only checks their plans.
- Read `ROADMAP.md` first and work on **one unchecked item at a time**. Propose a short plan before
  large changes; keep commits small and focused.
- Every behaviour change gets a test. Tests must not need the real model or the network — use the
  mock llama-server fixture in `tests/conftest.py`.
- After finishing an item: run the tests, tick the box in `ROADMAP.md`, and add a one-line note under
  "Log" there (date, what changed).
- When you need to try the real model, start it via the app or `server.py`; never leave stray
  llama-server processes running.
- Don't edit anything under `BitNet/` (upstream code). Work around upstream issues in our code or
  document a patch in `docs/`.
- Keep the README accurate for a non-expert Mac user.
