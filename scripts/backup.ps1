param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '..\db_backup'),
    [string]$PgDump = 'C:\Program Files\PostgreSQL\18\bin\pg_dump.exe'
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $PgDump)) { throw 'pg_dump not found; specify -PgDump' }
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$target = Join-Path (Resolve-Path -LiteralPath $OutputDirectory) ('office_desk_gear_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.dump')
# libpq reads PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD. Password is never passed as a CLI argument.
& $PgDump --format=custom --no-owner --no-acl --file=$target
if ($LASTEXITCODE -ne 0) { throw 'Backup failed' }
Write-Output $target
