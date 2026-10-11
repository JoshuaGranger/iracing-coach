//! coach-collector: SDK reader, live overlay, garage capture and uploader for the sim PC.
//!
//! Everything that does not call Win32 is portable and testable on any platform: the reader works
//! on a map abstraction (the real shared memory or a replayed fixture), the uploader and link talk
//! plain HTTP and WebSocket. `win` holds the Windows-only parts (memory map, overlay, tray,
//! directory watcher, install).

pub mod app;
pub mod backfill;
pub mod cli;
pub mod config;
pub mod cpu;
pub mod dry;
pub mod field;
pub mod fixture;
pub mod garage;
pub mod garage_io;
pub mod gate;
pub mod hotkey;
pub mod http;
pub mod hub;
pub mod journal;
pub mod layout;
pub mod link;
pub mod logging;
pub mod pipeline;
pub mod record;
pub mod scan;
pub mod sdk;
pub mod stream;
pub mod uploader;
pub mod util;
pub mod wheel;
pub mod ws;

#[cfg(windows)]
pub mod win;
