//! Session context: what the live engine needs from the session-info YAML (`IRacingSdkTelemetrySource`
//! `ReadSessionInfoIfChanged`, `LiveSteering.cs`).

use coach_ibt::{SessionInfo, YamlNode};

/// Built by the collector whenever `SdkHeader::session_info_update` changes, shared with every
/// following sample.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct SessionContext {
    pub session_info_update: i32,
    /// `WeekendInfo.SubSessionID`.
    pub subsession_id: Option<i64>,
    /// `Sessions[]`: (SessionNum, SessionType), entries with a type only.
    pub session_types: Vec<(i64, String)>,
    /// `LiveSteering` rules.
    pub steering_ratio: Option<f64>,
    /// `DriverInfo.DriverCarIdx`.
    pub driver_car_idx: Option<i64>,
    /// The player's `UserID` (the stream session ends when it changes).
    pub driver_user_id: Option<i64>,
    pub driver_user_name: Option<String>,
    pub car_path: Option<String>,
    /// `TrackDisplayName` else `TrackName`.
    pub track: Option<String>,
    pub track_config: Option<String>,
}

pub(crate) const MINIMUM_RATIO: f64 = 4.0;
pub(crate) const MAXIMUM_RATIO: f64 = 40.0;
/// Wheel-angle thresholds become road-wheel thresholds divided by this, as in the backend.
pub(crate) const THRESHOLD_REFERENCE_RATIO: f64 = 12.0;

impl SessionContext {
    /// Steering ratio per `LiveSteering.cs:22-58`; every other field from the session info.
    pub fn from_session_info(info: &SessionInfo, session_info_update: i32) -> SessionContext {
        let root = &info.root;
        let weekend = child(root, "WeekendInfo");
        let driver_info = child(root, "DriverInfo");

        let subsession_id = weekend
            .and_then(|node| child(node, "SubSessionID"))
            .and_then(int_value)
            .filter(|id| *id >= 0);

        let mut session_types: Vec<(i64, String)> = Vec::new();
        if let Some(YamlNode::List(sessions)) =
            child(root, "SessionInfo").and_then(|node| child(node, "Sessions"))
        {
            for session in sessions {
                let number = child(session, "SessionNum").and_then(int_value);
                let kind = child(session, "SessionType").and_then(text_value);
                if let (Some(number), Some(kind)) = (number, kind)
                    && !kind.is_empty()
                    && kind != "~"
                {
                    match session_types.iter_mut().find(|(n, _)| *n == number) {
                        Some(existing) => existing.1 = kind,
                        None => session_types.push((number, kind)),
                    }
                }
            }
        }

        let driver_car_idx = driver_info
            .and_then(|node| child(node, "DriverCarIdx"))
            .and_then(int_value);
        let player = driver_car_idx.and_then(|index| {
            let YamlNode::List(drivers) = driver_info.and_then(|node| child(node, "Drivers"))?
            else {
                return None;
            };
            drivers
                .iter()
                .find(|driver| child(driver, "CarIdx").and_then(int_value) == Some(index))
        });

        let steering_ratio = child(root, "CarSetup").and_then(ratio_from_car_setup);

        SessionContext {
            session_info_update,
            subsession_id,
            session_types,
            steering_ratio,
            driver_car_idx,
            driver_user_id: player.and_then(|p| child(p, "UserID")).and_then(int_value),
            driver_user_name: player
                .and_then(|p| child(p, "UserName"))
                .and_then(text_value),
            car_path: player
                .and_then(|p| child(p, "CarPath"))
                .and_then(text_value),
            track: weekend.and_then(|node| {
                child(node, "TrackDisplayName")
                    .and_then(text_value)
                    .or_else(|| child(node, "TrackName").and_then(text_value))
            }),
            track_config: weekend
                .and_then(|node| child(node, "TrackConfigName"))
                .and_then(text_value),
        }
    }

    /// The `SessionType` text for a session number, if the session info states one.
    pub fn session_type(&self, session_num: i64) -> Option<&str> {
        self.session_types
            .iter()
            .find(|(number, _)| *number == session_num)
            .map(|(_, kind)| kind.as_str())
    }
}

/// The value of `key` in a mapping node; a repeated key resolves to its last entry.
fn child<'a>(node: &'a YamlNode, key: &str) -> Option<&'a YamlNode> {
    match node {
        YamlNode::Map(entries) => entries.iter().rev().find(|(k, _)| k == key).map(|(_, v)| v),
        _ => None,
    }
}

