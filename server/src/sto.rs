//! Locked imported STO sources and immutable working revisions. Python owns the codec;
//! these bytes never enter observed garage setup identity or get written to the sim.
use axum::body::Bytes;
use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, StatusCode, header};
use axum::response::{IntoResponse, Response};
use base64::{Engine, engine::general_purpose::STANDARD};
use rusqlite::{Connection, OptionalExtension, params};
use serde::Deserialize;
use serde_json::{Value, json};
use std::process::Stdio;
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

use crate::app::AppState;
use crate::error::{Error, Result};
use crate::util::{iso_now, sha256_hex};

const MAX_BYTES: usize = 16 << 20;
const MAX_CODEC_OUTPUT: usize = 48 << 20;

fn source_id(customer: i64, car: &str, path: &str, sha: &str) -> String {
    format!(
        "source:{}",
        sha256_hex(json!([customer, car, path, sha]).to_string().as_bytes())
    )
}

fn sources(conn: &Connection, customer: i64) -> Result<Vec<Value>> {
    let mut stmt = conn.prepare("SELECT car_folder, relative_path, blob_sha256, last_seen_at, present FROM setup_files WHERE customer_id = ?1 AND format = 'sto' ORDER BY car_folder, relative_path, last_seen_at DESC")?;
    Ok(stmt.query_map([customer], |r| {
        let (car, name, sha): (String, String, String) = (r.get(0)?, r.get(1)?, r.get(2)?);
        Ok(json!({"id":source_id(customer,&car,&name,&sha),"name":name,"car_folder":car,"sha256":sha,"updated_at":r.get::<_,String>(3)?,"present":r.get::<_,i64>(4)? != 0,"locked":true,"current_version":1,"version_count":1}))
    })?.collect::<rusqlite::Result<_>>()?)
}

fn working_id(id: &str) -> Option<i64> {
    id.strip_prefix("coach:")
        .and_then(|s| s.parse::<i64>().ok())
        .filter(|n| *n > 0)
}

fn working(conn: &Connection, customer: i64, id: i64) -> Result<Option<Value>> {
    Ok(conn.query_row("SELECT f.name,f.car_folder,f.current_version,f.updated_at,r.sha256,f.parent_file_id,f.parent_version FROM sto_working_files f JOIN sto_revisions r ON r.file_id=f.file_id AND r.version=f.current_version WHERE f.customer_id=?1 AND f.file_id=?2",params![customer,id],|r| Ok(json!({"id":format!("coach:{id}"),"name":r.get::<_,String>(0)?,"car_folder":r.get::<_,String>(1)?,"locked":false,"current_version":r.get::<_,i64>(2)?,"version_count":r.get::<_,i64>(2)?,"updated_at":r.get::<_,String>(3)?,"sha256":r.get::<_,String>(4)?,"parent_file_id":r.get::<_,String>(5)?,"parent_version":r.get::<_,i64>(6)?,"present":true}))) .optional()?)
}

fn file(conn: &Connection, customer: i64, id: &str) -> Result<Value> {
    if let Some(n) = working_id(id) {
        return working(conn, customer, n)?.ok_or_else(|| Error::not_found("no such setup file"));
    }
    sources(conn, customer)?
        .into_iter()
        .find(|f| f["id"] == id)
        .ok_or_else(|| Error::not_found("no such setup file"))
}

fn history(conn: &Connection, id: &str, f: &Value) -> Result<Vec<Value>> {
    if let Some(n) = working_id(id) {
        let mut stmt=conn.prepare("SELECT version,sha256,created_at,message,based_on_version FROM sto_revisions WHERE file_id=?1 ORDER BY version DESC")?;
        return Ok(stmt.query_map([n],|r|Ok(json!({"version":r.get::<_,i64>(0)?,"sha256":r.get::<_,String>(1)?,"created_at":r.get::<_,String>(2)?,"message":r.get::<_,String>(3)?,"based_on_version":r.get::<_,Option<i64>>(4)?})))?.collect::<rusqlite::Result<_>>()?);
    }
    Ok(vec![
        json!({"version":1,"sha256":f["sha256"],"created_at":f["updated_at"],"message":"Imported original · locked","based_on_version":null}),
    ])
}

