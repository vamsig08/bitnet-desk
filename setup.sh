#!/usr/bin/env bash
# One-time setup for BitNet Desk (macOS / Linux). Needs: git, cmake >= 3.22, clang >= 18, python3 >= 3.9.
set -euo pipefail
cd "$(dirname "$0")"

for c in git cmake clang; do
  command -v "$c" >/dev/null 2>&1 || { echo "Missing: $c (see README → Prerequisites)"; exit 1; }
done

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

if [ ! -d BitNet ]; then
  git clone --recursive https://github.com/microsoft/BitNet.git
fi
cd BitNet

"$PYTHON" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -U "huggingface_hub[cli]"

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
python setup_env.py -md "$MODEL_DIR" -q i2_s

if [ ! -x build/bin/llama-server ]; then
  echo "Building llama-server target…"
  cmake --build build --config Release --target llama-server -j
fi

echo
echo "Done. Start the assistant with:  $PYTHON assistant.py"
