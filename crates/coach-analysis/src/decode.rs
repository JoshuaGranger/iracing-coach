//! The decode stage: `ibt_reader.load_telemetry(path, channels, target_hz)`'s `samples` and
//! `sample_indices`, column by column.
//!
//! Header, variable table and bounds come from `coach_ibt::IbtFile::open` (the port of
//! `_read_layout`); this module only selects variables, applies the sampling plan and gathers
//! each selected variable's bytes from the chosen records. Values are kept as stored, so the
//! Python seam rebuilds exactly the objects Python's own decode makes:
//!
//! | SDK type | Python value | Column element |
//! |---|---|---|
//! | bool (1) | `bool`, True for any nonzero byte (`struct` `?`) | `bool`: one byte, 0 or 1 |
//! | int (2) | `int` (`<i`) | `i32` little-endian |
//! | bitfield (3) | `int` (`<I`) | `u32` little-endian |
//! | float (4) | `float` widened from the stored f32 (`<f`) | `f32` little-endian, bits as stored |
//! | double (5) | `float` (`<d`) | `f64` little-endian, bits as stored |
//! | char (0) | `str`: bytes before the first NUL, UTF-8, else cp1252 with `errors="replace"` | `utf8`: text plus offsets |
//!
//! A variable with `count > 1` (other than char) is a list of `count` values per sample; the
//! column holds the elements sample-major. Non-finite floats are kept (Python keeps NaN and
//! infinities; the kernels normalise them later).

use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

use coach_ibt::{IbtFile, VarHeader, VarTable, VarType, decode_session_info};

use crate::DecodeError;
use crate::sampling::SamplingPlan;

/// Bytes of sample rows read per block.
const BLOCK_BYTES: usize = 8 * 1024 * 1024;

/// Storage element of a column.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Element {
    Bool,
    I32,
    U32,
    F32,
    F64,
    Utf8,
}

impl Element {
    pub fn for_type(var_type: VarType) -> Element {
        match var_type {
            VarType::Char => Element::Utf8,
            VarType::Bool => Element::Bool,
            VarType::Int => Element::I32,
            VarType::BitField => Element::U32,
            VarType::Float => Element::F32,
            VarType::Double => Element::F64,
        }
    }

    /// Bytes per element; 0 for `Utf8` (variable length).
    pub fn width(self) -> usize {
        match self {
            Element::Bool => 1,
            Element::I32 | Element::U32 | Element::F32 => 4,
            Element::F64 => 8,
            Element::Utf8 => 0,
        }
    }

    /// Name in the store header.
    pub fn name(self) -> &'static str {
        match self {
            Element::Bool => "bool",
            Element::I32 => "i32",
            Element::U32 => "u32",
            Element::F32 => "f32",
            Element::F64 => "f64",
            Element::Utf8 => "utf8",
        }
    }

    pub fn from_name(name: &str) -> Option<Element> {
        Some(match name {
            "bool" => Element::Bool,
            "i32" => Element::I32,
            "u32" => Element::U32,
            "f32" => Element::F32,
            "f64" => Element::F64,
            "utf8" => Element::Utf8,
            _ => return None,
        })
    }
}

/// One decoded variable.
#[derive(Debug, Clone, PartialEq)]
pub struct Column {
    pub name: String,
    pub var_type: VarType,
    /// SDK element count (`count`); a char variable is one string per sample whatever its count.
    pub count: usize,
    /// Offset of the variable within a sample row.
    pub var_offset: i32,
    pub element: Element,
    /// Numeric: little-endian elements, sample-major. `Utf8`: the samples' text, concatenated.
    pub data: Vec<u8>,
    /// `Utf8` only: `sample_count + 1` byte offsets into `data`; empty otherwise.
    pub text_offsets: Vec<u64>,
}

