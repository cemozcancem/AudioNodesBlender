# Building the Audio Nodes engine on Windows

Everything in the repo is already prepared — the CMake blockers are fixed, the
Windows-specific Python paths are corrected, and both `.bat` scripts check
their prerequisites and verify their own output. This is the sequence to run,
what each step should print, and what to send back if it doesn't.

The macOS side is unaffected: after these changes `cmake` configures, builds
and links on macOS exactly as before, and the add-on's Blender regression
suite passes 25/25.

---

## 1. Prerequisites

| What | Where | Notes |
|---|---|---|
| Visual Studio 2022 | visualstudio.microsoft.com | **Desktop development with C++** workload. Build Tools alone is fine. |
| CMake 3.22+ | cmake.org/download | Tick "Add CMake to the system PATH". |
| Git | git-scm.com | CMake fetches JUCE 8.0.4, cppzmq and nlohmann/json over Git at configure time. |
| vcpkg | github.com/microsoft/vcpkg | For ZeroMQ. |

```bat
git clone https://github.com/microsoft/vcpkg C:\dev\vcpkg
C:\dev\vcpkg\bootstrap-vcpkg.bat
set VCPKG_ROOT=C:\dev\vcpkg
%VCPKG_ROOT%\vcpkg install zeromq:x64-windows-static-md
```

`setx VCPKG_ROOT C:\dev\vcpkg` makes it permanent.

**Why `x64-windows-static-md`:** ZeroMQ linked statically (nothing to ship
beside the .exe) against the *dynamic* CRT (what JUCE and MSVC default to, so
no runtime mismatch). The build script defaults to this triplet; override with
`set VCPKG_TARGET_TRIPLET=...` if you need something else.

---

## 2. Build

```bat
cd path\to\AudioNodesV2
scripts\build_engine.bat Release
```

Expected, in order:

```
Audio Nodes Engine - Windows build (Release, x64-windows-static-md)
  CMake      : OK
  Git        : OK
  vcpkg      : C:\dev\vcpkg

[1/2] Configuring...
-- ZeroMQ: linking libzmq-static (static=TRUE)
...
-- Build files have been written to: ...\engine\build

[2/2] Building Release...
...
Build complete.
  ...\blender_addon\bin\win_x64\AudioNodesEngine.exe
AudioNodesEngine.exe
```

That last `dir` listing is the check that matters: **with a static triplet it
should show only the .exe.** Any `.dll` listed has to travel with the engine —
the packaging step includes whatever is in that folder, so it will, but a DLL
appearing means the triplet wasn't the static one.

Configure alone takes a few minutes the first time (JUCE clone + juceaide).

---

## 3. Package

```bat
scripts\package_addon.bat 4.14.0
```

It stages a clean copy (no `__pycache__`, `.pyc`, `.DS_Store`, `.gitkeep`),
zips it, then verifies its own output and refuses to ship a zip that fails:

```
  OK   audio_nodes/__init__.py present
  OK   no __pycache__
  OK   no .pyc
  OK   windows engine present

Package created: ...\dist\audio_nodes-4.14.0.zip
```

The zip will contain **both** platforms' engines if the macOS `.app` is still
in the tree — that's intended, one zip works on both.

---

## 4. Verify on a clean machine

The real test is a Windows box with no Visual Studio, no vcpkg, and no
redistributable installed — that's what catches a missing DLL or a CRT
mismatch, which look fine on the build machine.

1. Install the zip: Blender → Edit → Preferences → Add-ons → Install…
2. Enable **Audio Nodes**. pyzmq installs itself on first run.
3. Editor Type → Audio Nodes. The header should read **Engine: Stopped** with
   a ▶ button.
4. Press ▶. It should flip to **Engine: Running** within a second or two.
5. Shift+A → Generators → Oscillator, Shift+A → Analysis → FFT Analyzer,
   wire Oscillator → FFT. The spectrum should show a spike.
6. Add an Audio Output, wire the FFT through it, unmute — you should hear the
   tone.