/// Published text of a scalar: trimmed, without one pair of matching quotes; `None` for a null.
fn text_value(node: &YamlNode) -> Option<String> {
    match node {
        YamlNode::Scalar(scalar) => {
            let raw = scalar.raw.trim();
            Some(strip_quotes(raw).to_string())
        }
        _ => None,
    }
}

fn int_value(node: &YamlNode) -> Option<i64> {
    match node {
        YamlNode::Scalar(scalar) => strip_quotes(scalar.raw.trim()).trim().parse::<i64>().ok(),
        _ => None,
    }
}

fn strip_quotes(text: &str) -> &str {
    let bytes = text.as_bytes();
    if bytes.len() >= 2
        && (bytes[0] == b'"' || bytes[0] == b'\'')
        && bytes[bytes.len() - 1] == bytes[0]
    {
        &text[1..text.len() - 1]
    } else {
        text
    }
}

/// The one steering ratio a `CarSetup` mapping states, or `None` (absent, unreadable, or stated
/// twice differently). Every key whose letters and digits spell `SteeringRatio`, in any case.
pub(crate) fn ratio_from_car_setup(car_setup: &YamlNode) -> Option<f64> {
    let mut ratios: Vec<f64> = Vec::new();
    if collect_ratios(car_setup, &mut ratios) {
        return None;
    }
    let first = *ratios.first()?;
    ratios.iter().all(|ratio| *ratio == first).then_some(first)
}

/// Walks the tree; returns true when a `SteeringRatio` value could not be read.
fn collect_ratios(node: &YamlNode, ratios: &mut Vec<f64>) -> bool {
    match node {
        YamlNode::Map(entries) => {
            for (key, value) in entries {
                if is_steering_ratio_key(key) {
                    let parsed = match value {
                        YamlNode::Scalar(scalar) => parse_ratio(&scalar.raw),
                        _ => None,
                    };
                    match parsed {
                        Some(ratio) => ratios.push(ratio),
                        None => return true,
                    }
                } else if collect_ratios(value, ratios) {
                    return true;
                }
            }
            false
        }
        YamlNode::List(items) => items.iter().any(|item| collect_ratios(item, ratios)),
        _ => false,
    }
}

fn is_steering_ratio_key(key: &str) -> bool {
    !key.is_empty()
        && key
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b' ' || b == b'_')
        && key
            .bytes()
            .filter(u8::is_ascii_alphanumeric)
            .map(|b| b.to_ascii_lowercase())
            .eq(*b"steeringratio")
}

/// `LiveSteering.ParseRatio`: `n:1`, `n:1.0` or a bare number, optional matching quotes, in [4, 40].
pub(crate) fn parse_ratio(value: &str) -> Option<f64> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return None;
    }
    let text = strip_quotes(trimmed);
    let number = ratio_text(text).or_else(|| number_text(text))?;
    let ratio: f64 = number.parse().ok()?;
    (ratio.is_finite() && (MINIMUM_RATIO..=MAXIMUM_RATIO).contains(&ratio)).then_some(ratio)
}

/// `^\s*(\d+(?:\.\d+)?)\s*:\s*1(?:\.0+)?\s*$`
fn ratio_text(text: &str) -> Option<&str> {
    let text = text.trim_start();
    let (number, rest) = leading_number(text)?;
    let rest = rest
        .trim_start()
        .strip_prefix(':')?
        .trim_start()
        .strip_prefix('1')?;
    let rest = match rest.strip_prefix('.') {
        Some(zeros) => {
            let trimmed = zeros.trim_start_matches('0');
            if trimmed.len() == zeros.len() {
                return None;
            }
            trimmed
        }
        None => rest,
    };
    rest.trim().is_empty().then_some(number)
}

/// `^\s*(\d+(?:\.\d+)?)\s*$`
fn number_text(text: &str) -> Option<&str> {
    let (number, rest) = leading_number(text.trim_start())?;
    rest.trim().is_empty().then_some(number)
}

/// `\d+(?:\.\d+)?` at the start of `text`.
fn leading_number(text: &str) -> Option<(&str, &str)> {
    let bytes = text.as_bytes();
    let mut end = 0;
    while end < bytes.len() && bytes[end].is_ascii_digit() {
        end += 1;
    }
    if end == 0 {
        return None;
    }
    if end < bytes.len() && bytes[end] == b'.' {
        let mut fraction = end + 1;
        while fraction < bytes.len() && bytes[fraction].is_ascii_digit() {
            fraction += 1;
        }
        if fraction > end + 1 {
            end = fraction;
        }
    }
    Some((&text[..end], &text[end..]))
}

