//! coach-collector: SDK reader, live overlay, garage capture and uploader for the sim PC.
//! Windows only; other targets build a stub that exits with an error.

#[cfg(windows)]
fn main() {
    tracing_subscriber::fmt()
        .with_env_filter(tracing_subscriber::EnvFilter::from_default_env())
        .init();
    if std::env::args().any(|a| a == "--version") {
        println!("coach-collector {}", env!("CARGO_PKG_VERSION"));
        return;
    }
    tracing::info!(
        version = env!("CARGO_PKG_VERSION"),
        "coach-collector scaffold: nothing to run yet"
    );
}

#[cfg(not(windows))]
fn main() {
    eprintln!("coach-collector runs on Windows only");
    std::process::exit(2);
}
