//! Hand-written matchers for the fixed token grammars the parser uses and for the
//! `setup-keys.json` pattern subset. No regular-expression engine: what this crate links reaches
//! the collector, whose exe budget is 3 MB (`crate-api.md` section 1). Each matcher states the
//! pattern whose language it accepts; every one is a whole-string match.

fn unsigned(text: &[u8]) -> &[u8] {
    match text.first() {
        Some(b'+' | b'-') => &text[1..],
        _ => text,
    }
}

fn all_digits(text: &[u8]) -> bool {
    text.iter().all(u8::is_ascii_digit)
}

/// `^[+-]?(?:0|[1-9][0-9]*)$` (`_yaml_scalar` integers).
pub(crate) fn is_yaml_integer(text: &str) -> bool {
    let digits = unsigned(text.as_bytes());
    match digits.first() {
        Some(b'0') => digits.len() == 1,
        Some(_) => all_digits(digits),
        None => false,
    }
}

/// `^[+-]?(?:(?:[0-9]+\.[0-9]*)|(?:[0-9]*\.[0-9]+)|(?:[0-9]+[eE][+-]?[0-9]+)|(?:[0-9]+\.[0-9]*[eE][+-]?[0-9]+))$`
/// (`_yaml_scalar` floats): an exponent needs digits before the point.
pub(crate) fn is_yaml_float(text: &str) -> bool {
    let body = unsigned(text.as_bytes());
    let (mantissa, exponent) = match body.iter().position(|&b| matches!(b, b'e' | b'E')) {
        Some(at) => (&body[..at], Some(&body[at + 1..])),
        None => (body, None),
    };
    let (whole, fraction) = match mantissa.iter().position(|&b| b == b'.') {
        Some(at) => (&mantissa[..at], Some(&mantissa[at + 1..])),
        None => (mantissa, None),
    };
    if !all_digits(whole) || !fraction.is_none_or(all_digits) {
        return false;
    }
    match exponent {
        None => fraction.is_some_and(|fraction| !whole.is_empty() || !fraction.is_empty()),
        Some(exponent) => {
            let digits = unsigned(exponent);
            !whole.is_empty() && !digits.is_empty() && all_digits(digits)
        }
    }
}

/// `D(?:_?D)*`, or `(?:_?D)+` when `leading_underscore`: digits with single underscores between
/// them (Python numeric literals).
fn digit_part(text: &[u8], digit: impl Fn(u8) -> bool, leading_underscore: bool) -> bool {
    if text.is_empty() {
        return false;
    }
    let mut after_underscore = !leading_underscore;
    for &byte in text {
        if byte == b'_' {
            if after_underscore {
                return false;
            }
            after_underscore = true;
        } else if digit(byte) {
            after_underscore = false;
        } else {
            return false;
        }
    }
    !after_underscore
}

fn decimal_part(text: &[u8]) -> bool {
    digit_part(text, |b| b.is_ascii_digit(), false)
}

/// `(?:DP)?\.DP|DP\.` with `DP = [0-9](?:_?[0-9])*`.
fn point_float(text: &[u8]) -> bool {
    let Some(at) = text.iter().position(|&b| b == b'.') else {
        return false;
    };
    let (whole, fraction) = (&text[..at], &text[at + 1..]);
    let part = |part: &[u8]| part.is_empty() || decimal_part(part);
    part(whole) && part(fraction) && !(whole.is_empty() && fraction.is_empty())
}

/// Python 3.12 integer token:
/// `^(?:[1-9](?:_?[0-9])*|0+(?:_?0)*|0[xX](?:_?[0-9a-fA-F])+|0[oO](?:_?[0-7])+|0[bB](?:_?[01])+)$`.
pub(crate) fn is_python_int(token: &str) -> bool {
    let text = token.as_bytes();
    match text {
        [b'0', b'x' | b'X', rest @ ..] => digit_part(rest, |b| b.is_ascii_hexdigit(), true),
        [b'0', b'o' | b'O', rest @ ..] => digit_part(rest, |b| matches!(b, b'0'..=b'7'), true),
        [b'0', b'b' | b'B', rest @ ..] => digit_part(rest, |b| matches!(b, b'0' | b'1'), true),
        [b'0', ..] => digit_part(text, |b| b == b'0', false),
        [b'1'..=b'9', ..] => decimal_part(text),
        _ => false,
    }
}

