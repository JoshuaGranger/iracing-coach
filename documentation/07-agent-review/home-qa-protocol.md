# HOME_QA Protocol

HOME_QA is the only authority that may accept real iRacing telemetry and real-race Live Monitor usefulness. Replay, fixtures, development hosts, and screenshots cannot substitute for this protocol.

## Candidate identity

Record app version, installer SHA-256, source commit, Windows version/build, display topology and scaling, iRacing build, car, track/layout, session type, and test start/end UTC. Do not attach raw IBT files or secrets to a public issue or repository.

## Required scenarios

1. Start the installed app before iRacing; verify a truthful waiting state and automatic connection.
2. Join practice, qualifying when applicable, and race sessions; verify session resets and no stale values crossing boundaries.
3. Compare displayed position, class position, lap, flag, gear/RPM, fuel amount/percent, temperatures, brake bias when supported, pit-road state, and repairs against iRacing.
4. Exercise gaps with same-lap traffic, lapped traffic, caution, pit entry/exit, disconnect, and reconnect. Physical gap and pace comparison must remain distinct.
5. Exercise Default, Race, Qualifying, and one custom layout. Move, scale, lock/unlock, add, drag, resize, keyboard move/resize, undo, restart, and destination-display recovery.
6. Run at least 45 minutes with the monitor visible. Record update smoothness, stale-value incidents, dropped frames, compute/render latency, CPU, memory, and any iRacing frame-time impact.
7. Verify safe-glance cue timing on straights/caution/pit/lap completion and critical-warning precedence without unsafe distraction.

## Verdict format

For every scenario record PASS, FAIL, or NOT RUN; observed versus expected behavior; timestamps; sanitized screenshot/log references; severity; and exact reproduction steps. A release remains `HOME_QA pending` unless every release-blocking item passes and an explicit `RELEASE_ACCEPTED` decision names the exact installer hash.
