# Rewrite: owner decisions (October 2, 2026)

Joshua's decisions for restructuring iRacing Coach into a sim-PC collector and a mini-PC service, recorded from a 15-question interview in Claude Code on 2026-10-02. This file is the authority for the rewrite. Where it conflicts with older documents (one-PC install, WPF/Blazor desktop, no local service, UNC rejection, win-x64 package rule), this file wins.

Background evaluation (untracked): `.debate/should-iracing-coach-move-from-its-curre-20261002T164400Z/architecture-evaluation.html`. Its advice to keep a one-PC mode and to prototype before migrating is overruled below; its engineering cautions still apply.

## Directive

- "Instead of being a local PC installation for the entire app, I want to move it to a docker container on my home's mini pc."
- "I want to use whatever programming language(s) will maximize/optimize the performance of the app."
- The sim PC keeps "a very lightweight and optimized installation that does not impact simulator performance" and "performs all the features needed on the windows side."
- North star unchanged: "5 features that win me more races" over 1000 features. Capability may grow; interface complexity may not.

## Topology

| Box | Address | Role |
|---|---|---|
| Sim PC (DESKTOP-SIM, Windows) | 192.168.1.21 | Native collector and live monitor |
| NAS | `\\192.168.1.82\apps\iracing-coach` (folder not yet created) | Recordings in transit and the raw recording archive |
| Mini PC (`g-house-pc`, Debian 12, Docker, Intel N100) | 192.168.1.81, tailnet `minipc.tail4e6ca6.ts.net` | Service, analysis, web UI; stack `iracing-coach` |
| Browsers | desktop, rig, tablet, phone | Review and live pages; guests arrive over Joshua's tailnet |

The NAS and the mini PC are different machines. The mini PC does not mount the NAS today.

## Decisions

