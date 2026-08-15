<#
.SYNOPSIS
    Run a known development command with every backend default root confined to
    a unique per-run sandbox.

.DESCRIPTION
    Development tooling. This is environment containment for known commands, not
    an OS security sandbox: it cannot stop an arbitrary child executable from
    opening a socket or writing outside the sandbox. See tools/dev/README.md for
    the exact guarantee, its two dispatch tiers, and its limits.

    The interpreter is always supplied explicitly. This script performs no
    interpreter discovery and reads no agent runtime cache path; shared
    resolution belongs to DEV-TOOLCHAIN-001 / WS-13a.

.EXAMPLE
    .\tools\dev\Invoke-SandboxedDev.ps1 -PythonPath C:\Python312\python.exe -Target backend-suite
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PythonPath,
    [ValidateSet('backend-suite', 'mcp-smoke', 'verify-repository')][string]$Target,
    [string]$Script,
    [string]$Module,
    [string[]]$TargetArgs = @(),
    [switch]$FixtureIracingRoot,
    [switch]$KeepSandbox,
    [string]$SandboxParent
)

$ErrorActionPreference = 'Stop'

$selected = @($Target, $Script, $Module) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
if ($selected.Count -ne 1) {
    throw 'Supply exactly one of -Target, -Script, or -Module.'
}

$WorktreeRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).ProviderPath
$FixtureRoot = [System.IO.Path]::GetFullPath((Join-Path $WorktreeRoot 'test-data\ibt'))

if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Interpreter not found: $PythonPath"
}
$PythonPath = (Resolve-Path -LiteralPath $PythonPath).ProviderPath
# Capture first, then filter. Piping straight into Select-Object -First 1 stops
# the upstream pipeline before $LASTEXITCODE is assigned.
$pythonProbe = & $PythonPath -c "import sys; print(sys.version.split()[0])"
if ($LASTEXITCODE -ne 0) {
    throw "Interpreter did not execute: $PythonPath"
}
$pythonVersion = @($pythonProbe)[0]
$pythonHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $PythonPath).Hash.ToLower()

if ([string]::IsNullOrWhiteSpace($SandboxParent)) { $SandboxParent = $env:TEMP }
if (-not (Test-Path -LiteralPath $SandboxParent -PathType Container)) {
    throw "Sandbox parent does not exist: $SandboxParent"
}
$SandboxParent = (Resolve-Path -LiteralPath $SandboxParent).ProviderPath

$sandboxName = 'iracing-coach-dev-' + [Guid]::NewGuid().ToString('N')
$SandboxRoot = Join-Path $SandboxParent $sandboxName
# home\AppData\Local and home\AppData\Roaming must exist. Windows expands the
# per-user shell folders from a USERPROFILE-relative registry value, and
# Environment.GetFolderPath returns an empty string when the expanded directory
# is missing. Callers then combine that empty string with a relative tail and
# write into the current working directory, which is the worktree. Creating
# these two directories and naming them explicitly below keeps that write inside
# the sandbox.
foreach ($child in @('archive', 'home', 'temp', 'install', 'iracing', 'home\AppData\Local', 'home\AppData\Roaming')) {
    New-Item -ItemType Directory -Path (Join-Path $SandboxRoot $child) -Force | Out-Null
}
$SandboxRoot = (Resolve-Path -LiteralPath $SandboxRoot).ProviderPath

