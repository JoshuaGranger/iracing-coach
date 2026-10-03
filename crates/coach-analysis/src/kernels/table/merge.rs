//! The column merge of a multi-file group (`workflow._load_analysis_telemetry`, the
//! `reconnect` worker case), from the files' decode stores to one `merge`-stage store.
//!
//! Python's rule, reproduced exactly:
//!
//! - files with no sample are skipped; the rest keep their order (`file_index` is the position
//!   among them);
//! - channels: the caller's preferred order (`workflow.ANALYSIS_CHANNELS`) for the names any
//!   file holds, then every other name in file order;
//! - each sample's time is `_finite_number` of the file's `SessionTime` (or, without that
//!   column, `SessionTimeOfDay`): `bool`, text `float()` rejects, a list and a non-finite value
//!   are no time;
//! - references sorted by `(has_no_time, time, file_index, sample_index)`, where a sample with
//!   no time sorts by `float(file_index)` after every timed one;
//! - a timed sample within 1e-9 s of the previous kept timed sample is a duplicate: it fills
//!   only the previous row's missing cells (first non-missing wins) and adds no row; any other
//!   sample adds a row, missing where its file lacks the channel.
//!
//! The metadata merge (representative session info, variable catalogues, rates) stays Python.
//! A channel one file stores as `float` and another as `double` becomes a `double` column:
//! both decode to Python floats, and widening a `float` is exact. Any other difference of
//! element type or count has no single column type (Python would hold `int` beside `float`,
//! or lists of two lengths), so the merge fails instead of guessing.

use std::cmp::Ordering;
use std::collections::HashMap;

use coach_ibt::VarType;

use crate::decode::{Column, Element};
use crate::kernels::error::KernelError;
use crate::store::{ColumnStore, Merged, STAGE_DECODE, Sampling};

/// Python's message when no file of the group has a sample (`WorkflowError`).
pub const NO_SAMPLES: &str = "The selected IBT recording contains no telemetry samples.";

/// One file's column, read once.
struct Source<'a> {
    store: &'a ColumnStore,
    columns: HashMap<&'a str, usize>,
    sample_count: usize,
    record_indices: Vec<u32>,
}

/// `workflow._finite_number(value)` of every sample of column `column`.
fn finite_numbers(
    store: &ColumnStore,
    column: usize,
    samples: usize,
) -> Result<Vec<Option<f64>>, KernelError> {
    let column = store
        .column(column)
        .ok_or_else(|| KernelError::internal("time column unreadable"))?;
    if column.count != 1 && column.element != Element::Utf8 {
        return Ok(vec![None; samples]); // float(list) is a TypeError
    }
    let mut result = Vec::with_capacity(samples);
    for index in 0..samples {
        let word = |width: usize| -> [u8; 8] {
            let mut bytes = [0u8; 8];
            bytes[..width].copy_from_slice(&column.data[index * width..(index + 1) * width]);
            bytes
        };
        let number = match column.element {
            Element::Bool => None,
            Element::I32 => {
                let w = word(4);
                Some(f64::from(i32::from_le_bytes([w[0], w[1], w[2], w[3]])))
            }
            Element::U32 => {
                let w = word(4);
                Some(f64::from(u32::from_le_bytes([w[0], w[1], w[2], w[3]])))
            }
            Element::F32 => {
                let w = word(4);
                Some(f64::from(f32::from_le_bytes([w[0], w[1], w[2], w[3]])))
            }
            Element::F64 => Some(f64::from_le_bytes(word(8))),
            Element::Utf8 => {
                let start = column.text_offsets[index] as usize;
                let end = column.text_offsets[index + 1] as usize;
                let text = std::str::from_utf8(&column.data[start..end])
                    .map_err(|_| KernelError::internal("text sample is not UTF-8"))?;
                match coach_numerics::parse_float(text) {
                    Ok(number) => Some(number),
                    Err(error) if error.kind == coach_numerics::PyErrorKind::ValueError => None,
                    Err(error) => return Err(error.into()),
                }
            }
        };
        result.push(number.filter(|n| n.is_finite()));
    }
    Ok(result)
}

