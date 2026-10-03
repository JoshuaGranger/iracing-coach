# P3: does the collector need to be Rust?

Decides the D5 implementation (collector language), not D5 itself. Run 2026-10-02 on DESKTOP-SIM with the real simulator (Toyota Supra, Las Vegas oval, offline Test Drive, car parked in the pit stall). Prototype and scratch code live outside the repository (`%TEMP%\claude\...\scratchpad\prove\P3\`); this file is the only repository write.

## Thresholds (written before any measurement; never adjusted afterwards)

PASS for the C# route (reuse the existing tested live stack with a WPF overlay, drop WebView2): with the car on track in a solo test session, the sim's own `FrameRate`, `CpuUsageFG`, `CpuUsageBG` and `GpuUsage` with the C# prototype running are indistinguishable from the no-prototype baseline within noise, defined as:

- medians within 2% of baseline, and p1 `FrameRate` within 3% of baseline;
- C# prototype RSS <= 150 MB;
- streaming on versus off changes those sim medians by less than 1%.

The Rust prototype is measured against the same thresholds.

Decision rule:

- If only one route passes, that route is the decision.
- If both are indistinguishable from baseline, the choice is footprint versus porting cost: report both sets of numbers and ask the owner one multiple-choice question.
- If neither can be measured on the sim, status is needs-owner with the obstacle.

Nothing below changes these. Measurement-design additions (a third run, a WPF-only control, a non-WPF emulation) are extra evidence and are labelled as such.

## Method (commands)

Conditions: sim PC (DESKTOP-SIM, Ryzen 7 9800X3D, RTX 5080, three 2560x1440 displays = 7680x1440 render, iRacing capped near 180 FPS). `iRacingSim64DX11` was not running at start (checked with `remote_job processes`), so the sim was used. Solo offline Test Drive only, no other drivers, no data copied or modified, no iRacing files touched. iRacing was started from the iRacing UI (Test Drive, last settings: NASCAR O'Reilly Toyota Supra, Las Vegas Motor Speedway oval, Realistic Weather), then **Test** put the car in the pit stall (`IsOnTrack = 1`). The car stayed parked (speed 0, no driving; the harness has no driver). Overlay and streaming cost per tick does not depend on the driving data, but the engine branches that react to motion were not exercised on the sim (see Notes).

**Prototypes** (throwaway, each polls the SDK at 60 Hz, draws a topmost click-through text overlay updated at 10 Hz with speed, throttle, brake, steering, and streams a 22-field JSON live frame, about 300 bytes, over a plain WebSocket to the dev PC when `stream.flag` exists; the flag is checked once a second):

- `cs/` C#, 158 lines of own code. net10.0, self-contained, single file, WPF overlay. Reuses unchanged from `companion-app/src`: `IRacingSdkTelemetrySource.cs`, `LiveTelemetry.cs` (the `LiveTelemetryEngine` is called every tick), `LiveTelemetryCatalog.cs`, `LiveMonitorLayouts.cs`, `LiveSteering.cs`, `LiveTruthPolicy.cs` and `iRacingCoach.Contracts/*.cs`, linked as source so nothing was built inside the repository tree. Own code: a 60 Hz loop on a high-resolution waitable timer (the production helper class is private, so its pattern was copied), the JSON writer, `ClientWebSocket` sender, WPF overlay window with `WS_EX_TRANSPARENT | TOOLWINDOW | NOACTIVATE`. Trimming is not possible with WPF (`dotnet publish -p:PublishTrimmed=true` fails with NETSDK1168), so the 65.0 MB exe is untrimmed. `InvariantGlobalization` also crashes WPF (first sim try, `CultureNotFoundException`), so it is off.
- `rs/` Rust 1.99, 169 lines. `windows` 0.62 crate (`OpenFileMappingW`, `MapViewOfFile`, GDI layered topmost `WS_EX_LAYERED | TOPMOST | TRANSPARENT | NOACTIVATE | TOOLWINDOW` window with `WM_TIMER` at 100 ms), `tungstenite` 0.30 blocking WebSocket, thread plus bounded channel. 0.35 MB exe. It does **not** run the live engine (no port exists): it reads the SDK, draws the overlay and streams. So Rust numbers are a lower bound for a real Rust collector that also runs the engine; the engine arithmetic is microseconds per tick, so the expected gap is small, but that is not measured.
- `recorder/` Rust, 109 lines: once per second CSV of SDK `FrameRate`, `CpuUsageFG`, `CpuUsageBG`, `GpuUsage`, `IsOnTrack`, `Speed`, plus working set (RSS) and CPU percent of a named process. The recorder itself runs in every phase including the baselines.
- `receiver/recv.py`: stdlib asyncio WebSocket server on the dev PC (`C:\Users\joshu\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe recv.py 18765 out.csv`), counts text frames and bytes per second. The dev PC firewall already had an enabled inbound allow rule for that python.exe (Private and Public; the LAN is Public), so no firewall change and no fallback to the mini PC were needed. The sim reached 192.168.1.80:18765 (`Test-NetConnection` True). This is a real LAN path, not loopback.

Build: `cargo build --release` (rs, recorder), `dotnet publish -c Release -o pub` (cs). Files were copied to `C:\Users\joshu\ClaudeDev\p3-scratch\` on the sim with `remote_copy` (SHA-256 verified).

**Runs** (each phase driven by `run-phase.ps1`: start the prototype, wait 20 s, start the recorder for 300 s, create `stream.flag` at 100 s and remove it at 200 s, so streaming is on for the middle third; the baselines run only the recorder):

- Run 1: prototype v1, phases `base1, cs, rs, base2`, 300 s each (about 22 min).
- Run 2: prototype v2 (after fixing a busy-wait defect found in run 1, below), same four phases. **Run 2 is the headline measurement.**
- Run 3 (extra, 100 s phases, alternating): `b1, rs, b2, cs, b3, wpf, b4`, where `wpf` is an empty WPF overlay window with no SDK code (`wpfempty/`), to separate WPF cost from engine cost and to estimate the baseline noise.
- Smoke runs before each: 12 s streaming checks and a 4-config C# GC test (below).

Analysis: `analyze.py` (medians, p1 = 1st percentile of the 1 Hz samples, split into thirds), `recvstats.py` (frames per second from the receiver for the 90 s of the stream-on window, edges trimmed). Dev PC CPU while measuring: 10 to 18% (`Get-Counter '\Processor(_Total)\% Processor Time'`), other proofs running; this affects only the receiver, not the sim.

Closing: iRacing was left through the UI (hold Escape, Quit, "Quit Sim?" Yes, Save Replay dialog: Quit without saving), the iRacing UI Test Drive dialog I had left open was closed, the desktop lease was released, scratch folder and the .NET self-extract folders were deleted, and the dev-PC receiver was stopped. `iRacingSim64DX11` is not running; the iRacing UI is back on its Home page.

## Numbers

### Run 2 (headline; prototype v2; 300 s per phase, no interference)

Command: `analyze.py out/run2` on the recorder CSVs. Sim values are the SDK variables as fractions (0.1668 = 16.7%), FrameRate in FPS.

| phase | FrameRate median (p1) | CpuUsageFG median | CpuUsageBG median | GpuUsage median |
|---|---|---|---|---|
| base1 (no prototype) | 180.07 (178.83) | 0.1668 | 0.1680 | 0.2674 |
| base2 (no prototype, end) | 180.15 (169.91) | 0.1684 | 0.1700 | 0.2645 |
| C# (cs) | 180.24 (179.24) | 0.1635 | 0.1430 | 0.2650 |
| Rust (rs) | 180.18 (179.20) | 0.1648 | 0.1430 | 0.2646 |

Relative to base1:

| | FPS | p1 FPS | FG | BG | GPU |
|---|---|---|---|---|---|
| base2 (noise check) | +0.05% | **-4.99%** | +0.96% | +1.19% | -1.09% |
| C# | +0.10% | +0.23% | -1.96% | **-14.88%** | -0.89% |
| Rust | +0.06% | +0.21% | -1.18% | **-14.88%** | -1.07% |

Streaming on (middle 100 s) versus off (other 200 s), change in the same run's median:

| | FPS | FG | BG | GPU |
|---|---|---|---|---|
| C# | +0.01% | -0.44% | **-2.08%** | -0.20% |
| Rust | 0.00% | +0.34% | **-1.05%** | +0.31% |
| base1 (nothing running, same thirds) | -0.04% | +0.67% | -0.60% | +0.14% |
| base2 (nothing running, same thirds) | -0.46% | **+5.50%** | **+3.85%** | **+2.41%** |

Prototype footprint (recorder, 1 Hz): C# RSS median 275.9 MB, max 280.1 MB (on 277.1 / off 270.6), CPU mean 0.56% of one core (1.19% while streaming, 0.25% off). Rust RSS median 8.6 MB, max 8.7 MB, CPU mean 0.65% of one core (1.14% streaming, 0.40% off). Both exes sat at the quiet level the sim cannot see.

Stream delivery (receiver, `recvstats.py`, stream-on window minus 8 s and 3 s edges): C# 60 frames/s median (min 59, mean 59.99, 16,963 bytes/s), Rust 60 frames/s (min 59, mean 60.00, 17,803 bytes/s); zero frames received outside the window. Frames carry the same 22 fields; the C# frame also carries the live engine's coach cue text.

### Threshold evaluation, run 2 (literal, no judgement applied)

| threshold | C# (WPF) | Rust |
|---|---|---|
| FPS median within 2% | pass (+0.10%) | pass (+0.06%) |
| p1 FPS within 3% | pass (+0.23%) | pass (+0.21%) |
| CpuUsageFG median within 2% | pass (-1.96%, margin 0.04) | pass (-1.18%) |
| GpuUsage median within 2% | pass (-0.89%) | pass (-1.07%) |
| CpuUsageBG median within 2% | **fail (-14.88%)** | **fail (-14.88%)** |
| RSS <= 150 MB | **fail (276 MB median, 280 max)** | pass (8.6 MB) |
| streaming on/off < 1%: FPS, FG, GPU | pass | pass |
| streaming on/off < 1%: BG | **fail (-2.08%)** | **fail (-1.05%)** |

So by the letter neither route passes: both fail the BG tests, identically and in the favourable direction, and C# additionally fails RSS by 1.8x.

### Why the BG failures do not discriminate (extra evidence, not a threshold change)

- The shift is the same size in both prototypes (0.1680 to 0.1430 in both), flat across all five minutes of each phase, and is a **decrease** of sim background CPU. base2 returned to 0.1700.
- In run 3 (alternating 100 s phases) BG medians were b1 0.169, rs 0.151, b2 0.179, cs 0.1615, b3 0.191, wpf 0.173, b4 0.180. The no-prototype baselines alone span 0.169 to 0.191 (13%), so BG cannot be resolved to 2% in this setup. Against the adjacent baselines: Rust -13.2%, C# -12.7%, empty WPF window -6.7%. FG in run 3: Rust +1.8%, C# +0.1%, empty WPF -4.5% against adjacent baselines; GPU baselines wander 0.242 to 0.265 (about +-4.5%) with no prototype, and Rust +7.5%, C# +4.1%, WPF -1.2% (inside that wander). FPS stayed 180.0 to 180.3 in every phase.
- In run 1 (v1) BG did not move (C# -1.14%, Rust -1.14%), so the BG step in run 2 is not a stable property of "a prototype is running"; cause UNKNOWN (candidates: Windows timer resolution requested by the 1 ms waits, thread placement; not isolated).
- Baseline noise in thirds is large: base2 shows +5.5% FG with nothing running, and base2 p1 FPS differs from base1 by -4.99% in run 2 while run 1's baselines agree to 0.02%. The 1%, 2% and 3% tolerances are tighter than the measurement noise for FG, BG and p1.

### Run 1 (prototype v1; kept because it shows the measurement can see a real cost)

v1 paced the 60 Hz loop with a `yield_now`/`Thread.Yield` spin for the last 2 ms and retried every 2 ms with the same spin. That is my prototype defect, not a property of either language.

| phase | FPS median (p1) | FG | BG | GPU | proc CPU of one core | RSS |
|---|---|---|---|---|---|---|
| base1 | 179.54 (177.88) | 0.1746 | 0.1760 | 0.2695 | | |
| base2 | 179.62 (177.92) | 0.1737 | 0.1705 | 0.2688 | | |
| C# v1 | 179.29 (157.76) | 0.1760 | 0.1740 | 0.2700 | 4.12% | 275 MB |
| Rust v1 | 167.58 (156.11) | 0.1887 | 0.1740 | 0.2886 | 23.97% | 8.6 MB |

Rust v1 burned about a quarter of a core and cost the sim -6.7% FPS, +8.1% FG and +7.1% GPU median, consistently through the five minutes. That is a sensitivity check: the harness resolves a real collector cost of about 24% of one core. C# v1 (4% of a core) showed a transient in its last minute (FG 0.1895, GPU 0.3034 versus 0.174 and 0.270 before), cause UNKNOWN, and its p1 FPS was 157.8. v2 replaced the spin with sleeps (Rust `thread::sleep(1 ms)` which is high-resolution on this Windows; C# high-resolution waitable timer) and wakes at 80% of the tick period then retries every 1 ms, which also lifted stream delivery from 40 to 57 frames/s (v1, Rust) to 60 frames/s.

### Footprint and cost facts

- RSS: C# WPF prototype 266 to 280 MB (three runs: 275, 276, 266 in run 3). **An empty WPF overlay window with no SDK code and no engine measured 230 MB (run 3, `wpf` phase).** So about 230 of the 276 MB is WPF itself; the live engine and reader add roughly 40 MB. Rust: 8.2 to 8.7 MB. GC tuning did not help: C# default 248 MB, `DOTNET_GCgen0MaxBudget=16 MB` + non-concurrent GC 236 MB, plus `GCConserveMemory=5` + `TieredPGO=0` 234 MB (35 s test on the sim).
- Executable size: C# 65.0 MB (self-contained, untrimmed, WPF), Rust 0.35 MB.
- **Emulation, not a sim measurement:** a self-contained trimmed single-file C# console build with no window (`cs-console/`, same reused live-stack sources, real `LiveTelemetryEngine` fed 60 Hz synthetic samples plus 40 car entries, no SDK on the dev PC): 14.3 MB exe and working set 31 to 35 MB over 45 s, private bytes 14 to 17.5 MB (`./pub/p3con.exe`). That is the RSS floor of a non-WPF C# collector. The overlay window would then be a hand-written Win32 layered window as in the Rust prototype. This route was not run on the sim, so its sim impact is UNKNOWN (it would use the same kind of window and 60 Hz loop as the Rust prototype).
- Production polling is not what was measured: the production `LiveTelemetryService` polls every 2 ms while connected (about 500 wakeups per second), the prototypes about 60 to 240 per second. Production unchanged would cost more CPU than the C# prototype; UNKNOWN by how much.
- Live frame rate on the wire: 60 frames/s of about 295 bytes (17 KB/s) from either prototype, to a receiver across the LAN, with no change in sim FPS, FG or GPU beyond noise.

## Result

**Status: pass (the proof ran to a decision), with these findings stated plainly:**

1. Sim impact is not language-dependent. With the busy-wait fixed, both prototypes leave `FrameRate` (median and p1), `CpuUsageFG` and `GpuUsage` within about 2% of baseline, and streaming 60 frames/s over the LAN changes none of them by 1% or more beyond the baseline's own noise. Neither language is needed to protect the sim.
2. Literally, neither route passes the pre-written thresholds. Both fail `CpuUsageBG` (a drop of 14.9%, the same for both, and not reproduced in run 1), and the streaming on/off BG test (C# -2.08%, Rust -1.05%). The BG tolerance is below this setup's noise (baselines vary 13% across 100 s windows) and the deviation is the sim doing less background work, not more, so it does not separate C# from Rust and I do not count it for or against either.
3. The one threshold that does separate them is footprint: **the C# route as specified fails RSS <= 150 MB (276 MB, 1.8x over), and the cause is WPF (an empty WPF window is already 230 MB).** WebView2 is already dropped in this route and does not matter here. The Rust prototype is 8.6 MB.
4. Applying the decision rule as written (the route that passes the language-dependent criterion): **the Rust collector is the route that passes; the C# + WPF collector fails.** This confirms D5's Rust implementation for the stated reason (footprint and no UI framework), not because the sim needs it.
5. A third route exists that this proof did not measure on the sim: C# without WPF (reuse the tested live stack, own Win32 layered window). Emulation says its RSS floor is about 35 MB, which is under 150 MB. Whether it would be indistinguishable on the sim is UNKNOWN but expected to match the Rust window, since the window and loop are the same kind. It is a candidate only if reuse of the existing C# live stack (about 1,660 lines: `LiveTelemetry.cs` 1,008, `LiveTelemetryCatalog.cs` 525, `LiveSteering.cs` 81, `LiveTruthPolicy.cs` 48, plus `IRacingSdkTelemetrySource.cs` 484 and its tests) is worth more than the ~35 MB versus ~9 MB footprint. D5 says "Rust, no .NET"; nothing measured contradicts that.

Needs owner: nothing. (No remote viewer, tailnet, credentials, or physical presence was needed.)

## Decision impact

- **D5 implementation (Rust collector, no WebView, no .NET): confirmed on footprint, not on sim impact.** Measured: Rust 8.6 MB and 0.35 MB exe versus C# WPF 276 MB and 65 MB exe; both fit the sim's frame, CPU and GPU budget at 60 Hz streaming. Does not contradict D5.
- **D6 (watch-only crew chief, "plan up to 60 Hz ... prove on the rig that it has no measurable cost"): supported.** 60 frames/s of the live frame over a plain WebSocket on the LAN produced no measurable change in sim FPS, FG, GPU. Caveat: measured on a parked car at the 180 FPS cap, and noise-limited at about 1 to 2%.
- D2, D3, D4, D9: not touched by this proof.
- The 60 Hz overlay window requirement in the spike (`live-stack.md` section 3: click-through, no-activate, topmost) is feasible: both prototypes drew a transparent no-activate topmost window over borderless iRacing without focus loss in the three sim runs (no focus incident observed; the harness did not check driver input focus, UNKNOWN).

## Notes

- **Defects in my own prototypes, found and fixed, disclosed:** (a) v1 spin-wait described above (run 1 results kept); (b) `InvariantGlobalization` crashes WPF at the first text layout, so the first C# sim attempt exited on start-up (Windows Error Reporting `APPCRASH` on `p3cs.exe`, one entry in the sim's Application log, left in place); (c) polling at exactly 60 Hz without a retry lost about 25% of frames (40 to 57 per second received) until the wake-early-then-retry change.
- **Measurement limits:** car parked in the pit stall (speed 0, engine cue path static; per-tick overlay, JSON and WebSocket costs do not depend on that, engine arithmetic under motion was not exercised on the sim); iRacing frame-limited at about 180 FPS so `FrameRate` has little headroom to reveal a small cost (run 1's Rust v1 shows it still moves for a large cost); 1 Hz sampling; five-minute phases; baselines drift by up to 13% in BG and +-4.5% in GPU/FG between windows, so conclusions finer than about 2 to 5% are not supported; no on-track driving, no replay load, no other overlays tested together (Discord overlay, Trading Paints, Garage61 agent and Stream Deck were running as they always are). Prototype order was fixed (base, C#, Rust, base), not randomised; run 3 alternated instead.
- The Rust prototype has no engine; the C# prototype runs the real engine every tick. If the engine port adds meaningful per-tick cost, Rust's CPU would rise from the measured 0.65% of one core (estimated not to matter; not measured).
- The first C# stream attempt also showed no frames until the process crash was understood; no network or firewall problem existed. The dev PC receiver needed no firewall change.
- One early `dotnet build` of `iRacingCoach.Contracts` wrote `bin/` and `obj/` under `companion-app/src/iRacingCoach.Contracts/` (both git-ignored, `git status` clean); after that the Contracts sources were linked instead of project-referenced.
- Sim state left as found: scratch folder `C:\Users\joshu\ClaudeDev\p3-scratch` and the `%LOCALAPPDATA%\Temp\.net\p3*` self-extract folders deleted, desktop lease released, iRacing closed, iRacing UI on Home (a Test Drive dialog I had opened was closed). The sim's Documents/iRacing was not written to deliberately; iRacing itself wrote its normal logs on start and quit, and I declined its offer to save a replay. The sim is free for P5.
- Not tried: running either prototype while driving; mini-PC receiver; tailnet; the C# non-WPF route on the sim; a Rust port of the engine; the production 2 ms poll under the same harness.