function Test-ConfinedPath {
    param([string]$Value, [string]$Name, [string]$Root)

    if ([string]::IsNullOrWhiteSpace($Value)) { throw "$Name is empty." }
    $normalized = $Value.Replace('/', '\')
    if ($normalized.StartsWith('\\') -or $normalized.StartsWith('\?\') -or $normalized.StartsWith('\.\')) {
        throw "$Name is a UNC or device path."
    }
    $full = [System.IO.Path]::GetFullPath($normalized)
    if ($full -match '\.\.') { throw "$Name did not canonicalise cleanly." }
    $rootQualifier = [System.IO.Path]::GetPathRoot($Root)
    if ([System.IO.Path]::GetPathRoot($full) -ne $rootQualifier) { throw "$Name is on a different drive from the sandbox." }
    $rootPrefix = $Root.TrimEnd('\') + '\'
    if (-not $full.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Name is outside the sandbox."
    }
    return $full
}

$childEnvironment = @{
    'IRACING_COACH_DATA'         = (Join-Path $SandboxRoot 'archive')
    'IRACING_COACH_INSTALL_ROOT' = (Join-Path $SandboxRoot 'install')
    'USERPROFILE'                = (Join-Path $SandboxRoot 'home')
    'HOMEDRIVE'                  = [System.IO.Path]::GetPathRoot($SandboxRoot).TrimEnd('\')
    'HOMEPATH'                   = (Join-Path $SandboxRoot 'home').Substring([System.IO.Path]::GetPathRoot($SandboxRoot).TrimEnd('\').Length)
    'TEMP'                       = (Join-Path $SandboxRoot 'temp')
    'TMP'                        = (Join-Path $SandboxRoot 'temp')
    'LOCALAPPDATA'               = (Join-Path $SandboxRoot 'home\AppData\Local')
    'APPDATA'                    = (Join-Path $SandboxRoot 'home\AppData\Roaming')
    'IRACING_COACH_PYTHON'       = $PythonPath
    'PYTHONUTF8'                 = '1'
}

$allowedFixtureRoot = $null
if ($FixtureIracingRoot) {
    if (-not (Test-Path -LiteralPath $FixtureRoot -PathType Container)) {
        throw "Tracked fixture root is missing: $FixtureRoot"
    }
    $allowedFixtureRoot = (Resolve-Path -LiteralPath $FixtureRoot).ProviderPath
    $childEnvironment['IRACING_COACH_IRACING_ROOT'] = $allowedFixtureRoot
}
else {
    $childEnvironment['IRACING_COACH_IRACING_ROOT'] = (Join-Path $SandboxRoot 'iracing')
}

foreach ($name in @('IRACING_COACH_DATA', 'IRACING_COACH_INSTALL_ROOT', 'USERPROFILE', 'TEMP', 'TMP', 'LOCALAPPDATA', 'APPDATA')) {
    $childEnvironment[$name] = Test-ConfinedPath -Value $childEnvironment[$name] -Name $name -Root $SandboxRoot
}
if (-not $FixtureIracingRoot) {
    $childEnvironment['IRACING_COACH_IRACING_ROOT'] =
        Test-ConfinedPath -Value $childEnvironment['IRACING_COACH_IRACING_ROOT'] -Name 'IRACING_COACH_IRACING_ROOT' -Root $SandboxRoot
}

Write-Output '--- G0d-py sandboxed run ---'
Write-Output ("worktree      : " + $WorktreeRoot)
Write-Output ("sandbox       : " + $SandboxRoot)
foreach ($name in ($childEnvironment.Keys | Sort-Object)) {
    Write-Output ("  " + $name.PadRight(28) + $childEnvironment[$name])
}
Write-Output ("interpreter   : " + $PythonPath)
Write-Output ("  version     : " + $pythonVersion)
Write-Output ("  sha256      : " + $pythonHash)
Write-Output ("started (UTC) : " + [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'))

function ConvertTo-NativeArgument {
    # Windows PowerShell 5.1 runs on .NET Framework, which has no
    # ProcessStartInfo.ArgumentList, so arguments are quoted here per the
    # CommandLineToArgvW rules. The worktree path contains a space, so this is
    # load bearing rather than defensive.
    param([string]$Value)

    if ($Value -eq '') { return '""' }
    if ($Value -notmatch '[\s"]') { return $Value }
    $builder = New-Object System.Text.StringBuilder
    [void]$builder.Append('"')
    $backslashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq '\') { $backslashes++; continue }
        if ($character -eq '"') {
            [void]$builder.Append('\' * ($backslashes * 2 + 1))
            $backslashes = 0
            [void]$builder.Append('"')
            continue
        }
        if ($backslashes -gt 0) { [void]$builder.Append('\' * $backslashes); $backslashes = 0 }
        [void]$builder.Append($character)
    }
    if ($backslashes -gt 0) { [void]$builder.Append('\' * ($backslashes * 2)) }
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Invoke-Child {
    param([string]$FilePath, [string[]]$Arguments)

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    $psi.UseShellExecute = $false
    $psi.WorkingDirectory = $WorktreeRoot
    $psi.Arguments = (($Arguments | ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' ')
    # Child only. The caller's session is never mutated.
    $psi.EnvironmentVariables.Remove('HOME') | Out-Null
    foreach ($name in $childEnvironment.Keys) { $psi.EnvironmentVariables[$name] = $childEnvironment[$name] }
    $process = [System.Diagnostics.Process]::Start($psi)
    $process.WaitForExit()
    return $process.ExitCode
}

$bootstrap = Join-Path $PSScriptRoot 'sandbox_bootstrap.py'
$common = @('-X', 'utf8', $bootstrap, '--expect-sandbox', $SandboxRoot)
if ($allowedFixtureRoot) { $common += @('--allow-fixture-root', $allowedFixtureRoot) }

$exitCode = 1
try {
    if ($Target -eq 'verify-repository') {
        # Weaker dispatch tier: the assertion is a preceding process, then the
        # PowerShell target is spawned with the identical validated environment.
        $exitCode = Invoke-Child -FilePath $PythonPath -Arguments ($common + @('--assert-only'))
        if ($exitCode -ne 0) { throw 'Sandbox assertion failed; target was not started.' }
        $exitCode = Invoke-Child -FilePath 'C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe' -Arguments @(
            '-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass',
            '-File', (Join-Path $WorktreeRoot 'tools\verify-repository.ps1'),
            '-PythonPath', $PythonPath
        )
    }
    elseif ($Target -eq 'backend-suite') {
        $exitCode = Invoke-Child -FilePath $PythonPath -Arguments ($common + @(
            '--module', 'unittest', '--',
            'discover', '-s', (Join-Path $WorktreeRoot 'iracing-coach\tests'), '-p', 'test_*.py'
        ))
    }
    elseif ($Target -eq 'mcp-smoke') {
        $exitCode = Invoke-Child -FilePath $PythonPath -Arguments ($common + @(
            '--script', (Join-Path $WorktreeRoot 'tools\mcp_e2e_smoke.py')
        ))
    }
    elseif ($Script) {
        $exitCode = Invoke-Child -FilePath $PythonPath -Arguments ($common + @('--script', $Script, '--') + $TargetArgs)
    }
    else {
        $exitCode = Invoke-Child -FilePath $PythonPath -Arguments ($common + @('--module', $Module, '--') + $TargetArgs)
    }
}
finally {
    Write-Output ("exit code     : " + $exitCode)
    Write-Output ("finished (UTC): " + [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ'))
    $shouldRemove = ($exitCode -eq 0) -and (-not $KeepSandbox)
    if ($shouldRemove) {
        $resolved = (Resolve-Path -LiteralPath $SandboxRoot).ProviderPath
        $leaf = Split-Path -Path $resolved -Leaf
        $parentPrefix = $SandboxParent.TrimEnd('\') + '\'
        $item = Get-Item -LiteralPath $resolved -Force
        $isReparse = ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -eq [System.IO.FileAttributes]::ReparsePoint
        $worktreePrefix = $WorktreeRoot.TrimEnd('\') + '\'
        $safe = $item.PSIsContainer `
            -and (-not $isReparse) `
            -and ($leaf -match '^iracing-coach-dev-[0-9a-f]{32}$') `
            -and $resolved.StartsWith($parentPrefix, [StringComparison]::OrdinalIgnoreCase) `
            -and ($resolved.TrimEnd('\') -ne $SandboxParent.TrimEnd('\')) `
            -and (-not $worktreePrefix.StartsWith($resolved.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase))
        if ($safe) {
            [System.IO.Directory]::Delete($resolved, $true)
            Write-Output 'sandbox       : removed'
        }
        else {
            Write-Output ("sandbox       : RETAINED (failed a cleanup guard) " + $resolved)
        }
    }
    else {
        Write-Output ("sandbox       : retained at " + $SandboxRoot)
    }
}

exit $exitCode
