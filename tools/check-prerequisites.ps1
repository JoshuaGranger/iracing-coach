param(
    [string]$PythonPath,
    [switch]$SkipBackendTests
)

$ErrorActionPreference = 'Stop'
$workspaceRoot = Split-Path -Parent $PSScriptRoot

$dotnet = Get-Command dotnet.exe -ErrorAction SilentlyContinue
$dotnetSdks = @()
$dotnet10Available = $false
if ($dotnet) {
    $dotnetSdks = @(& $dotnet.Source --list-sdks 2>$null)
    $dotnet10Available = @($dotnetSdks | Where-Object { $_ -match '^10\.' }).Count -gt 0
}

$userProfilePath = if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
    [System.IO.Path]::GetFullPath($env:USERPROFILE)
}
else {
    [Environment]::GetFolderPath('UserProfile')
}
$pythonCandidates = @(
    $PythonPath,
    $env:IRACING_COACH_PYTHON,
    (Join-Path $userProfilePath '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'),
    (Join-Path $userProfilePath 'AppData\Local\Programs\Python\Python313\python.exe'),
    (Join-Path $userProfilePath 'AppData\Local\Programs\Python\Python312\python.exe'),
    (Join-Path $userProfilePath 'AppData\Local\Programs\Python\Python311\python.exe'),
    (Join-Path $userProfilePath 'AppData\Local\Programs\Python\Python310\python.exe')
) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
$python = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $python) {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pythonCommand) { $python = $pythonCommand.Source }
}
$pythonVersion = $null
$pythonCompatible = $false
if ($python) {
    $pythonVersion = (& $python -X utf8 -c "import sys; print('.'.join(map(str, sys.version_info[:3])))").Trim()
    $parts = @($pythonVersion.Split('.') | ForEach-Object { [int]$_ })
    $pythonCompatible = $parts[0] -gt 3 -or ($parts[0] -eq 3 -and $parts[1] -ge 10)
}

$codex = Get-Command codex.exe -ErrorAction SilentlyContinue
$webView2RegistryRoots = @(
    'HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\*',
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\*',
    'HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\*'
)
$webView2Entries = @(
    Get-ItemProperty -Path $webView2RegistryRoots -ErrorAction SilentlyContinue |
        Where-Object { [string]$_.name -like '*WebView2*' }
)
$webView2Versions = @(
    $webView2Entries |
        ForEach-Object { [string]$_.pv } |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Sort-Object -Unique
)
$result = [ordered]@{
    ok = $dotnet10Available -and $pythonCompatible
    workspace = $workspaceRoot
    powershell = $PSVersionTable.PSVersion.ToString()
    dotnet = [ordered]@{
        executable = if ($dotnet) { $dotnet.Source } else { $null }
        sdks = $dotnetSdks
        dotnet10Available = $dotnet10Available
    }
    python = [ordered]@{
        executable = $python
        version = $pythonVersion
        compatible = $pythonCompatible
    }
    optionalCodexAppServer = [ordered]@{
        commandFound = [bool]$codex
        executable = if ($codex) { $codex.Source } else { $null }
        requiredForDeterministicBuild = $false
    }
    webView2 = [ordered]@{
        detected = $webView2Entries.Count -gt 0
        versions = $webView2Versions
        requiredOnRacingPc = $true
        installerOrPackagedRuntimeRequiredWhenMissing = $true
    }
}

$result | ConvertTo-Json -Depth 6
if (-not $result.ok) {
    Write-Error 'Build machine needs a .NET 10 SDK and Python 3.10 or newer. Codex is optional for deterministic development.'
    exit 1
}

$arguments = @(
    '-NoLogo',
    '-NoProfile',
    '-ExecutionPolicy',
    'Bypass',
    '-File',
    (Join-Path $PSScriptRoot 'verify-repository.ps1'),
    '-PythonPath',
    $python
)
if ($SkipBackendTests) { $arguments += '-SkipTests' }
& powershell.exe @arguments
exit $LASTEXITCODE
