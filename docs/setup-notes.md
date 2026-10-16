# Setup notes — Apple Silicon (Phase 0, 2026-10-15)

## Status

| | Result |
|---|---|
| `./setup.sh` on M1 / macOS 26.6 | ✅ completes (after the fixes below) |
| bitnet.cpp + official HF i2_s GGUF | ❌ builds and runs, but outputs garbage on Apple Silicon |
| Plan B: mainline llama.cpp + TQ2_0 GGUF | ⚠️ runs CPU-only via the app, but answers are incoherent |
| App (`python3.12 assistant.py`) | ✅ opens, starts the server on 127.0.0.1 only, loads the model |

**So you can't chat usefully with the model on this Mac yet.** The plumbing works; the model
numerics don't.

## What `setup.sh` now does differently

1. **Pins BitNet to `1876a3e8895f5f88f976760fdbc588c1eafa2d48`** (2026-01-27; llama.cpp submodule
   `Eddie-Wang1120/llama.cpp` @ `1f86f058`). On 2026-07-15 upstream HEAD switched the submodule to a
   personal fork (`isHuangXin/llama.cpp`, branch `release-bitnet-embedding-0.6b-270m`). That fork fails
   to link on macOS: `ggml-base` calls `quantize_i2_s`, which lives in `ggml-cpu`, and Apple's `ld`
   rejects undefined symbols in a dylib. Even with a static-lib workaround it produced garbage at 3.5 tok/s.
   You can override the pin with `BITNET_REF=<sha> ./setup.sh`. Changing the ref, the compiler or
   `cmake/bitnet-build.cmake` wipes `BitNet/build` (marker: `build/.bitnet-ref`).
2. **`huggingface_hub[cli]>=0.34,<1.0`** instead of `pip install -U`. Upgrading pulled 2.2.0,
   which breaks BitNet's pinned `transformers`/`tokenizers`.
3. **Uses Homebrew LLVM on macOS** when it's installed, and refuses to run with clang < 18. `setup_env.py`
   passes a bare `-DCMAKE_C_COMPILER=clang`, so `CC=`/`CXX=` have no effect. setup.sh puts
   `$(brew --prefix llvm)/bin` first on `PATH` instead.
4. **`cmake/bitnet-build.cmake`**, passed via the `CMAKE_TOOLCHAIN_FILE` env var because
   `setup_env.py` runs CMake itself:
   - `GGML_METAL=OFF`: llama.cpp enables Metal on macOS by default, which conflicts with the CPU-only rule.
   - `-Wno-elaborated-enum-base`: clang ≥ 21 errors on the macOS SDK's own `CF_ENUM` headers.
   - `-include locale -include chrono` (C++ only): the pinned llama.cpp uses `std::wstring_convert`
     and `std::chrono` without including the headers (`<locale>` is only included on Windows). Older
     libc++ pulled them in transitively.
5. Prints a warning at the end on Apple Silicon that the bitnet.cpp output is broken.

Nothing under `BitNet/` is edited. It is a clean checkout of the pinned commit.

## Root cause of the bitnet.cpp garbage

The official GGUF (`microsoft/BitNet-b1.58-2B-4T-gguf`, `ggml-model-i2_s.gguf`, uploaded 2025-04-23,
sha256 `4221b252…2162`, which matches our download) packs i2_s weights in **128-wide blocks**. Commit
`112f853` (2025-11-19, "I2S kernels for weight & activation parallel") made the block size
platform-dependent in `src/ggml-bitnet-mad.cpp`:

```c
#if defined(__AVX__) || ...   #define QK_I2_S 128   // x86
#elif defined(__ARM_NEON)     #define QK_I2_S 64    // ARM
```

So every BitNet commit since Nov 2025 reads the HF file with the wrong layout on ARM. In the same
period (`2fed9af`, 2026-01-22) upstream switched ARM builds from `BITNET_ARM_TL1=ON` to `OFF`.

**Ruled out:** a corrupted download (sha256 matches HF), Metal (garbage with Metal off too), the
compiler (Apple clang 17 and Homebrew clang 23 behave the same), our harness (upstream `llama-cli`
also prints `!!!!`), and `NEON = 0` in `system_info` (only a runtime report; `__ARM_NEON` and
`__ARM_FEATURE_DOTPROD` are defined at compile time).

