# Start the asagent API container (waits for Docker Desktop to become ready).
$ErrorActionPreference = "Stop"

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Root

$Deadline = (Get-Date).AddMinutes(3)
while ($true) {
    $ready = $false
    try {
        $version = docker info --format "{{.ServerVersion}}" 2>$null
        if ($LASTEXITCODE -eq 0 -and $version) { $ready = $true }
    } catch {
        $ready = $false
    }
    if ($ready) { break }
    if ((Get-Date) -gt $Deadline) {
        throw "Docker is not ready after 3 minutes. Start Docker Desktop and retry."
    }
    Start-Sleep -Seconds 5
}

docker compose -f deploy\docker-compose.yml up -d
if ($LASTEXITCODE -ne 0) { throw "docker compose up failed" }
docker compose -f deploy\docker-compose.yml ps
