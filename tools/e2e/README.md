# End-to-end tools (wave 3)

Scripts the integration runs use against a running `coach-server` (a `--dev` server on the dev PC, or the released service on the mini PC). Records: `docs/08-rewrite/acceptance/e2e-dev-pc.md`, `e2e-mini-pc.md`. Python is the stdlib-only interpreter from `workstreams.md`; Playwright is resolved from `web/node_modules`.

| Script | What it does |
|---|---|
| `make_sdkfix.py` | Writes a synthetic SDK replay fixture for `coach-collector --replay`: 90 s on track (live tiles, on-track upload gate), pit road, a garage visit with four `CarSetup.UpdateCount` bumps and a name change after the exit (P5 two-moment rule), then off track and a sim exit. The driver's id and name are arguments; `--track-s` lengthens the on-track stretch (default 90 s). |
| `watch_upload.py` | Polls `HEAD /api/v1/uploads/{sha256}` and prints the offset and state timeline; can stop a collector process it is given once the server's durable offset reaches a byte count (restart-mid-upload check). |
| `spa_check.mjs` | Desktop 1280x720, tablet 1024x768, phone 390x844: Drivers landing, Sessions library against the API, the investigation views of one analysed session with a 5 s pointer sweep that must cause no request, Setups and a setup's values. Screenshots and `spa-check.json` go to `--out`. |
| `live_check.mjs` | While a collector streams: presence in the API, the Live page reaching Live at each viewport, tile texts, binary frames received. |
| `live_soak.mjs` | One replayed session as browsers see it (`--phase pre` without a collector, `--phase run` with one): Live indicator, first viewer starts the stream, frames/s, seq continuity and latency from the frame headers, a second viewer joining and leaving, the stream kept after the last viewer leaves until the sim exits, and the garage capture in Setups at three viewports. Presence timeline in `presence.jsonl`. |
| `time_to_visible.py` | Uploads one recording through `tools/rig/g1_upload.py`, then times verified to analysed and served (jobs, session state, view requests, NAS state). |

```powershell
$py = "C:\Users\joshu\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
& $py tools\e2e\make_sdkfix.py <scratch>\live.sdkfix --customer-id <id> --name "<name>"
node tools\e2e\spa_check.mjs --base http://127.0.0.1:8291 --out <scratch>\shots\dev --session 1
node tools\e2e\live_check.mjs --base http://127.0.0.1:8291 --out <scratch>\shots\live
node tools\e2e\live_soak.mjs --base http://192.168.1.81:8210 --out <scratch>\soak --phase run
& $py tools\e2e\time_to_visible.py --server http://192.168.1.81:8210 --file <copy of a recording>
```

Keep server data roots short on Windows (for example `%TEMP%\e2e-w3`): the Python worker writes `analysis-cache` temporary files whose full path exceeds `MAX_PATH` under a deep scratch folder.
