#!/usr/bin/env bash
# Linux gate in WSL Ubuntu. Run from anywhere: wsl -d Ubuntu -- bash tools/wsl/check.sh [scope]
#   (none) | all   rust, then the Python suite
#   rust           clippy -D warnings and tests over the whole workspace
#   server         coach-server and the crates it links (coach-wire, coach-ibt, coach-live, coach-views)
#   python         Python suite only
# Build output goes to the Linux filesystem; the /mnt/c checkout is read only for sources.
set -euo pipefail

scope="${1:-all}"
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo"

if [ -f "$HOME/.cargo/env" ]; then
  # shellcheck disable=SC1091
  . "$HOME/.cargo/env"
fi
export CARGO_TARGET_DIR="${CARGO_TARGET_DIR:-$HOME/.cache/iracing-coach/target}"
export CARGO_TERM_COLOR=never
export CARGO_TERM_PROGRESS_WHEN=never

step() { printf '\n== %s\n' "$*"; }

rust() {
  command -v cargo >/dev/null || { echo "cargo not found: run tools/wsl/setup-rust.sh first" >&2; exit 1; }
  local pkgs=("$@")
  step "cargo clippy ${pkgs[*]}"
  cargo clippy "${pkgs[@]}" --all-targets -- -D warnings
  step "cargo test ${pkgs[*]}"
  cargo test "${pkgs[@]}"
}

python() {
  step "python3.12 unittest iracing-coach/tests"
  python3.12 -X utf8 -m unittest discover -s iracing-coach/tests
}

case "$scope" in
  all) rust --workspace; python ;;
  rust) rust --workspace ;;
  server) rust -p coach-server -p coach-wire -p coach-ibt -p coach-live -p coach-views ;;
  python) python ;;
  *) echo "usage: check.sh [rust|server|all|python]" >&2; exit 2 ;;
esac
printf '\nWSL check passed (%s)\n' "$scope"
