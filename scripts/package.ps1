<#
Builds the portable app: one folder (and a zip) that runs from anywhere,
with no installer and no admin rights.

    powershell -ExecutionPolicy Bypass -File scripts\package.ps1             # build + package
    powershell -ExecutionPolicy Bypass -File scripts\package.ps1 -SkipBuild  # re-package the last build
    ... -StageRoot release\_build    # assemble the folder there instead (e.g. while release\PhotoExplorer is in use)

Result: release\PhotoExplorer\ and release\PhotoExplorer-<version>-windows-portable.zip

    PhotoExplorer\
      PhotoExplorer.exe       the app (UI)
      WebView2Loader.dll      needed by the app (windows-gnu build)
      backend\photoexplorer   the engine (Python source)
      runtime\python          private Python with only the needed packages
      models\                 face models (YuNet, SFace)
      bin\                    ffmpeg.exe (videos and slideshows)
      README.txt
      data\                   created on first run: library, thumbnails, exports
#>
param([switch]$SkipBuild, [switch]$NoZip, [string]$StageRoot)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$version = (Get-Content (Join-Path $root "app\src-tauri\tauri.conf.json") -Raw | ConvertFrom-Json).version
$outRoot = Join-Path $root "release"
$stage = if ($StageRoot) { [IO.Path]::GetFullPath((Join-Path $root $StageRoot)) } else { $outRoot }
$out = Join-Path $stage "PhotoExplorer"   # the zip's top folder must be called PhotoExplorer (Update software expects it)
$backendVersion = (Select-String -Path (Join-Path $root "backend\photoexplorer\__init__.py") -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
if ($backendVersion -ne $version) { throw "Version mismatch: tauri.conf.json $version, backend $backendVersion" }

# Same build-output rule as build_app.ps1 (space-free folder when the project path has spaces).
function Get-TargetDir {
    if ($env:CARGO_TARGET_DIR) { return $env:CARGO_TARGET_DIR }
    if ($root -match " ") {
        $fso = New-Object -ComObject Scripting.FileSystemObject
        return Join-Path $fso.GetFolder((Join-Path $env:LOCALAPPDATA "PhotoExplorer-build")).ShortPath "target"
    }
    return Join-Path $root "app\src-tauri\target"
}

foreach ($need in "runtime\python\python.exe", "models\face_detection_yunet_2023mar.onnx", "bin\ffmpeg.exe") {
    if (-not (Test-Path (Join-Path $root $need))) { throw "Missing $need - run scripts\setup_runtime.ps1, 'python -m photoexplorer models --download' and scripts\get_ffmpeg.ps1 first" }
}

if (-not $SkipBuild) {
    Write-Host "== Building the release app (a few minutes the first time)"
    powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "build_app.ps1") -Release
    if ($LASTEXITCODE -ne 0) { throw "Release build failed" }
}
$exe = Join-Path (Get-TargetDir) "release\photo-explorer.exe"
if (-not (Test-Path $exe)) { throw "Release exe not found: $exe" }

Write-Host "== Assembling $out"
if (Test-Path $out) {
    if (-not (Test-Path (Join-Path $out "PhotoExplorer.exe"))) { throw "$out exists but is not a previous package; not touching it" }
    if (Test-Path (Join-Path $out "data")) { throw "$out\data exists (someone used this copy); move it away first" }
    Remove-Item $out -Recurse -Force
}
New-Item -ItemType Directory -Force $out | Out-Null
Copy-Item $exe (Join-Path $out "PhotoExplorer.exe")
# The windows-gnu build loads WebView2 through this DLL (MSVC builds link it in).
$loader = Join-Path (Split-Path $exe) "WebView2Loader.dll"
if (Test-Path $loader) { Copy-Item $loader $out }
elseif ((& rustc -vV) -match "windows-gnu") { throw "WebView2Loader.dll missing next to $exe" }

