#!/usr/bin/env bash
# Headless add-on test driver.
#
# Boots Blender 4.5 with --background, enables the live `blender_addon`
# package, runs `tests/blender/run_in_blender.py`, and captures the exit code.
# The addon spawns and tears down its own engine the same way it would for an
# end user — no special test-only side launch.
#
# Usage:
#   scripts/run_blender_tests.sh             # full suite
#   scripts/run_blender_tests.sh --quick     # addon plumbing only — no engine
#
# Override Blender via BLENDER env var:
#   BLENDER=/Applications/Blender4.5.app/Contents/MacOS/Blender scripts/run_blender_tests.sh

set -euo pipefail

# The test Blender runs its engine on its own port so it never collides with
# (or cleans up) an engine belonging to a Blender you have open.
export AUDIONODES_TEST_PORT="${AUDIONODES_TEST_PORT:-5599}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Prefer a writable copy outside /Applications so the addon's plist patcher
# can re-sign without tripping macOS App Management. If the user has a clean
# Blender in /Applications and they're OK with "App Management" prompts that's
# fine too — set BLENDER=… to override.
DEFAULT_BLENDER="$HOME/.cache/blender-test/Blender.app/Contents/MacOS/Blender"
[[ -x "$DEFAULT_BLENDER" ]] || DEFAULT_BLENDER="/Applications/Blender.app/Contents/MacOS/Blender"
BLENDER="${BLENDER:-$DEFAULT_BLENDER}"

if [[ ! -x "$BLENDER" ]]; then
    echo "ERROR: Blender not found at $BLENDER" >&2
    echo "Set BLENDER=/path/to/Blender" >&2
    exit 2
fi

ENGINE_BIN="$REPO_ROOT/blender_addon/bin/darwin_arm64/AudioNodesEngine.app/Contents/MacOS/Audio Nodes Engine"
if [[ ! -x "$ENGINE_BIN" ]]; then
    echo "WARNING: engine binary missing — engine-integration tests will fail" >&2
    echo "  expected: $ENGINE_BIN" >&2
    echo "  build with: $REPO_ROOT/scripts/build_engine.sh Release" >&2
fi

# Stop an orphan engine left on the TEST port by a previous run. Scoped to
# that port so an Audio Nodes engine in your own Blender (5555) is untouched.
pkill -f "Engine.*--zmq-port ${AUDIONODES_TEST_PORT}( |$)" 2>/dev/null || true

cd "$REPO_ROOT"

set +e
"$BLENDER" \
    --background \
    --factory-startup \
    --python "$REPO_ROOT/tests/blender/run_in_blender.py" \
    -- "$@"
EXIT="$?"
set -e

# Belt-and-suspenders cleanup in case the addon left an engine running.
pkill -f "Engine.*--zmq-port ${AUDIONODES_TEST_PORT}( |$)" 2>/dev/null || true

exit "$EXIT"
