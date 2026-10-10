#!/bin/bash

# Package Audio Nodes add-on as a .zip file for Blender installation
#
# Usage:
#   ./scripts/package_addon.sh <version> [--platform mac|win|linux|both|combined]
#
#   --platform mac        only dist/audio_nodes-<v>-macos-arm64.zip
#   --platform win        only dist/audio_nodes-<v>-windows-x64.zip
#   --platform linux      only dist/audio_nodes-<v>-linux-x64.zip
#   --platform both       one zip per platform that has an engine (default)
#   --platform combined   the old single audio_nodes-<v>.zip with every engine
#
# One zip per platform is the default because the engine is half the
# download: a Mac user has no use for AudioNodesEngine.exe and vice versa.
# A platform zip is only written when that platform's engine actually
# exists - we never ship a zip whose engine is missing, because the add-on
# would install and register cleanly and then simply never make a sound.

set -e

VERSION=""
PLATFORM="both"
while [ $# -gt 0 ]; do
    case "$1" in
        --platform) PLATFORM="$2"; shift 2 ;;
        --platform=*) PLATFORM="${1#*=}"; shift ;;
        -*) echo "Unknown option: $1" >&2; exit 2 ;;
        *) VERSION="$1"; shift ;;
    esac
done
VERSION=${VERSION:-"1.0.0"}

case "$PLATFORM" in
    mac|win|linux|both|combined) ;;
    *) echo "Error: --platform must be mac, win, linux, both or combined" >&2; exit 2 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
ADDON_DIR="$PROJECT_DIR/blender_addon"
OUTPUT_DIR="$PROJECT_DIR/dist"
PACKAGE_NAME="audio_nodes-${VERSION}"

MAC_APP="$ADDON_DIR/bin/darwin_arm64/AudioNodesEngine.app"
WIN_EXE="$ADDON_DIR/bin/win_x64/AudioNodesEngine.exe"
LINUX_BIN="$ADDON_DIR/bin/linux_x64/AudioNodesEngine"
HAVE_MAC=0; [ -f "$MAC_APP/Contents/MacOS/Audio Nodes Engine" ] && HAVE_MAC=1
HAVE_WIN=0; [ -f "$WIN_EXE" ] && HAVE_WIN=1
HAVE_LINUX=0; [ -f "$LINUX_BIN" ] && HAVE_LINUX=1

echo "Packaging Audio Nodes add-on v${VERSION} (platform: ${PLATFORM})..."
echo "  macOS engine:   $([ $HAVE_MAC = 1 ] && echo present || echo MISSING)"
echo "  Windows engine: $([ $HAVE_WIN = 1 ] && echo present || echo MISSING)"
echo "  Linux engine:   $([ $HAVE_LINUX = 1 ] && echo present || echo MISSING)"

# Check if engine binaries exist
if [ $HAVE_MAC = 0 ] && [ $HAVE_WIN = 0 ] && [ $HAVE_LINUX = 0 ]; then
    echo "Error: No engine binaries found!"
    echo "Please build the engine first:"
    echo "  ./scripts/build_engine.sh          (macOS)"
    echo "  scripts\\build_engine.bat          (Windows)"
    echo "  ./scripts/build_engine.sh          (Linux)"
    exit 1
fi

if [ "$PLATFORM" = "mac" ] && [ $HAVE_MAC = 0 ]; then
    echo "Error: --platform mac but there is no macOS engine at:"
    echo "       $MAC_APP"
    exit 1
fi
if [ "$PLATFORM" = "win" ] && [ $HAVE_WIN = 0 ]; then
    echo "Error: --platform win but there is no Windows engine at:"
    echo "       $WIN_EXE"
    echo "       Build it on Windows (scripts\\build_engine.bat) or let CI"
    echo "       produce it - see WINDOWS_BUILD.md."
    exit 1
fi
if [ "$PLATFORM" = "linux" ] && [ $HAVE_LINUX = 0 ]; then
    echo "Error: --platform linux but there is no Linux engine at:"
    echo "       $LINUX_BIN"
    exit 1
fi

# Create output directory
mkdir -p "$OUTPUT_DIR"

# Create temporary directory for packaging
TEMP_DIR=$(mktemp -d)
PACKAGE_DIR="$TEMP_DIR/audio_nodes"

echo "Creating package structure..."

# Copy addon files (excluding __pycache__ and .pyc files)
mkdir -p "$PACKAGE_DIR"
rsync -av --exclude='__pycache__' --exclude='*.pyc' --exclude='.DS_Store' \
    "$ADDON_DIR/" "$PACKAGE_DIR/"