/// Python 3.12 float token: `^(?:POINT|(?:DP|POINT)[eE][+-]?DP)$` with `POINT` and `DP` as in
/// [`point_float`].
pub(crate) fn is_python_float(token: &str) -> bool {
    let text = token.as_bytes();
    match text.iter().position(|&b| matches!(b, b'e' | b'E')) {
        None => point_float(text),
        Some(at) => {
            let mantissa = &text[..at];
            (decimal_part(mantissa) || point_float(mantissa))
                && decimal_part(unsigned(&text[at + 1..]))
        }
    }
}

/// Python 3.12 imaginary token: `^(?:FLOAT|DP)[jJ]$`.
pub(crate) fn is_python_imaginary(token: &str) -> bool {
    match token.strip_suffix(['j', 'J']) {
        Some(number) => is_python_float(number) || decimal_part(number.as_bytes()),
        None => false,
    }
}

/// A `setup-keys.json` pattern: `^`, then ASCII letters and digits with at most one group
/// `(a|b|...)` of such non-empty literals, then an optional `$`. Matching folds ASCII case.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct LeafPattern {
    head: String,
    /// One empty entry when the pattern has no group.
    alternatives: Vec<String>,
    tail: String,
    whole: bool,
}

fn literal(text: &str) -> bool {
    text.bytes().all(|b| b.is_ascii_alphanumeric())
}

fn strip_prefix_folded<'a>(text: &'a [u8], prefix: &str) -> Option<&'a [u8]> {
    let prefix = prefix.as_bytes();
    text.get(..prefix.len())
        .filter(|head| head.eq_ignore_ascii_case(prefix))
        .map(|_| &text[prefix.len()..])
}

impl LeafPattern {
    /// None for any pattern outside the subset.
    pub(crate) fn parse(pattern: &str) -> Option<LeafPattern> {
        let body = pattern.strip_prefix('^')?;
        let (body, whole) = match body.strip_suffix('$') {
            Some(body) => (body, true),
            None => (body, false),
        };
        let (head, alternatives, tail) = match body.find('(') {
            None => (body, vec![String::new()], ""),
            Some(open) => {
                let close = open + body[open..].find(')')?;
                let alternatives: Vec<String> = body[open + 1..close]
                    .split('|')
                    .map(str::to_owned)
                    .collect();
                if alternatives.iter().any(|a| a.is_empty() || !literal(a)) {
                    return None;
                }
                (&body[..open], alternatives, &body[close + 1..])
            }
        };
        if !literal(head) || !literal(tail) || (head.is_empty() && alternatives == [""]) {
            return None;
        }
        Some(LeafPattern {
            head: head.to_owned(),
            alternatives,
            tail: tail.to_owned(),
            whole,
        })
    }

