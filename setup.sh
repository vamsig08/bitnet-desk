#!/usr/bin/env bash
# One-time setup for BitNet Desk (macOS / Linux). Needs: git, cmake >= 3.22, clang >= 18, python3 >= 3.9.
set -euo pipefail
cd "$(dirname "$0")"

# Apple clang 17 (Xcode 26) hangs or miscompiles bitnet.cpp's kernels; it needs clang >= 18.
# setup_env.py calls plain `clang`, so put Homebrew LLVM first on PATH when it's installed.
if [ "$(uname -s)" = "Darwin" ] && command -v brew >/dev/null 2>&1 \
   && [ -x "$(brew --prefix llvm)/bin/clang" ]; then
  export PATH="$(brew --prefix llvm)/bin:$PATH"
fi

for c in git cmake clang; do
  command -v "$c" >/dev/null 2>&1 || { echo "Missing: $c (see README → Prerequisites)"; exit 1; }
done
CLANG_MAJOR=$(clang -dM -E -x c /dev/null | awk '/__clang_major__/ {print $3}')
if [ "$CLANG_MAJOR" -lt 18 ]; then
  echo "bitnet.cpp needs clang >= 18; $(command -v clang) is $CLANG_MAJOR. On macOS: brew install llvm"
  exit 1
fi
echo "Using clang $CLANG_MAJOR at $(command -v clang)"

# BitNet's requirements pin torch 2.2, which supports Python 3.9–3.12 only.
# Pick a compatible interpreter (override with PYTHON=/path/to/python).
if [ -z "${PYTHON:-}" ]; then
  for cand in python3.12 python3.11 python3.10 python3.9 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11; do
    if command -v "$cand" >/dev/null 2>&1; then PYTHON="$cand"; break; fi
  done
fi
if [ -z "${PYTHON:-}" ]; then
  echo "Need Python 3.9–3.12. On macOS: brew install python@3.12 python-tk@3.12"; exit 1
fi
"$PYTHON" -c 'import sys; v=sys.version_info[:2]; assert (3,9)<=v<=(3,12), v' 2>/dev/null \
  || { echo "$PYTHON is not Python 3.9–3.12. Set PYTHON=… to a compatible one."; exit 1; }
echo "Using $("$PYTHON" --version) at $(command -v "$PYTHON")"

# Pin upstream to 765741d (2025-05-21). Later commits break the official HF i2_s GGUF on ARM:
# since 112f853 (2025-11) ARM kernels expect 64-wide i2_s blocks but the file has 128-wide ones,
# and HEAD (2026-07) moved llama.cpp to a fork that doesn't link on macOS. See docs/setup-notes.md.
BITNET_REF="${BITNET_REF:-765741d80bda4d3069e367bf0062134891232cce}"
if [ ! -d BitNet ]; then
  git clone https://github.com/microsoft/BitNet.git
fi
cd BitNet
if [ "$(git rev-parse HEAD)" != "$(git rev-parse "$BITNET_REF^{commit}" 2>/dev/null)" ]; then
  git fetch origin
  # --force discards include/ files that setup_env.py's codegen regenerates anyway.
  git checkout --force --detach "$BITNET_REF"
fi
git submodule sync --recursive
git submodule update --init --recursive --force
# A build/ from a different commit, compiler or config has a stale CMake cache; start clean.
BUILD_ID="$BITNET_REF clang$CLANG_MAJOR $(cksum < ../cmake/bitnet-build.cmake | cut -d" " -f1)"
if [ -d build ] && [ "$(cat build/.bitnet-ref 2>/dev/null)" != "$BUILD_ID" ]; then
  rm -rf build
fi

"$PYTHON" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
# Keep huggingface_hub < 1.0: BitNet's pinned transformers/tokenizers reject 1.x/2.x.
pip install "huggingface_hub[cli]>=0.34,<1.0"

MODEL_DIR=models/BitNet-b1.58-2B-4T
if [ ! -f "$MODEL_DIR/ggml-model-i2_s.gguf" ]; then
  echo "Downloading BitNet b1.58 2B4T (~1.2 GB)…"
  if command -v hf >/dev/null 2>&1; then
    hf download microsoft/BitNet-b1.58-2B-4T-gguf --local-dir "$MODEL_DIR"
  else
    huggingface-cli download microsoft/BitNet-b1.58-2B-4T-gguf --local-dir "$MODEL_DIR"
  fi
fi

echo "Building bitnet.cpp with the i2_s ternary kernel…"
# setup_env.py runs CMake itself; pass our settings (CPU only, macOS SDK flags) via a toolchain file.
CMAKE_TOOLCHAIN_FILE="$(cd .. && pwd)/cmake/bitnet-build.cmake" python setup_env.py -md "$MODEL_DIR" -q i2_s

echo "$BUILD_ID" > build/.bitnet-ref

if [ ! -x build/bin/llama-server ]; then
  echo "Building llama-server target…"
  cmake --build build --config Release --target llama-server -j
fi

# setup_env.py's codegen writes these into the checkout; they are only needed while compiling.
rm -f include/bitnet-lut-kernels.h include/kernel_config.ini

echo
echo "Done. Start the assistant with:  $PYTHON assistant.py"
