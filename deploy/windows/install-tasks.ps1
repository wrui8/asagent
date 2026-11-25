# Register Windows scheduled tasks (run from an elevated PowerShell):
#   - ASAgentStack     : start docker compose at logon
#   - ASAgentBackup    : daily backup at 03:17, keep 7 days
#   - ASAgentMemoryNightly : optional (-WithMemoryNightly), daily 03:47,
#     runs the stage-4 memory consolidation inside the memory-worker image.
# Image updates are manual: run deploy\pull-update.ps1 after CI publishes a new image.
param([switch]$WithMemoryNightly)

$ErrorActionPreference = "Stop"

$Principal = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated PowerShell (Run as administrator)."
}

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$PowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$User = [Security.Principal.WindowsIdentity]::GetCurrent().Name

$StackAction = New-ScheduledTaskAction -Execute $PowerShell `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSScriptRoot\start-stack.ps1`"" `
    -WorkingDirectory $Root
$StackTrigger = New-ScheduledTaskTrigger -AtLogOn -User $User
$StackSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$StackPrincipal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "ASAgentStack" -Action $StackAction -Trigger $StackTrigger `
    -Settings $StackSettings -Principal $StackPrincipal -Force | Out-Null

$BackupAction = New-ScheduledTaskAction -Execute $PowerShell `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSScriptRoot\backup-task.ps1`"" `
    -WorkingDirectory $Root
$BackupTrigger = New-ScheduledTaskTrigger -Daily -At "03:17"
$BackupSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$BackupPrincipal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "ASAgentBackup" -Action $BackupAction -Trigger $BackupTrigger `
    -Settings $BackupSettings -Principal $BackupPrincipal -Force | Out-Null

# 阶段 4：记忆夜间巩固（仅在启用记忆子系统时注册）
if ($WithMemoryNightly) {
    $Docker = (Get-Command docker.exe -ErrorAction SilentlyContinue).Source
    if (-not $Docker) {
        $Docker = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    }
    $NightlyAction = New-ScheduledTaskAction -Execute $Docker `
        -Argument "compose -f deploy/docker-compose.yml --profile memory run --rm memory-worker /app/.venv/bin/asagent worker memory --nightly" `
        -WorkingDirectory $Root
    $NightlyTrigger = New-ScheduledTaskTrigger -Daily -At "03:47"
    $NightlySettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $NightlyPrincipal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName "ASAgentMemoryNightly" -Action $NightlyAction `
        -Trigger $NightlyTrigger -Settings $NightlySettings -Principal $NightlyPrincipal -Force | Out-Null
    Write-Host "Registered task ASAgentMemoryNightly (daily 03:47)."
}

# Remove the legacy auto-update task if a previous version registered it.
if (Get-ScheduledTask -TaskName "ASAgentAutoUpdate" -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName "ASAgentAutoUpdate" -Confirm:$false
    Write-Host "Removed legacy task ASAgentAutoUpdate (updates are manual now)."
}

Write-Host "Registered tasks: ASAgentStack (at logon), ASAgentBackup (daily 03:17)."
