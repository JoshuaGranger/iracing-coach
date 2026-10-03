//! The Python `datetime` behaviour `_extract_summary` depends on: `os.stat().st_mtime`,
//! `datetime.fromtimestamp(t, timezone.utc).isoformat()` (`_safe_iso`) and
//! `datetime.fromisoformat(s).timestamp()` (`_parse_iso_epoch`), as CPython 3.12's C
//! implementation computes them on Linux.

use std::time::{SystemTime, UNIX_EPOCH};

use crate::extract::Val;
use crate::py;

/// Proleptic Gregorian ordinal of 1970-01-01 (0001-01-01 is 1).
const EPOCH_ORDINAL: i64 = 719_163;
const MIN_YEAR: i64 = 1;
const MAX_YEAR: i64 = 9999;

/// `os.stat().st_mtime`: whole seconds (floored) plus nanoseconds times 1e-9, in that order.
pub(crate) fn unix_seconds(time: SystemTime) -> f64 {
    match time.duration_since(UNIX_EPOCH) {
        Ok(after) => after.as_secs() as f64 + f64::from(after.subsec_nanos()) * 1e-9,
        Err(error) => {
            let before = error.duration();
            let (mut secs, mut nanos) = (-(before.as_secs() as i64), 0u32);
            if before.subsec_nanos() > 0 {
                secs -= 1;
                nanos = 1_000_000_000 - before.subsec_nanos();
            }
            secs as f64 + f64::from(nanos) * 1e-9
        }
    }
}

fn is_leap(year: i64) -> bool {
    year % 4 == 0 && (year % 100 != 0 || year % 400 == 0)
}

fn days_in_month(year: i64, month: i64) -> i64 {
    match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 if is_leap(year) => 29,
        2 => 28,
        _ => 0,
    }
}

/// Ordinal of a date with year >= 1 (`ymd_to_ord`).
fn ymd_to_ord(year: i64, month: i64, day: i64) -> i64 {
    let y = year - 1;
    let before_year = y * 365 + y / 4 - y / 100 + y / 400;
    let before_month: i64 = (1..month).map(|m| days_in_month(year, m)).sum();
    before_year + before_month + day
}

/// Civil date of an ordinal (Howard Hinnant's `civil_from_days`).
fn ord_to_ymd(ordinal: i64) -> (i64, i64, i64) {
    let z = ordinal - EPOCH_ORDINAL + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    (year, month, day)
}

/// `_PyTime_RoundHalfEven`.
fn round_half_even(x: f64) -> f64 {
    let rounded = x.round();
    if (x - rounded).abs() == 0.5 {
        2.0 * (x / 2.0).round()
    } else {
        rounded
    }
}

/// `_safe_iso` (ibt_reader.py:605-613): None for a non-finite epoch or one outside years 1 to
/// 9999 after microsecond rounding (round half to even).
pub(crate) fn safe_iso(epoch: f64) -> Option<String> {
    if !epoch.is_finite() {
        return None;
    }
    let mut whole = epoch.trunc();
    let mut micros = round_half_even((epoch - whole) * 1e6);
    if micros >= 1e6 {
        micros -= 1e6;
        whole += 1.0;
    } else if micros < 0.0 {
        micros += 1e6;
        whole -= 1.0;
    }
    if !(-9.2e18..9.2e18).contains(&whole) {
        return None;
    }
    let seconds = whole as i64;
    let (year, month, day) = ord_to_ymd(seconds.div_euclid(86_400) + EPOCH_ORDINAL);
    if !(MIN_YEAR..=MAX_YEAR).contains(&year) {
        return None;
    }
    let of_day = seconds.rem_euclid(86_400);
    let mut text = format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}",
        of_day / 3600,
        of_day % 3600 / 60,
        of_day % 60
    );
    let micros = micros as u32;
    if micros != 0 {
        text.push_str(&format!(".{micros:06}"));
    }
    text.push_str("+00:00");
    Some(text)
}

/// The string as C reads it: bytes followed by NULs.
struct CStr<'a>(&'a [u8]);

impl CStr<'_> {
    fn at(&self, index: usize) -> u8 {
        self.0.get(index).copied().unwrap_or(0)
    }

    /// `parse_digits`: exactly `count` ASCII digits.
    fn digits(&self, p: &mut usize, count: usize) -> Option<i64> {
        let mut value = 0i64;
        for _ in 0..count {
            let digit = self.at(*p).wrapping_sub(b'0');
            if digit > 9 {
                return None;
            }
            value = value * 10 + i64::from(digit);
            *p += 1;
        }
        Some(value)
    }
}

