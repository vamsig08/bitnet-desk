# BitNet Desk — project guide for Claude Code

## What this is
A **private, zero-GPU personal assistant for macOS** (Apple Silicon first; Windows/Linux kept working).
It runs Microsoft's **BitNet b1.58 2B4T** — a natively 1.58-bit (ternary −1/0/+1) LLM — on the CPU
through the official `bitnet.cpp` runtime. The goal is to grow it from a local chat window into an
assistant that knows the user's files, can take safe actions, and is always one hotkey away.
`ROADMAP.md` has the phases and the current status.

## Non-negotiable rules
1. **Nothing leaves the machine at runtime.** The only network traffic allowed while the app runs is
   to `127.0.0.1`. Servers bind to `127.0.0.1`, never `0.0.0.0`. No telemetry, no update checks, no
   cloud APIs, no "optional" online fallbacks. Network is allowed only in setup scripts (clone, model
   download, pip install).
2. **No GPU, ever.** Everything must run acceptably on a CPU-only MacBook. Pass `-ngl 0` to llama-server.
3. **Side effects need confirmation.** Any tool that writes, moves or deletes files, runs commands, or
   touches other apps must show the user exactly what it will do and wait for approval. Read-only
   tools can run without asking.
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
  memory/        (phase 2) local index of user folders: chunking, CPU embeddings, SQLite store
  tools/         (phase 3) tool registry; each tool declares read_only: bool
  ui/            Tkinter app (may move to a native wrapper in phase 5)
  app.py         entry point: python -m bitnet_desk
tests/           pytest; uses a mock llama-server, never the real model
BitNet/          upstream microsoft/BitNet clone + model weights (git-ignored, built by setup.sh)
```
Right now everything lives in the single file `assistant.py` (v0). Phase 1 splits it into the package
above without changing behaviour.

## Key technical facts
- Model file: `BitNet/models/BitNet-b1.58-2B-4T/ggml-model-i2_s.gguf` (from HF
  `microsoft/BitNet-b1.58-2B-4T-gguf`). The `i2_s` format needs **bitnet.cpp**; mainline llama.cpp
  and Ollama can't run it.
- Server binary: `BitNet/build/bin/llama-server`. API used: `GET /health`, `POST /completion` with
  `"stream": true` (SSE lines `data: {...}`; `stop: true` on the final event, which carries `timings`).
- Chat format (no chat template applied by us server-side):
  `System: …<|eot_id|>User: …<|eot_id|>Assistant: ` and stop on `<|eot_id|>`, `<|end_of_text|>`.
- Context default 4096 tokens; `fit_history` trims oldest turns using a ~3 chars/token estimate.
  Replace it with real token counts via the server's `/tokenize` endpoint when convenient.
- Threads: default is half the logical cores; on Apple Silicon, performance-core count is usually best.

## macOS setup gotchas
- Tools: `xcode-select --install`, `brew install cmake python@3.12 python-tk@3.12`. Use Python 3.12 (BitNet pins torch 2.2 → Python ≤ 3.12). This Mac also has an old python.org Python 3.7 in /usr/local/bin — never use it; run the app with `python3.12`.
- `bitnet.cpp` wants clang ≥ 18 (Apple clang 17 hangs/miscompiles). `setup.sh` uses Homebrew LLVM
  automatically if installed (`brew install llvm`); `CC=`/`CXX=` don't work because `setup_env.py`
  hardcodes `clang`. Extra CMake settings live in `cmake/bitnet-build.cmake`.
- Apple Silicon status, pinned BitNet commit and known model-output problems: `docs/setup-notes.md`.
- Tkinter from Homebrew Python needs the matching `python-tk@3.x` formula.
- If `setup_env.py` fails, read its log under `BitNet/logs/` before changing anything.

## Commands
```bash
./setup.sh                      # one-time: clone, download model, build (needs internet)
python3.12 assistant.py         # run v0 (after phase 1: python3 -m bitnet_desk)
python3.12 -m venv .venv && .venv/bin/pip install pytest   # one-time test setup
.venv/bin/python -m pytest -q   # tests (mock server; no model needed)
BITNET_THREADS=8 python3 assistant.py
```

## How to work in this repo
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
