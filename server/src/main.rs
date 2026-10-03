//! coach-server entry point: `serve` (default), `healthcheck`, `rebuild-index`; `--dev` keeps every
//! directory under `./dev-data`.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::process::ExitCode;
use std::time::Duration;

use coach_server::{App, Config, Parts, api, rebuild, scheduler};

fn usage() -> ExitCode {
    eprintln!("usage: coach-server [serve|healthcheck|rebuild-index] [--dev]");
    ExitCode::from(2)
}

/// Compose healthcheck: GET /health on the local listener; exit 0 only on a 200.
fn healthcheck(dev: bool) -> ExitCode {
    let bind = std::env::var("COACH_BIND").unwrap_or_else(|_| {
        if dev {
            "127.0.0.1:8210"
        } else {
            "0.0.0.0:8080"
        }
        .to_string()
    });
    let port = bind.rsplit(':').next().unwrap_or("8080");
    let addr: SocketAddr = match format!("127.0.0.1:{port}").parse() {
        Ok(a) => a,
        Err(_) => return ExitCode::FAILURE,
    };
    let Ok(mut stream) = TcpStream::connect_timeout(&addr, Duration::from_secs(3)) else {
        return ExitCode::FAILURE;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
    if stream
        .write_all(b"GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        .is_err()
    {
        return ExitCode::FAILURE;
    }
    let mut response = String::new();
    let _ = stream.read_to_string(&mut response);
    if response.starts_with("HTTP/1.1 200") {
        ExitCode::SUCCESS
    } else {
        ExitCode::FAILURE
    }
}

async fn shutdown_signal() {
    #[cfg(unix)]
    {
        use tokio::signal::unix::{SignalKind, signal};
        match signal(SignalKind::terminate()) {
            Ok(mut term) => {
                tokio::select! {
                    _ = tokio::signal::ctrl_c() => {}
                    _ = term.recv() => {}
                }
            }
            Err(_) => {
                let _ = tokio::signal::ctrl_c().await;
            }
        }
    }
    #[cfg(not(unix))]
    {
        let _ = tokio::signal::ctrl_c().await;
    }
}

async fn serve(config: Config) -> ExitCode {
    let bind = config.bind;
    let app = match App::start(config, Parts::default()).await {
        Ok(app) => app,
        Err(e) => {
            tracing::error!(error = %e, "start-up failed");
            return ExitCode::FAILURE;
        }
    };
    let listener = match tokio::net::TcpListener::bind(bind).await {
        Ok(l) => l,
        Err(e) => {
            tracing::error!(%bind, error = %e, "cannot bind");
            return ExitCode::FAILURE;
        }
    };
    tracing::info!(%bind, version = env!("CARGO_PKG_VERSION"), dev = app.cfg.dev, "coach-server listening");
    let served = axum::serve(listener, api::router(app.clone()))
        .with_graceful_shutdown(shutdown_signal())
        .await;
    scheduler::snapshot_on_shutdown(&app).await;
    match served {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            tracing::error!(error = %e, "server stopped");
            ExitCode::FAILURE
        }
    }
}

fn main() -> ExitCode {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env().unwrap_or_else(|_| "info".into()),
        )
        .init();
    let mut args: Vec<String> = std::env::args().skip(1).collect();
    let dev = args.iter().any(|a| a == "--dev");
    args.retain(|a| a != "--dev");
    let command = args.first().map(String::as_str).unwrap_or("serve");
    match command {
        "healthcheck" => healthcheck(dev),
        "serve" | "rebuild-index" => {
            let config = match Config::from_env(dev) {
                Ok(c) => c,
                Err(e) => {
                    eprintln!("configuration error: {e}");
                    return ExitCode::from(2);
                }
            };
            if command == "rebuild-index" {
                return match rebuild::rebuild_index(&config) {
                    Ok(summary) => {
                        println!("{}", rebuild::summary_text(&summary));
                        ExitCode::SUCCESS
                    }
                    Err(e) => {
                        eprintln!("rebuild-index failed: {e}");
                        ExitCode::FAILURE
                    }
                };
            }
            let runtime = match tokio::runtime::Builder::new_multi_thread()
                .worker_threads(2)
                .enable_all()
                .build()
            {
                Ok(r) => r,
                Err(e) => {
                    eprintln!("cannot start the runtime: {e}");
                    return ExitCode::FAILURE;
                }
            };
            runtime.block_on(serve(config))
        }
        _ => usage(),
    }
}
