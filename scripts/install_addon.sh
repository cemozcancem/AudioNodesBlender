#!/bin/bash

# Install a packaged Audio Nodes zip into the local Blender add-ons folder,
# overwriting any previous audio_nodes install.
#
# Usage:
#   ./scripts/install_addon.sh [version|zip-path]
#   ./scripts/install_addon.sh 4.8.0
#   ./scripts/install_addon.sh dist/audio_nodes-4.8.0.zip
#
# Destination (first match wins):
#   1. $BLENDER_ADDONS_DIR
#   2. ~/Library/Application Support/Blender/<BLENDER_VERSION>/scripts/addons
#      (BLENDER_VERSION defaults to 5.1)
#   3. Newest Blender version folder that has scripts/addons/
#
# Set SKIP_BLENDER_INSTALL=1 to no-op (used by package_addon.sh callers).

set -e

if [ "${SKIP_BLENDER_INSTALL:-0}" = "1" ]; then
    echo "Skipping Blender install (SKIP_BLENDER_INSTALL=1)"
    exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DIST_DIR="$PROJECT_DIR/dist"
ARG=${1:-}

resolve_addons_dir() {
    if [ -n "${BLENDER_ADDONS_DIR:-}" ]; then
        echo "$BLENDER_ADDONS_DIR"
        return
    fi

    local support="$HOME/Library/Application Support/Blender"
    local preferred="${BLENDER_VERSION:-5.1}"
    local preferred_dir="$support/$preferred/scripts/addons"
    if [ -d "$preferred_dir" ]; then
        echo "$preferred_dir"
        return
    fi

    # Fall back to the newest Blender version that has an addons folder
    local newest=""
    if [ -d "$support" ]; then
        newest=$(ls -1 "$support" 2>/dev/null | sort -V | while read -r ver; do
            if [ -d "$support/$ver/scripts/addons" ]; then
                echo "$ver"
            fi
        done | tail -1)
    fi

    if [ -n "$newest" ]; then
        echo "$support/$newest/scripts/addons"
        return
    fi

    # Create the preferred path if nothing exists yet
    mkdir -p "$preferred_dir"
    echo "$preferred_dir"
}

# This machine's platform suffix, as package_addon.sh writes it.
# Installing a Windows zip on a Mac would leave no runnable engine, so the
# host's zip is always preferred over a bare/older one.
host_suffix() {
    case "$(uname -s)" in
        Darwin) echo "-macos-arm64" ;;
        *)      echo "" ;;
    esac
}

resolve_zip() {
    local arg="$1"
    local suffix
    suffix="$(host_suffix)"

    if [ -z "$arg" ]; then
        # Latest zip for THIS platform, else any latest (pre-split zips had
        # no suffix and carried both engines).
        local latest
        latest=$(ls -1 "$DIST_DIR"/audio_nodes-*"${suffix}".zip 2>/dev/null | sort -V | tail -1 || true)
        if [ -z "$latest" ]; then
            latest=$(ls -1 "$DIST_DIR"/audio_nodes-*.zip 2>/dev/null \
                     | grep -v -- "-windows-x64.zip" | sort -V | tail -1 || true)
        fi
        if [ -z "$latest" ]; then
            echo "Error: no installable audio_nodes zip found in $DIST_DIR" >&2
            echo "Run ./scripts/package_addon.sh <version> first." >&2
            exit 1
        fi
        echo "$latest"
        return
    fi

    if [ -f "$arg" ]; then
        echo "$(cd "$(dirname "$arg")" && pwd)/$(basename "$arg")"
        return
    fi

    # A bare version resolves to this platform's zip first.
    if [ -f "$DIST_DIR/audio_nodes-${arg}${suffix}.zip" ]; then
        echo "$DIST_DIR/audio_nodes-${arg}${suffix}.zip"
        return
    fi

    if [ -f "$DIST_DIR/audio_nodes-${arg}.zip" ]; then
        echo "$DIST_DIR/audio_nodes-${arg}.zip"
        return
    fi

    if [ -f "$PROJECT_DIR/$arg" ]; then
        echo "$PROJECT_DIR/$arg"
        return
    fi

    echo "Error: zip not found for '$arg'" >&2
    exit 1
}

ADDONS_DIR="$(resolve_addons_dir)"
ZIP="$(resolve_zip "$ARG")"

if [ ! -f "$ZIP" ]; then
    echo "Error: zip missing: $ZIP" >&2
    exit 1
fi

mkdir -p "$ADDONS_DIR"

echo "Installing Audio Nodes into Blender add-ons..."
echo "  zip:    $ZIP"
echo "  target: $ADDONS_DIR/audio_nodes"

# Full replace so renamed/removed files cannot linger
rm -rf "$ADDONS_DIR/audio_nodes"
unzip -q -o "$ZIP" -d "$ADDONS_DIR"

if [ ! -f "$ADDONS_DIR/audio_nodes/__init__.py" ]; then
    echo "Error: install failed — audio_nodes/__init__.py missing after unzip" >&2
    exit 1
fi

VERSION_LINE=$(grep -E '"version"' "$ADDONS_DIR/audio_nodes/__init__.py" | head -1 || true)
ENGINE="$ADDONS_DIR/audio_nodes/bin/darwin_arm64/AudioNodesEngine.app/Contents/MacOS/Audio Nodes Engine"

echo "Installed successfully."
[ -n "$VERSION_LINE" ] && echo "  $VERSION_LINE"
if [ -f "$ENGINE" ]; then
    echo "  engine: $(ls -lh "$ENGINE" | awk '{print $5}') binary present"
fi
echo ""
echo "Reload the add-on in Blender (or restart Blender) to pick up changes."
