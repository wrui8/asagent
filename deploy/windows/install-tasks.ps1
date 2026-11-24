# Register Windows scheduled tasks (run from an elevated PowerShell):
#   - ASAgentStack     : start docker compose at logon
#   - ASAgentBackup    : daily backup at 03:17, keep 7 days
#   - ASAgentAutoUpdate: poll GHCR every 10 minutes, restart only on new image
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

$AutoUpdateAction = New-ScheduledTaskAction -Execute $PowerShell `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSScriptRoot\auto-update.ps1`"" `
    -WorkingDirectory $Root
$AutoUpdateTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
    -RepetitionInterval (New-TimeSpan -Minutes 10) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$AutoUpdateSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
$AutoUpdatePrincipal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "ASAgentAutoUpdate" -Action $AutoUpdateAction -Trigger $AutoUpdateTrigger `
    -Settings $AutoUpdateSettings -Principal $AutoUpdatePrincipal -Force | Out-Null

Write-Host "Registered tasks: ASAgentStack (at logon), ASAgentBackup (daily 03:17), ASAgentAutoUpdate (every 10 minutes)."
