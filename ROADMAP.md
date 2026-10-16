# BitNet Desk — Roadmap

Vision: a private assistant on your MacBook that runs a 1-bit LLM on the CPU, knows your files,
takes safe actions with your approval, and is always a hotkey away. Nothing leaves the machine.

Each phase ends with something usable. Tick boxes as items land; keep the log at the bottom.

---

## Phase 0 — Get v0 running on the Mac
- [x] `./setup.sh` completes on Apple Silicon (fix script issues found along the way) — pinned BitNet `765741d`, see `docs/setup-notes.md`
- [x] `python3 assistant.py` opens, loads the model, answers a question
- [x] Record baseline: Mac model, chip, RAM, threads, tokens/s, load time, RAM use → `docs/benchmarks.md` 

**Done when:** you can chat with the model offline (Wi-Fi off). — everything above works; the Wi-Fi-off check is still to do by hand.

## Phase 1 — Solid foundation
- [x] Split `assistant.py` into the `bitnet_desk/` package (config, server, llm, ui, app) with no behaviour change
- [x] Current local date and weekday in the system prompt, local time on the newest message only — computed on every request, never stored in history
- [x] `tests/conftest.py` mock llama-server fixture; tests for prompt format, streaming, stop, context fitting
- [x] Real token counting via `/tokenize` instead of the chars/3 estimate
- [x] Config file at `~/Library/Application Support/BitNetDesk/config.toml` (threads, ctx, temp, system prompt)
- [ ] Better UI: Markdown-ish rendering (code blocks, bold), copy button on replies, dark mode
- [ ] Persistent conversations (local SQLite) with a sidebar to reopen them
- [ ] Benchmark script: `python -m bitnet_desk.bench` → tokens/s, first-token latency, peak RAM

**Done when:** it's a pleasant daily chat app with tests passing and saved history.

## Phase 2 — It knows your stuff (local RAG)
- [ ] Choose a CPU embedding model (small, ARM-friendly, runs offline) and record why in `docs/decisions.md`
- [ ] Indexer: user picks folders → chunk .md/.txt/.pdf/code → embed → SQLite (vectors + text + source path)
- [ ] Incremental re-index on file changes (mtime-based; no background daemon needed at first)
- [ ] Retrieval in chat: top-k chunks injected into the prompt, with **source citations** shown in the UI
- [ ] Toggle per chat: "use my files" on/off
- [ ] Mini eval: 20 questions about your own notes, scored with vs. without retrieval → `docs/evals.md`

**Done when:** "What did I write about X?" gets a correct answer with clickable sources.

## Phase 3 — It can do things (tools, with approval)
- [ ] Tool registry: name, description, JSON args schema, `read_only` flag
- [ ] Simple tool-calling protocol the 2B model can follow reliably (constrained JSON / grammar if supported by the server)
- [ ] Read-only tools: search files, open & summarise a file, list recent files, current date/time, calculator
- [ ] Write tools (always confirm, show a diff/preview): create note, append to file, move file to folder
- [ ] Action log the user can review
- [ ] Eval: tool-selection accuracy on a fixed set of prompts

**Done when:** "Find my notes on X and draft a summary into summary.md" works, with one approval click.

## Phase 4 — Always there
- [ ] Global hotkey (e.g. ⌥Space) to show/hide a compact window
- [ ] Menu-bar icon; launch at login (optional)
- [ ] Voice in: `whisper.cpp` (small/base model, CPU) push-to-talk
- [ ] Voice out: `piper` TTS, toggleable
- [ ] "Ask about selection": send selected text from any app (clipboard-based)

**Done when:** you can press a hotkey, speak a question, and hear the answer — offline.

## Phase 5 — Shareable app
- [ ] macOS `.app` bundle (py2app or similar) with the built server + model download on first run
- [ ] First-run wizard: checks CPU/RAM, downloads model, picks folders to index
- [ ] Signed/notarised build or clear instructions for Gatekeeper
- [ ] README written for non-developers; short demo video/GIF
- [ ] Write-up: architecture, benchmarks, evals, lessons learned

**Done when:** a friend can install it on their Mac without a terminal.

---

## Stretch ideas
- Swap in larger/newer 1-bit models as they're released; compare in `docs/benchmarks.md`
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
