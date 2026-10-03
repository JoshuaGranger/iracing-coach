//! Global hotkey text (`ctrl+shift+L`), parsed with the rules of the retired monitor: parts joined
//! by `+`, modifiers `ctrl`/`control`, `shift`, `alt` only, at least two parts, the key is one
//! letter or digit or F1 to F24.

pub const MOD_ALT: u32 = 0x0001;
pub const MOD_CONTROL: u32 = 0x0002;
pub const MOD_SHIFT: u32 = 0x0004;

/// `(modifiers, virtual key code)` for `text`, or None when it does not follow the rules.
pub fn parse_hotkey(text: &str) -> Option<(u32, u32)> {
    let parts: Vec<&str> = text
        .split('+')
        .map(str::trim)
        .filter(|p| !p.is_empty())
        .collect();
    if parts.len() < 2 {
        return None;
    }
    let (key, mods) = parts.split_last()?;
    let mut modifiers = 0;
    for m in mods {
        modifiers |= match m.to_ascii_lowercase().as_str() {
            "ctrl" | "control" => MOD_CONTROL,
            "shift" => MOD_SHIFT,
            "alt" => MOD_ALT,
            _ => return None,
        };
    }
    if modifiers == 0 {
        return None;
    }
    let upper = key.to_ascii_uppercase();
    let vk = if upper.len() == 1 {
        let c = upper.as_bytes()[0];
        c.is_ascii_alphanumeric().then_some(u32::from(c))?
    } else {
        let n: u32 = upper.strip_prefix('F')?.parse().ok()?;
        (1..=24).contains(&n).then_some(0x6F + n)?
    };
    Some((modifiers, vk))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_letters_digits_and_function_keys() {
        assert_eq!(
            parse_hotkey("ctrl+shift+L"),
            Some((MOD_CONTROL | MOD_SHIFT, u32::from(b'L')))
        );
        assert_eq!(parse_hotkey("Alt + 7"), Some((MOD_ALT, u32::from(b'7'))));
        assert_eq!(parse_hotkey("control+F12"), Some((MOD_CONTROL, 0x7B)));
        assert_eq!(parse_hotkey("ctrl+F24"), Some((MOD_CONTROL, 0x87)));
    }

    #[test]
    fn rejects_everything_else() {
        for bad in [
            "", "L", "ctrl", "ctrl+", "win+L", "ctrl+LL", "ctrl+F25", "ctrl+F0", "L+ctrl", "+L",
        ] {
            assert_eq!(parse_hotkey(bad), None, "{bad}");
        }
    }
}
