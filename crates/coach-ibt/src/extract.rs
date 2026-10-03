//! Typed extraction from the session-info tree with `ibt_reader`'s conversions
//! (`_as_mapping`, `_as_list`, `_as_int`, `_as_bool`, `_as_text`, ibt_reader.py:562-602).

use std::borrow::Cow;
use std::collections::BTreeMap;

use crate::IbtError;
use crate::py;
use crate::yaml::{self, YamlNode, YamlScalar, dict_view, map_get};

/// A node seen as the Python value `_parse_session_info` would hold.
#[derive(Debug, Clone)]
pub(crate) enum Val<'a> {
    None,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(Cow<'a, str>),
    Map(&'a [(String, YamlNode)]),
    List(&'a [YamlNode]),
}

fn scalar_val(scalar: YamlScalar) -> Val<'static> {
    match scalar {
        YamlScalar::Null => Val::None,
        YamlScalar::Bool(value) => Val::Bool(value),
        YamlScalar::Int(value) => Val::Int(value),
        YamlScalar::Float(value) => Val::Float(value),
        YamlScalar::Str(value) => Val::Str(Cow::Owned(value)),
    }
}

pub(crate) fn val(node: Option<&YamlNode>) -> Val<'_> {
    match node {
        None | Some(YamlNode::Null) => Val::None,
        Some(YamlNode::Scalar(scalar)) => scalar_val(scalar.typed()),
        Some(YamlNode::Literal(scalar)) => scalar_val(scalar.clone()),
        Some(YamlNode::Map(entries)) => Val::Map(entries),
        Some(YamlNode::List(items)) => Val::List(items),
    }
}

/// `_as_mapping(value)`: the entries of a mapping, else none.
pub(crate) fn as_mapping<'a>(value: &Val<'a>) -> &'a [(String, YamlNode)] {
    match value {
        Val::Map(entries) => entries,
        _ => &[],
    }
}

pub(crate) fn as_list<'a>(value: &Val<'a>) -> &'a [YamlNode] {
    match value {
        Val::List(items) => items,
        _ => &[],
    }
}

/// `mapping.get(key)` on a mapping value.
pub(crate) fn get<'a>(entries: &'a [(String, YamlNode)], key: &str) -> Val<'a> {
    val(map_get(entries, key))
}

/// `_as_int`. Python returns integers beyond i64 (from an integral float or digit text); here
/// they are `None`.
pub(crate) fn as_int(value: &Val<'_>) -> Option<i64> {
    match value {
        Val::Bool(value) => Some(i64::from(*value)),
        Val::Int(value) => Some(*value),
        Val::Float(value) if value.is_finite() && value.fract() == 0.0 => {
            py::integral_to_i64(*value)
        }
        Val::Str(text) => py::parse_int(text),
        _ => None,
    }
}

/// `_as_bool`.
pub(crate) fn as_bool(value: &Val<'_>) -> Option<bool> {
    match value {
        Val::Bool(value) => Some(*value),
        Val::Int(value) => Some(*value != 0),
        Val::Float(value) => Some(*value != 0.0),
        Val::Str(text) => match py::strip(text).to_lowercase().as_str() {
            "1" | "true" | "yes" | "on" => Some(true),
            "0" | "false" | "no" | "off" => Some(false),
            _ => None,
        },
        _ => None,
    }
}

/// `_as_text`: None stays None; everything else is Python `str()`.
pub(crate) fn as_text(value: &Val<'_>) -> Option<String> {
    match value {
        Val::None => None,
        Val::Str(text) => Some(text.to_string()),
        other => Some(py_str(other)),
    }
}

/// Python `str()` of a value; containers use `repr` of their items.
pub(crate) fn py_str(value: &Val<'_>) -> String {
    match value {
        Val::Str(text) => text.to_string(),
        other => py_repr(other),
    }
}

