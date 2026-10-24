//! `coach-server rebuild-index`: disaster recovery of `blobs`, `recordings`, `setup_files` and
//! drivers from the NAS manifests (`nas-layout.md` section 4). Sessions are rebuilt by the
//! discovery jobs it queues; user-authored data comes from the backups.

use rusqlite::params;
use serde_json::{Value, json};

use crate::archiver::read_manifests;
use crate::config::Config;
use crate::db::Store;
use crate::error::{Error, Result};
use crate::jobs::{NewJob, enqueue_tx};
use crate::util::iso_now;

#[derive(Debug, Default, PartialEq, Eq)]
pub struct RebuildSummary {
    pub manifests: usize,
    pub recordings: usize,
    pub setup_files: usize,
    pub skipped: usize,
}

pub fn rebuild_index(cfg: &Config) -> Result<RebuildSummary> {
    cfg.ensure_dirs()?;
    let store = Store::open(&cfg.db_path(), &cfg.app_version)?;
    let manifests = read_manifests(&cfg.nas_root)?;
    let total = manifests.len();
    let nas_root = cfg.nas_root.clone();
    store.write_blocking(move |conn| {
        let tx = conn.transaction()?;
        let mut summary = RebuildSummary {
            manifests: total,
            ..Default::default()
        };
        for m in &manifests {
            let sha = m["sha256"].as_str().unwrap_or_default();
            let kind = m["kind"].as_str().unwrap_or_default();
            if sha.len() != 64 || !matches!(kind, "recording" | "setup_file" | "field_recording" | "replay") {
                summary.skipped += 1;
                continue;
            }
            if kind == "field_recording" {
                validate_field_manifest(m)?;
            }
            let archived_at = m["archived_at"].as_str().unwrap_or("1970-01-01T00:00:00Z");
            let received = m["received_at"].as_str().unwrap_or(archived_at);
            let collector = m["collector_id"].as_str();
            if let Some(c) = collector {
                tx.execute(
                    "INSERT OR IGNORE INTO collectors(collector_id, machine_name, collector_version, protocol, first_seen_at, last_seen_at)
                     VALUES (?1, 'unknown', 'unknown', 0, ?2, ?2)",
                    params![c, iso_now()],
                )?;
            }
            let customer = m["driver"]["customer_id"].as_i64();
            if let Some(id) = customer {
                crate::db::upsert_driver(&tx, id, m["driver"]["name"].as_str(), None)?;
            }
            let blob_kind = if kind=="replay" {"rpy".to_string()} else if kind == "recording" {
                "ibt".to_string()
            } else if kind == "field_recording" {
                "field".to_string()
            } else {
                m["setup_file"]["format"].as_str().unwrap_or("sto").to_string()
            };
            tx.execute(
                "INSERT OR IGNORE INTO blobs(sha256, size, kind, first_name, source_mtime, collector_id, received_at,
                   spool_present, nas_state, nas_archived_at) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, 0, 'archived', ?8)",
                params![
                    sha,
                    m["size"].as_i64().unwrap_or(0),
                    blob_kind,
                    m["original_name"].as_str().unwrap_or(sha),
                    m["source_mtime"].as_str(),
                    collector,
                    received,
                    archived_at
                ],
            )?;
            if let Some(path)=m["library"]["relative_path"].as_str(){
                let pin=m["library"]["identity"].as_str().ok_or_else(||Error::invalid("canonical manifest identity missing"))?;
                crate::nas_library::fs::identity(&nas_root,Some(pin))?;
                crate::nas_library::fs::checked(&nas_root,path)?;
                let existing:Option<String>=tx.query_row("SELECT identity FROM nas_library_meta WHERE singleton=1",[],|r|r.get(0))?;
                if existing.as_deref().is_some_and(|s|s!=pin){return Err(Error::invalid("rebuild library identity differs"));}
                tx.execute("UPDATE nas_library_meta SET identity=?1 WHERE singleton=1",[pin])?;
                let state=m["library"]["state"].as_str().filter(|s|matches!(*s,"canonical_verified"|"conflict"|"deleted")).unwrap_or("conflict");
                tx.execute("INSERT OR IGNORE INTO nas_library_objects(kind,sha256,size,relative_path,state,deleted_at) VALUES(?1,?2,?3,?4,?5,?6)",params![blob_kind,sha,m["size"].as_i64().unwrap_or(0),path,state,if state=="deleted"{Some(iso_now())}else{None}])?;
                if state=="deleted"{tx.execute("UPDATE blobs SET nas_state='failed',nas_error='library_file_deleted' WHERE sha256=?1",[sha])?;continue;}
            }
            if kind == "field_recording" {
                let f = &m["field_recording"];
                if let Some(c) = customer {
                    tx.execute("INSERT OR IGNORE INTO field_recordings VALUES (?1,?2,?3,?4,?5,?6,?7)", params![sha,c,f["subsession_id"].as_i64(),f["session_num"].as_i64(),f["sample_count"].as_i64(),f["first_time"].as_f64().unwrap_or(0.),f["last_time"].as_f64().unwrap_or(0.)])?;
                }
            } else if kind == "replay" {
                // Archive-only: never a recording, setup or discovery job.
            } else if kind == "recording" {
                let rec = &m["recording"];
                let header = json!({
                    "record_count": rec["record_count"], "tick_rate": rec["tick_rate"],
                    "buffer_length": rec["buffer_length"], "variable_count": rec["variable_count"],
                });
                tx.execute(
                    "INSERT OR IGNORE INTO recordings(sha256, customer_id, ingest_state, record_count, tick_rate,
                       session_info_sha256, subsession_id, track, track_config, car_path, start_time_utc, header_json, parsed_at)
                     VALUES (?1, ?2, 'parsed', ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
                    params![
                        sha,
                        customer,
                        rec["record_count"].as_i64(),
                        rec["tick_rate"].as_i64(),
                        rec["session_info_sha256"].as_str(),
                        rec["subsession_id"].as_i64(),
                        rec["track"].as_str(),
                        rec["track_config"].as_str(),
                        rec["car_path"].as_str(),
                        rec["start_time_utc"].as_str(),
                        header.to_string(),
                        iso_now()
                    ],
                )?;
                if let Some(c) = customer {
                    enqueue_tx(&tx, &NewJob::discover(c, sha), false)?;
                }
                summary.recordings += 1;
            } else if let Some(c) = customer {
                let setup = &m["setup_file"];
                crate::ingest::link_setup_file(
                    &tx,
                    c,
                    sha,
                    &blob_kind,
                    Some(&format!(
                        "{}/{}",
                        setup["car_folder"].as_str().unwrap_or(""),
                        setup["relative_path"].as_str().unwrap_or("")
                    )),
                    m["original_name"].as_str().unwrap_or(sha),
                )?;
                summary.setup_files += 1;
            } else {
                summary.skipped += 1;
            }
        }
        tx.commit()?;
        Ok(summary)
    })
}