function Copy-Tree($from, $to, [string[]]$xd = @(), [string[]]$xf = @()) {
    $args = @($from, $to, "/E", "/NFL", "/NDL", "/NJH", "/NJS", "/NP", "/R:1", "/W:1")
    if ($xd) { $args += "/XD"; $args += $xd }
    if ($xf) { $args += "/XF"; $args += $xf }
    & robocopy @args | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copy failed: $from (robocopy $LASTEXITCODE)" }
}
# Engine source (no caches / tests)
Copy-Tree (Join-Path $root "backend\photoexplorer") (Join-Path $out "backend\photoexplorer") @("__pycache__")
# Private Python, without what only development needs. Also left out to keep the
# download small: FAISS (face grouping falls back to NumPy; it only speeds up
# libraries over ~50,000 faces) and OpenCV's own video reader (FFmpeg does that).
$py = Join-Path $root "runtime\python"
Copy-Tree $py (Join-Path $out "runtime\python") `
    @("$py\include", "$py\libs", "$py\Scripts", "$py\Lib\ensurepip", "$py\Lib\site-packages\pip",
      "$py\Lib\site-packages\_pytest", "$py\Lib\site-packages\pytest", "$py\Lib\site-packages\pygments",
      "$py\Lib\site-packages\pluggy", "$py\Lib\site-packages\iniconfig",
      "$py\Lib\site-packages\faiss", "$py\Lib\site-packages\faiss_cpu.libs") `
    @("*.pyc.*", "py.test*", "opencv_videoio_ffmpeg*.dll")
Get-ChildItem (Join-Path $out "runtime\python\Lib\site-packages") -Directory |
    Where-Object { $_.Name -match '^(pip|pytest|_pytest|pygments|pluggy|iniconfig|faiss_cpu)-.*\.dist-info$' } |
    Remove-Item -Recurse -Force
Copy-Tree (Join-Path $root "models") (Join-Path $out "models")
# ffmpeg only: the engine reads video details from `ffmpeg -i` when ffprobe is absent
Copy-Tree (Join-Path $root "bin") (Join-Path $out "bin") @() @("ffprobe.exe")

@"
Photo Explorer $version - portable
==================================

Run "PhotoExplorer.exe". No installation and no admin rights are needed.
Everything the app creates stays in this folder, in "data": the library
index, thumbnails, slideshows/collages, settings, and the window's cache.
Your photos are only read; nothing is changed unless you choose to
(Duplicates > Move away, or Organize > Move originals - both can be undone).

First steps
  1. "+ Add" a folder that holds photos, then "Scan now".
  2. People: name the face groups. Timeline: pick people, set ages.
  3. Duplicates, Memories (slideshows, collages), Albums, Organize.

Moving or backing up: close the app and copy this whole folder.
Removing: close the app and delete this folder. (Files you exported to other
folders, and Google Photos imports, stay where you saved them.)

Requirements: Windows 10 or 11 (64-bit) with Microsoft Edge WebView2
(built into Windows 11 and current Windows 10).

Included third-party software
  - Python $((& (Join-Path $py "python.exe") -c "import sys; print(sys.version.split()[0])")) (PSF licence) and packages (see runtime\python\Lib\site-packages)
  - FFmpeg (bin\) - GPL build from gyan.dev, used for videos and slideshows
  - Face models from the OpenCV model zoo: YuNet (MIT), SFace (Apache-2.0)
"@ | Set-Content (Join-Path $out "README.txt") -Encoding utf8

$size = [math]::Round(((Get-ChildItem $out -Recurse -File | Measure-Object Length -Sum).Sum / 1MB))
Write-Host "Folder ready: $out ($size MB)"

if (-not $NoZip) {
    $zip = Join-Path $outRoot "PhotoExplorer-$version-windows-portable.zip"
    if (Test-Path $zip) { Remove-Item $zip -Force }
    Write-Host "== Zipping"
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [IO.Compression.ZipFile]::CreateFromDirectory($out, $zip, [IO.Compression.CompressionLevel]::Optimal, $true)
    Write-Host ("Zip ready: {0} ({1} MB)" -f $zip, [math]::Round((Get-Item $zip).Length / 1MB))

    # Starter: just the exe; on first start it downloads the portable zip above
    # from the newest GitHub release (app\src-tauri\src\setup.rs).
    $starter = Join-Path $outRoot "PhotoExplorer-$version-windows-starter.zip"
    if (Test-Path $starter) { Remove-Item $starter -Force }
    $z = [IO.Compression.ZipFile]::Open($starter, [IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($f in "PhotoExplorer.exe", "WebView2Loader.dll", "README.txt") {
            [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($z, (Join-Path $out $f), "PhotoExplorer/$f",
                [IO.Compression.CompressionLevel]::Optimal) | Out-Null
        }
    } finally { $z.Dispose() }
    Write-Host ("Starter ready: {0} ({1} MB)" -f $starter, [math]::Round((Get-Item $starter).Length / 1MB, 1))
}
