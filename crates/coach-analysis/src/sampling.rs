//! The sampling plan of `ibt_reader.load_telemetry` (`_sampling_plan`, `_iter_sample_indices`).
//!
//! The only floating-point arithmetic of the decode stage. Python computes `tick_rate / target`
//! (an int divided by a float: both operands are exact doubles, one correctly rounded IEEE
//! division) and walks `position += step` (IEEE addition), truncating with `int(position)`.
//! Rust's `f64` division and addition are the same IEEE operations, and `as i64` truncates
//! toward zero like `int()` for the non-negative positions that occur, so no compensated or
//! exact arithmetic is needed here. Vectors from CPython: `tests/vectors/sampling.json`.

use crate::DecodeError;

/// `(step, output_rate)` of `_sampling_plan`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct SamplingPlan {
    /// Records advanced per output sample (`1.0` at native rate).
    pub step: f64,
    /// `sample_rate_hz` of the decoded table (always a Python float).
    pub output_rate: f64,
}

impl SamplingPlan {
    /// `_sampling_plan(tick_rate, target_hz)`. `None` is native rate. A target that is not a
    /// positive finite number is the Python `ValueError`.
    pub fn new(tick_rate: i32, target_hz: Option<f64>) -> Result<SamplingPlan, DecodeError> {
        let tick = f64::from(tick_rate);
        let Some(target) = target_hz else {
            return Ok(SamplingPlan {
                step: 1.0,
                output_rate: tick,
            });
        };
        if !target.is_finite() || target <= 0.0 {
            return Err(DecodeError::TargetHz);
        }
        if target >= tick {
            return Ok(SamplingPlan {
                step: 1.0,
                output_rate: tick,
            });
        }
        Ok(SamplingPlan {
            step: tick / target,
            output_rate: target,
        })
    }

    /// Python's `step.is_integer()`: the strided decode path (`itertools.islice` or the typed
    /// view) instead of `unpack_from` per index. Both select the same records; kept for the
    /// store header so a reader can see which Python path the plan corresponds to.
    pub fn integral_step(&self) -> bool {
        self.step.is_finite() && self.step.fract() == 0.0
    }

    /// `list(_iter_sample_indices(record_count, step))`.
    ///
    /// A step that overflowed to infinity (`tick_rate / target` for a target below about
    /// `tick_rate / f64::MAX`, e.g. `5e-324`) makes the second position infinite, where
    /// Python's `int(position)` raises `OverflowError`; that is
    /// [`DecodeError::SampleIndexOverflow`], never a shortened index list.
    pub fn sample_indices(&self, record_count: u32) -> Result<Vec<u32>, DecodeError> {
        if self.step <= 1.0 {
            return Ok((0..record_count).collect());
        }
        let limit = i64::from(record_count);
        let mut indices = Vec::new();
        let mut position = 0.0_f64;
        let mut previous = -1_i64;
        loop {
            // int(position) of inf: OverflowError. NaN cannot occur (step is positive).
            if position.is_infinite() {
                return Err(DecodeError::SampleIndexOverflow);
            }
            // int(position): truncation toward zero; finite positions are >= 0, and `as`
            // saturates only above i64::MAX, where Python's int is merely large: both end
            // the walk.
            let index = position as i64;
            if index >= limit {
                break;
            }
            if index != previous {
                indices.push(index as u32);
                previous = index;
            }
            position += self.step;
        }
        Ok(indices)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn native_and_at_or_above_tick_rate_keep_every_record() {
        for target in [None, Some(60.0), Some(61.5), Some(1e300)] {
            let plan = SamplingPlan::new(60, target).unwrap();
            assert_eq!(plan.step, 1.0);
            assert_eq!(plan.output_rate, 60.0);
            assert_eq!(plan.sample_indices(5).unwrap(), vec![0, 1, 2, 3, 4]);
        }
    }

    #[test]
    fn invalid_targets_are_the_python_value_error() {
        for target in [0.0, -1.0, f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            assert!(matches!(
                SamplingPlan::new(60, Some(target)),
                Err(DecodeError::TargetHz)
            ));
        }
    }

    #[test]
    fn integral_and_fractional_steps() {
        let plan = SamplingPlan::new(60, Some(20.0)).unwrap();
        assert_eq!(plan.step, 3.0);
        assert!(plan.integral_step());
        assert_eq!(plan.sample_indices(10).unwrap(), vec![0, 3, 6, 9]);
        let plan = SamplingPlan::new(60, Some(7.0)).unwrap();
        assert!(!plan.integral_step());
        assert_eq!(plan.sample_indices(0).unwrap(), Vec::<u32>::new());
    }

    /// Review finding 2 (Codex Sol phase-1 review): `60 / 5e-324` is inf; CPython raises
    /// `OverflowError: cannot convert float infinity to integer` at the second position.
    #[test]
    fn infinite_step_is_python_overflow_error() {
        for target in [5e-324, 1e-307] {
            let plan = SamplingPlan::new(60, Some(target)).unwrap();
            assert_eq!(plan.step, f64::INFINITY);
            let error = plan.sample_indices(120).unwrap_err();
            assert!(matches!(error, DecodeError::SampleIndexOverflow));
            assert_eq!(
                error.to_string(),
                "cannot convert float infinity to integer"
            );
            // No records: Python breaks at index 0 before the infinite position.
            assert_eq!(plan.sample_indices(0).unwrap(), Vec::<u32>::new());
        }
        // A huge finite step ends the walk after index 0, as int(6e301) >= record_count.
        let plan = SamplingPlan::new(60, Some(1e-300)).unwrap();
        assert_eq!(plan.sample_indices(120).unwrap(), vec![0]);
    }
}
