//! The entry table: every oracle function a native kernel replaces, by the label the vectors
//! and the shims use (`module.name`, for example `analysis_engine._lap_summaries`).
//!
//! Each unit lists its entries in its own module (`table::ENTRIES`, ...); [`UNITS`] collects
//! them, so adding an entry never touches this file.

use super::error::KernelError;
use super::session::Session;
use super::value::{Dict, PyValue};
use super::{field, laps, sections, table, traces};

/// A native entry: called with the session (the table and its caches) and the decoded call,
/// it returns the oracle's return value or [`KernelError`]. It may change an argument only
/// where the oracle mutates the same argument; the replay compares every argument afterwards.
pub type EntryFn = fn(&Session, &mut Call) -> Result<PyValue, KernelError>;

#[derive(Clone, Copy)]
pub struct Entry {
    /// `module.name`, as `Entry.label` in `emit_kernel_vectors.py`.
    pub name: &'static str,
    pub run: EntryFn,
}

/// Unit name (the generator's `UNITS` key and the vector folder) and its entries.
pub const UNITS: &[(&str, &[Entry])] = &[
    ("table", table::ENTRIES),
    ("laps", laps::ENTRIES),
    ("traces", traces::ENTRIES),
    ("sections", sections::ENTRIES),
    ("field", field::ENTRIES),
];

/// Entries the `kernels` process serves in place of the [`UNITS`] entry of the same label
/// (`phase2-plan.md` section 4.2): `_setup_channel_series` answers `{"$handle": n}` and keeps
/// the series, which only `_setup_telemetry` and `_track_profile` read, in the process. The
/// replay entries return the value the vectors record; the analysis is the same either way.
pub const HANDOFF: &[Entry] = &[Entry {
    name: "analysis_engine._setup_channel_series",
    run: table::entry_setup_channel_series_handle,
}];

/// Every entry of every unit.
pub fn entries() -> impl Iterator<Item = &'static Entry> {
    UNITS.iter().flat_map(|(_, entries)| entries.iter())
}

/// The entry named `name`, if this build provides it.
pub fn find(name: &str) -> Option<&'static Entry> {
    entries().find(|entry| entry.name == name)
}

/// The entry the `kernels` process runs for `name`: its [`HANDOFF`] variant, else [`find`].
pub fn find_handoff(name: &str) -> Option<&'static Entry> {
    HANDOFF
        .iter()
        .find(|entry| entry.name == name)
        .or_else(|| find(name))
}

/// The arguments of one call: positional, then keyword in the order the caller passed them.
#[derive(Clone, Debug, Default)]
pub struct Call {
    pub args: Vec<PyValue>,
    pub kwargs: Vec<(String, PyValue)>,
}

/// Where a bound parameter's value sits in the [`Call`].
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Slot {
    Positional(usize),
    Keyword(usize),
}

/// A call bound to a signature (`Call::bind`).
#[derive(Clone, Debug)]
pub struct Bound {
    names: Vec<&'static str>,
    slots: Vec<Option<Slot>>,
    entry: &'static str,
}

impl Call {
    /// The call a codec document's `args` (a list) and `kwargs` (a `str`-keyed dict) describe.
    pub fn from_values(args: PyValue, kwargs: PyValue) -> Result<Call, KernelError> {
        let PyValue::List(args) = args else {
            return Err(KernelError::codec(format!(
                "args is a {}, not a list",
                args.type_name()
            )));
        };
        let PyValue::Dict(kwargs) = kwargs else {
            return Err(KernelError::codec(format!(
                "kwargs is a {}, not a dict",
                kwargs.type_name()
            )));
        };
        let mut named = Vec::with_capacity(kwargs.len());
        for (key, value) in kwargs.into_items() {
            let PyValue::Str(key) = key else {
                return Err(KernelError::codec("a keyword argument name is not a str"));
            };
            named.push((key, value));
        }
        Ok(Call {
            args,
            kwargs: named,
        })
    }