    pub(crate) fn matches(&self, leaf: &str) -> bool {
        let Some(rest) = strip_prefix_folded(leaf.as_bytes(), &self.head) else {
            return false;
        };
        self.alternatives.iter().any(|alternative| {
            strip_prefix_folded(rest, alternative)
                .and_then(|rest| strip_prefix_folded(rest, &self.tail))
                .is_some_and(|end| !self.whole || end.is_empty())
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn yaml_integers() {
        for yes in ["0", "+0", "-0", "7", "-12", "+1234567890123456789012"] {
            assert!(is_yaml_integer(yes), "{yes}");
        }
        for no in [
            "", "+", "-", "00", "01", "-007", "1.0", "1e3", "1_000", " 1", "1 ", "0x1", "١",
        ] {
            assert!(!is_yaml_integer(no), "{no}");
        }
    }

    #[test]
    fn yaml_floats() {
        for yes in [
            "1.", "1.5", ".5", "-.5", "+0.25", "1e3", "1E-3", "-2e+10", "1.e3", "1.5e3", "00.0",
        ] {
            assert!(is_yaml_float(yes), "{yes}");
        }
        for no in [
            "", ".", "-.", "1", "e3", ".5e3", "1e", "1e+", "1.5.2", "1e3e3", "1e3.0", "1_0.0",
            "1.0 ", "inf", "nan", "1.0f", "++1.0",
        ] {
            assert!(!is_yaml_float(no), "{no}");
        }
    }

    #[test]
    fn python_integers() {
        for yes in [
            "0", "000", "0_0", "1", "1_000", "123", "0x1F", "0X_ff", "0o17", "0O_7", "0b1",
            "0B_1_0",
        ] {
            assert!(is_python_int(yes), "{yes}");
        }
        for no in [
            "", "01", "0_1", "1_", "1__0", "_1", "0x", "0x_", "0x1_", "0x__1", "0o8", "0b2", "0xg",
            "1.0", "1e3", "1j",
        ] {
            assert!(!is_python_int(no), "{no}");
        }
    }

    #[test]
    fn python_floats_and_imaginaries() {
        for yes in [
            "1.", ".5", "1.5", "1_0.5_0", "1e3", "1_0e1_0", "1.e-3", ".5E+3", "007.5", "00e0",
        ] {
            assert!(is_python_float(yes), "{yes}");
        }
        for no in [
            "", ".", "1", "1_.5", "1._5", "1e", "1e_3", "1e3_", "e3", ".e3", "1.5.", "0x1.5", "1j",
        ] {
            assert!(!is_python_float(no), "{no}");
        }
        for yes in ["1j", "1J", "01j", "1_0j", "1.5j", ".5e3J"] {
            assert!(is_python_imaginary(yes), "{yes}");
        }
        for no in ["j", "1", "1jj", "0x1j", "1_j", ".j"] {
            assert!(!is_python_imaginary(no), "{no}");
        }
    }

    #[test]
    fn leaf_patterns_outside_the_subset_are_rejected() {
        for pattern in [
            "",
            "^",
            "^$",
            "CornerWeight",
            "CornerWeight$",
            "^Corner.*",
            "^Corner\\w+",
            "^a[bc]",
            "^(?i)a",
            "^a|b",
            "^(a|b)(c|d)",
            "^(a||b)",
            "^(|a)",
            "^()",
            "^(a",
            "^a)",
            "^)a(",
            "^((a))",
            "^a$$",
            "^Corner Weight",
            "^Ärger",
            "^a_b",
        ] {
            assert_eq!(LeafPattern::parse(pattern), None, "{pattern}");
        }
    }

    #[test]
    fn leaf_patterns_match_with_ascii_case_folding() {
        let pattern = LeafPattern::parse("^(Arb|TruckArm)Preload$").expect("in the subset");
        assert!(pattern.matches("ArbPreload"));
        assert!(pattern.matches("TRUCKARMPRELOAD"));
        assert!(!pattern.matches("ArbPreloadX"));
        assert!(!pattern.matches("Preload"));
        let prefix = LeafPattern::parse("^LastTemps").expect("in the subset");
        assert!(prefix.matches("lasttempsOMI"));
        assert!(prefix.matches("LastTemps"));
        assert!(!prefix.matches("LastTemp"));
        let open = LeafPattern::parse("^(A|AB)C").expect("in the subset");
        assert!(open.matches("ABC"));
        assert!(open.matches("ACX"));
        // ASCII folding only: the Kelvin sign is not `k`.
        let kelvin = LeafPattern::parse("^K$").expect("in the subset");
        assert!(kelvin.matches("k"));
        assert!(!kelvin.matches("\u{212A}"));
    }
}