/// A merged cell's source: (file position, sample index).
type Cell = Option<(usize, usize)>;

/// Merges the decode stores of one group (in recording order) as
/// `workflow._load_analysis_telemetry` merges their tables.
pub fn merge(inputs: &[&ColumnStore], preferred: &[String]) -> Result<Merged, KernelError> {
    let mut sources = Vec::new();
    for store in inputs {
        if store.stage() != STAGE_DECODE {
            return Err(KernelError::internal(format!(
                "merge input of stage {:?}, expected decode",
                store.stage()
            )));
        }
        let sample_count = usize::try_from(store.header.sampling.sample_count)
            .map_err(|_| KernelError::internal("sample count beyond usize"))?;
        if sample_count == 0 {
            continue;
        }
        sources.push(Source {
            store,
            columns: store
                .header
                .columns
                .iter()
                .enumerate()
                .map(|(i, c)| (c.name.as_str(), i))
                .collect(),
            sample_count,
            record_indices: store.sample_indices(),
        });
    }
    if sources.is_empty() {
        return Err(KernelError::raises("WorkflowError", NO_SAMPLES));
    }
    if sources.len() > usize::from(u16::MAX) + 1 {
        return Err(KernelError::internal(
            "more files than a u16 file index holds",
        ));
    }

    let mut names: Vec<String> = Vec::new();
    for name in preferred {
        if !names.contains(name)
            && sources
                .iter()
                .any(|s| s.columns.contains_key(name.as_str()))
        {
            names.push(name.clone());
        }
    }
    for source in &sources {
        for column in &source.store.header.columns {
            if !names.contains(&column.name) {
                names.push(column.name.clone());
            }
        }
    }

    // (has_no_time, time, file_index, sample_index)
    let mut references: Vec<(u8, f64, usize, usize)> = Vec::new();
    for (file, source) in sources.iter().enumerate() {
        let time_column = source
            .columns
            .get("SessionTime")
            .or_else(|| source.columns.get("SessionTimeOfDay"))
            .copied();
        let times = match time_column {
            Some(column) => finite_numbers(source.store, column, source.sample_count)?,
            None => vec![None; source.sample_count],
        };
        for (sample, time) in times.into_iter().enumerate() {
            references.push(match time {
                Some(time) => (0, time, file, sample),
                None => (1, file as f64, file, sample),
            });
        }
    }
    references.sort_by(|a, b| {
        a.0.cmp(&b.0)
            .then(a.1.partial_cmp(&b.1).unwrap_or(Ordering::Equal))
            .then(a.2.cmp(&b.2))
            .then(a.3.cmp(&b.3))
    });

    let mut cells: Vec<Vec<Cell>> = vec![Vec::new(); names.len()];
    let mut file_index: Vec<u16> = Vec::new();
    let mut sample_indices: Vec<u32> = Vec::new();
    let column_of: Vec<Vec<Option<usize>>> = sources
        .iter()
        .map(|source| {
            names
                .iter()
                .map(|name| source.columns.get(name.as_str()).copied())
                .collect()
        })
        .collect();
    let mut previous_time: Option<f64> = None;
    for &(no_time, time, file, sample) in &references {
        let duplicate =
            no_time == 0 && previous_time.is_some_and(|previous| (time - previous).abs() <= 1e-9);
        if !duplicate {
            for (channel, column) in cells.iter_mut().enumerate() {
                column.push(column_of[file][channel].map(|_| (file, sample)));
            }
            file_index.push(u16::try_from(file).unwrap_or(u16::MAX));
            sample_indices.push(sources[file].record_indices[sample]);
            previous_time = (no_time == 0).then_some(time);
            continue;
        }
        for (channel, column) in cells.iter_mut().enumerate() {
            let last = column
                .last_mut()
                .ok_or_else(|| KernelError::internal("duplicate before any row"))?;
            if last.is_none() && column_of[file][channel].is_some() {
                *last = Some((file, sample));
            }
        }
    }

    let length = file_index.len();
    let mut columns = Vec::with_capacity(names.len());
    let mut present = Vec::with_capacity(names.len());
    for (channel, name) in names.iter().enumerate() {
        let first = sources
            .iter()
            .enumerate()
            .find_map(|(file, source)| column_of[file][channel].map(|c| (source, c)))
            .ok_or_else(|| KernelError::internal("a merged channel no file holds"))?;
        let template = first
            .0
            .store
            .column(first.1)
            .ok_or_else(|| KernelError::internal(format!("column {name:?} unreadable")))?;
        let mut parts: Vec<Option<Column>> = Vec::with_capacity(sources.len());
        let floats = |element: Element| matches!(element, Element::F32 | Element::F64);
        let mut element = template.element;
        for (file, source) in sources.iter().enumerate() {
            let part = match column_of[file][channel] {
                Some(index) => {
                    let column = source.store.column(index).ok_or_else(|| {
                        KernelError::internal(format!("column {name:?} unreadable"))
                    })?;
                    let widened = floats(column.element) && floats(template.element);
                    if column.element == Element::F64 && widened {
                        element = Element::F64;
                    }
                    if (column.element != template.element && !widened)
                        || column.count != template.count
                    {
                        return Err(KernelError::internal(format!(
                            "channel {name:?} is stored as {} x{} in one file and {} x{} in another",
                            template.element.name(),
                            template.count,
                            column.element.name(),
                            column.count
                        )));
                    }
                    Some(column)
                }
                None => None,
            };
            parts.push(part);
        }
        let mut column = Column {
            name: name.clone(),
            var_type: if element == template.element {
                template.var_type
            } else {
                VarType::Double
            },
            count: template.count,
            var_offset: template.var_offset,
            element,
            data: Vec::new(),
            text_offsets: Vec::new(),
        };
        let width = element.width() * template.count;
        if template.element == Element::Utf8 {
            column.text_offsets.push(0);
        }
        let mut flags = Vec::with_capacity(length);
        for cell in &cells[channel] {
            match cell {
                Some((file, sample)) => {
                    let part = parts[*file].as_ref().ok_or_else(|| {
                        KernelError::internal("cell from a file without the channel")
                    })?;
                    if template.element == Element::Utf8 {
                        let start = part.text_offsets[*sample] as usize;
                        let end = part.text_offsets[*sample + 1] as usize;
                        column.data.extend_from_slice(&part.data[start..end]);
                    } else if part.element == element {
                        column
                            .data
                            .extend_from_slice(&part.data[sample * width..(sample + 1) * width]);
                    } else {
                        // A float cell in a double column: each element widened exactly.
                        let words = &part.data[sample * 4 * template.count..][..4 * template.count];
                        for word in words.as_chunks::<4>().0 {
                            let value = f64::from(f32::from_le_bytes(*word));
                            column.data.extend_from_slice(&value.to_le_bytes());
                        }
                    }
                    flags.push(1u8);
                }
                None => {
                    if template.element != Element::Utf8 {
                        column.data.extend(std::iter::repeat_n(0u8, width));
                    }
                    flags.push(0u8);
                }
            }
            if template.element == Element::Utf8 {
                column.text_offsets.push(column.data.len() as u64);
            }
        }
        present.push(flags.contains(&0).then_some(flags));
        columns.push(column);
    }

    let first = sources[0].store;
    let sample_rate = sources
        .iter()
        .map(|source| source.store.header.sampling.sample_rate_hz)
        .map(|rate| if rate.is_finite() { rate } else { 0.0 })
        .fold(
            f64::NEG_INFINITY,
            |best, rate| if rate > best { rate } else { best },
        );
    Ok(Merged {
        source: first.header.source,
        sampling: Sampling {
            target_hz: first.header.sampling.target_hz,
            step: first.header.sampling.step,
            integral_step: first.header.sampling.integral_step,
            sample_rate_hz: sample_rate,
            sample_count: length as u64,
        },
        sample_indices,
        file_index,
        columns,
        present,
    })
}
