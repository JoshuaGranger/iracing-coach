[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Installer
)

$ErrorActionPreference = 'Stop'
$installerPath = [System.IO.Path]::GetFullPath($Installer)
$checksumPath = "$installerPath.sha256"
if (-not (Test-Path -LiteralPath $installerPath) -or -not (Test-Path -LiteralPath $checksumPath)) {
    throw 'The installer and its checksum file are required.'
}

$declared = ((Get-Content -Raw -LiteralPath $checksumPath).Trim().Split(' ', [System.StringSplitOptions]::RemoveEmptyEntries))[0]
$actual = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($declared -ne $actual) { throw 'Release checksum mismatch.' }

$temporaryRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
$target = Join-Path $temporaryRoot ('iRacingCoach-Installer-Test-' + [guid]::NewGuid().ToString('N'))
$resolvedTarget = [System.IO.Path]::GetFullPath($target)
if (-not $resolvedTarget.StartsWith($temporaryRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
    -not [System.IO.Path]::GetFileName($resolvedTarget).StartsWith('iRacingCoach-Installer-Test-', [System.StringComparison]::Ordinal)) {
    throw 'Refusing to use an installer test target outside the guarded temporary folder.'
}

$app = $null
$uninstallSandbox = $null
$portableUpgradeRoot = Join-Path $temporaryRoot ('iRacingCoach-Archive-Test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path (Join-Path $portableUpgradeRoot 'data\driver-models') -Force | Out-Null
Set-Content -LiteralPath (Join-Path $portableUpgradeRoot 'data\driver-models\continuity.json') -Value '{"samples":42}' -Encoding ascii
$portableUpgradeHash = (Get-FileHash -LiteralPath (Join-Path $portableUpgradeRoot 'data\driver-models\continuity.json') -Algorithm SHA256).Hash
try {
    $first = Start-Process -FilePath $installerPath -ArgumentList @('--test-install', $resolvedTarget) -Wait -PassThru
    if ($first.ExitCode -ne 0) { throw "First install failed with exit code $($first.ExitCode)." }

    $required = @(
        'iRacing Coach.exe',
        'Uninstall iRacing Coach.exe',
        'python\python.exe',
        'iracing-coach\skills\analyze-iracing-race\scripts\mcp_server.py',
        'coach-engine\codex\codex.exe',
        'coach-engine\schemas\codex_app_server_protocol.schemas.json',
        'coach-engine\schemas\ai-coaching-output.schema.json',
        'coach-engine\schemas\ai-tuning-output.schema.json',
        'coach-engine\coach-engine-manifest.json',
        'release-manifest.json'
    )
    $missing = @($required | Where-Object { -not (Test-Path -LiteralPath (Join-Path $resolvedTarget $_)) })
    if ($missing.Count -gt 0) { throw "First install is missing: $($missing -join ', ')." }

    $validated = Start-Process -FilePath $installerPath -ArgumentList @('--test-validate-payload', $resolvedTarget) -WindowStyle Hidden -Wait -PassThru
    if ($validated.ExitCode -ne 0) { throw 'The installed exact payload did not match its complete manifest.' }
    $manifestCanary = Join-Path $resolvedTarget 'coach-engine\schemas\ai-coaching-output.schema.json'
    $manifestCanaryBytes = [System.IO.File]::ReadAllBytes($manifestCanary)
    try {
        [System.IO.File]::WriteAllBytes($manifestCanary, $manifestCanaryBytes + [byte]0)
        $modified = Start-Process -FilePath $installerPath -ArgumentList @('--test-validate-payload', $resolvedTarget) -WindowStyle Hidden -Wait -PassThru
        if ($modified.ExitCode -eq 0) { throw 'Complete payload validation accepted a modified manifested file.' }
        [System.IO.File]::WriteAllBytes($manifestCanary, $manifestCanaryBytes)

        Remove-Item -LiteralPath $manifestCanary -Force
        $missingManifested = Start-Process -FilePath $installerPath -ArgumentList @('--test-validate-payload', $resolvedTarget) -WindowStyle Hidden -Wait -PassThru
        if ($missingManifested.ExitCode -eq 0) { throw 'Complete payload validation accepted a missing manifested file.' }
        [System.IO.File]::WriteAllBytes($manifestCanary, $manifestCanaryBytes)

        $extraPayloadFile = Join-Path $resolvedTarget 'unmanifested-payload-canary.bin'
        [System.IO.File]::WriteAllBytes($extraPayloadFile, [byte[]](1, 2, 3))
        $extra = Start-Process -FilePath $installerPath -ArgumentList @('--test-validate-payload', $resolvedTarget) -WindowStyle Hidden -Wait -PassThru
        if ($extra.ExitCode -eq 0) { throw 'Complete payload validation accepted an unmanifested file.' }
        Remove-Item -LiteralPath $extraPayloadFile -Force
    }
    finally {
        if (-not (Test-Path -LiteralPath $manifestCanary)) { [System.IO.File]::WriteAllBytes($manifestCanary, $manifestCanaryBytes) }
        if ($extraPayloadFile -and (Test-Path -LiteralPath $extraPayloadFile)) { Remove-Item -LiteralPath $extraPayloadFile -Force }
    }

    $rollbackMarker = Join-Path $resolvedTarget 'rollback-preservation-marker.txt'
    Set-Content -LiteralPath $rollbackMarker -Value 'The original installed payload must return after simulated failure.' -Encoding ascii
    $rollback = Start-Process -FilePath $installerPath -ArgumentList @('--test-rollback', $resolvedTarget) -Wait -PassThru
    if ($rollback.ExitCode -ne 0) { throw "Simulated rollback failed with exit code $($rollback.ExitCode)." }
    if (-not (Test-Path -LiteralPath $rollbackMarker)) { throw 'Installer rollback did not restore the original installed payload.' }
    if (Test-Path -LiteralPath ($resolvedTarget + '.previous')) { throw 'Installer rollback left its backup folder behind.' }
    if (Test-Path -LiteralPath ($resolvedTarget + '.installing')) { throw 'Installer rollback left its staging folder behind.' }
    Remove-Item -LiteralPath $rollbackMarker -Force

    $marker = Join-Path $resolvedTarget 'old-version-marker.txt'
    Set-Content -LiteralPath $marker -Value 'This must be removed by the replacement install.' -Encoding ascii
    $app = Start-Process -FilePath (Join-Path $resolvedTarget 'iRacing Coach.exe') -ArgumentList '--minimized' -PassThru
    Start-Sleep -Seconds 5
    $app.Refresh()
    if ($app.HasExited) { throw 'The first installed app did not remain running for the upgrade test.' }

    $second = Start-Process -FilePath $installerPath -ArgumentList @('--test-install', $resolvedTarget) -Wait -PassThru
    if ($second.ExitCode -ne 0) { throw "Replacement install failed with exit code $($second.ExitCode)." }
    Start-Sleep -Milliseconds 500
    $app.Refresh()
    if (-not $app.HasExited) { throw 'The replacement install did not stop the running prior version.' }
    if (Test-Path -LiteralPath $marker) { throw 'The replacement install left the prior-version marker behind.' }
    if (Test-Path -LiteralPath ($resolvedTarget + '.previous')) { throw 'The replacement install left its backup folder behind.' }
    if (Test-Path -LiteralPath ($resolvedTarget + '.installing')) { throw 'The replacement install left its staging folder behind.' }
    if ($portableUpgradeHash -ne (Get-FileHash -LiteralPath (Join-Path $portableUpgradeRoot 'data\driver-models\continuity.json') -Algorithm SHA256).Hash) { throw 'Upgrade changed portable learned data.' }

    $missingAfter = @($required | Where-Object { -not (Test-Path -LiteralPath (Join-Path $resolvedTarget $_)) })
    if ($missingAfter.Count -gt 0) { throw "Replacement install is missing: $($missingAfter -join ', ')." }

    $uninstallSandbox = Join-Path $temporaryRoot ('iRacingCoach-Uninstall-Test-' + [guid]::NewGuid().ToString('N'))
    $portableArchive = Join-Path $uninstallSandbox 'Documents\iRacing Coach'
    $iracingSource = Join-Path $uninstallSandbox 'Documents\iRacing'
    $ownedRoots = @(
        (Join-Path $uninstallSandbox 'LocalAppData\iRacingCoach'),
        (Join-Path $uninstallSandbox 'LocalAppData\iRacing Coach'),
        (Join-Path $uninstallSandbox 'RoamingAppData\iRacingCoach'),
        (Join-Path $uninstallSandbox 'RoamingAppData\iRacing Coach'),
        (Join-Path $uninstallSandbox 'ProgramData\iRacingCoach'),
        (Join-Path $uninstallSandbox 'ProgramFiles\iRacing Coach')
    )
    New-Item -ItemType Directory -Path $portableArchive, $iracingSource -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $portableArchive 'archive-sentinel.bin') -Value 'portable data must survive' -Encoding ascii
    Set-Content -LiteralPath (Join-Path $iracingSource 'source-sentinel.ibt') -Value 'source telemetry must survive' -Encoding ascii
    foreach ($ownedRoot in $ownedRoots) {
        New-Item -ItemType Directory -Path $ownedRoot -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $ownedRoot 'owned.tmp') -Value 'remove me' -Encoding ascii
    }
    $credentialFixture = Join-Path $uninstallSandbox 'LocalAppData\iRacingCoach\credentials\garage61.pat.dpapi'
    $codexFixture = Join-Path $uninstallSandbox 'LocalAppData\iRacingCoach\CoachEngine\CodexHome\auth.json'
    New-Item -ItemType Directory -Path (Split-Path $credentialFixture), (Split-Path $codexFixture) -Force | Out-Null
    Set-Content -LiteralPath $credentialFixture -Value 'machine-bound credential fixture' -Encoding ascii
    Set-Content -LiteralPath $codexFixture -Value '{"private":true}' -Encoding ascii
    $portableHashBefore = (Get-FileHash -LiteralPath (Join-Path $portableArchive 'archive-sentinel.bin') -Algorithm SHA256).Hash
    $sourceHashBefore = (Get-FileHash -LiteralPath (Join-Path $iracingSource 'source-sentinel.ibt') -Algorithm SHA256).Hash
    $clean = Start-Process -FilePath (Join-Path $resolvedTarget 'Uninstall iRacing Coach.exe') -ArgumentList @('--test-clean-state', $uninstallSandbox) -Wait -PassThru
    if ($clean.ExitCode -ne 0) { throw "Guarded machine-state cleanup failed with exit code $($clean.ExitCode)." }
    $leftovers = @($ownedRoots | Where-Object { Test-Path -LiteralPath $_ })
    if ($leftovers.Count -gt 0) { throw "Uninstaller left app-owned test roots: $($leftovers -join ', ')." }
    if ($portableHashBefore -ne (Get-FileHash -LiteralPath (Join-Path $portableArchive 'archive-sentinel.bin') -Algorithm SHA256).Hash) { throw 'Uninstaller changed the durable Coach archive.' }
    if ($sourceHashBefore -ne (Get-FileHash -LiteralPath (Join-Path $iracingSource 'source-sentinel.ibt') -Algorithm SHA256).Hash) { throw 'Uninstaller changed the iRacing source folder.' }

    $remove = Start-Process -FilePath (Join-Path $resolvedTarget 'Uninstall iRacing Coach.exe') -ArgumentList @('--test-run', $resolvedTarget) -Wait -PassThru
    $removeDeadline = [DateTime]::UtcNow.AddSeconds(30)
    while ((Test-Path -LiteralPath $resolvedTarget) -and [DateTime]::UtcNow -lt $removeDeadline) { Start-Sleep -Milliseconds 200 }
    if ($remove.ExitCode -ne 0 -or (Test-Path -LiteralPath $resolvedTarget)) { throw 'Guarded uninstall did not remove the installed app folder.' }
    $runnerDeadline = [DateTime]::UtcNow.AddSeconds(30)
    while (@(Get-ChildItem -LiteralPath $temporaryRoot -Filter 'iRacingCoach-Uninstall-TestRunner-*.exe' -ErrorAction SilentlyContinue).Count -gt 0 -and [DateTime]::UtcNow -lt $runnerDeadline) { Start-Sleep -Milliseconds 200 }
    if (@(Get-ChildItem -LiteralPath $temporaryRoot -Filter 'iRacingCoach-Uninstall-TestRunner-*.exe' -ErrorAction SilentlyContinue).Count -gt 0) { throw 'Guarded uninstall left its detached test runner behind.' }

    $reinstall = Start-Process -FilePath $installerPath -ArgumentList @('--test-install', $resolvedTarget) -Wait -PassThru
    if ($reinstall.ExitCode -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $resolvedTarget 'iRacing Coach.exe'))) { throw 'Reinstall after clean uninstall failed.' }
    $removeAgain = Start-Process -FilePath (Join-Path $resolvedTarget 'Uninstall iRacing Coach.exe') -ArgumentList @('--test-run', $resolvedTarget) -Wait -PassThru
    $removeAgainDeadline = [DateTime]::UtcNow.AddSeconds(30)
    while ((Test-Path -LiteralPath $resolvedTarget) -and [DateTime]::UtcNow -lt $removeAgainDeadline) { Start-Sleep -Milliseconds 200 }
    if ($removeAgain.ExitCode -ne 0 -or (Test-Path -LiteralPath $resolvedTarget)) { throw 'The reinstalled app did not uninstall cleanly.' }
    $runnerAgainDeadline = [DateTime]::UtcNow.AddSeconds(30)
    while (@(Get-ChildItem -LiteralPath $temporaryRoot -Filter 'iRacingCoach-Uninstall-TestRunner-*.exe' -ErrorAction SilentlyContinue).Count -gt 0 -and [DateTime]::UtcNow -lt $runnerAgainDeadline) { Start-Sleep -Milliseconds 200 }
    if (@(Get-ChildItem -LiteralPath $temporaryRoot -Filter 'iRacingCoach-Uninstall-TestRunner-*.exe' -ErrorAction SilentlyContinue).Count -gt 0) { throw 'The reinstalled app left its detached uninstall runner behind.' }
    Remove-Item -LiteralPath $uninstallSandbox -Recurse -Force

    [pscustomobject]@{
        Checksum = $actual
        InstallerBytes = (Get-Item -LiteralPath $installerPath).Length
        FirstInstallExit = $first.ExitCode
        PriorAppStopped = $app.HasExited
        SimulatedRollbackExit = $rollback.ExitCode
        RollbackPreservedPriorPayload = $true
        ReplacementExit = $second.ExitCode
        PriorMarkerRemoved = $true
        UpgradePreservedDurableHash = $true
        RequiredFiles = $required.Count
        CompletePayloadValidated = $true
        ModifiedPayloadRefused = $modified.ExitCode -ne 0
        MissingPayloadRefused = $missingManifested.ExitCode -ne 0
        ExtraPayloadRefused = $extra.ExitCode -ne 0
        StagingClean = $true
        AppOwnedRootsRemoved = $ownedRoots.Count
        DurableArchiveHashPreserved = $true
        IRacingSourceHashPreserved = $true
        InstalledAppRemoved = $true
        ReinstallExit = $reinstall.ExitCode
        ReinstalledAppRemoved = $true
        CredentialFixtureRemoved = $true
        PrivateCodexFixtureRemoved = $true
    }
}
finally {
    if ($app -and -not $app.HasExited) { Stop-Process -Id $app.Id -Force }
    foreach ($path in @($resolvedTarget, ($resolvedTarget + '.previous'), ($resolvedTarget + '.installing'))) {
        $resolved = [System.IO.Path]::GetFullPath($path)
        if (-not $resolved.StartsWith($temporaryRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
            -not [System.IO.Path]::GetFileName($resolved).StartsWith('iRacingCoach-Installer-Test-', [System.StringComparison]::Ordinal)) {
            throw "Refusing to clean an unguarded path: $resolved"
        }
        if ([System.IO.Directory]::Exists($resolved)) { [System.IO.Directory]::Delete($resolved, $true) }
    }
    if ($uninstallSandbox) {
        $resolvedSandbox = [System.IO.Path]::GetFullPath($uninstallSandbox)
        if (-not $resolvedSandbox.StartsWith($temporaryRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
            -not [System.IO.Path]::GetFileName($resolvedSandbox).StartsWith('iRacingCoach-Uninstall-Test-', [System.StringComparison]::Ordinal)) {
            throw "Refusing to clean an unguarded uninstall sandbox: $resolvedSandbox"
        }
        if ([System.IO.Directory]::Exists($resolvedSandbox)) { [System.IO.Directory]::Delete($resolvedSandbox, $true) }
    }
    $resolvedArchiveTest = [System.IO.Path]::GetFullPath($portableUpgradeRoot)
    if (-not $resolvedArchiveTest.StartsWith($temporaryRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
        -not [System.IO.Path]::GetFileName($resolvedArchiveTest).StartsWith('iRacingCoach-Archive-Test-', [System.StringComparison]::Ordinal)) {
        throw "Refusing to clean an unguarded archive test root: $resolvedArchiveTest"
    }
    if ([System.IO.Directory]::Exists($resolvedArchiveTest)) { [System.IO.Directory]::Delete($resolvedArchiveTest, $true) }
}
