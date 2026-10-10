<# Release the committed service and its matching Windows companion through the managed tools. #>
[CmdletBinding()]
param(
    [string]$InnoCompilerPath,
    [string]$ServerUrl = 'http://192.168.1.81:8210'
)
$ErrorActionPreference = 'Stop'
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
if (& git -C $repository status --porcelain --untracked-files=no) { throw 'Commit tracked changes before releasing.' }
& (Join-Path $repository 'deploy/windows/Build-Companion.ps1') -InnoCompilerPath $InnoCompilerPath -ServerUrl $ServerUrl
& (Join-Path $repository 'deploy/windows/Publish-Companion.ps1')
& 'C:/Program Files/Git/bin/bash.exe' (Join-Path $env:USERPROFILE '.codex/skills/mini-pc/release.sh') iracing-coach $repository
if ($LASTEXITCODE -ne 0) { throw 'Managed mini-PC release failed.' }
$download = Join-Path $repository 'target/companion/download-check.exe'
Invoke-WebRequest -Uri "$($ServerUrl.TrimEnd('/'))/downloads/iRacingCoachCompanionSetup.exe" -OutFile $download
$manifest = Get-Content -LiteralPath (Join-Path $repository 'target/companion/companion.json') -Raw | ConvertFrom-Json
if ((Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash.ToLowerInvariant() -ne $manifest.sha256) {
    throw 'Published HTTP download differs from the built installer.'
}
Write-Host 'Service healthy; companion HTTP download verified byte-for-byte.'
