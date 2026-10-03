<#
.SYNOPSIS
  Local gate: repository verification, Rust format, lints and tests, wire and theme drift, web typecheck, tests and build.
.PARAMETER PythonPath
  Interpreter for tools\verify-repository.ps1. Defaults to the bundled interpreter; the python on PATH may be a Store alias.
.PARAMETER Linux
  Run only the Linux gate (tools/wsl/check.sh in WSL Ubuntu) instead of the Windows steps.
.PARAMETER LinuxScope
  Scope passed to tools/wsl/check.sh with -Linux: rust (default), server, all (adds the Python suite) or python.
.PARAMETER SkipRepository
  Skip tools\verify-repository.ps1 (slow) for quick loops. CI and delivery never skip it.
#>
param(
    [string]$PythonPath = 'C:\Users\joshu\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe',
    [switch]$Linux,
    [ValidateSet('rust', 'server', 'all', 'python')][string]$LinuxScope = 'rust',
    [switch]$SkipRepository
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$cargoBin = Join-Path $env:USERPROFILE '.cargo\bin'
if ((Test-Path $cargoBin) -and (($env:Path -split ';') -notcontains $cargoBin)) { $env:Path = "$cargoBin;$env:Path" }

function Invoke-Step {
    param([string]$Name, [scriptblock]$Body)
    Write-Host "`n== $Name"
    & $Body
    if ($LASTEXITCODE -ne 0) { throw "$Name failed (exit $LASTEXITCODE)" }
}

if ($Linux) {
    Invoke-Step "WSL check ($LinuxScope)" { wsl.exe -d Ubuntu -- bash tools/wsl/check.sh $LinuxScope }
    Write-Host "`nLinux checks passed"
    return
}

if (-not $SkipRepository) {
    Invoke-Step 'verify-repository' {
        powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'verify-repository.ps1') -PythonPath $PythonPath
    }
}

Invoke-Step 'cargo fmt --check' { cargo fmt --all --check }
Invoke-Step 'cargo clippy -D warnings' { cargo clippy --workspace --all-targets -- -D warnings }
Invoke-Step 'cargo test' { cargo test --workspace }

Invoke-Step 'pnpm install' { pnpm install --frozen-lockfile }
Invoke-Step 'wire drift' { node tools/wire/gen-wire.mjs --check }
Invoke-Step 'theme drift' { node tools/theme/gen-theme.mjs --check }
Invoke-Step 'web typecheck' { pnpm -C web typecheck }
Invoke-Step 'web test' { pnpm -C web test }
Invoke-Step 'web build' { pnpm -C web build }

Write-Host "`nAll checks passed"
