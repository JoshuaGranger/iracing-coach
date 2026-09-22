# iRacing Coach 0.17.6: recorded-data native QA follow-up

Date: September 21, 2026. Final application source: `21312afe9bf9ab634e2d225674836fc196a618a8`. This continues the [0.17.5 simulator-PC test](implementation-snapshot-0.17.5-development.md). Joshua requested recordings only for this pass; active live telemetry is deferred.

## Test access recovered

A full user-initiated Codex restart restored nonblack native screenshots and working navigation on DESKTOP-SIM. Earlier diagnostics had confirmed matching active Windows sessions and privileges, with a readable Default input desktop. A fresh Computer Use helper alone had failed. There is no evidence that Joshua's desktop was locked, and the precise helper failure remains unknown.

Access worked through the final replay and setup-comparison checks, then failed again when navigating to Planning. One refreshed-window recovery returned `GetCursorPos failed: Access is denied (0x80070005)`. Native input attempts stopped. The planning visual retest remains incomplete; this recurrence is an automation-access failure and is not evidence of an application crash.

The test used isolated 0.17.5/0.17.6 development copies and disposable profiles containing verified copies of real recordings and setup files. Installed 0.17.0, production settings and original source bytes were preserved.

## Race replay: racing frames were discarded

The important new defect was in the UI's phase filter. It accepted a state containing `race`, which does not match the backend's canonical `racing` state. A real 8,203-frame replay therefore lost all 8,014 racing frames during rendering. Seeking appeared to change the clock while the map remained frozen, and the UI reported an artificial gap between the last parade frame at session time 201.667 seconds and the first checkered frame at 3,563.567 seconds.

The fix explicitly accepts `racing` and includes the race-start transition in the event timeline. It does not change backend sampling, source data, telemetry math, or gap thresholds. Exact stored data reproduced the problem locally through the production C# mapper and replay component. After the fix, seeks at 5%, 50%, 50.67% and 90% select recorded racing frames and report the instant available. Before the fix, all four remained at the last parade frame and reported a gap.

Regression tests failed before the fix and now verify that racing frames remain between parade and checkered frames, routine display decimation is playable, and explicit source gaps remain unavailable. The production component was also checked against all 15 recorded gap intervals inside the real replay's playable player range: every interval remained unavailable.

### Unavailable replay explanations

The first replay fixture was a short cooldown-only capture. It contained 324 display frames and player rows, but no finite player lap position. Disabling playback was correct. An unrelated partial-capture warning obscured the reason with generic text. The UI now says, "Your car's track position was not recorded during the replay."

This was not a cache-loading defect: the native UI cache already contained the same partial replay as the saved backend report. No cache was edited to manufacture playback. A longer authentic capture from the same race supplied 204,870 raw frames in 341 hash-verified chunks. Normal discovery of the genuine earlier IBT then produced the playable grouped race. The combined replay has 205,798 unique source frames, 8,203 display frames, a 2.308 Hz display rate and 16 explicit display gap markers.

## Planning: aligned phase evidence

Native visual inspection found the zone label, Early and Middle phases occupying one grid row, while Late wrapped into the narrow label column. The label now spans the full row, with the supported phase columns sharing the available width. Existing small-window stacking remains.

The expanded corner text already showed readable entry/minimum/exit speeds, peak brake percentage and onset/release/turn-in positions. The change addresses alignment and wrapping without changing those measurements or their evidence status.

The longer grouped Bristol recording generated a 160-lap plan with one stop, a lap-80 target and a conservative 141.9-lap range. This is a different recording group from the earlier 15:09 Bristol benchmark; the earlier 139.4-lap range remains valid for that earlier fixture.

## Setups: verified selection and clearer comparison

Native 0.17.5 testing confirmed independent selection of identical-content Ford and Toyota files. A fresh Save a copy of the Ford selection retained the Ford source in its manifest; source/copy hashes matched. Previously saved evidence was preserved separately.

