//! The live frame codec (`docs/08-rewrite/contracts/live-frame.md`, table version 1).
//!
//! Layout, little-endian: 38-byte fixed header, `ceil(N/8)` presence bytes (channel `i` is bit
//! `i % 8`, least significant first, of byte `i / 8`), the present values in index order at their
//! table widths, then the cue block (id, priority, evidence, argument count, `k` f32 arguments).

use coach_wire::live_channels::{CHANNELS, CUES, ChannelType, Evidence, Priority, TABLE_VERSION};

use crate::state::{CueState, LiveState};

pub const FRAME_MAGIC: [u8; 2] = *b"LF";
pub const FRAME_VERSION: u8 = 1;
pub const FRAME_FIXED_HEADER_LEN: usize = 38;

const FLAG_CONNECTED: u8 = 1 << 0;
const FLAG_ON_TRACK: u8 = 1 << 1;
const FLAG_IN_GARAGE: u8 = 1 << 2;
const FLAG_ON_PIT_ROAD: u8 = 1 << 3;
const FLAG_FIRST_OF_EPOCH: u8 = 1 << 4;
const FLAG_COACHING_PAUSED: u8 = 1 << 5;
const FLAG_BACKLOG: u8 = 1 << 6;

const CHANNEL_COUNT: usize = 45;

impl LiveState {
    /// Clears `out` and writes one complete frame: header, presence bits for all `CHANNELS`,
    /// present values in index order, cue block (id 0 when `cue` is None).
    pub fn encode_frame(&self, seq: u32, out: &mut Vec<u8>) {
        debug_assert_eq!(CHANNELS.len(), CHANNEL_COUNT);
        out.clear();
        let mut flags = 0u8;
        if self.connected {
            flags |= FLAG_CONNECTED;
        }
        if self.on_track {
            flags |= FLAG_ON_TRACK;
        }
        if self.in_garage {
            flags |= FLAG_IN_GARAGE;
        }
        if self.on_pit_road == Some(true) {
            flags |= FLAG_ON_PIT_ROAD;
        }
        if self.first_of_epoch {
            flags |= FLAG_FIRST_OF_EPOCH;
        }
        if self.coaching_paused {
            flags |= FLAG_COACHING_PAUSED;
        }
        out.extend_from_slice(&FRAME_MAGIC);
        out.push(FRAME_VERSION);
        out.push(flags);
        out.extend_from_slice(&seq.to_le_bytes());
        out.extend_from_slice(&self.session_epoch.to_le_bytes());
        out.extend_from_slice(&self.captured_unix_ms.to_le_bytes());
        out.extend_from_slice(&self.session_time.unwrap_or(f64::NAN).to_le_bytes());
        out.extend_from_slice(&self.tick.to_le_bytes());
        out.extend_from_slice(&self.tick_rate.to_le_bytes());
        out.extend_from_slice(&TABLE_VERSION.to_le_bytes());
        out.extend_from_slice(&(CHANNEL_COUNT as u16).to_le_bytes());
        debug_assert_eq!(out.len(), FRAME_FIXED_HEADER_LEN);

        let values = self.channel_values();
        let mut presence = [0u8; CHANNEL_COUNT.div_ceil(8)];
        for (index, value) in values.iter().enumerate() {
            if value.is_some() {
                presence[index / 8] |= 1 << (index % 8);
            }
        }
        out.extend_from_slice(&presence);
        for value in values.iter().flatten() {
            match *value {
                ChannelValue::F32(v) => out.extend_from_slice(&v.to_le_bytes()),
                ChannelValue::F64(v) => out.extend_from_slice(&v.to_le_bytes()),
                ChannelValue::U8(v) => out.push(v),
                ChannelValue::I8(v) => out.push(v as u8),
                ChannelValue::U16(v) => out.extend_from_slice(&v.to_le_bytes()),
                ChannelValue::U32(v) => out.extend_from_slice(&v.to_le_bytes()),
                ChannelValue::Bool(v) => out.push(u8::from(v)),
                ChannelValue::Enum(v) => out.push(v),
            }
        }
        match self.cue {
            Some(cue) => {
                out.push(cue.id);
                out.push(cue.priority as u8);
                out.push(cue.evidence as u8);
                let count = cue.arg_count.min(4);
                out.push(count);
                for arg in &cue.args[..usize::from(count)] {
                    out.extend_from_slice(&arg.to_le_bytes());
                }
            }
            None => out.extend_from_slice(&[0, 0, 0, 0]),
        }
    }

