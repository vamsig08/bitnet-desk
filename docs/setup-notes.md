# Setup notes — Apple Silicon (Phase 0, 2026-10-15)

## Status

| | Result |
|---|---|
| `./setup.sh` on M1 / macOS 26.6 | ✅ completes from clean in ~2 min (model download aside) |
| bitnet.cpp `765741d` + official HF i2_s GGUF | ✅ **coherent answers, stop on their own, ~27 tok/s** |
| App (`python3.12 assistant.py`) | ✅ opens, starts bitnet.cpp's server on 127.0.0.1 only, ready in ~1 s |
| Plan B: mainline llama.cpp + TQ2_0 | ⚠️ runs, but loops; kept as an option via `BITNET_SERVER`, not recommended |
| Offline check (Wi-Fi off) | not tested yet; nothing in the app talks to anything but 127.0.0.1 |

## What `setup.sh` does on macOS

1. **Pins BitNet to `765741d80bda4d3069e367bf0062134891232cce`** (2025-05-21; llama.cpp submodule
   `Eddie-Wang1120/llama.cpp` @ `40ed0f29`). Later commits don't work with the official GGUF on ARM.
   See the root cause below. Override with `BITNET_REF=<sha> ./setup.sh`. A change of ref,
   compiler or `cmake/bitnet-build.cmake` wipes `BitNet/build` (marker: `build/.bitnet-ref`).
2. **`huggingface_hub[cli]>=0.34,<1.0`** instead of `pip install -U`. Upgrading pulled 2.2.0,
   which breaks BitNet's pinned `transformers`/`tokenizers`.
3. **Uses Homebrew LLVM** (`brew install llvm`) when installed, and refuses clang < 18. `setup_env.py`
   passes a bare `-DCMAKE_C_COMPILER=clang`, so `CC=`/`CXX=` have no effect; setup.sh puts
   `$(brew --prefix llvm)/bin` first on `PATH`.
4. **`cmake/bitnet-build.cmake`**, passed via the `CMAKE_TOOLCHAIN_FILE` env var because `setup_env.py`
   runs CMake itself:
   - `BITNET_ARM_TL1=OFF` (forced). This commit's `setup_env.py` turns TL1 on for arm64, and the
     generated TL1 lookup-table kernel (`src/ggml-bitnet-lut.cpp`) never finished compiling: 20+ min
     with Apple clang 17, 30+ min with LLVM 23. The i2_s model doesn't use TL1. Its kernels are in
     `src/ggml-bitnet-mad.cpp`.
   - `GGML_METAL=OFF`. llama.cpp enables Metal on macOS by default, which conflicts with the CPU-only rule.
   - `-Wno-elaborated-enum-base`: clang ≥ 21 errors on the macOS SDK's own `CF_ENUM` headers.
   - `-include locale -include chrono` (C++): llama.cpp uses `std::wstring_convert` / `std::chrono`
     without including the headers (`<locale>` only on Windows). Older libc++ pulled them in transitively.
5. Deletes `include/bitnet-lut-kernels.h` and `include/kernel_config.ini` after building. `setup_env.py`'s
   codegen writes them into the checkout and they're only needed while compiling, so `BitNet/` stays
   a clean checkout. Nothing under `BitNet/` is edited.

## Root cause of the garbage output (`!!!!` / `@@@@`)

The official GGUF (`microsoft/BitNet-b1.58-2B-4T-gguf`, `ggml-model-i2_s.gguf`, uploaded 2025-04-23,
sha256 `4221b252fdd5fd25e15847adfeb5ee88886506ba50b8a34548374492884c2162`) packs i2_s weights in
**128-wide blocks**. Commit `112f853` (2025-11-19) made the block size platform-dependent in
`src/ggml-bitnet-mad.cpp`:

```c
#if defined(__AVX__) || ...   #define QK_I2_S 128   // x86
#elif defined(__ARM_NEON)     #define QK_I2_S 64    // ARM
```

