# iRacing Coach 0.8.0

## Package

- Installer: `artifacts/dist/iRacingCoach-0.8.0-Setup.exe`
- Size: 498,604,070 bytes
- SHA-256: `d2118e58d24c586557defff50f56b2c615bbcfb8798a420d583284ba23e4c095`
- App version: 0.8.0
- Deterministic backend: 0.3.0
- MCP contract: 1
- Packaged Coach Engine: `codex-cli 0.146.0-alpha.9.2`, Authenticode-signed by OpenAI OpCo, LLC

## Install or upgrade

1. Copy the setup executable and optional `.sha256` file to the racing PC.
2. Exit iRacing Coach from its tray menu if it is open. The installer can also close the installed prior version during replacement.
3. Run `iRacingCoach-0.8.0-Setup.exe` and approve the Windows administrator prompt.
4. Leave **Create a desktop shortcut** selected if desired, then choose **Install iRacing Coach**.
5. The app is installed to `C:\Program Files\iRacing Coach`.
6. The portable archive and preferences remain in `Documents\iRacing Coach`. ChatGPT and Garage61 credentials are protected for one Windows user/PC and must be reconnected after moving the portable folder to another PC.

The installer replaces the current Program Files payload atomically, removes recognized legacy installation folders and old cached setup executables, and restores the prior payload if a post-swap step fails. Upgrade and uninstall never delete the portable `Documents\iRacing Coach` archive or the iRacing source folder.

## Release validation

- Clean Debug solution build: 0 warnings, 0 errors.
- .NET app/coordinator tests: 50 passed.
- Python telemetry/backend tests: 173 passed.
- Handoff: 109 files verified; 17 contracts, 14 fixtures, and 16 MCP tools loaded; end-to-end smoke test passed.
- Visual regression: 12 exact native screenshot hashes and dimensions verified.
- Packaged Coach Engine startup probe: installed and running; expected not-connected account state.
- Installer lifecycle: first install, simulated rollback, replacement while prior app was running, uninstall, reinstall, and second uninstall all passed.
- Upgrade removed the prior-version marker and preserved the durable archive hash.
- Uninstall removed six app-owned state roots plus protected credential fixtures while preserving both portable archive and iRacing source hashes.

## Highest-value next work

1. Add a telemetry-derived clickable track map and synchronized multi-lap charts once the normalized trace contract is complete.
2. Extend Progressive Tuning from one guided feedback item to prioritized feedback cards and matched-run review.
3. Add strict, auditable race grading only after its achievable-envelope evidence and calibration fixtures are ready.
4. Expand real planning inputs when local schedule/rule/position evidence becomes available; continue showing unavailable instead of estimates when it does not.
5. Implement readable setup comparison and worksheet capabilities without claiming to encode simulator-loadable `.sto` files.
6. Research an officially supported setup import/export mechanism as an isolated R&D task; never overwrite source `.sto` files.
7. Activate broader Garage61 comparison only after the account receives explicit global-visible API scope.

See `UI_UX_AUDIT_0.8.0.md` and `artifacts/qa/v0.8.0/` for the screen-by-screen audit and visual evidence.