# Remove .gitkeep files
find "$PACKAGE_DIR" -name ".gitkeep" -delete

# Bundle libzmq into the macOS engine app so customers don't need Homebrew.
#
# The zip must ALWAYS be fresh-Mac-ready. Two layers guarantee that:
#   1. On macOS this script relinks the engine to the bundled dylibs
#      (otool/install_name_tool/codesign below).
#   2. Regardless of where the zip was built, engine_manager.py launches
#      the engine with DYLD_LIBRARY_PATH pointed at Contents/Frameworks,
#      so the bundled (already relinked + signed) dylibs are used even if
#      the relink step could not run. The dylibs committed in
#      blender_addon/bin/.../Frameworks must therefore stay in the repo.
MACOS_ENGINE="$PACKAGE_DIR/bin/darwin_arm64/AudioNodesEngine.app/Contents/MacOS/Audio Nodes Engine"
FRAMEWORKS_DIR="$PACKAGE_DIR/bin/darwin_arm64/AudioNodesEngine.app/Contents/Frameworks"

if [ -f "$MACOS_ENGINE" ]; then
    if [ ! -f "$FRAMEWORKS_DIR/libzmq.5.dylib" ]; then
        echo "ERROR: bundled libzmq missing from Frameworks - this zip would"
        echo "NOT run on a fresh Mac. Re-run this script on macOS to bundle it."
        exit 1
    fi
    if ! command -v otool >/dev/null 2>&1; then
        echo "Note: otool unavailable (non-macOS host) - skipping relink step."
        echo "Bundled Frameworks dylibs + the DYLD_LIBRARY_PATH set by"
        echo "engine_manager.py keep the package fresh-Mac-ready."
    fi
fi

ZMQ_SRC=$(otool -L "$MACOS_ENGINE" 2>/dev/null | awk '/libzmq/{print $1}')

if [ -n "$ZMQ_SRC" ] && [ -f "$ZMQ_SRC" ]; then
    echo "Bundling $ZMQ_SRC and its dependencies..."
    mkdir -p "$FRAMEWORKS_DIR"

    # Bundle every non-system Homebrew dependency recursively
    bundle_dep() {
        local lib="$1"
        local filename=$(basename "$lib")
        [ -f "$FRAMEWORKS_DIR/$filename" ] && return  # already bundled
        [ ! -f "$lib" ] && return                      # not a real path
        echo "  Bundling $filename"
        cp "$lib" "$FRAMEWORKS_DIR/"
        # Fix each non-system dep of this lib to use @loader_path
        otool -L "$lib" | awk 'NR>1{print $1}' | grep '/opt/homebrew' | while read dep; do
            install_name_tool -change "$dep" "@loader_path/$(basename $dep)" "$FRAMEWORKS_DIR/$filename" 2>/dev/null || true
            bundle_dep "$dep"
        done
        codesign -s - --force "$FRAMEWORKS_DIR/$filename" 2>/dev/null || true
    }

    ZMQ_FILENAME=$(basename "$ZMQ_SRC")
    bundle_dep "$ZMQ_SRC"

    # Fix the engine's reference to libzmq
    install_name_tool -change \
        "$ZMQ_SRC" \
        "@executable_path/../Frameworks/$ZMQ_FILENAME" \
        "$MACOS_ENGINE" 2>/dev/null || true
    codesign -s - --force "$MACOS_ENGINE" 2>/dev/null || true
    echo "Dependencies bundled."
elif [ -n "$ZMQ_SRC" ]; then
    echo "Warning: libzmq referenced at '$ZMQ_SRC' but file not found — skipping bundle step."
fi

# ---------------------------------------------------------------------------
# Emit one zip per requested platform.
#
# The staging tree above (relinked + signed on macOS) is the master. For
# each platform we copy it, keep only that platform's engine, and zip.
# ---------------------------------------------------------------------------
BUILT=()

