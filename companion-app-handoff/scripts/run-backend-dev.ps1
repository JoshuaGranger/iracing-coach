param(
    [Parameter(Mandatory = $true)][string]$IRacingRoot,
    [Parameter(Mandatory = $true)][string]$ArchiveRoot,
    [string]$InstallRoot,
    [string]$PythonPath
)

$ErrorActionPreference = 'Stop'
$workspaceRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$pluginRoot = Join-Path $workspaceRoot 'iracing-coach'
$launcher = Join-Path $pluginRoot 'skills\analyze-iracing-race\scripts\start-mcp.ps1'

$resolvedIRacing = [System.IO.Path]::GetFullPath($IRacingRoot)
$resolvedArchive = [System.IO.Path]::GetFullPath($ArchiveRoot)
if (-not (Test-Path -LiteralPath $resolvedIRacing -PathType Container)) {
    throw "iRacing fixture/source root does not exist: $resolvedIRacing"
}
if (-not (Test-Path -LiteralPath $resolvedArchive -PathType Container)) {
    [void][System.IO.Directory]::CreateDirectory($resolvedArchive)
}

$env:IRACING_COACH_IRACING_ROOT = $resolvedIRacing
$env:IRACING_COACH_DATA = $resolvedArchive
if (-not [string]::IsNullOrWhiteSpace($InstallRoot)) {
    $resolvedInstall = [System.IO.Path]::GetFullPath($InstallRoot)
    if (-not (Test-Path -LiteralPath $resolvedInstall -PathType Container)) {
        throw "iRacing install root does not exist: $resolvedInstall"
    }
    $env:IRACING_COACH_INSTALL_ROOT = $resolvedInstall
}
$env:PYTHONUTF8 = '1'
if (-not [string]::IsNullOrWhiteSpace($PythonPath)) {
    $env:IRACING_COACH_PYTHON = [System.IO.Path]::GetFullPath($PythonPath)
}

& powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $launcher
exit $LASTEXITCODE
