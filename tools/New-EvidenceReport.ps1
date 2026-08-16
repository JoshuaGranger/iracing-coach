[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RepositoryRoot,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{40}$')][string]$SourceSha,
    [Parameter(Mandatory = $true)][ValidateSet('dotnet', 'backend', 'devtools', 'javascript')][string]$Family,
    [Parameter(Mandatory = $true)][string]$InputPath,
    [string]$RegistryPath,
    [Parameter(Mandatory = $true)][ValidateSet('local-diagnostic', 'ci-source-gate')][string]$Authority,
    [Parameter(Mandatory = $true)][object]$ToolchainProvenance,
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [AllowNull()][string]$Filter,
    [string]$CatalogPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$tiers = @('SourceContract', 'Fixture', 'Behavioral', 'Rendered', 'Package')
$techniques = @('none', 'fault', 'barrier', 'mutation')
$classificationLimitation = 'Tier assignments are author-asserted; independent review is sampling-based rather than exhaustive. Until the immutable family review is accepted, this report is review-pending.'
$javascriptLimitations = @(
    'JavaScript syntax-only.',
    'Does not prove behavior, DOM, geometry, timing, rendering, host, or package behavior.'
)
$normalizedSha = $SourceSha.ToLowerInvariant()

function Get-PropertyValue {
    param([Parameter(Mandatory = $true)][object]$Object, [Parameter(Mandatory = $true)][string]$Name)
    if ($Object -is [Collections.IDictionary]) {
        if (-not $Object.Contains($Name)) { throw "missing-property:$Name" }
        return $Object[$Name]
    }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { throw "missing-property:$Name" }
    return $property.Value
}

function Assert-ExactProperties {
    param([Parameter(Mandatory = $true)][object]$Object, [Parameter(Mandatory = $true)][string[]]$Required, [string[]]$Optional = @())
    $actual = @($Object.PSObject.Properties.Name)
    foreach ($name in $Required) {
        if ($actual -cnotcontains $name) { throw "missing-property:$name" }
    }
    $allowed = @($Required + $Optional)
    foreach ($name in $actual) {
        if ($allowed -cnotcontains $name) { throw "unknown-property:$name" }
    }
}

function ConvertFrom-JsonFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw 'missing-input' }
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'reparse-input' }
    return (Get-Content -LiteralPath $item.FullName -Raw -Encoding UTF8 | ConvertFrom-Json)
}

function Write-AtomicJson {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][object]$Value)
    $full = [IO.Path]::GetFullPath($Path)
    $parent = Split-Path -Parent $full
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) { throw 'missing-output-parent' }
    $temporary = Join-Path $parent ('.' + [IO.Path]::GetFileName($full) + '.' + [guid]::NewGuid().ToString('N') + '.tmp')
    $json = ($Value | ConvertTo-Json -Depth 16) + [Environment]::NewLine
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($json)
    $stream = [IO.FileStream]::new($temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try {
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
    }
    finally { $stream.Dispose() }
    [IO.File]::Move($temporary, $full)
}

function Normalize-RepositoryPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $value = $Path.Replace('\', '/').Trim()
    if ([string]::IsNullOrWhiteSpace($value) -or [IO.Path]::IsPathRooted($value)) { throw 'invalid-repository-path' }
    $segments = @($value.Split('/'))
    if ($segments | Where-Object { $_ -eq '' -or $_ -eq '.' -or $_ -eq '..' }) { throw 'invalid-repository-path' }
    return ($segments -join '/')
}

function Assert-TreePath {
    param([Parameter(Mandatory = $true)][string]$RelativePath)
    $normalized = Normalize-RepositoryPath -Path $RelativePath
    $specification = $normalizedSha + ':' + $normalized
    & git -C $script:repository cat-file -e $specification 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'missing-source-path' }
    $entry = @(& git -C $script:repository ls-tree $normalizedSha -- $normalized 2>$null)
    if ($LASTEXITCODE -ne 0 -or $entry.Count -ne 1 -or $entry[0] -match '^120000\s') { throw 'invalid-source-path' }
    return $normalized
}