impl Column {
    fn new(var: &VarHeader, samples: usize) -> Column {
        let element = Element::for_type(var.var_type);
        let count = usize::try_from(var.count).unwrap_or(0);
        let mut text_offsets = Vec::new();
        let data = if element == Element::Utf8 {
            text_offsets.reserve(samples + 1);
            text_offsets.push(0);
            Vec::new()
        } else {
            Vec::with_capacity(samples * count * element.width())
        };
        Column {
            name: var.name.clone(),
            var_type: var.var_type,
            count,
            var_offset: var.offset,
            element,
            data,
            text_offsets,
        }
    }

    /// Appends this variable's value from one sample row. `VarTable::parse_ibt` guarantees
    /// `offset + width * count <= buf_len == row.len()`.
    fn push(&mut self, row: &[u8]) {
        let start = usize::try_from(self.var_offset).unwrap_or(0);
        match self.element {
            Element::Bool => {
                self.data.extend(
                    row[start..start + self.count]
                        .iter()
                        .map(|&b| u8::from(b != 0)),
                );
            }
            Element::Utf8 => {
                let raw = &row[start..start + self.count];
                let cut = raw.split(|&b| b == 0).next().unwrap_or_default();
                match std::str::from_utf8(cut) {
                    // Python's strict UTF-8 decode keeps a leading BOM.
                    Ok(text) => self.data.extend_from_slice(text.as_bytes()),
                    // Invalid UTF-8: the cp1252 fallback; `cut` holds no NUL and no valid UTF-8,
                    // so this is exactly `cut.decode("cp1252", errors="replace")`.
                    Err(_) => self
                        .data
                        .extend_from_slice(decode_session_info(cut).as_bytes()),
                }
                self.text_offsets.push(self.data.len() as u64);
            }
            element => {
                let end = start + element.width() * self.count;
                self.data.extend_from_slice(&row[start..end]);
            }
        }
    }

    /// Number of samples held.
    pub fn sample_count(&self) -> usize {
        match self.element {
            Element::Utf8 => self.text_offsets.len().saturating_sub(1),
            element => {
                let per_sample = element.width() * self.count;
                self.data.len().checked_div(per_sample).unwrap_or(0)
            }
        }
    }

    /// Text of sample `index` (`Utf8` columns).
    pub fn text(&self, index: usize) -> Option<&str> {
        if self.element != Element::Utf8 {
            return None;
        }
        let start = usize::try_from(*self.text_offsets.get(index)?).ok()?;
        let end = usize::try_from(*self.text_offsets.get(index + 1)?).ok()?;
        std::str::from_utf8(self.data.get(start..end)?).ok()
    }

    /// Element `element` of sample `index`, widened as Python widens it, for numeric columns.
    pub fn value(&self, index: usize, element: usize) -> Option<Value> {
        if element >= self.count || self.element == Element::Utf8 {
            return None;
        }
        let width = self.element.width();
        let at = (index * self.count + element) * width;
        let bytes = self.data.get(at..at + width)?;
        let word4 = || [bytes[0], bytes[1], bytes[2], bytes[3]];
        Some(match self.element {
            Element::Bool => Value::Bool(bytes[0] != 0),
            Element::I32 => Value::Int(i64::from(i32::from_le_bytes(word4()))),
            Element::U32 => Value::Int(i64::from(u32::from_le_bytes(word4()))),
            Element::F32 => Value::Float32(f32::from_le_bytes(word4())),
            Element::F64 => {
                let mut word = [0u8; 8];
                word.copy_from_slice(bytes);
                Value::Float(f64::from_le_bytes(word))
            }
            Element::Utf8 => return None,
        })
    }
}

/// A numeric value as the Python object it becomes.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Value {
    Bool(bool),
    Int(i64),
    /// A stored f32; Python holds `f64::from(value)`.
    Float32(f32),
    Float(f64),
}

/// The recording facts the store carries so the Python seam can check it decoded the file it
/// scanned.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct SourceLayout {
    pub file_size: u64,
    pub record_count: u32,
    pub tick_rate: i32,
    pub buffer_offset: u64,
    pub buffer_length: u32,
    pub num_variables: u32,
}

