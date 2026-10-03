//! Number and time formatting that reproduces the .NET (en-US) renderings the C# live monitor used:
//! standard `F{n}`, custom `0.00`/`0.##`/`+0.000;-0.000;0.000`, `P0`, and `TimeSpan` lap times.
//! Facts checked against .NET 10 (see `tests/vectors/README.md`).

/// .NET standard `F{decimals}`: the exact decimal value rounded half to even, which is what
/// Rust's `{:.N}` does.
pub(crate) fn fixed(value: f64, decimals: usize) -> String {
    format!("{value:.decimals$}")
}

/// .NET custom format `0.000...`: the value is first reduced to 15 significant digits and then
/// rounded half away from zero at `decimals`. The sign is kept for a nonzero result.
pub(crate) fn custom(value: f64, decimals: usize) -> String {
    if !value.is_finite() {
        return value.to_string();
    }
    let negative = value.is_sign_negative() && value != 0.0;
    let (digits, point) = significant_digits(value.abs());
    let body = round_half_up(&digits, point, decimals);
    if negative && body.bytes().any(|b| b.is_ascii_digit() && b != b'0') {
        format!("-{body}")
    } else {
        body
    }
}

/// .NET custom format `0.##` (invariant): up to `max_decimals` places, trailing zeros dropped.
pub(crate) fn custom_optional(value: f64, max_decimals: usize) -> String {
    let text = custom(value, max_decimals);
    if text.contains('.') {
        text.trim_end_matches('0').trim_end_matches('.').to_string()
    } else {
        text
    }
}

/// .NET custom format `+0.000;-0.000;0.000`: a result that rounds to zero uses the zero section.
pub(crate) fn signed(value: f64, decimals: usize) -> String {
    let body = custom(value.abs(), decimals);
    if body.bytes().all(|b| b == b'0' || b == b'.') {
        body
    } else if value < 0.0 {
        format!("-{body}")
    } else {
        format!("+{body}")
    }
}

/// .NET standard `P0` in en-US: the exact value shifted two decimal places and rounded half to
/// even (which is `{:.2}` of the unscaled value), thousands separators, a trailing `%`.
pub(crate) fn percent0(value: f64) -> String {
    let hundredths = format!("{:.2}", value.abs());
    let digits: String = hundredths.chars().filter(char::is_ascii_digit).collect();
    let digits = digits.trim_start_matches('0');
    let digits = if digits.is_empty() { "0" } else { digits };
    let sign = if value < 0.0 { "-" } else { "" };
    format!("{sign}{}%", group_thousands(digits))
}

/// Integer text of a whole-number argument (cue arguments travel as f32).
pub(crate) fn whole(value: f64) -> String {
    if value.is_finite() {
        format!("{}", value.round() as i64)
    } else {
        String::new()
    }
}

/// `Math.Ceiling` interpolated into text.
pub(crate) fn ceiling(value: f64) -> String {
    if value.is_finite() {
        format!("{}", value.ceil() as i64)
    } else {
        String::new()
    }
}

/// `TimeSpan.FromSeconds(seconds).ToString(seconds >= 60 ? "m\:ss\.fff" : "s\.fff")`: the span is
/// truncated to whole ticks, the fraction is truncated (never rounded) to milliseconds, and the
/// hour component is dropped.
pub(crate) fn lap_time(seconds: f64) -> String {
    let ticks = (seconds * 10_000_000.0) as i64;
    let sign = if ticks < 0 { "-" } else { "" };
    let ticks = ticks.unsigned_abs();
    let millis = (ticks % 10_000_000) / 10_000;
    let whole_seconds = (ticks / 10_000_000) % 60;
    if seconds >= 60.0 {
        let minutes = (ticks / 600_000_000) % 60;
        format!("{sign}{minutes}:{whole_seconds:02}.{millis:03}")
    } else {
        format!("{sign}{whole_seconds}.{millis:03}")
    }
}

fn group_thousands(digits: &str) -> String {
    let bytes = digits.as_bytes();
    let mut out = String::with_capacity(digits.len() + digits.len() / 3);
    for (index, byte) in bytes.iter().enumerate() {
        if index > 0 && (bytes.len() - index).is_multiple_of(3) {
            out.push(',');
        }
        out.push(*byte as char);
    }
    out
}

/// 15 significant digits of a non-negative finite value and the position of the decimal point
/// (number of digits before it, which may be zero or negative).
fn significant_digits(value: f64) -> (Vec<u8>, i32) {
    if value == 0.0 {
        return (vec![0], 1);
    }
    let text = format!("{value:.14e}");
    let (mantissa, exponent) = text.split_once('e').unwrap_or((text.as_str(), "0"));
    let exponent: i32 = exponent.parse().unwrap_or(0);
    let digits = mantissa
        .bytes()
        .filter(u8::is_ascii_digit)
        .map(|b| b - b'0')
        .collect();
    (digits, exponent + 1)
}

