<# Real installer lifecycle smoke. All collector state, named objects and Run keys are isolated. #>
[CmdletBinding()]
param([string]$InnoCompilerPath)
$ErrorActionPreference = 'Stop'
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$testRoot = Join-Path $repository ('target/companion/smoke-' + [Guid]::NewGuid().ToString('N'))
$testInstall = Join-Path $testRoot 'installed'
$testState = Join-Path $testRoot 'state'
$testIracing = Join-Path $testRoot 'iracing'
$testRunKey = 'Software\iRacingCoach\InstallerSmoke\' + [Guid]::NewGuid().ToString('N')
$savedEnvironment = @{}
foreach ($name in @('COACH_TEST_INSTANCE_ID','COACH_TEST_RUN_KEY','COACH_TEST_STATE_DIR','COACH_TEST_IRACING_ROOT')) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

function Assert-Condition([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}
function Invoke-Installer([string]$Path, [string[]]$InstallerArguments, [int]$ExpectedExit = 0) {
    # Start-Process -Wait includes descendants, including the deliberately persistent collector.
    $process = Start-Process -FilePath $Path -ArgumentList $InstallerArguments -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(60000)) {
        $process.Kill(); $process.WaitForExit()
        throw 'Installer smoke timed out.'
    }
    Assert-Condition ($process.ExitCode -eq $ExpectedExit) "Unexpected installer exit $($process.ExitCode), expected $ExpectedExit."
}
function Read-Configuration { Get-Content -LiteralPath (Join-Path $testState 'collector.json') -Raw | ConvertFrom-Json }

