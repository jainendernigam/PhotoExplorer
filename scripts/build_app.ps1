<#
Builds the desktop app (Tauri). Usage:

    powershell -ExecutionPolicy Bypass -File scripts\build_app.ps1          # debug build (uses the vite dev server)
    powershell -ExecutionPolicy Bypass -File scripts\build_app.ps1 -Release # release exe (see package.ps1)

Why this script: with the MinGW (windows-gnu) Rust toolchain, GCC breaks
when its own install path contains a space (e.g. C:\Users\First Last\...):
it passes helper files such as default-manifest.o to ld unquoted. Setting
GCC_EXEC_PREFIX to the Windows 8.3 short path (no spaces) makes GCC build
those paths without spaces. Nothing is changed outside this session.
#>
param([switch]$Release)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

$gcc = Get-Command x86_64-w64-mingw32-gcc -ErrorAction SilentlyContinue
if ($gcc -and $gcc.Source -match " ") {
    $fso = New-Object -ComObject Scripting.FileSystemObject
    $shortRoot = $fso.GetFolder((Split-Path (Split-Path $gcc.Source))).ShortPath
    $env:PATH = "$shortRoot\bin;$env:PATH"
    $env:GCC_EXEC_PREFIX = "$shortRoot\lib\gcc\"
    Write-Host "Using MinGW via short path: $shortRoot"
}

# windres (embeds the app icon) also breaks on spaces in the build-output
# path, so build outside a spaced project folder: under LOCALAPPDATA, via its
# short path (drive C: keeps 8.3 names; D: may not).
if ($root -match " " -and -not $env:CARGO_TARGET_DIR) {
    $fso = New-Object -ComObject Scripting.FileSystemObject
    $base = Join-Path $env:LOCALAPPDATA "PhotoExplorer-build"
    New-Item -ItemType Directory -Force $base | Out-Null
    $env:CARGO_TARGET_DIR = Join-Path $fso.GetFolder($base).ShortPath "target"
    if ($env:CARGO_TARGET_DIR -match " ") { throw "No space-free build folder available: $env:CARGO_TARGET_DIR" }
}
Write-Host "Build output: $(if ($env:CARGO_TARGET_DIR) { $env:CARGO_TARGET_DIR } else { 'app\src-tauri\target' })"

Set-Location (Join-Path $root "app")
if ($Release) {
    npx tauri build --no-bundle      # just the exe; scripts\package.ps1 makes the portable zip
} else {
    Set-Location "src-tauri"
    cargo build
}
exit $LASTEXITCODE
