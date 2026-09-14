$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
function New-CampusSecret {
    $bytes = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return ([BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
}
if (-not (Test-Path '.env')) {
    $lines = @('APP_ORIGIN=http://localhost:8080', 'HTTP_PORT=8080', 'BIND_ADDRESS=127.0.0.1', 'COOKIE_SECURE=false')
    foreach ($key in @('INTERNAL_TOKEN','SETUP_KEY','AUTH_DB_PASSWORD','SCHEDULE_DB_PASSWORD','QUEUE_DB_PASSWORD')) {
        $lines += $key + '=' + (New-CampusSecret)
    }
    [IO.File]::WriteAllText((Join-Path (Get-Location) '.env'), ($lines -join "`n") + "`n", (New-Object Text.UTF8Encoding($false)))
    Write-Host 'Created .env with unique keys. Keep this file with your backups.'
}
docker compose version
if ($LASTEXITCODE -ne 0) { throw 'Install/start Docker Desktop with Linux containers, then try again.' }
docker compose up --build -d --wait --wait-timeout 180
if ($LASTEXITCODE -ne 0) { throw 'Startup failed. Run: docker compose logs --tail 100' }
Write-Host 'Open the APP_ORIGIN address from .env (default http://localhost:8080).'
Write-Host 'For the first administrator account, copy SETUP_KEY from .env into the first-run form.'
