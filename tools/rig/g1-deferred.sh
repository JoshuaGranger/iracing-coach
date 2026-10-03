#!/usr/bin/env bash
# The analyses the memory ceiling deferred (acceptance/migration.md, step 4), run cold inside the
# released iracing-coach container with the image's worker command (native stages since the
# native release; acceptance/g1-native.md). The server cannot run them: its admission estimate
# (server/src/jobs.rs analysis_estimate, worker-interface.md section 6) still defers them, and
# the API keeps a deferred job deferred. So each runs as G1's cold runs do (tools/rig/g1_cold_run.py,
# fresh archive, job root and worker home under /data/run/g1-cold; nothing the server owns is
# written), from the server's own NAS copies with their committed names and mtimes, traced, one
# after another, with Home Assistant, Frigate and the container's memory sampled
# (tools/rig/g1_sampler.sh, P1 abort rule armed after a 60 s baseline). Never changes the stack.
#
#   tools/rig/g1-deferred.sh OUT_DIR SESSION_ID [SESSION_ID ...]
set -euo pipefail
export MSYS_NO_PATHCONV=1

here=$(cd "$(dirname "$0")" && pwd -W)
SERVER=${SERVER:-http://192.168.1.81:8210}
SSH=${SSH:-g-house-pc}
CONTAINER=${CONTAINER:-iracing-coach-app-1}
PY=${PY:-C:/Users/joshu/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe}
COLD_BASE=/data/run/g1-cold
out=${1:?usage: g1-deferred.sh OUT_DIR SESSION_ID...}
shift
[ $# -ge 1 ] || { echo "name at least one session" >&2; exit 2; }
mkdir -p "$out"
remote=coach-g1/deferred-$(date -u +%Y%m%dT%H%M%SZ)
aborted=""

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$out/run.log" >&2; }
mark() { ssh "$SSH" "echo $1 \$(date +%s.%N) >> $remote/markers.log"; log "mark $1"; }
abort_reason() { ssh "$SSH" "cat $remote/abort 2>/dev/null" || true; }

curl -sf "$SERVER/api/v1/status" > "$out/status-before.json"
ssh "$SSH" "docker exec $CONTAINER test ! -e $COLD_BASE" || { log "$COLD_BASE exists in the container"; exit 2; }
ssh "$SSH" "mkdir -p $remote && cat > $remote/sampler.sh" < "$here/g1_sampler.sh"
ssh "$SSH" "nohup bash $remote/sampler.sh $remote $CONTAINER > $remote/sampler.log 2>&1 < /dev/null &"
mark sampler_start
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
  curl -sf "$SERVER/api/v1/sessions/$session" > "$out/$label-session.json"
  # SOURCE NAME MTIME per recording (NAS copy, original name, the copy's mtime), then the selector.
  args=$("$PY" - "$out/$label-session.json" <<'EOF'
import json, shlex, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
parts = []
for r in d["recordings"]:
    parts += [f"/nas/apps/iracing-coach/raw/{r['sha256'][:2]}/{r['sha256']}.ibt", r["file_name"]]
print(" ".join(shlex.quote(p) for p in parts), end="")
EOF
)
  selector=$("$PY" -c "import json,sys; print(json.load(open(sys.argv[1], encoding='utf-8'))['session']['group_key'], end='')" "$out/$label-session.json")
  # The copy's mtime, read in the container (the NAS keeps what the archiver set).
  triples=$(ssh "$SSH" "docker exec $CONTAINER python3.12 -c 'import os,shlex,sys; a=sys.argv[1:]; print(\" \".join(shlex.quote(x) for i in range(0,len(a),2) for x in (a[i],a[i+1],str(int(os.stat(a[i]).st_mtime)))))' $args")
  ssh "$SSH" "docker exec -i $CONTAINER python3.12 - prepare $triples" < "$here/g1_cold_run.py" > "$out/$label-prepare.json"
  log "$label prepared: $(cat "$out/$label-prepare.json"); selector $selector"
  sleep 10
  mark "${label}_start"
  ssh "$SSH" "docker exec -i $CONTAINER python3.12 - run '$selector' --trace" < "$here/g1_cold_run.py" > "$out/$label-run.json" &
  pid=$!
  while kill -0 $pid 2>/dev/null; do
    reason=$(abort_reason)
    if [ -n "$reason" ]; then
      ssh "$SSH" "docker exec $CONTAINER sh -c 'kill \$(cat $COLD_BASE/worker.pid)'" || true
      aborted="$label: $reason"
      log "ABORT $aborted"
      break
    fi
    sleep 1
  done
  wait $pid || true
  mark "${label}_end"
  ssh "$SSH" "docker exec -i $CONTAINER python3.12 - clean" < "$here/g1_cold_run.py" > "$out/$label-clean.json" || true
  log "$label: $("$PY" -c "import json,sys; d=json.load(open(sys.argv[1])); print({k: d.get(k) for k in ('exit_code', 'wall_s', 'is_error', 'analysis_id', 'vm_hwm_kib_sampled', 'tree_rss_sum_max_kib', 'cgroup_memory_current_max_bytes')})" "$out/$label-run.json")"
  sleep 20
done

ssh "$SSH" "touch $remote/stop"
sleep 3
mkdir -p "$out/samples"
ssh "$SSH" "tar -C $remote -cf - ." | tar -xf - -C "$out/samples"
curl -sf "$SERVER/api/v1/status" > "$out/status-after.json" || true
"$PY" "$here/g1.py" report --data "$out/samples" --out "$out/report.json" > /dev/null
log "done; aborted='${aborted}'; report $out/report.json"
[ -z "$aborted" ]
