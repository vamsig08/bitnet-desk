# Build settings for bitnet.cpp, passed by setup.sh via the CMAKE_TOOLCHAIN_FILE environment
# variable (setup_env.py runs CMake itself, so we can't add -D flags directly).

# BitNet Desk is CPU-only: don't build ggml's Metal (GPU) backend, which llama.cpp enables on macOS.
set(GGML_METAL OFF CACHE BOOL "BitNet Desk is CPU-only" FORCE)

if(APPLE)
  # Homebrew clang >= 21 errors on the macOS SDK's own CF_ENUM headers; Apple clang allows them.
  string(APPEND CMAKE_C_FLAGS_INIT " -Wno-elaborated-enum-base")
  # Pinned llama.cpp relies on headers that older libc++ included transitively: common.cpp uses
  # std::wstring_convert (<locale>, included only on Windows), imatrix.cpp uses std::chrono.
  string(APPEND CMAKE_CXX_FLAGS_INIT " -Wno-elaborated-enum-base -include locale -include chrono")
endif()

# We only run the i2_s model, whose kernels (src/ggml-bitnet-mad.cpp) don't need TL1. The pinned
# setup_env.py turns TL1 on for arm64, and its generated LUT kernel takes 30+ min to compile (or
# never finishes) with Apple clang 17 and LLVM 23. Override it.
set(BITNET_ARM_TL1 OFF CACHE BOOL "i2_s only; TL1 LUT kernels hang the compiler" FORCE)