## Things tried, in order

| # | Build | Result |
|---|---|---|
| 1 | HEAD `0b341e5`, Apple clang 17 | link error `_quantize_i2_s` undefined (macOS only) |
| 2 | HEAD + `BUILD_SHARED_LIBS=OFF` | builds; output `@@@@`, 3.5 tok/s |
| 3 | `1876a3e`, Apple clang 17 (± Metal) | builds; output `!!!!` |
| 4 | `765741d` (May 2025, `QK_I2_S 128`), Apple clang 17, TL1 on | `ggml-bitnet-lut.cpp` still compiling after 20 min; abandoned |
| 5 | `1876a3e`, Homebrew clang 23 + fixes above | builds; output `!!!!` (18.7 tok/s) |
| 6 | `765741d`, clang 23, TL1 on (upstream config of that era) | `ggml-bitnet-lut.cpp` still compiling after 30 min; abandoned (time-boxed) |
| 7 | Plan B: Homebrew llama.cpp + TQ2_0 | runs; incoherent (below) |

Untested idea: `765741d` (or `404980e`, same submodule) with `BITNET_ARM_TL1=OFF` forced. That has
the old 128-block i2_s kernels and skips the TL1 kernel file that hangs the compiler.

## Plan B — mainline llama.cpp + TQ2_0

- `brew install llama.cpp` → 0.6.0 (build 11429, `d81235049`), plus dependency `ggml` 0.27.0.
- Model: `Synapticode/bitnet-b1.58-2B-4T-tq2_0-gguf` @ revision `f965b4212a598c7a7d05c815135e214e2388a444`,
  `bitnet-2b4t-tq2_0.gguf`, sha256 `9f8e1097502528a0d80d885c603ea7ee3e4d214a6685356e39baaf697c02cbb6`
  (verified), stored in `models/bitnet-2b4t-tq2_0/` (git-ignored). Third-party conversion.
- Run:
  ```bash
  BITNET_SERVER=/opt/homebrew/bin/llama-server \
  BITNET_MODEL=$PWD/models/bitnet-2b4t-tq2_0/bitnet-2b4t-tq2_0.gguf \
  python3.12 assistant.py
  ```
  The app starts `llama-server … -ngl 0 --host 127.0.0.1 --device none`. It adds `--device none`
  automatically when the server's `--help` offers it, because newer llama.cpp can otherwise offload
  large batches to Metal even with `-ngl 0`.
- **Result:** 5 test prompts through the app's code path. Replies begin plausibly ("The capital of
  France is Paris.", "…Sam, I enjoy the hike") and then loop on repeated sentences. They never emit
  `<|eot_id|>` (4/5 hit the 768-token cap), echo the system prompt, and get the sky question wrong.
  Not usable. The model card notes its authors fixed the 2B4T FFN activation (relu² vs SiLU) in their
  own llama.cpp fork on 2026-08-23. If mainline lacks that fix it would explain this, but **that is
  unverified**.

## Installed on this Mac during Phase 0

- `brew install llvm` (23.1.3, keg-only: not on your PATH, used only by setup.sh)
- `brew install llama.cpp` (0.6.0) + `ggml` (0.27.0)
- Project `.venv/` with `pytest` (git-ignored); run tests with `.venv/bin/python -m pytest -q`
- Both brew installs needed `HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK=1`. Without it, Homebrew aborts
  because the existing taps `mongodb/brew` and `humansignal/tap` aren't marked trusted. Trusting
  or untapping them is your call (`brew trust …` / `brew untap …`).
- Disk: about 8 GB free after all this.

## Options from here

1. **Try `765741d` with TL1 forced off.** Cheap, no new downloads, keeps the official runtime.
2. **Convert the GGUF locally on ARM** with current bitnet.cpp, so the layout matches `QK_I2_S 64`.
   Needs the bf16 checkpoint (~5 GB) plus an f32 intermediate, which is more disk than is free now.
3. **Build Synapticode's llama.cpp fork** (tag `v0.1.0-relu2`) for the TQ2_0 file. That adds a
   third-party fork, so pin it and review it first.
4. **Report upstream:** the `QK_I2_S` / HF GGUF mismatch on ARM (microsoft/BitNet) and the possible
   relu² issue (llama.cpp).