fn round_half_up(digits: &[u8], point: i32, decimals: usize) -> String {
    // Integer digits: positions [0, point); fraction digits follow. Build a digit vector that
    // covers `point` integer places (at least one) plus `decimals` fraction places, then round on
    // the next digit.
    let int_len = point.max(1) as usize;
    let total = int_len + decimals;
    let mut grid = vec![0u8; total + 1];
    for (index, digit) in digits.iter().enumerate() {
        let position = index as i32 + (int_len as i32 - point);
        if (0..=total as i32).contains(&position) {
            grid[position as usize] = *digit;
        }
    }
    let round_up = grid[total] >= 5;
    grid.truncate(total);
    if round_up {
        let mut index = total;
        loop {
            if index == 0 {
                grid.insert(0, 1);
                break;
            }
            index -= 1;
            if grid[index] == 9 {
                grid[index] = 0;
            } else {
                grid[index] += 1;
                break;
            }
        }
    }
    let int_digits = grid.len() - decimals;
    let mut text = String::with_capacity(grid.len() + 1);
    for (index, digit) in grid.iter().enumerate() {
        if index == int_digits {
            text.push('.');
        }
        text.push((b'0' + digit) as char);
    }
    text
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fixed_rounds_exact_ties_to_even_like_net() {
        assert_eq!(fixed(0.125, 2), "0.12");
        assert_eq!(fixed(0.375, 2), "0.38");
        assert_eq!(fixed(0.625, 2), "0.62");
        assert_eq!(fixed(2.5, 0), "2");
        assert_eq!(fixed(3.5, 0), "4");
        assert_eq!(fixed(0.5, 0), "0");
        assert_eq!(fixed(1.005, 2), "1.00");
        assert_eq!(fixed(12.345, 2), "12.35");
        assert_eq!(fixed(-0.5, 0), "-0");
        assert_eq!(fixed(-0.0004, 2), "-0.00");
    }

    #[test]
    fn custom_rounds_half_away_after_fifteen_digits() {
        assert_eq!(custom(0.125, 2), "0.13");
        assert_eq!(custom(1.005, 2), "1.01");
        assert_eq!(custom(0.285, 2), "0.29");
        assert_eq!(custom(0.995, 2), "1.00");
        assert_eq!(custom(2.5, 0), "3");
        assert_eq!(custom(0.25, 1), "0.3");
        assert_eq!(custom(0.0, 2), "0.00");
        assert_eq!(custom(9.996, 2), "10.00");
        assert_eq!(custom(0.00125, 2), "0.00");
        assert_eq!(custom(123456789.125, 2), "123456789.13");
        assert_eq!(custom(0.9950000047683716, 2), "1.00");
    }

    #[test]
    fn custom_optional_drops_trailing_zeros() {
        assert_eq!(custom_optional(12.0, 2), "12");
        assert_eq!(custom_optional(16.5, 2), "16.5");
        assert_eq!(custom_optional(12.345, 2), "12.35");
        assert_eq!(custom_optional(0.995, 2), "1");
    }

    #[test]
    fn signed_uses_the_zero_section_for_values_that_round_to_zero() {
        assert_eq!(signed(0.125, 3), "+0.125");
        assert_eq!(signed(-0.125, 3), "-0.125");
        assert_eq!(signed(0.4, 3), "+0.400");
        assert_eq!(signed(-0.0004, 3), "0.000");
        assert_eq!(signed(0.0004, 3), "0.000");
        assert_eq!(signed(0.0, 3), "0.000");
    }

    #[test]
    fn percent0_matches_en_us_p0() {
        assert_eq!(percent0(0.5), "50%");
        assert_eq!(percent0(0.125), "12%");
        assert_eq!(percent0(0.375), "38%");
        assert_eq!(percent0(0.625), "62%");
        assert_eq!(percent0(12.345), "1,235%");
        assert_eq!(percent0(1234.567), "123,457%");
        assert_eq!(percent0(0.995), "99%");
        assert_eq!(percent0(0.145), "14%");
        assert_eq!(percent0(-0.5), "-50%");
    }

    #[test]
    fn lap_time_truncates_ticks_and_milliseconds() {
        assert_eq!(lap_time(92.4189), "1:32.418");
        assert_eq!(lap_time(92.418), "1:32.418");
        assert_eq!(lap_time(59.9996), "59.999");
        assert_eq!(lap_time(61.0), "1:01.000");
        assert_eq!(lap_time(3725.5), "2:05.500");
        assert_eq!(lap_time(7325.5), "2:05.500");
        assert_eq!(lap_time(0.4), "0.400");
        assert_eq!(lap_time(30.8), "30.800");
        assert_eq!(lap_time(92.41799926757812), "1:32.417");
        assert_eq!(lap_time(3599.9999), "59:59.999");
    }

    #[test]
    fn whole_and_ceiling() {
        assert_eq!(ceiling(12.0001), "13");
        assert_eq!(ceiling(0.2), "1");
        assert_eq!(whole(25.0), "25");
        assert_eq!(whole(3.0), "3");
    }
}