fn validate_field_manifest(manifest: &Value) -> Result<()> {
    let field = &manifest["field_recording"];
    let customer = field["customer_id"].as_i64().filter(|id| *id > 0);
    let subsession = field.get("subsession_id");
    if manifest["manifest_version"] != 1
        || customer.is_none()
        || customer != manifest["driver"]["customer_id"].as_i64()
        || !field["session_num"].as_i64().is_some_and(|n| n >= 0)
        || !field["sample_count"].as_i64().is_some_and(|n| n > 0)
        || !subsession.is_some_and(|s| s.is_null() || s.as_i64().is_some_and(|n| n > 0))
    {
        return Err(Error::invalid(
            "invalid field recording manifest identity or version",
        ));
    }
    Ok(())
}

pub fn summary_text(s: &RebuildSummary) -> Value {
    json!({"manifests": s.manifests, "recordings": s.recordings, "setup_files": s.setup_files, "skipped": s.skipped})
}

#[cfg(test)]
mod field_manifest_tests {
    use super::*;

    #[test]
    fn required_field_identity_is_never_defaulted() {
        let complete = json!({"manifest_version":1,"driver":{"customer_id":1},
            "field_recording":{"customer_id":1,"subsession_id":42,"session_num":0,"sample_count":1}});
        assert!(validate_field_manifest(&complete).is_ok());
        for key in [
            "customer_id",
            "subsession_id",
            "session_num",
            "sample_count",
        ] {
            let mut missing = complete.clone();
            missing["field_recording"]
                .as_object_mut()
                .unwrap()
                .remove(key);
            assert!(validate_field_manifest(&missing).is_err(), "{key}");
        }
        let mut incompatible = complete;
        incompatible["manifest_version"] = json!(2);
        assert!(validate_field_manifest(&incompatible).is_err());
    }
}
