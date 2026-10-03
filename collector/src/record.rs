//! `record-sdk <seconds> <out>`: copies what the shared-memory map publishes into a fixture
//! (`fixture.rs`) so a real session can be replayed later. Read-only on the map; it writes only the
//! output file. A fixture holds the session info of the recording, including the driver's name and
//! customer id, so it is private data unless deliberately sanitized.

use std::fs::File;
use std::io::{self, BufWriter};
use std::path::Path;
use std::time::{Duration, Instant};

use coach_ibt::VAR_HEADER_LEN;

use crate::fixture::FixtureWriter;
use crate::sdk::{MapSource, RowRead, SdkMap, read_header, read_newest_row};

/// What was recorded.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct RecordSummary {
    pub static_regions: u32,
    pub ticks: u32,
    pub status_changes: u32,
}

/// Records for `seconds`, waiting up to 5 s for the map to appear.
pub fn record(source: &mut dyn MapSource, seconds: f64, out: &Path) -> io::Result<RecordSummary> {
    let started = Instant::now();
    let mut map = loop {
        if let Some(map) = source.open()? {
            break map;
        }
        if started.elapsed() > Duration::from_secs(5) {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                "the SDK map does not exist: iRacing has not run",
            ));
        }
        std::thread::sleep(Duration::from_millis(200));
    };
    let mut writer = FixtureWriter::new(BufWriter::new(File::create(out)?))?;
    let summary = record_from(map.as_mut(), seconds, &mut writer)?;
    writer.finish()?;
    Ok(summary)
}

pub fn record_from<W: io::Write>(
    map: &mut dyn SdkMap,
    seconds: f64,
    writer: &mut FixtureWriter<W>,
) -> io::Result<RecordSummary> {
    let start = Instant::now();
    let mut summary = RecordSummary::default();
    let mut shape: Option<[i32; 6]> = None;
    let mut last_status: Option<i32> = None;
    let mut last_tick: Option<i32> = None;
    let mut row = Vec::new();
    while start.elapsed().as_secs_f64() < seconds {
        map.wait(Duration::from_millis(100));
        let Some(header) = read_header(map) else {
            continue;
        };
        let t_ms = start.elapsed().as_millis() as u32;
        let now_shape = [
            header.num_vars,
            header.var_header_offset,
            header.session_info_update,
            header.session_info_len,
            header.buf_len,
            header.num_buf,
        ];
        if shape != Some(now_shape) && header.connected() {
            // The first region is the whole map, so the buffers the header already points at
            // carry their rows; later ones (the session info changed) only the static part.
            let static_end = if shape.is_none() {
                map.len()
            } else {
                (header.var_header_offset.max(0) as usize
                    + header.num_vars.max(0) as usize * VAR_HEADER_LEN)
                    .max(
                        header.session_info_offset.max(0) as usize
                            + header.session_info_len.max(0) as usize,
                    )
                    .max(112)
                    .min(map.len())
            };
            let mut bytes = vec![0u8; static_end];
            if map.read(0, &mut bytes) {
                writer.write_static(map.len() as u32, &bytes)?;
                summary.static_regions += 1;
                shape = Some(now_shape);
            }
        }
        if last_status != Some(header.status) {
            last_status = Some(header.status);
            if shape.is_some() {
                writer.write_status(t_ms, header.status)?;
                summary.status_changes += 1;
            }
        }
        if !header.connected() || shape.is_none() {
            continue;
        }
        if let (RowRead::Row { tick }, after) = read_newest_row(map, &header, &mut row)
            && last_tick != Some(tick)
        {
            last_tick = Some(tick);
            writer.write_tick(
                t_ms,
                after.status,
                after.tick_rate,
                after.session_info_update,
                tick,
                &row,
            )?;
            summary.ticks += 1;
        }
    }
    Ok(summary)
}
