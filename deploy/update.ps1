# Rebuild the image from local source and restart the production stack.
# Use this for development or when GHCR is unavailable; for the CI-built image
# use deploy\pull-update.ps1 instead.
#   powershell -ExecutionPolicy Bypass -File deploy\update.ps1
#   powershell -ExecutionPolicy Bypass -File deploy\update.ps1 -NoPull
param(
    [switch]$NoPull,
    [int]$HealthTimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $Root

if (-not $NoPull) {
    $branch = (git rev-parse --abbrev-ref HEAD).Trim()
    if ($LASTEXITCODE -ne 0) { throw "git is not available" }
    if ($branch -ne "main") { throw "Current branch is '$branch'; switch to main or pass -NoPull." }
    if (git status --porcelain) { throw "Working tree is not clean; commit or stash first." }
    git pull --ff-only
    if ($LASTEXITCODE -ne 0) { throw "git pull --ff-only failed" }
}

$deadline = (Get-Date).AddMinutes(3)
while ($true) {
    docker info --format "{{.ServerVersion}}" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { break }
    if ((Get-Date) -gt $deadline) { throw "Docker is not ready after 3 minutes." }
    Start-Sleep -Seconds 5
}

# 源码构建使用独立 tag，避免覆盖 GHCR 的 :latest 造成误判
$env:ASAGENT_IMAGE = "asagent-api:local"

docker compose -f deploy\docker-compose.yml build
if ($LASTEXITCODE -ne 0) { throw "Image build failed" }

docker compose -f deploy\docker-compose.yml up -d
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

docker compose -f deploy\docker-compose.yml ps
Write-Host "Update complete: http://127.0.0.1:8080/api/health is ok"