fn download_url(customer: i64, id: &str, version: i64) -> String {
    format!("/api/v1/drivers/{customer}/sto-files/{id}/download?version={version}")
}

fn detail(
    f: Value,
    version: i64,
    sha: &str,
    document: Value,
    history: Vec<Value>,
    reason: Option<String>,
    customer: i64,
) -> Value {
    let url = download_url(customer, f["id"].as_str().unwrap_or_default(), version);
    json!({"file":f,"version":version,"sha256":sha,"document":document,"history":history,"unavailable_reason":reason,"download_url":url})
}

#[derive(Default, Deserialize)]
pub struct Selection {
    version: Option<i64>,
}

pub async fn list(State(app): State<AppState>, Path(customer): Path<i64>) -> Result<Response> {
    crate::api::require_driver(&app, customer).await?;
    let files=app.store.read(move|conn|{
        let mut out=sources(conn,customer)?;
        let ids=conn.prepare("SELECT file_id FROM sto_working_files WHERE customer_id=?1 ORDER BY updated_at DESC,file_id DESC")?.query_map([customer],|r|r.get::<_,i64>(0))?.collect::<rusqlite::Result<Vec<_>>>()?;
        for id in ids {
            out.push(working(conn,customer,id)?.ok_or_else(||Error::internal("working setup has no revision"))?);
        }
        Ok(out)
    }).await?;
    Ok(crate::api::json_ok(json!({"files":files})))
}

async fn content(
    app: &AppState,
    customer: i64,
    id: &str,
    version: Option<i64>,
) -> Result<(Value, i64, Vec<u8>, Option<Value>, Vec<Value>)> {
    let id = id.to_string();
    let (f,v,bytes,doc,h)=app.store.read(move|conn|{
        let f=file(conn,customer,&id)?;
        let v=version.unwrap_or_else(|| f["current_version"].as_i64().unwrap_or(1));
        if v < 1 {return Err(Error::invalid("revision must be positive"));}
        let h=history(conn,&id,&f)?;
        if let Some(n)=working_id(&id) {
            let pair=conn.query_row("SELECT content,document_json FROM sto_revisions WHERE file_id=?1 AND version=?2",params![n,v],|r|Ok((r.get::<_,Vec<u8>>(0)?,r.get::<_,String>(1)?))).optional()?.ok_or_else(||Error::not_found("no such revision"))?;
            let doc=serde_json::from_str(&pair.1).map_err(|_|Error::internal("saved setup document is invalid"))?;
            return Ok((f,v,Some(pair.0),Some(doc),h));
        }
        if v != 1 {return Err(Error::not_found("imported originals have only revision 1"));}
        Ok((f,v,None,None,h))
    }).await?;
    let bytes = if let Some(bytes) = bytes {
        bytes
    } else {
        let sha = f["sha256"]
            .as_str()
            .ok_or_else(|| Error::internal("source hash missing"))?;
        let source_sha = sha.to_string();
        let size = app
            .store
            .read(move |conn| {
                Ok(conn.query_row(
                    "SELECT size FROM blobs WHERE sha256=?1",
                    [source_sha],
                    |r| r.get::<_, i64>(0),
                )?)
            })
            .await?;
        if size < 0 || size > MAX_BYTES as i64 {
            return Err(Error::unprocessable(
                "invalid_sto",
                "setup exceeds the 16 MiB limit",
            ));
        }
        let p = crate::ingest::ready_path(app, sha);
        if !p.is_file() {
            crate::ingest::restage_recording(app, sha).await?;
        }
        tokio::task::spawn_blocking(move || -> Result<Vec<u8>> {
            if std::fs::metadata(&p)?.len() > MAX_BYTES as u64 {
                return Err(Error::unprocessable(
                    "invalid_sto",
                    "setup exceeds the 16 MiB limit",
                ));
            }
            Ok(std::fs::read(p)?)
        })
        .await
        .map_err(|e| Error::internal(e.to_string()))??
    };
    if bytes.len() > MAX_BYTES {
        return Err(Error::unprocessable(
            "invalid_sto",
            "setup exceeds the 16 MiB limit",
        ));
    }
    let expected = h
        .iter()
        .find(|r| r["version"] == v)
        .and_then(|r| r["sha256"].as_str())
        .unwrap_or_default();
    if sha256_hex(&bytes) != expected {
        return Err(Error::unprocessable(
            "invalid_sto",
            "setup bytes do not match the archived hash",
        ));
    }
    Ok((f, v, bytes, doc, h))
}

