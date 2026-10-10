#!/bin/bash

# Build script for Audio Nodes Engine (macOS and Linux)
# Usage: ./scripts/build_engine.sh [Debug|Release]
#
# Linux needs the JUCE build dependencies, e.g. on Ubuntu/Debian:
#   sudo apt install build-essential cmake pkg-config libasound2-dev \
#       libfreetype-dev libfontconfig1-dev libx11-dev libxcomposite-dev \
#       libxcursor-dev libxext-dev libxinerama-dev libxrandr-dev libxrender-dev
# ZeroMQ is built from source by CMake on Linux.

set -e

BUILD_TYPE=${1:-Release}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="$PROJECT_DIR/engine/build"

echo "Building Audio Nodes Engine ($BUILD_TYPE)..."

# Check for required tools
if ! command -v cmake &> /dev/null; then
    echo "Error: CMake is required but not installed."
    echo "Install with: brew install cmake (macOS) or your package manager (Linux)"
    exit 1
fi

# Create build directory
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

if [ "$(uname -s)" = "Darwin" ]; then
    # Check for libzmq
    if ! brew list zeromq &> /dev/null; then
        echo "Installing ZeroMQ..."
        brew install zeromq
    fi

    cmake .. \
        -DCMAKE_BUILD_TYPE=$BUILD_TYPE \
        -DCMAKE_OSX_ARCHITECTURES=arm64 \
        -DCMAKE_OSX_DEPLOYMENT_TARGET=12.0
    cmake --build . --config $BUILD_TYPE --parallel $(sysctl -n hw.ncpu)
    BINARY="$PROJECT_DIR/blender_addon/bin/darwin_arm64/AudioNodesEngine.app"
else
    cmake .. -DCMAKE_BUILD_TYPE=$BUILD_TYPE
    cmake --build . --config $BUILD_TYPE --parallel $(nproc)
    BINARY="$PROJECT_DIR/blender_addon/bin/linux_x64/AudioNodesEngine"
fi

echo ""
echo "Build complete!"
echo "Binary location: $BINARY"

