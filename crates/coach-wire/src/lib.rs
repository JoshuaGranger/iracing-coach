//! Wire types generated from `docs/08-rewrite/contracts/`. Regenerate with `pnpm gen:wire`.

#![forbid(unsafe_code)]

/// One type per `components/schemas` entry of `api.openapi.yaml` (typify output).
#[allow(clippy::all, unused, missing_docs)]
pub mod api {
    include!(concat!(env!("OUT_DIR"), "/api.rs"));
}

#[rustfmt::skip]
#[path = "gen/live_channels.rs"]
pub mod live_channels;

#[rustfmt::skip]
#[path = "gen/setup_keys.rs"]
pub mod setup_keys;
