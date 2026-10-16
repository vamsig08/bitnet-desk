# One-time setup for BitNet Desk (Windows).
# Run from a "Developer PowerShell for VS 2022" with the "Desktop development with C++" and
# "C++ Clang Compiler for Windows" + "MS-Build Support for LLVM (clang-cl)" components installed.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

foreach ($c in "git", "cmake", "clang", "python") {
  if (-not (Get-Command $c -ErrorAction SilentlyContinue)) { throw "Missing: $c (see README -> Prerequisites)" }
}

if (-not (Test-Path BitNet)) { git clone --recursive https://github.com/microsoft/BitNet.git }
Set-Location BitNet

python -m venv .venv
. .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -U "huggingface_hub[cli]"

$ModelDir = "models/BitNet-b1.58-2B-4T"
if (-not (Test-Path "$ModelDir/ggml-model-i2_s.gguf")) {
  Write-Host "Downloading BitNet b1.58 2B4T (~1.2 GB)..."
  if (Get-Command hf -ErrorAction SilentlyContinue) {
    hf download microsoft/BitNet-b1.58-2B-4T-gguf --local-dir $ModelDir
  } else {
    huggingface-cli download microsoft/BitNet-b1.58-2B-4T-gguf --local-dir $ModelDir
  }
}

python setup_env.py -md $ModelDir -q i2_s

if (-not (Test-Path build/bin/Release/llama-server.exe) -and -not (Test-Path build/bin/llama-server.exe)) {
  cmake --build build --config Release --target llama-server
}

Write-Host "`nDone. Start the assistant with:  python assistant.py"
