# Benchmarks

Speed and memory only — **neither backend below gives usable answers on Apple Silicon yet**
(see `docs/setup-notes.md`). Re-run once the model produces coherent output.

## Machine

| | |
|---|---|
| Mac | MacBook Pro 13" (M1, 2020) — `MacBookPro17,1` |
| Chip | Apple M1: 4 performance + 4 efficiency cores |
| RAM | 16 GB |
| macOS | 26.6.2 |
| Power | AC adapter, battery 100 % |
| GPU | not used (`-ngl 0 --device none`; Metal not initialised, checked in server log) |

## Baseline — 2026-10-15

**Backend:** Homebrew llama.cpp 0.6.0 (build 11429, commit `d81235049`) `llama-server`
**Model:** `Synapticode/bitnet-b1.58-2B-4T-tq2_0-gguf` @ `f965b42`, `bitnet-2b4t-tq2_0.gguf` (TQ2_0, 1.2 GB)
**Output quality:** ❌ incoherent: starts on-topic, then loops and never emits `<|eot_id|>`

Method: start `llama-server` the way `assistant.py` does (`-c 4096 -ngl 0 --device none --host
127.0.0.1`), one warm-up request, then 3 prompts (~30 prompt tokens, 128 new tokens, greedy,
`cache_prompt: false`). Averages over the 3 prompts, one run each, so treat as rough.

| Threads | Load time | First token | Prompt eval | Generation | Peak RSS |
|---|---|---|---|---|---|
| 4 (app default) | 11.6 s (cold) | 0.69 s | 58.0 tok/s | **15.7 tok/s** | 1.65 GB |
| 8 | 1.2 s (warm cache) | 1.38 s | 30.6 tok/s | 11.6 tok/s | 1.69 GB |

- **4 threads (= performance cores) is fastest.** 8 threads pulls in the efficiency cores and is ~25 % slower.
- Load time depends mostly on the disk cache: 11.6 s for the first load after download, ~1 s when the file is cached.
- Through the app's own code path (temp 0.6, top_p 0.9, repeat_penalty 1.1, 4 threads), generation
  ran at 13.6–14.9 tok/s on long (768-token) replies and 18.3 tok/s on a short one.

## Reference: bitnet.cpp (output was garbage, numbers for comparison only)

| Build | Generation | Note |
|---|---|---|
| BitNet `1876a3e`, Homebrew clang 23, `llama-cli -t 4` | 18.7 tok/s | output `!!!!…` |
| BitNet HEAD `0b341e5` (Jul 2026 fork), static-lib workaround | 3.5 tok/s | output `@@@@…` |

## How to reproduce

The script is not in the repo yet (Phase 1 adds `python -m bitnet_desk.bench`). Equivalent manual check:

```bash
llama-server -m models/bitnet-2b4t-tq2_0/bitnet-2b4t-tq2_0.gguf -c 4096 -t 4 -ngl 0 \
  --device none --host 127.0.0.1 --port 8080
# POST /completion with "stream": true; the final event's "timings" has prompt/predicted tok/s
```
