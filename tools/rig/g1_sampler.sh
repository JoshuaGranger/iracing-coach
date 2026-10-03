#!/usr/bin/env bash
# Gate G1 sampler, run on the mini PC by tools/rig/g1-analysis.sh (never by hand on a stack).
# Reads only: Home Assistant and Frigate over HTTP (P1 method), the iracing-coach container's
# cgroup memory files, and /proc of the worker processes inside that cgroup. Writes only under
# the directory given as $1. Stops when $1/stop exists.
#
#   ha.csv        unix_time,time_total_s,http_code              (1 per second)
#   frigate.jsonl {"t": unix_time, "stats": <api/stats>}        (1 per second)
#   memory.csv    unix_time,memory.current,memory.peak          (4 per second)
#   worker.csv    unix_time,pid,vm_hwm_kib,vm_rss_kib,cmdline   (4 per second, worker processes and
#                 their coach-analysis children: decode, merge, kernels)
#   abort         written once when the P1 abort rule fires (the orchestrator cancels the run)
set -u
out=$1
container=${2:-iracing-coach-app-1}
mkdir -p "$out"
id=$(docker inspect -f '{{.Id}}' "$container")
cg=/sys/fs/cgroup/system.slice/docker-$id.scope
[ -r "$cg/memory.current" ] || { echo "no cgroup at $cg" >&2; exit 2; }
echo "$cg" > "$out/cgroup.path"

fast() {
  while [ ! -e "$out/stop" ]; do
    now=$(date +%s.%N)
    printf '%s,%s,%s\n' "$now" "$(cat "$cg/memory.current")" "$(cat "$cg/memory.peak" 2>/dev/null || echo)" >> "$out/memory.csv"
    for pid in $(cat "$cg/cgroup.procs" 2>/dev/null); do
      cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null) || continue
      case "$cmd" in
        *mcp_server.py*|*worker_trace.py*|*coach-analysis\ *)
          hwm=$(awk '/^VmHWM:/ {print $2}' "/proc/$pid/status" 2>/dev/null)
          rss=$(awk '/^VmRSS:/ {print $2}' "/proc/$pid/status" 2>/dev/null)
          [ -n "$hwm" ] && printf '%s,%s,%s,%s,%s\n' "$now" "$pid" "$hwm" "$rss" "$(printf '%s' "${cmd%% -X*}" | tr ',' ' ')" >> "$out/worker.csv"
          ;;
      esac
    done
    sleep 0.25
  done
}

slow() {
  limit=""
  streak=0
  while [ ! -e "$out/stop" ]; do
    now=$(date +%s.%N)
    ha=$(curl -s -o /dev/null -m 10 -w '%{time_total},%{http_code}' http://127.0.0.1:8123/)
    printf '%s,%s\n' "$now" "$ha" >> "$out/ha.csv"
    fr=$(curl -s -m 5 http://127.0.0.1:5000/api/stats)
    printf '{"t": %s, "stats": %s}\n' "$now" "${fr:-null}" >> "$out/frigate.jsonl"
    # P1 abort rule, armed once the orchestrator writes the baseline p95 (seconds) to $out/limit
    if [ -z "$limit" ] && [ -s "$out/limit" ]; then limit=$(cat "$out/limit"); fi
    if [ -n "$limit" ] && [ ! -e "$out/abort" ]; then
      t=${ha%%,*}
      code=${ha##*,}
      if [ "$code" != 200 ]; then
        echo "failed request $now $ha" > "$out/abort"
      elif awk -v t="$t" -v l="$limit" 'BEGIN { exit !(t > 2 * l) }'; then
        streak=$((streak + 1))
        [ "$streak" -ge 3 ] && echo "three responses over 2x baseline p95 $now" > "$out/abort"
      else
        streak=0
      fi
    fi
    sleep 0.98
  done
}

fast &
slow &
wait
