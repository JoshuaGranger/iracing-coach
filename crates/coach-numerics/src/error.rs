use std::fmt;

/// What Python raises (or, for the last three kinds, where this port stops short of Python).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum PyErrorKind {
    ZeroDivisionError,
    OverflowError,
    ValueError,
    StatisticsError,
    /// `statistics.stdev`/`pstdev` of data holding an infinity or NaN: CPython 3.12 asks the
    /// non-finite float for `.numerator` and fails.
    AttributeError,
    TypeError,
    /// Python would return an `int` outside `i64`; this port does not carry big ints in values.
    IntBeyondI64,
    /// Python would return a `complex` (negative base, fractional exponent).
    ComplexResult,
    /// A format type or input form this port does not implement (for example locale `n`).
    Unsupported,
}

impl PyErrorKind {
    /// Python exception class name; the three port limits use their own names.
    pub fn name(self) -> &'static str {
        match self {
            Self::ZeroDivisionError => "ZeroDivisionError",
            Self::OverflowError => "OverflowError",
            Self::ValueError => "ValueError",
            Self::StatisticsError => "StatisticsError",
            Self::AttributeError => "AttributeError",
            Self::TypeError => "TypeError",
            Self::IntBeyondI64 => "IntBeyondI64",
            Self::ComplexResult => "ComplexResult",
            Self::Unsupported => "Unsupported",
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PyError {
    pub kind: PyErrorKind,
    pub message: &'static str,
}

impl PyError {
    pub(crate) const fn new(kind: PyErrorKind, message: &'static str) -> Self {
        Self { kind, message }
    }
}

impl fmt::Display for PyError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.kind.name(), self.message)
    }
}

impl std::error::Error for PyError {}

pub type PyResult<T> = Result<T, PyError>;

pub(crate) const ZERO_DIVISION: PyError =
    PyError::new(PyErrorKind::ZeroDivisionError, "division by zero");
pub(crate) const INT_BEYOND_I64: PyError =
    PyError::new(PyErrorKind::IntBeyondI64, "integer result outside i64");
