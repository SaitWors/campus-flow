$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
. (Join-Path $PSScriptRoot 'backup_io.ps1')
$backupDir = Join-Path 'backups' ([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $backupDir | Out-Null
$backupAbs = (Resolve-Path $backupDir).Path
Copy-Item '.env' (Join-Path $backupDir 'config.env')
$config = & docker compose config --format json
if ($LASTEXITCODE -ne 0) { throw 'Could not validate Compose configuration.' }
[System.IO.File]::WriteAllText((Join-Path $backupAbs 'effective-config.json'), ($config -join "`n"), (New-Object System.Text.UTF8Encoding($false)))
if ($env:OS -ne 'Windows_NT') { & chmod 700 $backupAbs; & chmod 600 (Join-Path $backupAbs 'config.env') (Join-Path $backupAbs 'effective-config.json') }
$active = @(& docker compose ps --services --filter status=running | Where-Object { $_ -in @('auth','schedule','notifications','web') })
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect running application services.' }
& docker compose stop web notifications schedule auth
if ($LASTEXITCODE -ne 0) { throw 'Could not stop application services. Backup cancelled.' }
try {
    $schemas = @(); $scheduleSchema = 0
    foreach ($service in @('auth','schedule','notifications')) {
        $dbService = $service + '-db'
        $version = (& docker compose exec -T $dbService psql -X -At -U $service -d $service -c 'SELECT MAX(version) FROM schema_migrations;') -join ''
        if ($LASTEXITCODE -ne 0 -or $version -notmatch '^\d+$') { throw ('Invalid schema: ' + $service) }
        $schemas += $service + ':' + $version
        if ($service -eq 'schedule') { $scheduleSchema = [int]$version }
        & docker compose exec -T $dbService pg_dump -U $service -d $service -Fc -f /tmp/campus-backup.dump
        if ($LASTEXITCODE -ne 0) { throw ('Dump failed: ' + $service) }
        & docker compose cp ($dbService + ':/tmp/campus-backup.dump') (Join-Path $backupDir ($service + '.dump'))
        if ($LASTEXITCODE -ne 0) { throw ('Copy failed: ' + $service) }
        & docker compose exec -T $dbService rm -f /tmp/campus-backup.dump
    }
    Invoke-DockerBinary -Arguments @('compose','run','--rm','--no-deps','-T','schedule','python','-m','scripts.materials_archive','create','/var/lib/campus/materials') -OutputPath (Join-Path $backupAbs 'materials.tar')
    $metadata = '[]'
    if ($scheduleSchema -ge 5) {
        $metadata = (& docker compose exec -T schedule-db psql -X -At -U schedule -d schedule -c "SELECT COALESCE(json_agg(json_build_object('id', id, 'bytes', size_bytes, 'sha256', sha256)), '[]') FROM materials;") -join "`n"
        if ($LASTEXITCODE -ne 0) { throw 'Could not inspect material metadata.' }
    }
    $userArgs = @(Get-BackupUserArgs)
    $metadata | & docker compose run --rm --no-deps -T @userArgs --volume ($backupAbs + ':/backup') schedule python -m scripts.materials_archive finish-backup /backup --schemas ($schemas -join ',') --metadata-stdin
    if ($LASTEXITCODE -ne 0) { throw 'Backup verification failed.' }
    if ($env:OS -ne 'Windows_NT') { Get-ChildItem $backupAbs -File | ForEach-Object { & chmod 600 $_.FullName } }
    Write-Host ('Backup completed: ' + $backupDir)
    Write-Host 'Keep the entire folder private; it contains accounts, material files and server keys.'
} finally {
    if ($active.Count -gt 0) {
        & docker compose start @active
        if ($LASTEXITCODE -ne 0) { Write-Warning 'Run docker compose up -d to restart the application.' }
    }
}
