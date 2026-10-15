# Audio Nodes for Blender

Node-based real-time audio for Blender 5. Two processes:

- **Blender add-on** (`blender_addon/`, Python): node editor, UI, drivers. Sends commands to the engine over **ZeroMQ** (REQ/REP, port 5555 by default, set in add-on preferences) and reads results from **shared memory**.
- **Audio engine** (`engine/`, C++20 + JUCE 8): separate process. Audio graph, devices, FFT, MIDI. Writes visualization data to a file-backed mapping at `/tmp/AudioNodesVisualization` (named mapping on Windows).

Details: `ARCHITECTURE.md`. Contributor workflow: `CONTRIBUTING.md`.

## Commands

| Task | Command |
|---|---|
| Build engine (macOS/Linux) | `./scripts/build_engine.sh Release` |
| Build engine (Windows) | `scripts\build_engine.bat Release` (needs `VCPKG_ROOT`) |
| Offline tests (no engine) | `cd tests && python3 test_pure_logic.py && python3 test_control_logic.py && python3 test_meter_and_layout.py` |
| Engine tests | `cd tests && python3.11 -m pytest -q test_engine.py test_devices.py` (115 tests; all pass on a Mac with working audio) |
| Tests inside Blender | `./scripts/run_blender_tests.sh` (uses engine port 5599) |
| Package a zip | `SKIP_BLENDER_INSTALL=1 ./scripts/package_addon.sh <version> --platform mac` |

The version lives in two places, which must match: `blender_addon/__init__.py` (`bl_info["version"]`) and `engine/CMakeLists.txt` (`project(... VERSION)` and `juce_add_gui_app(... VERSION)`). Releases are built by GitHub Actions from a `v*` tag.

## Rules learned the hard way

**Engine**
- Graph changes (create/delete/connect) go through a lock-free queue to the audio thread. A command returns `ok` before the node exists, and nothing is applied while audio isn't running.
- The command server handles one command at a time. Device changes are slow (the engine waits up to 15 s); never block it on anything else.
- Start the command server **before** opening the audio device (`Main.cpp`). Opening a Bluetooth device can stall for seconds.
- Shared memory is written from the audio callback, about every 12 ms. Deleted nodes fade out for about 250 ms but are removed from shared memory at once (`NodeManager::unpublishNode`). Keep it that way.
- Sequencer patterns are set **only** with the `set_pattern` command. `set_param("pattern_N", float)` is ignored on purpose, because float32 corrupts masks over 24 steps.
- GainNode uses dB (`gain=0` is unity; use `mute=1` for silence). The mixer's first input is `input_1`.
- The shared-memory layout must match in three places: `engine/src/common/SharedTypes.h`, `blender_addon/shared_memory_reader.py` and `tests/harness.py`.

**Tests**
- Bluetooth headsets (AirPods) used for input and output drop to a 24 kHz headset mode and make timing tests fail at random. Test with built-in or wired audio.
- Tests that need real audio are marked `@pytest.mark.needs_audio` and skip themselves when no audio flows (`tests/conftest.py`). CI machines have no sound card, so **the local Mac run is the only real audio test**.
- Timing tests need realistic margins (CI machines stall). Poll for the expected state; don't `sleep` a few ms and assert.
- Shared-memory reads can be torn; `Engine.read_shared_memory()` retries. Device commands need a long reply timeout (`Engine.SLOW_COMMANDS`).
- Never kill engines globally (`pkill -f "Audio Nodes Engine"`). Always scope it to a port (`--zmq-port N`). The user's own Blender may be running one.

**Packaging and platforms**
- The macOS zip must work on a fresh Mac: bundled libzmq/libsodium in `Contents/Frameworks`, ad-hoc signing, and the add-on clears the quarantine flag. A past release broke on a second Mac; check fresh-install behaviour for any packaging change.
- The engine is copied into `blender_addon/bin/<platform>/` only when it relinks.
- Linux links libzmq statically (built by CMake); the engine needs only glibc, ALSA, freetype and fontconfig.
- In `.bat` files, parentheses inside `if (...)` blocks must be escaped (`^(`, `^)`).

**Product**
- Regressions are unacceptable: anything that worked in a released version must keep working.
- User-facing text (README, UI labels, release notes) is plain language for non-technical Blender users.
- `main` is protected. All changes arrive as pull requests that the maintainer merges.

## Agent team

New work is run by the `/pm` skill (`.claude/skills/pm/SKILL.md`) with the specialists in `.claude/agents/`. Agents work in a git worktree on an `agent/<issue>-<slug>` branch, open a pull request and stop. Only the maintainer merges, tags or releases.
