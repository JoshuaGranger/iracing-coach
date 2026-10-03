//! Port phase 2: the per-sample analysis kernels (`docs/08-rewrite/port/phase2-plan.md`).
//!
//! Each native entry replaces one module-level function of the Python analysis that reads
//! per-sample columns, takes the same arguments and returns the same Python objects (types,
//! float bits, dict order), byte-identical in `analysis.json`.
//!
//! - [`value`]: [`PyValue`], the ordered Python value tree, and [`same`], the exact comparison.
//! - [`codec`]: codec version 1 (`tools/kernel_codec.py`) to and from JSON text, and the
//!   per-call vector store.
//! - [`dispatch`]: the entry table ([`dispatch::UNITS`]) and call binding.
//! - [`session`]: one analysis (store, table metadata, steering basis, caches, accessed
//!   channels) and [`Session::call`].
//! - [`error`]: [`KernelError`], what an entry returns instead of a value.
//! - Units: [`table`] (U1), [`laps`] (U2), [`traces`] (U3), [`sections`] (U4), [`field`] (U5);
//!   each lists its entries in its own `ENTRIES`.

pub mod codec;
pub mod dispatch;
pub mod error;
pub mod session;
pub mod value;

pub mod field;
pub mod laps;
pub mod sections;
pub mod table;
pub mod traces;

pub use codec::{CODEC_VERSION, ColumnSink, VectorStore};
pub use dispatch::{Bound, Call, Entry, EntryFn, Slot, UNITS};
pub use error::KernelError;
pub use session::{AccessRecord, Answer, Outcome, Session, TableMeta};
pub use value::{Dataclass, Dict, Difference, PyValue, same};
