//! `cargo run --release -p coach-views --example project_file -- <analysis.json> <out_dir>`: projects one
//! document and prints the summary (used to time and measure the projector on private recordings).

use std::path::Path;

fn main() {
    let mut args = std::env::args().skip(1);
    let (Some(input), Some(out)) = (args.next(), args.next()) else {
        eprintln!("usage: project_file <analysis.json> <out_dir>");
        std::process::exit(2);
    };
    match coach_views::project(Path::new(&input), Path::new(&out)) {
        Ok(summary) => println!(
            "{}",
            serde_json::to_string_pretty(&summary).unwrap_or_default()
        ),
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(1);
        }
    }
}
