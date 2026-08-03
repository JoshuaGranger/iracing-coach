[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$rootPrefix = $root.TrimEnd('\') + '\'
$destination = [System.IO.Path]::GetFullPath($OutputPath)
if ($destination.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'The source archive must be written outside the source tree.'
}

$excludedDirectories = @('.git', 'bin', 'obj', 'artifacts', '__pycache__', '.pytest_cache', '.validation-deps', 'data')
$files = Get-ChildItem -LiteralPath $root -Recurse -File | Where-Object {
    $relative = $_.FullName.Substring($rootPrefix.Length)
    $parts = $relative -split '[\\/]'
    -not ($parts | Where-Object { $_ -in $excludedDirectories }) -and
    $_.Name -ne 'installer-payload.zip' -and
    $_.Extension -notin @('.pyc', '.pyo') -and
    $_.Name -notlike 'preview*.log'
}

$parent = Split-Path -Parent $destination
New-Item -ItemType Directory -Path $parent -Force | Out-Null
if (Test-Path -LiteralPath $destination) { Remove-Item -LiteralPath $destination -Force }

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$stream = [System.IO.File]::Open($destination, [System.IO.FileMode]::CreateNew)
try {
    $archive = [System.IO.Compression.ZipArchive]::new($stream, [System.IO.Compression.ZipArchiveMode]::Create, $false)
    try {
        foreach ($file in $files) {
            $relative = $file.FullName.Substring($rootPrefix.Length).Replace('\', '/')
            [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                $archive,
                $file.FullName,
                "iRacing Coach/$relative",
                [System.IO.Compression.CompressionLevel]::Optimal
            ) | Out-Null
        }
    }
    finally { $archive.Dispose() }
}
finally { $stream.Dispose() }

$hash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
$name = [System.IO.Path]::GetFileName($destination)
[System.IO.File]::WriteAllText("$destination.sha256", "$hash  $name$([Environment]::NewLine)", [System.Text.UTF8Encoding]::new($false))
[pscustomobject]@{ Archive = $destination; Files = @($files).Count; Bytes = (Get-Item -LiteralPath $destination).Length; Sha256 = $hash }
