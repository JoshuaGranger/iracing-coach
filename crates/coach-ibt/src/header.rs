//! SDK header (`irsdk_header`), variable table (`irsdk_varHeader`) and typed row readers.

use std::collections::HashMap;

use crate::py::decode_c_string;
use crate::{IbtError, MAX_LIVE_SESSION_INFO_BYTES, MAX_VARS, SDK_HEADER_LEN, VAR_HEADER_LEN};

pub(crate) fn read_i32(bytes: &[u8], at: usize) -> i32 {
    let mut word = [0u8; 4];
    word.copy_from_slice(&bytes[at..at + 4]);
    i32::from_le_bytes(word)
}

pub(crate) fn read_u32(bytes: &[u8], at: usize) -> u32 {
    let mut word = [0u8; 4];
    word.copy_from_slice(&bytes[at..at + 4]);
    u32::from_le_bytes(word)
}

pub(crate) fn read_f32(bytes: &[u8], at: usize) -> f32 {
    f32::from_bits(read_u32(bytes, at))
}

pub(crate) fn read_i64(bytes: &[u8], at: usize) -> i64 {
    let mut word = [0u8; 8];
    word.copy_from_slice(&bytes[at..at + 8]);
    i64::from_le_bytes(word)
}

pub(crate) fn read_f64(bytes: &[u8], at: usize) -> f64 {
    let mut word = [0u8; 8];
    word.copy_from_slice(&bytes[at..at + 8]);
    f64::from_le_bytes(word)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct VarBuf {
    pub tick_count: i32,
    pub buf_offset: i32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SdkHeader {
    pub ver: i32,
    /// Bit 0 = connected (live only).
    pub status: i32,
    /// Hz.
    pub tick_rate: i32,
    /// Increments on every session-info change.
    pub session_info_update: i32,
    /// Bytes.
    pub session_info_len: i32,
    /// From the start of the file or map.
    pub session_info_offset: i32,
    pub num_vars: i32,
    pub var_header_offset: i32,
    pub num_buf: i32,
    /// Bytes per sample row.
    pub buf_len: i32,
    /// At offset 48, stride 16: tick at +0, offset at +4.
    pub var_bufs: [VarBuf; 4],
}

fn live_bounds(detail: String) -> IbtError {
    IbtError::Bounds {
        what: "live SDK header",
        detail,
    }
}

impl SdkHeader {
    /// Little-endian decode of `bytes[0..112]`. Fails only on a short slice.
    pub fn parse(bytes: &[u8]) -> Result<SdkHeader, IbtError> {
        if bytes.len() < SDK_HEADER_LEN {
            return Err(IbtError::Truncated {
                region: "SDK header",
                start: 0,
                end: SDK_HEADER_LEN as u64,
                available: bytes.len() as u64,
            });
        }
        let at = |index: usize| read_i32(bytes, index * 4);
        let var_buf = |index: usize| VarBuf {
            tick_count: read_i32(bytes, 48 + index * 16),
            buf_offset: read_i32(bytes, 48 + index * 16 + 4),
        };
        Ok(SdkHeader {
            ver: at(0),
            status: at(1),
            tick_rate: at(2),
            session_info_update: at(3),
            session_info_len: at(4),
            session_info_offset: at(5),
            num_vars: at(6),
            var_header_offset: at(7),
            num_buf: at(8),
            buf_len: at(9),
            var_bufs: [var_buf(0), var_buf(1), var_buf(2), var_buf(3)],
        })
    }

    /// `status & 1 != 0`.
    pub fn connected(&self) -> bool {
        self.status & 1 != 0
    }

    /// Live-map bounds (C# `IRacingSdkTelemetrySource.cs:42`, `:209`, `:235`):
    /// 1 <= num_vars <= 16384, var_header_offset >= 112, buf_len > 0, session_info_len in
    /// 1..=16 MiB, session_info_offset >= 112, and both regions inside `map_len`.
    pub fn validate_live(&self, map_len: usize) -> Result<(), IbtError> {
        let map_len = map_len as i64;
        if self.num_vars <= 0 || self.num_vars > MAX_VARS {
            return Err(live_bounds(format!("variable count {}", self.num_vars)));
        }
        if self.var_header_offset < SDK_HEADER_LEN as i32 {
            return Err(live_bounds(format!(
                "variable header offset {}",
                self.var_header_offset
            )));
        }
        if self.buf_len <= 0 {
            return Err(live_bounds(format!(
                "sample buffer length {}",
                self.buf_len
            )));
        }
        let var_end =
            i64::from(self.var_header_offset) + i64::from(self.num_vars) * VAR_HEADER_LEN as i64;
        if var_end > map_len {
            return Err(live_bounds(format!(
                "variable table ends at {var_end}, map is {map_len} bytes"
            )));
        }
        if self.session_info_len <= 0 || self.session_info_len > MAX_LIVE_SESSION_INFO_BYTES {
            return Err(live_bounds(format!(
                "session info length {}",
                self.session_info_len
            )));
        }
        let info_end = i64::from(self.session_info_offset) + i64::from(self.session_info_len);
        if self.session_info_offset < SDK_HEADER_LEN as i32 || info_end > map_len {
            return Err(live_bounds(format!(
                "session info [{}, {info_end}) outside the {map_len}-byte map",
                self.session_info_offset
            )));
        }
        Ok(())
    }

    /// Among `var_bufs[0..clamp(num_buf, 0, 4)]`, the one with the highest tick whose offset
    /// is at least 112 and whose row ends inside the map (C# `:48-60`).
    pub fn newest_buffer(&self, map_len: usize) -> Option<VarBuf> {
        let count = self.num_buf.clamp(0, 4) as usize;
        let mut best: Option<VarBuf> = None;
        let mut best_tick = i32::MIN;
        for buffer in &self.var_bufs[..count] {
            let inside = buffer.buf_offset >= SDK_HEADER_LEN as i32
                && i64::from(buffer.buf_offset) + i64::from(self.buf_len) <= map_len as i64;
            if buffer.tick_count > best_tick && inside {
                best_tick = buffer.tick_count;
                best = Some(*buffer);
            }
        }
        best
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(i32)]
pub enum VarType {
    Char = 0,
    Bool = 1,
    Int = 2,
    BitField = 3,
    Float = 4,
    Double = 5,
}

impl VarType {
    pub fn from_code(code: i32) -> Option<VarType> {
        Some(match code {
            0 => VarType::Char,
            1 => VarType::Bool,
            2 => VarType::Int,
            3 => VarType::BitField,
            4 => VarType::Float,
            5 => VarType::Double,
            _ => return None,
        })
    }

    /// Bytes per element: 1, 1, 4, 4, 4, 8.
    pub fn width(self) -> usize {
        match self {
            VarType::Char | VarType::Bool => 1,
            VarType::Int | VarType::BitField | VarType::Float => 4,
            VarType::Double => 8,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct VarHeader {
    pub var_type: VarType,
    /// Within a sample row.
    pub offset: i32,
    /// Elements (> 0).
    pub count: i32,
    pub count_as_time: bool,
    /// Text rule of the parser that built the table (`parse_ibt` or `parse_live`).
    pub name: String,
    pub desc: String,
    pub unit: String,
}

/// One scalar from a sample row with its SDK type preserved (edge reads, goldens).
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum SdkScalar {
    Char(u8),
    Bool(bool),
    Int(i32),
    BitField(u32),
    Float(f32),
    Double(f64),
}

impl VarHeader {
    /// Element 0 of a sample row decoded with its SDK type, or `None` when `row` does not hold it.
    pub(crate) fn scalar(&self, row: &[u8]) -> Option<SdkScalar> {
        self.decode(row, usize::try_from(self.offset).ok()?)
    }

    /// One element of this variable's type stored at `bytes[start..]`.
    pub(crate) fn decode(&self, bytes: &[u8], start: usize) -> Option<SdkScalar> {
        if bytes.len() < start.checked_add(self.var_type.width())? {
            return None;
        }
        Some(match self.var_type {
            VarType::Char => SdkScalar::Char(bytes[start]),
            VarType::Bool => SdkScalar::Bool(bytes[start] != 0),
            VarType::Int => SdkScalar::Int(read_i32(bytes, start)),
            VarType::BitField => SdkScalar::BitField(read_u32(bytes, start)),
            VarType::Float => SdkScalar::Float(read_f32(bytes, start)),
            VarType::Double => SdkScalar::Double(read_f64(bytes, start)),
        })
    }

    /// Start of every element when `row` holds all of them.
    fn elements(&self, row: &[u8]) -> Option<impl Iterator<Item = usize>> {
        let start = usize::try_from(self.offset).ok()?;
        let count = usize::try_from(self.count).ok()?;
        let width = self.var_type.width();
        if row.len() < start.checked_add(count.checked_mul(width)?)? {
            return None;
        }
        Some((0..count).map(move |index| start + index * width))
    }
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct VarTable {
    headers: Vec<VarHeader>,
    index: HashMap<String, usize>,
}

/// C# `Encoding.ASCII.GetString` over the whole field (bytes >= 0x80 become '?') with trailing
/// NULs trimmed.
fn ascii_trim_end(bytes: &[u8]) -> String {
    let text: String = bytes
        .iter()
        .map(|&b| if b < 0x80 { char::from(b) } else { '?' })
        .collect();
    text.trim_end_matches('\0').to_owned()
}

fn round_half_even_i64(value: f64) -> Option<i64> {
    if !value.is_finite() {
        return None;
    }
    crate::py::integral_to_i64(value.round_ties_even())
}

// The contract (crate-api.md 3.2) fixes `len` and no `is_empty`.
#[allow(clippy::len_without_is_empty)]
impl VarTable {
    /// IBT rules (`ibt_reader.py:983-1023`): unknown type code -> `UnsupportedVarType`; empty or
    /// duplicate name -> `Format`; count <= 0 or row bounds exceeded -> `Bounds`. Text fields
    /// follow Python `_decode_c_string`: bytes before the first NUL, UTF-8, else cp1252 with
    /// `errors="replace"`.
    pub fn parse_ibt(var_bytes: &[u8], num_vars: i32, buf_len: i32) -> Result<VarTable, IbtError> {
        let count = usize::try_from(num_vars).map_err(|_| IbtError::Bounds {
            what: "variable count",
            detail: num_vars.to_string(),
        })?;
        let needed = count as u64 * VAR_HEADER_LEN as u64;
        if (var_bytes.len() as u64) < needed {
            return Err(IbtError::Truncated {
                region: "variable headers",
                start: 0,
                end: needed,
                available: var_bytes.len() as u64,
            });
        }
        let mut table = VarTable::default();
        for index in 0..count {
            let entry = &var_bytes[index * VAR_HEADER_LEN..(index + 1) * VAR_HEADER_LEN];
            let type_code = read_i32(entry, 0);
            let offset = read_i32(entry, 4);
            let element_count = read_i32(entry, 8);
            let name = decode_c_string(&entry[16..48]);
            let Some(var_type) = VarType::from_code(type_code) else {
                return Err(IbtError::UnsupportedVarType {
                    index: index as u32,
                    name,
                    type_code,
                });
            };
            if name.is_empty() {
                return Err(IbtError::Format(format!(
                    "variable header #{index} has no name"
                )));
            }
            if table.index.contains_key(&name) {
                return Err(IbtError::Format(format!(
                    "duplicate variable name {name:?}"
                )));
            }
            if element_count <= 0 {
                return Err(IbtError::Bounds {
                    what: "variable element count",
                    detail: format!("variable {name:?} has invalid element count {element_count}"),
                });
            }
            let end = i64::from(offset) + var_type.width() as i64 * i64::from(element_count);
            if offset < 0 || end > i64::from(buf_len) {
                return Err(IbtError::Bounds {
                    what: "variable offset",
                    detail: format!(
                        "variable {name:?} occupies bytes [{offset}, {end}) outside the {buf_len}-byte sample buffer"
                    ),
                });
            }
            table.index.insert(name.clone(), table.headers.len());
            table.headers.push(VarHeader {
                var_type,
                offset,
                count: element_count,
                count_as_time: entry[12] != 0,
                name,
                desc: decode_c_string(&entry[48..112]),
                unit: decode_c_string(&entry[112..144]),
            });
        }
        Ok(table)
    }

    /// Live rules (C# `:207-230`): entries with an unknown type, negative offset, count <= 0,
    /// empty name or row overflow are skipped, never an error; a later duplicate name replaces
    /// the earlier entry in place. The table itself must lie inside the map (1..=16384 entries
    /// at an offset >= 112), else `Bounds`.
    pub fn parse_live(map: &[u8], header: &SdkHeader) -> Result<VarTable, IbtError> {
        if header.num_vars <= 0 || header.num_vars > MAX_VARS {
            return Err(live_bounds(format!("variable count {}", header.num_vars)));
        }
        let start = usize::try_from(header.var_header_offset)
            .ok()
            .filter(|&offset| offset >= SDK_HEADER_LEN)
            .ok_or_else(|| {
                live_bounds(format!(
                    "variable header offset {}",
                    header.var_header_offset
                ))
            })?;
        let count = header.num_vars as usize;
        let end = start + count * VAR_HEADER_LEN;
        if end > map.len() {
            return Err(live_bounds(format!(
                "variable table ends at {end}, map is {} bytes",
                map.len()
            )));
        }
        let mut table = VarTable::default();
        let (entries, _) = map[start..end].as_chunks::<VAR_HEADER_LEN>();
        for entry in entries {
            let Some(var_type) = VarType::from_code(read_i32(entry, 0)) else {
                continue;
            };
            let offset = read_i32(entry, 4);
            let element_count = read_i32(entry, 8);
            if offset < 0 || element_count <= 0 {
                continue;
            }
            let name = ascii_trim_end(&entry[16..48]);
            let row_end = i64::from(offset) + i64::from(element_count) * var_type.width() as i64;
            if name.is_empty() || row_end > i64::from(header.buf_len) {
                continue;
            }
            let variable = VarHeader {
                var_type,
                offset,
                count: element_count,
                count_as_time: entry[12] != 0,
                desc: ascii_trim_end(&entry[48..112]),
                unit: ascii_trim_end(&entry[112..144]),
                name,
            };
            match table.index.get(&variable.name) {
                Some(&slot) => table.headers[slot] = variable,
                None => {
                    table
                        .index
                        .insert(variable.name.clone(), table.headers.len());
                    table.headers.push(variable);
                }
            }
        }
        Ok(table)
    }

    pub fn len(&self) -> usize {
        self.headers.len()
    }

    pub fn iter(&self) -> impl Iterator<Item = &VarHeader> {
        self.headers.iter()
    }

    pub fn get(&self, name: &str) -> Option<&VarHeader> {
        self.index.get(name).map(|&slot| &self.headers[slot])
    }

    fn scalar(&self, name: &str, row: &[u8]) -> Option<SdkScalar> {
        self.get(name)?.scalar(row)
    }

    // Readers over one sample row (`row.len() >= buf_len`); None when the variable is absent,
    // its type is not accepted, or the row is too short; never because of the value.

    /// Any type, element 0; char as unsigned byte, bool 0/1, int, bitfield as u32, float,
    /// double -> f64 (C# ReadNumber). A non-finite float or double is returned as is.
    pub fn f64(&self, name: &str, row: &[u8]) -> Option<f64> {
        Some(match self.scalar(name, row)? {
            SdkScalar::Char(value) => f64::from(value),
            SdkScalar::Bool(value) => f64::from(u8::from(value)),
            SdkScalar::Int(value) => f64::from(value),
            SdkScalar::BitField(value) => f64::from(value),
            SdkScalar::Float(value) => f64::from(value),
            SdkScalar::Double(value) => value,
        })
    }

    /// Int and bitfield exactly (bitfield reinterpreted); bool/char as 0/1/byte; float/double
    /// truncated toward zero, None when non-finite or outside i32.
    pub fn i32(&self, name: &str, row: &[u8]) -> Option<i32> {
        match self.scalar(name, row)? {
            SdkScalar::Char(value) => Some(i32::from(value)),
            SdkScalar::Bool(value) => Some(i32::from(value)),
            SdkScalar::Int(value) => Some(value),
            SdkScalar::BitField(value) => Some(value as i32),
            SdkScalar::Float(value) => truncate_i32(f64::from(value)),
            SdkScalar::Double(value) => truncate_i32(value),
        }
    }

    /// Like `i32` but i64; bitfield as its unsigned value; float/double rounded half to even
    /// (C# ReadLong), None when non-finite or outside i64.
    pub fn i64(&self, name: &str, row: &[u8]) -> Option<i64> {
        match self.scalar(name, row)? {
            SdkScalar::Char(value) => Some(i64::from(value)),
            SdkScalar::Bool(value) => Some(i64::from(value)),
            SdkScalar::Int(value) => Some(i64::from(value)),
            SdkScalar::BitField(value) => Some(i64::from(value)),
            SdkScalar::Float(value) => round_half_even_i64(f64::from(value)),
            SdkScalar::Double(value) => round_half_even_i64(value),
        }
    }

    /// Int or bitfield only (C# ReadUInt).
    pub fn u32(&self, name: &str, row: &[u8]) -> Option<u32> {
        match self.scalar(name, row)? {
            SdkScalar::Int(value) => Some(value as u32),
            SdkScalar::BitField(value) => Some(value),
            _ => None,
        }
    }

    /// Bool only (C# ReadBool).
    pub fn bool(&self, name: &str, row: &[u8]) -> Option<bool> {
        match self.scalar(name, row)? {
            SdkScalar::Bool(value) => Some(value),
            _ => None,
        }
    }

    /// Int or bitfield arrays into `out` (cleared first); empty when not accepted.
    pub fn i32_array(&self, name: &str, row: &[u8], out: &mut Vec<i32>) {
        out.clear();
        let Some(variable) = self.get(name) else {
            return;
        };
        if !matches!(variable.var_type, VarType::Int | VarType::BitField) {
            return;
        }
        if let Some(starts) = variable.elements(row) {
            out.extend(starts.map(|start| read_i32(row, start)));
        }
    }

    /// Float arrays only; every element as stored, non-finite included, so indices stay car
    /// indices (C# ReadFloatArray).
    pub fn f32_array(&self, name: &str, row: &[u8], out: &mut Vec<f32>) {
        out.clear();
        let Some(variable) = self.get(name) else {
            return;
        };
        if variable.var_type != VarType::Float {
            return;
        }
        if let Some(starts) = variable.elements(row) {
            out.extend(starts.map(|start| read_f32(row, start)));
        }
    }
}

fn truncate_i32(value: f64) -> Option<i32> {
    if !value.is_finite() {
        return None;
    }
    let truncated = value.trunc();
    (truncated >= f64::from(i32::MIN) && truncated <= f64::from(i32::MAX))
        .then_some(truncated as i32)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn var_entry(type_code: i32, offset: i32, count: i32, name: &[u8]) -> Vec<u8> {
        let mut entry = vec![0u8; VAR_HEADER_LEN];
        entry[0..4].copy_from_slice(&type_code.to_le_bytes());
        entry[4..8].copy_from_slice(&offset.to_le_bytes());
        entry[8..12].copy_from_slice(&count.to_le_bytes());
        entry[16..16 + name.len()].copy_from_slice(name);
        entry
    }

    fn table(entries: &[Vec<u8>], buf_len: i32) -> Result<VarTable, IbtError> {
        let bytes: Vec<u8> = entries.concat();
        VarTable::parse_ibt(&bytes, entries.len() as i32, buf_len)
    }

    #[test]
    fn ibt_table_rules() {
        assert!(matches!(
            table(&[var_entry(9, 0, 1, b"X")], 8),
            Err(IbtError::UnsupportedVarType { type_code: 9, .. })
        ));
        assert!(matches!(
            table(&[var_entry(2, 0, 1, b"")], 8),
            Err(IbtError::Format(_))
        ));
        assert!(matches!(
            table(&[var_entry(2, 0, 1, b"A"), var_entry(2, 4, 1, b"A")], 8),
            Err(IbtError::Format(_))
        ));
        assert!(matches!(
            table(&[var_entry(2, 0, 0, b"A")], 8),
            Err(IbtError::Bounds { .. })
        ));
        assert!(matches!(
            table(&[var_entry(5, 4, 1, b"A")], 8),
            Err(IbtError::Bounds { .. })
        ));
        assert!(matches!(
            table(&[var_entry(2, -1, 1, b"A")], 8),
            Err(IbtError::Bounds { .. })
        ));
        let names = table(
            &[var_entry(4, 0, 1, b"Caf\xe9\0x"), var_entry(1, 4, 1, b"B")],
            8,
        )
        .expect("valid");
        assert_eq!(
            names.get("Caf\u{e9}").map(|v| v.var_type),
            Some(VarType::Float)
        );
        assert_eq!(names.len(), 2);
    }

    #[test]
    fn live_table_skips_bad_entries_and_replaces_duplicates() {
        let entries = [
            var_entry(9, 0, 1, b"Bad"),
            var_entry(2, 0, 1, b"A"),
            var_entry(2, 0, 0, b"Zero"),
            var_entry(5, 4, 1, b"Over"),
            var_entry(4, 4, 1, b"A"),
            var_entry(2, 0, 1, b"N\xff\0"),
        ];
        let mut map = vec![0u8; SDK_HEADER_LEN];
        map.extend(entries.concat());
        let header = SdkHeader {
            ver: 2,
            status: 1,
            tick_rate: 60,
            session_info_update: 0,
            session_info_len: 0,
            session_info_offset: 0,
            num_vars: entries.len() as i32,
            var_header_offset: SDK_HEADER_LEN as i32,
            num_buf: 1,
            buf_len: 8,
            var_bufs: [VarBuf {
                tick_count: 0,
                buf_offset: 0,
            }; 4],
        };
        let vars = VarTable::parse_live(&map, &header).expect("table");
        let names: Vec<&str> = vars.iter().map(|v| v.name.as_str()).collect();
        assert_eq!(names, ["A", "N?"]);
        assert_eq!(
            vars.get("A").map(|v| (v.var_type, v.offset)),
            Some((VarType::Float, 4))
        );
    }

    #[test]
    fn readers_keep_raw_values() {
        let vars = table(
            &[
                var_entry(4, 0, 2, b"F"),
                var_entry(3, 8, 1, b"Bits"),
                var_entry(5, 16, 1, b"D"),
                var_entry(1, 12, 1, b"B"),
                var_entry(0, 13, 1, b"C"),
            ],
            24,
        )
        .expect("valid");
        let mut row = vec![0u8; 24];
        row[0..4].copy_from_slice(&f32::NAN.to_le_bytes());
        row[4..8].copy_from_slice(&2.5f32.to_le_bytes());
        row[8..12].copy_from_slice(&0xFFFF_FFFFu32.to_le_bytes());
        row[12] = 2;
        row[13] = 200;
        row[16..24].copy_from_slice(&2.5f64.to_le_bytes());
        assert!(vars.f64("F", &row).is_some_and(f64::is_nan));
        assert_eq!(vars.i32("F", &row), None);
        let mut floats = Vec::new();
        vars.f32_array("F", &row, &mut floats);
        assert!(floats[0].is_nan() && floats[1] == 2.5);
        assert_eq!(vars.i32("Bits", &row), Some(-1));
        assert_eq!(vars.i64("Bits", &row), Some(0xFFFF_FFFF));
        assert_eq!(vars.u32("Bits", &row), Some(0xFFFF_FFFF));
        assert_eq!(vars.i64("D", &row), Some(2));
        assert_eq!(vars.i32("D", &row), Some(2));
        assert_eq!(vars.bool("B", &row), Some(true));
        assert_eq!(vars.bool("D", &row), None);
        assert_eq!(vars.f64("C", &row), Some(200.0));
        assert_eq!(vars.f64("Missing", &row), None);
        assert_eq!(vars.f64("D", &row[..20]), None);
        let mut ints = vec![7];
        vars.i32_array("F", &row, &mut ints);
        assert!(ints.is_empty());
    }

    #[test]
    fn newest_buffer_and_live_bounds() {
        let mut header = SdkHeader::parse(&[0u8; SDK_HEADER_LEN]).expect("parses");
        header.num_buf = 3;
        header.buf_len = 16;
        header.var_bufs[0] = VarBuf {
            tick_count: 5,
            buf_offset: 200,
        };
        header.var_bufs[1] = VarBuf {
            tick_count: 9,
            buf_offset: 300,
        };
        header.var_bufs[2] = VarBuf {
            tick_count: 7,
            buf_offset: 50,
        };
        header.var_bufs[3] = VarBuf {
            tick_count: 99,
            buf_offset: 400,
        };
        assert_eq!(header.newest_buffer(400).map(|b| b.tick_count), Some(9));
        assert_eq!(header.newest_buffer(310).map(|b| b.tick_count), Some(5));
        assert_eq!(header.newest_buffer(100), None);
        assert!(header.validate_live(4096).is_err());
        header.num_vars = 1;
        header.var_header_offset = 112;
        header.session_info_offset = 256;
        header.session_info_len = 10;
        assert!(header.validate_live(4096).is_ok());
        assert!(header.validate_live(260).is_err());
    }
}