async fn codec(app: &AppState, bytes: &[u8], write: Option<&Value>) -> Result<Value> {
    // The caller holds sto-operation before loading bytes, bounding queued memory too.
    let args = &app.cfg.worker.command;
    let index = args
        .iter()
        .position(|s| s.ends_with(".py"))
        .ok_or_else(|| {
            Error::unprocessable(
                "material_unavailable",
                "Python setup codec is not configured",
            )
        })?;
    let scripts = app
        .cfg
        .worker
        .scripts_dir
        .clone()
        .or_else(|| {
            std::path::Path::new(&args[index])
                .parent()
                .map(|p| p.to_path_buf())
        })
        .ok_or_else(|| Error::internal("worker scripts directory missing"))?;
    let mut request = json!({"schema":1,"operation":if write.is_some(){"encode"}else{"decode"},"profile_path":app.cfg.data_dir.join("sto-profile.json"),"data_base64":STANDARD.encode(bytes)});
    if let Some(body) = write {
        request["changes"] = body["changes"].clone();
        if let Some(notes) = body.get("notes") {
            request["notes"] = notes.clone();
        }
    }
    let input = serde_json::to_vec(&request).map_err(|e| Error::internal(e.to_string()))?;
    let mut command = tokio::process::Command::new(&args[0]);
    command
        .args(&args[1..index])
        .arg(scripts.join("sto_codec.py"))
        .env("PYTHONUTF8", "1")
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true);
    #[cfg(windows)]
    command.creation_flags(0x08000000);
    let mut child = command.spawn().map_err(|_| {
        Error::unprocessable("material_unavailable", "Python setup codec could not start")
    })?;
    let mut stdin = child
        .stdin
        .take()
        .ok_or_else(|| Error::internal("codec stdin missing"))?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| Error::internal("codec stdout missing"))?;
    let outcome = tokio::time::timeout(Duration::from_secs(30), async {
        let send = async {
            stdin.write_all(&input).await?;
            stdin.shutdown().await?;
            drop(stdin);
            Ok::<_, std::io::Error>(())
        };
        let receive = async {
            let mut output = Vec::new();
            stdout
                .take((MAX_CODEC_OUTPUT + 1) as u64)
                .read_to_end(&mut output)
                .await?;
            if output.len() > MAX_CODEC_OUTPUT {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::InvalidData,
                    "codec response exceeds bound",
                ));
            }
            Ok::<_, std::io::Error>(output)
        };
        let (_, output) = tokio::try_join!(send, receive)?;
        let status = child.wait().await?;
        Ok::<_, std::io::Error>((output, status))
    })
    .await;
    let (output, status) = match outcome {
        Ok(Ok(output)) => output,
        error => {
            let _ = child.kill().await;
            let _ = child.wait().await;
            return Err(if error.is_err() {
                Error::unavailable(1, "setup codec timed out")
            } else {
                Error::internal("setup codec response failed or exceeded its bound")
            });
        }
    };
    let result: Value = serde_json::from_slice(&output).map_err(|_| {
        Error::unprocessable(
            "material_unavailable",
            "Python setup codec returned no valid result",
        )
    })?;
    if result["schema"] != 1 {
        return Err(Error::internal("setup codec schema incompatible"));
    }
    if result["ok"] != true {
        let code = match result["error"]["code"].as_str() {
            Some("invalid_edit") => "invalid_edit",
            Some("unsupported_version") => "unsupported_version",
            Some("material_unavailable") => "material_unavailable",
            _ => "invalid_sto",
        };
        return Err(Error::unprocessable(
            code,
            result["error"]["message"]
                .as_str()
                .unwrap_or("The setup could not be decoded"),
        ));
    }
    if !status.success() {
        return Err(Error::internal("setup codec exited unsuccessfully"));
    }
    crate::schema::Schemas::global()
        .check("StoDocument", &result["document"])?
        .map_err(|_| Error::internal("setup codec document incompatible"))?;
    Ok(result)
}

