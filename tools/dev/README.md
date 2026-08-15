# Development sandbox runner

Development tooling for running known backend commands with every backend
default root confined to a unique per-run sandbox. Nothing here is imported by
production code, and nothing here changes backend path resolution.

## Why

`storage.default_archive_root()` falls back to `%USERPROFILE%\Documents\iRacing Coach\data`
when `IRACING_COACH_DATA` is unset, and `mcp_server` computes its module-level
defaults at import time. A development command run without those variables
therefore resolves its defaults to the real user archive. No current gate writes
there, but the discipline is convention rather than enforcement: one new test
that omits an explicit root is enough. This runner makes it enforcement.

Note that supplying an explicit `archive_root` argument is **not** an isolation
mechanism. `mcp_server._archive_root` confines an explicit root beneath
`DEFAULT_ARCHIVE_ROOT`, so without the environment variables the only accepted
"explicit" roots are subdirectories of the real archive. Set the environment
before the process starts; that is the only seam.

## Usage

The interpreter is always explicit. This script performs no discovery and reads
no agent-runtime cache path — shared resolution belongs to `DEV-TOOLCHAIN-001` /
WS-13a.

```powershell
# Backend unit suite
.\tools\dev\Invoke-SandboxedDev.ps1 -PythonPath <python.exe> -Target backend-suite

# Real MCP end-to-end smoke against the tracked synthetic IBT
.\tools\dev\Invoke-SandboxedDev.ps1 -PythonPath <python.exe> -Target mcp-smoke -FixtureIracingRoot

# Full repository verification
.\tools\dev\Invoke-SandboxedDev.ps1 -PythonPath <python.exe> -Target verify-repository -FixtureIracingRoot

# Arbitrary in-worktree script or module
.\tools\dev\Invoke-SandboxedDev.ps1 -PythonPath <python.exe> -Script .\tools\mcp_e2e_smoke.py
.\tools\dev\Invoke-SandboxedDev.ps1 -PythonPath <python.exe> -Module unittest -TargetArgs discover,-s,iracing-coach\tests

# This tooling's own tests
<python.exe> -X utf8 -m unittest discover -s tools\dev\tests -p "test_*.py" -v
```

`-KeepSandbox` retains the sandbox on success and prints its path. A failed run
always retains it.

## What is confined

`IRACING_COACH_DATA`, `IRACING_COACH_INSTALL_ROOT`, `USERPROFILE`, `HOMEDRIVE`,
`HOMEPATH`, `LOCALAPPDATA`, `APPDATA`, `TEMP`, and `TMP` are pointed at
descendants of the sandbox. `IRACING_COACH_PYTHON` is the supplied interpreter
and `PYTHONUTF8` is `1`. Variables are set on the child process only; the
caller's session is never mutated.

`IRACING_COACH_IRACING_ROOT` is the single value permitted to sit outside the
sandbox, and only when `-FixtureIracingRoot` is given, and only as the exact
resolved `test-data/ibt` directory of the current worktree.

`LOCALAPPDATA` and `APPDATA` are confined for a specific reason: Windows expands
the per-user shell folders from a `USERPROFILE`-relative registry value, and
`Environment.GetFolderPath` returns an empty string when the expanded directory
does not exist. A caller that combines that empty string with a relative tail
then writes into the current working directory — the worktree. Creating
`home\AppData\Local` and `home\AppData\Roaming` and naming them explicitly keeps
that write inside the sandbox.

## The guarantee, and its limits

**Proven:** for a supported target, before that target's module or script
imports or executes, `storage.default_archive_root()`,
`mcp_server.DEFAULT_ARCHIVE_ROOT`, `Path.home()`, `tempfile.gettempdir()`,
`TEMP`, and `TMP` all resolve inside the sandbox, and
`mcp_server.DEFAULT_IRACING_ROOT` resolves inside the sandbox or equals the
permitted tracked fixture root.

**Two dispatch tiers:**

| Tier | Targets | Guarantee |
| --- | --- | --- |
| Strong, same process | `-Script`, `-Module`, `backend-suite`, `mcp-smoke` | the assertion runs in the same process that then executes the target through `runpy`, so the target cannot run unless it passed |
| Weaker, preceding process | `verify-repository` | the assertion runs as a separate process first; the PowerShell target is then spawned with the identical validated environment |

**Not proven.** This is environment containment for known development commands,
not an OS security sandbox. It cannot stop an arbitrary child executable from
opening a socket, writing outside the sandbox, or reading anything its user can
read. It provides no isolation for the .NET host, WebView2, Windows Known
Folders, the registry, installers, or packages — `Environment.GetFolderPath` and
`SHGetKnownFolderPath` do not consult `USERPROFILE`. It says nothing about
mounted UI, packaged artifacts, or acceptance.

## Diagnosing a refusal

Failure output names the offending variable or attribute and a failure class
(`missing`, `outside-sandbox`, `unc-or-device`, `drive-mismatch`,
`unexpected-fixture-root`) and deliberately never prints the resolved value, so
that evidence files cannot echo an ambient private root. Re-run with
`-KeepSandbox` and inspect locally.

If a gate passes normally but fails only under this runner, that failure is the
finding: it means the gate depends on the real temporary directory or the real
archive. Report it rather than loosening the runner.