function Get-MethodTokens {
    param([Parameter(Mandatory = $true)][string]$Identity)
    $method = ($Identity -split '\.')[-1]
    return @([regex]::Matches($method, '[A-Z]+(?=[A-Z][a-z]|\b)|[A-Z]?[a-z]+|[0-9]+') | ForEach-Object { $_.Value.ToLowerInvariant() })
}

function Test-NameMismatch {
    param([Parameter(Mandatory = $true)][string]$Identity, [Parameter(Mandatory = $true)][string]$Tier)
    if ($Tier -ne 'SourceContract') { return $false }
    $verbs = @('renders', 'displays', 'shows', 'calculates', 'publishes', 'handles', 'prevents', 'ensures')
    foreach ($token in @(Get-MethodTokens -Identity $Identity)) {
        if ($verbs -ccontains $token) { return $true }
    }
    return $false
}

function Get-Toolchain {
    $value = Get-PropertyValue -Object $ToolchainProvenance -Name 'toolchain'
    if ($null -eq $value) { throw 'missing-toolchain-provenance' }
    return $value
}

function Read-Registry {
    if ($Family -eq 'javascript') { return @{} }
    if ([string]::IsNullOrWhiteSpace($RegistryPath)) { throw 'missing-registry' }
    $registry = ConvertFrom-JsonFile -Path $RegistryPath
    Assert-ExactProperties -Object $registry -Required @('schemaVersion', 'declarations')
    if ([int](Get-PropertyValue -Object $registry -Name 'schemaVersion') -ne 1) { throw 'registry-schema-version' }

    $selected = @{}
    $seen = @{}
    foreach ($declaration in @(Get-PropertyValue -Object $registry -Name 'declarations')) {
        Assert-ExactProperties -Object $declaration -Required @('family', 'id', 'tier', 'technique') -Optional @('fixtureSources')
        $declarationFamily = [string](Get-PropertyValue -Object $declaration -Name 'family')
        $identity = [string](Get-PropertyValue -Object $declaration -Name 'id')
        $tier = [string](Get-PropertyValue -Object $declaration -Name 'tier')
        $technique = [string](Get-PropertyValue -Object $declaration -Name 'technique')
        if (@('dotnet', 'backend', 'devtools') -cnotcontains $declarationFamily) { throw 'unknown-declaration-family' }
        if ([string]::IsNullOrWhiteSpace($identity)) { throw 'empty-declaration-id' }
        if ($tiers -cnotcontains $tier) { throw 'unknown-tier' }
        if ($techniques -cnotcontains $technique) { throw 'unknown-technique' }
        $key = $declarationFamily + "`0" + $identity
        if ($seen.ContainsKey($key)) { throw 'duplicate-declaration' }
        $seen[$key] = $true

        $fixtureProperty = $declaration.PSObject.Properties['fixtureSources']
        if ($tier -eq 'Fixture') {
            if ($null -eq $fixtureProperty -or @($fixtureProperty.Value).Count -eq 0) { throw 'missing-fixture-sources' }
            $fixtureKeys = @{}
            foreach ($source in @($fixtureProperty.Value)) {
                Assert-ExactProperties -Object $source -Required @('kind', 'value') -Optional @('sourcePath')
                $kind = [string](Get-PropertyValue -Object $source -Name 'kind')
                $value = [string](Get-PropertyValue -Object $source -Name 'value')
                if ($kind -eq 'path') {
                    if ($null -ne $source.PSObject.Properties['sourcePath']) { throw 'unexpected-provider-source-path' }
                    $canonical = Assert-TreePath -RelativePath $value
                    $fixtureKey = 'path:' + $canonical
                }
                elseif ($kind -eq 'provider') {
                    if ([string]::IsNullOrWhiteSpace($value)) { throw 'empty-provider-id' }
                    $providerSource = [string](Get-PropertyValue -Object $source -Name 'sourcePath')
                    $canonical = Assert-TreePath -RelativePath $providerSource
                    $fixtureKey = 'provider:' + $value + ':' + $canonical
                }
                else { throw 'unknown-fixture-kind' }
                if ($fixtureKeys.ContainsKey($fixtureKey)) { throw 'duplicate-fixture-source' }
                $fixtureKeys[$fixtureKey] = $true
            }
        }
        elseif ($null -ne $fixtureProperty) { throw 'fixture-sources-on-non-fixture' }

        if ($declarationFamily -eq $Family) {
            $selected[$identity] = [pscustomobject]@{ Tier = $tier; Technique = $technique }
        }
    }
    if ($selected.Count -eq 0) { throw 'family-not-declared' }
    return $selected
}

