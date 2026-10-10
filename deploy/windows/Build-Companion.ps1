<# Build the self-contained Windows x64 installer. Build output stays under ignored target/. #>
[CmdletBinding()]
param(
    [string]$InnoCompilerPath,
    [string]$ServerUrl = 'http://192.168.1.81:8210',
    [switch]$SmokeTest
)
$ErrorActionPreference = 'Stop'
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$outputDirectory = Join-Path $repository 'target/companion'
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

$server = [Uri]$ServerUrl
if (-not $server.IsAbsoluteUri -or $server.Scheme -ne 'http' -or $server.UserInfo -or
    $server.Query -or $server.Fragment -or $server.AbsolutePath -ne '/') {
    throw 'ServerUrl must be an HTTP origin without credentials, a path, query or fragment.'
}
$ServerUrl = $server.GetLeftPart([UriPartial]::Authority)
if (-not $InnoCompilerPath) {
    $candidates = @(
        (Join-Path $env:USERPROFILE '.cache/iracing-coach-tools/inno-6.7.3/ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6/ISCC.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs/Inno Setup 6/ISCC.exe')
    )
    $InnoCompilerPath = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $InnoCompilerPath -or -not (Test-Path -LiteralPath $InnoCompilerPath)) {
    throw 'Install the Inno Setup 6 compiler, or pass -InnoCompilerPath. See deploy/windows/README.md.'
}

Push-Location $repository
try {
    $revision = (& git rev-parse HEAD).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Could not read Git revision.' }
    $dirty = [bool](& git status --porcelain --untracked-files=no)
    $productionManifest = Join-Path $outputDirectory 'companion.json'
    $productionInstaller = Join-Path $outputDirectory 'iRacingCoachCompanionSetup.exe'
    # Preserve the exact published bytes when retrying a release of the same commit.
    if (-not $SmokeTest -and -not $dirty -and (Test-Path -LiteralPath $productionManifest) -and
        (Test-Path -LiteralPath $productionInstaller)) {
        $previous = Get-Content -LiteralPath $productionManifest -Raw | ConvertFrom-Json
        if ($previous.schema_version -eq 1 -and -not $previous.smoke_test -and -not $previous.dirty -and
            $previous.revision -eq $revision -and $previous.server_url -eq $ServerUrl -and
            $previous.sha256 -eq (Get-FileHash -LiteralPath $productionInstaller -Algorithm SHA256).Hash.ToLowerInvariant() -and
            $previous.size_bytes -eq (Get-Item -LiteralPath $productionInstaller).Length) {
            Write-Host "Reusing verified installer from $revision"
            return
        }
    }
    $cargo = Join-Path $env:USERPROFILE '.cargo/bin/cargo.exe'
    & $cargo build --release --locked -p coach-collector --target x86_64-pc-windows-msvc
    if ($LASTEXITCODE -ne 0) { throw 'Collector build failed.' }
    $collector = Join-Path $repository 'target/x86_64-pc-windows-msvc/release/coach-collector.exe'
    $collectorVersion = & $collector --version
    if ($LASTEXITCODE -ne 0 -or $collectorVersion -notmatch '^coach-collector (\d+\.\d+\.\d+)$') {
        throw 'Could not read the collector version.'
    }
    $version = $Matches[1]
    $compilerArguments = @('/Qp', "/DCollectorExe=$collector", "/DCompanionVersion=$version",
        "/DOutputDirectory=$outputDirectory", "/DServerUrl=$ServerUrl")
    if ($SmokeTest) { $compilerArguments += '/DSMOKE_TEST' }
    $compilerArguments += (Join-Path $PSScriptRoot 'companion.iss')
    & $InnoCompilerPath @compilerArguments
    if ($LASTEXITCODE -ne 0) { throw 'Companion installer compilation failed.' }
    $name = if ($SmokeTest) { 'iRacingCoachCompanionSetup-test.exe' } else { 'iRacingCoachCompanionSetup.exe' }
    $installer = Join-Path $outputDirectory $name
    $manifest = [ordered]@{
        schema_version = 1; version = $version; revision = $revision; dirty = $dirty
        smoke_test = [bool]$SmokeTest; server_url = $ServerUrl; file_name = $name
        size_bytes = (Get-Item -LiteralPath $installer).Length
        sha256 = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
        collector_sha256 = (Get-FileHash -LiteralPath $collector -Algorithm SHA256).Hash.ToLowerInvariant()
        compiler_sha256 = (Get-FileHash -LiteralPath $InnoCompilerPath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $manifestName = if ($SmokeTest) { 'companion-test.json' } else { 'companion.json' }
    [IO.File]::WriteAllText((Join-Path $outputDirectory $manifestName), ($manifest | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
    Write-Host "Built $installer ($($manifest.size_bytes) bytes)"
    Write-Host "SHA-256 $($manifest.sha256)"
} finally { Pop-Location }
