//! coach-server: HTTP API, ingest, archiver, job runner, collector hub and live relay for the
//! mini PC. Linux is the production target; the code is platform-neutral and builds and tests on
//! Windows too. Contracts: `docs/08-rewrite/contracts/`.

#![deny(unsafe_code)]
// SQL result rows are read as tuples; naming each one would add types nobody reuses.
#![allow(clippy::type_complexity)]

pub mod adapters;
pub mod api;
pub mod app;
pub mod archiver;
pub mod config;
pub mod db;
pub mod error;
pub mod events;
pub mod faults;
pub mod handlers;
pub mod hub;
pub mod import;
pub mod ingest;
pub mod jobs;
pub mod rebuild;
pub mod scheduler;
pub mod schema;
pub mod setups;
pub mod sys;
pub mod tuning;
pub mod userdata;
pub mod util;
pub mod worker;
pub mod ws;

pub use app::{App, AppState, Parts};
pub use config::Config;
