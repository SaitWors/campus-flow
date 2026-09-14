param([Parameter(Mandatory=$true)][string]$BackupPath)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
foreach ($file in @('COMPLETE','auth.dump','schedule.dump','queue.dump')) {
    if (-not (Test-Path (Join-Path $BackupPath $file))) { throw "Missing backup file: $file" }
}
Write-Host 'This replaces ALL current accounts, schedule and queues with the selected backup.'
if ((Read-Host 'Type RESTORE to continue') -cne 'RESTORE') { exit 1 }
docker compose stop web queue schedule auth
if ($LASTEXITCODE -ne 0) { throw 'Could not stop application services. Restore cancelled.' }
Write-Host 'Application stays stopped if restoration fails. Restore all three databases before restarting.'
foreach ($service in @('auth','schedule','queue')) {
    $dbService = $service + '-db'
    docker compose cp (Join-Path $BackupPath ($service + '.dump')) ($dbService + ':/tmp/campus-restore.dump')
    if ($LASTEXITCODE -ne 0) { throw "Copy failed: $service" }
    docker compose exec -T $dbService pg_restore -U $service -d $service --clean --if-exists --no-owner --exit-on-error /tmp/campus-restore.dump
    if ($LASTEXITCODE -ne 0) { throw "Restore failed: $service" }
    docker compose exec -T $dbService rm -f /tmp/campus-restore.dump
}
docker compose up -d --wait --wait-timeout 180
if ($LASTEXITCODE -ne 0) { throw 'Databases restored, but application startup failed. Check docker compose logs.' }
Write-Host 'Restoration completed.'
