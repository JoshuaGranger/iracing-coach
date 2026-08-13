# Backend runtime and packaging

The deterministic backend is standard-library-only and supports Python 3.10+. Package a 64-bit Windows Python runtime with the companion app and set `IRACING_COACH_PYTHON` to its absolute executable for each backend child process.

Preserve this relative tree inside the application package:

```text
iracing-coach/
  config/defaults.json
  skills/analyze-iracing-race/
    scripts/*.py
    scripts/*.ps1
    references/*.md
```

The backend derives `PLUGIN_ROOT` from the script location. Do not flatten the directory during packaging.

Recommended first-release packaging:

1. Publish the .NET application self-contained for `win-x64`.
2. Verify the WebView2 Runtime and include the Evergreen bootstrapper or a tested fixed-runtime policy for Blazor Hybrid.
3. Place an official embeddable/full Python runtime under `runtime/python`.
4. Place the complete validated `iracing-coach` folder under `backend/iracing-coach`.
5. Launch `start-mcp.ps1` with process-local trusted-root variables.
6. Run `verify-contract.ps1` and a packaged end-to-end smoke test before signing/checksums.

PyInstaller/Nuitka are optional later optimizations, not requirements. If used, verify memory-mapped IBT reads, dynamic module imports, `config/defaults.json`, PowerShell scripts, reports, and every MCP/CLI command. A bundled Python tree is simpler and more transparent for release one.
