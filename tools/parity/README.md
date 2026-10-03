# Analysis parity

The Python backend is the oracle (owner decision D2). A change to it, a Rust stage that replaces one of its stages, or a move to another platform passes only when every compared file is byte-identical. The rules are in `docs/08-rewrite/contracts/worker-interface.md` section 8.

| Tool | Use |
|---|---|
| `analysis_parity.py capture` | One cold `analyze_race_workflow` run in a fresh process; parsed outputs, persisted bytes and timings |
| `analysis_parity.py capture-worker` | One analysis driven through `mcp_server.py` as `coach-server` drives it (section 2 environment, section 3 job roots, discovered selector) |
| `analysis_parity.py compare` | Parsed comparison (default, diagnostic) or `--raw` (persisted bytes, the gate); `--cross-platform` for the Windows oracle against Linux |
| `worker_corpus.py` | Generated recordings for the worker cases: `synthetic`, `reconnect` (two files, 20 overlapping samples), `local-file` (no server ids, no start time); `restaged` is the synthetic race copied instead of linked |
| `emit_ibt_golden.py` | Decoded-matrix golden for the decode stage (port phase 1) |

Every capture sets `IRACING_COACH_RAW_ARCHIVE=external` (`--raw-archive copy` for the legacy raw copy) and passes `--native-stages` as `IRACING_COACH_NATIVE_STAGES`; a non-empty list needs `coach_native`. `capture` pins the analyzer digest unless `--unpinned-identity`; `capture-worker` never pins it. `compare` refuses captures whose digest pinning or raw-archive setting differ.

```powershell
$py = 'C:\Users\joshu\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $py tools\parity\analysis_parity.py capture --source RACE.ibt --out cap\win --archive SCRATCH\archive --runs 0
& $py tools\parity\analysis_parity.py capture-worker --case reconnect --out cap\win-reconnect
```

```bash
python3.12 tools/parity/analysis_parity.py capture --source RACE.ibt --out cap/linux --archive ~/scratch/archive --runs 0
python3.12 tools/parity/analysis_parity.py capture-worker --case reconnect --out cap/linux-reconnect
python3.12 tools/parity/analysis_parity.py compare --raw --cross-platform cap/win cap/linux
```

Keep capture and scratch paths short on Windows: report paths under a long root pass `MAX_PATH`.

## Windows against Linux

Strict raw comparison of a Windows 3.12 capture with a Linux 3.12 capture differs in exactly two ways, and `--cross-platform` folds only these:

- the analysis-cache envelope ends in CRLF on Windows (`workflow._write_json_cache` writes text); every other persisted file is written as bytes;
- paths under the archive and job roots use `\` on Windows and `/` on Linux after the root token.

Run on 2026-10-02 (Windows 3.12.14, WSL Ubuntu 3.12.3): `synthetic-race.ibt` in process (pinned and unpinned) and the four worker cases, and the private corpus (New Hampshire race 203 MB `582b11e2`, Iowa race 146 MB, a New Hampshire practice and an Offline Testing session) in process and through the worker: every parsed and raw comparison identical, and every `analysis_id` equal between the platforms.
