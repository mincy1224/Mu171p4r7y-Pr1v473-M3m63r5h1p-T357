#!/bin/bash
# Build and install emp-toolkit dependencies from source
# Usage: .github/install-deps.sh {emp-tool | emp-ot | emp-sh2pc}
set -euo pipefail

PKG="$1"
REPO="https://github.com/emp-toolkit/${PKG}.git"

echo "Building $PKG ..."
git clone --depth 1 "$REPO" "/tmp/$PKG"
cd "/tmp/$PKG"

# emp-tool HEAD is missing <array> in bit.h (breaks GCC 12+ on GitHub runner)
find . -name 'bit.h' -exec sed -i '1i#include <array>' {} \; || true

cmake -B build \
  -DCMAKE_C_COMPILER=clang-15 \
  -DCMAKE_CXX_COMPILER=clang++-15 \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX=/usr/local

cmake --build build -j"$(nproc)"
sudo cmake --install build
rm -rf "/tmp/$PKG"
