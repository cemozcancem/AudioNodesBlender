#!/bin/bash

# One-command release: build engine -> run tests -> package -> verify.
# Usage: ./scripts/release.sh <version>     e.g. ./scripts/release.sh 4.4.0
#
# Must run on macOS (the engine needs Apple's SDK and code signing).
# The build's POST_BUILD step already copies the .app into
# blender_addon/bin/darwin_arm64/, merging over the committed Frameworks
# dylibs, so no manual sync is needed here.

set -e

VERSION=${1:?"usage: ./scripts/release.sh <version>"}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

if [ "$(uname -s)" != "Darwin" ]; then
    echo "ERROR: release builds must run on macOS (Apple SDK + codesign)."
    exit 1
fi

echo "==> [1/4] Building engine (Release)..."
"$SCRIPT_DIR/build_engine.sh" Release

ENGINE_BIN="$PROJECT_DIR/blender_addon/bin/darwin_arm64/AudioNodesEngine.app/Contents/MacOS/Audio Nodes Engine"
if [ ! -f "$ENGINE_BIN" ]; then
    echo "ERROR: engine binary missing after build: $ENGINE_BIN"
    exit 1
fi
# Guard against packaging a stale binary (POST_BUILD copy failed, etc.)
if [ -n "$(find "$ENGINE_BIN" -mmin +10 2>/dev/null)" ]; then
    echo "WARNING: engine binary is older than 10 minutes - was the build a no-op?"
fi

echo "==> [2/4] Running engine tests..."
if command -v pytest >/dev/null 2>&1; then
    (cd "$PROJECT_DIR/tests" && pytest -x -q) || {
        echo "ERROR: tests failed - not packaging."
        exit 1
    }
else
    echo "    pytest not found - skipping (pip install pytest pyzmq)"
fi

echo "==> [3/4] Packaging v${VERSION}..."
"$SCRIPT_DIR/package_addon.sh" "$VERSION"

echo "==> [4/4] Verifying package..."
ZIP="$PROJECT_DIR/dist/audio_nodes-${VERSION}.zip"
[ -f "$ZIP" ] || { echo "ERROR: $ZIP missing"; exit 1; }
unzip -l "$ZIP" | grep -q "Frameworks/libzmq" || {
    echo "ERROR: bundled libzmq missing from zip"; exit 1; }
unzip -l "$ZIP" | grep -q "MacOS/Audio Nodes Engine" || {
    echo "ERROR: engine binary missing from zip"; exit 1; }

echo ""
echo "Release ready: $ZIP"
echo "Local Blender add-ons install was refreshed by package_addon.sh."
echo "Reload the add-on in Blender (or restart) to pick up changes."
