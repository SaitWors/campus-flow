param([Parameter(Mandatory=$true)][string]$BackupPath)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
. (Join-Path $PSScriptRoot 'backup_io.ps1')
$item = Get-Item $BackupPath
if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Backup directory may not be a symlink.' }
$backupAbs = (Resolve-Path $BackupPath).Path
$legacyArgs = @(Get-LegacyBackupArgs $backupAbs); $userArgs = @(Get-BackupUserArgs)
$header = (& docker compose run --rm --no-deps -T --interactive=false @userArgs --volume ($backupAbs + ':/backup:ro') schedule python -m scripts.materials_archive backup-header /backup @legacyArgs) -join "`n"
if ($LASTEXITCODE -ne 0) { throw 'Backup verification failed. No application data was changed.' }
foreach ($service in @('auth','schedule','notifications')) {
    & docker run --rm --network none --volume ($backupAbs + ':/backup:ro') postgres:17-alpine pg_restore --list ('/backup/' + $service + '.dump') | Out-Null
    if ($LASTEXITCODE -ne 0) { throw ('Invalid database dump: ' + $service) }
}
Write-Host 'This replaces ALL current accounts, schedule, notifications and material files with the selected backup.'
if ((Read-Host 'Type RESTORE to continue') -cne 'RESTORE') { exit 1 }
& (Join-Path $PSScriptRoot 'backup.ps1')
& docker compose stop web notifications schedule auth
if ($LASTEXITCODE -ne 0) { throw 'Could not stop application services. Restore cancelled.' }
Write-Host 'Application stays stopped if restoration fails. Restore all databases and files before restarting.'
$stage = $null
try {
    $archive = Join-Path $backupAbs 'materials.tar'
    if (-not (Test-Path $archive)) { $archive = $null }
    $stage = Invoke-DockerBinary -Arguments @('compose','run','--rm','--no-deps','-T','schedule','python','-m','scripts.materials_archive','stage-stream','/var/lib/campus/materials') -InputPath $archive -Header $header
    if ($stage -notmatch '^\.restore-[a-f0-9]{32}$') { throw 'Invalid restore stage.' }
    foreach ($service in @('auth','schedule','notifications')) {
        $dbService = $service + '-db'
        & docker compose cp (Join-Path $backupAbs ($service + '.dump')) ($dbService + ':/tmp/campus-restore.dump')
        if ($LASTEXITCODE -ne 0) { throw ('Copy failed: ' + $service) }
        & docker compose exec -T $dbService psql -X -v ON_ERROR_STOP=1 -U $service -d $service -c ('DROP SCHEMA public CASCADE; CREATE SCHEMA public AUTHORIZATION ' + $service + ';')
        if ($LASTEXITCODE -ne 0) { throw ('Could not clear restore destination: ' + $service) }
        & docker compose exec -T $dbService pg_restore -U $service -d $service --no-owner --exit-on-error /tmp/campus-restore.dump
        if ($LASTEXITCODE -ne 0) { throw ('Restore failed: ' + $service) }
        & docker compose exec -T $dbService rm -f /tmp/campus-restore.dump
    }
    $version = (& docker compose exec -T schedule-db psql -X -At -U schedule -d schedule -c 'SELECT MAX(version) FROM schema_migrations;') -join ''
    if ($LASTEXITCODE -ne 0 -or $version -notmatch '^\d+$') { throw 'Invalid restored schedule schema.' }
    if ([int]$version -ge 5) {
        $metadata = (& docker compose exec -T schedule-db psql -X -At -U schedule -d schedule -c "SELECT COALESCE(json_agg(json_build_object('id', id, 'bytes', size_bytes, 'sha256', sha256)), '[]') FROM materials;") -join "`n"
        if ($LASTEXITCODE -ne 0) { throw 'Could not inspect restored material metadata.' }
        $metadata | & docker compose run --rm --no-deps -T @userArgs --volume ($backupAbs + ':/backup:ro') schedule python -m scripts.materials_archive verify-metadata /backup @legacyArgs
        if ($LASTEXITCODE -ne 0) { throw 'Restored material metadata differs from files.' }
    }
    & docker compose run --rm --no-deps -T schedule python -m scripts.materials_archive publish /var/lib/campus/materials $stage
    if ($LASTEXITCODE -ne 0) { throw 'Could not publish complete material snapshot.' }
    $stage = $null
    & docker compose up -d --wait --wait-timeout 180
    if ($LASTEXITCODE -ne 0) { throw 'Data restored, but application startup failed. Check docker compose logs.' }
    Write-Host 'Restoration completed.'
} finally {
    if ($stage) { & docker compose run --rm --no-deps -T schedule python -m scripts.materials_archive discard /var/lib/campus/materials $stage }
}
