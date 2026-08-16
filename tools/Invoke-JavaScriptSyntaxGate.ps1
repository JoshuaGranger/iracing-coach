[CmdletBinding(DefaultParameterSetName = 'Tracked')]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryRoot,

    [Parameter(Mandatory = $true, ParameterSetName = 'Tracked')]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$SourceSha,

    [string]$NodePath,

    [ValidateSet('local-diagnostic', 'ci-source-gate')]
    [string]$Authority = 'local-diagnostic',

    [string]$EventName,

    [string]$OutputPath,

    [Parameter(Mandatory = $true, ParameterSetName = 'Synthetic')]
    [string[]]$CandidatePath,

    [Parameter(Mandatory = $true, ParameterSetName = 'Synthetic')]
    [string]$SyntheticRoot
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$limitations = @(
    'JavaScript syntax-only.',
    'Does not prove behavior, DOM, geometry, timing, rendering, host, or package behavior.'
)
$results = New-Object System.Collections.Generic.List[object]
$nodeEvidence = $null
$failure = $null
$discoveryComplete = $false
$mode = if ($PSCmdlet.ParameterSetName -eq 'Synthetic') { 'synthetic' } else { 'tracked-source' }
$normalizedSha = if ($mode -eq 'tracked-source') { $SourceSha.ToLowerInvariant() } else { $null }

function Normalize-RelativePath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $normalized = $Path.Replace('\', '/').Trim()
    while ($normalized.StartsWith('./', [StringComparison]::Ordinal)) {
        $normalized = $normalized.Substring(2)
    }
    if ([string]::IsNullOrWhiteSpace($normalized) -or [IO.Path]::IsPathRooted($normalized)) {
        throw 'invalid-relative-path'
    }
    $segments = @($normalized.Split('/'))
    if ($segments | Where-Object { $_ -eq '' -or $_ -eq '.' -or $_ -eq '..' }) {
        throw 'invalid-relative-path'
    }
    return ($segments -join '/')
}

