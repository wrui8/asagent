# Pull the CI-built image from GHCR and restart the production stack.
#   powershell -ExecutionPolicy Bypass -File deploy\pull-update.ps1
# Pin a version or roll back with a sha tag:
#   powershell -ExecutionPolicy Bypass -File deploy\pull-update.ps1 -Image ghcr.io/wrui8/asagent:sha-abc1234
param(
    [string]$Image = "ghcr.io/wrui8/asagent:latest",
    [int]$HealthTimeoutSeconds = 90
)

# docker writes normal errors (e.g. "No such image") to stderr; Stop would turn
# them into terminating errors in PowerShell 5.1, so use Continue and check codes.
$ErrorActionPreference = "Continue"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root
$Compose = "deploy\docker-compose.yml"

function Get-ImageId([string]$Ref) {
    $id = docker image inspect --format "{{.Id}}" $Ref 2>$null
    if ($LASTEXITCODE -ne 0) { return "" }
    return ([string]($id | Select-Object -First 1)).Trim()
}

# Wait for Docker Desktop (up to 3 minutes).
$deadline = (Get-Date).AddMinutes(3)
while ($true) {
    docker info --format "{{.ServerVersion}}" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { break }
    if ((Get-Date) -gt $deadline) { throw "Docker is not ready after 3 minutes." }
    Start-Sleep -Seconds 5
}

$env:ASAGENT_IMAGE = $Image
$before = Get-ImageId $Image
$hadBefore = [bool]$before

$pullOutput = docker compose -f $Compose pull api 2>&1
if ($LASTEXITCODE -ne 0) {
    throw ("Image pull failed: " + ($pullOutput -join " ") +
        "`nIf the package is private, run 'docker login ghcr.io' with a token that has read:packages, or make the package public.")
}

$after = Get-ImageId $Image
if ($hadBefore -and $before -eq $after) {
    $running = (docker ps --filter "name=asagent-api" --format "{{.Names}}")
    if ($running -eq "asagent-api") {
        Write-Host "Already up to date: $Image"
        exit 0
    }
}

docker compose -f $Compose up -d
if ($LASTEXITCODE -ne 0) { throw "Container update failed" }

$healthy = $false
$deadline = (Get-Date).AddSeconds($HealthTimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/health" -TimeoutSec 3
        if ($health.status -eq "ok") { $healthy = $true; break }
    } catch {
        # container may still be starting
    }
}
if (-not $healthy) {
    throw "Health check failed; inspect with: docker logs asagent-api"
}

docker compose -f $Compose ps
Write-Host "Update complete: $Image -> http://127.0.0.1:8080/api/health is ok"
