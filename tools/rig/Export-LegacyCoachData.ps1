<#
.SYNOPSIS
Copies the legacy iRacing Coach user data that D12 converts into a staging folder on the sim PC.

.DESCRIPTION
[sim: blocked] Runs on DESKTOP-SIM through the remote tools once Joshua lifts the sim-PC pause
in writing (workstreams.md, D12 migration step 1). Read-only on the source: it copies, then
hashes source and copy and records both in manifest.json, written last. The staging folder is
then brought to the dev PC with the remote tools and inventoried there with
tools/migrate/inventory_legacy.py. Only the copies are opened.

Never copies or reads data\auth. Refuses to run while the old desktop app is running, to
write into the Coach root or under Documents\iRacing, or to reuse a non-empty destination.

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File Export-LegacyCoachData.ps1 -Destination C:\Users\joshu\coach-migration\legacy-copy
#>
[CmdletBinding()]
param(
    [string]$CoachRoot = (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'iRacing Coach'),
    [Parameter(Mandatory = $true)][string]$Destination
)

Set-StrictMode -Version 3
$ErrorActionPreference = 'Stop'

# Same list as tools/migrate/inventory_legacy.py USER_AUTHORED.
$Items = @(
    'settings.json',
    'data\history.sqlite3',
    'data\history.sqlite3-wal',
    'data\history.sqlite3-shm',
    'portable-settings',
    'user-library',
    'data\tuning',
    'data\tuning-experiments',
    'data\activity-history',
    'data\test-artifacts',
    'data\strategy-history',
    'data\setup-history',
    'data\target-laps'
)

function Test-Inside([string]$Child, [string]$Parent) {
    $c = [IO.Path]::GetFullPath($Child).TrimEnd('\') + '\'
    $p = [IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
    return $c.StartsWith($p, [StringComparison]::OrdinalIgnoreCase)
}

$CoachRoot = [IO.Path]::GetFullPath($CoachRoot)
$Destination = [IO.Path]::GetFullPath($Destination)
$IRacingRoot = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'iRacing'

if (-not (Test-Path -LiteralPath $CoachRoot -PathType Container)) { throw "Coach root not found: $CoachRoot" }
if (Test-Inside $Destination $CoachRoot) { throw 'The destination must be outside the Coach root.' }
if (Test-Inside $Destination $IRacingRoot) { throw 'The destination must be outside Documents\iRacing.' }
if ((Test-Path -LiteralPath $Destination) -and (Get-ChildItem -LiteralPath $Destination -Force | Select-Object -First 1)) {
    throw "The destination is not empty: $Destination"
}
if (Get-Process -Name 'iRacing Coach' -ErrorAction SilentlyContinue) {
    throw 'The old iRacing Coach app is running; close it so history.sqlite3 is not being written.'
}

$files = New-Object System.Collections.Generic.List[string]
foreach ($item in $Items) {
    if ($item -like 'data\auth*') { throw 'data\auth is never copied.' }
    $source = Join-Path $CoachRoot $item
    if (Test-Path -LiteralPath $source -PathType Leaf) {
        $files.Add($source)
    } elseif (Test-Path -LiteralPath $source -PathType Container) {
        Get-ChildItem -LiteralPath $source -Recurse -File -Force | ForEach-Object { $files.Add($_.FullName) }
    }
}

New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$entries = New-Object System.Collections.Generic.List[object]
foreach ($source in $files) {
    $relative = $source.Substring($CoachRoot.TrimEnd('\').Length + 1)
    if ($relative -like 'data\auth\*') { continue }
    $target = Join-Path $Destination $relative
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
    Copy-Item -LiteralPath $source -Destination $target
    $sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
    $copyHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
    $entries.Add([ordered]@{
        path = $relative.Replace('\', '/')
        bytes = (Get-Item -LiteralPath $source).Length
        modified_utc = (Get-Item -LiteralPath $source).LastWriteTimeUtc.ToString('yyyy-MM-ddTHH:mm:ssZ')
        sha256 = $copyHash
        source_sha256 = $sourceHash
        equal = ($sourceHash -eq $copyHash)
    })
}

$unequal = @($entries | Where-Object { -not $_.equal })
$manifest = [ordered]@{
    format = 'iracing-coach-legacy-export 1'
    machine = $env:COMPUTERNAME
    coach_root = $CoachRoot
    created_utc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    items = $Items
    files = $entries
    unequal = $unequal.Count
}
$manifestPath = Join-Path $Destination 'manifest.json'
[IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 6), (New-Object Text.UTF8Encoding($false)))
Write-Output "$($entries.Count) files copied to $Destination; $($unequal.Count) hash mismatches"
if ($unequal.Count -gt 0) { exit 1 }
