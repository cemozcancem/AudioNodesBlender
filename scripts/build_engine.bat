@echo off
REM Build script for Audio Nodes Engine (Windows)
REM
REM Usage: scripts\build_engine.bat [Debug|Release]
REM
REM Requires:
REM   - Visual Studio 2022 with the "Desktop development with C++" workload
REM   - CMake 3.22+            https://cmake.org/download/
REM   - Git                    (CMake FetchContent pulls JUCE, cppzmq, json)
REM   - vcpkg with ZeroMQ:     vcpkg install zeromq:x64-windows-static-md
REM
REM Point VCPKG_ROOT at your vcpkg checkout, or pass it in the environment:
REM   set VCPKG_ROOT=C:\dev\vcpkg
REM
REM The triplet matters: -static-md links ZeroMQ statically against the
REM DYNAMIC CRT. Static ZeroMQ means no DLL to ship beside the .exe; the
REM dynamic CRT is what JUCE and MSVC default to, so mixing is avoided.

setlocal enabledelayedexpansion

set BUILD_TYPE=%1
if "%BUILD_TYPE%"=="" set BUILD_TYPE=Release

set TRIPLET=%VCPKG_TARGET_TRIPLET%
if "%TRIPLET%"=="" set TRIPLET=x64-windows-static-md

set SCRIPT_DIR=%~dp0
set PROJECT_DIR=%SCRIPT_DIR%..
set BUILD_DIR=%PROJECT_DIR%\engine\build
set OUT_EXE=%PROJECT_DIR%\blender_addon\bin\win_x64\AudioNodesEngine.exe

echo ============================================================
echo Audio Nodes Engine - Windows build (%BUILD_TYPE%, %TRIPLET%)
echo ============================================================

REM ---- prerequisite checks (fail early with something actionable) ----
where cmake >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: CMake not found on PATH.
    echo        Install from https://cmake.org/download/ and tick
    echo        "Add CMake to the system PATH".
    exit /b 1
)

where git >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Git not found on PATH.
    echo        CMake fetches JUCE, cppzmq and nlohmann/json over Git.
    exit /b 1
)

if "%VCPKG_ROOT%"=="" (
    echo ERROR: VCPKG_ROOT is not set.
    echo        set VCPKG_ROOT=C:\path\to\vcpkg
    echo        %%VCPKG_ROOT%%\vcpkg install zeromq:%TRIPLET%
    exit /b 1
)

set TOOLCHAIN=%VCPKG_ROOT%\scripts\buildsystems\vcpkg.cmake
if not exist "%TOOLCHAIN%" (
    echo ERROR: vcpkg toolchain not found at:
    echo        %TOOLCHAIN%
    echo        Is VCPKG_ROOT pointing at a real vcpkg checkout?
    exit /b 1
)

echo   CMake      : OK
echo   Git        : OK
echo   vcpkg      : %VCPKG_ROOT%
echo.

REM ---- configure ----
if not exist "%BUILD_DIR%" mkdir "%BUILD_DIR%"

echo [1/2] Configuring...
cmake -S "%PROJECT_DIR%\engine" -B "%BUILD_DIR%" ^
    -G "Visual Studio 17 2022" -A x64 ^
    -DCMAKE_TOOLCHAIN_FILE="%TOOLCHAIN%" ^
    -DVCPKG_TARGET_TRIPLET=%TRIPLET%
if %errorlevel% neq 0 (
    echo.
    echo ERROR: CMake configure failed.
    echo   * "Could not find ZeroMQ"  -^> %%VCPKG_ROOT%%\vcpkg install zeromq:%TRIPLET%
    echo   * Git clone failures       -^> check network/proxy; FetchContent needs it
    exit /b 1
)

REM ---- build ----
echo.
echo [2/2] Building %BUILD_TYPE%...
cmake --build "%BUILD_DIR%" --config %BUILD_TYPE% --parallel
if %errorlevel% neq 0 (
    echo.
    echo ERROR: Build failed.
    echo   * "unresolved external symbol zmq_*" -^> ZeroMQ static/shared mismatch.
    echo     The CMakeLists defines ZMQ_STATIC for static triplets; confirm the
    echo     triplet you installed matches the one used here (%TRIPLET%).
    exit /b 1
)

REM ---- verify the POST_BUILD copy actually landed ----
if not exist "%OUT_EXE%" (
    echo.
    echo ERROR: build reported success but the engine is not at:
    echo        %OUT_EXE%
    echo        The POST_BUILD copy step did not run.
    exit /b 1
)

echo.
echo ============================================================
echo Build complete.
echo   %OUT_EXE%
dir /b "%PROJECT_DIR%\blender_addon\bin\win_x64"
echo.
echo Any .dll listed above must ship alongside the .exe. With a
echo -static-md triplet there should be none.
echo.
echo Next: scripts\package_addon.bat ^<version^>
echo ============================================================
endlocal
