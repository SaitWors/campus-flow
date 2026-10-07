# Binary Docker streams bypass PowerShell's text pipeline (including Windows
# PowerShell 5.1). Material archives are copied in chunks directly to/from disk.
function Invoke-DockerBinary {
    param([string[]]$Arguments, [string]$OutputPath, [string]$InputPath, [string]$Header)
    foreach ($argument in $Arguments) {
        if ($argument -notmatch '^[A-Za-z0-9/_.:-]+$') { throw 'Invalid binary helper argument.' }
    }
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = 'docker'; $info.Arguments = $Arguments -join ' '
    $info.UseShellExecute = $false
    $info.RedirectStandardInput = $true; $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process; $process.StartInfo = $info
    $output = $null; $source = $null
    try {
        if (-not $process.Start()) { throw 'Could not start Docker binary helper.' }
        $errors = $process.StandardError.ReadToEndAsync()
        if (-not $OutputPath) { $response = $process.StandardOutput.ReadToEndAsync() }
        if ($Header) {
            $bytes = [System.Text.Encoding]::UTF8.GetBytes($Header + "`n")
            $process.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length)
        }
        if ($InputPath) {
            $source = [System.IO.File]::OpenRead($InputPath)
            $source.CopyTo($process.StandardInput.BaseStream, 1048576)
            $source.Close(); $source = $null
        }
        $process.StandardInput.Close()
        if ($OutputPath) {
            $output = [System.IO.File]::Open($OutputPath, [System.IO.FileMode]::CreateNew)
            $process.StandardOutput.BaseStream.CopyTo($output, 1048576)
            $output.Flush(); $output.Close(); $output = $null
        }
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) { throw ('Docker binary helper failed (exit ' + $process.ExitCode + '). Application data was not published.') }
        if (-not $OutputPath) { return $response.Result.Trim() }
    } finally {
        if ($source) { $source.Dispose() }; if ($output) { $output.Dispose() }
        if ($process.Id -and -not $process.HasExited) { $process.Kill(); $process.WaitForExit() }
        $process.Dispose()
    }
}

function Get-BackupUserArgs {
    if ($env:OS -eq 'Windows_NT') { return @() }
    return @('--user', ((& id -u).Trim() + ':' + (& id -g).Trim()))
}

function Get-LegacyBackupArgs {
    param([string]$Folder)
    if (Test-Path (Join-Path $Folder 'manifest.json')) { return @() }
    $schemas = @()
    foreach ($service in @('auth','schedule','notifications')) {
        $schemaOutput = & docker run --rm --network none --volume ($Folder + ':/backup:ro') postgres:17-alpine pg_restore -a -t schema_migrations ('/backup/' + $service + '.dump')
        if ($LASTEXITCODE -ne 0) { throw ('Cannot inspect historical dump: ' + $service) }
        $version = (($schemaOutput | & docker compose run --rm --no-deps -T schedule python -m scripts.materials_archive schema-version) -join '').Trim()
        if ($LASTEXITCODE -ne 0 -or $version -notmatch '^[0-9]+$') { throw ('Invalid historical schema COPY data: ' + $service) }
        $schemas += $service + ':' + $version
    }
    return @('--schemas', ($schemas -join ','))
}
