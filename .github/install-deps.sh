#!/bin/bash
# Build and install emp-toolkit dependencies from source
# Usage: .github/install-deps.sh {emp-tool | emp-ot | emp-sh2pc}
set -euo pipefail

PKG="$1"
REPO="https://github.com/emp-toolkit/${PKG}.git"

echo "Building $PKG ..."
git clone --depth 1 "$REPO" "/tmp/$PKG"
cd "/tmp/$PKG"

cmake -B build \
  -DCMAKE_C_COMPILER=clang-15 \
  -DCMAKE_CXX_COMPILER=clang++-15 \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX=/usr/local \
  -DBUILD_TESTING=OFF

cmake --build build -j"$(nproc)"
sudo cmake --install build
rm -rf "/tmp/$PKG"
