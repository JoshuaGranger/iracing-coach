//! `steering_basis.SteeringBasis` as Python computed it for the recording (`_steering_basis`
//! stays Python in phase 2; the session receives it in the start request, the vectors in
//! `table.json` or as a call argument).

use crate::kernels::error::KernelError;
use crate::kernels::session::Session;
use crate::kernels::value::PyValue;

pub const ROAD_WHEEL: &str = "road_wheel";
pub const STEERING_WHEEL: &str = "steering_wheel";
/// `steering_basis.THRESHOLD_REFERENCE_RATIO`.
pub const THRESHOLD_REFERENCE_RATIO: f64 = 12.0;
pub const DATACLASS: &str = "steering_basis.SteeringBasis";

/// The fields of `steering_basis.SteeringBasis`, in declared order.
#[derive(Clone, Debug, PartialEq)]
pub struct SteeringBasis {
    pub kind: String,
    pub status: String,
    pub ratio: Option<f64>,
    pub ratio_text: Option<String>,
    pub ratio_source: Option<String>,
    pub offset_text: Option<String>,
    pub reason: Option<String>,
    pub wheel_angle_recorded: bool,
}

impl SteeringBasis {
    /// Reads the codec's `{"$dataclass": "steering_basis.SteeringBasis", ...}`; any other shape
    /// is `Internal`.
    pub fn from_value(value: &PyValue) -> Result<SteeringBasis, KernelError> {
        let bad = |what: &str| KernelError::internal(format!("steering basis: {what}"));
        let dataclass = value
            .as_dataclass()
            .filter(|d| d.class == DATACLASS)
            .ok_or_else(|| {
                bad(&format!(
                    "expected {DATACLASS}, got a {}",
                    value.type_name()
                ))
            })?;
        let field = |name: &str| {
            dataclass
                .field(name)
                .ok_or_else(|| bad(&format!("no field {name}")))
        };
        let text = |name: &str| -> Result<String, KernelError> {
            field(name)?
                .as_str()
                .map(str::to_owned)
                .ok_or_else(|| bad(&format!("{name} is not a str")))
        };
        let optional_text = |name: &str| -> Result<Option<String>, KernelError> {
            match field(name)? {
                PyValue::None => Ok(None),
                PyValue::Str(s) => Ok(Some(s.clone())),
                _ => Err(bad(&format!("{name} is not a str or None"))),
            }
        };
        let ratio = match field("ratio")? {
            PyValue::None => None,
            PyValue::Float(x) => Some(*x),
            _ => return Err(bad("ratio is not a float or None")),
        };
        Ok(SteeringBasis {
            kind: text("kind")?,
            status: text("status")?,
            ratio,
            ratio_text: optional_text("ratio_text")?,
            ratio_source: optional_text("ratio_source")?,
            offset_text: optional_text("offset_text")?,
            reason: optional_text("reason")?,
            wheel_angle_recorded: field("wheel_angle_recorded")?
                .as_bool()
                .ok_or_else(|| bad("wheel_angle_recorded is not a bool"))?,
        })
    }

    /// The basis Python computed for the session's recording (`Session::steering_basis`);
    /// `Internal` when the session has none.
    pub fn of(session: &Session) -> Result<SteeringBasis, KernelError> {
        match session.steering_basis() {
            PyValue::None => Err(KernelError::internal(
                "the session carries no steering basis (start request or table.json)",
            )),
            value => SteeringBasis::from_value(value),
        }
    }

    /// `SteeringBasis.road_wheel`: `kind == "road_wheel" and ratio is not None`.
    pub fn road_wheel(&self) -> bool {
        self.kind == ROAD_WHEEL && self.ratio.is_some()
    }

    /// The ratio `_steering_column` divides by: `Some` on a road-wheel basis only.
    pub fn road_wheel_ratio(&self) -> Option<f64> {
        if self.road_wheel() { self.ratio } else { None }
    }

    /// `SteeringBasis.threshold(wheel_value)`: a steering-wheel threshold in this basis.
    pub fn threshold(&self, wheel_value: f64) -> f64 {
        if self.road_wheel() {
            wheel_value / THRESHOLD_REFERENCE_RATIO
        } else {
            wheel_value
        }
    }
}
