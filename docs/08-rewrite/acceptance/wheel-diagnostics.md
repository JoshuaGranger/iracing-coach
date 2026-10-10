# Optional wheel interruption recorder

Joshua requested carrying the October 7 SAB-L04 diagnostic recorder into the
current Rust companion on October 9. This is local troubleshooting evidence for
the intermittent 3–4 second loss of steering/force feedback. It observes the
selected steering axis and existing public SDK input; it does not test force
feedback or establish the cause.

## Source reuse

The supplied branch `codex/wheel-disconnect-diagnostics` and frozen commit
`40fd67651d5950059919d38d9c0367164600e9fb` were absent from this checkout and the
branch was absent from GitHub when inspected. The existing **Add wheel diagnostic
recorder** chat (`01a118ad-b1cd-7261-8082-e775454a80f9`) was accessible. Its complete
source additions and subsequent changes were replayed into ignored
`target/wheel-source/`, with every retained hunk matched against its preceding
source. That recovered the recorder, DirectInput source, USB helper, controls,
integration actions, guide and 16 original regression tests before implementation
started. `target/wheel-source/provenance.json` identifies the source.

The actual Git patch on SAB-L04 was not transferred, so recovered files are not
claimed byte-identical to that commit. The original implementation was inspected
and its lifecycle, retention, export and cleanup semantics informed the Rust port;
the retired WPF/.NET/Blazor application is not reinstated.

## User workflow

Install the current **iRacing Coach Companion** from the website's **Download
companion** link. It starts at Windows sign-in and shows a tray icon. Open its
wheel interruption recorder from **Troubleshooting → Wheel interruption recorder**. Refresh
controllers, choose the wheelbase and steering axis, enable recording and turn
the wheel to confirm the raw axis changes. Every launch starts recording disabled.

The recorder continues while its panel is hidden. After a dropout, promptly mark
an incident from the tray or panel. Wait at least 20 seconds before stopping and
exporting; stopping earlier preserves an explicitly incomplete post-event window.
The export is a local ZIP. Ordinary recording needs no administrator rights.
Optional USB tracing has a separate action and administrator prompt.

An explicit **Recover interrupted USB trace** action is available while recording
is off. It prompts separately, recovers only this user's application-owned traces,
and starts no new trace or recording. It also works when preserved interrupted
traces prevent a new capture from reserving space; confirmed-safe rolling history
can then be pruned on the next enable. Uncertain cleanup preserves the data.

## Intended bounds

- Selected game-controller axis at 10 Hz, shared nonexclusive background
  DirectInput. No buttons, keyboard capture, input injection or FFB commands.
- UTC and monotonic timing, raw axis, availability/native read error, and the
  existing SDK steering/connection/source time/reading age. A steady axis never
  implies a disconnect. Readable-to-unavailable transitions may mark an
  observation, throttled to once per 30 seconds.
- 600 samples of memory history, eight incidents with 10 seconds before and 20
  seconds after the mark, four sample segments of at most 1 MiB, normally three
  rolling sessions, and a 192 MiB rolling-storage budget. Uncertain USB cleanup
  retains recovery metadata and blocks export.
- Rolling data under `%LOCALAPPDATA%\iRacingCoach\wheel-diagnostics`. Exports under
  `Documents\iRacing Coach\exports\wheel-diagnostics`, with no automatic export
  deletion or upload. Bundles allowlist metadata, manifest, sample NDJSON,
  incident JSON and optional USB status/ETL; helper owner/heartbeat files are
  excluded.
- Optional USBHUB3/UCX/USBXHCI ETW uses Default state keywords, a 32 MiB circular
  ETL, bounded buffers and an explicitly elevated same-user helper. Parent
  identity, monotonic heartbeat, stop, confirmed session absence and recovery
  protect helper lifecycle. This can include other USB devices' state metadata.

## Review corrections

The old DirectInput source interpreted any nonnegative `GetDeviceStatus` HRESULT
as attached. `DI_NOTATTACHED` is a successful HRESULT with a different meaning.
The port distinguishes attached, absent and native errors without inferring the
cause of a wheel interruption.

