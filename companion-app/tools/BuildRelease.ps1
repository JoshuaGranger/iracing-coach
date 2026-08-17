[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,

    [Parameter(Mandatory = $true)]
    [string]$PythonRuntime,

    [Parameter(Mandatory = $true)]
    [string]$CodexRuntime,

    [switch]$IncludePortable
)

$ErrorActionPreference = 'Stop'
$version = '0.16.0'
$pythonVersion = '3.12.13'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$workspaceRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot '..'))
$backendSource = Join-Path $workspaceRoot 'iracing-coach'
$artifactRoot = Join-Path $projectRoot 'artifacts\release'
$payload = Join-Path $artifactRoot 'payload'
$uninstallerOutput = Join-Path $artifactRoot 'uninstaller'
$installerOutput = Join-Path $artifactRoot 'installer'
$payloadArchive = Join-Path $projectRoot 'tools\installer-payload.zip'
$destination = [System.IO.Path]::GetFullPath($OutputDirectory)
$pythonSource = [System.IO.Path]::GetFullPath($PythonRuntime)
$codexSource = [System.IO.Path]::GetFullPath($CodexRuntime)
$codexSchemaSource = Join-Path $projectRoot 'generated\codex-app-server-0.146.0-alpha.9.2'

function Get-ProjectVersion([string]$ProjectPath) {
    [xml]$projectDocument = Get-Content -LiteralPath $ProjectPath -Raw
    return [string]($projectDocument.Project.PropertyGroup.Version | Select-Object -First 1)
}

function Reset-ReleaseDirectory([string]$Path) {
    $resolved = [System.IO.Path]::GetFullPath($Path)
    $allowedRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot 'artifacts')) + [System.IO.Path]::DirectorySeparatorChar
    if (-not $resolved.StartsWith($allowedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to reset a directory outside the project artifacts folder: $resolved"
    }
    if (Test-Path -LiteralPath $resolved) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
    New-Item -ItemType Directory -Path $resolved | Out-Null
}

if (-not (Test-Path -LiteralPath (Join-Path $pythonSource 'python.exe'))) {
    throw "PythonRuntime must name a portable Python folder containing python.exe."
}
$pythonVersionOutput = (& (Join-Path $pythonSource 'python.exe') --version | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $pythonVersionOutput -ne "Python $pythonVersion") {
    throw "The Python runtime version is incompatible: $pythonVersionOutput"
}
if (-not (Test-Path -LiteralPath (Join-Path $backendSource 'skills'))) {
    throw "The deterministic backend source was not found at $backendSource."
}
if (-not (Test-Path -LiteralPath $codexSource -PathType Leaf)) {
    throw 'CodexRuntime must name the pinned official codex.exe.'
}
if (-not (Test-Path -LiteralPath (Join-Path $codexSchemaSource 'codex_app_server_protocol.schemas.json'))) {
    throw "The generated Codex app-server schemas were not found at $codexSchemaSource."
}
$releaseProjects = @(
    (Join-Path $projectRoot 'src\iRacingCoach.App\iRacingCoach.App.csproj'),
    (Join-Path $projectRoot 'src\iRacingCoach.Installer\iRacingCoach.Installer.csproj'),
    (Join-Path $projectRoot 'src\iRacingCoach.Uninstaller\iRacingCoach.Uninstaller.csproj')
)
$versionMismatches = $releaseProjects | Where-Object { (Get-ProjectVersion $_) -ne $version }
if ($versionMismatches.Count -gt 0) {
    $details = $versionMismatches | ForEach-Object { "$(Split-Path $_ -Leaf)=$(Get-ProjectVersion $_)" }
    throw "Release identity mismatch. BuildRelease.ps1 targets $version but these projects do not: $($details -join ', '). Update every release identity together before packaging."
}
$releaseSourceIdentities = @(
    [pscustomobject]@{
        Label = 'installer product version'
        Path = (Join-Path $projectRoot 'src\iRacingCoach.Installer\Program.cs')
        Expected = "internal const string ProductVersion = `"$version`";"
    },
    [pscustomobject]@{
        Label = 'application repair version'
        Path = (Join-Path $projectRoot 'src\iRacingCoach.Coordinator\CompanionState.cs')
        Expected = "private const string AppVersion = `"$version`";"
    },
    [pscustomobject]@{
        Label = 'backend client version'
        Path = (Join-Path $projectRoot 'src\iRacingCoach.Contracts\Models.cs')
        Expected = "string ClientVersion = `"$version`""
    },
    [pscustomobject]@{
        Label = 'Coach Engine client version'
        Path = (Join-Path $projectRoot 'src\iRacingCoach.Coordinator\CoachEngine.cs')
        Expected = "version = `"$version`""
    }
)
$sourceIdentityMismatches = $releaseSourceIdentities | Where-Object {
    -not (Get-Content -LiteralPath $_.Path -Raw).Contains($_.Expected)
}
if ($sourceIdentityMismatches.Count -gt 0) {
    $details = $sourceIdentityMismatches | ForEach-Object { $_.Label }
    throw "Release identity mismatch. BuildRelease.ps1 targets $version but these source identities do not: $($details -join ', '). Update every release identity together before packaging."
}
$gitStatus = @(& git -C $workspaceRoot status --porcelain --untracked-files=all 2>&1)
if ($LASTEXITCODE -ne 0) {
    throw "Unable to verify release source provenance with git: $($gitStatus -join [Environment]::NewLine)"
}
if ($gitStatus.Count -gt 0) {
    throw "Release source is not clean. Commit every intended tracked and untracked file before packaging:`n$($gitStatus -join [Environment]::NewLine)"
}
$releaseCommit = (& git -C $workspaceRoot rev-parse HEAD | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $releaseCommit -notmatch '^[0-9a-f]{40}$') {
    throw "Unable to resolve the release source commit: $releaseCommit"
}
$codexSignature = Get-AuthenticodeSignature -LiteralPath $codexSource
if ($codexSignature.Status -ne [System.Management.Automation.SignatureStatus]::Valid -or
    $codexSignature.SignerCertificate.Subject -notlike '*OpenAI OpCo, LLC*') {
    throw 'The Codex runtime is not signed by OpenAI OpCo, LLC.'
}
$codexVersionOutput = (& $codexSource --version | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $codexVersionOutput -ne 'codex-cli 0.146.0-alpha.9.2') {
    throw "The Codex runtime version is incompatible: $codexVersionOutput"
}