function Read-PythonRaw {
    param([Parameter(Mandatory = $true)][string]$Path, [switch]$RequireComplete)
    $raw = ConvertFrom-JsonFile -Path $Path
    Assert-ExactProperties -Object $raw -Required @('schemaVersion', 'family', 'discoveryComplete', 'runState', 'filter', 'results', 'failure')
    if ([int](Get-PropertyValue -Object $raw -Name 'schemaVersion') -ne 1) { throw 'raw-schema-version' }
    if ([string](Get-PropertyValue -Object $raw -Name 'family') -ne $Family) { throw 'raw-family-mismatch' }
    $state = [string](Get-PropertyValue -Object $raw -Name 'runState')
    if ($state -eq 'invalid' -or -not [bool](Get-PropertyValue -Object $raw -Name 'discoveryComplete')) { throw 'invalid-raw-run' }
    if ($RequireComplete -and $state -ne 'complete') { throw 'catalog-not-complete' }
    if (@('complete', 'partial') -cnotcontains $state) { throw 'unknown-run-state' }
    $records = New-Object System.Collections.Generic.List[object]
    foreach ($item in @(Get-PropertyValue -Object $raw -Name 'results')) {
        Assert-ExactProperties -Object $item -Required @('id', 'displayId', 'outcome', 'durationMs', 'skipReason')
        $outcome = [string](Get-PropertyValue -Object $item -Name 'outcome')
        if (@('passed', 'failed', 'skipped', 'notRun') -cnotcontains $outcome) { throw 'unknown-outcome' }
        $records.Add([pscustomobject]@{
            ParentId = [string](Get-PropertyValue -Object $item -Name 'id')
            Id = [string](Get-PropertyValue -Object $item -Name 'id')
            DisplayId = [string](Get-PropertyValue -Object $item -Name 'displayId')
            Outcome = $outcome
            DurationMs = [double](Get-PropertyValue -Object $item -Name 'durationMs')
        }) | Out-Null
    }
    if ($records.Count -eq 0) { throw 'zero-discovery' }
    return [pscustomobject]@{ State = $state; Records = $records.ToArray(); Filter = (Get-PropertyValue -Object $raw -Name 'filter') }
}

