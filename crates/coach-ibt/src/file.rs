//! IBT layout (`ibt_reader._read_layout`), edge reads and the recording summary.

use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::time::SystemTime;

use sha2::{Digest, Sha256};

use crate::extract::{
    SessionInfo, Val, as_bool, as_int, as_list, as_mapping, as_text, get, setup_modified, val,
};
use crate::header::{SdkHeader, SdkScalar, VarTable, read_f64, read_i32, read_i64};
use crate::py;
use crate::time::{parse_iso_epoch, safe_iso, unix_seconds};
use crate::{
    DISK_SUBHEADER_LEN, IbtError, MAX_IBT_SESSION_INFO_BYTES, MAX_TICK_RATE, MAX_VARS,
    SDK_HEADER_LEN, VAR_HEADER_LEN,
};

/// Fixed IBT headers: the SDK header and the disk subheader.
const MINIMUM: i64 = (SDK_HEADER_LEN + DISK_SUBHEADER_LEN) as i64;
/// `_MIN_REASONABLE_EPOCH` and `_MAX_REASONABLE_EPOCH` (2000-01-01 and 2100-01-01 UTC).
const MIN_REASONABLE_EPOCH: f64 = 946_684_800.0;
const MAX_REASONABLE_EPOCH: f64 = 4_102_444_800.0;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct DiskSubHeader {
    /// Unix seconds (wall clock of recording start).
    pub session_start_date: i64,
    /// SDK SessionTime at start, s.
    pub session_start_time: f64,
    /// SDK SessionTime at end, s.
    pub session_end_time: f64,
    pub session_lap_count: i32,
    /// Equals `record_count`.
    pub session_record_count: i32,
}

