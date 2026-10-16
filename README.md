# BitNet Desk — a zero-GPU, fully private desktop assistant

A small desktop chat app that runs **Microsoft's BitNet b1.58 2B4T** — a natively 1.58-bit
(ternary: −1, 0, +1) LLM — on your **CPU only**, via the official `bitnet.cpp` runtime.
The model server listens on `127.0.0.1` only; nothing you type leaves your machine.

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
- The app makes no network calls besides `localhost`. After setup, you can block the
  BitNet folder in your firewall or set `HF_HUB_OFFLINE=1` to be extra sure.
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