function Test-FirstPartyJavaScript {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    $path = $RelativePath.Replace('\', '/')
    $lower = $path.ToLowerInvariant()
    if (
        $lower.Contains('/wwwroot/lib/') -or
        $lower.Contains('/node_modules/') -or
        $lower.Contains('/bin/') -or
        $lower.Contains('/obj/') -or
        $lower.Contains('/artifacts/') -or
        $lower.Contains('/generated/')
    ) {
        return $false
    }

    $uiPrefix = 'companion-app/src/iracingcoach.ui/wwwroot/'
    if ($lower.StartsWith($uiPrefix, [StringComparison]::Ordinal) -and $lower.EndsWith('.js', [StringComparison]::Ordinal)) {
        return $true
    }

    $previewPrefix = 'companion-app/src/iracingcoach.preview/'
    return $lower.StartsWith($previewPrefix, [StringComparison]::Ordinal) -and $lower.EndsWith('.razor.js', [StringComparison]::Ordinal)
}

function Test-StrictChildPath {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $prefix = $Root.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    return $Path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
}

function Test-ReparsePath {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $current = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    while ($null -ne $current -and (Test-StrictChildPath -Root $Root -Path $current.FullName)) {
        if (($current.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            return $true
        }
        if ($current -is [IO.DirectoryInfo]) {
            $current = $current.Parent
        }
        else {
            $current = $current.Directory
        }
    }
    return $false
}

function Invoke-GitText {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $output = @(& git @Arguments 2>$null)
    if ($LASTEXITCODE -ne 0) { throw 'git-command-failed' }
    return @($output | ForEach-Object { $_.ToString().Trim() } | Where-Object { $_ -ne '' })
}

function Write-AtomicUtf8 {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Text
    )

    $full = [IO.Path]::GetFullPath($Path)
    $parent = Split-Path -Parent $full
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) { throw 'output-parent-missing' }
    if (Test-Path -LiteralPath $full) { throw 'output-already-exists' }
    $temporary = $full + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($Text + [Environment]::NewLine)
    $stream = [IO.FileStream]::new($temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try {
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    }
    finally {
        $stream.Dispose()
    }
    [IO.File]::Move($temporary, $full)
}

try {
    $repository = [IO.Path]::GetFullPath($RepositoryRoot)
    if (-not (Test-Path -LiteralPath $repository -PathType Container)) { throw 'repository-root-missing' }

    $candidates = New-Object System.Collections.Generic.List[object]
    if ($mode -eq 'tracked-source') {
        $treePaths = Invoke-GitText -Arguments @('-C', $repository, 'ls-tree', '-r', '--name-only', $normalizedSha, '--', 'companion-app/src')
        foreach ($treePath in $treePaths) {
            $relative = Normalize-RelativePath -Path $treePath
            if (-not (Test-FirstPartyJavaScript -RelativePath $relative)) { continue }
            $fullPath = [IO.Path]::GetFullPath((Join-Path $repository $relative.Replace('/', [IO.Path]::DirectorySeparatorChar)))
            if (-not (Test-StrictChildPath -Root $repository -Path $fullPath)) { throw 'candidate-outside-repository' }
            $candidates.Add([pscustomobject]@{ Relative = $relative; Full = $fullPath }) | Out-Null
        }
    }
    else {
        $synthetic = [IO.Path]::GetFullPath($SyntheticRoot)
        if (-not (Test-Path -LiteralPath $synthetic -PathType Container)) { throw 'synthetic-root-missing' }
        foreach ($candidate in $CandidatePath) {
            $fullPath = if ([IO.Path]::IsPathRooted($candidate)) {
                [IO.Path]::GetFullPath($candidate)
            }
            else {
                [IO.Path]::GetFullPath((Join-Path $synthetic $candidate))
            }
            if (-not (Test-StrictChildPath -Root $synthetic -Path $fullPath)) { throw 'candidate-outside-synthetic-root' }
            $relative = Normalize-RelativePath -Path $fullPath.Substring($synthetic.TrimEnd('\', '/').Length + 1)
            if (-not (Test-FirstPartyJavaScript -RelativePath $relative)) { continue }
            $candidates.Add([pscustomobject]@{ Relative = $relative; Full = $fullPath }) | Out-Null
        }
    }

    $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    $candidateByPath = @{}
    foreach ($candidate in $candidates) {
        if (-not $seen.Add($candidate.Relative)) { throw 'duplicate-or-case-colliding-candidate' }
        $candidateByPath[$candidate.Relative] = $candidate
    }
    $relativePaths = [string[]]@($candidateByPath.Keys)
    [Array]::Sort($relativePaths, [StringComparer]::Ordinal)
    $orderedCandidates = @($relativePaths | ForEach-Object { $candidateByPath[$_] })
    if ($orderedCandidates.Count -eq 0) { throw 'zero-first-party-javascript-files' }

    $validationFailed = $false
    foreach ($candidate in $orderedCandidates) {
        $reason = $null
        if (-not (Test-Path -LiteralPath $candidate.Full -PathType Leaf)) {
            $reason = 'candidate-missing'
        }
        else {
            try {
                $root = if ($mode -eq 'synthetic') { $synthetic } else { $repository }
                if (Test-ReparsePath -Root $root -Path $candidate.Full) { $reason = 'reparse-point-refused' }
                if ($null -eq $reason) {
                    $probe = [IO.File]::Open($candidate.Full, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
                    $probe.Dispose()
                }
            }
            catch {
                $reason = 'candidate-unreadable'
            }
        }

        if ($null -eq $reason -and $mode -eq 'tracked-source') {
            try {
                $expected = @(Invoke-GitText -Arguments @('-C', $repository, 'rev-parse', ($normalizedSha + ':' + $candidate.Relative)))[0]
                $actual = @(Invoke-GitText -Arguments @('-C', $repository, 'hash-object', ('--path=' + $candidate.Relative), '--', $candidate.Full))[0]
                if (-not $expected.Equals($actual, [StringComparison]::OrdinalIgnoreCase)) {
                    $reason = 'source-byte-mismatch'
                }
            }
            catch {
                $reason = 'source-byte-binding-failed'
            }
        }

        if ($null -ne $reason) {
            $validationFailed = $true
            $results.Add([ordered]@{ path = $candidate.Relative; outcome = 'notRun'; reason = $reason }) | Out-Null
        }
    }
    if ($validationFailed) { throw 'candidate-validation-failed' }

    $discoveryComplete = $true
    . (Join-Path $PSScriptRoot 'Resolve-Toolchain.ps1')
    $nodeResult = Resolve-CoachNode -NodePath $NodePath -Required
    $provenance = Get-CoachToolchainProvenance -Node $nodeResult -Required @('node') -Authority $Authority
    Assert-CoachToolchain -Provenance $provenance -Required @('node')
    $nodeEvidence = [ordered]@{
        path = $nodeResult.Resolved.Path
        version = $nodeResult.Resolved.Version
        sha256 = $nodeResult.Resolved.Sha256
        rule = $nodeResult.Resolved.Rule
        rejected = @($nodeResult.Rejected | ForEach-Object {
            [ordered]@{ tool = $_.tool; rule = $_.rule; reason = $_.reason; path = $_.path }
        })
    }

    foreach ($candidate in $orderedCandidates) {
        & $nodeResult.Resolved.Path --check $candidate.Full *> $null
        $outcome = if ($LASTEXITCODE -eq 0) { 'passed' } else { 'failed' }
        $results.Add([ordered]@{ path = $candidate.Relative; outcome = $outcome; reason = $null }) | Out-Null
    }
}
catch {
    $failure = 'gate-failed'
}

$passed = @($results | Where-Object { $_.outcome -eq 'passed' }).Count
$failed = @($results | Where-Object { $_.outcome -eq 'failed' }).Count
$notRun = @($results | Where-Object { $_.outcome -eq 'notRun' }).Count
$record = [ordered]@{
    schemaVersion = 1
    gate = 'javascript-syntax-only'
    mode = $mode
    exactSha = $normalizedSha
    authority = $Authority
    eventName = if ([string]::IsNullOrWhiteSpace($EventName)) { $null } else { $EventName }
    tier = 'SourceContract'
    proves = 'JavaScript syntax-only'
    doesNotProve = @('behavior', 'DOM', 'geometry', 'timing', 'rendering', 'host', 'package')
    node = $nodeEvidence
    discoveryComplete = $discoveryComplete
    results = $results.ToArray()
    totals = [ordered]@{
        run = $passed + $failed
        passed = $passed
        failed = $failed
        skipped = 0
        notRun = $notRun
    }
    limitations = $limitations
    failure = $failure
}
$json = $record | ConvertTo-Json -Depth 12

if (-not [string]::IsNullOrWhiteSpace($OutputPath)) {
    try { Write-AtomicUtf8 -Path $OutputPath -Text $json }
    catch {
        [Console]::Error.WriteLine('JavaScript syntax gate could not publish its requested evidence file.')
        throw 'javascript-syntax-evidence-publication-failed'
    }
}

Write-Output $json
if ($null -ne $failure -or $failed -gt 0 -or $notRun -gt 0 -or -not $discoveryComplete) {
    throw 'JavaScript syntax-only gate failed.'
}
