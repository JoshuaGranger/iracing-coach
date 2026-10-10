# Windows companion installer (2026-10-09)

Owner request: provide a subtle **Download companion** link that downloads an
installer for the small Windows sim-PC app, including automatic startup.

The product name is **iRacing Coach Companion**; its process remains
`coach-collector.exe`. W7 added a muted footer on normal pages, including the empty
Drivers page, with a direct download and a Windows sim-PC label. The investigation
workspace keeps its full chart/map area. W3 added graceful `stop`, an
installer-owned uninstall mode, preservation of unknown settings during upgrade,
and private-console hiding for Start menu launch. W9 packages a self-contained
per-user Inno Setup installer. W4 gives `/downloads/*` a dedicated static service
so a missing executable is a real 404, never the SPA document.

## Verified

- `tools/check.ps1`: passed repository verification (1,485 Python cases, 18
  skips), workspace format/clippy/tests, wire/theme drift, web typecheck, all 643
  web unit tests, and production build.
- `tools/check.ps1 -Linux`: passed workspace clippy/tests and 1,485 Python cases
  (38 skips).
- `deploy/windows/Test-Companion.ps1`: actual silent installer passed first
  install, Windows installed-app registration, startup command, refusal while a
  harmless fake simulator process was running, replacement of a running collector,
  retention of identity/server/monitor/unknown settings, and removal of installed
  program/startup entries with retained state files byte-identical. Isolated
  evidence: `target/companion/smoke-9cbf73737f8f4335a22a16bd668c3848`.
- W3's 167 collector cases passed, including isolated graceful-stop race/timeout
  and installer-uninstall cases; six existing opt-in cases remain ignored.
- W4's download regression proves exact binary GET, HEAD length, byte ranges,
  no-cache responses, and missing-file/directory 404s. Full server tests passed.
- W7's 60 existing library/live browser tests passed. Additional desktop
  1280x720, tablet 1024x768 and phone 390x844 browser smoke verified a 44 px target,
  no horizontal overflow, keyboard download retaining the page, accessible
  description and no request on hover. Screenshots are under ignored
  `web/target/companion-download/`; the browser smoke used a disposable synthetic
  download, not a race-data fixture.

## Distribution and limits

Released code revision `0f1984ef57aa44d26b120616a30b7613b0dc7b1c` through the
managed mini-PC release on October 9. The service reported healthy. The production
installer is 2,674,733 bytes, SHA-256
`bfef0aa43d703e5628f2d9dc629f6de736cd525faf4b1fe1e084cdba3ca8616b`.
Both the release wrapper's HTTP verification and a real headless-browser click on
the live **Download companion** link saved those exact bytes as
`iRacingCoachCompanionSetup.exe`. After the normal single-driver landing redirect,
the download preserved the Sessions page. A missing installer returned 404 with
no SPA HTML. Browser evidence is under ignored `target/companion/`.

`deploy/mini-pc/Release-WithCompanion.ps1` builds Windows artifacts on the dev PC,
rejects dirty or mismatched source at publication, preserves prior installer
releases, and releases only through the managed mini-pc tool. A read-only bind
mount supplies `/app/web/downloads`; the wrapper verifies the actual HTTP download
against the built SHA-256. See `deploy/windows/README.md`.

The installer is unsigned. The sim PC was running iRacing when checked earlier in
this chat, so no companion replacement or startup change was made there. Windows
sign-in launch is registered and tested in isolation; no reboot or sign-out was
performed. Existing iRacing files are never written, and installer removal keeps
the collector's settings, journals and logs.
