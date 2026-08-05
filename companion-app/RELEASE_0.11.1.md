# iRacing Coach 0.11.1 targeted development candidate

Date: 2026-08-03

## Release artifacts

- Installer: `artifacts/dist/v0.11.1/iRacingCoach-0.11.1-Setup.exe`
- Installer bytes: `498800680`
- Installer SHA-256: `8db7d07a6bbed942e94b11a120b40718d3e0b5161349381c58df50a24baf952e`
- Portable package: `artifacts/dist/v0.11.1/iRacingCoach-0.11.1-Portable-win-x64.zip`
- Portable package bytes: `382805629`
- Portable package SHA-256: `4df1235afd8cbd78e2ecd39c7d511844a0be804e77b011566b29d06b94df8e69`
- Source revision: `ed96871`
- Payload files: `9269`

## Change

Live telemetry no longer passes through the former 10 Hz source-polling and 4 Hz page-render ceilings. The app polls quickly enough to capture every native 60 Hz SDK tick and publishes each captured trace frame separately. Speed, throttle, brake, and steering are drawn on a canvas through the display's `requestAnimationFrame` cadence, with continuous time-window scrolling and within-pixel minimum/maximum preservation. Slower text, layout, status, and navigation surfaces remain independently throttled.

## Targeted verification

- Release build: zero warnings and zero errors.
- .NET regression suite: 74 passed, 0 failed.
- JavaScript syntax check: passed.
- Handoff/Python baseline before implementation: passed, including 173 Python tests.
- Accelerated deterministic replay exercised an effective 60 incoming frames per second in the open chart.
- Five-second open-chart sample: approximately 18.4% of one CPU core, 223.2 MB working set, and no working-set growth.

The installer/upgrade/uninstall lifecycle was not re-certified because this iteration changes only live capture and rendering. The complete unchanged lifecycle boundary remains recorded in the 0.11.0 release evidence. Real 60 Hz iRacing SDK acceptance requires direct validation on the racing PC.
