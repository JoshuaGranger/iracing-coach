//! Seams to the library crates. The server depends on `coach-ibt` and `coach-views` through
//! their contract APIs only; these traits let tests substitute fixtures while a crate is a stub.

use std::collections::BTreeMap;
use std::path::Path;

use serde_json::{Value, json};

use crate::util;

/// What ingest keeps from one recording (`recordings` row, driver, setup observation).
#[derive(Debug, Clone, Default)]
pub struct ParsedRecording {
    pub customer_id: Option<i64>,
    pub user_name: Option<String>,
    pub irating: Option<i64>,
    pub record_count: i64,
    pub tick_rate: i64,
    pub buffer_length: i64,
    pub variable_count: i64,
    pub session_info_sha256: String,
    pub subsession_id: Option<i64>,
    pub sim_session_num: Option<i64>,
    pub session_types: Vec<String>,
    pub sim_session_type: Option<String>,
    pub is_race: bool,
    pub track: Option<String>,
    pub track_config: Option<String>,
    pub car_path: Option<String>,
    pub car_name: Option<String>,
    pub series_name: Option<String>,
    pub season_name: Option<String>,
    pub start_time_utc: Option<String>,
    pub header_json: Value,
    pub setup: Option<ParsedSetup>,
}

#[derive(Debug, Clone)]
pub struct ParsedSetup {
    pub values: BTreeMap<String, String>,
    pub fingerprint: String,
    pub setup_name: Option<String>,
    pub is_modified: Option<bool>,
    pub load_type: Option<String>,
    pub update_count: Option<i64>,
}

pub trait RecordingParser: Send + Sync {
    /// Reads the header and session info of the recording at `path`. The error text becomes
    /// `recordings.ingest_error`.
    fn parse(&self, path: &Path) -> Result<ParsedRecording, String>;
    /// Setup values fingerprint (`setup-keys.json`), used for garage and export observations.
    fn fingerprint(&self, values: &BTreeMap<String, String>) -> String;
}

/// `coach-ibt` behind the trait.
pub struct IbtParser;

fn scalar_text(si: &coach_ibt::SessionInfo, path: &str) -> Option<String> {
    match si.get(path)? {
        coach_ibt::YamlNode::Scalar(s) => {
            let text = s.text().trim();
            (!text.is_empty()).then(|| text.to_string())
        }
        _ => None,
    }
}

/// The recording's last `Lap` sample as stored (`IbtFile::edge_scalar`); None when the file has
/// no integer `Lap` channel or no samples, or the sample cannot be read.
fn last_lap(file: &coach_ibt::IbtFile) -> Option<i64> {
    match file.edge_scalar("Lap").ok().flatten()? {
        coach_ibt::SdkScalar::Int(lap) => Some(i64::from(lap)),
        coach_ibt::SdkScalar::BitField(lap) => Some(i64::from(lap)),
        _ => None,
    }
}

impl RecordingParser for IbtParser {
    fn fingerprint(&self, values: &BTreeMap<String, String>) -> String {
        coach_ibt::setup_values_fingerprint(values)
    }