/// Road-wheel angle: the wheel angle over the ratio, only for a finite wheel and a ratio above zero.
pub(crate) fn road_wheel_radians(wheel: Option<f64>, ratio: Option<f64>) -> Option<f64> {
    match (wheel, ratio) {
        (Some(wheel), Some(ratio)) if wheel.is_finite() && ratio > 0.0 => Some(wheel / ratio),
        _ => None,
    }
}

/// A near-centre wheel: 0.12 rad at the wheel, or 0.12/12 rad of road wheel when the ratio is known.
pub(crate) fn near_center(wheel: Option<f64>, ratio: Option<f64>) -> bool {
    if let Some(road) = road_wheel_radians(wheel, ratio) {
        return road.abs() < 0.12 / THRESHOLD_REFERENCE_RATIO;
    }
    wheel.is_some_and(|wheel| wheel.is_finite() && wheel.abs() < 0.12)
}

#[cfg(test)]
mod tests {
    use super::*;
    use coach_ibt::Scalar;

    fn scalar(text: &str) -> YamlNode {
        YamlNode::Scalar(Scalar {
            raw: text.to_string(),
        })
    }

    fn map(entries: Vec<(&str, YamlNode)>) -> YamlNode {
        YamlNode::Map(
            entries
                .into_iter()
                .map(|(k, v)| (k.to_string(), v))
                .collect(),
        )
    }

    #[test]
    fn ratio_parsing_follows_the_backend_rules() {
        let cases: [(&str, Option<f64>); 17] = [
            ("12:1", Some(12.0)),
            ("10:1", Some(10.0)),
            ("16.5:1", Some(16.5)),
            (" 12 : 1 ", Some(12.0)),
            ("\"12:1\"", Some(12.0)),
            ("14:1.0", Some(14.0)),
            ("12", Some(12.0)),
            ("12:2", None),
            ("1:12", None),
            ("0:1", None),
            ("-12:1", None),
            ("twelve", None),
            ("", None),
            ("3:1", None),
            ("41:1", None),
            ("12:1 turns", None),
            ("12:10", None),
        ];
        for (text, expected) in cases {
            assert_eq!(parse_ratio(text), expected, "{text:?}");
        }
        assert_eq!(parse_ratio("40:1"), Some(40.0));
        assert_eq!(parse_ratio("4"), Some(4.0));
        assert_eq!(parse_ratio("'12:1'"), Some(12.0));
        assert_eq!(parse_ratio("12:1.5"), None);
    }

    fn supra(ratio: &str) -> YamlNode {
        map(vec![
            ("UpdateCount", scalar("10")),
            (
                "Chassis",
                map(vec![
                    (
                        "Front",
                        map(vec![
                            ("BallastForward", scalar("914 mm")),
                            ("SteeringRatio", scalar(ratio)),
                            ("SteeringOffset", scalar("+6 deg")),
                        ]),
                    ),
                    ("Rear", map(vec![("RearEndRatio", scalar("5.5"))])),
                ]),
            ),
        ])
    }

    #[test]
    fn ratio_comes_from_the_car_setup_block_only() {
        assert_eq!(ratio_from_car_setup(&supra("12:1")), Some(12.0));
        // absent: not guessed
        let absent = map(vec![(
            "Chassis",
            map(vec![(
                "Front",
                map(vec![("BallastForward", scalar("914 mm"))]),
            )]),
        )]);
        assert_eq!(ratio_from_car_setup(&absent), None);
        // unreadable: not guessed
        assert_eq!(ratio_from_car_setup(&supra("12:2")), None);
        // two different ratios: not guessed
        let two = map(vec![
            ("Front", map(vec![("SteeringRatio", scalar("12:1"))])),
            ("Rear", map(vec![("SteeringRatio", scalar("10:1"))])),
        ]);
        assert_eq!(ratio_from_car_setup(&two), None);
        // the same ratio twice: fine
        let same = map(vec![
            ("Front", map(vec![("SteeringRatio", scalar("12:1"))])),
            ("Rear", map(vec![("Steering Ratio", scalar("12"))])),
        ]);
        assert_eq!(ratio_from_car_setup(&same), Some(12.0));
        // a key with no value is unreadable
        let empty = map(vec![("SteeringRatio", YamlNode::Null)]);
        assert_eq!(ratio_from_car_setup(&empty), None);
    }

