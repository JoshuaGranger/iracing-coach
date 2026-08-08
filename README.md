# iRacing Coach

This folder is the complete engineering handoff for Joshua's iRacing coaching system.

The latest stable packaged release is `0.14.2`; its immutable artifact record remains in [documentation/06-reality/implementation-snapshot-0.14.2.md](documentation/06-reality/implementation-snapshot-0.14.2.md). The working tree now identifies `0.15.0` development source and is intentionally exercised through direct development executables rather than a new installer or portable package. Its current, non-release reality is recorded in [documentation/06-reality/implementation-snapshot-0.15.0-development.md](documentation/06-reality/implementation-snapshot-0.15.0-development.md).

The reviewable product specification, current implementation snapshot, traceability matrix, and agent criticism protocol begin at [documentation/README.md](documentation/README.md). The documentation deliberately separates intended behavior, functional reality, and verification evidence.

For the Windows companion app, begin with [companion-app-handoff/START_HERE.md](companion-app-handoff/START_HERE.md). The authoritative deterministic backend is in `iracing-coach/`; do not rewrite its telemetry math in the frontend.

The selected application stack is C#/.NET 10 with a WPF host and Blazor Hybrid views. Its binding modern gentle-dark specification and machine-readable tokens are in `companion-app-handoff/UI_DESIGN_SYSTEM.md` and `companion-app-handoff/config/theme.dark.json`.

The `data/` directory is Joshua's optional private regression corpus. It is not required to build the app and is not a repository fixture set. Portable settings, logs, setups, archives, credentials, and raw personal telemetry are also excluded from source control.

To create a verified clean ZIP for the development machine, run:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass `
  -File .\companion-app-handoff\scripts\prepare-transfer.ps1
```

The script excludes private `data/`, credentials, repository history, caches, and compiled artifacts. Copying this entire folder directly is also supported on a private trusted machine.
