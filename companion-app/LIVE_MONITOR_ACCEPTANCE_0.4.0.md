# Live Monitor v0.4.0 acceptance

## Passed locally

- [x] One shared local IRSDK reader and deterministic evaluation path
- [x] No AI or network dependency in the live path
- [x] Native always-on-top Compact and Expanded windows
- [x] Open/hide from Home and Live telemetry
- [x] Tray action implementation, live status, pause/resume, Settings, and Exit
- [x] Close/minimize-to-tray and single-instance restore
- [x] Portable layout/hotkey/safe-glance/reconnect persistence
- [x] Position/size/opacity/lock/click-through/chrome/reset controls
- [x] Safe off-screen placement fallback and Per-Monitor V2 DPI configuration
- [x] Physical overall/class/ahead/behind gap models and rolling trends
- [x] Caution and pit-cycle trend suppression
- [x] Physical race gap separated from last-lap pace difference
- [x] Clean-evidence pace target and honest unavailable state
- [x] Fuel hard limit separated from unsupported strategic window
- [x] Critical priority for black flag, tow, mandatory repair, and fuel
- [x] Persistent clean-baseline braking cue and repair/traffic suppression
- [x] Safe-glance ordinary delay and urgent override
- [x] Disconnected state and reconnect baseline reset
- [x] Monitor-only resource measurement and pipeline drop/latency counters
- [x] Scenario captures for green, expanded, caution, fuel, repair, braking, unavailable, and disconnected states
- [x] Self-contained Release build, checksum generation, handoff verification, and automated tests

## Racing-PC acceptance still required

- [ ] Actual live iRacing session with representative same-lap and lapped traffic
- [ ] Borderless-windowed overlay behavior and second-monitor placement
- [ ] 100%, 150%, and 200% scaling on the intended monitors
- [ ] Disconnect/reconnect a physical monitor and verify restored placement
- [ ] Long real race for telemetry-age, CPU, memory, and dropped-frame logging
- [ ] Real pit cycle, caution compression/scoring correction, tow, repair, and reconnect transitions
- [ ] Global hotkey conflict check against Joshua's installed software

These hardware checks are not replaced by synthetic fixtures. Unsupported evidence remains unavailable until a real validated source exists.
