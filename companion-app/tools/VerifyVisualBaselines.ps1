[CmdletBinding()]
param(
    [string]$Manifest
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($Manifest)) {
    $Manifest = Join-Path $PSScriptRoot '..\artifacts\qa\v0.9.0\manifest.json'
}
$manifestPath = [System.IO.Path]::GetFullPath($Manifest)
$baselineRoot = [System.IO.Path]::GetDirectoryName($manifestPath)
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Visual baseline manifest was not found: $manifestPath"
}

Add-Type -AssemblyName System.Drawing
$entries = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
foreach ($entry in $entries.files) {
    $path = [System.IO.Path]::GetFullPath((Join-Path $baselineRoot $entry.file))
    if (-not $path.StartsWith($baselineRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Visual baseline path escapes its artifact folder: $($entry.file)"
    }
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Visual baseline is missing: $($entry.file)"
    }
    $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne $entry.sha256) {
        throw "Visual baseline changed: $($entry.file)"
    }
    $image = [System.Drawing.Image]::FromFile($path)
    try {
        if ($image.Width -ne $entry.width -or $image.Height -ne $entry.height) {
            throw "Visual baseline dimensions changed: $($entry.file)"
        }
    }
    finally { $image.Dispose() }
}

Write-Output "Verified $($entries.files.Count) visual baselines."
