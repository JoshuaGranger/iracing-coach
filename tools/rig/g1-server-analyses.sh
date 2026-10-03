#!/usr/bin/env bash
# Re-request analyses through the API and measure them as the server runs them (the deferred
# sessions of acceptance/migration.md, step 4, once the native admission model admits them;
# acceptance/g1-native.md). One session at a time: POST /api/v1/sessions/{id}/analysis, then
# wait until that job is terminal (a deferred answer is recorded with its reason and the next
# session follows). Home Assistant, Frigate, the container's memory and the server's worker
# processes with their coach-analysis children are sampled by tools/rig/g1_sampler.sh (60 s
# baseline, P1 abort rule armed; an abort cancels the running job through the API). While a job
# runs, the worker's IRACING_COACH_NATIVE_* environment is read once from /proc inside the
# container: the evidence of the stages the server passed. Writes only what the API writes.
#
#   tools/rig/g1-server-analyses.sh OUT_DIR SESSION_ID [SESSION_ID ...]
set -euo pipefail
export MSYS_NO_PATHCONV=1

here=$(cd "$(dirname "$0")" && pwd -W)
SERVER=${SERVER:-http://192.168.1.81:8210}
SSH=${SSH:-g-house-pc}
CONTAINER=${CONTAINER:-iracing-coach-app-1}
PY=${PY:-C:/Users/joshu/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe}
out=${1:?usage: g1-server-analyses.sh OUT_DIR SESSION_ID...}
shift
[ $# -ge 1 ] || { echo "name at least one session" >&2; exit 2; }
mkdir -p "$out"
remote=coach-g1/server-$(date -u +%Y%m%dT%H%M%SZ)
aborted=""

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$out/run.log" >&2; }
mark() { ssh "$SSH" "echo $1 \$(date +%s.%N) >> $remote/markers.log"; log "mark $1"; }
abort_reason() { ssh "$SSH" "cat $remote/abort 2>/dev/null" || true; }
field() { "$PY" -c "import json,sys; d=json.load(open(sys.argv[1], encoding='utf-8')); print(eval(sys.argv[2], {'d': d}), end='')" "$@"; }
# The NATIVE variables of every worker process in the container (mcp_server.py), one line each;
# the probe's own shell names mcp_server.py too but has no IRACING_COACH_ variable, so it drops out.
worker_env() {
  { ssh "$SSH" "docker exec $CONTAINER sh -c 'for p in /proc/[0-9]*; do tr \"\\0\" \" \" < \$p/cmdline 2>/dev/null | grep -q mcp_server.py || continue; echo pid=\${p#/proc/} \$(tr \"\\0\" \"\\n\" < \$p/environ | grep ^IRACING_COACH_NATIVE_ | tr \"\\n\" \" \"); done'" || true; } \
    | grep IRACING_COACH_NATIVE_STAGES || true
}

curl -sf "$SERVER/api/v1/status" > "$out/status-before.json"
[ "$(field "$out/status-before.json" "d['nas']['state']")" = ok ] || { log "nas not ok"; exit 2; }
[ "$(field "$out/status-before.json" "d['queue']['analysis'] + d['queue']['light']")" = 0 ] || { log "queue not empty"; exit 2; }
ssh "$SSH" "mkdir -p $remote && cat > $remote/sampler.sh" < "$here/g1_sampler.sh"
ssh "$SSH" "nohup bash $remote/sampler.sh $remote $CONTAINER > $remote/sampler.log 2>&1 < /dev/null &"
mark sampler_start
ssh "$SSH" "grep oom_kill \$(cat $remote/cgroup.path)/memory.events" > "$out/oom-before.txt"
sleep 58
p95=$(ssh "$SSH" "cat $remote/ha.csv" | "$PY" -c "
import sys
t = sorted(float(l.split(',')[1]) for l in sys.stdin if l.count(',') == 2 and l.strip().endswith(',200'))
print(t[round(0.95 * (len(t) - 1))], end='')")
ssh "$SSH" "echo $p95 > $remote/limit"
log "baseline HA p95 ${p95}s; abort rule armed"
sleep 2

for session in "$@"; do
  [ -z "$aborted" ] || break
  label=s$session
  curl -sf "$SERVER/api/v1/sessions/$session" > "$out/$label-session-before.json"
  mark "${label}_start"
  code=$(curl -s -o "$out/$label-post.json" -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{}' \
    "$SERVER/api/v1/sessions/$session/analysis")
  log "$label POST $code $(cat "$out/$label-post.json")"
  job=$(field "$out/$label-post.json" "d.get('job_id', '')")
  state=$(field "$out/$label-post.json" "d.get('state', '')")
  seen_env=""
  while [ -n "$job" ] && [ "$state" != deferred ]; do
    curl -sf "$SERVER/api/v1/jobs/$job" > "$out/$label-job.json"
    state=$(field "$out/$label-job.json" "d['state']")
    case "$state" in succeeded|failed|cancelled|deferred) break ;; esac
    if [ -z "$seen_env" ] && [ "$state" = running ]; then
      seen_env=$(worker_env)
      [ -n "$seen_env" ] && { printf '%s\n' "$seen_env" > "$out/$label-worker-env.txt"; log "$label worker env: $seen_env"; }
    fi
    reason=$(abort_reason)
    if [ -n "$reason" ]; then
      curl -s -X POST -d '{}' "$SERVER/api/v1/jobs/$job/cancel" > /dev/null || true
      aborted="$label: $reason"
      log "ABORT $aborted"
      break
    fi
    sleep 0.5
  done
  mark "${label}_end"
  [ -n "$job" ] && curl -sf "$SERVER/api/v1/jobs/$job" > "$out/$label-job.json"
  log "$label job $job: $(field "$out/$label-job.json" "{k: d.get(k) for k in ('state', 'error_code', 'error_message', 'started_at', 'finished_at', 'attempts')}" 2>/dev/null || cat "$out/$label-post.json")"
  sleep 20
  curl -sf "$SERVER/api/v1/sessions/$session" > "$out/$label-session-after.json"
done

ssh "$SSH" "grep oom_kill \$(cat $remote/cgroup.path)/memory.events" > "$out/oom-after.txt"
ssh "$SSH" "touch $remote/stop"
sleep 3
mkdir -p "$out/samples"
ssh "$SSH" "tar -C $remote -cf - ." | tar -xf - -C "$out/samples"
curl -sf "$SERVER/api/v1/status" > "$out/status-after.json" || true
"$PY" "$here/g1.py" report --data "$out/samples" --out "$out/report.json" > /dev/null
log "done; aborted='${aborted}'; report $out/report.json"
[ -z "$aborted" ]
