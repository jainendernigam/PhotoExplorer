<#
Sets up a private, portable Python for Photo Explorer in .\runtime\python
(no admin rights, no installer, nothing written outside this folder), then
installs the backend packages into it.

    powershell -ExecutionPolicy Bypass -File scripts\setup_runtime.ps1

Uses the official python.org "python" NuGet package: a plain zip of a full
CPython with pip.
#>
param([string]$Version = "3.12.10")
$ErrorActionPreference = "Stop"

$root    = Split-Path -Parent $PSScriptRoot
$runtime = Join-Path $root "runtime"
$pyDir   = Join-Path $runtime "python"
$python  = Join-Path $pyDir "python.exe"

if (-not (Test-Path $python)) {
    New-Item -ItemType Directory -Force $runtime | Out-Null
    $pkg = Join-Path $runtime "python.$Version.zip"
    $tmp = Join-Path $runtime "_python_pkg"
    Write-Host "Downloading Python $Version (python.org NuGet package)..."
    Invoke-WebRequest "https://www.nuget.org/api/v2/package/python/$Version" -OutFile $pkg -UseBasicParsing
    Expand-Archive $pkg $tmp -Force
    Move-Item (Join-Path $tmp "tools") $pyDir
    Remove-Item $tmp, $pkg -Recurse -Force
}

& $python -m pip install --upgrade pip --no-warn-script-location
& $python -m pip install -r (Join-Path $root "backend\requirements.txt") --no-warn-script-location
& $python -c "import sqlite3; c=sqlite3.connect(':memory:'); c.execute('create virtual table t using fts5(x)'); print('Python OK, SQLite', sqlite3.sqlite_version, 'with FTS5')"
Write-Host "`nRuntime ready: $python"
Write-Host "Optional: put ffmpeg.exe and ffprobe.exe in $root\bin for video support."
