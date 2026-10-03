//! One error type for the server; API errors carry their HTTP shape (`Error` schema).

use axum::Json;
use axum::http::{HeaderValue, StatusCode, header};
use axum::response::{IntoResponse, Response};
use serde_json::{Value, json};

#[derive(Debug)]
pub struct ApiError {
    pub status: StatusCode,
    pub code: &'static str,
    pub message: String,
    pub details: Option<Value>,
    pub retry_after: Option<u32>,
    /// Body of a 412: the resource as it is now.
    pub current: Option<Value>,
}

impl ApiError {
    pub fn new(status: StatusCode, code: &'static str, message: impl Into<String>) -> Self {
        ApiError {
            status,
            code,
            message: message.into(),
            details: None,
            retry_after: None,
            current: None,
        }
    }
    pub fn with_details(mut self, details: Value) -> Self {
        self.details = Some(details);
        self
    }
    pub fn body(&self) -> Value {
        let mut body = json!({"error": self.code, "message": self.message});
        if let Some(details) = &self.details {
            body["details"] = details.clone();
        }
        if let Some(current) = &self.current {
            body["current"] = current.clone();
        }
        body
    }
}

#[derive(Debug, thiserror::Error)]
pub enum Error {
    #[error("{}: {}", .0.code, .0.message)]
    Api(ApiError),
    #[error("database: {0}")]
    Db(#[from] rusqlite::Error),
    #[error("I/O: {0}")]
    Io(#[from] std::io::Error),
    /// Fault injection: the process "died" at this point; no cleanup runs.
    #[error("simulated crash at {0}")]
    Crash(String),
    #[error("{0}")]
    Internal(String),
}

pub type Result<T> = std::result::Result<T, Error>;

impl Error {
    pub fn not_found(message: impl Into<String>) -> Error {
        Error::Api(ApiError::new(StatusCode::NOT_FOUND, "not_found", message))
    }
    pub fn invalid(message: impl Into<String>) -> Error {
        Error::Api(ApiError::new(
            StatusCode::BAD_REQUEST,
            "invalid_request",
            message,
        ))
    }
    pub fn unprocessable(code: &'static str, message: impl Into<String>) -> Error {
        Error::Api(ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            code,
            message,
        ))
    }
    pub fn conflict(code: &'static str, message: impl Into<String>) -> Error {
        Error::Api(ApiError::new(StatusCode::CONFLICT, code, message))
    }
    pub fn unavailable(retry_after: u32, message: impl Into<String>) -> Error {
        let mut e = ApiError::new(StatusCode::SERVICE_UNAVAILABLE, "unavailable", message);
        e.retry_after = Some(retry_after);
        Error::Api(e)
    }
    pub fn internal(message: impl Into<String>) -> Error {
        Error::Internal(message.into())
    }

    /// True for I/O and SQLite out-of-space failures (retryable; nothing was committed).
    pub fn is_no_space(&self) -> bool {
        match self {
            Error::Io(e) => e.kind() == std::io::ErrorKind::StorageFull,
            Error::Db(rusqlite::Error::SqliteFailure(f, _)) => {
                f.code == rusqlite::ErrorCode::DiskFull
            }
            _ => false,
        }
    }
}

impl From<ApiError> for Error {
    fn from(value: ApiError) -> Self {
        Error::Api(value)
    }
}

impl IntoResponse for Error {
    fn into_response(self) -> Response {
        let api = match self {
            Error::Api(api) => api,
            e if e.is_no_space() => {
                let mut api = ApiError::new(
                    StatusCode::SERVICE_UNAVAILABLE,
                    "no_space",
                    "storage is full; retry later",
                );
                api.retry_after = Some(30);
                api
            }
            Error::Crash(point) => ApiError::new(
                StatusCode::INTERNAL_SERVER_ERROR,
                "crash",
                format!("simulated crash at {point}"),
            ),
            other => {
                tracing::error!(error = %other, "request failed");
                ApiError::new(
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "internal",
                    other.to_string(),
                )
            }
        };
        let mut response = (api.status, Json(api.body())).into_response();
        if let Some(seconds) = api.retry_after {
            response
                .headers_mut()
                .insert(header::RETRY_AFTER, HeaderValue::from(seconds));
        }
        response
    }
}
