# Audio Nodes - Blender 5.0 Add-on

Real-time audio processing and visualization with a node-based workflow in Blender.

## Features

- **Custom Editor Type**: "Audio Nodes" appears in Blender's editor type dropdown
- **Node-Based Workflow**: Connect audio nodes visually
- **Real-time Processing**: C++ audio engine runs separately from Blender's UI thread
- **Audio-Reactive Animations**: Drive any Blender property from audio using the Attribute node
- **Cross-Platform**: macOS (Apple Silicon) and Windows support

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Blender Process                          │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │  Node Editor UI │  │  Timer (60fps)  │  │    Drivers      │  │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘  │
│           │                    │                    │           │
│           │ ZeroMQ             │ Shared Memory      │           │
│           │ Commands           │ FFT/Levels         │           │
└───────────┼────────────────────┼────────────────────┼───────────┘
            │                    │                    │
            ▼                    ▼                    │
┌───────────────────────────────────────────────────────────────┐
│                    C++ Audio Engine (JUCE)                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │ ZMQ Server  │  │ NodeManager │  │  AudioDeviceManager     │ │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘ │
└───────────────────────────────────────────────────────────────┘
```

## Requirements

### Build Requirements
- CMake 3.22+
- C++20 compiler (Clang 14+ on macOS, MSVC 2022 on Windows)
- libzmq (ZeroMQ)

### macOS
```bash
brew install cmake zeromq
```

### Windows
- Visual Studio 2022
- CMake (included with VS or install separately)
- vcpkg for ZeroMQ: `vcpkg install zeromq:x64-windows`

## Building

### macOS (Apple Silicon)
```bash
./scripts/build_engine.sh Release
```

### Windows
```batch
scripts\build_engine.bat Release
```

## Installation

### From a release (recommended)

1. Download the zip for your platform from [Releases](../../releases)
   (`audio_nodes-<version>-macos-arm64.zip` or `audio_nodes-<version>-windows-x64.zip`)
2. In Blender: Edit > Preferences > Add-ons > Install from Disk, and pick the zip
3. Enable "Audio Nodes". On first enable it installs `pyzmq` into Blender's Python automatically.

### From source

1. Build the engine (see above)
2. Copy or symlink `blender_addon/` to Blender's addons folder:
   - macOS: `~/Library/Application Support/Blender/5.0/scripts/addons/audio_nodes/`
   - Windows: `%APPDATA%\Blender Foundation\Blender\5.0\scripts\addons\audio_nodes\`
3. Enable "Audio Nodes" in Blender's Add-ons preferences

## Usage

1. Open Blender
2. Switch any editor to "Audio Nodes" from the editor type dropdown
3. The audio engine should start automatically
4. Add nodes from the Add menu:
   - **Audio Input**: Receive audio from your microphone/interface
   - **Audio Output**: Send audio to your speakers
   - **Oscillator**: Generate test tones
   - **Filter**: Low/High/Band pass filtering
   - **Gain**: Volume control
   - **Delay**: Echo effect
   - **FFT Analyzer**: Spectrum analysis
   - **Attribute**: Drive Blender properties from audio

## Audio-Reactive Animations

1. Add an **FFT Analyzer** node in your audio chain
2. Add an **Attribute** node
3. Select a target object and property (e.g., `scale[2]`)
4. Configure value mapping (input/output ranges)
5. Click "Create Driver"
6. The property now responds to audio in real-time!

## Troubleshooting

### Engine not starting
- Check System Console (Window > Toggle System Console) for errors
- Verify the engine binary exists in `blender_addon/bin/`
- Try running the engine manually: `./AudioNodesEngine.app/Contents/MacOS/AudioNodesEngine --zmq-port 5555`

### No audio input
- On macOS, ensure microphone permission is granted in System Preferences > Privacy & Security > Microphone
- Check the Audio Devices panel in the sidebar for available devices

### Audio glitches
- Increase buffer size in add-on preferences
- Reduce the number of active nodes
- Check CPU usage

## Development

See [ARCHITECTURE.md](ARCHITECTURE.md) for how the add-on and engine fit together (node types, ZeroMQ command protocol, shared memory layout, thread safety).

Want to help? Read [CONTRIBUTING.md](CONTRIBUTING.md).

## License

GNU General Public License v3.0 - see [LICENSE](LICENSE).

The audio engine is built on [JUCE](https://juce.com), which has its own licensing terms (AGPLv3 or a commercial JUCE license).