function Read-TrxRaw {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw 'missing-trx' }
    [xml]$document = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    $manager = [Xml.XmlNamespaceManager]::new($document.NameTable)
    $manager.AddNamespace('t', 'http://microsoft.com/schemas/VisualStudio/TeamTest/2010')
    $summary = $document.SelectSingleNode('//t:ResultSummary', $manager)
    if ($null -eq $summary -or @('Completed', 'Passed', 'Failed') -cnotcontains [string]$summary.outcome) { throw 'invalid-trx-summary' }
    $parents = @{}
    foreach ($test in @($document.SelectNodes('//t:TestDefinitions/t:UnitTest', $manager))) {
        $method = $test.SelectSingleNode('t:TestMethod', $manager)
        if ($null -eq $method) { throw 'missing-test-method' }
        $name = [regex]::Replace([string]$method.name, '\s*\(.*\)$', '')
        $parents[[string]$test.id] = ([string]$method.className + '.' + $name)
    }
    $records = New-Object System.Collections.Generic.List[object]
    foreach ($result in @($document.SelectNodes('//t:Results/t:UnitTestResult', $manager))) {
        $testId = [string]$result.testId
        if (-not $parents.ContainsKey($testId)) { throw 'orphan-trx-result' }
        $parent = [string]$parents[$testId]
        $display = [string]$result.testName
        $normalizedOutcome = switch ([string]$result.outcome) {
            'Passed' { 'passed' }
            'Failed' { 'failed' }
            'Error' { 'failed' }
            'Timeout' { 'failed' }
            'NotExecuted' { 'skipped' }
            default { throw 'invalid-trx-outcome' }
        }
        $duration = [TimeSpan]::Zero
        if (-not [string]::IsNullOrWhiteSpace([string]$result.duration)) {
            $duration = [TimeSpan]::Parse([string]$result.duration, [Globalization.CultureInfo]::InvariantCulture)
        }
        $resultId = if ($display -eq $parent -or $display -eq (($parent -split '\.')[-1])) { $parent } else { $parent + ' :: ' + $display }
        $records.Add([pscustomobject]@{
            ParentId = $parent; Id = $resultId; DisplayId = $display; Outcome = $normalizedOutcome
            DurationMs = [math]::Round($duration.TotalMilliseconds, 3)
        }) | Out-Null
    }
    if ($records.Count -eq 0) { throw 'zero-discovery' }
    return [pscustomobject]@{ State = $(if ([string]::IsNullOrWhiteSpace($Filter)) { 'complete' } else { 'partial' }); Records = $records.ToArray(); Filter = $Filter }
}

function Read-JavaScriptRaw {
    $raw = ConvertFrom-JsonFile -Path $InputPath
    if ([int](Get-PropertyValue -Object $raw -Name 'schemaVersion') -ne 1 -or [string](Get-PropertyValue -Object $raw -Name 'gate') -ne 'javascript-syntax-only') { throw 'javascript-schema' }
    if ([string](Get-PropertyValue -Object $raw -Name 'mode') -ne 'tracked-source') { throw 'javascript-not-tracked' }
    if ([string](Get-PropertyValue -Object $raw -Name 'exactSha') -ne $normalizedSha) { throw 'javascript-sha-mismatch' }
    if (-not [bool](Get-PropertyValue -Object $raw -Name 'discoveryComplete')) { throw 'javascript-discovery-incomplete' }
    $records = New-Object System.Collections.Generic.List[object]
    foreach ($item in @(Get-PropertyValue -Object $raw -Name 'results')) {
        $path = [string](Get-PropertyValue -Object $item -Name 'path')
        $outcome = [string](Get-PropertyValue -Object $item -Name 'outcome')
        if (@('passed', 'failed', 'notRun') -cnotcontains $outcome) { throw 'javascript-outcome' }
        $records.Add([pscustomobject]@{ ParentId = $path; Id = $path; DisplayId = $path; Outcome = $outcome; DurationMs = 0.0 }) | Out-Null
    }
    if ($records.Count -eq 0) { throw 'zero-discovery' }
    return [pscustomobject]@{ State = 'complete'; Records = $records.ToArray(); Filter = $null }
}

function New-TierTotals {
    $totals = [ordered]@{}
    foreach ($tier in $tiers) {
        $totals[$tier] = [ordered]@{ run = 0; passed = 0; failed = 0; skipped = 0; notRun = 0 }
    }
    return $totals
}

function New-InvalidReport {
    param([Parameter(Mandatory = $true)][object]$Toolchain)
    return [ordered]@{
        schemaVersion = 1; family = $Family; exactSha = $normalizedSha; authority = $Authority
        discoveryComplete = $false; runState = 'invalid'; filter = $(if ([string]::IsNullOrWhiteSpace($Filter)) { $null } else { $Filter })
        toolchain = $Toolchain; results = @()
        limitations = [string[]]$(if ($Family -eq 'javascript') { $javascriptLimitations } else { @($classificationLimitation) })
    }
}