Reset-ReleaseDirectory $artifactRoot
New-Item -ItemType Directory -Path $payload, $uninstallerOutput, $installerOutput, $destination -Force | Out-Null

dotnet publish (Join-Path $projectRoot 'src\iRacingCoach.App\iRacingCoach.App.csproj') `
    -c Release -r win-x64 --self-contained true `
    -p:PublishSingleFile=false -p:DebugType=None -p:DebugSymbols=false `
    -o $payload
if ($LASTEXITCODE -ne 0) { throw 'Application publish failed.' }

dotnet publish (Join-Path $projectRoot 'src\iRacingCoach.Uninstaller\iRacingCoach.Uninstaller.csproj') `
    -c Release -r win-x64 --self-contained true `
    -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true `
    -p:DebugType=None -p:DebugSymbols=false `
    -o $uninstallerOutput
if ($LASTEXITCODE -ne 0) { throw 'Uninstaller publish failed.' }

Copy-Item -LiteralPath (Join-Path $uninstallerOutput 'Uninstall iRacing Coach.exe') -Destination $payload

$backendDestination = Join-Path $payload 'iracing-coach'
New-Item -ItemType Directory -Path $backendDestination | Out-Null
& robocopy $backendSource $backendDestination /E /R:1 /W:1 `
    /XD '.git' '__pycache__' '.pytest_cache' '.validation-deps' 'tests' 'data' 'logs' 'setups' 'backups' 'exports' 'credentials' 'auth' 'portable-settings' 'user-library' `
    /XF '*.pyc' '*.pyo' '*.ibt' '*.log' '.env' '.env.*' 'auth.json' 'settings.json' 'portable-state.json' 'archive-manifest.json' '*.machine-local.json' | Out-Null
if ($LASTEXITCODE -gt 7) { throw "Backend copy failed with robocopy code $LASTEXITCODE." }

$pythonDestination = Join-Path $payload 'python'
New-Item -ItemType Directory -Path $pythonDestination | Out-Null
& robocopy $pythonSource $pythonDestination /E /R:1 /W:1 /XD '__pycache__' /XF '*.pyc' '*.pyo' | Out-Null
if ($LASTEXITCODE -gt 7) { throw "Python runtime copy failed with robocopy code $LASTEXITCODE." }

$coachEngineDestination = Join-Path $payload 'coach-engine'
$codexDestination = Join-Path $coachEngineDestination 'codex'
$schemaDestination = Join-Path $coachEngineDestination 'schemas'
New-Item -ItemType Directory -Path $codexDestination, $schemaDestination -Force | Out-Null
Copy-Item -LiteralPath $codexSource -Destination (Join-Path $codexDestination 'codex.exe')
& robocopy $codexSchemaSource $schemaDestination /E /R:1 /W:1 | Out-Null
if ($LASTEXITCODE -gt 7) { throw "Codex schema copy failed with robocopy code $LASTEXITCODE." }
Copy-Item -LiteralPath (Join-Path $workspaceRoot 'contracts\ai-coaching-output.schema.json') -Destination $schemaDestination
Copy-Item -LiteralPath (Join-Path $workspaceRoot 'contracts\ai-tuning-output.schema.json') -Destination $schemaDestination
$codexHash = (Get-FileHash -LiteralPath $codexSource -Algorithm SHA256).Hash.ToLowerInvariant()
$coachEngineManifest = [ordered]@{
    manifestVersion = 1
    appVersion = $version
    sourceCommit = $releaseCommit
    runtimeVersion = '0.146.0-alpha.9.2'
    runtimeSha256 = $codexHash
    runtimePublisher = 'OpenAI OpCo, LLC'
    schemaGeneration = 'codex app-server generate-json-schema --experimental'
    backendVersion = '0.3.0'
    mcpContractVersion = 1
}
$coachEngineManifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $coachEngineDestination 'coach-engine-manifest.json') -Encoding utf8

# Freeze every payload byte before it is embedded. The release manifest is the
# only excluded file because a file cannot truthfully contain its own hash.
$releaseManifestPath = Join-Path $payload 'release-manifest.json'
$payloadFiles = @(Get-ChildItem -LiteralPath $payload -Recurse -File | Where-Object {
    -not [System.IO.Path]::GetFullPath($_.FullName).Equals(
        [System.IO.Path]::GetFullPath($releaseManifestPath),
        [System.StringComparison]::OrdinalIgnoreCase)
})
$manifestFiles = @($payloadFiles | ForEach-Object {
    $relative = [System.IO.Path]::GetRelativePath($payload, $_.FullName).Replace('\', '/')
    $component = 'application'
    $componentVersion = $version
    if ($relative.StartsWith('python/', [System.StringComparison]::OrdinalIgnoreCase)) {
        $component = 'python'
        $componentVersion = $pythonVersion
    }
    elseif ($relative.StartsWith('iracing-coach/', [System.StringComparison]::OrdinalIgnoreCase)) {
        $component = 'backend'
        $componentVersion = '0.3.0'
    }
    elseif ($relative.StartsWith('coach-engine/codex/', [System.StringComparison]::OrdinalIgnoreCase)) {
        $component = 'codex-runtime'
        $componentVersion = '0.146.0-alpha.9.2'
    }
    elseif ($relative.StartsWith('coach-engine/schemas/', [System.StringComparison]::OrdinalIgnoreCase)) {
        $component = 'coach-contracts'
        $componentVersion = '1'
    }
    elseif ($relative.Equals('coach-engine/coach-engine-manifest.json', [System.StringComparison]::OrdinalIgnoreCase)) {
        $component = 'coach-engine'
        $componentVersion = '0.146.0-alpha.9.2'
    }
    elseif ($relative.Equals('Uninstall iRacing Coach.exe', [System.StringComparison]::OrdinalIgnoreCase)) {
        $component = 'uninstaller'
    }
    [ordered]@{
        path = $relative
        size = $_.Length
        sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        component = $component
        version = $componentVersion
    }
} | Sort-Object { $_.path })
$releaseManifest = [ordered]@{
    manifestVersion = 1
    appVersion = $version
    sourceCommit = $releaseCommit
    pythonVersion = $pythonVersion
    files = $manifestFiles
}
$releaseManifestJson = $releaseManifest | ConvertTo-Json -Depth 6
[System.IO.File]::WriteAllText(
    $releaseManifestPath,
    $releaseManifestJson + [Environment]::NewLine,
    [System.Text.UTF8Encoding]::new($false))

if (Test-Path -LiteralPath $payloadArchive) {
    Remove-Item -LiteralPath $payloadArchive -Force
}
Compress-Archive -Path (Join-Path $payload '*') -DestinationPath $payloadArchive -CompressionLevel Optimal

dotnet publish (Join-Path $projectRoot 'src\iRacingCoach.Installer\iRacingCoach.Installer.csproj') `
    -c Release -r win-x64 --self-contained true `
    -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true `
    -p:EmbedInstallerPayload=true `
    -p:DebugType=None -p:DebugSymbols=false `
    -o $installerOutput