Independent ABI review also found that the inherited `IDirectInput8W` status call
used vtable slot 6 (`RunControlPanel`) instead of slot 5 (`GetDeviceStatus`). This
is corrected in the port and covered by a fake native vtable dispatch regression,
in addition to checking method signatures and layouts against Microsoft's
`dinput.h`. Recorder fakes alone did not exercise this boundary.

Incident markers now retain the time of the UI action, update the associated SDK
reading age, and do not pause sampling while the user responds to optional UAC.
USB heartbeat and stop intent are independent of the pending administrator
prompt. Completed exports clear the controller-enumeration list from their
manifest; only the selected wheel's metadata is included.

USB cleanup remains uncertain when a positive session query contradicts a
successful `logman stop`; export does not claim cleanup from that command alone.
Recovery verifies the parent before each stop/query and progresses through at
most 16 owned interrupted sessions per action, retaining ETL bytes. A second
explicit action can recover the next group without reserving storage.

## Verification and release

Both full `tools/check.ps1` and `tools/check.ps1 -Linux` passed again after the
final ABI/export/recovery corrections. This includes the unchanged Python suite
(1,485 cases, 18 Windows skips and 38 Linux skips), workspace checks,
generated-contract/theme drift checks, web typecheck, all 643 web tests and the
production build. Final logs are ignored under
`target/companion/check-wheel-final*.log`.

Independent export verification used Python's standard `zipfile` reader for CRC,
valid timestamps, flat unique allowlisted entries, UTF-8 JSON/NDJSON, declared
bounds, 321 samples plus one mark, and a completed 300-sample incident within its
10-second/20-second window. Windows .NET ZIP extraction also passed. The check
found invalid zero DOS dates in the first custom stored ZIP; local and central
headers now use valid January 1, 1980 dates. The final synthetic bundle, including
recovery status, is 160,135 bytes under ignored `target/wheel-export-validation/`.

Four native software UI smokes passed default-off/no-device/selection controls,
Tab/Escape, DPI scrolling, busy-state responsiveness and continued fake capture
after the panel closes, plus explicitly requested recovery while off without
creating capture files. The existing replay overlay/tray smoke passed. The final
rendered panel was inspected from `target/wheel-ui-smoke/panel-recovery.png`.

The final collector suite passed 204 cases (six existing opt-in cases ignored),
including 13 recorder regressions. The Windows-module subset passed 26 cases,
covering SDK ABI layouts and native dispatch, helper identity/lifecycle/recovery,
and the native panel. Collector formatting and all-target clippy passed with
warnings denied. The release binary is 2,183,168 bytes, below the 3 MB size limit.
The real isolated `deploy/windows/Test-Companion.ps1` passed fresh install,
Windows installed-app/startup registration, refusal while a harmless fake sim
was active, upgrade of a running companion, settings/identity retention and
uninstall. Nested synthetic wheel-diagnostic files stayed byte-identical through
upgrade and removal. Evidence:
`target/companion/smoke-d4d0a7c51a364a98a8e35a43463a807e`.

Released code revision `538afdcc581596da5c136a9994e4b7ff06fd1b5f` through
`deploy/mini-pc/Release-WithCompanion.ps1` under D14's standing permission.
The managed service reported healthy. The unsigned production installer is
2,802,306 bytes, SHA-256
`d8a5737cd67e3f5a92724d89feb7dc9d8b6a1935acb8858869d0a0a87566964d`.
Both the wrapper's HTTP check and a real browser click on the live **Download
companion** link verified those exact bytes. The click preserved the Sessions
page, and a missing download returned a proper 404. Previous installer releases
remain available in the managed download directory. Release/browser evidence is
under ignored `target/companion/`.

Real Simagic input/FFB behavior, physical reconnect, native tray operation during
a race, actual UAC/ETW cleanup and racing-PC impact remain hardware acceptance
work. Source/fake-based tests do not establish those results. No real wheel
capture or USB trace is started by this development task.

An authenticated read-only process check on DESKTOP-SIM at 01:20 UTC October 10
still found `iRacingSim64DX11` running (PID 25376). Sim-PC work stopped there; no
installer, companion replacement, recording or desktop interaction was performed.
