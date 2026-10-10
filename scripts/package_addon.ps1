<#
Package the Audio Nodes add-on as installable .zip(s).   [Windows]

  scripts\package_addon.ps1 <version> [-Platform mac|win|both|combined]

Mirrors scripts/package_addon.sh. One zip per platform is the default because
the engine is most of the download: a Windows user has no use for the macOS
.app and vice versa. A platform zip is only written when that platform's
engine actually exists - we never ship a zip whose engine is missing, because
the add-on would install and register cleanly and then never make a sound.

The macOS dylib relink in the .sh has no Windows equivalent: a -static-md
ZeroMQ build leaves nothing to bundle, and any DLL the build did produce was
already copied next to the .exe by the POST_BUILD step in engine/CMakeLists.
#>
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [ValidateSet('mac', 'win', 'both', 'combined')][string]$Platform = 'both'
)

$ErrorActionPreference = 'Stop'
$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$AddonDir   = Join-Path $ProjectDir 'blender_addon'
$OutputDir  = Join-Path $ProjectDir 'dist'
$PackageName = "audio_nodes-$Version"

$MacApp = Join-Path $AddonDir 'bin\darwin_arm64\AudioNodesEngine.app'
$WinExe = Join-Path $AddonDir 'bin\win_x64\AudioNodesEngine.exe'
$HaveMac = Test-Path (Join-Path $MacApp 'Contents\MacOS\Audio Nodes Engine')
$HaveWin = Test-Path $WinExe

Write-Host "Packaging Audio Nodes add-on v$Version (platform: $Platform)..."
Write-Host ("  macOS engine:   " + $(if ($HaveMac) { 'present' } else { 'MISSING' }))
Write-Host ("  Windows engine: " + $(if ($HaveWin) { 'present' } else { 'MISSING' }))

