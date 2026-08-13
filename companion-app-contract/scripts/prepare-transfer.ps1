param(
    [string]$OutputPath,
    [string]$PythonPath
)

$ErrorActionPreference = 'Stop'
$contractRoot = Split-Path -Parent $PSScriptRoot
$workspaceRoot = Split-Path -Parent $contractRoot

& (Join-Path $PSScriptRoot 'verify-contract.ps1') -PythonPath $PythonPath
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss')
    $OutputPath = Join-Path (Split-Path -Parent $workspaceRoot) ("iRacing-Coach-build-inputs-$stamp.zip")
}
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
$workspacePrefix = $workspaceRoot.TrimEnd(
    [System.IO.Path]::DirectorySeparatorChar,
    [System.IO.Path]::AltDirectorySeparatorChar
) + [System.IO.Path]::DirectorySeparatorChar
if ($resolvedOutput.StartsWith($workspacePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'OutputPath must be outside the workspace so the transfer archive cannot include itself.'
}
if ([System.IO.File]::Exists($resolvedOutput)) {
    throw "Refusing to overwrite existing archive: $resolvedOutput"
}
$checksumOutput = $resolvedOutput + '.sha256'
if ([System.IO.File]::Exists($checksumOutput)) {
    throw "Refusing to overwrite existing checksum: $checksumOutput"
}

$manifestPath = Join-Path $contractRoot 'manifest.json'
$manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
$relativeFiles = @($manifest.files | ForEach-Object { [string]$_.path })
$relativeFiles += 'companion-app-contract/manifest.json'
$relativeFiles += 'companion-app-contract/SHA256SUMS.txt'

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$stream = $null
try {
    $stream = [System.IO.File]::Open($resolvedOutput, [System.IO.FileMode]::CreateNew)
    $archive = [System.IO.Compression.ZipArchive]::new(
        $stream,
        [System.IO.Compression.ZipArchiveMode]::Create,
        $false
    )
    try {
        foreach ($relative in $relativeFiles) {
            $normalized = $relative.Replace('/', [System.IO.Path]::DirectorySeparatorChar)
            $source = [System.IO.Path]::GetFullPath((Join-Path $workspaceRoot $normalized))
            if (-not $source.StartsWith($workspacePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                throw "Manifest path escapes workspace: $relative"
            }
            if (-not [System.IO.File]::Exists($source)) {
                throw "Manifest file is missing: $relative"
            }
            $entryName = ('iRacing Coach/' + $relative).Replace('\', '/')
            [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                $archive,
                $source,
                $entryName,
                [System.IO.Compression.CompressionLevel]::Optimal
            ) | Out-Null
        }
    }
    finally {
        $archive.Dispose()
    }
}
catch {
    if ($stream) { $stream.Dispose() }
    if ([System.IO.File]::Exists($resolvedOutput)) {
        Remove-Item -LiteralPath $resolvedOutput -Force
    }
    throw
}
finally {
    if ($stream) { $stream.Dispose() }
}

$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $resolvedOutput).Hash.ToLowerInvariant()
[System.IO.File]::WriteAllText(
    $checksumOutput,
    ($hash + '  ' + [System.IO.Path]::GetFileName($resolvedOutput) + [Environment]::NewLine),
    [System.Text.UTF8Encoding]::new($false)
)
[pscustomobject]@{
    ok = $true
    archive = $resolvedOutput
    files = $relativeFiles.Count
    bytes = (Get-Item -LiteralPath $resolvedOutput).Length
    sha256 = $hash
    checksumFile = $checksumOutput
} | ConvertTo-Json