    fn parse(&self, path: &Path) -> Result<ParsedRecording, String> {
        let file = coach_ibt::IbtFile::open(path).map_err(|e| e.to_string())?;
        let summary = file.summary().map_err(|e| e.to_string())?;
        let info = file.session_info().map_err(|e| e.to_string())?;
        let driver = info.driver();
        let setup = info.car_setup().map(|car| {
            let driver_setup = info.driver_setup();
            ParsedSetup {
                fingerprint: coach_ibt::setup_values_fingerprint(&car.values),
                values: car.values,
                setup_name: driver_setup.name,
                is_modified: summary.driver_setup_modified.or(driver_setup.is_modified),
                load_type: driver_setup.load_type_name,
                update_count: car.update_count,
            }
        });
        let start_time_utc = summary
            .start_time_utc
            .as_deref()
            .and_then(util::parse_iso)
            .map(util::iso_from_unix);
        let session_types: Vec<String> = info
            .sessions()
            .into_iter()
            .filter_map(|s| s.session_type)
            .collect();
        let header_json = json!({
            "record_count": file.record_count,
            "tick_rate": file.header.tick_rate,
            "buffer_length": file.buffer_length,
            "variable_count": file.vars.len(),
            "session_start_date": file.disk.session_start_date,
            // the sim's SessionTime at the file's start and end: with the start date they give
            // the sim session's own start (`offline_sessions`)
            "session_start_time": file.disk.session_start_time,
            "session_end_time": file.disk.session_end_time,
            "session_lap_count": file.disk.session_lap_count,
            // the last `Lap` sample (null when the file has none): the sim stops counting laps
            // at 1024 in one sim session (`offline_sessions::lap_counter_stopped`)
            "lap_last": last_lap(&file),
            "file_size": file.file_size,
            "summary": serde_json::to_value(&summary).unwrap_or(Value::Null),
        });
        Ok(ParsedRecording {
            customer_id: summary
                .driver_user_id
                .or(driver.as_ref().and_then(|d| d.user_id)),
            user_name: driver.as_ref().and_then(|d| d.user_name.clone()),
            irating: driver.as_ref().and_then(|d| d.irating),
            record_count: file.record_count as i64,
            tick_rate: file.header.tick_rate as i64,
            buffer_length: file.buffer_length as i64,
            variable_count: file.vars.len() as i64,
            session_info_sha256: file.session_info_sha256(),
            subsession_id: summary.subsession_id,
            sim_session_num: summary.sim_session_num,
            session_types,
            sim_session_type: summary.sim_session_type.clone(),
            is_race: summary.is_race,
            track: summary
                .track_display_name
                .clone()
                .or(summary.track_name.clone()),
            track_config: summary.track_config_name.clone(),
            car_path: summary.car_path.clone(),
            car_name: summary.car_screen_name.clone(),
            series_name: scalar_text(&info, "WeekendInfo.SeriesName"),
            season_name: scalar_text(&info, "WeekendInfo.SeasonName"),
            start_time_utc,
            header_json,
            setup,
        })
    }
}

#[derive(Debug, Clone)]
pub struct ProjectionInfo {
    pub analysis_id: String,
    pub projection_version: u32,
    pub schema_version: i64,
    pub profile_version: String,
    pub lap_count: u32,
}

#[derive(Debug)]
pub enum SelectFailure {
    BadLaps(String),
    UnknownChannel(String),
    TooLarge { bytes: u64, limit: u64 },
    Corrupt(String),
    Io(String),
}

pub trait Projector: Send + Sync {
    fn version(&self) -> u32;
    /// Writes the view files into `out_dir` (which must not exist), atomically.
    fn project(&self, analysis_json: &Path, out_dir: &Path) -> Result<ProjectionInfo, String>;
    fn select_traces(
        &self,
        view_dir: &Path,
        laps: Option<&str>,
        channels: Option<&str>,
        max_bytes: u64,
    ) -> Result<Vec<u8>, SelectFailure>;
}

/// `coach-views` behind the trait.
pub struct ViewsProjector;

impl Projector for ViewsProjector {
    fn version(&self) -> u32 {
        coach_views::PROJECTION_VERSION
    }

    fn project(&self, analysis_json: &Path, out_dir: &Path) -> Result<ProjectionInfo, String> {
        let summary = coach_views::project(analysis_json, out_dir).map_err(|e| e.to_string())?;
        Ok(ProjectionInfo {
            analysis_id: summary.analysis_id,
            projection_version: summary.projection_version,
            schema_version: summary.schema_version,
            profile_version: summary.profile_version,
            lap_count: summary.lap_count,
        })
    }

    fn select_traces(
        &self,
        view_dir: &Path,
        laps: Option<&str>,
        channels: Option<&str>,
        max_bytes: u64,
    ) -> Result<Vec<u8>, SelectFailure> {
        use coach_views::SelectError;
        coach_views::select_traces(view_dir, laps, channels, max_bytes).map_err(|e| match e {
            SelectError::Io(e) => SelectFailure::Io(e.to_string()),
            SelectError::BadLaps(m) => SelectFailure::BadLaps(m),
            SelectError::UnknownChannel(c) => SelectFailure::UnknownChannel(c),
            SelectError::TooLarge { bytes, limit } => SelectFailure::TooLarge { bytes, limit },
            SelectError::Corrupt(m) => SelectFailure::Corrupt(m),
        })
    }
}