#[derive(Debug, Clone)]
pub struct IbtFile {
    pub path: PathBuf,
    pub file_size: u64,
    pub mtime: SystemTime,
    pub header: SdkHeader,
    pub disk: DiskSubHeader,
    pub vars: VarTable,
    /// `header.var_bufs[0].buf_offset`.
    pub buffer_offset: u64,
    /// `header.buf_len`.
    pub buffer_length: u32,
    /// `disk.session_record_count`.
    pub record_count: u32,
    /// Raw region `[session_info_offset, +len)`.
    pub session_info_bytes: Vec<u8>,
}

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct RecordingSummary {
    pub series_id: Option<i64>,
    pub season_id: Option<i64>,
    pub session_id: Option<i64>,
    /// 0 for offline sessions, kept as 0.
    pub subsession_id: Option<i64>,
    pub race_week: Option<i64>,
    pub event_type: Option<String>,
    pub category: Option<String>,
    pub official: Option<bool>,
    pub track_id: Option<i64>,
    pub track_name: Option<String>,
    pub track_display_name: Option<String>,
    pub track_config_name: Option<String>,
    /// As published, e.g. "1.06 km".
    pub track_length: Option<String>,
    pub is_fixed_setup: Option<bool>,
    /// SessionNum edge scalar, else the single Race session's.
    pub sim_session_num: Option<i64>,
    /// Matched `Sessions[].SessionType`.
    pub sim_session_type: Option<String>,
    pub session_unique_id: Option<i64>,
    pub is_race: bool,
    pub car_id: Option<i64>,
    pub car_path: Option<String>,
    pub car_screen_name: Option<String>,
    /// `Drivers[DriverCarIdx].UserID` = customer_id.
    pub driver_user_id: Option<i64>,
    pub driver_setup_name: Option<String>,
    pub driver_setup_modified: Option<bool>,
    /// Seconds.
    pub start_time_unix: f64,
    pub end_time_unix: f64,
    /// Python `_safe_iso`: `datetime.isoformat()` (`YYYY-MM-DDTHH:MM:SS[.ffffff]+00:00`), None
    /// when the epoch is non-finite or outside Python `datetime`.
    pub start_time_utc: Option<String>,
    pub end_time_utc: Option<String>,
    pub time_source: TimeSource,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum TimeSource {
    DiskHeader,
    SessionYaml,
    Mtime,
}

fn bounds(what: &'static str, detail: String) -> IbtError {
    IbtError::Bounds { what, detail }
}

/// `_checked_region`: negative offset or length -> `Bounds`; past the end -> `Truncated`.
fn checked_region(
    offset: i64,
    length: i64,
    file_size: u64,
    region: &'static str,
) -> Result<(u64, u64), IbtError> {
    if offset < 0 || length < 0 {
        return Err(bounds(
            region,
            format!("negative offset or length (offset={offset}, length={length})"),
        ));
    }
    let (start, end) = (offset as u64, offset as u64 + length as u64);
    if end > file_size {
        return Err(IbtError::Truncated {
            region,
            start,
            end,
            available: file_size,
        });
    }
    Ok((start, end))
}

/// `_read_exact_at`.
fn read_exact_at(
    file: &mut File,
    offset: i64,
    length: i64,
    file_size: u64,
    region: &'static str,
) -> Result<Vec<u8>, IbtError> {
    let (start, end) = checked_region(offset, length, file_size, region)?;
    file.seek(SeekFrom::Start(start))?;
    let mut buffer = vec![0u8; (end - start) as usize];
    let mut filled = 0;
    while filled < buffer.len() {
        match file.read(&mut buffer[filled..])? {
            0 => {
                return Err(IbtError::Truncated {
                    region,
                    start,
                    end,
                    available: start + filled as u64,
                });
            }
            read => filled += read,
        }
    }
    Ok(buffer)
}

/// `_regions_overlap`.
fn overlap(first: i64, first_len: i64, second: i64, second_len: i64) -> bool {
    first_len != 0 && second_len != 0 && first < second + second_len && second < first + first_len
}

/// Session-info text: bytes up to the first NUL; UTF-8 with an optional BOM stripped; on invalid
/// UTF-8 the whole block is cp1252 with Python's `errors="replace"`.
pub fn decode_session_info(bytes: &[u8]) -> String {
    let cut = bytes.split(|&b| b == 0).next().unwrap_or_default();
    match std::str::from_utf8(cut) {
        Ok(text) => text.strip_prefix('\u{FEFF}').unwrap_or(text).to_owned(),
        Err(_) => py::decode_cp1252(cut),
    }
}

impl IbtFile {
    /// Port of `ibt_reader._read_layout` bounds and order (ibt_reader.py:829-1060), without YAML
    /// parsing. Reads no sample rows.
    pub fn open(path: &Path) -> Result<IbtFile, IbtError> {
        // Python checks the path before opening it: a directory is a format error, not I/O.
        if !std::fs::metadata(path)?.is_file() {
            return Err(IbtError::Format(format!(
                "{} is not a regular file",
                path.display()
            )));
        }
        let mut file = File::open(path)?;
        let metadata = file.metadata()?;
        let file_size = metadata.len();
        let mtime = metadata.modified()?;
        checked_region(0, MINIMUM, file_size, "IBT header")?;
        let header_bytes =
            read_exact_at(&mut file, 0, SDK_HEADER_LEN as i64, file_size, "SDK header")?;
        let header = SdkHeader::parse(&header_bytes)?;

        if header.ver <= 0 || header.ver > 1_000 {
            return Err(IbtError::Format(format!(
                "implausible SDK header version {}",
                header.ver
            )));
        }
        if header.tick_rate <= 0 || header.tick_rate > MAX_TICK_RATE {
            return Err(IbtError::Format(format!(
                "invalid tick rate {} Hz",
                header.tick_rate
            )));
        }
        if header.session_info_len < 0 || header.session_info_len > MAX_IBT_SESSION_INFO_BYTES {
            return Err(bounds(
                "session info length",
                header.session_info_len.to_string(),
            ));
        }
        if header.num_vars < 0 || header.num_vars > MAX_VARS {
            return Err(bounds("variable count", header.num_vars.to_string()));
        }
        if header.num_buf < 1 || header.num_buf > 4 {
            return Err(bounds("telemetry buffer count", header.num_buf.to_string()));
        }
        if header.buf_len <= 0 {
            return Err(bounds("sample buffer length", header.buf_len.to_string()));
        }
        let info_offset = i64::from(header.session_info_offset);
        let info_len = i64::from(header.session_info_len);
        if info_len != 0 && info_offset < MINIMUM {
            return Err(bounds(
                "session info offset",
                format!("{info_offset} overlaps the fixed IBT headers"),
            ));
        }
        let var_offset = i64::from(header.var_header_offset);
        if header.num_vars != 0 && var_offset < MINIMUM {
            return Err(bounds(
                "variable header offset",
                format!("{var_offset} overlaps the fixed IBT headers"),
            ));
        }
        let buffer_offset = i64::from(header.var_bufs[0].buf_offset);
        if buffer_offset < 0 {
            return Err(bounds(
                "telemetry buffer offset",
                format!("negative offset {buffer_offset}"),
            ));
        }

        let disk_bytes = read_exact_at(
            &mut file,
            SDK_HEADER_LEN as i64,
            DISK_SUBHEADER_LEN as i64,
            file_size,
            "disk subheader",
        )?;
        let disk = DiskSubHeader {
            session_start_date: read_i64(&disk_bytes, 0),
            session_start_time: read_f64(&disk_bytes, 8),
            session_end_time: read_f64(&disk_bytes, 16),
            session_lap_count: read_i32(&disk_bytes, 24),
            session_record_count: read_i32(&disk_bytes, 28),
        };
        let record_count = u32::try_from(disk.session_record_count).map_err(|_| {
            bounds(
                "disk record count",
                format!("negative disk record count {}", disk.session_record_count),
            )
        })?;

        let session_info_bytes =
            read_exact_at(&mut file, info_offset, info_len, file_size, "session info")?;

        let var_len = i64::from(header.num_vars) * VAR_HEADER_LEN as i64;
        if overlap(info_offset, info_len, var_offset, var_len) {
            return Err(bounds(
                "variable header offset",
                "session info and variable header regions overlap".to_owned(),
            ));
        }
        let var_bytes = read_exact_at(
            &mut file,
            var_offset,
            var_len,
            file_size,
            "variable headers",
        )?;
        let vars = VarTable::parse_ibt(&var_bytes, header.num_vars, header.buf_len)?;

        let sample_len = i64::from(record_count) * i64::from(header.buf_len);
        if sample_len != 0 && buffer_offset < MINIMUM {
            return Err(bounds(
                "telemetry buffer offset",
                format!("{buffer_offset} overlaps the fixed IBT headers"),
            ));
        }
        if overlap(buffer_offset, sample_len, info_offset, info_len)
            || overlap(buffer_offset, sample_len, var_offset, var_len)
        {
            return Err(bounds(
                "telemetry buffer offset",
                "telemetry samples overlap session information or variable headers".to_owned(),
            ));
        }
        checked_region(
            buffer_offset,
            sample_len,
            file_size,
            "telemetry sample buffers",
        )?;

        Ok(IbtFile {
            path: path.to_path_buf(),
            file_size,
            mtime,
            header,
            disk,
            vars,
            buffer_offset: buffer_offset as u64,
            buffer_length: header.buf_len as u32,
            record_count,
            session_info_bytes,
        })
    }

    /// Same function as [`decode_session_info`].
    pub fn session_info_text(&self) -> String {
        decode_session_info(&self.session_info_bytes)
    }

    pub fn session_info(&self) -> Result<SessionInfo, IbtError> {
        SessionInfo::parse(&self.session_info_text())
    }

    /// SHA-256 (lowercase hex) of `session_info_bytes` as stored.
    pub fn session_info_sha256(&self) -> String {
        crate::gate::hex(&Sha256::digest(&self.session_info_bytes))
    }

    /// Port of `ibt_reader._read_edge_scalar` (ibt_reader.py:666-702): reads the last record and,
    /// when record_count > 1, the first (either read past the file -> `Truncated`), and returns
    /// the last record's value as stored, a non-finite Float/Double included. Python's
    /// first-record fallback triggers only on a None decode, which these types never produce.
    /// None for absent, array or char variables, or record_count == 0.
    pub fn edge_scalar(&self, name: &str) -> Result<Option<crate::SdkScalar>, IbtError> {
        let Some(variable) = self.vars.get(name) else {
            return Ok(None);
        };
        if variable.count != 1
            || self.record_count == 0
            || variable.var_type == crate::VarType::Char
        {
            return Ok(None);
        }
        let mut file = File::open(&self.path)?;
        let mut indices = vec![u64::from(self.record_count) - 1];
        if self.record_count > 1 {
            indices.push(0);
        }
        let mut last: Option<SdkScalar> = None;
        for index in indices {
            let absolute =
                self.buffer_offset + index * u64::from(self.buffer_length) + variable.offset as u64;
            let width = variable.var_type.width() as i64;
            let bytes = read_exact_at(
                &mut file,
                absolute as i64,
                width,
                self.file_size,
                "edge sample",
            )?;
            let value = variable.decode(&bytes, 0).ok_or(IbtError::Truncated {
                region: "edge sample",
                start: absolute,
                end: absolute + width as u64,
                available: self.file_size,
            })?;
            last = last.or(Some(value));
        }
        Ok(last)
    }

    /// Port of `ibt_reader._extract_summary` (ibt_reader.py:705-826).
    pub fn summary(&self) -> Result<RecordingSummary, IbtError> {
        let info = self.session_info()?;
        let session_num = self.edge_scalar("SessionNum")?;
        let session_unique_id = self.edge_scalar("SessionUniqueID")?;
        Ok(extract_summary(
            &info,
            &self.disk,
            unix_seconds(self.mtime),
            session_num.map(edge_val),
            session_unique_id.map(edge_val),
        ))
    }
}

fn edge_val(scalar: SdkScalar) -> Val<'static> {
    match scalar {
        SdkScalar::Char(value) => Val::Int(i64::from(value)),
        SdkScalar::Bool(value) => Val::Bool(value),
        SdkScalar::Int(value) => Val::Int(i64::from(value)),
        SdkScalar::BitField(value) => Val::Int(i64::from(value)),
        SdkScalar::Float(value) => Val::Float(f64::from(value)),
        SdkScalar::Double(value) => Val::Float(value),
    }
}

