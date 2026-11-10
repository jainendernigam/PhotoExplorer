<#
Downloads FFmpeg (gyan.dev "release essentials" build for Windows, linked from
ffmpeg.org) and keeps only ffmpeg.exe and ffprobe.exe in .\bin. No admin.

    powershell -ExecutionPolicy Bypass -File scripts\get_ffmpeg.ps1

Licence: this build is GPL (it includes x264). Fine for personal use.
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$bin  = Join-Path $root "bin"
$tmp  = Join-Path ([IO.Path]::GetTempPath()) ("pe-ffmpeg-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force $bin, $tmp | Out-Null
try {
    $zip = Join-Path $tmp "ffmpeg.zip"
    Write-Host "Downloading FFmpeg..."
    Invoke-WebRequest "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile $zip -UseBasicParsing
    Expand-Archive $zip (Join-Path $tmp "x") -Force
    foreach ($exe in "ffmpeg.exe", "ffprobe.exe") {
        $f = Get-ChildItem (Join-Path $tmp "x") -Recurse -Filter $exe | Select-Object -First 1
        if (-not $f) { throw "$exe not found in the download" }
        Copy-Item $f.FullName (Join-Path $bin $exe) -Force
    }
    & (Join-Path $bin "ffmpeg.exe") -hide_banner -version | Select-Object -First 1
    Write-Host "FFmpeg ready in $bin"
} finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
