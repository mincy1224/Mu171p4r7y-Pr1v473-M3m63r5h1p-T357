#!/bin/bash
# Build and install emp-toolkit dependencies from source
# Usage: .github/install-deps.sh {emp-tool | emp-ot | emp-sh2pc}
set -euo pipefail

PKG="$1"
REPO="https://github.com/emp-toolkit/${PKG}.git"

# Pinned versions (update manually when upgrading; keep in sync with README)
declare -A REFS=(
    ["emp-tool"]="0.3.0"
    ["emp-ot"]="0.3.0"
    ["emp-sh2pc"]="0.3.0"
)

echo "Building $PKG (ref: ${REFS[$PKG]})..."
git clone --depth 1 --branch "${REFS[$PKG]}" "$REPO" "/tmp/$PKG" 2>/dev/null \
  || { git clone "$REPO" "/tmp/$PKG" && git -C "/tmp/$PKG" checkout "${REFS[$PKG]}"; }
cd "/tmp/$PKG"

# emp-tool HEAD is missing <array> in bit.h (breaks GCC 12+ on GitHub runner)
find . -name 'bit.h' -exec sed -i '1i#include <array>' {} \; || true

cmake -B build \
  -DCMAKE_C_COMPILER=clang-15 \
  -DCMAKE_CXX_COMPILER=clang++-15 \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX=/usr/local \
  -DCMAKE_POSITION_INDEPENDENT_CODE=ON

cmake --build build -j"$(nproc)"
sudo cmake --install build
rm -rf "/tmp/$PKG"