if (-not $HaveMac -and -not $HaveWin) {
    throw "No engine binaries found. Build first: scripts\build_engine.bat Release"
}
if ($Platform -eq 'mac' -and -not $HaveMac) { throw "--platform mac but no macOS engine at $MacApp" }
if ($Platform -eq 'win' -and -not $HaveWin) {
    throw "--platform win but no Windows engine at $WinExe`n       Build it: scripts\build_engine.bat Release"
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$Temp = Join-Path ([IO.Path]::GetTempPath()) ("an_pkg_" + [Guid]::NewGuid().ToString('N').Substring(0,8))
$Stage = Join-Path $Temp 'audio_nodes'
New-Item -ItemType Directory -Force -Path $Stage | Out-Null

Write-Host "Creating package structure..."
robocopy $AddonDir $Stage /E /NFL /NDL /NJH /NJS /NP /XD __pycache__ /XF *.pyc .DS_Store .gitkeep | Out-Null
if ($LASTEXITCODE -ge 8) { throw "staging copy failed (robocopy $LASTEXITCODE)" }
$global:LASTEXITCODE = 0

# Compress-Archive on Windows PowerShell 5.1 writes entry names with
# BACKSLASHES ("audio_nodes\bin\..."), which the ZIP spec forbids - it
# mandates "/". Python's zipfile silently tolerates it on read, so the zips
# looked fine while being malformed, and they do not extract correctly on
# macOS or Linux. Build the archive entry by entry instead so separators are
# always "/".
function New-ZipWithForwardSlashes([string]$SourceDir, [string]$OutFile) {
    Add-Type -AssemblyName System.IO.Compression | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem | Out-Null
    $root   = (Resolve-Path $SourceDir).Path.TrimEnd('\')
    $parent = Split-Path -Parent $root
    $fs  = [IO.File]::Open($OutFile, [IO.FileMode]::CreateNew)
    $zip = New-Object IO.Compression.ZipArchive($fs, [IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($f in Get-ChildItem $root -Recurse -File) {
            $rel = $f.FullName.Substring($parent.Length + 1).Replace('\', '/')
            $entry = $zip.CreateEntry($rel, [IO.Compression.CompressionLevel]::Optimal)
            $es = $entry.Open()
            $in = [IO.File]::OpenRead($f.FullName)
            try { $in.CopyTo($es) } finally { $in.Dispose(); $es.Dispose() }
        }
    } finally { $zip.Dispose(); $fs.Dispose() }
}

$Built = @()
function Emit-Zip([string]$Suffix, [string]$KeepBinDir) {
    $out = Join-Path $OutputDir "$PackageName$Suffix.zip"
    $per = Join-Path $Temp ("out" + $Suffix.Replace('-', '_'))
    New-Item -ItemType Directory -Force -Path $per | Out-Null
    Copy-Item $Stage (Join-Path $per 'audio_nodes') -Recurse
    # Ship the license texts with the binaries (GPL/AGPL/MPL require it)
    Copy-Item (Join-Path $ProjectDir 'LICENSE'), (Join-Path $ProjectDir 'THIRD_PARTY_NOTICES.md') (Join-Path $per 'audio_nodes')
    # Keep only this platform's engine folder
    if ($KeepBinDir) {
        Get-ChildItem (Join-Path $per "audio_nodes\bin") -Directory |
            Where-Object { $_.Name -ne $KeepBinDir } |
            Remove-Item -Recurse -Force
    }
    if (Test-Path $out) { Remove-Item $out -Force }
    New-ZipWithForwardSlashes (Join-Path $per 'audio_nodes') $out
    $size = '{0:N1} MB' -f ((Get-Item $out).Length / 1MB)
    Write-Host "  -> $(Split-Path -Leaf $out) ($size)"
    $script:Built += $out
}

Write-Host "`nWriting packages..."
switch ($Platform) {
    'combined' { Emit-Zip '' '' }
    'mac'      { Emit-Zip '-macos-arm64' 'darwin_arm64' }
    'win'      { Emit-Zip '-windows-x64' 'win_x64' }
    'both'     {
        if ($HaveMac) { Emit-Zip '-macos-arm64' 'darwin_arm64' }
        if ($HaveWin) { Emit-Zip '-windows-x64' 'win_x64' }
        if (-not $HaveWin) {
            Write-Host "`nNOTE: no Windows zip - blender_addon\bin\win_x64\AudioNodesEngine.exe"
            Write-Host "      does not exist yet. Build it with scripts\build_engine.bat."
        }
    }
}

Write-Host "`nVerifying..."
Add-Type -AssemblyName System.IO.Compression.FileSystem
foreach ($z in $Built) {
    $name = Split-Path -Leaf $z
    $zip = [IO.Compression.ZipFile]::OpenRead($z)
    try { $names = $zip.Entries.FullName } finally { $zip.Dispose() }

    if ($names -match '\\\\') { throw "$name : entries use backslashes - not a spec-conformant zip" }
    if ($names -notcontains 'audio_nodes/__init__.py') { throw "$name : __init__.py missing" }
    if ($names -match '__pycache__')                   { throw "$name : __pycache__ leaked" }
    if ($names -match '\.pyc$')                        { throw "$name : .pyc leaked" }
    switch -Wildcard ($name) {
        '*macos*' {
            # NB: -notmatch on an array filters, it does not return a bool.
            if (-not ($names -match 'MacOS/Audio Nodes Engine')) { throw "$name : no macOS engine" }
            if ($names -match 'win_x64/AudioNodesEngine\.exe') { throw "$name : Windows engine should not be here" }
        }
        '*windows*' {
            if (-not ($names -match 'win_x64/AudioNodesEngine\.exe')) { throw "$name : no Windows engine" }
            if ($names -match 'MacOS/Audio Nodes Engine') { throw "$name : macOS engine should not be here" }
            $dlls = $names | Where-Object { $_ -like 'audio_nodes/bin/win_x64/*.dll' }
            if ($dlls) { Write-Host "  NOTE bundled DLLs: $($dlls -join ', ')" }
        }
    }
    Write-Host "  OK   $name"
}

Remove-Item $Temp -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "`nDone. $($Built.Count) package(s) in $OutputDir"
