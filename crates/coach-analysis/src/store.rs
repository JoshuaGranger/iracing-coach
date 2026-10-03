//! Coach column store (`.ccs`), format version 1: the decode stage's intermediate file, read by
//! the Python seam (`native_stages.py`) and later by native kernels.
//!
//! ```text
//! 0   8 bytes  magic "COACHCS\0"
//! 8   u32 LE   format version (1)
//! 12  u32 LE   header length H
//! 16  H bytes  header, UTF-8 JSON (StoreHeader)
//! ..  zero padding to a multiple of 8: the data section starts here
//! ..  blocks; every block offset is relative to the data section and a multiple of 8
//! ```
//!
//! Specified in `docs/08-rewrite/contracts/worker-interface.md` section 7. A reader rejects
//! another magic, version or stage, a column whose element is not its type's, and any block
//! outside the data section, unaligned, or not exactly the length the header implies; unknown
//! header fields are ignored.

use std::borrow::Cow;
use std::fs::File;
use std::io::{BufWriter, Write};
use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::DecodeError;
use crate::decode::{Column, Decoded, Element, SourceLayout};

pub const MAGIC: [u8; 8] = *b"COACHCS\0";
pub const FORMAT_VERSION: u32 = 1;
pub const FORMAT_NAME: &str = "coach-column-store";
const ALIGN: u64 = 8;

/// A byte range of the data section.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct Block {
    pub offset: u64,
    pub length: u64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Sampling {
    /// The requested rate; null is native rate (`target_hz=None`).
    pub target_hz: Option<f64>,
    pub step: f64,
    pub integral_step: bool,
    /// `sample_rate_hz` of the table, a Python float.
    pub sample_rate_hz: f64,
    pub sample_count: u64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ColumnHeader {
    pub name: String,
    pub type_code: i32,
    pub count: u32,
    pub var_offset: i32,
    /// `bool`, `i32`, `u32`, `f32`, `f64` or `utf8`.
    pub element: String,
    pub data: Block,
    /// `utf8` only: `sample_count + 1` u64 offsets into `data`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub text_offsets: Option<Block>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct StoreHeader {
    pub format: String,
    pub version: u32,
    /// The stage that wrote the store (`decode`).
    pub stage: String,
    pub producer: String,
    pub source: SourceLayout,
    pub sampling: Sampling,
    /// `all` (`channels=None`) or `selected`.
    pub channel_mode: String,
    /// u32 LE native record indices, one per sample.
    pub sample_indices: Block,
    pub columns: Vec<ColumnHeader>,
}

fn align(value: u64) -> u64 {
    value.div_ceil(ALIGN) * ALIGN
}

/// Blocks of the data section in file order, with their offsets.
struct Blocks<'a> {
    cursor: u64,
    blocks: Vec<PlacedBlock<'a>>,
}

impl<'a> Blocks<'a> {
    fn place(&mut self, bytes: Cow<'a, [u8]>) -> Block {
        let block = Block {
            offset: self.cursor,
            length: bytes.len() as u64,
        };
        self.cursor = align(self.cursor + block.length);
        self.blocks.push((block.offset, bytes));
        block
    }
}

/// A data-section block's offset and bytes.
type PlacedBlock<'a> = (u64, Cow<'a, [u8]>);

/// Lays out the blocks of `decoded` and returns the header and the blocks' bytes in order.
fn layout(decoded: &Decoded) -> (StoreHeader, Vec<PlacedBlock<'_>>) {
    let mut blocks = Blocks {
        cursor: 0,
        blocks: Vec::new(),
    };
    let indices: Vec<u8> = decoded
        .sample_indices
        .iter()
        .flat_map(|index| index.to_le_bytes())
        .collect();
    let sample_indices = blocks.place(Cow::Owned(indices));
    let columns = decoded
        .columns
        .iter()
        .map(|column| {
            let data = blocks.place(Cow::Borrowed(&column.data));
            let text_offsets = (column.element == Element::Utf8).then(|| {
                let offsets: Vec<u8> = column
                    .text_offsets
                    .iter()
                    .flat_map(|offset| offset.to_le_bytes())
                    .collect();
                blocks.place(Cow::Owned(offsets))
            });
            ColumnHeader {
                name: column.name.clone(),
                type_code: column.var_type as i32,
                count: column.count as u32,
                var_offset: column.var_offset,
                element: column.element.name().to_owned(),
                data,
                text_offsets,
            }
        })
        .collect();
    let header = StoreHeader {
        format: FORMAT_NAME.to_owned(),
        version: FORMAT_VERSION,
        stage: "decode".to_owned(),
        producer: format!("coach-analysis {}", env!("CARGO_PKG_VERSION")),
        source: decoded.source,
        sampling: Sampling {
            target_hz: decoded.target_hz,
            step: decoded.plan.step,
            integral_step: decoded.plan.integral_step(),
            sample_rate_hz: decoded.plan.output_rate,
            sample_count: decoded.sample_indices.len() as u64,
        },
        channel_mode: if decoded.all_channels {
            "all"
        } else {
            "selected"
        }
        .to_owned(),
        sample_indices,
        columns,
    };
    (header, blocks.blocks)
}