$script:repository = [IO.Path]::GetFullPath($RepositoryRoot)
$report = $null
$failed = $false
$invalid = $false
$toolchain = [ordered]@{}
try {
    if (-not (Test-Path -LiteralPath $script:repository -PathType Container)) { throw 'missing-repository' }
    $resolvedHead = @(& git -C $script:repository rev-parse $normalizedSha 2>$null)
    if ($LASTEXITCODE -ne 0 -or $resolvedHead.Count -ne 1 -or $resolvedHead[0].Trim().ToLowerInvariant() -ne $normalizedSha) { throw 'unknown-source-sha' }
    & git -C $script:repository diff --quiet $normalizedSha --
    if ($LASTEXITCODE -ne 0) { throw 'source-tree-divergence' }
    $untracked = @(& git -C $script:repository ls-files --others --exclude-standard)
    if ($LASTEXITCODE -ne 0 -or $untracked.Count -ne 0) { throw 'untracked-source-divergence' }
    $toolchain = Get-Toolchain
    $declarations = Read-Registry

    if ($Family -eq 'javascript') { $rawRun = Read-JavaScriptRaw }
    elseif ($Family -eq 'dotnet') { $rawRun = Read-TrxRaw -Path $InputPath }
    else { $rawRun = Read-PythonRaw -Path $InputPath }

    $catalogParents = @{}
    if ($rawRun.State -eq 'partial') {
        if ([string]::IsNullOrWhiteSpace($CatalogPath)) { throw 'missing-complete-catalog' }
        if ($Family -eq 'dotnet') { $catalog = Read-TrxRaw -Path $CatalogPath }
        else { $catalog = Read-PythonRaw -Path $CatalogPath -RequireComplete }
        if ($catalog.State -ne 'complete') { throw 'catalog-not-complete' }
        foreach ($item in @($catalog.Records)) { $catalogParents[[string]$item.ParentId] = $true }
    }
    else { foreach ($item in @($rawRun.Records)) { $catalogParents[[string]$item.ParentId] = $true } }

    if ($Family -ne 'javascript') {
        foreach ($identity in @($catalogParents.Keys)) { if (-not $declarations.ContainsKey($identity)) { throw 'missing-declaration' } }
        foreach ($identity in @($declarations.Keys)) { if (-not $catalogParents.ContainsKey($identity)) { throw 'stale-declaration' } }
    }

    $normalizedResults = New-Object System.Collections.Generic.List[object]
    $totals = New-TierTotals
    foreach ($item in @($rawRun.Records | Sort-Object -Property @{Expression = 'Id'; Ascending = $true}, @{Expression = 'DisplayId'; Ascending = $true})) {
        if ($Family -eq 'javascript') { $tier = 'SourceContract'; $technique = 'none' }
        else { $tier = [string]$declarations[[string]$item.ParentId].Tier; $technique = [string]$declarations[[string]$item.ParentId].Technique }
        $outcome = [string]$item.Outcome
        $normalizedResults.Add([ordered]@{
            id = [string]$item.Id; displayId = [string]$item.DisplayId; tier = $tier; technique = $technique
            outcome = $outcome; durationMs = [double]$item.DurationMs
            nameMismatch = [bool](Test-NameMismatch -Identity ([string]$item.ParentId) -Tier $tier)
        }) | Out-Null
        if ($outcome -eq 'notRun') { $totals[$tier].notRun++ }
        else { $totals[$tier].run++; $totals[$tier][$outcome]++ }
        if ($outcome -eq 'failed') { $failed = $true }
    }

    $report = [ordered]@{
        schemaVersion = 1; family = $Family; exactSha = $normalizedSha; authority = $Authority
        discoveryComplete = $true; runState = [string]$rawRun.State; filter = $rawRun.Filter
        toolchain = $toolchain; results = $normalizedResults.ToArray(); totals = $totals
        limitations = [string[]]$(if ($Family -eq 'javascript') { $javascriptLimitations } else { @($classificationLimitation) })
    }
}
catch {
    $invalid = $true
    $report = New-InvalidReport -Toolchain $toolchain
}

Write-AtomicJson -Path $OutputPath -Value $report
if ($invalid) { throw 'evidence-report-invalid' }
if ($failed) { throw 'evidence-report-failed' }
