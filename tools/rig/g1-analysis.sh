#!/usr/bin/env bash
# Gate G1: analysis on the N100 inside the released iracing-coach container
# (docs/08-rewrite/workstreams.md, Gates; record: docs/08-rewrite/acceptance/g1-analysis.md).
#
# Run from the dev PC (Git bash). It never changes the stack: the recording reaches the server
# through the upload API (tools/rig/g1_upload.py, collector-protocol.md section 4); run 1 is the
# server's own ingest, archive, analysis and projection; run 2 is a second cold analysis with
# the same image and worker environment in the container's /tmp (tools/rig/g1_cold_run.py),
# because the API has no way to clear the worker's analysis cache. Home Assistant and Frigate
# are sampled once per second from 60 s before run 1 to 30 s after run 2 (P1 method) by
# tools/rig/g1_sampler.sh in the publisher's home on the mini PC; the P1 abort rule cancels a
# run. Optional OVERLAP_RACE, PAIR_A, PAIR_B add a window in which uploads overlap an analysis
# and a two-recording reconnect group is analysed.
#
#   RECORDING=... ORACLE=<capture dir> tools/rig/g1-analysis.sh OUT_DIR
set -euo pipefail
# Container paths (/data/..., /tmp/...) go to the Windows Python as arguments: stop Git bash from
# rewriting them into Windows paths.
export MSYS_NO_PATHCONV=1