if ($LASTEXITCODE -ne 0) { throw 'Installer publish failed.' }

$setupName = "iRacingCoach-$version-Setup.exe"
$setupDestination = Join-Path $destination $setupName
Copy-Item -LiteralPath (Join-Path $installerOutput 'iRacing Coach Setup.exe') -Destination $setupDestination -Force
$hash = (Get-FileHash -LiteralPath $setupDestination -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath "$setupDestination.sha256" -Value "$hash  $setupName" -Encoding ascii

$portableDestination = $null
$portableHash = $null
$portableBytes = $null
if ($IncludePortable) {
    $portableName = "iRacingCoach-$version-Portable-win-x64.zip"
    $portableDestination = Join-Path $destination $portableName
    Copy-Item -LiteralPath $payloadArchive -Destination $portableDestination -Force
    $portableHash = (Get-FileHash -LiteralPath $portableDestination -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath "$portableDestination.sha256" -Value "$portableHash  $portableName" -Encoding ascii
    $portableBytes = (Get-Item -LiteralPath $portableDestination).Length
}

[pscustomobject]@{
    Installer = $setupDestination
    Bytes = (Get-Item -LiteralPath $setupDestination).Length
    Sha256 = $hash
    Portable = $portableDestination
    PortableBytes = $portableBytes
    PortableSha256 = $portableHash
    PayloadFiles = (Get-ChildItem -LiteralPath $payload -Recurse -File).Count
}