pub async fn open(
    State(app): State<AppState>,
    Path((customer, id)): Path<(i64, String)>,
    Query(sel): Query<Selection>,
) -> Result<Response> {
    let _admission = app.upload_locks.lock("sto-operation").await;
    let (f, v, bytes, doc, h) = match content(&app, customer, &id, sel.version).await {
        Ok(c) => c,
        Err(e) => {
            // Preserve file identity and explain a temporarily unavailable NAS without losing the list.
            if matches!(&e,Error::Api(a) if a.status==StatusCode::NOT_FOUND || a.status==StatusCode::BAD_REQUEST)
            {
                return Err(e);
            }
            let lookup = id.clone();
            let (f, h) = app
                .store
                .read(move |c| {
                    let f = file(c, customer, &lookup)?;
                    let h = history(c, &lookup, &f)?;
                    Ok((f, h))
                })
                .await?;
            let version = sel
                .version
                .unwrap_or_else(|| f["current_version"].as_i64().unwrap_or(1));
            let sha = h
                .iter()
                .find(|r| r["version"] == version)
                .and_then(|r| r["sha256"].as_str())
                .ok_or_else(|| Error::not_found("no such revision"))?
                .to_string();
            let reason = match &e {
                Error::Api(a) => a.message.clone(),
                Error::Io(_) => {
                    "Setup bytes are unavailable; check the archive connection and retry.".into()
                }
                _ => {
                    "Saved revision metadata could not be verified. Select another revision.".into()
                }
            };
            return Ok(crate::api::json_ok(detail(
                f,
                version,
                &sha,
                Value::Null,
                h,
                Some(reason),
                customer,
            )));
        }
    };
    let sha = sha256_hex(&bytes);
    let (doc, reason) = match doc {
        Some(doc) => (doc, None),
        None => match codec(&app, &bytes, None).await {
            Ok(r) => (r["document"].clone(), None),
            Err(e) => (Value::Null, Some(e.to_string())),
        },
    };
    Ok(crate::api::json_ok(detail(
        f, v, &sha, doc, h, reason, customer,
    )))
}

fn safe_name(raw: &str) -> Result<String> {
    if raw.is_empty()
        || raw.chars().count() > 120
        || raw.trim() != raw
        || raw.ends_with('.')
        || raw
            .chars()
            .any(|c| c.is_control() || "<>:\"/\\|?*".contains(c))
    {
        return Err(Error::invalid(
            "Use a .sto filename without paths, reserved characters or trailing spaces",
        ));
    }
    let lower = raw.to_lowercase();
    let stem = lower
        .strip_suffix(".sto")
        .ok_or_else(|| Error::invalid("The filename must end in .sto"))?;
    if stem.is_empty() || stem.ends_with(".sto") || stem.ends_with('.') || stem.ends_with(' ') {
        return Err(Error::invalid(
            "Use one .sto extension and a nonempty filename",
        ));
    }
    let base = stem.split('.').next().unwrap_or_default();
    if ["con", "prn", "aux", "nul"].contains(&base)
        || (base.len() == 4
            && (base.starts_with("com") || base.starts_with("lpt"))
            && base.as_bytes()[3].is_ascii_digit()
            && base.as_bytes()[3] != b'0')
    {
        return Err(Error::invalid("That filename is reserved by Windows"));
    }
    Ok(format!("{}.sto", &raw[..raw.len() - 4]))
}

