# Install cloudflared as a Windows service using runtime\cloudflared-asagent.yml.
# Run from an elevated PowerShell. Credentials stay in the config file and are
# never echoed by this script.
$ErrorActionPreference = "Stop"

$Principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated PowerShell (Run as administrator)."
}

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Config = Join-Path $Root "runtime\cloudflared-asagent.yml"
if (-not (Test-Path $Config)) { throw "Config not found: $Config" }

$Candidates = @(
    (Get-Command cloudflared -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source),
    (Join-Path $env:ProgramFiles "cloudflared\cloudflared.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "cloudflared\cloudflared.exe"),
    "D:\cloudflared\cloudflared.exe"
) | Where-Object { $_ -and (Test-Path $_) }

if (-not $Candidates) { throw "cloudflared.exe not found; install it or add it to PATH." }
$Exe = $Candidates[0]

& $Exe --config $Config service install
if ($LASTEXITCODE -ne 0) {
    throw "cloudflared service install failed. Ensure the credentials file in the config is readable by SYSTEM."
}

Start-Service cloudflared
Get-Service cloudflared | Format-List Name, Status, StartType
Write-Host "cloudflared service configured with $Config"