fn py_repr(value: &Val<'_>) -> String {
    match value {
        Val::None => "None".to_owned(),
        Val::Bool(true) => "True".to_owned(),
        Val::Bool(false) => "False".to_owned(),
        Val::Int(value) => value.to_string(),
        Val::Float(value) => py::float_repr(*value),
        Val::Str(text) => py::str_repr(text),
        Val::List(items) => {
            let parts: Vec<String> = items.iter().map(|item| py_repr(&val(Some(item)))).collect();
            format!("[{}]", parts.join(", "))
        }
        Val::Map(entries) => {
            let parts: Vec<String> = dict_view(entries)
                .into_iter()
                .map(|(key, node)| format!("{}: {}", py::str_repr(key), py_repr(&val(Some(node)))))
                .collect();
            format!("{{{}}}", parts.join(", "))
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct SessionInfo {
    pub root: YamlNode,
}

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct WeekendInfo {
    pub track_name: Option<String>,
    pub track_display_name: Option<String>,
    pub track_config_name: Option<String>,
    pub track_id: Option<i64>,
    /// As published, e.g. "1.06 km".
    pub track_length: Option<String>,
    pub series_id: Option<i64>,
    pub season_id: Option<i64>,
    pub session_id: Option<i64>,
    /// 0 for offline sessions, kept as 0.
    pub subsession_id: Option<i64>,
    pub race_week: Option<i64>,
    pub event_type: Option<String>,
    pub category: Option<String>,
    pub official: Option<bool>,
    /// `WeekendOptions.IsFixedSetup`.
    pub is_fixed_setup: Option<bool>,
}

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct SessionEntry {
    /// `Sessions[].SessionNum`.
    pub session_num: Option<i64>,
    /// "Race", "Qualify", "Practice", "Offline Testing", ...
    pub session_type: Option<String>,
    pub session_name: Option<String>,
}

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct PlayerDriver {
    pub car_idx: i64,
    /// customer_id.
    pub user_id: Option<i64>,
    pub user_name: Option<String>,
    /// `IRating`.
    pub irating: Option<i64>,
    pub car_id: Option<i64>,
    pub car_path: Option<String>,
    pub car_screen_name: Option<String>,
}

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct DriverSetup {
    /// `DriverInfo.DriverSetupName`.
    pub name: Option<String>,
    /// `DriverSetupIsModified`, else `DriverSetupModified`.
    pub is_modified: Option<bool>,
    /// `DriverSetupLoadTypeName` (user, iracing, fixed).
    pub load_type_name: Option<String>,
    /// `DriverSetupLoadType`.
    pub load_type: Option<i64>,
}

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct CarSetup {
    /// `CarSetup.UpdateCount`.
    pub update_count: Option<i64>,
    /// Every scalar leaf under `CarSetup`, key = dotted path (e.g. `Chassis.LeftFront.Camber`,
    /// list items by index), value = `Scalar::text()` (e.g. "-2.8 deg") or, for a literal leaf,
    /// Python `str()` of its value. Null leaves are omitted. Includes `UpdateCount` and derived
    /// keys; this is the wire `SetupValues` map.
    pub values: BTreeMap<String, String>,
}

/// `DriverInfo.DriverSetupIsModified`, falling back to `DriverSetupModified` when the first is
/// absent or null (`setup_recordings.recorded_setup_identity`, worker-interface.md section 5).
pub(crate) fn setup_modified(driver_info: &[(String, YamlNode)]) -> Option<bool> {
    let mut value = get(driver_info, "DriverSetupIsModified");
    if matches!(value, Val::None) {
        value = get(driver_info, "DriverSetupModified");
    }
    as_bool(&value)
}

fn collect_leaves(prefix: &str, node: &YamlNode, out: &mut BTreeMap<String, String>) {
    let join = |segment: &str| {
        if prefix.is_empty() {
            segment.to_owned()
        } else {
            format!("{prefix}.{segment}")
        }
    };
    match node {
        YamlNode::Map(entries) => {
            for (key, child) in dict_view(entries) {
                collect_leaves(&join(key), child, out);
            }
        }
        YamlNode::List(items) => {
            for (index, child) in items.iter().enumerate() {
                collect_leaves(&join(&index.to_string()), child, out);
            }
        }
        YamlNode::Scalar(scalar) => {
            if scalar.typed() != YamlScalar::Null {
                out.insert(prefix.to_owned(), scalar.text().to_owned());
            }
        }
        YamlNode::Literal(YamlScalar::Null) | YamlNode::Null => {}
        YamlNode::Literal(scalar) => {
            out.insert(prefix.to_owned(), py_str(&scalar_val(scalar.clone())));
        }
    }
}

impl SessionInfo {
    /// The in-house YAML subset of `_parse_iracing_yaml_subset`; empty text -> empty map.
    pub fn parse(text: &str) -> Result<SessionInfo, IbtError> {
        Ok(SessionInfo {
            root: yaml::parse(text)?,
        })
    }

    /// Dotted lookup with list indices, e.g. `WeekendInfo.WeekendOptions.IsFixedSetup`.
    pub fn get(&self, path: &str) -> Option<&YamlNode> {
        self.root.lookup(path)
    }

    pub(crate) fn section(&self, key: &str) -> &[(String, YamlNode)] {
        match &self.root {
            YamlNode::Map(entries) => as_mapping(&get(entries, key)),
            _ => &[],
        }
    }

    pub fn weekend(&self) -> WeekendInfo {
        let weekend = self.section("WeekendInfo");
        let options = get(weekend, "WeekendOptions");
        WeekendInfo {
            track_name: as_text(&get(weekend, "TrackName")),
            track_display_name: as_text(&get(weekend, "TrackDisplayName")),
            track_config_name: as_text(&get(weekend, "TrackConfigName")),
            track_id: as_int(&get(weekend, "TrackID")),
            track_length: as_text(&get(weekend, "TrackLength")),
            series_id: as_int(&get(weekend, "SeriesID")),
            season_id: as_int(&get(weekend, "SeasonID")),
            session_id: as_int(&get(weekend, "SessionID")),
            subsession_id: as_int(&get(weekend, "SubSessionID")),
            race_week: as_int(&get(weekend, "RaceWeek")),
            event_type: as_text(&get(weekend, "EventType")),
            category: as_text(&get(weekend, "Category")),
            official: as_bool(&get(weekend, "Official")),
            is_fixed_setup: as_bool(&get(as_mapping(&options), "IsFixedSetup")),
        }
    }

    /// One entry per `SessionInfo.Sessions[]` item; a non-mapping item gives an all-`None` entry.
    pub fn sessions(&self) -> Vec<SessionEntry> {
        as_list(&get(self.section("SessionInfo"), "Sessions"))
            .iter()
            .map(|item| {
                let item = val(Some(item));
                let entry = as_mapping(&item);
                SessionEntry {
                    session_num: as_int(&get(entry, "SessionNum")),
                    session_type: as_text(&get(entry, "SessionType")),
                    session_name: as_text(&get(entry, "SessionName")),
                }
            })
            .collect()
    }

    /// The first `DriverInfo.Drivers[]` entry whose `CarIdx` equals `DriverInfo.DriverCarIdx`;
    /// `None` when either is unknown.
    pub fn driver(&self) -> Option<PlayerDriver> {
        let driver_info = self.section("DriverInfo");
        let car_idx = as_int(&get(driver_info, "DriverCarIdx"))?;
        as_list(&get(driver_info, "Drivers"))
            .iter()
            .find_map(|candidate| {
                let candidate = val(Some(candidate));
                let entry = as_mapping(&candidate);
                (as_int(&get(entry, "CarIdx")) == Some(car_idx)).then(|| PlayerDriver {
                    car_idx,
                    user_id: as_int(&get(entry, "UserID")),
                    user_name: as_text(&get(entry, "UserName")),
                    irating: as_int(&get(entry, "IRating")),
                    car_id: as_int(&get(entry, "CarID")),
                    car_path: as_text(&get(entry, "CarPath")),
                    car_screen_name: as_text(&get(entry, "CarScreenName")),
                })
            })
    }

    pub fn driver_setup(&self) -> DriverSetup {
        let driver_info = self.section("DriverInfo");
        DriverSetup {
            name: as_text(&get(driver_info, "DriverSetupName")),
            is_modified: setup_modified(driver_info),
            load_type_name: as_text(&get(driver_info, "DriverSetupLoadTypeName")),
            load_type: as_int(&get(driver_info, "DriverSetupLoadType")),
        }
    }

    /// `None` when there is no `CarSetup` map.
    pub fn car_setup(&self) -> Option<CarSetup> {
        let YamlNode::Map(root) = &self.root else {
            return None;
        };
        let node = map_get(root, "CarSetup")?;
        let YamlNode::Map(entries) = node else {
            return None;
        };
        let mut values = BTreeMap::new();
        collect_leaves("", node, &mut values);
        Some(CarSetup {
            update_count: as_int(&get(entries, "UpdateCount")),
            values,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const TEXT: &str = "\
WeekendInfo:
 TrackName: irp
 TrackID: 15.0
 SubSessionID: 0
 Official: 0
 WeekendOptions: {'IsFixedSetup': False}
SessionInfo:
 Sessions:
 - SessionNum: 0
   SessionType: Offline Testing
 - plain
DriverInfo:
 DriverCarIdx: 1
 DriverSetupName: base.sto
 DriverSetupModified: 0
 DriverSetupIsModified: 1
 DriverSetupLoadTypeName: user
 Drivers:
 - CarIdx: 0
   UserID: 5
 - CarIdx: 1
   UserID: 123
   UserName: \"A B\"
   IRating: 1350
   CarPath: supra
CarSetup:
 UpdateCount: 3
 Tires:
  LeftFront:
   ColdPressure: 25.0 psi
   Quoted: \"1.50\"
   Missing: ~
   Flow: [1, 'x', None]
 Chassis:
  Front:
   ArbBlades: 1.50
";

    #[test]
    fn extracts_follow_python_conversions() {
        let info = SessionInfo::parse(TEXT).expect("parses");
        let weekend = info.weekend();
        assert_eq!(weekend.track_id, Some(15));
        assert_eq!(weekend.subsession_id, Some(0));
        assert_eq!(weekend.official, Some(false));
        assert_eq!(weekend.is_fixed_setup, Some(false));
        let sessions = info.sessions();
        assert_eq!(sessions.len(), 2);
        assert_eq!(sessions[0].session_type.as_deref(), Some("Offline Testing"));
        assert_eq!(sessions[1].session_num, None);
        let driver = info.driver().expect("player");
        assert_eq!(
            (driver.user_id, driver.user_name.as_deref()),
            (Some(123), Some("A B"))
        );
        assert_eq!(driver.irating, Some(1350));
        let setup = info.driver_setup();
        assert_eq!(setup.is_modified, Some(true));
        assert_eq!(setup.load_type_name.as_deref(), Some("user"));
        let car = info.car_setup().expect("car setup");
        assert_eq!(car.update_count, Some(3));
        let values: Vec<(&str, &str)> = car
            .values
            .iter()
            .map(|(k, v)| (k.as_str(), v.as_str()))
            .collect();
        assert_eq!(
            values,
            [
                ("Chassis.Front.ArbBlades", "1.50"),
                ("Tires.LeftFront.ColdPressure", "25.0 psi"),
                ("Tires.LeftFront.Flow.0", "1"),
                ("Tires.LeftFront.Flow.1", "x"),
                ("Tires.LeftFront.Quoted", "1.50"),
                ("UpdateCount", "3"),
            ]
        );
    }

    #[test]
    fn modified_falls_back_only_when_absent_or_null() {
        let info =
            SessionInfo::parse("DriverInfo:\n DriverSetupIsModified:\n DriverSetupModified: 1\n")
                .expect("parses");
        assert_eq!(info.driver_setup().is_modified, Some(true));
        let info = SessionInfo::parse(
            "DriverInfo:\n DriverSetupIsModified: maybe\n DriverSetupModified: 1\n",
        )
        .expect("parses");
        assert_eq!(info.driver_setup().is_modified, None);
    }

    #[test]
    fn text_of_containers_is_python_str() {
        let info =
            SessionInfo::parse("A:\n B: [1, 'it''s', 2.0]\n C:\n  - x: 1\n").expect("parses");
        let a = info.section("A");
        assert_eq!(as_text(&get(a, "B")).as_deref(), Some("[1, 'its', 2.0]"));
        assert_eq!(as_text(&get(a, "C")).as_deref(), Some("[{'x': 1}]"));
    }
}
