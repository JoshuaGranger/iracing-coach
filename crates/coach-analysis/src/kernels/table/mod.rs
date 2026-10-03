//! Unit U1: the table, normalisation and whole-column summaries
//! (`docs/08-rewrite/port/phase2-plan.md` section 2).
//!
//! Milestone M1, the API every unit reads: [`Table`] (`TelemetryTable`) with its recording
//! accessors ([`Table::has`], [`Table::get`], [`Table::resolve`], [`Table::normalized`],
//! [`Table::fractions`], [`Table::booleans`], [`Table::flag_bits`]), the non-recording cached
//! views keyed by [`ColumnKey`] ([`Table::finite_for`], [`Table::fractions_for`],
//! [`Table::booleans_for`], [`Table::flag_bits_for`], [`Table::wheel_ratios`],
//! [`Table::steering_column`]), [`Raw`] columns, [`View`]s, the unit conversions
//! ([`unit_steps`], [`apply_steps`], [`row_means`]) and [`SteeringBasis`].
//!
//! The unit's entries: `_setup_channel_series`, `_setup_telemetry`, `_conditions_summary`,
//! `_driver_adjustments_summary`, `_caution_flag_series`, `_dt_series`; and the native merge of
//! a multi-file group ([`merge`]).
//!
//! An entry that builds a view the port cannot build as the oracle does fails instead of
//! returning a value ([`Table::check`]).

pub mod merge;
pub mod setup;
pub mod steering;
pub mod summaries;
pub mod units;
pub mod views;

pub use setup::{SeriesItem, SetupSeries, TireSeries, setup_channel_series, setup_telemetry};
pub use steering::SteeringBasis;
pub use summaries::{
    caution_flag_series, conditions_summary, driver_adjustments_summary, dt_series,
};
pub use units::{
    Op, apply_op_steps, apply_optional_steps, apply_steps, normalize_view, normalized_unit,
    row_means, unit_steps,
};
pub use views::{
    ArrayColumn, ArrayElements, ColumnKey, Raw, Table, TextColumn, View, ViewBuilder, fraction,
};

use super::dispatch::{Call, Entry};
use super::error::KernelError;
use super::session::Session;
use super::value::PyValue;

/// The oracle entry points this unit replaces (`dispatch::UNITS`).
pub const ENTRIES: &[Entry] = &[
    Entry {
        name: "analysis_engine._setup_channel_series",
        run: entry_setup_channel_series,
    },
    Entry {
        name: "analysis_engine._setup_telemetry",
        run: entry_setup_telemetry,
    },
    Entry {
        name: "analysis_engine._conditions_summary",
        run: entry_conditions_summary,
    },
    Entry {
        name: "analysis_engine._driver_adjustments_summary",
        run: entry_driver_adjustments_summary,
    },
    Entry {
        name: "analysis_engine._caution_flag_series",
        run: entry_caution_flag_series,
    },
    Entry {
        name: "analysis_engine._dt_series",
        run: entry_dt_series,
    },
];

/// The table of a call whose only parameter is `table`.
fn table_only(
    session: &Session,
    call: &Call,
    entry: &'static str,
) -> Result<std::rc::Rc<Table>, KernelError> {
    let bound = call.bind(entry, &["table"], &[])?;
    bound.require_table(call, "table")?;
    Table::of(session)
}

/// `_setup_channel_series(table)`: the dict the oracle returns (the replay compares it).
fn entry_setup_channel_series(session: &Session, call: &mut Call) -> Result<PyValue, KernelError> {
    let table = table_only(session, call, "_setup_channel_series")?;
    Ok(setup_channel_series(&table)?.to_value())
}

/// `_setup_channel_series(table)` for the `kernels` hand-off (`phase2-plan.md` section 4.2):
/// the [`SetupSeries`] kept in the session, returned as its handle (`{"$handle": n}`), which
/// `_setup_telemetry` and `_track_profile` accept in place of the dict.
pub fn setup_channel_series_handle(session: &Session) -> Result<PyValue, KernelError> {
    let table = Table::of(session)?;
    let series = setup_channel_series(&table)?;
    Ok(session.put_handle(series))
}

