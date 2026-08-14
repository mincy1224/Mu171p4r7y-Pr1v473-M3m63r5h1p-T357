#!/bin/bash
# Build and install emp-toolkit dependencies from source
# Usage: .github/install-deps.sh {emp-tool | emp-ot | emp-sh2pc}
set -euo pipefail

PKG="$1"
REPO="https://github.com/emp-toolkit/${PKG}.git"
PREFIX="${EMP_INSTALL_PREFIX:-/usr/local}"

echo "Building $PKG into $PREFIX ..."
git clone --depth 1 "$REPO" "/tmp/$PKG"
cd "/tmp/$PKG"

find . -name 'bit.h' -exec sed -i '1i#include <array>' {} \; || true

cmake -B build \
  -DCMAKE_C_COMPILER=clang-15 \
  -DCMAKE_CXX_COMPILER=clang++-15 \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$PREFIX" \
  -DCMAKE_POSITION_INDEPENDENT_CODE=ON

cmake --build build -j"$(nproc)"
mkdir -p "$PREFIX" || true
if [[ -w "$PREFIX" ]]; then
    cmake --install build
else
    sudo cmake --install build
fi
rm -rf "/tmp/$PKG"