fn replay(conn: &Connection, key: &str, hash: &str) -> Result<Option<Value>> {
    let receipt: Option<(String, String)> = conn
        .query_row(
            "SELECT request_sha256,response_json FROM sto_write_receipts WHERE operation_id=?1",
            [key],
            |r| Ok((r.get(0)?, r.get(1)?)),
        )
        .optional()?;
    receipt
        .map(|(saved, response)| {
            if saved != hash {
                return Err(Error::unprocessable(
                    "idempotency_key_reused",
                    "This save key was used for different content",
                ));
            }
            serde_json::from_str(&response).map_err(|_| Error::internal("save receipt invalid"))
        })
        .transpose()
}

async fn write(
    app: AppState,
    customer: i64,
    id: String,
    headers: HeaderMap,
    body: Bytes,
    fork: bool,
) -> Result<Response> {
    let body = crate::schema::parse_request("StoWrite", &body)?;
    let key = headers
        .get("idempotency-key")
        .and_then(|v| v.to_str().ok())
        .filter(|s| !s.is_empty() && s.len() <= 128)
        .ok_or_else(|| Error::invalid("Idempotency-Key is required for Save"))?
        .to_string();
    let current = crate::userdata::if_match(&headers);
    let hash = sha256_hex(
        json!([
            customer,
            id,
            fork,
            body,
            headers.get("if-match").and_then(|v| v.to_str().ok())
        ])
        .to_string()
        .as_bytes(),
    );
    let _lock = app.idem_locks.lock(&format!("sto:{key}")).await;
    let (k, rh) = (key.clone(), hash.clone());
    if let Some(r) = app.store.read(move |c| replay(c, &k, &rh)).await? {
        return Ok(crate::api::json_status(
            if fork {
                StatusCode::CREATED
            } else {
                StatusCode::OK
            },
            r,
        ));
    }
    let _admission = app.upload_locks.lock("sto-operation").await;
    let base = body["base_version"]
        .as_i64()
        .ok_or_else(|| Error::invalid("base_version required"))?;
    let (f, _, bytes, _, _) = content(&app, customer, &id, Some(base)).await?;
    if !fork && f["locked"] == true {
        return Err(Error::conflict(
            "locked_source",
            "Imported originals are locked. Use Save As to create your own setup.",
        ));
    }
    if !fork && current != f["current_version"].as_i64() {
        let (cf, cv, cb, cd, ch) = content(&app, customer, &id, None).await?;
        return Err(crate::userdata::version_conflict(detail(
            cf,
            cv,
            &sha256_hex(&cb),
            cd.unwrap_or(Value::Null),
            ch,
            None,
            customer,
        )));
    }
    let name = if fork {
        safe_name(
            body["name"]
                .as_str()
                .ok_or_else(|| Error::invalid("Save As requires a filename"))?,
        )?
    } else {
        f["name"].as_str().unwrap_or_default().to_string()
    };
    let result = codec(&app, &bytes, Some(&body)).await?;
    let generated = STANDARD
        .decode(
            result["data_base64"]
                .as_str()
                .ok_or_else(|| Error::internal("codec generated bytes missing"))?,
        )
        .map_err(|_| Error::internal("codec generated bytes invalid"))?;
    let doc = result["document"].clone();
    let sha = sha256_hex(&generated);
    if generated.len() > MAX_BYTES || doc["sha256"] != sha {
        return Err(Error::internal("codec output hash/size mismatch"));
    }
    let response=app.store.write(move|conn|{
        let tx=conn.transaction()?;
        if let Some(r)=replay(&tx,&key,&hash)? {return Ok(r);}
        let now=iso_now();
        let fid=if fork {
            let car=f["car_folder"].as_str().unwrap_or_default();
            let exists:bool=tx.query_row("SELECT COUNT(*)>0 FROM sto_working_files WHERE customer_id=?1 AND car_folder=?2 COLLATE NOCASE AND name_key=?3",params![customer,car,name.to_lowercase()],|r|r.get(0))?;
            if exists{return Err(Error::conflict("duplicate_name","A working setup already has that filename for this car"));}
            tx.execute("INSERT INTO sto_working_files(customer_id,name,name_key,car_folder,parent_file_id,parent_version,current_version,created_at,updated_at) VALUES(?1,?2,?3,?4,?5,?6,1,?7,?7)",params![customer,name,name.to_lowercase(),car,id,base,now])?;
            tx.last_insert_rowid()
        } else {working_id(&id).ok_or_else(||Error::conflict("locked_source","Use Save As"))?};
        let version=if fork {1}else{
            let actual=file(&tx,customer,&id)?;
            if actual["current_version"].as_i64()!=current{
                let n=actual["current_version"].as_i64().unwrap_or(1);
                let (s,d):(String,String)=tx.query_row("SELECT sha256,document_json FROM sto_revisions WHERE file_id=?1 AND version=?2",params![fid,n],|r|Ok((r.get(0)?,r.get(1)?)))?;
                let h=history(&tx,&id,&actual)?;
                return Err(crate::userdata::version_conflict(detail(actual,n,&s,serde_json::from_str(&d).map_err(|_|Error::internal("stored document invalid"))?,h,None,customer)));
            }
            actual["current_version"].as_i64().unwrap_or(0)+1
        };
        tx.execute("INSERT INTO sto_revisions(file_id,version,sha256,content,document_json,created_at,message,based_on_version) VALUES(?1,?2,?3,?4,?5,?6,?7,?8)",params![fid,version,sha,generated,doc.to_string(),now,body["message"].as_str().unwrap_or(if fork{"Save As"}else{"Saved revision"}),base])?;
        tx.execute("UPDATE sto_working_files SET current_version=?2,updated_at=?3 WHERE file_id=?1",params![fid,version,now])?;
        let new_id=format!("coach:{fid}");
        let nf=file(&tx,customer,&new_id)?;
        let h=history(&tx,&new_id,&nf)?;
        let r=detail(nf,version,&sha,doc,h,None,customer);
        tx.execute("INSERT INTO sto_write_receipts(operation_id,request_sha256,response_json) VALUES(?1,?2,?3)",params![key,hash,r.to_string()])?;
        crate::db::bump_generation(&tx)?;
        tx.commit()?;
        Ok(r)
    }).await?;
    app.events.library_changed(customer, "setups");
    Ok(crate::api::json_status(
        if fork {
            StatusCode::CREATED
        } else {
            StatusCode::OK
        },
        response,
    ))
}

