# Benchmarks

## Machine

| | |
|---|---|
| Mac | MacBook Pro 13" (M1, 2020) — `MacBookPro17,1` |
| Chip | Apple M1: 4 performance + 4 efficiency cores |
| RAM | 16 GB |
| macOS | 26.6.2 |
| Power | AC adapter, battery 100 % |
| GPU | not used (`-ngl 0`; Metal not compiled in) |

## Baseline — 2026-10-15

**Backend:** bitnet.cpp, BitNet `765741d` (TL1 off, i2_s kernels), built with Homebrew clang 23.1.3
**Model:** `microsoft/BitNet-b1.58-2B-4T-gguf` `ggml-model-i2_s.gguf` (1.19 GB)
**Output quality:** ✅ coherent, and replies end on their own

Method: start `llama-server` the way `assistant.py` does (`-c 4096 -ngl 0 --host 127.0.0.1`), one
warm-up request, then 3 prompts (~30 prompt tokens, up to 128 new tokens, greedy, `cache_prompt: false`).
Averages over the 3 prompts, one run each, so treat as rough.

| Threads | Load time | First token | Prompt eval | Generation | Peak RSS |
|---|---|---|---|---|---|
| 4 (app default) | 2.0 s | 0.31 s | 98.1 tok/s | **27.2 tok/s** | 1.51 GB |
| 8 | 0.5 s | 0.28 s | 107.2 tok/s | 15.8 tok/s | 1.51 GB |

- **4 threads (= performance cores) is the right default.** 8 threads adds the efficiency cores and
  generation drops ~40 %; prompt eval gains slightly.
- Load times are with the model already in the OS file cache. The first load after a reboot will be slower (not measured).
- Through the app's own code path (temp 0.6, top_p 0.9, repeat_penalty 1.1, 4 threads), 5 chat
  prompts generated at 27.0–30.9 tok/s, replies of 8–102 tokens, all ending on `<|eot_id|>`.

## Other backends tried (same machine, same day)

| Backend | Generation (4 threads) | Peak RSS | Output |
|---|---|---|---|
| Homebrew llama.cpp 0.6.0 + TQ2_0 GGUF (`--device none`) | 15.7 tok/s | 1.65 GB | loops, rarely stops |
| bitnet.cpp `1876a3e` (Jan 2026) + i2_s GGUF | 18.7 tok/s | — | garbage (`!!!!`) |
| bitnet.cpp HEAD `0b341e5` (Jul 2026) + i2_s GGUF | 3.5 tok/s | — | garbage (`@@@@`) |

## How to reproduce

The script is not in the repo yet (Phase 1 adds `python -m bitnet_desk.bench`). Equivalent manual check:

```bash
BitNet/build/bin/llama-server -m BitNet/models/BitNet-b1.58-2B-4T/ggml-model-i2_s.gguf \
  -c 4096 -t 4 -ngl 0 --host 127.0.0.1 --port 8080
# POST /completion with "stream": true; the final event's "timings" has prompt/predicted tok/s
```
