"""Settings, read once at import from environment variables."""
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent      # project root
BITNET_DIR = Path(os.environ.get("BITNET_DIR", HERE / "BitNet"))
MODEL = Path(os.environ.get(
    "BITNET_MODEL",
    BITNET_DIR / "models" / "BitNet-b1.58-2B-4T" / "ggml-model-i2_s.gguf"))
# Any llama-server binary, e.g. mainline llama.cpp's for a TQ2_0 model. Default: the bitnet.cpp build.
SERVER_BIN = os.environ.get("BITNET_SERVER")
HOST = "127.0.0.1"                      # never 0.0.0.0 — keeps the model local-only
PORT = int(os.environ.get("BITNET_PORT", "8080"))
BASE = f"http://{HOST}:{PORT}"
CTX = int(os.environ.get("BITNET_CTX", "4096"))
THREADS = int(os.environ.get("BITNET_THREADS", str(max(1, (os.cpu_count() or 4) // 2))))
MAX_NEW_TOKENS = int(os.environ.get("BITNET_MAX_TOKENS", "768"))
TEMPERATURE = float(os.environ.get("BITNET_TEMP", "0.6"))

SYSTEM_PROMPT = os.environ.get("BITNET_SYSTEM", (
    "You are a helpful, concise assistant running entirely offline on the user's computer. "
    "Answer clearly. If you are not sure about a fact, say so instead of guessing."))

MAX_FILE_CHARS = 6000
SERVER_LOG = HERE / "server.log"