    /// Binds the call as Python binds it to `def f(<positional>, *, <keyword_only>)`: a
    /// positional parameter may come by position or by keyword, a keyword-only one by keyword.
    /// Too many positional arguments, an unknown keyword or a parameter given twice is an
    /// `Internal` error (the oracle never makes such a call; a default would hide a broken
    /// seam). Missing parameters are left for [`Bound::require`] or the entry's default.
    pub fn bind(
        &self,
        entry: &'static str,
        positional: &[&'static str],
        keyword_only: &[&'static str],
    ) -> Result<Bound, KernelError> {
        if self.args.len() > positional.len() {
            return Err(KernelError::internal(format!(
                "{entry}: {} positional arguments, the signature takes {}",
                self.args.len(),
                positional.len()
            )));
        }
        let names: Vec<&'static str> = positional.iter().chain(keyword_only).copied().collect();
        let mut slots: Vec<Option<Slot>> = vec![None; names.len()];
        for (index, slot) in slots.iter_mut().enumerate().take(self.args.len()) {
            *slot = Some(Slot::Positional(index));
        }
        for (index, (key, _)) in self.kwargs.iter().enumerate() {
            let Some(position) = names.iter().position(|name| name == key) else {
                return Err(KernelError::internal(format!(
                    "{entry}: unexpected keyword argument {key:?}"
                )));
            };
            if slots[position].is_some() {
                return Err(KernelError::internal(format!(
                    "{entry}: argument {key:?} given twice"
                )));
            }
            slots[position] = Some(Slot::Keyword(index));
        }
        Ok(Bound {
            names,
            slots,
            entry,
        })
    }

    pub fn value(&self, slot: Slot) -> &PyValue {
        match slot {
            Slot::Positional(index) => &self.args[index],
            Slot::Keyword(index) => &self.kwargs[index].1,
        }
    }

    pub fn value_mut(&mut self, slot: Slot) -> &mut PyValue {
        match slot {
            Slot::Positional(index) => &mut self.args[index],
            Slot::Keyword(index) => &mut self.kwargs[index].1,
        }
    }

    /// The arguments as the codec document's `args` and `kwargs` values.
    pub fn to_values(&self) -> (PyValue, PyValue) {
        let kwargs: Dict = self
            .kwargs
            .iter()
            .map(|(key, value)| (PyValue::Str(key.clone()), value.clone()))
            .collect();
        (PyValue::List(self.args.clone()), PyValue::Dict(kwargs))
    }

    /// Argument `key` in the vector's `mutated` naming: `args.<index>` or `kwargs.<name>`.
    pub fn argument(&self, key: &str) -> Option<&PyValue> {
        if let Some(index) = key.strip_prefix("args.") {
            return self.args.get(index.parse::<usize>().ok()?);
        }
        let name = key.strip_prefix("kwargs.")?;
        self.kwargs
            .iter()
            .find(|(k, _)| k == name)
            .map(|(_, value)| value)
    }

    /// Every argument with its `mutated` key, positional first.
    pub fn arguments(&self) -> Vec<(String, &PyValue)> {
        let mut out: Vec<(String, &PyValue)> = self
            .args
            .iter()
            .enumerate()
            .map(|(index, value)| (format!("args.{index}"), value))
            .collect();
        out.extend(
            self.kwargs
                .iter()
                .map(|(key, value)| (format!("kwargs.{key}"), value)),
        );
        out
    }
}

impl Bound {
    pub fn slot(&self, name: &str) -> Option<Slot> {
        let index = self.names.iter().position(|n| *n == name)?;
        self.slots[index]
    }

    /// The parameter's value, or `None` when the caller relied on its default.
    pub fn get<'c>(&self, call: &'c Call, name: &str) -> Option<&'c PyValue> {
        self.slot(name).map(|slot| call.value(slot))
    }

    /// A parameter without a default: missing is an `Internal` error.
    pub fn require<'c>(&self, call: &'c Call, name: &str) -> Result<&'c PyValue, KernelError> {
        self.get(call, name).ok_or_else(|| {
            KernelError::internal(format!(
                "{}: missing required argument {name:?}",
                self.entry
            ))
        })
    }

    /// A parameter that must be the analysis table (`{"$table": 1}`).
    pub fn require_table(&self, call: &Call, name: &str) -> Result<(), KernelError> {
        match self.require(call, name)? {
            PyValue::Table => Ok(()),
            other => Err(KernelError::internal(format!(
                "{}: {name} is a {}, not the analysis table",
                self.entry,
                other.type_name()
            ))),
        }
    }
}
