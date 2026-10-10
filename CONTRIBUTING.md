# Contributing to Audio Nodes

Thanks for wanting to help! Bug reports, ideas and code are all welcome.

## How changes get in

The `main` branch is protected. Nobody pushes to it directly: every change
arrives as a **pull request** and is reviewed by the maintainer before it is
merged. If a PR isn't merged, nothing in `main` changes.

## Reporting bugs and ideas

Open an [issue](../../issues) first, especially for bigger changes, so we can
agree on the approach before you spend time on it. For bugs, include:

- Blender version and OS (macOS Apple Silicon / Windows)
- Add-on version (Preferences > Add-ons > Audio Nodes)
- Steps to reproduce, and what you expected vs. what happened
- Any errors from the System Console

## Making a change

1. **Fork** this repository (the "Fork" button at the top right).
2. **Clone** your fork and create a branch:
   ```bash
   git clone https://github.com/<your-username>/AudioNodesBlender.git
   cd AudioNodesBlender
   git checkout -b fix-short-description
   ```
3. Make your change. Keep each PR focused on one thing.
4. **Test** it (see below).
5. **Push** the branch to your fork and open a **pull request** against `main`.
   Describe what changed, why, and how you tested it.
6. Address review comments by pushing more commits to the same branch.
   New commits clear earlier approvals, so the final version gets reviewed.

## Building and testing

- Build the engine: `scripts/build_engine.sh Release` (macOS) or
  `scripts\build_engine.bat Release` (Windows). JUCE and other C++
  dependencies are downloaded by CMake. See [WINDOWS_BUILD.md](WINDOWS_BUILD.md).
- Install the add-on into Blender: `scripts/install_addon.sh`
- Python tests (no engine needed): `python3 tests/test_pure_logic.py`,
  `python3 tests/test_control_logic.py`, `python3 tests/test_meter_and_layout.py`
- Engine integration tests (needs a built engine):
  `cd tests && python3 -m pytest test_engine.py test_devices.py`
- Tests inside Blender: `scripts/run_blender_tests.sh`

[ARCHITECTURE.md](ARCHITECTURE.md) explains how the Blender add-on talks to the
C++ engine (ZeroMQ commands, shared memory) and how to add a new node type.

## Please don't commit

- Build output (`engine/build*/`, `dist/`, `.zip` packages)
- Compiled engine binaries (`.exe`, the `.app` executable). Releases are
  built by the maintainer and attached to GitHub Releases.
- `__pycache__`, virtual environments, editor or OS files

## License

By contributing, you agree that your contributions are licensed under the
[GNU General Public License v3.0](LICENSE), the same license as the project.