here=$(cd "$(dirname "$0")" && pwd -W)
repo=$(cd "$here/../.." && pwd -W)
SERVER=${SERVER:-http://192.168.1.81:8210}
SSH=${SSH:-g-house-pc}
CONTAINER=${CONTAINER:-iracing-coach-app-1}
PY=${PY:-C:/Users/joshu/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe}
RECORDING=${RECORDING:?set RECORDING to the 203 MB New Hampshire race}
ORACLE=${ORACLE:?set ORACLE to the Windows 3.12 capture-worker capture of RECORDING}
OVERLAP_RACE=${OVERLAP_RACE:-}
PAIR_A=${PAIR_A:-}
PAIR_B=${PAIR_B:-}
out=${1:?usage: g1-analysis.sh OUT_DIR}
mkdir -p "$out"
remote=coach-g1/$(date -u +%Y%m%dT%H%M%SZ)
aborted=""

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$out/run.log" >&2; }
mark() { ssh "$SSH" "echo $1 \$(date +%s.%N) >> $remote/markers.log"; log "mark $1"; }
json() { "$PY" -c "import json,sys; d=json.load(open(sys.argv[1], encoding='utf-8')); print(eval(sys.argv[2], {'d': d}), end='')" "$@"; }
max_job() { curl -sf "$SERVER/api/v1/jobs?limit=1" | "$PY" -c "import json,sys; j=json.load(sys.stdin)['jobs']; print(j[0]['job_id'] if j else 0, end='')"; }
abort_reason() { ssh "$SSH" "cat $remote/abort 2>/dev/null" || true; }
oom_kills() { ssh "$SSH" "grep oom_kill \$(cat $remote/cgroup.path)/memory.events"; }

# ---------------------------------------------------------------- preflight
sha=$(sha256sum "$RECORDING" | cut -d' ' -f1)
name=$(basename "$RECORDING")
mtime=$("$PY" -c "import os,sys; print(int(os.stat(sys.argv[1]).st_mtime), end='')" "$RECORDING")
curl -sf "$SERVER/api/v1/status" > "$out/status-before.json"
[ "$(json "$out/status-before.json" "d['nas']['state']")" = ok ] || { log "nas not ok"; exit 2; }
[ "$(json "$out/status-before.json" "d['queue']['analysis'] + d['queue']['light']")" = 0 ] || { log "queue not empty"; exit 2; }
head_code=$(curl -s -o /dev/null -w '%{http_code}' -I "$SERVER/api/v1/uploads/$sha")
[ "$head_code" = 404 ] || { log "the server already holds $sha (HEAD $head_code): run 1 would not be cold"; exit 2; }
log "recording $name sha256 $sha mtime $mtime; image $(ssh "$SSH" "docker inspect -f '{{.Config.Image}}' $CONTAINER")"

ssh "$SSH" "mkdir -p $remote && cat > $remote/sampler.sh" < "$here/g1_sampler.sh"
ssh "$SSH" "nohup bash $remote/sampler.sh $remote $CONTAINER > $remote/sampler.log 2>&1 < /dev/null &"
mark sampler_start
oom_kills > "$out/oom-before.txt"

# ---------------------------------------------------------------- baseline (60 s)
sleep 58
p95=$(ssh "$SSH" "cat $remote/ha.csv" | "$PY" -c "
import sys
t = sorted(float(l.split(',')[1]) for l in sys.stdin if l.count(',') == 2 and l.strip().endswith(',200'))
print(t[round(0.95 * (len(t) - 1))], end='')")
ssh "$SSH" "echo $p95 > $remote/limit"
log "baseline HA p95 ${p95}s; abort rule armed (three responses over $(awk -v p="$p95" 'BEGIN{print 2*p}') s or any failure)"
sleep 2

# ---------------------------------------------------------------- run 1: the server's own path
after=$(max_job)
mark run1_start
"$PY" "$here/g1_upload.py" --server "$SERVER" --file "$RECORDING" > "$out/run1-upload.json"
mark run1_uploaded
log "run 1 upload: $(cat "$out/run1-upload.json")"
set +e
"$PY" "$here/g1.py" wait --server "$SERVER" --after "$after" --need analyze project archive --remote-dir "$remote" > "$out/run1-jobs.json"
status=$?
set -e
mark run1_end
[ $status -eq 0 ] || { aborted="run1 (wait exit $status): $(abort_reason)"; log "ABORT $aborted"; }

if [ -z "$aborted" ]; then
  # ------------------------------------------------------------ gap (15 s), then run 2: cold in /tmp
  sleep 15
  if ssh "$SSH" "docker exec $CONTAINER test -f /data/spool/ready/$sha"; then
    source_path=/data/spool/ready/$sha
  else
    source_path=/nas/apps/iracing-coach/raw/${sha:0:2}/$sha.ibt
  fi
  selector=$(json "$ORACLE/capture.json" "d['server_group_key']")
  ssh "$SSH" "docker exec -i $CONTAINER python3.12 - prepare '$source_path' '$name' $mtime" < "$here/g1_cold_run.py" > "$out/run2-prepare.json"
  log "run 2 prepared from $source_path: $(cat "$out/run2-prepare.json")"
  mark run2_start
  ssh "$SSH" "docker exec -i $CONTAINER python3.12 - run '$selector'" < "$here/g1_cold_run.py" > "$out/run2-run.json" &
  run2=$!
  while kill -0 $run2 2>/dev/null; do
    reason=$(abort_reason)
    if [ -n "$reason" ]; then
      ssh "$SSH" "docker exec $CONTAINER sh -c 'kill \$(cat /tmp/g1-cold/worker.pid)'" || true
      aborted="run2: $reason"
      log "ABORT $aborted"
      break
    fi
    sleep 1
  done
  wait $run2 || true
  mark run2_end
  sleep 30
fi

# ---------------------------------------------------------------- run-1 archive, read-only, before more analyses join it
customer=$(json "$out/run1-jobs.json" "[j for j in d['jobs'] if j['kind'] == 'analyze'][-1]['customer_id']") || true
analyze_job=$(json "$out/run1-jobs.json" "[j for j in d['jobs'] if j['kind'] == 'analyze'][-1]['job_id']") || true
if [ -n "${customer:-}" ]; then
  mkdir -p "$out/run1-archive"
  ssh "$SSH" "docker exec $CONTAINER tar -C /data/py/$customer/archive -cf - ." | tar -xf - -C "$out/run1-archive"
fi
if [ -s "$out/run2-run.json" ]; then
  mkdir -p "$out/run2-archive"
  ssh "$SSH" "docker exec $CONTAINER tar -C /tmp/g1-cold/archive -cf - ." | tar -xf - -C "$out/run2-archive"
fi
ssh "$SSH" "docker exec -i $CONTAINER python3.12 - clean" < "$here/g1_cold_run.py" > "$out/run2-clean.json" || true

# ---------------------------------------------------------------- optional overlap window
if [ -z "$aborted" ] && [ -n "$OVERLAP_RACE" ]; then
  after=$(max_job)
  mark overlap_start
  "$PY" "$here/g1_upload.py" --server "$SERVER" --file "$OVERLAP_RACE" > "$out/overlap-upload-race.json"
  for _ in $(seq 1 120); do
    curl -sf "$SERVER/api/v1/jobs?state=running&limit=50" | grep -q '"kind":"analyze"' && break
    sleep 1
  done
  mark overlap_analysis_running
  for f in "$PAIR_A" "$PAIR_B"; do
    [ -n "$f" ] && "$PY" "$here/g1_upload.py" --server "$SERVER" --file "$f" >> "$out/overlap-upload-pair.json"
  done
  mark overlap_uploads_done
  set +e
  "$PY" "$here/g1.py" wait --server "$SERVER" --after "$after" --need analyze project --remote-dir "$remote" --timeout 1800 > "$out/overlap-jobs.json"
  status=$?
  set -e
  mark overlap_end
  [ $status -eq 0 ] || { aborted="overlap (wait exit $status): $(abort_reason)"; log "ABORT $aborted"; }
  sleep 30
fi

# ---------------------------------------------------------------- stop, collect, report
oom_kills > "$out/oom-after.txt"
ssh "$SSH" "touch $remote/stop"
sleep 3
mkdir -p "$out/samples"
ssh "$SSH" "tar -C $remote -cf - ." | tar -xf - -C "$out/samples"
curl -sf "$SERVER/api/v1/status" > "$out/status-after.json" || true
"$PY" "$here/g1.py" report --data "$out/samples" --out "$out/report.json" > /dev/null

# ---------------------------------------------------------------- parity against the Windows 3.12 oracle
# Raw mode (the harness's gate: roots, separators, newlines and the timestamp allowlist), then
# strict mode on analysis.json alone (roots, separators and newlines only) against the oracle's
# kept archive (capture-worker --keep-base).
oracle_archive=$(json "$ORACLE/capture.json" "d['roots']['<archive_root>']")
oracle_iracing=$(json "$ORACLE/capture.json" "d['roots']['<iracing_root>']")
oracle_json=$(find "$(cygpath -u "$oracle_archive")/reports" -name analysis.json | head -1)
compare() { # label archive_root iracing_root analysis_id
  "$PY" "$here/g1.py" capture --archive "$out/$1-archive" --out "$out/cap-$1" \
    --archive-root "$2" --iracing-root "$3" --analysis-id "$4"
  "$PY" "$repo/tools/parity/analysis_parity.py" compare --raw --cross-platform "$ORACLE" "$out/cap-$1" > "$out/parity-$1.txt" || true
  "$PY" "$here/g1.py" strict "$(find "$out/$1-archive/reports" -name analysis.json | head -1)" \
    --left-archive "$2" --left-iracing "$3" \
    "$oracle_json" --right-archive "$oracle_archive" --right-iracing "$oracle_iracing" > "$out/strict-$1.json" || true
}
if [ -d "$out/run1-archive" ]; then
  compare run1 "/data/py/$customer/archive" "/data/py/$customer/jobs/$analyze_job" \
    "$(json "$out/run1-jobs.json" "[j for j in d['jobs'] if j['kind'] == 'analyze'][-1]['result']['analysis_id']")"
fi
if [ -d "$out/run2-archive" ]; then
  compare run2 /tmp/g1-cold/archive /tmp/g1-cold/root "$(json "$out/run2-run.json" "d['analysis_id']")"
fi
log "done; aborted='${aborted}'; report $out/report.json"
[ -z "$aborted" ]
