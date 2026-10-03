//! One analysis: the decode store, the table metadata, the steering basis Python computed, the
//! per-session caches the units keep (unit U1's `Table` with its view cache) and the record of
//! accessed channels. A call runs through [`Session::call`], which reports what the entry
//! returned and which channels it accessed, as the oracle's wrapper records them.

use std::any::{Any, TypeId};
use std::cell::RefCell;
use std::collections::{BTreeSet, HashMap};
use std::path::Path;
use std::rc::Rc;

use crate::store::ColumnStore;

use super::codec;
use super::dispatch::{self, Call};
use super::error::KernelError;
use super::value::PyValue;

/// `TelemetryTable` metadata as `TelemetryTable.__init__` derives it (`table.json` in the
/// vectors, `"table"` in the `kernels` start request).
#[derive(Clone, Debug)]
pub struct TableMeta {
    pub metadata: PyValue,
    pub session_info: PyValue,
    pub variables: PyValue,
    pub available_variables: PyValue,
    /// `channel_units` in insertion order.
    pub channel_units: Vec<(String, String)>,
    pub length: usize,
    /// `list(table.channels)`: the store's columns in this order.
    pub channel_names: Vec<String>,
}

impl TableMeta {
    /// Reads the fields from a decoded `table.json` or start-request `"table"` object. Every
    /// field is required; unknown fields are ignored.
    pub fn from_value(value: &PyValue) -> Result<TableMeta, KernelError> {
        let field = |name: &str| {
            value
                .get(name)
                .ok_or_else(|| KernelError::codec(format!("table metadata: {name:?} missing")))
        };
        let malformed =
            |name: &str| KernelError::codec(format!("table metadata: {name:?} malformed"));
        let units = field("channel_units")?
            .as_dict()
            .ok_or_else(|| malformed("channel_units"))?;
        let mut channel_units = Vec::with_capacity(units.len());
        for (key, unit) in units.iter() {
            match (key, unit) {
                (PyValue::Str(name), PyValue::Str(unit)) => {
                    channel_units.push((name.clone(), unit.clone()));
                }
                _ => return Err(malformed("channel_units")),
            }
        }
        let length = field("length")?
            .as_int()
            .and_then(|n| usize::try_from(n).ok())
            .ok_or_else(|| malformed("length"))?;
        let names = field("channel_names")?
            .as_list()
            .ok_or_else(|| malformed("channel_names"))?;
        let channel_names = names
            .iter()
            .map(|name| name.as_str().map(str::to_owned))
            .collect::<Option<Vec<String>>>()
            .ok_or_else(|| malformed("channel_names"))?;
        Ok(TableMeta {
            metadata: field("metadata")?.clone(),
            session_info: field("session_info")?.clone(),
            variables: field("variables")?.clone(),
            available_variables: field("available_variables")?.clone(),
            channel_units,
            length,
            channel_names,
        })
    }
}

/// The channels a call accessed: the table's `accessed_channels` for the duration of one call.
/// Unit U1's table records into a clone of the session's record.
#[derive(Clone, Debug, Default)]
pub struct AccessRecord(Rc<RefCell<BTreeSet<String>>>);

impl AccessRecord {
    pub fn record(&self, channel: &str) {
        let mut set = self.0.borrow_mut();
        if !set.contains(channel) {
            set.insert(channel.to_owned());
        }
    }

    /// The names recorded since the last take, sorted (the vectors' `accessed`).
    pub fn take(&self) -> BTreeSet<String> {
        std::mem::take(&mut *self.0.borrow_mut())
    }
}

/// What a call produced.
#[derive(Clone, Debug)]
pub enum Outcome {
    Value(PyValue),
    /// The oracle's exception for the same input.
    Raises {
        class: String,
        message: String,
    },
    /// A failure of the native stage (`NativeStageError`).
    Error(String),
}

/// A call's outcome and the channels it accessed.
#[derive(Clone, Debug)]
pub struct Answer {
    pub outcome: Outcome,
    pub accessed: BTreeSet<String>,
}

pub struct Session {
    store: Rc<ColumnStore>,
    meta: Rc<TableMeta>,
    steering_basis: PyValue,
    access: AccessRecord,
    caches: RefCell<HashMap<TypeId, Rc<dyn Any>>>,
    handles: RefCell<Vec<Rc<dyn Any>>>,
}

impl Session {
    /// A session over a decode (or merged) store. The store must hold exactly the table's
    /// channels in the table's order and `length` samples.
    pub fn new(
        store: ColumnStore,
        meta: TableMeta,
        steering_basis: PyValue,
    ) -> Result<Session, KernelError> {
        let names: Vec<&str> = store
            .header
            .columns
            .iter()
            .map(|c| c.name.as_str())
            .collect();
        if names != meta.channel_names {
            return Err(KernelError::internal(
                "the store's columns are not the table's channels in the table's order",
            ));
        }
        if usize::try_from(store.header.sampling.sample_count).ok() != Some(meta.length) {
            return Err(KernelError::internal(format!(
                "the store holds {} samples, the table {}",
                store.header.sampling.sample_count, meta.length
            )));
        }
        Ok(Session {
            store: Rc::new(store),
            meta: Rc::new(meta),
            steering_basis,
            access: AccessRecord::default(),
            caches: RefCell::new(HashMap::new()),
            handles: RefCell::new(Vec::new()),
        })
    }