/// Writes `decoded` to `path` (created or truncated) and returns the bytes written.
pub fn write(decoded: &Decoded, path: &Path) -> Result<u64, DecodeError> {
    let (header, blocks) = layout(decoded);
    let header_json = serde_json::to_vec(&header)
        .map_err(|error| DecodeError::Store(format!("header: {error}")))?;
    let header_len = u32::try_from(header_json.len())
        .map_err(|_| DecodeError::Store("header longer than 4 GiB".to_owned()))?;
    let data_start = align(16 + u64::from(header_len));
    let mut out = BufWriter::with_capacity(1 << 20, File::create(path)?);
    out.write_all(&MAGIC)?;
    out.write_all(&FORMAT_VERSION.to_le_bytes())?;
    out.write_all(&header_len.to_le_bytes())?;
    out.write_all(&header_json)?;
    let mut position = 16 + u64::from(header_len);
    let zeros = [0u8; ALIGN as usize];
    let pad_to = |target: u64, position: &mut u64, out: &mut BufWriter<File>| {
        let gap = (target - *position) as usize;
        *position = target;
        out.write_all(&zeros[..gap])
    };
    pad_to(data_start, &mut position, &mut out)?;
    for (offset, bytes) in &blocks {
        pad_to(data_start + offset, &mut position, &mut out)?;
        out.write_all(bytes)?;
        position += bytes.len() as u64;
    }
    pad_to(align(position), &mut position, &mut out)?;
    out.flush()?;
    Ok(position)
}

/// A store read back into memory (tests, `coach-analysis inspect`, later native stages).
#[derive(Debug, Clone)]
pub struct ColumnStore {
    pub header: StoreHeader,
    data: Vec<u8>,
}

impl ColumnStore {
    pub fn read(path: &Path) -> Result<ColumnStore, DecodeError> {
        ColumnStore::parse(std::fs::read(path)?)
    }

    pub fn parse(bytes: Vec<u8>) -> Result<ColumnStore, DecodeError> {
        let bad = |message: &str| DecodeError::Store(message.to_owned());
        if bytes.len() < 16 || bytes[..8] != MAGIC {
            return Err(bad("not a coach column store (magic)"));
        }
        let version = u32::from_le_bytes([bytes[8], bytes[9], bytes[10], bytes[11]]);
        if version != FORMAT_VERSION {
            return Err(DecodeError::Store(format!(
                "coach column store version {version} is not supported (expected {FORMAT_VERSION})"
            )));
        }
        let header_len = u32::from_le_bytes([bytes[12], bytes[13], bytes[14], bytes[15]]) as usize;
        let header_bytes = bytes
            .get(16..16 + header_len)
            .ok_or_else(|| bad("header runs past the end of the file"))?;
        let header: StoreHeader = serde_json::from_slice(header_bytes)
            .map_err(|error| DecodeError::Store(format!("header: {error}")))?;
        if header.format != FORMAT_NAME || header.version != FORMAT_VERSION {
            return Err(bad("header format or version does not match the file"));
        }
        if header.stage != "decode" {
            return Err(DecodeError::Store(format!(
                "stage {:?} is not decode",
                header.stage
            )));
        }
        let data_start = align(16 + header_len as u64) as usize;
        let data = bytes.get(data_start..).unwrap_or_default().to_vec();
        let store = ColumnStore { header, data };
        store.validate()?;
        Ok(store)
    }

