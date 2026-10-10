<# Publish a verified installer built from the committed revision, preserving previous releases. #>
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$outputDirectory = Join-Path $repository 'target/companion'
$installer = Join-Path $outputDirectory 'iRacingCoachCompanionSetup.exe'
$manifestPath = Join-Path $outputDirectory 'companion.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$revision = (& git -C $repository rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $revision -notmatch '^[0-9a-f]{40}$') { throw 'Could not resolve committed revision.' }
if ($manifest.schema_version -ne 1 -or $manifest.smoke_test -or $manifest.dirty -or
    $manifest.revision -ne $revision -or $manifest.file_name -ne 'iRacingCoachCompanionSetup.exe') {
    throw 'Installer is not a production build of the current committed revision. Rebuild it.'
}
if (& git -C $repository status --porcelain --untracked-files=no) { throw 'Commit tracked changes before publishing.' }
$installerHash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
if ($installerHash -ne $manifest.sha256 -or (Get-Item -LiteralPath $installer).Length -ne $manifest.size_bytes) {
    throw 'Installer does not match its build manifest.'
}
$manifestHash = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
$staging = '/tmp/iracing-coach-companion-' + [Guid]::NewGuid().ToString('N')
& ssh g-house-pc "install -d -m 700 $staging"
if ($LASTEXITCODE -ne 0) { throw 'Could not create artifact staging folder.' }
& scp $installer $manifestPath "g-house-pc:${staging}/"
if ($LASTEXITCODE -ne 0) { throw 'Installer upload failed.' }
# Literal shell source; inserted values are validated SHA-256/Git hashes and our generated path.
$publishScript = @'
set -euo pipefail
staging='__STAGING__'
downloads=/srv/g-house/apps/iracing-coach/downloads
release="$downloads/releases/__REVISION__"
printf '%s  %s\n' '__INSTALLER_HASH__' "$staging/iRacingCoachCompanionSetup.exe" | sha256sum -c -
printf '%s  %s\n' '__MANIFEST_HASH__' "$staging/companion.json" | sha256sum -c -
sudo -n install -d -m 755 "$release"
if [ -f "$release/iRacingCoachCompanionSetup.exe" ]; then
  printf '%s  %s\n' '__INSTALLER_HASH__' "$release/iRacingCoachCompanionSetup.exe" | sha256sum -c -
else
  sudo -n install -m 644 "$staging/iRacingCoachCompanionSetup.exe" "$release/iRacingCoachCompanionSetup.exe"
fi
if [ -f "$release/companion.json" ]; then
  printf '%s  %s\n' '__MANIFEST_HASH__' "$release/companion.json" | sha256sum -c -
else
  sudo -n install -m 644 "$staging/companion.json" "$release/companion.json"
fi
sudo -n ln -s "releases/__REVISION__/iRacingCoachCompanionSetup.exe" "$downloads/.installer-__ID__"
sudo -n mv -Tf "$downloads/.installer-__ID__" "$downloads/iRacingCoachCompanionSetup.exe"
sudo -n ln -s "releases/__REVISION__/companion.json" "$downloads/.manifest-__ID__"
sudo -n mv -Tf "$downloads/.manifest-__ID__" "$downloads/companion.json"
sha256sum "$downloads/iRacingCoachCompanionSetup.exe"
exit 0
'@
$publishScript = $publishScript.Replace('__STAGING__', $staging).Replace('__REVISION__', $revision).
    Replace('__INSTALLER_HASH__', $installerHash).Replace('__MANIFEST_HASH__', $manifestHash).
    Replace('__ID__', [Guid]::NewGuid().ToString('N'))
$publishScript = $publishScript.Replace("`r", '') + "`n"
$publishScript | & ssh g-house-pc 'bash -s'
if ($LASTEXITCODE -ne 0) { throw 'Artifact verification/publication failed.' }
Write-Host "Published companion from $revision; previous installer releases are retained."
