<#
.SYNOPSIS
  D12 step 3 for the legacy raw archive: upload every recording under
  `Documents\iRacing Coach\data\telemetry-traces\raw\<sha256>\` to coach-server through the public
  upload API (collector-protocol.md section 4), read-only.

.DESCRIPTION
  Stand-in for `coach-collector backfill`, which the collector does not have (W3 defect, reported in
  docs/08-rewrite/acceptance/migration.md). The installed collector already uploads every `.ibt` in
  `Documents\iRacing\telemetry`; this script covers the archive the collector does not watch.

  For each archived `.ibt` (one per folder): SHA-256 of the file (shared read, never written),
  `HEAD /api/v1/uploads/{sha256}` (skip when the server already holds it), `POST /api/v1/uploads`
  with the recording's original file name from the folder's `manifest.json`, then 4 MiB
  `PUT /api/v1/uploads/{sha256}` chunks from the server's offset, following 409 {offset} and
  503 Retry-After. File work runs only while iRacing's simulator is not running: the script checks
  for `iRacingSim64DX11` before every file and every chunk and stops (exit code 3) if it appears,
  so a rerun resumes from the server's offset.

  Writes one JSON line per recording to -Log. Windows PowerShell 5.1, no modules.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File Backfill-RawArchive.ps1 -Server http://192.168.1.81:8210 -Log C:\scratch\backfill.jsonl
#>
param(
    [Parameter(Mandatory = $true)] [string] $Server,
    [Parameter(Mandatory = $true)] [string] $Log,
    [string] $Archive = (Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'iRacing Coach\data\telemetry-traces\raw'),
    [switch] $HashOnly
)

$ErrorActionPreference = 'Stop'
$env:PSModulePath = [Environment]::GetEnvironmentVariable('PSModulePath', 'Machine')
# A fixed collector identity for this tool (uuid5 of URL "iracing-coach/tools/migrate/backfill-raw-archive").
$ToolCollectorId = '5b98304f-2eb9-5f13-8aed-7dfe033778c1'
$Chunk = 4MB
$Server = $Server.TrimEnd('/')

function Assert-SimNotRunning {
    if (Get-Process -Name 'iRacingSim64DX11' -ErrorAction SilentlyContinue) {
        Write-Output 'iRacingSim64DX11 is running: stopping (rerun later to resume)'
        exit 3
    }
}

function Open-Read([string] $Path) {
    [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read,
        [System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete)
}

function Get-Sha256([string] $Path) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $stream = Open-Read $Path
    try { ($sha.ComputeHash($stream) | ForEach-Object { $_.ToString('x2') }) -join '' }
    finally { $stream.Dispose(); $sha.Dispose() }
}

# One HTTP request; returns @{ Status; Headers; Body }. Non-2xx answers are returned, not thrown.
function Invoke-Http([string] $Method, [string] $Path, [byte[]] $Body, [hashtable] $Headers, [string] $ContentType) {
    $request = [System.Net.HttpWebRequest]::Create($Server + $Path)
    $request.Method = $Method
    $request.Timeout = 120000
    $request.ReadWriteTimeout = 120000
    $request.KeepAlive = $false
    if ($Headers) { foreach ($k in $Headers.Keys) { $request.Headers.Add($k, $Headers[$k]) } }
    if ($null -ne $Body) {
        if ($ContentType) { $request.ContentType = $ContentType }
        $request.ContentLength = $Body.Length
        $out = $request.GetRequestStream()
        try { $out.Write($Body, 0, $Body.Length) } finally { $out.Dispose() }
    }
    try { $response = $request.GetResponse() }
    catch [System.Net.WebException] {
        $response = $_.Exception.Response
        if ($null -eq $response) { throw }
    }
    try {
        $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
        $text = $reader.ReadToEnd()
        $reader.Dispose()
        @{ Status = [int]$response.StatusCode; Headers = $response.Headers; Body = $text }
    }
    finally { $response.Close() }
}

function Get-OriginalName([System.IO.FileInfo] $File) {
    $manifest = Join-Path $File.DirectoryName 'manifest.json'
    if (Test-Path -LiteralPath $manifest) {
        try {
            $m = Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json
            $sources = @()
            if ($m.original_path) { $sources += [string]$m.original_path }
            foreach ($d in @($m.source_discoveries)) { if ($d.source_path) { $sources += [string]$d.source_path } }
            foreach ($s in $sources) {
                $leaf = Split-Path -Leaf ($s -replace '/', '\')
                if ($leaf -like '*.ibt') { return $leaf }
            }
        }
        catch { }
    }
    $File.Name
}

$files = @(Get-ChildItem -LiteralPath $Archive -Directory | ForEach-Object {
        Get-ChildItem -LiteralPath $_.FullName -Filter '*.ibt' -File
    } | Sort-Object Length)
Write-Output ("archive {0}: {1} recordings, {2} bytes" -f $Archive, $files.Count, ($files | Measure-Object Length -Sum).Sum)

foreach ($file in $files) {
    Assert-SimNotRunning
    $started = Get-Date
    $name = Get-OriginalName $file
    $sha = Get-Sha256 $file.FullName
    $folder = $file.Directory.Name.ToLowerInvariant()
    $entry = [ordered]@{
        folder = $folder; sha256 = $sha; folder_matches = ($folder -eq $sha); size = $file.Length
        file_name = $name; archived_name = $file.Name; utc = (Get-Date).ToUniversalTime().ToString('o')
    }
    if ($HashOnly) {
        ($entry | ConvertTo-Json -Compress) | Add-Content -LiteralPath $Log -Encoding UTF8
        continue
    }
    $head = Invoke-Http 'HEAD' "/api/v1/uploads/$sha" $null $null $null
    $entry.head_status = $head.Status
    if ($head.Status -eq 200 -and $head.Headers['Upload-State'] -and $head.Headers['Upload-State'] -ne 'open') {
        $entry.result = 'already_held'
        $entry.state = $head.Headers['Upload-State']
        ($entry | ConvertTo-Json -Compress) | Add-Content -LiteralPath $Log -Encoding UTF8
        continue
    }
    $create = [ordered]@{
        sha256 = $sha; size = $file.Length; kind = 'ibt'; file_name = $name; relative_path = $null
        source_mtime = $file.LastWriteTimeUtc.ToString('yyyy-MM-ddTHH:mm:ssZ'); collector_id = $ToolCollectorId; customer_id = $null
    }
    $json = [System.Text.Encoding]::UTF8.GetBytes(($create | ConvertTo-Json -Compress))
    $retries = 0; $chunks = 0; $conflicts = 0
    while ($true) {
        $r = Invoke-Http 'POST' '/api/v1/uploads' $json $null 'application/json'
        if ($r.Status -eq 503) {
            $retries++
            $wait = $r.Headers['Retry-After']; if (-not $wait) { $wait = 5 }
            Start-Sleep -Seconds ([int]$wait); continue
        }
        if ($r.Status -ne 200 -and $r.Status -ne 201) { throw "create $name : HTTP $($r.Status) $($r.Body)" }
        $state = $r.Body | ConvertFrom-Json
        break
    }
    $entry.create_status = $r.Status
    $offset = [long]$state.offset
    $stream = Open-Read $file.FullName
    try {
        $buffer = New-Object byte[] $Chunk
        while ($state.state -eq 'open') {
            Assert-SimNotRunning
            $count = [int][Math]::Min([long]$Chunk, $file.Length - $offset)
            $stream.Position = $offset
            $read = 0
            while ($read -lt $count) { $n = $stream.Read($buffer, $read, $count - $read); if ($n -le 0) { throw 'short read' }; $read += $n }
            $body = New-Object byte[] $count
            [Array]::Copy($buffer, $body, $count)
            $range = "bytes {0}-{1}/{2}" -f $offset, ($offset + $count - 1), $file.Length
            $r = Invoke-Http 'PUT' "/api/v1/uploads/$sha" $body @{ 'Content-Range' = $range } 'application/octet-stream'
            switch ($r.Status) {
                200 { $chunks++; $state = $r.Body | ConvertFrom-Json; $offset = [long]$state.offset }
                409 { $conflicts++; $offset = [long](($r.Body | ConvertFrom-Json).offset) }
                503 { $retries++; Start-Sleep -Seconds 5 }
                default { throw "chunk $name at $offset : HTTP $($r.Status) $($r.Body)" }
            }
        }
    }
    finally { $stream.Dispose() }
    $entry.result = 'uploaded'
    $entry.state = $state.state
    $entry.chunks = $chunks; $entry.conflicts = $conflicts; $entry.retries = $retries
    $entry.seconds = [Math]::Round(((Get-Date) - $started).TotalSeconds, 1)
    ($entry | ConvertTo-Json -Compress) | Add-Content -LiteralPath $Log -Encoding UTF8
}
Write-Output 'done'
