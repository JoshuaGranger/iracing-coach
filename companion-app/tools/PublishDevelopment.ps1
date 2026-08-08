[CmdletBinding()]
param(
    [string]$OutputDirectory,
    [string]$PythonRuntime,
    [string]$CoachEngineRoot,
    [switch]$Restore,
    [switch]$SelfContained
)

$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$workspaceRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot '..'))
$applicationProject = Join-Path $projectRoot 'src\iRacingCoach.App\iRacingCoach.App.csproj'
$backendSource = Join-Path $workspaceRoot 'iracing-coach'
$developmentRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot 'artifacts\dev'))

[xml]$projectDocument = Get-Content -LiteralPath $applicationProject -Raw
$version = [string]($projectDocument.Project.PropertyGroup.Version | Select-Object -First 1)
if ([string]::IsNullOrWhiteSpace($version)) { $version = 'development' }
$safeVersion = $version -replace '[^A-Za-z0-9._-]', '-'

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $stamp = [DateTimeOffset]::Now.ToString('yyyyMMdd-HHmmss')
    $OutputDirectory = Join-Path $developmentRoot "v$safeVersion-dev-$stamp"
}
$destination = [System.IO.Path]::GetFullPath($OutputDirectory)
$allowedPrefix = $developmentRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
if (-not $destination.StartsWith($allowedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Development output must stay beneath $developmentRoot"
}
if (Test-Path -LiteralPath $destination) {
    throw "Development output already exists; choose a new path so stale files cannot survive: $destination"
}

$installedRoots = @(
    (Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)) 'Programs\iRacing Coach'),
    (Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFiles)) 'iRacing Coach')
)
if ([string]::IsNullOrWhiteSpace($PythonRuntime)) {
    $PythonRuntime = @(
        $installedRoots | ForEach-Object { Join-Path $_ 'python' }
        (Join-Path $projectRoot 'artifacts\runtime-cache\python')
    ) | Where-Object { Test-Path -LiteralPath (Join-Path $_ 'python.exe') } | Select-Object -First 1
}
if ([string]::IsNullOrWhiteSpace($CoachEngineRoot)) {
    $CoachEngineRoot = @(
        $installedRoots | ForEach-Object { Join-Path $_ 'coach-engine' }
        (Join-Path $projectRoot 'artifacts\release\payload\coach-engine')
    ) | Where-Object { Test-Path -LiteralPath (Join-Path $_ 'codex\codex.exe') } | Select-Object -First 1
}

if ([string]::IsNullOrWhiteSpace($PythonRuntime) -or
    -not (Test-Path -LiteralPath (Join-Path $PythonRuntime 'python.exe'))) {
    throw 'A development Python runtime containing python.exe is required.'
}
if ([string]::IsNullOrWhiteSpace($CoachEngineRoot) -or
    -not (Test-Path -LiteralPath (Join-Path $CoachEngineRoot 'codex\codex.exe'))) {
    throw 'A development Coach Engine root containing codex\codex.exe is required.'
}
if (-not (Test-Path -LiteralPath (Join-Path $backendSource 'skills\analyze-iracing-race\scripts\start-mcp.ps1'))) {
    throw "The workspace backend was not found at $backendSource"
}

$publishArguments = @(
    'publish',
    $applicationProject,
    '-c', 'Release',
    '-r', 'win-x64',
    '--self-contained', $SelfContained.IsPresent.ToString().ToLowerInvariant(),
    '-p:PublishSingleFile=false',
    '-p:DebugType=None',
    '-p:DebugSymbols=false',
    '-o', $destination
)
if (-not $Restore) { $publishArguments += '--no-restore' }

& dotnet @publishArguments
if ($LASTEXITCODE -ne 0) { throw 'Development application publish failed.' }

# Development outputs intentionally point at trusted local dependencies. This
# keeps iteration fast without copying or compressing a portable runtime. A
# release package must continue to use BuildRelease.ps1 instead.
New-Item -ItemType Junction -Path (Join-Path $destination 'iracing-coach') -Target $backendSource | Out-Null
New-Item -ItemType Junction -Path (Join-Path $destination 'python') -Target ([System.IO.Path]::GetFullPath($PythonRuntime)) | Out-Null
New-Item -ItemType Junction -Path (Join-Path $destination 'coach-engine') -Target ([System.IO.Path]::GetFullPath($CoachEngineRoot)) | Out-Null

$executable = Join-Path $destination 'iRacing Coach.exe'
if (-not (Test-Path -LiteralPath $executable)) { throw "Published executable was not found: $executable" }

[pscustomobject]@{
    Executable = $executable
    OutputDirectory = $destination
    Version = $version
    SelfContained = $SelfContained.IsPresent
    Backend = $backendSource
    Python = [System.IO.Path]::GetFullPath($PythonRuntime)
    CoachEngine = [System.IO.Path]::GetFullPath($CoachEngineRoot)
}