    /// The channel values in table order (index = `CHANNELS[i].index`).
    pub fn channel_values(&self) -> [Option<ChannelValue>; CHANNEL_COUNT] {
        use ChannelValue::{Bool, Enum, F32, F64, I8, U8, U16, U32};
        [
            self.speed.map(F32),
            self.throttle.map(F32),
            self.brake.map(F32),
            self.steering_wheel_angle.map(F32),
            self.steering_road_wheel.map(F32),
            self.steering_ratio.map(F32),
            self.gear.map(I8),
            self.rpm.map(F32),
            self.yaw_rate.map(F32),
            self.lateral_accel.map(F32),
            self.longitudinal_accel.map(F32),
            self.lap_dist_pct.map(F32),
            self.latitude.map(F64),
            self.longitude.map(F64),
            self.lap.map(U16),
            self.laps_remaining.map(U16),
            self.position.map(U8),
            self.class_position.map(U8),
            self.flag_state.map(|v| Enum(v as u8)),
            self.session_flags.map(U32),
            self.abs_active.map(Bool),
            self.brake_bias.map(F32),
            self.fuel.map(F32),
            self.fuel_pct.map(F32),
            self.fuel_laps_remaining.map(F32),
            self.fuel_hard_limit_laps.map(U16),
            self.last_lap.map(F32),
            self.leader_last_lap.map(F32),
            self.ahead_gap.map(F32),
            self.ahead_gap_trend.map(|v| Enum(v as u8)),
            self.behind_gap.map(F32),
            self.behind_gap_trend.map(|v| Enum(v as u8)),
            self.leader_gap.map(F32),
            self.pace_band_min.map(F32),
            self.pace_band_max.map(F32),
            self.run_phase.map(|v| Enum(v as u8)),
            self.green_run_laps.map(U16),
            self.mandatory_repair.map(F32),
            self.optional_repair.map(F32),
            self.on_pit_road.map(Bool),
            self.tow_active.map(Bool),
            self.track_temperature.map(F32),
            self.air_temperature.map(F32),
            self.incidents.map(U16),
            self.pace_delta_to_leader.map(F32),
        ]
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct FrameHeader {
    pub version: u8,
    pub flags: u8,
    pub seq: u32,
    pub session_epoch: u32,
    pub captured_unix_ms: u64,
    /// NaN when absent.
    pub session_time: f64,
    pub tick: u32,
    pub tick_rate: u16,
    pub table_version: u16,
    pub channel_count: u16,
}

/// Server check: magic, `version == 1`, `table_version <=` own `TABLE_VERSION`,
/// `channel_count <=` own table length, and `len >= 38 + ceil(channel_count / 8)`. Decodes no values.
pub fn parse_header(frame: &[u8]) -> Result<FrameHeader, FrameError> {
    if frame.len() < FRAME_FIXED_HEADER_LEN {
        return Err(FrameError::TooShort {
            needed: FRAME_FIXED_HEADER_LEN,
            len: frame.len(),
        });
    }
    if frame[0..2] != FRAME_MAGIC {
        return Err(FrameError::BadMagic);
    }
    let version = frame[2];
    if version != FRAME_VERSION {
        return Err(FrameError::UnsupportedVersion(version));
    }
    let header = FrameHeader {
        version,
        flags: frame[3],
        seq: u32_at(frame, 4),
        session_epoch: u32_at(frame, 8),
        captured_unix_ms: u64_at(frame, 12),
        session_time: f64::from_bits(u64_at(frame, 20)),
        tick: u32_at(frame, 28),
        tick_rate: u16_at(frame, 32),
        table_version: u16_at(frame, 34),
        channel_count: u16_at(frame, 36),
    };
    if header.table_version > TABLE_VERSION {
        return Err(FrameError::TableNewer {
            frame: header.table_version,
            own: TABLE_VERSION,
        });
    }
    if usize::from(header.channel_count) > CHANNELS.len() {
        return Err(FrameError::TooManyChannels {
            frame: header.channel_count,
            own: CHANNELS.len() as u16,
        });
    }
    let needed = FRAME_FIXED_HEADER_LEN + usize::from(header.channel_count).div_ceil(8);
    if frame.len() < needed {
        return Err(FrameError::TooShort {
            needed,
            len: frame.len(),
        });
    }
    Ok(header)
}

/// Sets flags bit 6 in place (backlog frames).
pub fn set_backlog_flag(frame: &mut [u8]) -> Result<(), FrameError> {
    if frame.len() < FRAME_FIXED_HEADER_LEN {
        return Err(FrameError::TooShort {
            needed: FRAME_FIXED_HEADER_LEN,
            len: frame.len(),
        });
    }
    if frame[0..2] != FRAME_MAGIC {
        return Err(FrameError::BadMagic);
    }
    frame[3] |= FLAG_BACKLOG;
    Ok(())
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum ChannelValue {
    F32(f32),
    F64(f64),
    U8(u8),
    I8(i8),
    U16(u16),
    U32(u32),
    Bool(bool),
    Enum(u8),
}

#[derive(Debug, Clone, PartialEq)]
pub struct DecodedFrame {
    pub header: FrameHeader,
    /// `len = channel_count` (<= own table); index >= `channel_count` is absent.
    pub values: Vec<Option<ChannelValue>>,
    /// `None` when the cue id is 0.
    pub cue: Option<CueState>,
}

/// Full decode for tests, golden vectors and the collector's own checks. Every length and
/// discriminant is checked before use.
pub fn decode_frame(frame: &[u8]) -> Result<DecodedFrame, FrameError> {
    let header = parse_header(frame)?;
    let count = usize::from(header.channel_count);
    let presence_len = count.div_ceil(8);
    let presence = &frame[FRAME_FIXED_HEADER_LEN..FRAME_FIXED_HEADER_LEN + presence_len];
    let mut offset = FRAME_FIXED_HEADER_LEN + presence_len;
    let mut values = Vec::with_capacity(count);
    for (index, spec) in CHANNELS.iter().enumerate().take(count) {
        if presence[index / 8] & (1 << (index % 8)) == 0 {
            values.push(None);
            continue;
        }
        let width = match spec.ty {
            ChannelType::F32 | ChannelType::U32 => 4,
            ChannelType::F64 => 8,
            ChannelType::U16 => 2,
            ChannelType::U8 | ChannelType::I8 | ChannelType::Bool8 | ChannelType::Enum8 => 1,
        };
        let end = offset + width;
        let Some(bytes) = frame.get(offset..end) else {
            return Err(FrameError::TooShort {
                needed: end,
                len: frame.len(),
            });
        };
        values.push(Some(match spec.ty {
            ChannelType::F32 => {
                ChannelValue::F32(f32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]))
            }
            ChannelType::F64 => ChannelValue::F64(f64::from_le_bytes([
                bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5], bytes[6], bytes[7],
            ])),
            ChannelType::U8 => ChannelValue::U8(bytes[0]),
            ChannelType::I8 => ChannelValue::I8(bytes[0] as i8),
            ChannelType::U16 => ChannelValue::U16(u16::from_le_bytes([bytes[0], bytes[1]])),
            ChannelType::U32 => {
                ChannelValue::U32(u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]))
            }
            ChannelType::Bool8 => ChannelValue::Bool(bytes[0] != 0),
            ChannelType::Enum8 => ChannelValue::Enum(bytes[0]),
        }));
        offset = end;
    }

    let cue = decode_cue(frame.get(offset..).unwrap_or(&[]))?;
    Ok(DecodedFrame {
        header,
        values,
        cue,
    })
}

