# Daily backup task entry point (wraps scripts/backup.py and appends to a log).
# uv writes progress to stderr; Stop would turn it into a terminating error in
# PowerShell 5.1, so use Continue and check $LASTEXITCODE explicitly.
$ErrorActionPreference = "Continue"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Root

$Uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $Uv) { throw "uv not found in PATH; install uv before registering this task" }
$env:PYTHONIOENCODING = "utf-8"
# PowerShell 5.1 decodes native stdout with the OEM code page by default, which
# turns the UTF-8 Chinese output into mojibake in the log.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$LogDir = Join-Path $Root "backups"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir "backup.log"

$Started = Get-Date -Format "s"
$Output = & $Uv.Source run python scripts\backup.py --keep 7 2>&1 | Out-String
$Code = $LASTEXITCODE
$Entry = "[$Started] exit=$Code`n$($Output.TrimEnd())"
Add-Content -Path $Log -Value $Entry -Encoding UTF8
exit $Code
