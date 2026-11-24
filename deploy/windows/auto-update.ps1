# Optional script (NOT registered by install-tasks.ps1 anymore): poll GHCR for a
# new :latest image and restart only when the digest changed. Restarts the
# previous image if the health check fails. Manual alternative: pull-update.ps1.
# Log: runtime\auto-update.log (rotated at 5 MB).
# docker writes normal errors (e.g. "No such image") to stderr; Stop would turn
# them into terminating errors in PowerShell 5.1, so use Continue and check codes.
$ErrorActionPreference = "Continue"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Root

$Image = "ghcr.io/wrui8/asagent:latest"
$RollbackImage = "ghcr.io/wrui8/asagent:rollback"
$Compose = "deploy\docker-compose.yml"
$Log = Join-Path $Root "runtime\auto-update.log"

function Get-ImageId([string]$Ref) {
    $id = docker image inspect --format "{{.Id}}" $Ref 2>$null
    if ($LASTEXITCODE -ne 0) { return "" }
    return ([string]($id | Select-Object -First 1)).Trim()
}

function Write-Log([string]$Message) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    $dir = Split-Path -Parent $Log
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
    if ((Test-Path $Log) -and ((Get-Item $Log).Length -gt 5MB)) {
        Move-Item -Force $Log "$Log.1"
    }
    Add-Content -Path $Log -Value $line -Encoding UTF8
}

function Wait-Docker([int]$Minutes = 3) {
    $deadline = (Get-Date).AddMinutes($Minutes)
    while ($true) {
        docker info --format "{{.ServerVersion}}" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { return $true }
        if ((Get-Date) -gt $deadline) { return $false }
        Start-Sleep -Seconds 5
    }
}

function Test-Health([int]$Seconds = 90) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        try {
            $h = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/health" -TimeoutSec 3
            if ($h.status -eq "ok") { return $true }
        } catch {
            # container may still be starting
        }
    }
    return $false
}

if (-not (Wait-Docker)) {
    Write-Log "ERROR docker daemon not ready, skip"
    exit 1
}

$env:ASAGENT_IMAGE = $Image
$before = Get-ImageId $Image
$hadBefore = [bool]$before

$pullOutput = docker compose -f $Compose pull --quiet api 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Log ("ERROR pull failed: " + ($pullOutput -join " ") +
        " (private package? run: docker login ghcr.io)")
    exit 1
}

$after = Get-ImageId $Image
if (-not $after) {
    Write-Log "ERROR image missing after pull"
    exit 1
}

if ($hadBefore -and $before -eq $after) {
    $running = (docker ps --filter "name=asagent-api" --format "{{.Names}}")
    if ($running -eq "asagent-api") { exit 0 }
    Write-Log "container not running, starting $Image"
} else {
    Write-Log ("new image {0} -> {1}" -f $before, $after)
}

docker compose -f $Compose up -d
if ($LASTEXITCODE -ne 0) {
    Write-Log "ERROR docker compose up failed"
    exit 1
}

if (Test-Health) {
    Write-Log "update ok ($Image)"
    exit 0
}

Write-Log "ERROR health check failed, rolling back"
if ($hadBefore) {
    docker tag $before $RollbackImage 2>&1 | Out-Null
    $env:ASAGENT_IMAGE = $RollbackImage
    docker compose -f $Compose up -d
    if ($LASTEXITCODE -eq 0 -and (Test-Health 60)) {
        Write-Log "rollback ok"
    } else {
        Write-Log "ERROR rollback failed; inspect: docker logs asagent-api"
    }
} else {
    Write-Log "no previous image to roll back to; inspect: docker logs asagent-api"
}
exit 1
