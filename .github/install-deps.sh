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

# emp-tool HEAD is missing <array> in bit.h (breaks GCC 12+ on GitHub runner)
find . -name 'bit.h' -exec sed -i '1i#include <array>' {} \; || true

cmake -B build \
  -DCMAKE_C_COMPILER=clang-15 \
  -DCMAKE_CXX_COMPILER=clang++-15 \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$PREFIX" \
  -DCMAKE_POSITION_INDEPENDENT_CODE=ON

cmake --build build -j"$(nproc)"
# Only elevate when the prefix needs root (e.g. /usr/local).  The GitHub
# Actions cache must be able to restore this prefix as the unprivileged
# runner, so CI installs to $HOME/.local (user-writable, and already on
# CMakeLists.txt's CMAKE_PREFIX_PATH).
mkdir -p "$PREFIX" || true
if [[ -w "$PREFIX" ]]; then
    cmake --install build
else
    sudo cmake --install build
fi
rm -rf "/tmp/$PKG"