emit_zip() {
    local label="$1" suffix="$2" keep="$3"
    local stage="$TEMP_DIR/out_${label}"
    rm -rf "$stage"; mkdir -p "$stage"
    cp -R "$PACKAGE_DIR" "$stage/audio_nodes"
    # Ship the license texts with the binaries (GPL/AGPL/MPL require it)
    cp "$PROJECT_DIR/LICENSE" "$PROJECT_DIR/THIRD_PARTY_NOTICES.md" "$stage/audio_nodes/"
    if [ -n "$keep" ]; then
        for d in "$stage/audio_nodes/bin"/*/; do
            [ "$(basename "$d")" = "$keep" ] || rm -rf "$d"
        done
    fi
    local name="${PACKAGE_NAME}${suffix}.zip"
    local out="$OUTPUT_DIR/$name"
    # Build inside the temp dir and copy into place. zip replaces an existing
    # archive by writing a temp file and renaming over it, which needs unlink
    # rights in the destination - copying truncates instead, so this also
    # works on read-restricted or synced folders.
    ( cd "$stage" && zip -qr "$TEMP_DIR/$name" "audio_nodes" \
        -x "*.DS_Store" -x "*__pycache__*" )
    cp "$TEMP_DIR/$name" "$out"
    BUILT+=("$out")
    echo "  -> $(basename "$out") ($(du -h "$out" | cut -f1))"
}

echo ""
echo "Writing packages..."
case "$PLATFORM" in
    combined)
        emit_zip combined "" ""
        ;;
    mac)
        emit_zip mac "-macos-arm64" "darwin_arm64"
        ;;
    win)
        emit_zip win "-windows-x64" "win_x64"
        ;;
    linux)
        emit_zip linux "-linux-x64" "linux_x64"
        ;;
    both)
        [ $HAVE_MAC = 1 ] && emit_zip mac "-macos-arm64" "darwin_arm64"
        [ $HAVE_WIN = 1 ] && emit_zip win "-windows-x64" "win_x64"
        [ $HAVE_LINUX = 1 ] && emit_zip linux "-linux-x64" "linux_x64"
        echo ""
        echo "Zips are only written for engines built on this machine. GitHub"
        echo "Actions (.github/workflows/release.yml) builds all three platforms."
        ;;
esac

# ---- verify each zip before anyone installs it ----
echo ""
echo "Verifying..."
for z in "${BUILT[@]}"; do
    name=$(basename "$z")
    unzip -l "$z" | grep -q "audio_nodes/__init__.py" \
        || { echo "  FAIL $name: no __init__.py"; exit 1; }
    unzip -l "$z" | grep -q "__pycache__" \
        && { echo "  FAIL $name: __pycache__ leaked"; exit 1; }
    case "$name" in
      *macos*)   unzip -l "$z" | grep -q "MacOS/Audio Nodes Engine" \
                    || { echo "  FAIL $name: no macOS engine"; exit 1; }
                 unzip -l "$z" | grep -q "Frameworks/libzmq" \
                    || { echo "  FAIL $name: bundled libzmq missing"; exit 1; }
                 unzip -l "$z" | grep -q "win_x64/AudioNodesEngine.exe" \
                    && { echo "  FAIL $name: windows engine should not be here"; exit 1; }
                 ;;
      *windows*) unzip -l "$z" | grep -q "win_x64/AudioNodesEngine.exe" \
                    || { echo "  FAIL $name: no Windows engine"; exit 1; }
                 unzip -l "$z" | grep -q "MacOS/Audio Nodes Engine" \
                    && { echo "  FAIL $name: macOS engine should not be here"; exit 1; }
                 ;;
      *linux*)   unzip -l "$z" | grep -q "linux_x64/AudioNodesEngine" \
                    || { echo "  FAIL $name: no Linux engine"; exit 1; }
                 unzip -l "$z" | grep -qE "MacOS/Audio Nodes Engine|win_x64/AudioNodesEngine.exe" \
                    && { echo "  FAIL $name: another platform's engine is here"; exit 1; }
                 ;;
    esac
    echo "  OK   $name"
done

# Cleanup
rm -rf "$TEMP_DIR"

echo ""
echo "Done. ${#BUILT[@]} package(s) in $OUTPUT_DIR/"

# Also overwrite the local Blender add-ons install (dev convenience) with the
# zip for THIS machine. Skip with SKIP_BLENDER_INSTALL=1.
if [ "$(uname -s)" = "Darwin" ]; then
    LOCAL_ZIP=""
    for z in "${BUILT[@]}"; do
        case "$(basename "$z")" in
            *macos*) LOCAL_ZIP="$z" ;;
            *) [ -z "$LOCAL_ZIP" ] && [ "$PLATFORM" = "combined" ] && LOCAL_ZIP="$z" ;;
        esac
    done
    if [ -n "$LOCAL_ZIP" ]; then
        "$SCRIPT_DIR/install_addon.sh" "$LOCAL_ZIP"
    else
        echo "No macOS zip built - skipping local install."
    fi
else
    echo ""
    echo "Installation instructions:"
    echo "1. Open Blender"
    echo "2. Go to Edit > Preferences > Add-ons"
    echo "3. Click 'Install...' and select the .zip for your platform"
    echo "4. Enable 'Audio Nodes' in the add-ons list"
    echo ""
    echo "Note: The add-on will automatically install pyzmq on first run."
fi