    /// Every block inside the data section and 8-byte aligned, and every block exactly the
    /// length its header implies, so no reader drops a partial word (review findings 7, 12):
    /// `sample_count` u32 indices; per numeric column `sample_count * count` elements of the
    /// width its type is stored at; per `utf8` column `sample_count + 1` u64 offsets starting
    /// at 0, non-decreasing, ending at the text length, each sample valid UTF-8.
    fn validate(&self) -> Result<(), DecodeError> {
        let fail = |message: String| Err(DecodeError::Store(message));
        let samples = self.header.sampling.sample_count;
        let block = |name: &str, what: &str, block: Block, length: Option<u64>| {
            let Some(bytes) = self.block(block) else {
                return Err(DecodeError::Store(format!(
                    "{name}: {what} block runs past the end of the file"
                )));
            };
            if !block.offset.is_multiple_of(ALIGN) {
                return Err(DecodeError::Store(format!(
                    "{name}: {what} block offset {} is not a multiple of 8",
                    block.offset
                )));
            }
            if length != Some(block.length) {
                return Err(DecodeError::Store(format!(
                    "{name}: {what} block of {} bytes, expected {}",
                    block.length,
                    length.map_or_else(|| "a representable length".to_owned(), |n| n.to_string())
                )));
            }
            Ok(bytes)
        };
        block(
            "sample_indices",
            "index",
            self.header.sample_indices,
            samples.checked_mul(4),
        )?;
        let mut names = std::collections::HashSet::new();
        for column in &self.header.columns {
            let name = format!("column {:?}", column.name);
            if column.name.is_empty() || !names.insert(column.name.as_str()) {
                return fail(format!("{name}: empty or repeated name"));
            }
            let Some(element) = Element::from_name(&column.element) else {
                return fail(format!("{name}: unknown element {:?}", column.element));
            };
            let Some(var_type) = coach_ibt::VarType::from_code(column.type_code) else {
                return fail(format!("{name}: unknown type code {}", column.type_code));
            };
            if Element::for_type(var_type) != element {
                return fail(format!(
                    "{name}: type code {} is not stored as {}",
                    column.type_code, column.element
                ));
            }
            if column.count == 0 {
                return fail(format!("{name}: count 0"));
            }
            if element == Element::Utf8 {
                let Some(offsets_block) = column.text_offsets else {
                    return fail(format!("{name}: utf8 column without text_offsets"));
                };
                let words = block(
                    &name,
                    "text offset",
                    offsets_block,
                    samples.checked_add(1).and_then(|n| n.checked_mul(8)),
                )?;
                let text = block(&name, "text", column.data, Some(column.data.length))?;
                let offsets: Vec<u64> = words
                    .as_chunks::<8>()
                    .0
                    .iter()
                    .map(|word| u64::from_le_bytes(*word))
                    .collect();
                if offsets.first() != Some(&0)
                    || offsets.last() != Some(&column.data.length)
                    || offsets.windows(2).any(|pair| pair[0] > pair[1])
                {
                    return fail(format!(
                        "{name}: text offsets must rise from 0 to the text length"
                    ));
                }
                for pair in offsets.windows(2) {
                    // In range: offsets rise to text.len(), checked above.
                    let piece = &text[pair[0] as usize..pair[1] as usize];
                    if std::str::from_utf8(piece).is_err() {
                        return fail(format!("{name}: a sample is not UTF-8"));
                    }
                }
            } else {
                if column.text_offsets.is_some() {
                    return fail(format!(
                        "{name}: text_offsets on a {} column",
                        column.element
                    ));
                }
                let length = samples
                    .checked_mul(u64::from(column.count))
                    .and_then(|n| n.checked_mul(element.width() as u64));
                block(&name, "data", column.data, length)?;
            }
        }
        Ok(())
    }

    /// Bytes of `block`, or None when it lies outside the data section.
    pub fn block(&self, block: Block) -> Option<&[u8]> {
        let start = usize::try_from(block.offset).ok()?;
        let end = start.checked_add(usize::try_from(block.length).ok()?)?;
        self.data.get(start..end)
    }

    /// The sample indices; [`ColumnStore::parse`] checked the block holds exactly
    /// `sample_count` whole words.
    pub fn sample_indices(&self) -> Vec<u32> {
        self.block(self.header.sample_indices)
            .unwrap_or_default()
            .as_chunks::<4>()
            .0
            .iter()
            .map(|word| u32::from_le_bytes(*word))
            .collect()
    }

    /// Column `index` as a decoded [`Column`].
    pub fn column(&self, index: usize) -> Option<Column> {
        let header = self.header.columns.get(index)?;
        let element = Element::from_name(&header.element)?;
        let text_offsets = match header.text_offsets {
            Some(block) => self
                .block(block)?
                .as_chunks::<8>()
                .0
                .iter()
                .map(|word| u64::from_le_bytes(*word))
                .collect(),
            None => Vec::new(),
        };
        Some(Column {
            name: header.name.clone(),
            var_type: coach_ibt::VarType::from_code(header.type_code)?,
            count: header.count as usize,
            var_offset: header.var_offset,
            element,
            data: self.block(header.data)?.to_vec(),
            text_offsets,
        })
    }
}
