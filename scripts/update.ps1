$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$repo = 'https://github.com/GhostR11709/UniversalDownload'
if (Test-Path (Join-Path $root '.git')) { git -C $root pull --ff-only; exit $LASTEXITCODE }
$temp = Join-Path ([System.IO.Path]::GetTempPath()) ("UniversalDownload-update-" + [guid]::NewGuid().ToString('N'))
$zip = "$temp.zip"
try {
  Invoke-WebRequest -Uri "$repo/archive/refs/heads/main.zip" -OutFile $zip
  Expand-Archive -LiteralPath $zip -DestinationPath $temp -Force
  $source = Get-ChildItem -Path $temp -Directory | Select-Object -First 1
  if (-not $source) { throw 'GitHub archive was empty.' }
  $keep = @('.venv', 'temp', 'logs', 'tools', '.env')
  Get-ChildItem -LiteralPath $source.FullName -Force | Where-Object { $keep -notcontains $_.Name } | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $root $_.Name) -Recurse -Force }
  Write-Host 'GitHub files copied. Your environment, temp files, logs, tools, and .env were preserved.'
} finally {
  Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
}
