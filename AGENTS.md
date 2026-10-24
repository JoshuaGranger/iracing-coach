# iRacing Coach contributor instructions

iRacing Coach is being rebuilt (decided 2026-10-02): a Rust collector and live overlay on the sim PC, a Rust service with the Python analysis worker in a Docker container on the mini PC, raw recordings on the NAS, and a TypeScript web UI for desktop, rig screen, tablet and phone. The authority is `docs/08-rewrite/owner-decisions-2026-10-02.md` as refined by `docs/08-rewrite/prove/verdicts.md`; the design is `docs/08-rewrite/architecture.md`; the plan is `docs/08-rewrite/workstreams.md`. They supersede older documents where they conflict.

## Product north star

Joshua's direction (September 22, 2026): **"I would rather an app that has 5 features that win me more races than an app that has 1000 features, graphics, and charts that don't help me win any more races."**

Capability may grow; interface complexity may not. Before adding a screen, chart, menu or button, name the racing decision it improves, the action it enables, and how its benefit can be checked in a later recording; remove or consolidate what fails that test. Use restrained presentation and concise racing language; no agentic prose or generic assurances. Design method: `docs/01-product/companion-design-principles.md` and `docs/01-product/visual-companion-redesign-2026-09-22.md`.

Keep the investigation on one well-planned screen: track location, exact comparison and run context, and critical telemetry visible together. Never hide driving inputs in accordions or replace the synchronized chart/map cursor with a slider. Pointer movement never causes a request. Comparable green exposure and supported slip evidence matter; copying a faster lap's inputs is not a coaching objective. Every chart makes comparison membership, units and selection marks understandable without an essay. Distinguish an observed run trend from a screened fit; never extend a screened line into unsupported observations. Detected sections are not official turn numbers.

## Where things live

| Path | What |
|---|---|
| `collector/`, `crates/coach-live/`, `crates/coach-ibt/` | sim-PC collector, live engine, IBT and session-info parsing |
| `server/`, `crates/coach-views/`, `crates/coach-wire/` | mini-PC service, view projection, generated wire types |
| `iracing-coach/` | Python analysis: the v1 production worker and the permanent parity oracle |
| `web/` | TypeScript SPA |
| `deploy/mini-pc/` | Dockerfile, Compose file, `app.env` (mini-pc skill conventions) |
| `docs/08-rewrite/contracts/` | interfaces between workstreams; change them only by the process in its README |

The retired desktop app (`companion-app/`) was deleted in rewrite wave 4 (2026-10-03); its source lives in Git history (last present at `3ff5e87`).

Work in your workstream's owned paths only (`docs/08-rewrite/workstreams.md`).

## Verify before delivery

Until the scaffold lands, run:

```powershell
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\tools\verify-repository.ps1 -PythonPath C:\Users\joshu\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe
```

After it lands, run `tools\check.ps1` (adds Rust, web, and wire-drift checks) and, for server-side code, `tools\check.ps1 -Linux` (WSL Ubuntu). The only `python` on PATH is the Windows Store alias; pass the interpreter above explicitly. Never start Docker Desktop; images are built on the mini PC by the release script.

## Hard boundaries

- Never modify or delete raw iRacing telemetry, replay, setup or purchased source files. The collector reads sources with sharing. Owner-authorized NAS sync (2026-10-24) may create new, hash/version-named files only beneath `Documents\iRacing\setups\<car>\Coach Synced`; it never replaces or deletes an existing source. Sim deletion never propagates to the NAS library; deliberate share-folder deletion is remembered (contracts/nas-sync.md).
- No Garage61, AI or other credentials in the repository, settings files, arguments, logs or fixtures. Secrets reach the mini PC on standard input through `g-house-app set`.
- Deterministic analysis works without the internet, Garage61 or AI.
- The Python backend is authoritative for telemetry math, evidence labels, damage and repair screening, and setup safeguards. The server and UI pass its results through; they do not recompute them. A Rust stage replaces a Python stage only with byte-identical output on the parity corpus; an unexplained numeric difference is a failure, not a tolerance. A backend fix gets regression tests and regenerated contracts and fixtures.
- Do not display exact target telemetry, directional groove labels, damage onset or repair-only time unless backend evidence supports the claim. Evidence labels and screening semantics do not change in the port.
- Preserve unknown optional JSON fields; fail clearly on missing required fields or incompatible contract versions.
- `config/theme.dark.json` is the canonical UI token source; generated CSS and TS come from it.
- No bulk file transfer while the car is on track. Sim-PC impact is proven on the rig against the G4 gate, not assumed.
- Mini PC: follow the mini-pc skill (never delete data, never restart Docker or reboot, change containers only through the managed tools, keep `mem_limit`, leave the `g-house` stack alone). D14 is standing permission to release the `iracing-coach` stack.
- Sim PC: use the remote tools, never log off or reboot, work on copies of real data, never join a session with other people under automation.
- Private race data never enters the repository; sanitized fixtures under `test-data/` are the only race-like data in Git.