The native catalog displayed 1,081 entries: 1,079 local groups plus two recorded setups. Bristol search returned 22, a season filter reduced the result to eight, and a Ford filter to two. An unmatched search showed an empty state. Clear filters restored all entries and reset the controls. The dedicated Bristol track filter also exposed both recorded setups with numeric values.

Numeric comparison worked, but both table columns could have the same setup filename. Version 0.17.6 labels them "Selected setup" and "Comparison setup" and adds linked recording dates to the headings and comparison selector where available. Dates come from existing evidence associations; filenames and numeric settings remain unchanged.

## Layout persistence and native coverage

A custom "QA Recorded Layout" was created by copying a telemetry layout and pairing Speed with Air temperature through drag interaction. The setting was saved to the disposable profile and survived closing the editor, opening the updated race and a full native application process restart into the final 0.17.6 build.

Native replay checks before the phase fix confirmed Play/Pause and 2x clock progression, but the map/seek result was defective. Those initial interactions are not a playback acceptance claim. Replay focus-driver and zoom controls were not exposed in the observed page, so they are not claimed as tested.

## Verification and development artifact

- 390 application tests passed, zero failed or skipped.
- 1,029 backend tests passed at the start of this pass. Backend code is unchanged; final repository/contract checks passed without repeating that unchanged suite.
- A .NET self-contained Release win-x64 development publish completed successfully.
- Final executable directory: `companion-app/artifacts/dev/v0.17.6-dev-20260921-205951`.
- Final transport: `iRacingCoach-0.17.6-development-transfer-v3.zip`.
- Size: 87,253,058 bytes; 668 manifested files.
- SHA-256: `7a8e0102f11e554b6e68f04d592350e09f93d50f8e9a17bcfa8f5918ae9fd892`.
- Private transfer directory: `\\192.168.1.82\Joshua\iRacing-Coach-QA-20260921`; destination hash verified.

The transport contains the .NET runtime and tracked backend source. Python and Coach Engine reuse existing installed runtimes in the isolated test directory. It is not an installer or a complete portable release. Earlier development transports are preserved; v3 is the final candidate for this report.

Private diagnostics, the redacted replay/geometry reproduction and test logs remain under ignored `companion-app/artifacts/remote-qa-20260921` and `companion-app/artifacts/restart-retest-*`. No private telemetry, paid setup contents or credentials were added to source control.

## Final native retest

DESKTOP-SIM verified the final v3 transport's size, SHA-256, source revision and all 668 manifested files, then launched it from `C:\Temp\ICQA0176v3-0921\app` with the same disposable test profile.

Confirmed in the native final build:

- A 5% replay seek displayed 2:50, lap 5 and synchronized telemetry, with no artificial gap.
- A 50% replay seek displayed 28:29, lap 67 and 130 mph, with no artificial gap.
- The 2x selector and Play/Pause worked; the map and telemetry moved as the clock advanced from 28:29/lap 67 to 28:46/lap 68.
- The custom Speed/Air temperature layout persisted through the full application restart.
- The setup comparison selector displayed a recording date. Table headings distinguished "Selected setup" (September 20, 9:08 PM) and "Comparison setup" (September 20, 3:09 PM), resolving the identical-filename ambiguity.

The native slider has 1,000 steps across approximately 3,419 seconds of playable time. None of the short explicit gap intervals in this recording contains an exact integer slider position, so native gap-overlay acceptance is not claimed. The real-data production-component check above verifies all 15 playable gaps directly.

The renewed Windows input denial prevented the corrected planning alignment from being inspected in the final native build. Replay screenshots were visually inspected in the remote task but were not saved as standalone files before access failed; no new screenshot artifact is claimed. The final remote evidence is preserved privately as `DESKTOP-SIM-0176v3-QA.md` alongside the development transport and in the ignored local evidence directory.

## Remaining limits

Cold analysis is unchanged in this UI follow-up: the controlled Bristol result remains about 18.7 seconds cold and 1.8 seconds cached. Active simulator telemetry is excluded by Joshua's instruction to use recordings. External connections, verified tuning geometry and completed tuning feedback remain outside this recorded-data pass. Every-control, every-resolution and production-installation acceptance are not claimed.