/// `_find_isoformat_datetime_separator`; None where C returns -1.
fn find_separator(s: &CStr<'_>, len: usize) -> Option<usize> {
    if len == 7 {
        return Some(7);
    }
    if s.at(4) == b'-' {
        if s.at(5) != b'W' {
            return Some(10);
        }
        if len < 8 {
            return None;
        }
        if len > 8 && s.at(8) == b'-' {
            if len == 9 {
                return None;
            }
            if len > 10 && s.at(10).is_ascii_digit() {
                return Some(8);
            }
            return Some(10);
        }
        return Some(8);
    }
    if s.at(4) == b'W' {
        let mut index = 7;
        while index < len && s.at(index).is_ascii_digit() {
            index += 1;
        }
        if index < 9 {
            return Some(index);
        }
        return Some(if index % 2 == 0 { 7 } else { 8 });
    }
    Some(8)
}

/// `iso_to_ymd`.
fn iso_to_ymd(iso_year: i64, week: i64, day: i64) -> Option<(i64, i64, i64)> {
    if iso_year < MIN_YEAR {
        return None;
    }
    if week <= 0 || week >= 53 {
        let first_weekday = ymd_to_ord(iso_year, 1, 1) % 7;
        let long_year = first_weekday == 4 || (first_weekday == 3 && is_leap(iso_year));
        if !(week == 53 && long_year) {
            return None;
        }
    }
    if !(1..=7).contains(&day) {
        return None;
    }
    let first_day = ymd_to_ord(iso_year, 1, 1);
    let first_weekday = (first_day + 6) % 7;
    let mut week1_monday = first_day - first_weekday;
    if first_weekday > 3 {
        week1_monday += 7;
    }
    Some(ord_to_ymd(week1_monday + (week - 1) * 7 + day - 1))
}

/// `parse_isoformat_date` over `len` bytes.
fn parse_date(s: &CStr<'_>, len: usize) -> Option<(i64, i64, i64)> {
    let mut p = 0;
    let year = s.digits(&mut p, 4)?;
    let separated = s.at(p) == b'-';
    if separated {
        p += 1;
    }
    if s.at(p) == b'W' {
        p += 1;
        let week = s.digits(&mut p, 2)?;
        let day = if p < len {
            if separated {
                if s.at(p) != b'-' {
                    return None;
                }
                p += 1;
            }
            s.digits(&mut p, 1)?
        } else {
            1
        };
        return iso_to_ymd(year, week, day);
    }
    let month = s.digits(&mut p, 2)?;
    if separated {
        if s.at(p) != b'-' {
            return None;
        }
        p += 1;
    }
    let day = s.digits(&mut p, 2)?;
    Some((year, month, day))
}

/// `parse_hh_mm_ss_ff` between `p` and `end`: `(hour, minute, second, microsecond, more)` where
/// `more` is C's return value 1 ("not the end of the string").
fn parse_clock(s: &CStr<'_>, mut p: usize, end: usize) -> Option<([i64; 3], i64, bool)> {
    let mut values = [0i64; 3];
    let mut separated = true;
    for index in 0..3 {
        values[index] = s.digits(&mut p, 2)?;
        let c = s.at(p);
        p += 1;
        if index == 0 {
            separated = c == b':';
        }
        if p >= end {
            return Some((values, 0, c != 0));
        }
        if separated && c == b':' {
            continue;
        }
        if c == b'.' || c == b',' {
            break;
        }
        if separated {
            return None;
        }
        p -= 1;
    }
    // Every path here leaves at least one byte before `end`.
    let to_parse = end.saturating_sub(p).min(6);
    if to_parse == 0 {
        return None;
    }
    let micros = s.digits(&mut p, to_parse)? * 10i64.pow((6 - to_parse) as u32);
    while s.at(p).is_ascii_digit() {
        p += 1;
    }
    Some((values, micros, s.at(p) != 0))
}

/// `datetime.fromisoformat(text)` as (proleptic ordinal, seconds of day, microsecond,
/// UTC offset in microseconds).
fn from_isoformat(text: &str) -> Option<(i64, i64, i64, i64)> {
    if text.chars().count() < 7 {
        return None;
    }
    let bytes = text.as_bytes();
    let s = CStr(bytes);
    let len = bytes.len();
    let separator = find_separator(&s, len)?;
    let (year, month, day) = parse_date(&s, separator)?;
    let mut clock = [0i64; 3];
    let mut micros = 0;
    let mut offset: Option<i64> = None;
    if len > separator {
        let start = separator + 1;
        let end = len;
        let mut tz = start;
        loop {
            if matches!(s.at(tz), b'Z' | b'+' | b'-') {
                break;
            }
            tz += 1;
            if tz >= end {
                break;
            }
        }
        let (values, us, more) = parse_clock(&s, start, tz)?;
        clock = values;
        micros = us;
        if tz == end {
            if more {
                return None;
            }
        } else if s.at(tz) == b'Z' {
            if s.at(tz + 1) != 0 {
                return None;
            }
            offset = Some(0);
        } else {
            let sign = if s.at(tz) == b'-' { -1 } else { 1 };
            let (tz_values, tz_micros, more) = parse_clock(&s, tz + 1, end)?;
            if more {
                return None;
            }
            let seconds = tz_values[0] * 3600 + tz_values[1] * 60 + tz_values[2];
            let total = sign * (seconds * 1_000_000 + tz_micros);
            if total.abs() >= 86_400 * 1_000_000 {
                return None;
            }
            offset = Some(total);
        }
    }
    let [hour, minute, second] = clock;
    let valid = (MIN_YEAR..=MAX_YEAR).contains(&year)
        && (1..=12).contains(&month)
        && day >= 1
        && day <= days_in_month(year, month)
        && (0..24).contains(&hour)
        && (0..60).contains(&minute)
        && (0..60).contains(&second);
    if !valid {
        return None;
    }
    Some((
        ymd_to_ord(year, month, day),
        hour * 3600 + minute * 60 + second,
        micros,
        offset.unwrap_or(0),
    ))
}

