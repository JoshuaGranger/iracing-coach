#!/usr/bin/env bash
# User-local Rust 1.99 in WSL Ubuntu: rustup under $HOME/.rustup and $HOME/.cargo, no sudo, no
# shell-profile edits (tools/wsl/check.sh sources ~/.cargo/env itself). Safe to re-run.
set -euo pipefail

TOOLCHAIN=1.99
TARGETS=(x86_64-unknown-linux-gnu x86_64-pc-windows-msvc)

command -v cc >/dev/null || { echo "setup-rust: a C compiler (cc) is required; install build-essential once with sudo" >&2; exit 1; }

if [ -x "$HOME/.cargo/bin/rustup" ]; then
  # shellcheck disable=SC1091
  . "$HOME/.cargo/env"
  target_args=()
  for target in "${TARGETS[@]}"; do target_args+=(-t "$target"); done
  rustup toolchain install "$TOOLCHAIN" --profile minimal -c rustfmt -c clippy "${target_args[@]}"
else
  installer="$(mktemp)"
  trap 'rm -f "$installer"' EXIT
  curl --proto '=https' --tlsv1.2 -fsSL https://sh.rustup.rs -o "$installer"
  sh "$installer" -y --no-modify-path --profile minimal \
    --default-toolchain "$TOOLCHAIN" -c rustfmt -c clippy -t "${TARGETS[0]}" -t "${TARGETS[1]}"
  # shellcheck disable=SC1091
  . "$HOME/.cargo/env"
fi

rustup default "$TOOLCHAIN"
rustc --version
cargo --version
