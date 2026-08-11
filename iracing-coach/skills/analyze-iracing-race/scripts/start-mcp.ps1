$ErrorActionPreference = 'Stop'

# MCP is a UTF-8 JSON-lines protocol. Windows PowerShell otherwise decodes a
# native child's UTF-8 stdout using the active console code page before
# forwarding it to the companion, which turns characters such as `·` and `→`
# into visible mojibake. Pin every side of the PowerShell/Python bridge.
$taskUtf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $taskUtf8
[Console]::OutputEncoding = $taskUtf8
$OutputEncoding = $taskUtf8

$taskScriptPath = Join-Path $PSScriptRoot 'mcp_server.py'
$taskUserProfile = if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
    [System.IO.Path]::GetFullPath($env:USERPROFILE)
}
else {
    [Environment]::GetFolderPath('UserProfile')
}
$taskCandidates = @(
    $env:IRACING_COACH_PYTHON,
    (Join-Path $taskUserProfile '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'),
    (Join-Path $taskUserProfile 'AppData\Local\Programs\Python\Python313\python.exe'),
    (Join-Path $taskUserProfile 'AppData\Local\Programs\Python\Python312\python.exe'),
    (Join-Path $taskUserProfile 'AppData\Local\Programs\Python\Python311\python.exe'),
    (Join-Path $taskUserProfile 'AppData\Local\Programs\Python\Python310\python.exe')
) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }

$taskPython = $taskCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $taskPython) {
    $taskPythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($taskPythonCommand) {
        $taskPython = $taskPythonCommand.Source
    }
}
if (-not $taskPython) {
    [Console]::Error.WriteLine('iRacing Coach requires Python 3.10 or newer. No compatible Python executable was found.')
    exit 1
}

$taskVersionText = & $taskPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($taskVersionText)) {
    [Console]::Error.WriteLine('The selected iRacing Coach Python runtime could not be started.')
    exit 1
}
$taskVersion = [Version]$taskVersionText.Trim()
if ($taskVersion -lt [Version]'3.10') {
    [Console]::Error.WriteLine("iRacing Coach requires Python 3.10 or newer; found $taskVersion.")
    exit 1
}

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
& $taskPython -X utf8 -u $taskScriptPath
exit $LASTEXITCODE