/// Output of the decode stage for one recording.
#[derive(Debug, Clone, PartialEq)]
pub struct Decoded {
    pub source: SourceLayout,
    pub target_hz: Option<f64>,
    pub plan: SamplingPlan,
    /// `channels is None` in Python (`channel_selection.mode == "all"`).
    pub all_channels: bool,
    pub sample_indices: Vec<u32>,
    pub columns: Vec<Column>,
}

/// `_normalise_channels`: every variable in file order for `None`; otherwise the requested
/// names in caller order without repeats, failing like Python on an empty or absent name.
pub fn select_variables<'a>(
    vars: &'a VarTable,
    channels: Option<&[String]>,
) -> Result<Vec<&'a VarHeader>, DecodeError> {
    let Some(requested) = channels else {
        return Ok(vars.iter().collect());
    };
    if requested.iter().any(String::is_empty) {
        return Err(DecodeError::EmptyChannelName);
    }
    let missing: Vec<&str> = requested
        .iter()
        .filter(|name| vars.get(name).is_none())
        .map(String::as_str)
        .collect();
    if !missing.is_empty() {
        // sorted(by_name)[:20]: Python sorts str by code point, which is UTF-8 byte order.
        let mut names: Vec<&str> = vars.iter().map(|var| var.name.as_str()).collect();
        names.sort_unstable();
        names.truncate(20);
        return Err(DecodeError::Channel {
            missing: missing.join(", "),
            preview: names.join(", "),
        });
    }
    let mut selected: Vec<&VarHeader> = Vec::with_capacity(requested.len());
    for name in requested {
        if selected.iter().any(|var| &var.name == name) {
            continue;
        }
        if let Some(var) = vars.get(name) {
            selected.push(var);
        }
    }
    Ok(selected)
}

/// Decodes `channels` (all variables for `None`) of the recording at `path` at `target_hz`
/// (native rate for `None`).
pub fn decode(
    path: &Path,
    channels: Option<&[String]>,
    target_hz: Option<f64>,
) -> Result<Decoded, DecodeError> {
    let ibt = IbtFile::open(path)?;
    let selected = select_variables(&ibt.vars, channels)?;
    let plan = SamplingPlan::new(ibt.header.tick_rate, target_hz)?;
    let sample_indices = plan.sample_indices(ibt.record_count)?;
    let mut columns: Vec<Column> = selected
        .iter()
        .map(|var| Column::new(var, sample_indices.len()))
        .collect();
    if !sample_indices.is_empty() && !columns.is_empty() {
        gather(&ibt, &sample_indices, &mut columns)?;
    }
    Ok(Decoded {
        source: SourceLayout {
            file_size: ibt.file_size,
            record_count: ibt.record_count,
            tick_rate: ibt.header.tick_rate,
            buffer_offset: ibt.buffer_offset,
            buffer_length: ibt.buffer_length,
            num_variables: ibt.vars.len() as u32,
        },
        target_hz,
        plan,
        all_channels: channels.is_none(),
        sample_indices,
        columns,
    })
}

/// Reads the rows named by `indices` (ascending) in blocks and appends each to every column.
fn gather(ibt: &IbtFile, indices: &[u32], columns: &mut [Column]) -> Result<(), DecodeError> {
    let row_len = ibt.buffer_length as usize;
    let rows_per_block = (BLOCK_BYTES / row_len).max(1);
    let mut block = vec![0u8; rows_per_block * row_len];
    let mut file = File::open(&ibt.path)?;
    let mut next = 0usize;
    while next < indices.len() {
        let first = indices[next];
        let rows = rows_per_block.min((ibt.record_count - first) as usize);
        let bytes = &mut block[..rows * row_len];
        file.seek(SeekFrom::Start(
            ibt.buffer_offset + u64::from(first) * row_len as u64,
        ))?;
        file.read_exact(bytes)?;
        let end = u64::from(first) + rows as u64;
        while next < indices.len() && u64::from(indices[next]) < end {
            let at = (indices[next] - first) as usize * row_len;
            let row = &bytes[at..at + row_len];
            for column in columns.iter_mut() {
                column.push(row);
            }
            next += 1;
        }
    }
    Ok(())
}
