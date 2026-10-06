$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$bin = Join-Path $root 'tools\ffmpeg\bin'
$ffmpeg = Join-Path $bin 'ffmpeg.exe'
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) { Write-Host 'FFmpeg found on PATH.'; exit 0 }
if (Test-Path $ffmpeg) { Write-Host 'FFmpeg already installed in tools\ffmpeg.'; exit 0 }
Write-Host 'FFmpeg was not found. Downloading the current Windows essentials build...'
$tools = Join-Path $root 'tools'
$archive = Join-Path $tools 'ffmpeg-release-essentials.zip'
$extract = Join-Path $tools 'ffmpeg-extract'
New-Item -ItemType Directory -Force -Path $tools | Out-Null
Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile $archive
if (Test-Path $extract) { Remove-Item -Recurse -Force $extract }
Expand-Archive -LiteralPath $archive -DestinationPath $extract -Force
$found = Get-ChildItem -Path $extract -Filter ffmpeg.exe -Recurse | Select-Object -First 1
if (-not $found) { throw 'The FFmpeg archive did not contain ffmpeg.exe.' }
New-Item -ItemType Directory -Force -Path $bin | Out-Null
Copy-Item -Path (Join-Path $found.Directory.FullName '*') -Destination $bin -Recurse -Force
Remove-Item -Recurse -Force $extract, $archive
Write-Host 'FFmpeg installed in tools\ffmpeg\bin.'
