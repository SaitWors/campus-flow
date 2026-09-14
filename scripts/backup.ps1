$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$backupDir = Join-Path 'backups' ([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'))
New-Item -ItemType Directory -Path $backupDir | Out-Null
Copy-Item '.env' (Join-Path $backupDir 'config.env')
docker compose stop web notifications queue schedule auth
if ($LASTEXITCODE -ne 0) { throw 'Could not stop application services. Backup cancelled.' }
try {
    foreach ($service in @('auth','schedule','queue','notifications')) {
        $dbService = $service + '-db'
        docker compose exec -T $dbService pg_dump -U $service -d $service -Fc -f /tmp/campus-backup.dump
        if ($LASTEXITCODE -ne 0) { throw "Dump failed: $service" }
        docker compose cp ($dbService + ':/tmp/campus-backup.dump') (Join-Path $backupDir ($service + '.dump'))
        if ($LASTEXITCODE -ne 0) { throw "Copy failed: $service" }
        docker compose exec -T $dbService rm -f /tmp/campus-backup.dump
    }
    New-Item -ItemType File -Path (Join-Path $backupDir 'COMPLETE') | Out-Null
    Write-Host "Backup completed: $backupDir"
    Write-Host 'Keep the entire folder private; it contains accounts and server keys.'
} finally {
    docker compose start auth schedule queue notifications web
    if ($LASTEXITCODE -ne 0) { Write-Warning 'Run docker compose up -d to restart the application.' }
}