/// The `kernels` process's `_setup_channel_series` (`dispatch::HANDOFF`): the call bound as
/// the replay entry binds it, the series kept behind a handle.
pub fn entry_setup_channel_series_handle(
    session: &Session,
    call: &mut Call,
) -> Result<PyValue, KernelError> {
    let bound = call.bind("_setup_channel_series", &["table"], &[])?;
    bound.require_table(call, "table")?;
    setup_channel_series_handle(session)
}

/// `_setup_telemetry(table, series)`: `series` by value (the vectors) or as the handle of a
/// [`SetupSeries`].
fn entry_setup_telemetry(session: &Session, call: &mut Call) -> Result<PyValue, KernelError> {
    let bound = call.bind("_setup_telemetry", &["table", "series"], &[])?;
    bound.require_table(call, "table")?;
    let table = Table::of(session)?;
    let argument = bound.require(call, "series")?;
    let summary = match argument {
        PyValue::Handle(_) => setup_telemetry(&table, &*session.handle::<SetupSeries>(argument)?)?,
        value => setup_telemetry(&table, &SetupSeries::from_value(value)?)?,
    };
    Ok(PyValue::Dict(summary))
}

fn entry_conditions_summary(session: &Session, call: &mut Call) -> Result<PyValue, KernelError> {
    let table = table_only(session, call, "_conditions_summary")?;
    Ok(PyValue::Dict(conditions_summary(&table)?))
}

fn entry_driver_adjustments_summary(
    session: &Session,
    call: &mut Call,
) -> Result<PyValue, KernelError> {
    let table = table_only(session, call, "_driver_adjustments_summary")?;
    Ok(PyValue::Dict(driver_adjustments_summary(&table)?))
}

fn entry_caution_flag_series(session: &Session, call: &mut Call) -> Result<PyValue, KernelError> {
    let table = table_only(session, call, "_caution_flag_series")?;
    let flags = caution_flag_series(&table);
    table.check()?;
    // A flag beyond i64 is never cleared (its low 11 bits are zero), so its value is the
    // flag view's float.
    let key = table.key(&["SessionFlags"], PyValue::Int(0));
    Ok(PyValue::List(table.flag_values(&key, &flags)))
}

/// `_dt_series(times, fallback_hz)`: `times` is a table column (by reference) or a list.
fn entry_dt_series(session: &Session, call: &mut Call) -> Result<PyValue, KernelError> {
    let bound = call.bind("_dt_series", &["times", "fallback_hz"], &[])?;
    let fallback = match bound.require(call, "fallback_hz")? {
        PyValue::Float(x) => *x,
        PyValue::Int(i) => *i as f64,
        PyValue::Bool(b) => f64::from(u8::from(*b)),
        other => {
            return Err(KernelError::internal(format!(
                "_dt_series: fallback_hz is a {}",
                other.type_name()
            )));
        }
    };
    let times = match bound.require(call, "times")? {
        PyValue::TableColumn(name) => {
            let table = Table::of(session)?;
            if !table.contains(name) {
                return Err(KernelError::internal(format!(
                    "_dt_series: {name:?} is not a table channel"
                )));
            }
            let view = table.finite_for(&ColumnKey::Channel(name.clone()));
            table.check()?;
            view
        }
        PyValue::List(items) | PyValue::Tuple(items) => {
            let mut builder = ViewBuilder::with_capacity(items.len());
            for item in items {
                builder.push(item.finite()?);
            }
            std::rc::Rc::new(builder.finish(true))
        }
        other => {
            return Err(KernelError::internal(format!(
                "_dt_series: times is a {}",
                other.type_name()
            )));
        }
    };
    Ok(PyValue::List(
        dt_series(&times, fallback)
            .into_iter()
            .map(PyValue::Float)
            .collect(),
    ))
}
