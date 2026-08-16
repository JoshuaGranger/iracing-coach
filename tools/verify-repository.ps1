param(
    [string]$PythonPath,
    [switch]$SkipTests,
    [switch]$ShowToolchain
)

$ErrorActionPreference = 'Stop'
$workspaceRoot = Split-Path -Parent $PSScriptRoot

# Single shared resolution path. The previous candidate list privileged a
# private agent-runtime cache ahead of every system interpreter and ordered
# Python313 before Python312 for no stated reason; that was
# TOOLCHAIN-COUPLING-001. Resolution now lives in tools/Resolve-Toolchain.ps1,
# which selects by validated version rather than by vendor path, and selects
# nothing by mere file existence. This entry point requires Python only.
. (Join-Path $PSScriptRoot 'Resolve-Toolchain.ps1')
$pythonResult = Resolve-CoachPython -PythonPath $PythonPath -Required
$python = $pythonResult.Resolved.Path
$provenance = Get-CoachToolchainProvenance -Python $pythonResult -Required @('python') -Authority 'local-diagnostic'
Assert-CoachToolchain -Provenance $provenance -Required @('python')
if ($ShowToolchain) { Write-CoachToolchainProvenance -Provenance $provenance }

$env:PYTHONUTF8 = '1'
& $python -X utf8 (Join-Path $PSScriptRoot 'export_contracts.py') --check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$arguments = @('-X', 'utf8', (Join-Path $PSScriptRoot 'verify_repository.py'))
if (-not $SkipTests) { $arguments += '--full' }
& $python @arguments
exit $LASTEXITCODE
