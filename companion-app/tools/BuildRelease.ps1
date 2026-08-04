[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputDirectory,

    [Parameter(Mandatory = $true)]
    [string]$PythonRuntime,

    [Parameter(Mandatory = $true)]
    [string]$CodexRuntime
)

$ErrorActionPreference = 'Stop'
$version = '0.11.0'
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
if (-not (Test-Path -LiteralPath (Join-Path $backendSource 'skills'))) {
    throw "The deterministic backend source was not found at $backendSource."
}
if (-not (Test-Path -LiteralPath $codexSource -PathType Leaf)) {
    throw 'CodexRuntime must name the pinned official codex.exe.'
}
if (-not (Test-Path -LiteralPath (Join-Path $codexSchemaSource 'codex_app_server_protocol.schemas.json'))) {
    throw "The generated Codex app-server schemas were not found at $codexSchemaSource."
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
& robocopy $backendSource $backendDestination /E /R:1 /W:1 /XD '.git' '__pycache__' '.pytest_cache' '.validation-deps' 'tests' 'data' /XF '*.pyc' '*.pyo' | Out-Null
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
Copy-Item -LiteralPath (Join-Path $workspaceRoot 'companion-app-handoff\contracts\ai-coaching-output.schema.json') -Destination $schemaDestination
$codexHash = (Get-FileHash -LiteralPath $codexSource -Algorithm SHA256).Hash.ToLowerInvariant()
$coachEngineManifest = [ordered]@{
    manifestVersion = 1
    appVersion = $version
    runtimeVersion = '0.146.0-alpha.9.2'
    runtimeSha256 = $codexHash
    runtimePublisher = 'OpenAI OpCo, LLC'
    schemaGeneration = 'codex app-server generate-json-schema --experimental'
    backendVersion = '0.3.0'
    mcpContractVersion = 1
}
$coachEngineManifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $coachEngineDestination 'coach-engine-manifest.json') -Encoding utf8

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

$portableName = "iRacingCoach-$version-Portable-win-x64.zip"
$portableDestination = Join-Path $destination $portableName
Copy-Item -LiteralPath $payloadArchive -Destination $portableDestination -Force
$portableHash = (Get-FileHash -LiteralPath $portableDestination -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath "$portableDestination.sha256" -Value "$portableHash  $portableName" -Encoding ascii

[pscustomobject]@{
    Installer = $setupDestination
    Bytes = (Get-Item -LiteralPath $setupDestination).Length
    Sha256 = $hash
    Portable = $portableDestination
    PortableBytes = (Get-Item -LiteralPath $portableDestination).Length
    PortableSha256 = $portableHash
    PayloadFiles = (Get-ChildItem -LiteralPath $payload -Recurse -File).Count
}
