param(
    [string]$PythonPath,
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$workspaceRoot = Split-Path -Parent $PSScriptRoot

$userProfilePath = if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
    [System.IO.Path]::GetFullPath($env:USERPROFILE)
}
else {
    [Environment]::GetFolderPath('UserProfile')
}

$candidates = @(
    $PythonPath,
    $env:IRACING_COACH_PYTHON,
    (Join-Path $userProfilePath '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'),
    (Join-Path $userProfilePath 'AppData\Local\Programs\Python\Python313\python.exe'),
    (Join-Path $userProfilePath 'AppData\Local\Programs\Python\Python312\python.exe'),
    (Join-Path $userProfilePath 'AppData\Local\Programs\Python\Python311\python.exe'),
    (Join-Path $userProfilePath 'AppData\Local\Programs\Python\Python310\python.exe')
) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }

$python = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $python) {
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($command) { $python = $command.Source }
}
if (-not $python) { throw 'Python 3.10 or newer was not found.' }

$env:PYTHONUTF8 = '1'
& $python -X utf8 (Join-Path $PSScriptRoot 'export_contracts.py') --check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$arguments = @('-X', 'utf8', (Join-Path $PSScriptRoot 'verify_repository.py'))
if (-not $SkipTests) { $arguments += '--full' }
& $python @arguments
exit $LASTEXITCODE
