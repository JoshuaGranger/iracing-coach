//! Small shared helpers: clocks, randomness, UTC timestamps.

use std::collections::hash_map::RandomState;
use std::hash::{BuildHasher, Hasher};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

/// Time source. Everything that depends on elapsed time (the on-track evidence, reconnect
/// backoff, stale detection) reads a `Clock` so tests and replays are deterministic.
pub trait Clock: Send + Sync {
    /// Milliseconds since the Unix epoch (wall clock).
    fn unix_ms(&self) -> u64;
    /// Monotonic milliseconds from an arbitrary origin.
    fn mono_ms(&self) -> u64;
}

#[derive(Debug)]
pub struct SystemClock {
    origin: Instant,
}

impl SystemClock {
    pub fn new() -> SystemClock {
        SystemClock {
            origin: Instant::now(),
        }
    }
}

impl Default for SystemClock {
    fn default() -> Self {
        Self::new()
    }
}

impl Clock for SystemClock {
    fn unix_ms(&self) -> u64 {
        unix_ms_now()
    }
    fn mono_ms(&self) -> u64 {
        self.origin.elapsed().as_millis() as u64
    }
}

/// A clock that moves only when told to; wall and monotonic time advance together.
#[derive(Debug)]
pub struct ManualClock {
    unix_origin: u64,
    mono: AtomicU64,
}

impl ManualClock {
    pub fn new(unix_origin_ms: u64) -> ManualClock {
        ManualClock {
            unix_origin: unix_origin_ms,
            mono: AtomicU64::new(0),
        }
    }
    pub fn advance(&self, ms: u64) {
        self.mono.fetch_add(ms, Ordering::SeqCst);
    }
    pub fn set_mono(&self, ms: u64) {
        self.mono.store(ms, Ordering::SeqCst);
    }
}

impl Clock for ManualClock {
    fn unix_ms(&self) -> u64 {
        self.unix_origin + self.mono.load(Ordering::SeqCst)
    }
    fn mono_ms(&self) -> u64 {
        self.mono.load(Ordering::SeqCst)
    }
}

pub fn unix_ms_now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

/// 64 random bits from the operating system's per-process hash seed. Not cryptographic; used
/// for ids and jitter only.
pub fn random_u64() -> u64 {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let mut hasher = RandomState::new().build_hasher();
    hasher.write_u64(COUNTER.fetch_add(1, Ordering::Relaxed));
    hasher.write_u128(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0),
    );
    hasher.finish()
}

/// A random UUID (version 4, RFC 4122 variant), lowercase hyphenated.
pub fn uuid_v4() -> String {
    let mut bytes = [0u8; 16];
    bytes[..8].copy_from_slice(&random_u64().to_le_bytes());
    bytes[8..].copy_from_slice(&random_u64().to_le_bytes());
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    let mut out = String::with_capacity(36);
    for (i, b) in bytes.iter().enumerate() {
        if matches!(i, 4 | 6 | 8 | 10) {
            out.push('-');
        }
        out.push_str(&format!("{b:02x}"));
    }
    out
}

/// `base` scaled by a random factor in `[1 - fraction, 1 + fraction]`.
pub fn jitter(base: Duration, fraction: f64) -> Duration {
    let unit = (random_u64() >> 11) as f64 / (1u64 << 53) as f64;
    let factor = 1.0 + fraction * (2.0 * unit - 1.0);
    Duration::from_secs_f64((base.as_secs_f64() * factor).max(0.0))
}

/// `YYYY-MM-DDTHH:MM:SS.mmmZ` for a Unix time in milliseconds.
pub fn iso_utc(unix_ms: u64) -> String {
    let secs = (unix_ms / 1000) as i64;
    let ms = unix_ms % 1000;
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    let (y, m, d) = civil_from_days(days);
    format!(
        "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}.{ms:03}Z",
        rem / 3600,
        rem % 3600 / 60,
        rem % 60
    )
}

pub fn iso_from_system_time(time: SystemTime) -> String {
    let ms = time
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0);
    iso_utc(ms)
}

/// Days since 1970-01-01 to a proleptic Gregorian date (Howard Hinnant's algorithm).
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

/// Lowercase hexadecimal of `bytes`.
pub fn hex(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        out.push_str(&format!("{b:02x}"));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn iso_formats_known_instants() {
        assert_eq!(iso_utc(0), "1970-01-01T00:00:00.000Z");
        assert_eq!(iso_utc(1_790_899_200_123), "2026-10-02T00:00:00.123Z");
        assert_eq!(iso_utc(951_782_400_000), "2000-02-29T00:00:00.000Z");
    }

    #[test]
    fn uuid_has_version_and_variant() {
        let id = uuid_v4();
        assert_eq!(id.len(), 36);
        assert_eq!(id.as_bytes()[14], b'4');
        assert!(matches!(id.as_bytes()[19], b'8' | b'9' | b'a' | b'b'));
        assert_ne!(id, uuid_v4());
    }

    #[test]
    fn jitter_stays_inside_the_band() {
        for _ in 0..200 {
            let d = jitter(Duration::from_secs(10), 0.2);
            assert!(d >= Duration::from_millis(8000) && d <= Duration::from_millis(12000));
        }
    }

    #[test]
    fn manual_clock_moves_both_scales() {
        let c = ManualClock::new(1000);
        c.advance(250);
        assert_eq!((c.unix_ms(), c.mono_ms()), (1250, 250));
    }
}
