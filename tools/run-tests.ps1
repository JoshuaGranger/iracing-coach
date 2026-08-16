param([string]$PythonPath, [switch]$ShowToolchain)

$ErrorActionPreference = 'Stop'
$workspaceRoot = Split-Path -Parent $PSScriptRoot

# Single shared resolution path. tools/Resolve-Toolchain.ps1 owns the canonical
# precedence order and the reason no agent-runtime cache is an implicit
# candidate. This entry point requires Python only; Node and .NET are unused.
. (Join-Path $PSScriptRoot 'Resolve-Toolchain.ps1')
$pythonResult = Resolve-CoachPython -PythonPath $PythonPath -Required
$python = $pythonResult.Resolved.Path
$provenance = Get-CoachToolchainProvenance -Python $pythonResult -Required @('python') -Authority 'local-diagnostic'
Assert-CoachToolchain -Provenance $provenance -Required @('python')
if ($ShowToolchain) { Write-CoachToolchainProvenance -Provenance $provenance }

$env:PYTHONUTF8 = '1'
& $python -X utf8 -m unittest discover -s (Join-Path $workspaceRoot 'iracing-coach\tests') -p 'test_*.py'
exit $LASTEXITCODE
