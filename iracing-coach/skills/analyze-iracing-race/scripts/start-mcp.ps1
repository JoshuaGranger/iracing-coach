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

# The interpreter version is asserted at import in mcp_server.py. Probing it here
# started a second Python process on every backend call purely to print two numbers.

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
& $taskPython -X utf8 -u $taskScriptPath
exit $LASTEXITCODE
