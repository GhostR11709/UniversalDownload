$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$build = Join-Path $root 'build\ud-server'
$dist = Join-Path $build 'dist'
$work = Join-Path $build 'work'
$spec = Join-Path $build 'spec'
$output = Join-Path $dist 'UD Server.exe'

if (-not (Test-Path -LiteralPath $python)) {
    throw "The project virtual environment is missing: $python"
}

New-Item -ItemType Directory -Force -Path $build | Out-Null
if (Test-Path -LiteralPath $dist) { Remove-Item -LiteralPath $dist -Recurse -Force }
if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force }
if (Test-Path -LiteralPath $spec) { Remove-Item -LiteralPath $spec -Recurse -Force }

Write-Host 'Building named background process: UD Server.exe'
& $python -m PyInstaller --noconfirm --clean --onefile --noconsole `
    --name 'UD Server' --paths $root --distpath $dist --workpath $work --specpath $spec `
    --hidden-import server.__main__ --hidden-import server.app `
    --hidden-import core.config --hidden-import core.downloader --hidden-import core.jobs `
    --hidden-import core.media --hidden-import core.platforms --hidden-import pystray `
    --collect-all yt_dlp --collect-all yt_dlp_ejs `
    (Join-Path $root 'server\launcher.py')

if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $output)) {
    throw 'PyInstaller did not create UD Server.exe.'
}

Copy-Item -LiteralPath $output -Destination (Join-Path $root 'UD Server.exe') -Force
Write-Host 'UD Server.exe is ready.'
