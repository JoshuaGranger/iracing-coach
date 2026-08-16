<#
.SYNOPSIS
    Shared toolchain resolution and provenance for iRacing Coach development entry points.

.DESCRIPTION
    Dot-source this file. It defines functions only and resolves nothing on load,
    so importing it can never change which interpreter a caller uses.

    One canonical precedence order is applied by every entry point:

        explicit parameter -> environment override -> declared candidates -> PATH -> refusal

    No private agent-runtime cache is ever an implicit candidate. Such a runtime
    remains perfectly usable, but only when named explicitly, and it is then
    reported with full provenance. That is TOOLCHAIN-COUPLING-001.

    Resolution is entirely offline: nothing is downloaded, installed, restored,
    or requested from a package feed.

    Sandbox roots and the run-evidence contract are owned by tools/dev and are
    NOT redefined here. This file implements only the toolchain clause.
#>

# Deliberately no Set-StrictMode here. This file is dot-sourced, so it runs in
# the caller's scope, and setting strict mode would silently change execution
# semantics for every entry point that imports it. A library must not alter how
# its caller runs.

# The repository's declared compatibility floor. Six sources agree on 3.10:
# README.md, run-tests.ps1, generate-fixtures.ps1, check-prerequisites.ps1,
# verify_repository.py, and export_contracts.py, which exports python_minimum
# into the generated contracts/compatibility.json. CI selecting 3.12 is an
# environment choice for the source gate, not a compatibility floor, and is
# recorded separately.
$script:CoachPythonMinimum = [Version]'3.10'

function Get-CoachToolHash {
    param([Parameter(Mandatory = $true)][string]$Path)

    try { return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path -ErrorAction Stop).Hash.ToLower() }
    catch { return $null }
}

function Test-CoachPythonCandidate {
    <#
        Validate by EXECUTION, never by file size.

        A zero-length python.exe on Windows is normally an app-execution alias:
        a reparse point that runs a real interpreter. Rejecting it for its size
        would reject a working tool. The only meaningful questions are whether
        it runs and whether it reports a compatible version.
    #>
    param([Parameter(Mandatory = $true)][AllowNull()][string]$Path, [string]$Rule)

    $result = [pscustomobject]@{
        Ok = $false; Rule = $Rule; Path = $Path; Executable = $null
        Version = $null; Sha256 = $null; Reason = $null
    }
    if ([string]::IsNullOrWhiteSpace($Path)) { $result.Reason = 'not-found'; return $result }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { $result.Reason = 'not-found'; return $result }

    $probe = $null
    try {
        $probe = & $Path -c "import sys; print('.'.join(map(str, sys.version_info[:3]))); print(sys.executable)" 2>$null
    }
    catch { $result.Reason = 'did-not-execute'; return $result }
    if ($LASTEXITCODE -ne 0 -or $null -eq $probe) { $result.Reason = 'did-not-execute'; return $result }

    $lines = @($probe)
    $parsed = $null
    if ($lines.Count -lt 1 -or -not [Version]::TryParse(($lines[0]).Trim(), [ref]$parsed)) {
        $result.Reason = 'malformed-version'; return $result
    }

    $result.Version = $parsed.ToString()
    $result.Executable = if ($lines.Count -ge 2) { ($lines[1]).Trim() } else { $Path }
    # An app-execution alias is a reparse point whose bytes cannot be hashed
    # directly. Fall back to the interpreter it actually resolved to, so
    # provenance still identifies the executable that ran.
    $result.Sha256 = Get-CoachToolHash -Path $Path
    if ($null -eq $result.Sha256 -and $result.Executable -ne $Path) {
        $result.Sha256 = Get-CoachToolHash -Path $result.Executable
    }

    if ($parsed -lt $script:CoachPythonMinimum) { $result.Reason = 'version-below-minimum'; return $result }

    $result.Ok = $true
    return $result
}

