//! coach-server: HTTP API, ingest, live relay and job runner for the mini PC.
//! The scaffold serves `/health` only; subsystems land per `docs/08-rewrite/workstreams.md`.

use std::net::SocketAddr;

use axum::{Json, Router, routing::get};
use coach_wire::api::{Health, HealthStatus};

/// Database schema version reported by `/health`; the scaffold has no database yet.
const SCHEMA_VERSION: i64 = 0;

async fn health() -> Json<Health> {
    Json(Health {
        status: HealthStatus::Ok,
        server_version: env!("CARGO_PKG_VERSION").to_string(),
        schema_version: SCHEMA_VERSION,
    })
}

fn app() -> Router {
    Router::new().route("/health", get(health))
}

#[tokio::main]
async fn main() -> std::io::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .init();
    let bind: SocketAddr = std::env::var("COACH_BIND")
        .unwrap_or_else(|_| "0.0.0.0:8080".to_string())
        .parse()
        .map_err(|e| {
            std::io::Error::new(std::io::ErrorKind::InvalidInput, format!("COACH_BIND: {e}"))
        })?;
    let listener = tokio::net::TcpListener::bind(bind).await?;
    tracing::info!(%bind, version = env!("CARGO_PKG_VERSION"), "coach-server listening");
    axum::serve(listener, app()).await
}

#[cfg(test)]
mod tests {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    use super::*;

    #[tokio::test]
    async fn health_reports_ok() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(async move { axum::serve(listener, app()).await });

        let mut stream = tokio::net::TcpStream::connect(addr).await.unwrap();
        stream
            .write_all(b"GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            .await
            .unwrap();
        let mut response = String::new();
        stream.read_to_string(&mut response).await.unwrap();

        assert!(response.starts_with("HTTP/1.1 200"), "{response}");
        let body = response.split("\r\n\r\n").nth(1).unwrap();
        let health: Health = serde_json::from_str(body.trim()).unwrap();
        assert_eq!(health.status, HealthStatus::Ok);
        assert_eq!(health.server_version, env!("CARGO_PKG_VERSION"));
    }
}
