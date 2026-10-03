//! Unit conversion and per-sample transforms: `_normalized_unit`, `_unit_steps`,
//! `_apply_unit_steps`, `_apply_steps`, `_normalize_column` of a converted view and
//! `_row_means`.
//!
//! A conversion is its infix expression as ordered binary operations on the same operands
//! (`n * 9.0 / 5.0 + 32.0` is `((n * 9.0) / 5.0) + 32.0`), each one IEEE operation in Rust as
//! in CPython. No finite check follows a conversion; `_finite_after_transform` and
//! `_normalize_column` re-check only where the oracle does.

use crate::kernels::error::KernelError;

use super::views::{View, ViewBuilder, is_py_space};

/// One binary operation of a unit conversion (`operator.add`, `sub`, `mul`, `truediv`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Op {
    Add,
    Sub,
    Mul,
    Div,
}

pub const METERS_TO_INCHES: f64 = 39.37007874015748;
pub const KPA_TO_PSI: f64 = 0.14503773773020923;

/// `operation(number, operand)`.
pub fn apply_op(number: f64, operation: Op, operand: f64) -> f64 {
    match operation {
        Op::Add => number + operand,
        Op::Sub => number - operand,
        Op::Mul => number * operand,
        Op::Div => number / operand,
    }
}

/// `_apply_unit_steps(number, steps)`: the steps in order.
pub fn apply_op_steps(number: f64, steps: &[(Op, f64)]) -> f64 {
    steps.iter().fold(number, |value, &(operation, operand)| {
        apply_op(value, operation, operand)
    })
}

/// `_apply_steps(values, complete, steps)`: each present value converted, gaps kept, the
/// input's `complete` flag kept, no finite check.
pub fn apply_steps(values: &View, steps: &[(Op, f64)]) -> View {
    View::from_options_with(
        values
            .iter()
            .map(|value| value.map(|n| apply_op_steps(n, steps))),
        values.complete(),
    )
}

/// `_apply_steps(values, complete, steps)` with `steps` possibly `None` (unchanged).
pub fn apply_optional_steps(values: &View, steps: Option<&[(Op, f64)]>) -> View {
    match steps {
        None => values.clone(),
        Some(steps) => apply_steps(values, steps),
    }
}

/// `_normalize_column(values)` of a float view: non-finite values become `None`, `complete`
/// when none is `None`.
pub fn normalize_view(values: &View) -> View {
    let mut builder = ViewBuilder::with_capacity(values.len());
    for value in values.iter() {
        builder.push(value.filter(|n| n.is_finite()));
    }
    let complete = builder.all_present();
    builder.finish(complete)
}

/// `_normalized_unit(value)`: `str(value or "").strip().lower().replace("°", "").replace(" ", "")`.
pub fn normalized_unit(value: Option<&str>) -> String {
    value
        .unwrap_or_default()
        .trim_matches(is_py_space)
        .to_lowercase()
        .replace(['°', ' '], "")
}

const FT_TO_IN: &[(Op, f64)] = &[(Op::Mul, 12.0)];
const MM_TO_IN: &[(Op, f64)] = &[(Op::Div, 25.4)];
const CM_TO_IN: &[(Op, f64)] = &[(Op::Div, 2.54)];
const M_TO_IN: &[(Op, f64)] = &[(Op::Mul, METERS_TO_INCHES)];
const PA_TO_PSI: &[(Op, f64)] = &[(Op::Mul, KPA_TO_PSI), (Op::Div, 1000.0)];
const BAR_TO_PSI: &[(Op, f64)] = &[(Op::Mul, 100.0), (Op::Mul, KPA_TO_PSI)];
const KPA_PSI: &[(Op, f64)] = &[(Op::Mul, KPA_TO_PSI)];
const K_TO_F: &[(Op, f64)] = &[
    (Op::Sub, 273.15),
    (Op::Mul, 9.0),
    (Op::Div, 5.0),
    (Op::Add, 32.0),
];
const C_TO_F: &[(Op, f64)] = &[(Op::Mul, 9.0), (Op::Div, 5.0), (Op::Add, 32.0)];

/// `_unit_steps(quantity, unit)` for a normalised unit: the conversion to inches, inches per
/// second, psi or Fahrenheit; `None` means unchanged.
pub fn unit_steps(quantity: &str, unit: &str) -> Option<&'static [(Op, f64)]> {
    match quantity {
        "distance" => match unit {
            "in" | "inch" | "inches" | "\"" => None,
            "ft" | "foot" | "feet" => Some(FT_TO_IN),
            "mm" | "millimeter" | "millimeters" => Some(MM_TO_IN),
            "cm" | "centimeter" | "centimeters" => Some(CM_TO_IN),
            _ => Some(M_TO_IN),
        },
        "velocity" => match unit {
            "in/s" | "inch/s" | "inches/s" | "ips" => None,
            "ft/s" | "fps" => Some(FT_TO_IN),
            "mm/s" => Some(MM_TO_IN),
            "cm/s" => Some(CM_TO_IN),
            _ => Some(M_TO_IN),
        },
        "pressure" => match unit {
            "psi" | "lb/in2" | "lbf/in2" => None,
            "pa" | "pascal" | "pascals" => Some(PA_TO_PSI),
            "bar" => Some(BAR_TO_PSI),
            _ => Some(KPA_PSI),
        },
        "temperature" => match unit {
            "f" | "fahrenheit" => None,
            "k" | "kelvin" => Some(K_TO_F),
            _ => Some(C_TO_F),
        },
        _ => None,
    }
}

/// `_row_means(columns)`: per sample, `fsum` of the columns' present finite values divided by
/// their count (`statistics.fmean`), `None` where there is none; a shorter column contributes
/// nothing past its end. `complete` when no mean is `None`. `fsum` raises `OverflowError` on
/// intermediate overflow, as the oracle's does.
pub fn row_means(columns: &[&View]) -> Result<View, KernelError> {
    let length = columns.iter().map(|column| column.len()).max().unwrap_or(0);
    let mut builder = ViewBuilder::with_capacity(length);
    let mut numbers = Vec::with_capacity(columns.len());
    for index in 0..length {
        numbers.clear();
        numbers.extend(
            columns
                .iter()
                .filter(|column| index < column.len())
                .filter_map(|column| column.get(index))
                .filter(|value| value.is_finite()),
        );
        if numbers.is_empty() {
            builder.push(None);
        } else {
            builder.push(Some(coach_numerics::fsum(&numbers)? / numbers.len() as f64));
        }
    }
    let complete = builder.all_present();
    Ok(builder.finish(complete))
}