function Get-CoachDeclaredPythonCandidate {
    <#
        Standard, vendor-neutral install locations only, newest first.
        Deliberately excludes any agent-runtime cache path.
    #>
    $found = New-Object System.Collections.Generic.List[string]
    $bases = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python'),
        (Join-Path $env:ProgramFiles 'Python')
    )
    foreach ($base in $bases) {
        if ([string]::IsNullOrWhiteSpace($base) -or -not (Test-Path -LiteralPath $base -PathType Container)) { continue }
        Get-ChildItem -LiteralPath $base -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object {
                $exe = Join-Path $_.FullName 'python.exe'
                if (Test-Path -LiteralPath $exe -PathType Leaf) { $found.Add($exe) | Out-Null }
            }
    }
    foreach ($root in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if ([string]::IsNullOrWhiteSpace($root)) { continue }
        Get-ChildItem -LiteralPath $root -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object {
                $exe = Join-Path $_.FullName 'python.exe'
                if (Test-Path -LiteralPath $exe -PathType Leaf) { $found.Add($exe) | Out-Null }
            }
    }
    return $found.ToArray()
}

function Resolve-CoachPython {
    param([string]$PythonPath, [switch]$Required)

    $rejected = New-Object System.Collections.Generic.List[object]

    $tiers = New-Object System.Collections.Generic.List[object]
    if (-not [string]::IsNullOrWhiteSpace($PythonPath)) { $tiers.Add(@{ Rule = 'parameter'; Path = $PythonPath }) | Out-Null }
    if (-not [string]::IsNullOrWhiteSpace($env:IRACING_COACH_PYTHON)) { $tiers.Add(@{ Rule = 'environment'; Path = $env:IRACING_COACH_PYTHON }) | Out-Null }
    foreach ($candidate in (Get-CoachDeclaredPythonCandidate)) { $tiers.Add(@{ Rule = 'declared'; Path = $candidate }) | Out-Null }
    foreach ($name in @('python.exe', 'python3.exe')) {
        $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command) { $tiers.Add(@{ Rule = 'path'; Path = $command.Source }) | Out-Null }
    }

    foreach ($tier in $tiers) {
        $check = Test-CoachPythonCandidate -Path $tier.Path -Rule $tier.Rule
        if ($check.Ok) { return [pscustomobject]@{ Tool = 'python'; Resolved = $check; Rejected = $rejected.ToArray() } }
        # Report a path only when the caller supplied it; ambient probes are
        # reported by rule alone so evidence cannot accumulate machine layout.
        $explicit = $tier.Rule -in @('parameter', 'environment')
        $rejected.Add([pscustomobject]@{
            tool = 'python'; rule = $tier.Rule; reason = $check.Reason
            path = if ($explicit) { $tier.Path } else { $null }
        }) | Out-Null

        # An explicitly named interpreter that fails validation is a hard stop.
        # Falling through to a different one would silently run a tool the
        # caller did not ask for, which is the exact failure this workstream
        # exists to prevent.
        if ($explicit) {
            throw ("The {0} Python '{1}' was rejected ({2}). Resolution does not fall back to another interpreter when one is named explicitly." -f `
                $tier.Rule, $tier.Path, $check.Reason)
        }
    }

    if ($Required) {
        throw ("No compatible Python was resolved (minimum {0}). Tried: {1}. Supply -PythonPath or IRACING_COACH_PYTHON." -f `
            $script:CoachPythonMinimum, (($rejected | ForEach-Object { "$($_.rule)=$($_.reason)" }) -join ', '))
    }
    return [pscustomobject]@{ Tool = 'python'; Resolved = $null; Rejected = $rejected.ToArray() }
}

