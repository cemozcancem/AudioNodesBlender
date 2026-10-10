@echo off
REM Package the Audio Nodes add-on as installable .zip(s).
REM
REM   scripts\package_addon.bat <version> [mac^|win^|both^|combined]
REM
REM Thin wrapper around package_addon.ps1 - the real logic lives there,
REM because zipping and verifying an archive in batch is far more error
REM prone than in PowerShell.

setlocal
set VERSION=%1
set PLATFORM=%2
if "%VERSION%"=="" (
    echo ERROR: version required.  Usage: scripts\package_addon.bat 4.14.1 win
    exit /b 1
)
if "%PLATFORM%"=="" set PLATFORM=both

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0package_addon.ps1" ^
    -Version %VERSION% -Platform %PLATFORM%
exit /b %errorlevel%