So every BitNet commit since Nov 2025 reads the HF file with the wrong layout on ARM. HEAD
(2026-07-15) additionally moved llama.cpp to a fork (`isHuangXin/llama.cpp`) that fails to link on
macOS (`ggml-base` → `quantize_i2_s` in `ggml-cpu`; Apple's `ld` rejects undefined symbols in a dylib).

**Ruled out along the way:** a corrupted download (sha256 matches HF), Metal, the compiler (Apple clang 17
vs LLVM 23), our harness (upstream `llama-cli` showed the same garbage), and `NEON = 0` in
`system_info` (only a runtime report; `__ARM_NEON` is defined at compile time).

## Things tried, in order

| # | Build | Result |
|---|---|---|
| 1 | HEAD `0b341e5`, Apple clang 17 | link error `_quantize_i2_s` undefined |
| 2 | HEAD + static libs | output `@@@@`, 3.5 tok/s |
| 3 | `1876a3e` (Jan 2026), Apple clang 17, ± Metal | output `!!!!` |
| 4 | `765741d`, Apple clang 17, TL1 on | TL1 kernel still compiling after 20 min |
| 5 | `1876a3e`, LLVM 23 + flags | output `!!!!` (18.7 tok/s) |
| 6 | `765741d`, LLVM 23, TL1 on | TL1 kernel still compiling after 30 min |
| 7 | Homebrew llama.cpp 0.6.0 + TQ2_0 | runs; loops (below) |
| 8 | **`765741d`, LLVM 23, TL1 off** | ✅ **coherent, 27 tok/s; builds in ~2 min** |

## Plan B notes (mainline llama.cpp + TQ2_0), kept for reference

- `brew install llama.cpp` → 0.6.0 (build 11429, `d81235049`) + `ggml` 0.27.0.
- Model `Synapticode/bitnet-b1.58-2B-4T-tq2_0-gguf` @ `f965b4212a598c7a7d05c815135e214e2388a444`,
  sha256 `9f8e1097…02cbb6` (verified), in `models/bitnet-2b4t-tq2_0/` (git-ignored). Third-party conversion.
- Run with `BITNET_SERVER=/opt/homebrew/bin/llama-server BITNET_MODEL=<gguf> python3.12 assistant.py`.
  The app adds `--device none` because this server offers it; otherwise newer llama.cpp can
  offload big batches to Metal even with `-ngl 0`.
- **Loops, and it isn't a prompt or stop-token problem:**
  - The GGUF's built-in chat template renders byte-for-byte the same prompt as `assistant.py`.
  - `<|eot_id|>` is one special token (128009) and also the EOS. When the model emits it the
    server stops (`stop_type=eos`); in the looping replies it is never emitted.
  - `/v1/chat/completions` gave 3/5 replies that stop, but with nonsense ("1 is the capital of France?").
  - `/completion` with temperature 0 and repeat_penalty 1.1 or 1.2: 2/5 stop; the rest loop.
  - The model card says its authors fixed the 2B4T FFN activation (relu² vs SiLU) in their own
    llama.cpp fork on 2026-08-23. If mainline lacks that fix it would explain this (unverified).

## Installed on this Mac during Phase 0

- `brew install llvm` (23.1.3, keg-only: not on your PATH; setup.sh uses it)
- `brew install llama.cpp` (0.6.0) + `ggml` (0.27.0). Only needed for plan B; safe to `brew uninstall`.
- Project `.venv/` with `pytest` (git-ignored)
- Both brew installs needed `HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK=1`. Without it, Homebrew aborts
  because the existing taps `mongodb/brew` and `humansignal/tap` aren't marked trusted. Trusting
  or untapping them is the owner's call.

## Follow-ups

- Report upstream (microsoft/BitNet): the HF i2_s GGUF doesn't match `QK_I2_S 64` on ARM since `112f853`.
- Moving to a newer BitNet on ARM would need an i2_s GGUF converted with the new layout (bf16
  checkpoint ~5 GB plus intermediates), which needs more free disk than this Mac has now.
- x86 is untested with this pin. The pinned `setup_env.py` still turns TL2 on for x86 (upstream's
  default at that commit).