- **D1 Deployment: mini PC only.** No one-PC mode and no local analysis fallback. When the mini PC or LAN is down, capture and the live monitor keep working and recordings queue.
- **D2 Backend: staged Rust port.** Rust server, ingestion and IBT decode first, with the existing Python analysis running as the container's worker so the split is usable early. Rust then replaces Python stage by stage, each stage gated on identical output against the Python oracle. An unexplained numeric difference is a failure, not a tolerance.
- **D3 Web UI: TypeScript single-page app** served as static files by the Rust server. The existing JS cursor/map loops and CSS carry over; Razor and coordinator logic are rewritten. No .NET on the mini PC. Pointer movement never causes a request; loaded data stays inspectable when the server drops.
- **D4 Transfer (superseded by the Prove-wave ruling below): NAS for files, direct connection for status.** The collector copies each finalized recording to the NAS share (temp name, rename, hash manifest) and notifies the service. The mini PC mounts the share, verifies and ingests, and reports status over the direct connection. Raw recordings live on the NAS. SQLite and derived data never live on the share.
- **D5 Live: native overlay on the rig plus on-demand streaming.** The collector (Rust, no WebView, no .NET) reads the SDK, runs the live calculations and draws the always-on-top monitor. It keeps a small presence signal with the service so the web UI shows a "Live" indicator whenever iRacing is running. Opening the live page tells the collector to start streaming; it keeps streaming until the session ends, then stops unless someone is still on the live page. Joshua: "it doesn't have to be this exact specification but you get the idea." No bulk file transfer while the car is on track.
- **D6 Crew chief: watch only.** A friend or family member sees a read-only live page with the same data the rig's Live dashboard has. The rate need not be low: plan up to the SDK rate (60 Hz) for inputs and track position, chosen per device, and prove on the rig that it has no measurable cost. Nothing travels back to the car in v1.
- **D7 Access: no authentication.** The site opens on a list of drivers that have data; with one driver it opens straight to that driver ("Joshua Granger"). Guests use Joshua's tailnet; being on the tailnet is the authorization. Joshua: "I am not worried about encryption or safety." The data model is keyed by driver.
- **D8 v1 scope:** Race investigation (Sessions library; Race, Runs, Sections, Traces with the synchronized chart/map cursor), Live monitor (including today's text "Coach cue" tile as it is: no new cues, no audio), Setups and tuning (library, compare, Find a baseline, Progressive Tuning).
- **D9 Setup library fills itself.** It must not be empty when recordings exist and must show at least every setup run in a race. Sources: `CarSetup` from every recording of every session type; garage `.htm` exports; passive garage capture (the collector records the setup values iRacing publishes over the SDK when a setup is loaded or applied in the garage, linked to the `.sto` by name and file hash). `.sto` files without values are listed as values unknown. A rig test must confirm the SDK behaviour early.
- **D10 Integrations:** Garage61 runs on the server with its token as a root-only stack secret. Ask is a later phase and will use an API key the way the g-house apps do, not a signed-in Codex CLI.
- **D11 Devices:** desktop browser, sim rig screen, tablet and phone are all acceptance targets.
- **D12 Migration:** re-ingest and re-analyze the existing raw recordings; convert user-authored data (notes, saved Tests, plans, tuning) once. No legacy report formats are supported.
- **D13 Cutover:** the current desktop app is retired now. Joshua: "retire it now. not in use. go all-in." No parallel run and no rollback requirement.
- **D14 Process:** "go A to Z. full autonomy. release on mini PC at will. my only ask is that you do things in parallel with multiple sub-agents and minimize rework."
- **D15 Review: critical paths only.** Sonnet 5.5 builds routine code. Opus 5.5 at high effort handles architecture, IBT decode and analysis parity. Codex Sol 6.1 reviews the plan, contracts, ingest protocol and analysis kernels.

## Prove-wave rulings (2026-10-02, evening)

Five proofs with thresholds fixed in advance ran before any design (`prove/P1` to `P5`, compiled in `prove/verdicts.md`). Joshua ruled on the two that needed him; the rest confirmed or narrowed the decisions.

- **D2 ruling: B, the staged Rust port, by the written rule.** P1 failed only the Frigate detection-fps criterion (13.0 s wall, 2.27 GiB peak, HA untouched); the rule that follows a FAIL is to port the stages the profile shows dominate, in that order: per-sample kernels and trace payload (71.5 % of time), then IBT decode (11.8 % of time, most of the peak memory), then JSON writing. v1 ships with the Python worker in the container; the port follows as staged phases after v1 is usable, each gated on byte-identical output against the Python oracle (`analysis_id` pinning, Python 3.12 compensated `sum()`, exact `statistics`). If the architect takes decode before kernels for a dependency reason, the plan states that reason. Pin Python 3.12 in the container; `mem_limit` at least 3 GiB until the memory stage lands.
- **D4 ruling: HTTP upload is the bulk path.** P4 measured 5.6 to 7.3 MB/s to the share from both PCs (threshold 50 MB/s) and the same ceiling on every LAN path. The collector uploads each finalized recording to the service over a resumable, hash-verified, acknowledged, idempotent HTTP connection; the service writes raw recordings onward to the NAS over NFS (the `apps` export already admits the mini PC with no credentials), so raw recordings still live on the NAS and the sim PC needs no NAS access. Writers use write-through or a hash after flush before any commit. The LAN ceiling (about 6 to 8 MB/s, 6 ms ping) is Joshua's infrastructure item; the design must not depend on it being fixed. No bulk transfer while the car is on track stands.
- **D3 confirmed (P2):** Razor in WebAssembly rendered Traces in 74 to 78 s at 6x CPU throttle against a 3 s limit. The SPA's own cursor loop at 6x becomes an acceptance gate; 55 fps is not assumed.
- **D5 implementation confirmed (P3):** on the real sim, both prototypes left frame rate, foreground CPU and GPU within noise while streaming 60 frames/s; C# with WPF needs 276 MB, Rust 8.6 MB. The collector is Rust with no WebView and no .NET. Re-prove on track with the live engine in the agreed solo sessions.
- **D9 narrowed (P5):** iRacing publishes `CarSetup` on every garage load and apply (`UpdateCount` increments) but `DriverSetupName` and `DriverSetupIsModified` change only at garage exit, for the last file loaded. Passive capture records values on every `UpdateCount` change while `IsInGarage` is 1 and commits the record, linked to the `.sto` by name and file hash, when `DriverSetupName` changes at exit; earlier loads in a visit are values without a name. Derived keys (corner weights, ride heights, preloads, last temperatures and pressures, tread) are excluded or matched with a 1 N / 1 mm / 0.2 Nm tolerance. Defect to fix in the Python oracle: `ibt_reader.py` reads `DriverSetupModified`; real recordings carry `DriverSetupIsModified`.

## Parking lot (not in this product now)

- Bulk `.sto` harvest (an agent loads every `.sto` in a test session) and `.sto` decryption. Joshua: these "are utility functions that need to join the test driving stuff on a separate app or something."
- Plan, saved Tests and Test day screens; Replay; Details; Ask; crew-chief calls that appear on the overlay. Their data is still preserved by D12.

## Still binding

- Never modify or delete raw iRacing telemetry, replay, setup or purchased source files.
- No Garage61, AI or other credentials in the repository, settings files, arguments, logs or fixtures. Secrets reach the mini PC on standard input through `g-house-app set`.
- Deterministic analysis works without the internet, Garage61 or AI.
- No exact target telemetry, directional groove labels, damage onset or repair-only time unless backend evidence supports the claim. Evidence labels and screening semantics do not change in the port.
- Preserve unknown optional JSON fields; fail clearly on missing required fields or incompatible contract versions.
- `config/theme.dark.json` remains the canonical UI token source.
- Product and design rules in `docs/01-product/companion-design-principles.md` and `visual-companion-redesign-2026-09-22.md`: one well-planned investigation screen, synchronized chart/map cursor, no accordions hiding inputs, concise racing language.
- Mini PC rules (the `mini-pc` skill): never delete data, never restart Docker or reboot, change containers only through the managed tools, keep services small with `mem_limit`, leave the `g-house` stack alone. D14 is the standing permission to release the `iracing-coach` stack.
- Sim PC rules (Joshua's global instructions): use the remote tools, never log off or reboot, work on copies of real data, and never join a session with other people under automation.
- **Sim PC access: paused and then lifted the same evening.** Joshua paused it ("Stop using remote engineer and desktop-sim for now") and later lifted it in writing ("Desktop sim sub agents allowed again"). Agents may use the remote-engineer tools on DESKTOP-SIM again under the standing rules: only when the simulator is not in use (check for the iRacingSim64DX11 process first and stop if it is running), solo offline sessions only, copies of his data, never modify iRacing's own files, leave the machine as found and release every lease. Steps marked [sim: blocked] in `workstreams.md` are unblocked and carry those rules.

## Verified facts (2026-10-02)

- Backend: `iracing-coach/skills/analyze-iracing-race/scripts`, 45 stdlib-only Python modules (about 49.6k lines), 1,347 unit tests, 26 MCP tools, no numpy. Cold analysis 7.1 s (146 MB recording) to 10.3 s (203 MB) on the main PC; peak working set 2.1 to 2.3 GiB. `analysis.json` is 40 to 80 MB per session.
- Desktop app: WPF host with BlazorWebView; coordinator about 20.4k lines of C#; UI 42 Razor files (12.3k lines), 10 JS files (3.6k), CSS (5.2k). The chart/map cursor is JavaScript.
- Live today: C# SDK reader, a text "Coach cue" tile (black flag, tow, mandatory repair, fuel limit, caution, pit road, gap closing, pace band, braking overshoot), WPF always-on-top monitor, `.ircr2` live-replay capture. No audio.
- Setup library today: only Race and test-day stints auto-analyze and only the newest 200 reports load, so "every setup run" is not guaranteed.
- `.sto` payloads are encrypted (decision log, 2026-08-18). No decoder exists.
- Mini PC: N100 4 cores, 16 GB (8.6 GB available), swap 975 MB and full, 361 GB of 467 GB NVMe free, Frigate takes a steady share, application ports 8200 to 8299.
- NAS shares: `apps`, `Backups`, `Common`, `Gabriella`, `Joshua`, `Joshua_Work`.
- Dev PC (DESKTOP-JOSHUA): Node 24.19, pnpm, .NET, Visual Studio C++ tools, WSL Ubuntu (never start Docker Desktop). rustup installed 2026-10-02 at `%USERPROFILE%\.cargo\bin` (may not be on PATH in existing shells). The only `python` on PATH is the Store alias; use `C:\Users\joshu\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`.
- Codex CLI is driven through `node "C:/Users/joshu/Documents/Codex/2026-09-05/upda/outputs/orchestrate/driver/orchestrate.mjs"`.

## Orchestrator's assumptions (not Joshua's words)

- The old C# application is reference material and is deleted as each piece is replaced. The Python backend stays as worker and parity oracle until the Rust port passes.
- Development continues on `main`, in new top-level directories. Nothing is pushed to GitHub unless Joshua asks.
- The investigation is designed for desktop, rig and tablet; the phone must fully support the live page and the library, and the investigation as far as the screen allows.
- "No impact on simulator performance" is proven by agent-run solo sessions on the sim PC, with and without the collector, against agreed budgets.
- Existing `.ircr2` live-replay capture is not needed by v1 (Replay is parked) and should not add on-track disk work unless it is nearly free.