    #[test]
    fn steering_helpers() {
        let wheel = 71.53_f64.to_radians();
        let road = road_wheel_radians(Some(wheel), Some(12.0)).unwrap();
        assert!((road.to_degrees() - 71.53 / 12.0).abs() < 1e-9);
        assert_eq!(road_wheel_radians(Some(wheel), None), None);
        assert_eq!(road_wheel_radians(None, Some(12.0)), None);
        assert_eq!(road_wheel_radians(Some(f64::NAN), Some(12.0)), None);
        assert_eq!(road_wheel_radians(Some(wheel), Some(0.0)), None);
        assert!(near_center(Some(0.11), None));
        assert!(!near_center(Some(0.13), None));
        assert!(near_center(Some(0.11), Some(12.0)));
        assert!(!near_center(Some(0.11), Some(10.0)));
        assert!(!near_center(None, Some(12.0)));
        assert!(!near_center(Some(f64::NAN), None));
    }

    #[test]
    fn context_reads_the_session_info_tree() {
        let sessions = YamlNode::List(vec![
            map(vec![
                ("SessionNum", scalar("0")),
                ("SessionType", scalar("Practice")),
            ]),
            map(vec![
                ("SessionNum", scalar("1")),
                ("SessionType", scalar("\"Race\"")),
            ]),
            map(vec![
                ("SessionNum", scalar("2")),
                ("SessionType", scalar("~")),
            ]),
            map(vec![
                ("SessionNum", scalar("3")),
                ("SessionType", YamlNode::Null),
            ]),
            map(vec![
                ("SessionNum", scalar("1")),
                ("SessionType", scalar("Qualify")),
            ]),
        ]);
        let drivers = YamlNode::List(vec![
            map(vec![
                ("CarIdx", scalar("0")),
                ("UserID", scalar("11")),
                ("UserName", scalar("Other")),
            ]),
            map(vec![
                ("CarIdx", scalar("3")),
                ("UserID", scalar("321")),
                ("UserName", scalar("Joshua Granger")),
                ("CarPath", scalar("toyotagr86")),
            ]),
        ]);
        let root = map(vec![
            (
                "WeekendInfo",
                map(vec![
                    ("TrackName", scalar("irp")),
                    ("TrackDisplayName", scalar("Indianapolis Raceway Park")),
                    ("TrackConfigName", scalar("Road")),
                    ("SubSessionID", scalar("8001")),
                ]),
            ),
            ("SessionInfo", map(vec![("Sessions", sessions)])),
            (
                "DriverInfo",
                map(vec![("DriverCarIdx", scalar("3")), ("Drivers", drivers)]),
            ),
            ("CarSetup", supra("12:1")),
        ]);
        let ctx = SessionContext::from_session_info(&SessionInfo { root }, 7);
        assert_eq!(ctx.session_info_update, 7);
        assert_eq!(ctx.subsession_id, Some(8001));
        assert_eq!(ctx.session_type(0), Some("Practice"));
        assert_eq!(
            ctx.session_type(1),
            Some("Qualify"),
            "a repeated number resolves to its last type"
        );
        assert_eq!(ctx.session_type(2), None);
        assert_eq!(ctx.session_type(3), None);
        assert_eq!(ctx.steering_ratio, Some(12.0));
        assert_eq!(ctx.driver_car_idx, Some(3));
        assert_eq!(ctx.driver_user_id, Some(321));
        assert_eq!(ctx.driver_user_name.as_deref(), Some("Joshua Granger"));
        assert_eq!(ctx.car_path.as_deref(), Some("toyotagr86"));
        assert_eq!(ctx.track.as_deref(), Some("Indianapolis Raceway Park"));
        assert_eq!(ctx.track_config.as_deref(), Some("Road"));
    }

    #[test]
    fn context_of_an_empty_session_info_is_empty() {
        let ctx = SessionContext::from_session_info(
            &SessionInfo {
                root: YamlNode::Map(vec![]),
            },
            1,
        );
        assert_eq!(
            ctx,
            SessionContext {
                session_info_update: 1,
                ..SessionContext::default()
            }
        );
    }
}
