# Analysis parity

The Python backend is the oracle (owner decision D2). A change to it, a Rust stage that replaces one of its stages, or a move to another platform passes only when every compared file is byte-identical. The rules are in `docs/08-rewrite/contracts/worker-interface.md` section 8.

| Tool | Use |
|---|---|
| `analysis_parity.py capture` | One cold `analyze_race_workflow` run in a fresh process; parsed outputs, persisted bytes and timings |
| `analysis_parity.py capture-worker` | One analysis driven through `mcp_server.py` as `coach-server` drives it (section 2 environment, section 3 job roots, discovered selector) |
| `analysis_parity.py corpus` | The port gate in one command: decoded-matrix golden, oracle and native captures compared raw and parsed for every fixture and every `COACH_PRIVATE_CORPUS` recording, and the four worker cases; `report.json` in `--out` |
| `analysis_parity.py compare` | Parsed comparison (default, diagnostic) or `--raw` (persisted bytes, the gate); `--cross-platform` for the Windows oracle against Linux |
| `worker_corpus.py` | Generated recordings for the worker cases: `synthetic`, `reconnect` (two files, 20 overlapping samples), `local-file` (no server ids, no start time); `restaged` is the synthetic race copied instead of linked |
| `emit_ibt_golden.py` | Decoded-matrix golden for the decode stage (port phase 1); `coach-analysis golden` prints the same fields from the native decode |
| `measure_worker.py` | Wall time and peak memory of one worker analysis with and without native stages, and of the decoder alone |
| `worker_trace.py` | Runs `mcp_server.py` unchanged as `__main__` and appends one line per `native_stages.load_telemetry` call to a trace; `capture-worker` starts every worker through it and keeps the calls in `capture.json` |
| `test_analysis_parity.py` | Tests of the gate itself: rejection matching, raw SQLite dumps, the worker native-decode check |

Raw mode compares SQLite files as row dumps (`raw/<name>.dump.json`) with only the raw allowlist applied to each value: an ISO instant in a `*_at` column, a `*_ms` / `elapsed_seconds` number, an `analysis_sha256` hash, and the allowlist's text rules inside other strings (`created_at="corrupt"` stays visible). Parsed mode keeps its key-based normalisation as a diagnostic. Captures are format 3; older captures lack the raw dumps and the worker trace.

Every capture sets `IRACING_COACH_RAW_ARCHIVE=external` (`--raw-archive copy` for the legacy raw copy) and passes `--native-stages` as `IRACING_COACH_NATIVE_STAGES`; a non-empty list needs the `coach-analysis` executable (`--native-bin`, else `IRACING_COACH_NATIVE_BIN`, else `PATH`), which the harness passes on as `IRACING_COACH_NATIVE_BIN`. `capture` pins the analyzer digest unless `--unpinned-identity`; `capture-worker` never pins it. `compare` refuses captures whose digest pinning or raw-archive setting differ.

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

Keep capture and scratch paths short on Windows: the archive handles paths beyond `MAX_PATH`, but the harness's own staging and the recordings it reads from a job root do not.

## Windows against Linux

Strict raw comparison of a Windows 3.12 capture with a Linux 3.12 capture differs in exactly two ways, and `--cross-platform` folds only these:

- the analysis-cache envelope ends in CRLF on Windows (`workflow._write_json_cache` writes text); every other persisted file is written as bytes;
- paths under the archive and job roots use `\` on Windows and `/` on Linux after the root token.

Run on 2026-10-02 (Windows 3.12.14, WSL Ubuntu 3.12.3): `synthetic-race.ibt` in process (pinned and unpinned) and the four worker cases, and the private corpus (New Hampshire race 203 MB `582b11e2`, Iowa race 146 MB, a New Hampshire practice and an Offline Testing session) in process and through the worker: every parsed and raw comparison identical, and every `analysis_id` equal between the platforms.

## Port gate (native stages)

```powershell
$env:CARGO_TARGET_DIR = "$PWD\target\port"
cargo build --release -p coach-analysis
$env:COACH_PRIVATE_CORPUS = 'C:\path\to\copies\of\recordings'   # optional; os.pathsep-separated dirs or files
& $py tools\parity\analysis_parity.py corpus --native-stages decode --native-bin target\port\release\coach-analysis.exe --out target\port\gate
```

Exit 0 only when every recording's decode golden (native rate and 20 Hz, all channels) and analysis capture pair (raw and parsed) are identical, failures included (the captures must fail with the same exception line; the golden's native rejection must be a recorded `coach-analysis golden` rejection with the oracle's exception class, its own messages verbatim and a truncation's region and byte range, never a crash or an unrelated failure), the native capture's timings show `native_stages.load_telemetry` ran, and the four worker cases are identical with equal `analysis_id`, no native decode call on the oracle side and one successful traced call per recording on the native side. Captures of identical recordings are deleted unless `--keep`. On Windows keep `--out` short (the archive paths inside a capture add about 160 characters). Results per recording: `docs/08-rewrite/port/phase1-decode.md`.