try {
    Assert-Condition (-not (Get-Process -Name iRacingSim64DX11 -ErrorAction SilentlyContinue)) 'Close iRacing before running installer smoke.'
    New-Item -ItemType Directory -Path $testInstall,$testState,(Join-Path $testIracing 'telemetry'),(Join-Path $testIracing 'setups') -Force | Out-Null
    $env:COACH_TEST_INSTANCE_ID = [Guid]::NewGuid().ToString('N')
    $env:COACH_TEST_RUN_KEY = $testRunKey
    $env:COACH_TEST_STATE_DIR = $testState
    $env:COACH_TEST_IRACING_ROOT = $testIracing
    & (Join-Path $PSScriptRoot 'Build-Companion.ps1') -SmokeTest -ServerUrl 'http://127.0.0.1:9' -InnoCompilerPath $InnoCompilerPath
    $setup = Join-Path $repository 'target/companion/iRacingCoachCompanionSetup-test.exe'
    $installArgs = @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',"/DIR=`"$testInstall`"","/LOG=`"$(Join-Path $testRoot 'install.log')`"")
    Invoke-Installer $setup $installArgs
    $collector = Join-Path $testInstall 'coach-collector.exe'
    Assert-Condition (Test-Path -LiteralPath $collector) 'Installed collector is missing.'
    Assert-Condition (Test-Path -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\iRacingCoachCompanionSmoke_is1') 'Windows installed-app entry is missing.'
    $first = Read-Configuration
    Assert-Condition ($first.server -eq 'http://127.0.0.1:9') 'Fresh install did not configure the intended server.'
    $run = (Get-ItemProperty -LiteralPath "HKCU:\$testRunKey").'iRacing Coach Collector'
    Assert-Condition ($run -like "*${collector}*run*") 'Startup command does not target the installed collector.'
    & $collector stop
    Assert-Condition ($LASTEXITCODE -eq 0) 'Could not stop the isolated collector.'
    # A harmless copied ping process proves setup refuses to change a running sim.
    $dummySimPath = Join-Path $testRoot 'iRacingSim64DX11.exe'
    Copy-Item -LiteralPath (Join-Path $env:SystemRoot 'System32/ping.exe') -Destination $dummySimPath
    $dummySim = Start-Process -FilePath $dummySimPath -ArgumentList '-n','30','127.0.0.1' -WindowStyle Hidden -PassThru
    try {
        $beforeBlockedInstall = (Get-FileHash -LiteralPath (Join-Path $testState 'collector.json') -Algorithm SHA256).Hash
        Invoke-Installer $setup $installArgs 1
        Assert-Condition ((Get-FileHash -LiteralPath (Join-Path $testState 'collector.json') -Algorithm SHA256).Hash -eq $beforeBlockedInstall) 'Blocked install changed settings.'
    } finally {
        if (-not $dummySim.HasExited) { $dummySim.Kill(); $dummySim.WaitForExit() }
    }
    $first.monitor_visible = $false
    $first | Add-Member -NotePropertyName future_setting -NotePropertyValue ([pscustomobject]@{ keep = 'upgrade-marker' })
    [IO.File]::WriteAllText((Join-Path $testState 'collector.json'), ($first | ConvertTo-Json -Depth 12), (New-Object Text.UTF8Encoding($false)))
    'preserved upload history' | Set-Content -LiteralPath (Join-Path $testState 'upload-history-marker.txt') -Encoding UTF8
    # Start before upgrading: exercises named-event shutdown and replacement of a running exe.
    Start-Process -FilePath $collector -ArgumentList 'run','--state-dir',"`"$testState`"",'--iracing-root',"`"$testIracing`"" -WindowStyle Hidden
    Start-Sleep -Seconds 2
    Invoke-Installer $setup $installArgs
    $upgraded = Read-Configuration
    Assert-Condition ($upgraded.collector_id -eq $first.collector_id) 'Upgrade changed the collector identity.'
    Assert-Condition ($upgraded.monitor_visible -eq $false) 'Upgrade reset monitor settings.'
    Assert-Condition ($upgraded.future_setting.keep -eq 'upgrade-marker') 'Upgrade dropped an unknown setting.'
    Assert-Condition ($upgraded.server -eq 'http://127.0.0.1:9') 'Upgrade reset the saved server.'
    & $collector stop
    Assert-Condition ($LASTEXITCODE -eq 0) 'Could not stop upgraded collector.'
    $beforeUninstall = @{}
    foreach ($file in Get-ChildItem -LiteralPath $testState -File) {
        $beforeUninstall[$file.Name] = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
    }
    Invoke-Installer (Join-Path $testInstall 'unins000.exe') @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',"/LOG=`"$(Join-Path $testRoot 'uninstall.log')`"")
    Assert-Condition (-not (Test-Path -LiteralPath $collector)) 'Uninstall left the installed executable.'
    Assert-Condition (-not (Test-Path -LiteralPath 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\iRacingCoachCompanionSmoke_is1')) 'Uninstall left the Windows installed-app entry.'
    $afterRun = Get-ItemProperty -LiteralPath "HKCU:\$testRunKey" -ErrorAction SilentlyContinue
    Assert-Condition (-not $afterRun.'iRacing Coach Collector') 'Uninstall left the startup entry.'
    foreach ($name in $beforeUninstall.Keys) {
        $path = Join-Path $testState $name
        Assert-Condition (Test-Path -LiteralPath $path) "Uninstall removed retained state: $name"
        Assert-Condition ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -eq $beforeUninstall[$name]) "Uninstall changed retained state: $name"
    }
    Write-Host "Companion installer smoke passed: install, running upgrade, identity/settings retention, startup and data-preserving uninstall. Evidence: $testRoot"
} finally {
    $installedCollector = Join-Path $testInstall 'coach-collector.exe'
    if (Test-Path -LiteralPath $installedCollector) { & $installedCollector stop | Out-Null }
    $testUninstaller = Join-Path $testInstall 'unins000.exe'
    if (Test-Path -LiteralPath $testUninstaller) {
        $cleanupProcess = Start-Process -FilePath $testUninstaller -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART' -WindowStyle Hidden -PassThru
        $cleanupProcess.WaitForExit()
    }
    # Remove only this unique disposable registry key; evidence files are retained.
    if (Test-Path -LiteralPath "HKCU:\$testRunKey") { Remove-Item -LiteralPath "HKCU:\$testRunKey" }
    foreach ($name in $savedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process')
    }
}