function Resolve-CoachNode {
    param([string]$NodePath, [switch]$Required)

    $rejected = New-Object System.Collections.Generic.List[object]
    $tiers = New-Object System.Collections.Generic.List[object]
    if (-not [string]::IsNullOrWhiteSpace($NodePath)) { $tiers.Add(@{ Rule = 'parameter'; Path = $NodePath }) | Out-Null }
    if (-not [string]::IsNullOrWhiteSpace($env:IRACING_COACH_NODE)) { $tiers.Add(@{ Rule = 'environment'; Path = $env:IRACING_COACH_NODE }) | Out-Null }
    $command = Get-Command 'node' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { $tiers.Add(@{ Rule = 'path'; Path = $command.Source }) | Out-Null }

    foreach ($tier in $tiers) {
        $path = $tier.Path
        # Explicit tiers report their path; ambient probes are reported by rule
        # alone so evidence cannot accumulate machine layout.
        $explicit = $tier.Rule -in @('parameter', 'environment')
        $reason = $null

        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { $reason = 'not-found' }
        else {
            $probe = $null
            try { $probe = & $path --version 2>$null } catch { $probe = $null }
            if ($LASTEXITCODE -ne 0 -or $null -eq $probe) { $reason = 'did-not-execute' }
            else {
                $version = (@($probe)[0]).Trim()
                if ($version -notmatch '^v?\d+\.\d+') { $reason = 'malformed-version' }
                else {
                    return [pscustomobject]@{
                        Tool = 'node'
                        Resolved = [pscustomobject]@{ Ok = $true; Rule = $tier.Rule; Path = $path; Version = $version; Sha256 = (Get-CoachToolHash -Path $path) }
                        Rejected = $rejected.ToArray()
                    }
                }
            }
        }

        $rejected.Add([pscustomobject]@{
            tool = 'node'; rule = $tier.Rule; reason = $reason
            path = if ($explicit) { $path } else { $null }
        }) | Out-Null

        # Same rule as Python: a tool named explicitly must never fall through
        # to a different one. Selecting a substitute the caller did not ask for
        # is the failure this workstream exists to prevent.
        if ($explicit) {
            throw ("The {0} Node '{1}' was rejected ({2}). Resolution does not fall back to another tool when one is named explicitly." -f `
                $tier.Rule, $path, $reason)
        }
    }

    if ($Required) { throw 'No usable Node was resolved. Supply -NodePath or IRACING_COACH_NODE, or provision Node on PATH.' }
    return [pscustomobject]@{ Tool = 'node'; Resolved = $null; Rejected = $rejected.ToArray() }
}

function Resolve-CoachDotnet {
    param([string]$DotnetPath, [string]$GlobalJsonDirectory, [switch]$Required)

    $rejected = New-Object System.Collections.Generic.List[object]
    $path = $DotnetPath
    if ([string]::IsNullOrWhiteSpace($path)) {
        $command = Get-Command 'dotnet' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command) { $path = $command.Source }
    }
    $rule = if (-not [string]::IsNullOrWhiteSpace($DotnetPath)) { 'parameter' } else { 'path' }

    $explicitDotnet = $rule -eq 'parameter'
    $reject = {
        param($Reason)
        $rejected.Add([pscustomobject]@{
            tool = 'dotnet'; rule = $rule; reason = $Reason
            # An explicitly supplied path is reported; ambient PATH candidates
            # stay path-free.
            path = if ($explicitDotnet) { $DotnetPath } else { $null }
        }) | Out-Null
    }

    if ([string]::IsNullOrWhiteSpace($path) -or -not (Test-Path -LiteralPath $path -PathType Leaf)) {
        & $reject 'not-found'
        if ($Required) { throw 'No dotnet executable was resolved.' }
        return [pscustomobject]@{ Tool = 'dotnet'; Resolved = $null; Rejected = $rejected.ToArray() }
    }

    # A constraint that cannot be read cannot be satisfied. Nothing below may
    # default to "satisfied": when a caller asks for a global.json constraint,
    # every step of establishing it must succeed or the resolution fails.
    $constraintRequested = -not [string]::IsNullOrWhiteSpace($GlobalJsonDirectory)
    $pinVersion = $null; $rollForward = $null; $pin = $null
    if ($constraintRequested) {
        if (-not (Test-Path -LiteralPath $GlobalJsonDirectory -PathType Container)) {
            & $reject 'constraint-directory-missing'
            if ($Required) { throw "The global.json directory '$GlobalJsonDirectory' does not exist." }
            return [pscustomobject]@{ Tool = 'dotnet'; Resolved = $null; Rejected = $rejected.ToArray() }
        }
        $globalJson = Join-Path $GlobalJsonDirectory 'global.json'
        if (-not (Test-Path -LiteralPath $globalJson -PathType Leaf)) {
            & $reject 'global-json-missing'
            if ($Required) { throw "No global.json was found in '$GlobalJsonDirectory'." }
            return [pscustomobject]@{ Tool = 'dotnet'; Resolved = $null; Rejected = $rejected.ToArray() }
        }
        $document = $null
        try { $document = Get-Content -LiteralPath $globalJson -Raw | ConvertFrom-Json }
        catch { $document = $null }
        if ($null -eq $document) {
            & $reject 'global-json-unreadable'
            if ($Required) { throw "global.json in '$GlobalJsonDirectory' could not be parsed." }
            return [pscustomobject]@{ Tool = 'dotnet'; Resolved = $null; Rejected = $rejected.ToArray() }
        }
        $pinVersion = $document.sdk.version
        $rollForward = $document.sdk.rollForward
        if ([string]::IsNullOrWhiteSpace($pinVersion) -or -not [Version]::TryParse((($pinVersion -split '-')[0]), [ref]$pin)) {
            & $reject 'global-json-version-malformed'
            if ($Required) { throw "global.json declares an unparseable sdk.version ('$pinVersion')." }
            return [pscustomobject]@{ Tool = 'dotnet'; Resolved = $null; Rejected = $rejected.ToArray() }
        }
    }

    # Ask dotnet from the global.json directory so the reported SDK is the one
    # the pin actually selects, rather than the newest installed.
    $previous = Get-Location
    $sdkVersion = $null
    $exitCode = $null
    try {
        if (-not [string]::IsNullOrWhiteSpace($GlobalJsonDirectory)) { Set-Location -LiteralPath $GlobalJsonDirectory }
        $output = & $path --version 2>$null
        $exitCode = $LASTEXITCODE
        if ($null -ne $output) { $sdkVersion = (@($output)[0]); if ($null -ne $sdkVersion) { $sdkVersion = $sdkVersion.Trim() } }
    }
    catch { $sdkVersion = $null; $exitCode = -1 }
    finally { Set-Location -LiteralPath $previous }

    if ($exitCode -ne 0) {
        & $reject 'did-not-execute'
        if ($Required) { throw "dotnet did not execute successfully (exit $exitCode)." }
        return [pscustomobject]@{ Tool = 'dotnet'; Resolved = $null; Rejected = $rejected.ToArray() }
    }

    $selected = $null
    if ([string]::IsNullOrWhiteSpace($sdkVersion) -or -not [Version]::TryParse((($sdkVersion -split '-')[0]), [ref]$selected)) {
        & $reject 'malformed-version'
        if ($Required) { throw "dotnet reported an unparseable SDK version." }
        return [pscustomobject]@{ Tool = 'dotnet'; Resolved = $null; Rejected = $rejected.ToArray() }
    }

    # Validate the selected SDK against the pin rather than asserting it. There
    # is deliberately no "assume satisfied" default: when no constraint was
    # requested, satisfaction is reported as null rather than true, because an
    # unasked question has no affirmative answer.
    $satisfies = $null
    if ($constraintRequested) {
        $satisfies = ($selected.Major -eq $pin.Major) -and ($selected.Minor -eq $pin.Minor)
        if ($satisfies -and $rollForward -eq 'latestPatch') {
            # latestPatch stays inside the pinned feature band (the hundreds
            # component of the patch) and never moves backwards.
            $satisfies = ([math]::Floor($selected.Build / 100) -eq [math]::Floor($pin.Build / 100)) -and ($selected.Build -ge $pin.Build)
        }
        elseif ($satisfies) {
            $satisfies = $selected -ge $pin
        }

        if (-not $satisfies) {
            & $reject 'pin-unsatisfied'
            if ($Required) { throw ("dotnet selected SDK {0}, which does not satisfy global.json {1} (rollForward {2})." -f $sdkVersion, $pinVersion, $rollForward) }
            return [pscustomobject]@{ Tool = 'dotnet'; Resolved = $null; Rejected = $rejected.ToArray() }
        }
    }

    return [pscustomobject]@{
        Tool = 'dotnet'
        Resolved = [pscustomobject]@{
            Ok = $true; Rule = $rule; Path = $path; Sha256 = (Get-CoachToolHash -Path $path)
            SdkVersion = $sdkVersion; GlobalJsonVersion = $pinVersion; RollForward = $rollForward
            SatisfiesPin = $satisfies
        }
        Rejected = $rejected.ToArray()
    }
}

function Get-CoachToolchainProvenance {
    <#
        Emits only the named fields. No unrelated environment value is captured.
    #>
    param(
        [object]$Python, [object]$Node, [object]$Dotnet,
        [string[]]$Required = @(), [string[]]$Optional = @(),
        [string]$Authority = 'local-diagnostic'
    )

    $rejected = New-Object System.Collections.Generic.List[object]
    foreach ($group in @($Python, $Node, $Dotnet)) {
        if ($null -ne $group -and $null -ne $group.Rejected) { foreach ($item in $group.Rejected) { $rejected.Add($item) | Out-Null } }
    }

    $pythonRecord = $null
    if ($null -ne $Python -and $null -ne $Python.Resolved) {
        $pythonRecord = [ordered]@{
            path = $Python.Resolved.Path; executable = $Python.Resolved.Executable
            version = $Python.Resolved.Version; sha256 = $Python.Resolved.Sha256
            rule = $Python.Resolved.Rule; minimum = $script:CoachPythonMinimum.ToString(); satisfiesMinimum = $true
        }
    }
    $nodeRecord = $null
    if ($null -ne $Node -and $null -ne $Node.Resolved) {
        $nodeRecord = [ordered]@{ path = $Node.Resolved.Path; version = $Node.Resolved.Version; sha256 = $Node.Resolved.Sha256; rule = $Node.Resolved.Rule }
    }
    $dotnetRecord = $null
    if ($null -ne $Dotnet -and $null -ne $Dotnet.Resolved) {
        $dotnetRecord = [ordered]@{
            path = $Dotnet.Resolved.Path; sha256 = $Dotnet.Resolved.Sha256
            sdkVersion = $Dotnet.Resolved.SdkVersion; globalJsonVersion = $Dotnet.Resolved.GlobalJsonVersion
            rollForward = $Dotnet.Resolved.RollForward; satisfiesPin = $Dotnet.Resolved.SatisfiesPin; rule = $Dotnet.Resolved.Rule
        }
    }

    return [ordered]@{
        toolchain = [ordered]@{
            python = $pythonRecord; node = $nodeRecord; dotnet = $dotnetRecord
            required = $Required; optional = $Optional
            authority = $Authority
            rejected = $rejected.ToArray()
        }
    }
}

function Assert-CoachToolchain {
    <#
        A required tool may never be null on a passing result.
        An optional tool that is absent is recorded and does not fail the caller.
    #>
    param([Parameter(Mandatory = $true)][object]$Provenance, [string[]]$Required = @())

    foreach ($tool in $Required) {
        if ($null -eq $Provenance.toolchain[$tool]) {
            throw ("Required tool '{0}' was not resolved. Rejections: {1}" -f $tool,
                (($Provenance.toolchain.rejected | Where-Object { $_.tool -eq $tool } | ForEach-Object { "$($_.rule)=$($_.reason)" }) -join ', '))
        }
    }
}

function Write-CoachToolchainProvenance {
    param([Parameter(Mandatory = $true)][object]$Provenance)
    $Provenance | ConvertTo-Json -Depth 6
}
