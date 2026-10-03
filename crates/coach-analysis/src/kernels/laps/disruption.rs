//! `analysis_engine._disrupted_sample_indices(table, timer_epsilon_s, *, mandatory_recorded,
//! optional_recorded, session_flags_channel, fast_increment_indices)`.

use std::collections::HashSet;

use super::super::error::KernelError;
use super::super::table::{ColumnKey, Table};
use super::super::value::PyValue;
use super::py_repr_str;

const REPAIR_REQUIRED_FLAG: i64 = 0x0010_0000;
const TOW_TIMER: &[&str] = &["PlayerCarTowTime"];
const MANDATORY_REPAIR_TIMER: &[&str] = &["PitRepairLeft"];
const OPTIONAL_REPAIR_TIMER: &[&str] = &["PitOptRepairLeft"];

/// `timer_epsilon_s` as the comparison `(value or 0.0) > timer_epsilon_s` uses it.
#[derive(Clone, Copy, Debug)]
pub enum Epsilon {
    Float(f64),
    Int(i64),
}

impl Epsilon {
    fn exceeded_by(self, value: f64) -> bool {
        match self {
            Epsilon::Float(epsilon) => value > epsilon,
            Epsilon::Int(epsilon) => {
                coach_numerics::cmp_int_float(epsilon, value) == Some(std::cmp::Ordering::Less)
            }
        }
    }
}

pub fn disrupted_sample_indices(
    table: &Table,
    timer_epsilon_s: Epsilon,
    mandatory_recorded: bool,
    optional_recorded: bool,
    session_flags_channel: Option<&str>,
    fast_increment_indices: &HashSet<i64>,
) -> Result<PyValue, KernelError> {
    // (_finite(value) or 0.0) > timer_epsilon_s over resolve(*aliases, default=None).
    let positive_timer = |aliases: &[&str]| {
        let values = table.finite_for(&table.key(aliases, PyValue::None));
        (0..table.len())
            .map(|i| timer_epsilon_s.exceeded_by(values.get(i).unwrap_or(0.0)))
            .collect::<Vec<bool>>()
    };
    let on_pit = table.booleans_for(&table.key(&["OnPitRoad"], PyValue::Bool(false)));
    let in_stall = table.booleans_for(&table.key(&["PlayerCarInPitStall"], PyValue::Bool(false)));
    let active = table.booleans_for(&table.key(&["PitstopActive"], PyValue::Bool(false)));
    let tow = positive_timer(TOW_TIMER);
    let mandatory = mandatory_recorded.then(|| positive_timer(MANDATORY_REPAIR_TIMER));
    let optional = optional_recorded.then(|| positive_timer(OPTIONAL_REPAIR_TIMER));
    let repair_flag = match session_flags_channel {
        Some(channel) if !channel.is_empty() => {
            // table._flag_bits_for(channel): the channel itself is the key, so an absent one
            // is the KeyError of table.channels[channel].
            let key = table.key(&[channel], PyValue::None);
            if !matches!(&key, ColumnKey::Channel(_)) {
                return Err(KernelError::raises("KeyError", py_repr_str(channel)));
            }
            Some(table.flag_bits_for(&key))
        }
        _ => None,
    };
    let mut result = Vec::new();
    for index in 0..table.len() {
        let disrupted = on_pit[index]
            || in_stall[index]
            || active[index]
            || tow[index]
            || mandatory.as_ref().is_some_and(|terms| terms[index])
            || optional.as_ref().is_some_and(|terms| terms[index])
            || repair_flag
                .as_ref()
                .is_some_and(|flags| flags[index] & REPAIR_REQUIRED_FLAG != 0)
            || fast_increment_indices.contains(&(index as i64));
        if disrupted {
            result.push(PyValue::Int(index as i64));
        }
    }
    table.check()?;
    Ok(PyValue::List(result))
}
