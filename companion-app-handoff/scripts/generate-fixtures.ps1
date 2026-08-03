param([string]$PythonPath)

$ErrorActionPreference = 'Stop'
$python = $PythonPath
if ([string]::IsNullOrWhiteSpace($python)) {
    $command = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($command) { $python = $command.Source }
}
if ([string]::IsNullOrWhiteSpace($python)) {
    throw 'Pass -PythonPath with a Python 3.10+ executable.'
}
$env:PYTHONUTF8 = '1'
& $python -X utf8 (Join-Path $PSScriptRoot 'generate_fixtures.py')
exit $LASTEXITCODE

