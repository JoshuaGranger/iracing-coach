# Security, transfer, and deployment

## Copying this folder

Copy the whole `iRacing Coach` folder if the development machine is private and trusted. The clean build inputs are `companion-app/`, `iracing-coach/`, and `companion-app-contract/`.

Preferred clean-transfer command:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\companion-app-contract\scripts\prepare-transfer.ps1
```

This runs the complete verifier and creates a new timestamped ZIP next to the workspace plus a sibling `.sha256` file. It refuses to overwrite existing output or write the archive inside its own workspace, includes `manifest.json` and `SHA256SUMS.txt`, removes a partial ZIP after a failed build, and excludes the private `data/` corpus.

These are not required and may be excluded from a clean transfer:

- `.git/`
- `.validation-deps/`
- every `__pycache__/` and `*.pyc`
- `data/test-artifacts/`

The `data/` directory is optional private regression material, roughly 80 MB at the recorded baseline. It includes Joshua's derived telemetry, account/driver identifiers, setup values, purchased-setup provenance, and absolute local paths. Do not commit or redistribute it. Sanitized fixtures under this contract package are the normal UI-development corpus.

No raw IBTs, replays, simulator-loadable STOs, purchased HTML exports, or Garage61 telemetry CSVs are bundled here.

## Credentials

- The Garage61 PAT is stored outside this folder under `%LOCALAPPDATA%\iRacingCoach\credentials` using Windows user-bound DPAPI.
- A copied DPAPI credential cannot be decrypted by another PC/user and must not be transferred.
- Configure Garage61 on the racing PC after API approval through the backend's no-echo flow.
- Codex/ChatGPT authentication belongs to Codex app-server. Never copy Codex auth files, browser profiles, cookies, or tokens.
- Do not put any token in application settings, command arguments, environment variables, crash reports, analytics, AI context, or logs.

## Filesystem boundary

- Source root: configured local iRacing Documents directory, read-only.
- Archive root: configured coach data directory, backend-owned writes.
- App state/logs: `%LOCALAPPDATA%\iRacingCoach\Companion`.
- Reject UNC/device paths and path traversal.
- The UI never overwrites raw telemetry, replay, STO, HTML, or lap files.
- Setup packages are coaching records; simulator-loadable STO generation is forbidden.

## Network boundary

- The deterministic app must work offline.
- Garage61 authenticated traffic is pinned to the exact HTTPS `garage61.net` origin; cross-origin redirects never receive the token.
- Global-visible lap search stays disabled until Garage61 explicitly approves it.
- Codex/web research is background enrichment and never delays the first local result.

## Logging

Use structured local logs with operation ID, tool name, stage, elapsed time, contract versions, source fingerprint, and error class. Redact:

- authorization headers and tokens;
- driver/customer identifiers unless a local diagnostics export explicitly includes them;
- full prompts containing private telemetry or setup data;
- raw high-frequency channel payloads;
- browser/auth state.

Offer a user-reviewed diagnostics export with a manifest of included files.

## Packaging

- Publish a self-contained `win-x64` .NET build.
- Verify WebView2 and include the Evergreen bootstrapper or a tested fixed-runtime policy for the Hybrid view layer.
- Bundle a verified Python 3.10+ runtime and the complete `iracing-coach` directory structure.
- Set trusted roots and packaged Python only in each backend child environment.
- Start background processes hidden and terminate their process trees during app shutdown or cancellation.
- Sign release artifacts when possible and publish SHA-256 checksums.
- Store app version, backend version, contract compatibility, and migration status in diagnostics.

## Upgrade and rollback

- Never delete or replace the existing archive during an app upgrade.
- Back up settings and database before a schema migration.
- Keep migrations backward-compatible or provide a non-destructive rebuild from original IBTs.
- Retain the previous application/backend package for rollback.
- Copied archives with old absolute paths are private regression material, not a portable production archive. On the racing PC, preserve the configured original roots.
