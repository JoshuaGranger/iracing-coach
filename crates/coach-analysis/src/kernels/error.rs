//! What a kernel entry returns instead of a value.

/// `Raises` is an exception the oracle raises for the same input: the shim raises that class
/// with that message, and the replay compares both exactly. Everything else (`Internal`,
/// `Codec`) is a failure of the native stage (`NativeStageError`), never a value.
#[derive(Clone, Debug, PartialEq, Eq, thiserror::Error)]
pub enum KernelError {
    #[error("{class}: {message}")]
    Raises { class: String, message: String },
    /// Arguments of a shape the entry does not accept, a broken invariant, a port limit.
    #[error("internal: {0}")]
    Internal(String),
    /// A malformed codec document or vector store.
    #[error("codec: {0}")]
    Codec(String),
}

impl KernelError {
    pub fn raises(class: impl Into<String>, message: impl Into<String>) -> KernelError {
        KernelError::Raises {
            class: class.into(),
            message: message.into(),
        }
    }

    pub fn internal(message: impl Into<String>) -> KernelError {
        KernelError::Internal(message.into())
    }

    pub fn codec(message: impl Into<String>) -> KernelError {
        KernelError::Codec(message.into())
    }
}

/// A `coach-numerics` error: a Python exception class becomes `Raises` with the crate's
/// message, which is CPython's for the operation the crate names. Where the oracle's message
/// differs (the same class raised from another expression), the entry builds its own `Raises`;
/// the replay compares messages exactly. The port limits (`IntBeyondI64`, `ComplexResult`,
/// `Unsupported`) are `Internal`: Python would have returned a value.
impl From<coach_numerics::PyError> for KernelError {
    fn from(error: coach_numerics::PyError) -> KernelError {
        use coach_numerics::PyErrorKind as Kind;
        match error.kind {
            Kind::IntBeyondI64 | Kind::ComplexResult | Kind::Unsupported => {
                KernelError::Internal(format!("port limit: {error}"))
            }
            kind => KernelError::raises(kind.name(), error.message),
        }
    }
}