    /// The session of one vector folder: `table.ccs` and `table.json` (with its
    /// `steering_basis`, `None` when the folder predates it).
    pub fn open_vector_folder(folder: &Path) -> Result<Session, KernelError> {
        let at = |error: String| KernelError::codec(format!("{}: {error}", folder.display()));
        let store = ColumnStore::read(&folder.join("table.ccs")).map_err(|e| at(e.to_string()))?;
        let text =
            std::fs::read_to_string(folder.join("table.json")).map_err(|e| at(e.to_string()))?;
        let document = codec::decode(&text, None).map_err(|e| at(e.to_string()))?;
        if document.get("codec").and_then(PyValue::as_int) != Some(codec::CODEC_VERSION) {
            return Err(at("table.json is not codec version 1".to_owned()));
        }
        let meta = TableMeta::from_value(&document).map_err(|e| at(e.to_string()))?;
        let basis = document
            .get("steering_basis")
            .cloned()
            .unwrap_or(PyValue::None);
        Session::new(store, meta, basis).map_err(|e| at(e.to_string()))
    }

    pub fn store(&self) -> &Rc<ColumnStore> {
        &self.store
    }

    pub fn meta(&self) -> &Rc<TableMeta> {
        &self.meta
    }

    /// `_steering_basis(table)` as Python computed it (a `steering_basis.SteeringBasis`
    /// dataclass), or `None` when the caller did not send one.
    pub fn steering_basis(&self) -> &PyValue {
        &self.steering_basis
    }

    pub fn access(&self) -> &AccessRecord {
        &self.access
    }

    /// The session's one value of type `T`, built on first use (unit U1's `Table`, a unit's
    /// derived per-recording data). `build` may itself use other cached types.
    pub fn cached<T: Any>(
        &self,
        build: impl FnOnce(&Session) -> Result<T, KernelError>,
    ) -> Result<Rc<T>, KernelError> {
        let key = TypeId::of::<T>();
        if let Some(found) = self.caches.borrow().get(&key) {
            return Rc::clone(found)
                .downcast::<T>()
                .map_err(|_| KernelError::internal("session cache holds another type"));
        }
        let value: Rc<dyn Any> = Rc::new(build(self)?);
        let stored = Rc::clone(self.caches.borrow_mut().entry(key).or_insert(value));
        stored
            .downcast::<T>()
            .map_err(|_| KernelError::internal("session cache holds another type"))
    }

    /// Keeps `value` for later calls and returns its reference (`{"$handle": n}`).
    pub fn put_handle<T: Any>(&self, value: T) -> PyValue {
        let mut handles = self.handles.borrow_mut();
        handles.push(Rc::new(value));
        PyValue::Handle(handles.len() as u64 - 1)
    }

    /// The value behind a handle; `Internal` for anything else or another type.
    pub fn handle<T: Any>(&self, value: &PyValue) -> Result<Rc<T>, KernelError> {
        let PyValue::Handle(index) = value else {
            return Err(KernelError::internal(format!(
                "expected a handle, got a {}",
                value.type_name()
            )));
        };
        let found = usize::try_from(*index)
            .ok()
            .and_then(|i| self.handles.borrow().get(i).cloned())
            .ok_or_else(|| KernelError::internal(format!("unknown handle {index}")))?;
        found
            .downcast::<T>()
            .map_err(|_| KernelError::internal(format!("handle {index} holds another type")))
    }

    /// Runs entry `name` on `call` and reports its outcome and accessed channels. An entry this
    /// build does not provide is an error, never a fallback.
    pub fn call(&self, name: &str, call: &mut Call) -> Answer {
        self.run(name, dispatch::find(name), call)
    }

    /// [`Session::call`] as the `kernels` process runs it: the entry's
    /// [`dispatch::HANDOFF`] variant where it has one (a handle instead of a value).
    pub fn serve(&self, name: &str, call: &mut Call) -> Answer {
        self.run(name, dispatch::find_handoff(name), call)
    }

    fn run(&self, name: &str, entry: Option<&dispatch::Entry>, call: &mut Call) -> Answer {
        self.access.take();
        let outcome = match entry {
            None => Outcome::Error(format!("entry {name} is not provided by this build")),
            Some(entry) => match (entry.run)(self, call) {
                Ok(value) => Outcome::Value(value),
                Err(KernelError::Raises { class, message }) => Outcome::Raises { class, message },
                Err(error) => Outcome::Error(format!("{name}: {error}")),
            },
        };
        Answer {
            outcome,
            accessed: self.access.take(),
        }
    }
}
