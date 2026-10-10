#!/usr/bin/env bash
# Fresh-install validation.
#
# Wipes any previously-installed `audio_nodes` addon and any previously
# auto-installed pyzmq, packages a fresh zip, then drives Blender through
# the exact end-user flow:
#   addon_install(zip)  →  addon_enable("audio_nodes")  →  use it.
#
# This catches issues that source-mode tests miss:
#   - Wrong package name in hardcoded preferences lookups.
#   - Dependency installer can't find a writable target during a real
#     addon_enable (no sys.path tricks).
#   - Engine binary missing from the packaged zip.
#
# Usage:
#   scripts/run_fresh_install_test.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

DEFAULT_BLENDER="$HOME/.cache/blender-test/Blender.app/Contents/MacOS/Blender"
[[ -x "$DEFAULT_BLENDER" ]] || DEFAULT_BLENDER="/Applications/Blender.app/Contents/MacOS/Blender"
BLENDER="${BLENDER:-$DEFAULT_BLENDER}"

if [[ ! -x "$BLENDER" ]]; then
    echo "ERROR: Blender not found at $BLENDER" >&2
    exit 2
fi

VERSION="99.0.0-freshinstall"
ZIP="$REPO_ROOT/dist/audio_nodes-${VERSION}.zip"

# Detect Blender version dir for cleaning up addon/modules.
BLENDER_VER="$("$BLENDER" --version 2>/dev/null | head -1 | awk '{print $2}' | cut -d. -f1-2)"
USER_BLENDER_DIR="$HOME/Library/Application Support/Blender/${BLENDER_VER}"
USER_ADDONS="$USER_BLENDER_DIR/scripts/addons"
USER_MODULES="$USER_BLENDER_DIR/scripts/modules"

echo ">>> Blender version dir: $BLENDER_VER"
echo ">>> wiping previously-installed addon at $USER_ADDONS/audio_nodes"
rm -rf "$USER_ADDONS/audio_nodes"

echo ">>> wiping previously-installed pyzmq from $USER_MODULES"
rm -rf "$USER_MODULES/zmq" "$USER_MODULES"/pyzmq* 2>/dev/null || true

# Belt-and-suspenders: also wipe pyzmq from the bundle's site-packages so
# the addon's installer is the only thing that can satisfy the import.
BLENDER_SITE="$(dirname "$BLENDER")/../Resources/${BLENDER_VER}/python/lib/python3.11/site-packages"
if [[ -d "$BLENDER_SITE" ]]; then
    rm -rf "$BLENDER_SITE/zmq" "$BLENDER_SITE"/pyzmq* 2>/dev/null || true
fi

echo ">>> packaging addon zip"
"$REPO_ROOT/scripts/package_addon.sh" "$VERSION" >/dev/null
[[ -f "$ZIP" ]] || { echo "ERROR: zip not produced at $ZIP" >&2; exit 2; }

# Stop any orphan engine before the test binds 5555.
pkill -f "Audio Nodes Engine" 2>/dev/null || true

set +e
"$BLENDER" \
    --background \
    --factory-startup \
    --python "$REPO_ROOT/tests/blender/run_fresh_install.py" \
    -- "$ZIP"
EXIT="$?"
set -e

# Cleanup: remove the installed addon and the high-version zip. Leave any
# pyzmq the addon installed alone — that's expected residue of an end-user
# enable.
rm -rf "$USER_ADDONS/audio_nodes"
rm -f "$ZIP"

pkill -f "Audio Nodes Engine" 2>/dev/null || true

if [[ "$EXIT" -ne 0 ]]; then
    echo "FAIL fresh-install (exit $EXIT)"
    exit "$EXIT"
fi

echo ""
echo "============================================================"
echo "fresh-install validation PASSED"
echo "============================================================"