pub async fn save(
    State(app): State<AppState>,
    Path((c, id)): Path<(i64, String)>,
    headers: HeaderMap,
    body: Bytes,
) -> Result<Response> {
    write(app, c, id, headers, body, false).await
}
pub async fn save_as(
    State(app): State<AppState>,
    Path((c, id)): Path<(i64, String)>,
    headers: HeaderMap,
    body: Bytes,
) -> Result<Response> {
    write(app, c, id, headers, body, true).await
}

pub async fn download(
    State(app): State<AppState>,
    Path((customer, id)): Path<(i64, String)>,
    Query(sel): Query<Selection>,
) -> Result<Response> {
    let _admission = app.upload_locks.lock("sto-operation").await;
    let (f, v, bytes, _, _) = content(&app, customer, &id, sel.version).await?;
    let stem = f["name"]
        .as_str()
        .unwrap_or("setup.sto")
        .rsplit(['/', '\\'])
        .next()
        .unwrap_or("setup.sto");
    let stem = stem.get(..stem.len().saturating_sub(4)).unwrap_or("setup");
    let suffix = if let Some(n) = working_id(&id) {
        format!("coach-{n}-r{v}")
    } else {
        format!("original-{}", &sha256_hex(&bytes)[..12])
    };
    let name = format!("{stem}-{suffix}.sto");
    let encoded: String = name
        .bytes()
        .map(|b| {
            if b.is_ascii_alphanumeric() || b"-._".contains(&b) {
                (b as char).to_string()
            } else {
                format!("%{b:02X}")
            }
        })
        .collect();
    let disposition = format!("attachment; filename=\"{suffix}.sto\"; filename*=UTF-8''{encoded}");
    Ok((
        [
            (header::CONTENT_TYPE, "application/octet-stream".to_string()),
            (header::CONTENT_DISPOSITION, disposition),
            (header::CACHE_CONTROL, "no-store".to_string()),
        ],
        bytes,
    )
        .into_response())
}