fn decode_cue(block: &[u8]) -> Result<Option<CueState>, FrameError> {
    if block.len() < 4 {
        return Err(FrameError::BadCue("truncated"));
    }
    let (id, priority, evidence, count) = (block[0], block[1], block[2], block[3]);
    if count > 4 {
        return Err(FrameError::BadCue("argument count above 4"));
    }
    let args_end = 4 + 4 * usize::from(count);
    let Some(arg_bytes) = block.get(4..args_end) else {
        return Err(FrameError::BadCue("truncated"));
    };
    if id == 0 {
        return Ok(None);
    }
    if !CUES.iter().any(|spec| spec.id == id) {
        return Err(FrameError::BadCue("unknown cue id"));
    }
    let Some(priority) = Priority::from_u8(priority) else {
        return Err(FrameError::BadCue("unknown priority"));
    };
    let Some(evidence) = Evidence::from_u8(evidence) else {
        return Err(FrameError::BadCue("unknown evidence"));
    };
    let mut args = [0.0_f32; 4];
    let (chunks, _) = arg_bytes.as_chunks::<4>();
    for (slot, chunk) in args.iter_mut().zip(chunks) {
        *slot = f32::from_le_bytes(*chunk);
    }
    Ok(Some(CueState {
        id,
        priority,
        evidence,
        arg_count: count,
        args,
    }))
}

fn u16_at(frame: &[u8], offset: usize) -> u16 {
    u16::from_le_bytes([frame[offset], frame[offset + 1]])
}

fn u32_at(frame: &[u8], offset: usize) -> u32 {
    u32::from_le_bytes([
        frame[offset],
        frame[offset + 1],
        frame[offset + 2],
        frame[offset + 3],
    ])
}

fn u64_at(frame: &[u8], offset: usize) -> u64 {
    let mut bytes = [0u8; 8];
    bytes.copy_from_slice(&frame[offset..offset + 8]);
    u64::from_le_bytes(bytes)
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum FrameError {
    #[error("frame shorter than {needed} bytes ({len})")]
    TooShort { needed: usize, len: usize },
    #[error("bad magic")]
    BadMagic,
    #[error("unsupported frame version {0}")]
    UnsupportedVersion(u8),
    #[error("table version {frame} newer than {own}")]
    TableNewer { frame: u16, own: u16 },
    /// A sender at a table version <= own cannot know more channels; their widths are unknown.
    #[error("channel count {frame} exceeds own table's {own}")]
    TooManyChannels { frame: u16, own: u16 },
    /// "truncated", "argument count above 4", "unknown cue id", "unknown priority" or
    /// "unknown evidence"; cue id 0 skips the id, priority and evidence checks.
    #[error("bad cue block: {0}")]
    BadCue(&'static str),
}
