#!/bin/bash

# Build script for Audio Nodes Engine (macOS)
# Usage: ./scripts/build_engine.sh [Debug|Release]

set -e

BUILD_TYPE=${1:-Release}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BUILD_DIR="$PROJECT_DIR/engine/build"

echo "Building Audio Nodes Engine ($BUILD_TYPE)..."

# Check for required tools
if ! command -v cmake &> /dev/null; then
    echo "Error: CMake is required but not installed."
    echo "Install with: brew install cmake"
    exit 1
fi

# Check for libzmq
if ! brew list zeromq &> /dev/null; then
    echo "Installing ZeroMQ..."
    brew install zeromq
fi

# Create build directory
mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

# Configure with CMake
cmake .. \
    -DCMAKE_BUILD_TYPE=$BUILD_TYPE \
    -DCMAKE_OSX_ARCHITECTURES=arm64 \
    -DCMAKE_OSX_DEPLOYMENT_TARGET=12.0

# Build
cmake --build . --config $BUILD_TYPE --parallel $(sysctl -n hw.ncpu)

echo ""
echo "Build complete!"
echo "Binary location: $PROJECT_DIR/blender_addon/bin/darwin_arm64/AudioNodesEngine.app"