Or run the headless smoke test, which does all of that and more:

```bat
"C:\Program Files\Blender Foundation\Blender 5.1\blender.exe" ^
    --background --factory-startup --python tests\smoke_installed.py
```

Expect `10/10 PASSED`. It needs an audio device (it pushes a real 440 Hz tone
through the engine's FFT).

---

## 5. If it fails

Send me the output and I'll work from it. The failures worth anticipating:

**`Could not find a package configuration file provided by "ZeroMQ"`**
vcpkg didn't install it, or `VCPKG_ROOT` points somewhere else. Re-run the
`vcpkg install` line and confirm `%VCPKG_ROOT%\installed\x64-windows-static-md\share\zeromq\`
exists.

**`unresolved external symbol __imp_zmq_ctx_new`** (and friends)
A static libzmq being compiled as if it were a DLL. `engine/CMakeLists.txt`
defines `ZMQ_STATIC` when it detects a static target — if this still happens,
the triplet you installed and the one CMake used have diverged. Delete
`engine\build` and reconfigure.

**`LNK2038: mismatch detected for 'RuntimeLibrary'`**
CRT mismatch — a `-static` (not `-static-md`) triplet. Reinstall as
`zeromq:x64-windows-static-md` and wipe `engine\build`.

**Engine builds but won't start from Blender**
Read `%TEMP%\AudioNodesEngine.log` — the add-on writes the engine's stdout and
stderr there. "ready on port 5555" means it started and the problem is on the
ZMQ side; an empty file usually means a missing DLL (the loader kills the
process before `main`).

**Meters and spectrum stay flat but the engine says Running**
Shared memory. The C++ side creates a `CreateFileMappingA` named
`AudioNodesVisualization`; Python attaches to the same name. Both must be in
the same Windows session — they will be if Blender launched the engine.

---

## What was changed to make this possible

For reference, and because these are the places to look if something behaves
oddly on Windows specifically:

**`engine/CMakeLists.txt`**
- ZeroMQ discovery is branched: pkg-config on Unix, `find_package(ZeroMQ CONFIG)`
  on Windows. `find_package(PkgConfig REQUIRED)` used to run unconditionally
  and failed configure outright — the first and hardest blocker.
- Removed a `set(CMAKE_MSVC_RUNTIME_LIBRARY ...)` that sat *after*
  `juce_add_gui_app()`. CMake reads that variable when a target is created, so
  it never did anything; setting it for real would clash with vcpkg's CRT.
- Post-build copies `$<TARGET_RUNTIME_DLLS:...>` next to the .exe, so a shared
  ZeroMQ build is runnable too. Empty and skipped for a static triplet.

**`engine/src/Main.cpp`**
- `moreThanOneInstanceAllowed()` now returns `true`. It returned `false`,
  which made JUCE take a machine-wide named mutex and silently refuse to start
  a second engine — even on a different port. Port collisions are caught where
  they belong, in `CommandServer::start()`.

**`blender_addon/engine_manager.py`**
- Orphan cleanup is scoped to our port via CIM/PowerShell. It was
  `taskkill /F /IM AudioNodesEngine.exe`, which killed *every* engine on the
  machine — a second Blender lost its engine without warning. There is
  deliberately no blanket-kill fallback: a stuck orphan gives a clear "not
  responding" message, whereas killing someone else's engine is silent.
- `Popen` gets `DETACHED_PROCESS | CREATE_NO_WINDOW` on Windows.
  `start_new_session=True` is POSIX-only and was being silently ignored.

**`blender_addon/shared_memory_reader.py`**
- Windows attach now probes with `OpenFileMappingW` first.
  `mmap.mmap(-1, size, tagname=...)` *creates* the mapping when it doesn't
  exist, so with the engine dead this used to succeed and read zeros forever —
  flat meters, silent taps, no error anywhere.

**`scripts/build_engine.bat`, `scripts/package_addon.bat`**
- Prerequisite checks that name the fix, vcpkg toolchain wiring, verification
  that the post-build copy actually landed, and package self-verification.