fn is_race_text(value: Option<&str>) -> bool {
    py::strip(value.unwrap_or_default()).to_lowercase() == "race"
}

fn extract_summary(
    info: &SessionInfo,
    disk: &DiskSubHeader,
    mtime: f64,
    session_num_sample: Option<Val<'_>>,
    session_unique_id_sample: Option<Val<'_>>,
) -> RecordingSummary {
    let weekend = info.section("WeekendInfo");
    let options = as_mapping(&get(weekend, "WeekendOptions"));
    let driver_info = info.section("DriverInfo");
    let session_root = info.section("SessionInfo");

    let mut sim_session_num = session_num_sample.as_ref().and_then(as_int);
    let session_items: Vec<Val<'_>> = as_list(&get(session_root, "Sessions"))
        .iter()
        .map(|item| val(Some(item)))
        .collect();
    let sessions: Vec<&[(String, crate::YamlNode)]> =
        session_items.iter().map(as_mapping).collect();
    // An empty mapping is falsy in Python, so it never counts as matched.
    let mut matched: &[(String, crate::YamlNode)] = &[];
    if let Some(number) = sim_session_num
        && let Some(item) = sessions
            .iter()
            .find(|item| as_int(&get(item, "SessionNum")) == Some(number))
    {
        matched = item;
    }
    if matched.is_empty() {
        let races: Vec<&&[(String, crate::YamlNode)]> = sessions
            .iter()
            .filter(|item| is_race_text(as_text(&get(item, "SessionType")).as_deref()))
            .collect();
        if let [only] = races.as_slice() {
            matched = only;
            if sim_session_num.is_none() {
                sim_session_num = as_int(&get(matched, "SessionNum"));
            }
        }
    }

    let sim_session_type = as_text(&get(matched, "SessionType"));
    let event_type = as_text(&get(weekend, "EventType"));
    let is_race = match sim_session_type.as_deref() {
        Some(kind) if !kind.is_empty() => is_race_text(Some(kind)),
        _ => is_race_text(event_type.as_deref()),
    };

    // Python compares `_as_int(CarIdx) == driver_car_idx`, so an unknown index matches the first
    // driver without one.
    let driver_car_idx = as_int(&get(driver_info, "DriverCarIdx"));
    let driver_items: Vec<Val<'_>> = as_list(&get(driver_info, "Drivers"))
        .iter()
        .map(|item| val(Some(item)))
        .collect();
    let driver: &[(String, crate::YamlNode)] = driver_items
        .iter()
        .map(as_mapping)
        .find(|candidate| as_int(&get(candidate, "CarIdx")) == driver_car_idx)
        .unwrap_or(&[]);

    let base_epoch = Some(disk.session_start_date as f64)
        .filter(|epoch| (MIN_REASONABLE_EPOCH..=MAX_REASONABLE_EPOCH).contains(epoch));
    let mut start_epoch: Option<f64> = None;
    let mut end_epoch: Option<f64> = None;
    let mut time_source = TimeSource::Mtime;
    if let Some(base) = base_epoch {
        // sessionStartDate is the wall-clock start; the two doubles are SessionTime values, so
        // only their difference belongs on the wall-clock duration.
        start_epoch = Some(base);
        let (start_offset, end_offset) = (disk.session_start_time, disk.session_end_time);
        end_epoch = Some(if start_offset.is_finite() && end_offset.is_finite() {
            let duration = end_offset - start_offset;
            base + if duration > 0.0 { duration } else { 0.0 }
        } else {
            base
        });
        time_source = TimeSource::DiskHeader;
    } else if let Some(yaml_start) = parse_iso_epoch(&get(weekend, "SessionStartTime")) {
        start_epoch = Some(yaml_start);
        end_epoch = Some(yaml_start);
        time_source = TimeSource::SessionYaml;
    }
    let start = start_epoch.unwrap_or(mtime);
    if start_epoch.is_none() {
        end_epoch = Some(mtime);
    }
    let end = match end_epoch {
        Some(end) if end >= start => end,
        Some(end) if end.is_nan() => end,
        _ => start,
    };

    RecordingSummary {
        series_id: as_int(&get(weekend, "SeriesID")),
        season_id: as_int(&get(weekend, "SeasonID")),
        session_id: as_int(&get(weekend, "SessionID")),
        subsession_id: as_int(&get(weekend, "SubSessionID")),
        race_week: as_int(&get(weekend, "RaceWeek")),
        event_type,
        category: as_text(&get(weekend, "Category")),
        official: as_bool(&get(weekend, "Official")),
        track_id: as_int(&get(weekend, "TrackID")),
        track_name: as_text(&get(weekend, "TrackName")),
        track_display_name: as_text(&get(weekend, "TrackDisplayName")),
        track_config_name: as_text(&get(weekend, "TrackConfigName")),
        track_length: as_text(&get(weekend, "TrackLength")),
        is_fixed_setup: as_bool(&get(options, "IsFixedSetup")),
        sim_session_num,
        sim_session_type,
        session_unique_id: session_unique_id_sample.as_ref().and_then(as_int),
        is_race,
        car_id: as_int(&get(driver, "CarID")),
        car_path: as_text(&get(driver, "CarPath")),
        car_screen_name: as_text(&get(driver, "CarScreenName")),
        driver_user_id: as_int(&get(driver, "UserID")),
        driver_setup_name: as_text(&get(driver_info, "DriverSetupName")),
        driver_setup_modified: setup_modified(driver_info),
        start_time_unix: start,
        end_time_unix: end,
        start_time_utc: safe_iso(start),
        end_time_utc: safe_iso(end),
        time_source,
    }
}
