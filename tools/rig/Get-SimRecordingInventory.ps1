<#
.SYNOPSIS
Lists every raw recording and setup file on the sim PC that D12 re-ingests, optionally with SHA-256.

.DESCRIPTION
[sim: blocked] Runs on DESKTOP-SIM through the remote tools once Joshua lifts the sim-PC pause
in writing (workstreams.md, D12 migration steps 3 and 5). Read-only: it lists
Documents\iRacing\telemetry (*.ibt), the old app's archived raw recordings
(Documents\iRacing Coach\data\telemetry-traces\raw) and Documents\iRacing\setups (*.sto, *.htm).
With -Hash it reads each recording at below-normal priority, only while the iRacing simulator is
not running, and stops if it starts. The JSON it writes is the legacy hash list the migration
completeness report checks against NAS manifests (nas-layout.md section 4).

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File Get-SimRecordingInventory.ps1 -OutFile C:\Users\joshu\coach-migration\recordings.json -Hash
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$OutFile,
    [switch]$Hash,
    [string]$IRacingRoot = (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'iRacing'),
    [string]$CoachRoot = (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'iRacing Coach')
)

Set-StrictMode -Version 3
$ErrorActionPreference = 'Stop'

$SimProcess = 'iRacingSim64DX11'

function Test-Inside([string]$Child, [string]$Parent) {
    $c = [IO.Path]::GetFullPath($Child).TrimEnd('\') + '\'
    $p = [IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
    return $c.StartsWith($p, [StringComparison]::OrdinalIgnoreCase)
}

function Get-Listing([string]$Root, [string[]]$Patterns, [string]$Kind) {
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) { return @() }
    $rootFull = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    Get-ChildItem -LiteralPath $Root -Recurse -File -Force -Include $Patterns | Sort-Object FullName | ForEach-Object {
        [ordered]@{
            kind = $Kind
            path = $_.FullName.Substring($rootFull.Length + 1).Replace('\', '/')
            bytes = $_.Length
            modified_utc = $_.LastWriteTimeUtc.ToString('yyyy-MM-ddTHH:mm:ssZ')
            sha256 = $null
        }
    }
}

function Get-ByteSum([object[]]$Entries) {
    [long]$sum = 0
    foreach ($entry in $Entries) { $sum += $entry.bytes }
    return $sum
}

$OutFile = [IO.Path]::GetFullPath($OutFile)
if ((Test-Inside $OutFile $IRacingRoot) -or (Test-Inside $OutFile $CoachRoot)) {
    throw 'Write the inventory outside Documents\iRacing and the Coach root.'
}

$telemetry = @(Get-Listing (Join-Path $IRacingRoot 'telemetry') @('*.ibt') 'telemetry')
$archived = @(Get-Listing (Join-Path $CoachRoot 'data\telemetry-traces\raw') @('*.ibt') 'coach_raw_archive')
$setups = @(Get-Listing (Join-Path $IRacingRoot 'setups') @('*.sto', '*.htm') 'setup')

$roots = @{
    telemetry = Join-Path $IRacingRoot 'telemetry'
    coach_raw_archive = Join-Path $CoachRoot 'data\telemetry-traces\raw'
    setup = Join-Path $IRacingRoot 'setups'
}

$stopped = $null
if ($Hash) {
    [Diagnostics.Process]::GetCurrentProcess().PriorityClass = 'BelowNormal'
    foreach ($entry in @($telemetry + $archived + $setups)) {
        if (Get-Process -Name $SimProcess -ErrorAction SilentlyContinue) {
            $stopped = "$SimProcess started; hashing stopped before $($entry.path)"
            break
        }
        $full = Join-Path $roots[$entry.kind] ($entry.path.Replace('/', '\'))
        $entry.sha256 = (Get-FileHash -LiteralPath $full -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}

$report = [ordered]@{
    format = 'iracing-coach-sim-recordings 1'
    machine = $env:COMPUTERNAME
    created_utc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    roots = $roots
    hashed = [bool]$Hash
    hashing_stopped = $stopped
    counts = [ordered]@{
        telemetry = $telemetry.Count
        telemetry_bytes = (Get-ByteSum $telemetry)
        coach_raw_archive = $archived.Count
        coach_raw_archive_bytes = (Get-ByteSum $archived)
        setup = $setups.Count
    }
    files = @($telemetry + $archived + $setups)
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutFile) | Out-Null
[IO.File]::WriteAllText($OutFile, ($report | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding($false)))
Write-Output ("{0} telemetry, {1} archived raw, {2} setup files -> {3}" -f $telemetry.Count, $archived.Count, $setups.Count, $OutFile)
if ($stopped) { Write-Warning $stopped; exit 3 }