/// `_parse_iso_epoch` (ibt_reader.py:616-628): a non-empty string, trailing `Z` read as
/// `+00:00`, naive times taken as UTC; `.timestamp()` is total microseconds / 10^6, rounded
/// once.
pub(crate) fn parse_iso_epoch(value: &Val<'_>) -> Option<f64> {
    let Val::Str(text) = value else {
        return None;
    };
    let text = py::strip(text);
    if text.is_empty() {
        return None;
    }
    let text = match text.strip_suffix('Z') {
        Some(head) => format!("{head}+00:00"),
        None => text.to_owned(),
    };
    let (ordinal, of_day, micros, offset) = from_isoformat(&text)?;
    let total = (i128::from(ordinal - EPOCH_ORDINAL) * 86_400 + i128::from(of_day)) * 1_000_000
        + i128::from(micros)
        - i128::from(offset);
    let sign = if total < 0 { "-" } else { "" };
    let magnitude = total.unsigned_abs();
    format!(
        "{sign}{}.{:06}",
        magnitude / 1_000_000,
        magnitude % 1_000_000
    )
    .parse()
    .ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::borrow::Cow;
    use std::time::Duration;

    fn epoch(text: &str) -> Option<f64> {
        parse_iso_epoch(&Val::Str(Cow::Borrowed(text)))
    }

    #[test]
    fn safe_iso_matches_python() {
        assert_eq!(
            safe_iso(1_780_000_000.0).as_deref(),
            Some("2026-05-28T20:26:40+00:00")
        );
        assert_eq!(
            safe_iso(1_780_000_001.983333).as_deref(),
            Some("2026-05-28T20:26:41.983333+00:00")
        );
        assert_eq!(
            safe_iso(-1.5).as_deref(),
            Some("1969-12-31T23:59:58.500000+00:00")
        );
        assert_eq!(
            safe_iso(0.0000005).as_deref(),
            Some("1970-01-01T00:00:00+00:00")
        );
        assert_eq!(
            safe_iso(0.0000015).as_deref(),
            Some("1970-01-01T00:00:00.000002+00:00")
        );
        // Linux CPython; Windows CPython raises OSError (so None) after year 3000.
        assert_eq!(
            safe_iso(253_402_300_799.0).as_deref(),
            Some("9999-12-31T23:59:59+00:00")
        );
        assert_eq!(safe_iso(253_402_300_800.0), None);
        assert_eq!(
            safe_iso(-62_135_596_800.0).as_deref(),
            Some("0001-01-01T00:00:00+00:00")
        );
        assert_eq!(safe_iso(-62_135_596_801.0), None);
        assert_eq!(safe_iso(f64::INFINITY), None);
        assert_eq!(safe_iso(1e300), None);
    }

    #[test]
    fn iso_epoch_matches_python() {
        assert_eq!(epoch("2026-05-28T20:26:40Z"), Some(1_780_000_000.0));
        assert_eq!(epoch("2026-05-28 20:26:40"), Some(1_780_000_000.0));
        assert_eq!(epoch("2026-05-28T21:26:40+01:00"), Some(1_780_000_000.0));
        assert_eq!(epoch("20260528T202640"), Some(1_780_000_000.0));
        assert_eq!(epoch("2026-05-28"), Some(1_779_926_400.0));
        assert_eq!(epoch("2026-05-28T20:26:40.5"), Some(1_780_000_000.5));
        assert_eq!(epoch("2026-W22-4"), Some(1_779_926_400.0));
        assert_eq!(epoch("2026-05-28T24:00:00"), None);
        assert_eq!(epoch("2026-02-30"), None);
        assert_eq!(epoch("2026-05"), None);
        assert_eq!(epoch("nonsense"), None);
        assert_eq!(epoch(" "), None);
    }

    #[test]
    fn mtime_seconds_follow_cpython() {
        let time = UNIX_EPOCH + Duration::new(1_786_582_907, 540_936_000);
        assert_eq!(unix_seconds(time), 1_786_582_907.0 + 540_936_000.0 * 1e-9);
        let before = UNIX_EPOCH - Duration::new(1, 250_000_000);
        assert_eq!(unix_seconds(before), -1.25);
    }
}
