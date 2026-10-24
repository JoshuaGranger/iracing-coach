//! Pulls immutable Coach/NAS setup revisions into new managed copies only (nas-sync.md).
//! No source deletion/overwrite and no bulk I/O while driving, racing or evidence is stale.

use std::collections::BTreeMap;
#[cfg(test)]
use std::fs;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};

use crate::config::{Paths, server_authority};
use crate::http::{Body, HttpClient};
use crate::hub::Hub;
use crate::managed_fs::Dir;
#[cfg(test)]
use crate::util::uuid_v4;
use crate::util::{Clock, SystemClock, hex, iso_utc};

const MAX_SETUP: usize = 16 << 20;
const MAX_MANIFEST: usize = 1 << 20;
const RECEIPTS: &str = "setup-sync.json";
const POLL_MS: u64 = 60_000;

#[derive(Debug, Deserialize)]
struct Page {
    schema_version: u32,
    customer_id: Option<i64>,
    replay_upload: bool,
    files: Vec<SyncFile>,
    next_cursor: Option<String>,
    #[serde(flatten)]
    _extra: Map<String, Value>,
}

#[derive(Debug, Deserialize)]
struct SyncFile {
    id: String,
    version: u64,
    sha256: String,
    size: usize,
    relative_path: String,
    download_url: String,
    #[serde(flatten)]
    _extra: Map<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct Receipt {
    customer_id: i64,
    id: String,
    version: u64,
    sha256: String,
    #[serde(flatten)]
    extra: Map<String, Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct Book {
    schema_version: u32,
    installed: BTreeMap<String, Receipt>,
    #[serde(default)]
    intents: BTreeMap<String, Receipt>,
    last_error: Option<String>,
    last_sync_at: Option<String>,
    #[serde(flatten)]
    extra: Map<String, Value>,
}

impl Default for Book {
    fn default() -> Self {
        Self {
            schema_version: 1,
            installed: BTreeMap::new(),
            intents: BTreeMap::new(),
            last_error: None,
            last_sync_at: None,
            extra: Map::new(),
        }
    }
}

pub struct SetupSync {
    hub: Arc<Hub>,
    paths: Paths,
    http: HttpClient,
    collector_id: String,
    clock: Arc<dyn Clock>,
    book: Option<Book>,
    cursor: Option<String>,
    page_customer: Option<i64>,
    observed_driver: Option<i64>,
    observed_driver_epoch: u64,
    due_ms: u64,
    installed_this_run: u64,
}

impl SetupSync {
    pub fn new(hub: Arc<Hub>, server: &str, paths: Paths, collector_id: &str) -> Self {
        Self {
            hub,
            paths,
            http: HttpClient::new(&server_authority(server))
                .with_timeouts(Duration::from_secs(3), Duration::from_secs(30)),
            collector_id: collector_id.into(),
            clock: Arc::new(SystemClock::new()),
            book: None,
            cursor: None,
            page_customer: None,
            observed_driver: None,
            observed_driver_epoch: 0,
            due_ms: 0,
            installed_this_run: 0,
        }
    }

    pub fn with_clock(mut self, clock: Arc<dyn Clock>) -> Self {
        self.clock = clock;
        self
    }

    fn proceed(&self) -> impl Fn() -> bool + use<> {
        let hub = self.hub.clone();
        move || hub.gate.allows_bulk_work() && !hub.is_shutdown()
    }

    fn proceed_for_driver(&self, epoch: u64) -> impl Fn() -> bool + use<> {
        let hub = self.hub.clone();
        move || hub.gate.allows_bulk_work() && !hub.is_shutdown() && hub.driver_epoch() == epoch
    }

    pub fn run(mut self) {
        while !self.hub.is_shutdown() {
            std::thread::sleep(self.step());
        }
    }

    /// One bounded manifest page; an error never advances its cursor.
    pub fn step(&mut self) -> Duration {
        let driver = self
            .hub
            .status()
            .driver
            .as_ref()
            .and_then(|d| d["customer_id"].as_i64());
        let epoch = self.hub.driver_epoch();
        if driver != self.observed_driver || epoch != self.observed_driver_epoch {
            self.observed_driver = driver;
            self.observed_driver_epoch = epoch;
            self.cursor = None;
            self.page_customer = None;
            self.due_ms = 0;
        }
        let now = self.clock.mono_ms();
        if !(self.proceed())() || now < self.due_ms {
            return Duration::from_millis(200);
        }
        match self.pull_page() {
            Ok(()) => {
                self.due_ms = if self.cursor.is_some() {
                    now
                } else {
                    now + POLL_MS
                };
            }
            Err(error) => {
                if self.hub.driver_epoch() != self.observed_driver_epoch {
                    self.cursor = None;
                    self.page_customer = None;
                    self.due_ms = 0;
                    return Duration::from_millis(200);
                }
                if !(self.proceed())() {
                    return Duration::from_millis(200);
                }
                self.due_ms = now + 5_000;
                if self.hub.sync_info()["last_error"].as_str() != Some(&error) {
                    crate::warn!("setup sync: {error}");
                }
                let proceed = self.proceed();
                if let Some(book) = &self.book {
                    let mut next = book.clone();
                    next.last_error = Some(error.clone());
                    if save_book(&self.paths.state_dir, &next, &proceed).is_ok() {
                        self.book = Some(next);
                    }
                }
                self.publish("error", Some(error));
            }
        }
        Duration::from_millis(200)
    }

    fn publish(&self, state: &str, error: Option<String>) {
        self.hub
            .set_sync_info(json!({"state":state,"last_error":error,
            "last_sync_at":self.book.as_ref().and_then(|b|b.last_sync_at.clone()),
            "installed":self.installed_this_run}));
    }

    fn pull_page(&mut self) -> Result<(), String> {
        let proceed = self.proceed_for_driver(self.hub.driver_epoch());
        require(&proceed)?;
        if !valid_uuid(&self.collector_id) {
            return Err("invalid collector id".into());
        }
        if self.book.is_none() {
            self.book = Some(load_book(&self.paths.state_dir, &proceed)?);
        }
        let mut route = format!("/api/v1/collectors/{}/sync", self.collector_id);
        if let Some(cursor) = &self.cursor {
            route.push_str(&format!("?cursor={}", escape(cursor)));
        }
        let response = self
            .http
            .request_gated("GET", &route, &[], Body::None, &proceed, MAX_MANIFEST)
            .map_err(|e| e.to_string())?;
        if response.status == 409 {
            self.cursor = None;
            self.page_customer = None;
            return Err("driver changed; restarting setup sync".into());
        }
        if response.status != 200 {
            self.hub.set_replay_upload_capability(false);
            return Err(format!("manifest HTTP {}", response.status));
        }
        let raw: Value =
            serde_json::from_slice(&response.body).map_err(|e| format!("manifest JSON: {e}"))?;
        for key in [
            "schema_version",
            "customer_id",
            "replay_upload",
            "files",
            "next_cursor",
        ] {
            if raw.get(key).is_none() {
                return Err(format!("manifest missing {key}"));
            }
        }
        let page: Page =
            serde_json::from_value(raw).map_err(|e| format!("manifest fields: {e}"))?;
        if page.schema_version != 1 {
            return Err("unsupported setup sync schema".into());
        }
        if page.files.len() > 128
            || page
                .next_cursor
                .as_ref()
                .is_some_and(|c| c.len() > 512 || c.is_empty())
        {
            return Err("setup sync page exceeds bounds".into());
        }
        self.hub.set_replay_upload_capability(page.replay_upload);
        let Some(customer) = page.customer_id.filter(|n| *n > 0) else {
            if !page.files.is_empty() || page.next_cursor.is_some() {
                return Err("unscoped setup sync page".into());
            }
            self.cursor = None;
            self.page_customer = None;
            self.publish("waiting_for_driver", None);
            return Ok(());
        };
        if self.observed_driver.is_some_and(|id| id != customer)
            || (self.cursor.is_some() && self.page_customer != Some(customer))
        {
            self.cursor = None;
            self.page_customer = None;
            return Err("setup sync driver mismatch".into());
        }
        if page.next_cursor.is_some() && page.next_cursor == self.cursor {
            return Err("setup sync cursor did not advance".into());
        }
        let mut destinations = BTreeMap::new();
        for file in &page.files {
            validate_file(file, customer, &self.paths.setups_dir())?;
            let key = file.relative_path.to_lowercase();
            let identity = (&file.id, file.version, &file.sha256);
            if destinations.insert(key, identity).is_some() {
                return Err("duplicate setup sync destination".into());
            }
        }
        for file in &page.files {
            require(&proceed)?;
            self.install(file, customer, &proceed)?;
        }
        self.page_customer = Some(customer);
        self.cursor = page.next_cursor;
        if self.cursor.is_none() {
            let mut next = self.book.as_ref().expect("loaded book").clone();
            next.last_error = None;
            next.last_sync_at = Some(iso_utc(self.clock.unix_ms()));
            save_book(&self.paths.state_dir, &next, &proceed)?;
            self.book = Some(next);
            self.publish("ok", None);
        }
        Ok(())
    }

    fn install(
        &mut self,
        file: &SyncFile,
        customer: i64,
        proceed: &dyn Fn() -> bool,
    ) -> Result<(), String> {
        require(proceed)?;
        // A change in driver during a page cannot install the previous driver's files.
        if self
            .hub
            .status()
            .driver
            .as_ref()
            .and_then(|d| d["customer_id"].as_i64())
            .is_some_and(|id| id != customer)
        {
            return Err("driver changed during setup sync".into());
        }
        let root = self.paths.setups_dir();
        let destination = root.join(&file.relative_path);
        let key = file.relative_path.to_lowercase();
        let book = self.book.as_ref().expect("loaded book");
        if book
            .installed
            .iter()
            .chain(book.intents.iter())
            .any(|(path, receipt)| {
                receipt.customer_id == customer
                    && receipt.id == file.id
                    && receipt.version == file.version
                    && (receipt.sha256 != file.sha256 || path != &key)
            })
        {
            return Err("immutable setup revision changed identity or destination".into());
        }
        let receipt = book.installed.get(&key);
        let intent = book.intents.get(&key);
        if receipt.into_iter().chain(intent).any(|receipt| {
            receipt.customer_id != customer
                || receipt.id != file.id
                || receipt.version != file.version
                || receipt.sha256 != file.sha256
        }) {
            return Err(format!(
                "managed setup identity collision: {}",
                file.relative_path
            ));
        }
        let remembered = receipt.is_some();
        let prepared = intent.is_some();
        let parent = destination.parent().ok_or("managed setup has no parent")?;
        let leaf = destination
            .file_name()
            .and_then(|n| n.to_str())
            .ok_or("invalid managed setup leaf")?;
        let dir = match Dir::open(parent, false, proceed) {
            Ok(dir) => dir,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound && remembered => return Ok(()),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound && prepared => {
                return Err(
                    "unresolved installation intent; absent revision will not be recreated".into(),
                );
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                Dir::open(parent, true, proceed).map_err(|e| e.to_string())?
            }
            Err(e) => return Err(format!("managed setup ancestor: {e}")),
        };
        match dir.open_regular(leaf) {
            Ok(mut existing) => {
                if existing.metadata().map_err(|e| e.to_string())?.len() != file.size as u64
                    || hash_handle(&mut existing, proceed)? != file.sha256
                {
                    return Err(format!("preserved changed setup: {}", file.relative_path));
                }
                require(proceed)?;
                if !remembered || prepared {
                    self.remember(file, customer, proceed)?;
                }
                return Ok(());
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                if remembered {
                    return Ok(());
                }
                if prepared {
                    return Err(
                        "unresolved installation intent; absent revision will not be recreated"
                            .into(),
                    );
                }
            }
            Err(e) => return Err(format!("managed setup target: {e}")),
        }
        let response = self
            .http
            .request_gated(
                "GET",
                &file.download_url,
                &[],
                Body::None,
                proceed,
                MAX_SETUP,
            )
            .map_err(|e| e.to_string())?;
        if response.status != 200 || response.body.len() != file.size {
            return Err(format!(
                "setup download HTTP {} or size mismatch",
                response.status
            ));
        }
        if hash_bytes(&response.body, proceed)? != file.sha256 {
            return Err("setup download hash mismatch".into());
        }
        require(proceed)?;
        let mut temp = dir.temp().map_err(|e| e.to_string())?;
        write_gated(temp.file(), &response.body, proceed)?;
        require(proceed)?;
        temp.file().sync_all().map_err(|e| e.to_string())?;
        temp.file()
            .seek(SeekFrom::Start(0))
            .map_err(|e| e.to_string())?;
        if hash_handle(temp.file(), proceed)? != file.sha256 {
            return Err("setup temp read-back hash mismatch".into());
        }
        self.prepare(file, customer, proceed)?;
        require(proceed)?;
        temp.publish_new(leaf)
            .map_err(|e| format!("managed setup publish: {e}"))?;
        require(proceed)?;
        self.remember(file, customer, proceed)?;
        self.installed_this_run += 1;
        self.hub.wake_uploader();
        // Temp and all directory handles remain pinned until after the durable acknowledgement.
        Ok(())
    }

    fn prepare(
        &mut self,
        file: &SyncFile,
        customer: i64,
        proceed: &dyn Fn() -> bool,
    ) -> Result<(), String> {
        let mut next = self.book.as_ref().expect("loaded book").clone();
        next.intents.insert(
            file.relative_path.to_lowercase(),
            receipt_for(file, customer),
        );
        save_book(&self.paths.state_dir, &next, proceed)?;
        self.book = Some(next);
        Ok(())
    }

    fn remember(
        &mut self,
        file: &SyncFile,
        customer: i64,
        proceed: &dyn Fn() -> bool,
    ) -> Result<(), String> {
        let mut next = self.book.as_ref().expect("loaded book").clone();
        let key = file.relative_path.to_lowercase();
        let receipt = next
            .intents
            .remove(&key)
            .or_else(|| next.installed.get(&key).cloned())
            .unwrap_or_else(|| receipt_for(file, customer));
        next.installed.insert(key, receipt);
        save_book(&self.paths.state_dir, &next, proceed)?;
        self.book = Some(next);
        Ok(())
    }
}

fn receipt_for(file: &SyncFile, customer: i64) -> Receipt {
    Receipt {
        customer_id: customer,
        id: file.id.clone(),
        version: file.version,
        sha256: file.sha256.clone(),
        extra: Map::new(),
    }
}
fn require(proceed: &dyn Fn() -> bool) -> Result<(), String> {
    if proceed() {
        Ok(())
    } else {
        Err("bulk sync paused".into())
    }
}
fn valid_uuid(id: &str) -> bool {
    id.len() == 36
        && id.bytes().enumerate().all(|(i, b)| {
            if [8, 13, 18, 23].contains(&i) {
                b == b'-'
            } else {
                b.is_ascii_hexdigit()
            }
        })
}
fn hash_text(text: &str) -> String {
    hex(&Sha256::digest(text.as_bytes()))
}
fn hash_bytes(bytes: &[u8], proceed: &dyn Fn() -> bool) -> Result<String, String> {
    let mut hash = Sha256::new();
    for piece in bytes.chunks(32 * 1024) {
        require(proceed)?;
        hash.update(piece);
    }
    Ok(hex(&hash.finalize()))
}
fn write_gated(
    out: &mut impl Write,
    bytes: &[u8],
    proceed: &dyn Fn() -> bool,
) -> Result<(), String> {
    for piece in bytes.chunks(32 * 1024) {
        require(proceed)?;
        out.write_all(piece).map_err(|e| e.to_string())?;
    }
    require(proceed)
}
fn hash_handle(file: &mut File, proceed: &dyn Fn() -> bool) -> Result<String, String> {
    let mut hash = Sha256::new();
    let mut buf = [0u8; 32 * 1024];
    let mut total = 0usize;
    loop {
        require(proceed)?;
        let n = file.read(&mut buf).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        total += n;
        if total > MAX_SETUP {
            return Err("setup file grew beyond limit".into());
        }
        hash.update(&buf[..n]);
    }
    Ok(hex(&hash.finalize()))
}

fn escape(text: &str) -> String {
    text.bytes()
        .map(|b| {
            if b.is_ascii_alphanumeric() || b"-._~".contains(&b) {
                (b as char).to_string()
            } else {
                format!("%{b:02X}")
            }
        })
        .collect()
}
fn safe_component(text: &str) -> bool {
    if text.is_empty()
        || matches!(text, "." | "..")
        || text.ends_with(['.', ' '])
        || text
            .chars()
            .any(|c| c.is_control() || "/\\:<>\"|?*".contains(c))
    {
        return false;
    }
    let stem = text
        .split('.')
        .next()
        .unwrap_or("")
        .trim_end_matches(' ')
        .to_ascii_uppercase();
    !matches!(
        stem.as_str(),
        "CON" | "PRN" | "AUX" | "NUL" | "CLOCK$" | "CONIN$" | "CONOUT$"
    ) && !["COM", "LPT"].iter().any(|prefix| {
        stem.strip_prefix(prefix).is_some_and(|tail| {
            matches!(
                tail,
                "0" | "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9" | "¹" | "²" | "³"
            )
        })
    })
}
fn validate_file(file: &SyncFile, customer: i64, root: &Path) -> Result<(), String> {
    let source = file.id.strip_prefix("source:").is_some_and(|s| {
        s.len() == 64
            && s.bytes()
                .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
    });
    let coach = file
        .id
        .strip_prefix("coach:")
        .and_then(|n| n.parse::<u64>().ok())
        .is_some_and(|n| n > 0);
    if !(source || coach)
        || file.id.len() > 128
        || file.version == 0
        || file.version > i64::MAX as u64
        || (source && file.version != 1)
        || file.sha256.len() != 64
        || !file
            .sha256
            .bytes()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
        || file.size == 0
        || file.size > MAX_SETUP
    {
        return Err("invalid setup sync identity/size".into());
    }
    let expected = format!(
        "/api/v1/drivers/{customer}/sto-files/{}/download?version={}",
        file.id, file.version
    );
    if file.download_url != expected {
        return Err("unscoped setup download route".into());
    }
    let parts: Vec<_> = file.relative_path.split('/').collect();
    if parts.len() != if source { 4 } else { 3 }
        || parts.get(1) != Some(&"Coach Synced")
        || (source && parts.get(2) != Some(&"NAS"))
        || parts.iter().any(|p| !safe_component(p))
        || parts[0].encode_utf16().count() > 64
        || file.relative_path.encode_utf16().count() > 180
        || std::path::absolute(root)
            .map_err(|e| e.to_string())?
            .join(&file.relative_path)
            .to_string_lossy()
            .encode_utf16()
            .count()
            > 240
    {
        return Err("unsafe/long managed setup path".into());
    }
    let id_hash = hash_text(&file.id);
    let suffix = if source {
        format!("--{}--{}.sto", &id_hash[..12], &file.sha256[..16])
    } else {
        format!(
            "--{}--v{}--{}.sto",
            &id_hash[..12],
            file.version,
            &file.sha256[..16]
        )
    };
    let leaf = parts.last().expect("path length checked");
    let Some(stem) = leaf.strip_suffix(&suffix) else {
        return Err("managed setup name does not match full identity".into());
    };
    if !safe_component(stem) || stem.encode_utf16().count() > 32 {
        return Err("unsafe/long managed setup stem".into());
    }
    Ok(())
}
fn load_book(state: &Path, proceed: &dyn Fn() -> bool) -> Result<Book, String> {
    require(proceed)?;
    let dir = match Dir::open(state, false, proceed) {
        Ok(dir) => dir,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Book::default()),
        Err(e) => return Err(e.to_string()),
    };
    let mut file = match dir.open_regular(RECEIPTS) {
        Ok(file) => file,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(Book::default()),
        Err(e) => return Err(e.to_string()),
    };
    let mut bytes = Vec::new();
    let mut piece = [0u8; 32 * 1024];
    loop {
        require(proceed)?;
        let n = file.read(&mut piece).map_err(|e| e.to_string())?;
        if n == 0 {
            break;
        }
        if bytes.len() + n > 16 << 20 {
            return Err("setup sync receipts exceed bounds".into());
        }
        bytes.extend_from_slice(&piece[..n]);
    }
    let text = std::str::from_utf8(&bytes).map_err(|e| e.to_string())?;
    let book: Book = serde_json::from_str(text.trim_start_matches('\u{feff}'))
        .map_err(|e| format!("setup sync receipts: {e}"))?;
    if book.schema_version != 1 {
        return Err("unsupported setup sync receipts schema".into());
    }
    Ok(book)
}
fn save_book(state: &Path, book: &Book, proceed: &dyn Fn() -> bool) -> Result<(), String> {
    require(proceed)?;
    let dir = Dir::open(state, true, proceed).map_err(|e| e.to_string())?;
    let mut temp = dir.temp().map_err(|e| e.to_string())?;
    let bytes = serde_json::to_vec_pretty(book).map_err(|e| e.to_string())?;
    write_gated(temp.file(), &bytes, proceed)?;
    require(proceed)?;
    temp.file().sync_all().map_err(|e| e.to_string())?;
    temp.replace(RECEIPTS).map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::gate::Evidence;
    use crate::journal::Journal;
    use crate::util::ManualClock;
    use std::io::{BufRead, BufReader};
    use std::net::TcpListener;
    use std::sync::{
        Mutex,
        atomic::{AtomicBool, Ordering},
    };
    use std::thread::{self, JoinHandle};

    const CID: &str = "11111111-2222-4333-8444-555555555555";
    const CUSTOMER: i64 = 123456;
    const MANIFEST: &str = "/api/v1/collectors/11111111-2222-4333-8444-555555555555/sync";
    type Hook = Arc<dyn Fn() + Send + Sync>;
    #[derive(Clone)]
    struct Reply {
        status: u16,
        body: Vec<u8>,
        hook: Option<Hook>,
    }
    struct Server {
        url: String,
        routes: Arc<Mutex<BTreeMap<String, Reply>>>,
        requests: Arc<Mutex<Vec<String>>>,
        stop: Arc<AtomicBool>,
        thread: Option<JoinHandle<()>>,
    }
    impl Server {
        fn new() -> Self {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            listener.set_nonblocking(true).unwrap();
            let url = format!("http://{}", listener.local_addr().unwrap());
            let routes = Arc::new(Mutex::new(BTreeMap::<String, Reply>::new()));
            let requests = Arc::new(Mutex::new(Vec::new()));
            let stop = Arc::new(AtomicBool::new(false));
            let (r, q, s) = (routes.clone(), requests.clone(), stop.clone());
            let thread = thread::spawn(move || {
                while !s.load(Ordering::SeqCst) {
                    match listener.accept() {
                        Ok((mut socket, _)) => {
                            socket.set_nonblocking(false).unwrap();
                            socket
                                .set_write_timeout(Some(Duration::from_secs(2)))
                                .unwrap();
                            socket
                                .set_read_timeout(Some(Duration::from_secs(2)))
                                .unwrap();
                            let mut reader = BufReader::new(socket.try_clone().unwrap());
                            let mut line = String::new();
                            if reader.read_line(&mut line).is_err() {
                                continue;
                            }
                            let route = line.split_whitespace().nth(1).unwrap_or("").to_owned();
                            loop {
                                line.clear();
                                if reader.read_line(&mut line).unwrap_or(0) == 0 || line == "\r\n" {
                                    break;
                                }
                            }
                            q.lock().unwrap().push(route.clone());
                            let reply = r.lock().unwrap().get(&route).cloned().unwrap_or(Reply {
                                status: 404,
                                body: b"{}".to_vec(),
                                hook: None,
                            });
                            let head = format!(
                                "HTTP/1.1 {} OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                                reply.status,
                                reply.body.len()
                            );
                            if socket.write_all(head.as_bytes()).is_ok() {
                                for (i, piece) in reply.body.chunks(32 * 1024).enumerate() {
                                    if socket.write_all(piece).is_err() {
                                        break;
                                    }
                                    if i == 0
                                        && let Some(hook) = &reply.hook
                                    {
                                        hook();
                                        thread::sleep(Duration::from_millis(10));
                                    }
                                }
                            }
                        }
                        Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                            thread::sleep(Duration::from_millis(2))
                        }
                        Err(_) => break,
                    }
                }
            });
            Self {
                url,
                routes,
                requests,
                stop,
                thread: Some(thread),
            }
        }
        fn set(&self, route: &str, status: u16, body: Vec<u8>) {
            self.routes.lock().unwrap().insert(
                route.into(),
                Reply {
                    status,
                    body,
                    hook: None,
                },
            );
        }
        fn page(&self, route: &str, files: Vec<Value>, cursor: Option<&str>) {
            self.set(route,200,serde_json::to_vec(&json!({"schema_version":1,"customer_id":CUSTOMER,"replay_upload":true,"files":files,"next_cursor":cursor,"future":{"retained":true}})).unwrap());
        }
        fn count(&self, route: &str) -> usize {
            self.requests
                .lock()
                .unwrap()
                .iter()
                .filter(|p| p.as_str() == route)
                .count()
        }
    }
    impl Drop for Server {
        fn drop(&mut self) {
            self.stop.store(true, Ordering::SeqCst);
            if let Some(t) = self.thread.take() {
                t.join().unwrap();
            }
        }
    }
    struct Env {
        root: std::path::PathBuf,
        paths: Paths,
        clock: Arc<ManualClock>,
        hub: Arc<Hub>,
    }
    impl Env {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!("coach-sync-test-{}", uuid_v4()));
            let paths = Paths {
                state_dir: root.join("state"),
                iracing_root: root.join("iRacing"),
            };
            fs::create_dir_all(paths.setups_dir()).unwrap();
            let clock = Arc::new(ManualClock::new(1_790_000_000_000));
            let hub = Hub::new(clock.clone(), Journal::in_memory(), true);
            hub.gate.observe_with_session(
                Evidence::Live {
                    on_track: Some(false),
                },
                Some(false),
            );
            let mut status = hub.status();
            status.driver = Some(json!({"customer_id":CUSTOMER,"name":"Synthetic"}));
            hub.set_status(status);
            Self {
                root,
                paths,
                clock,
                hub,
            }
        }
        fn worker(&self, server: &Server) -> SetupSync {
            SetupSync::new(self.hub.clone(), &server.url, self.paths.clone(), CID)
                .with_clock(self.clock.clone())
        }
        fn next(&self) {
            self.clock.advance(POLL_MS + 1);
            self.hub.gate.observe_with_session(
                Evidence::Live {
                    on_track: Some(false),
                },
                Some(false),
            );
        }
    }
    impl Drop for Env {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.root);
        }
    }
    fn item(id: &str, bytes: &[u8], version: u64) -> Value {
        let sha = hash_bytes(bytes, &|| true).unwrap();
        let identity = hash_text(id);
        let nas = id.starts_with("source:");
        let path = if nas {
            format!(
                "car/Coach Synced/NAS/test--{}--{}.sto",
                &identity[..12],
                &sha[..16]
            )
        } else {
            format!(
                "car/Coach Synced/test--{}--v{version}--{}.sto",
                &identity[..12],
                &sha[..16]
            )
        };
        json!({"id":id,"version":version,"sha256":sha,"size":bytes.len(),"relative_path":path,
            "download_url":format!("/api/v1/drivers/{CUSTOMER}/sto-files/{id}/download?version={version}"),"future_field":[1,2]})
    }
    fn route(file: &Value) -> &str {
        file["download_url"].as_str().unwrap()
    }
    fn dest(env: &Env, file: &Value) -> std::path::PathBuf {
        env.paths
            .setups_dir()
            .join(file["relative_path"].as_str().unwrap())
    }

    #[test]
    fn downloads_exact_revision_preserves_source_and_does_not_resurrect_rig_deletion() {
        let env = Env::new();
        let server = Server::new();
        let bytes = b"synthetic setup bytes";
        let file = item("coach:1", bytes, 1);
        let source = env.paths.setups_dir().join("car/purchased.sto");
        fs::create_dir_all(source.parent().unwrap()).unwrap();
        fs::write(&source, b"original").unwrap();
        server.page(MANIFEST, vec![file.clone()], None);
        server.set(route(&file), 200, bytes.to_vec());
        let mut worker = env.worker(&server);
        worker.step();
        assert_eq!(fs::read(dest(&env, &file)).unwrap(), bytes);
        assert_eq!(fs::read(&source).unwrap(), b"original");
        assert_eq!(env.hub.sync_info()["state"], "ok");
        assert!(env.hub.replay_upload_capability());
        fs::remove_file(dest(&env, &file)).unwrap();
        env.next();
        let mut restarted = env.worker(&server);
        restarted.step();
        assert!(!dest(&env, &file).exists());
        assert_eq!(
            server.count(route(&file)),
            1,
            "deleted exact revision must stay removed"
        );
        let file2 = item("coach:1", b"changed setup", 2);
        server.page(MANIFEST, vec![file2.clone()], None);
        server.set(route(&file2), 200, b"changed setup".to_vec());
        env.next();
        restarted.step();
        assert_eq!(fs::read(dest(&env, &file2)).unwrap(), b"changed setup");
    }
    #[test]
    fn equal_existing_copy_is_recorded_but_changed_copy_is_preserved() {
        let env = Env::new();
        let server = Server::new();
        let file = item("coach:2", b"good", 1);
        let path = dest(&env, &file);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, b"good").unwrap();
        server.page(MANIFEST, vec![file.clone()], None);
        let mut worker = env.worker(&server);
        worker.step();
        assert_eq!(server.count(route(&file)), 0);
        fs::write(&path, b"user").unwrap();
        env.next();
        worker.step();
        assert_eq!(fs::read(&path).unwrap(), b"user");
        assert!(
            env.hub.sync_info()["last_error"]
                .as_str()
                .unwrap()
                .contains("preserved changed")
        );
        assert_eq!(server.count(route(&file)), 0);
    }
    #[test]
    fn malformed_manifest_path_driver_and_hash_never_publish() {
        for case in [
            "path",
            "driver",
            "hash",
            "missing",
            "schema",
            "duplicate",
            "size",
            "device",
            "long",
            "redirect",
        ] {
            let env = Env::new();
            let server = Server::new();
            let mut file = item("coach:3", b"good", 1);
            match case {
                "path" => file["relative_path"] = json!("car/../source.sto"),
                "driver" => {
                    file["download_url"] =
                        json!("/api/v1/drivers/9/sto-files/coach:3/download?version=1")
                }
                "hash" => {}
                "size" => file["size"] = json!(MAX_SETUP + 1),
                "device" => {
                    file["relative_path"] = json!(
                        file["relative_path"]
                            .as_str()
                            .unwrap()
                            .replacen("car/", "CON/", 1)
                    )
                }
                "long" => {
                    file["relative_path"] = json!(format!("{}/Coach Synced/a.sto", "x".repeat(65)))
                }
                "redirect" => {}
                _ => {}
            }
            if case == "missing" {
                server.set(
                    MANIFEST,
                    200,
                    b"{\"schema_version\":1,\"files\":[]}".to_vec(),
                );
            } else if case == "schema" {
                server.set(MANIFEST,200,serde_json::to_vec(&json!({"schema_version":2,"customer_id":CUSTOMER,"replay_upload":true,"files":[],"next_cursor":null})).unwrap());
            } else {
                server.page(
                    MANIFEST,
                    if case == "duplicate" {
                        vec![file.clone(), file.clone()]
                    } else {
                        vec![file.clone()]
                    },
                    None,
                );
            }
            server.set(
                route(&file),
                if case == "redirect" { 302 } else { 200 },
                if case == "hash" {
                    b"evil".to_vec()
                } else {
                    b"good".to_vec()
                },
            );
            let mut worker = env.worker(&server);
            worker.step();
            assert_eq!(env.hub.sync_info()["state"], "error", "{case}");
            assert!(!dest(&env, &file).is_file(), "{case}");
        }
    }
    #[test]
    fn race_unknown_stale_and_ontrack_cannot_start_a_request() {
        let env = Env::new();
        let server = Server::new();
        server.page(MANIFEST, vec![], None);
        let mut worker = env.worker(&server);
        for (evidence, race) in [
            (
                Evidence::Live {
                    on_track: Some(false),
                },
                Some(true),
            ),
            (
                Evidence::Live {
                    on_track: Some(false),
                },
                None,
            ),
            (
                Evidence::Live {
                    on_track: Some(true),
                },
                Some(false),
            ),
            (Evidence::Stale, Some(false)),
        ] {
            env.hub.gate.observe_with_session(evidence, race);
            worker.step();
            assert_eq!(server.count(MANIFEST), 0);
        }
        env.hub.gate.observe_with_session(
            Evidence::Live {
                on_track: Some(false),
            },
            Some(false),
        );
        worker.step();
        assert_eq!(server.count(MANIFEST), 1);
    }
    #[test]
    fn interrupted_download_leaves_no_partial_setup_then_retries() {
        let env = Env::new();
        let server = Server::new();
        let bytes = vec![7u8; 512 * 1024];
        let file = item("coach:4", &bytes, 1);
        server.page(MANIFEST, vec![file.clone()], None);
        let hub = env.hub.clone();
        server.routes.lock().unwrap().insert(
            route(&file).into(),
            Reply {
                status: 200,
                body: bytes.clone(),
                hook: Some(Arc::new(move || {
                    hub.gate.observe_with_session(
                        Evidence::Live {
                            on_track: Some(false),
                        },
                        Some(true),
                    )
                })),
            },
        );
        let mut worker = env.worker(&server);
        worker.step();
        assert!(!dest(&env, &file).exists());
        server.set(route(&file), 200, bytes.clone());
        env.next();
        worker.step();
        assert_eq!(
            fs::read(dest(&env, &file)).unwrap_or_else(|e| panic!(
                "retry failed: {e}; status {:?}; requests {:?}",
                env.hub.sync_info(),
                server.requests.lock().unwrap()
            )),
            bytes
        );
    }
    #[test]
    fn failed_page_retries_same_cursor_and_409_resets_it() {
        let env = Env::new();
        let server = Server::new();
        let one = item("coach:5", b"one", 1);
        let two = item("coach:6", b"two", 1);
        server.page(MANIFEST, vec![one.clone()], Some("next:page/+"));
        server.set(route(&one), 200, b"bad".to_vec());
        let mut worker = env.worker(&server);
        worker.step();
        assert_eq!(worker.cursor, None);
        server.set(route(&one), 200, b"one".to_vec());
        env.next();
        worker.step();
        assert_eq!(worker.cursor.as_deref(), Some("next:page/+"));
        let next = format!("{MANIFEST}?cursor=next%3Apage%2F%2B");
        server.set(&next, 409, b"{}".to_vec());
        worker.step();
        assert_eq!(worker.cursor, None);
        server.page(MANIFEST, vec![two.clone()], None);
        server.set(route(&two), 200, b"two".to_vec());
        env.next();
        worker.step();
        assert!(dest(&env, &two).exists());
    }
    #[test]
    fn old_server_404_disables_replay_capability_and_later_valid_page_enables_it() {
        let env = Env::new();
        let server = Server::new();
        let mut worker = env.worker(&server);
        env.hub.set_replay_upload_capability(true);
        worker.step();
        assert!(!env.hub.replay_upload_capability());
        server.page(MANIFEST, vec![], None);
        env.next();
        worker.step();
        assert!(env.hub.replay_upload_capability());
        env.clock.advance(120_001);
        assert!(!env.hub.replay_upload_capability());
    }
    #[test]
    fn receipt_unknown_fields_survive_and_full_identity_collision_refuses() {
        let env = Env::new();
        let server = Server::new();
        let file = item("coach:7", b"good", 1);
        let mut book = Book::default();
        book.extra
            .insert("future".into(), json!({"preserved":true}));
        save_book(&env.paths.state_dir, &book, &|| true).unwrap();
        server.page(MANIFEST, vec![file.clone()], None);
        server.set(route(&file), 200, b"good".to_vec());
        let mut worker = env.worker(&server);
        worker.step();
        assert_eq!(
            load_book(&env.paths.state_dir, &|| true).unwrap().extra["future"]["preserved"],
            true
        );
        let mut book = load_book(&env.paths.state_dir, &|| true).unwrap();
        book.installed
            .get_mut(&file["relative_path"].as_str().unwrap().to_lowercase())
            .unwrap()
            .id = "coach:999".into();
        save_book(&env.paths.state_dir, &book, &|| true).unwrap();
        env.next();
        let mut worker = env.worker(&server);
        worker.step();
        assert!(
            env.hub.sync_info()["last_error"]
                .as_str()
                .unwrap()
                .contains("identity collision")
        );
        assert_eq!(fs::read(dest(&env, &file)).unwrap(), b"good");
    }
    #[test]
    fn nas_import_installs_only_beneath_managed_nas_folder_and_driver_change_resets_pass() {
        let env = Env::new();
        let server = Server::new();
        let id = format!("source:{}", "a".repeat(64));
        let file = item(&id, b"NAS source", 1);
        server.page(MANIFEST, vec![file.clone()], Some("old-driver"));
        server.set(route(&file), 200, b"NAS source".to_vec());
        let mut worker = env.worker(&server);
        worker.step();
        assert_eq!(fs::read(dest(&env, &file)).unwrap(), b"NAS source");
        let mut status = env.hub.status();
        status.driver = Some(json!({"customer_id":999}));
        env.hub.set_status(status);
        worker.step();
        assert_eq!(worker.cursor, None);
        assert_eq!(server.count(MANIFEST), 2);
        assert_eq!(server.count(route(&file)), 1);
        assert!(
            env.hub.sync_info()["last_error"]
                .as_str()
                .unwrap()
                .contains("driver mismatch")
        );
    }

    #[test]
    fn corrupt_receipts_fail_closed_without_overwriting_state_or_downloading() {
        let env = Env::new();
        let server = Server::new();
        fs::create_dir_all(&env.paths.state_dir).unwrap();
        let receipt = env.paths.state_dir.join(RECEIPTS);
        fs::write(&receipt, b"unknown torn receipt").unwrap();
        server.page(MANIFEST, vec![item("coach:10", b"good", 1)], None);
        let mut worker = env.worker(&server);
        worker.step();
        assert_eq!(server.count(MANIFEST), 0);
        assert_eq!(fs::read(&receipt).unwrap(), b"unknown torn receipt");
        assert_eq!(env.hub.sync_info()["state"], "error");
    }

    #[test]
    fn a_driver_change_during_download_aborts_before_file_publication() {
        let env = Env::new();
        let server = Server::new();
        let bytes = vec![31u8; 512 * 1024];
        let file = item("coach:40", &bytes, 1);
        server.page(MANIFEST, vec![file.clone()], None);
        let hub = env.hub.clone();
        server.routes.lock().unwrap().insert(
            route(&file).into(),
            Reply {
                status: 200,
                body: bytes,
                hook: Some(Arc::new(move || {
                    let mut status = hub.status();
                    status.driver = Some(json!({"customer_id":999}));
                    hub.set_status(status);
                })),
            },
        );
        let mut worker = env.worker(&server);
        worker.step();
        assert!(!dest(&env, &file).exists());
        assert_eq!(worker.installed_this_run, 0);
        assert!(worker.book.as_ref().unwrap().installed.is_empty());
        assert!(worker.book.as_ref().unwrap().intents.is_empty());
    }

    #[test]
    fn driver_epoch_binding_interrupts_hash_and_write_even_if_driver_switches_back() {
        use std::cell::Cell;
        let env = Env::new();
        let server = Server::new();
        let worker = env.worker(&server);
        let bound = worker.proceed_for_driver(env.hub.driver_epoch());
        let checks = Cell::new(0);
        let guarded = || {
            checks.set(checks.get() + 1);
            if checks.get() == 2 {
                let original = env.hub.status();
                let mut changed = original.clone();
                changed.driver = Some(json!({"customer_id":999}));
                env.hub.set_status(changed);
                env.hub.set_status(original);
            }
            bound()
        };
        assert!(hash_bytes(&vec![3u8; 100_000], &guarded).is_err());
        let bound = worker.proceed_for_driver(env.hub.driver_epoch());
        struct SwitchWriter<'a> {
            out: &'a mut File,
            hub: Arc<Hub>,
        }
        impl Write for SwitchWriter<'_> {
            fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
                let n = self.out.write(bytes)?;
                let mut status = self.hub.status();
                status.driver = Some(json!({"customer_id":999}));
                self.hub.set_status(status);
                Ok(n)
            }
            fn flush(&mut self) -> std::io::Result<()> {
                self.out.flush()
            }
        }
        let dir = Dir::open(
            &env.paths.setups_dir().join("car/Coach Synced"),
            true,
            &|| true,
        )
        .unwrap();
        let mut temp = dir.temp().unwrap();
        assert!(
            write_gated(
                &mut SwitchWriter {
                    out: temp.file(),
                    hub: env.hub.clone()
                },
                &vec![9u8; 100_000],
                &bound
            )
            .is_err()
        );
        assert_eq!(temp.file().metadata().unwrap().len(), 32 * 1024);
        drop(temp);
        drop(dir);
        assert_eq!(
            fs::read_dir(env.paths.setups_dir().join("car/Coach Synced"))
                .unwrap()
                .count(),
            0
        );
    }

    #[test]
    fn interrupted_install_intent_never_recreates_an_absent_revision_or_claims_it_installed() {
        let env = Env::new();
        let server = Server::new();
        let file = item("coach:41", b"prepared", 1);
        let parsed: SyncFile = serde_json::from_value(file.clone()).unwrap();
        let mut book = Book::default();
        book.intents.insert(
            parsed.relative_path.to_lowercase(),
            receipt_for(&parsed, CUSTOMER),
        );
        save_book(&env.paths.state_dir, &book, &|| true).unwrap();
        server.page(MANIFEST, vec![file.clone()], None);
        server.set(route(&file), 200, b"prepared".to_vec());
        let mut worker = env.worker(&server);
        worker.step();
        assert!(!dest(&env, &file).exists());
        assert_eq!(server.count(route(&file)), 0);
        assert_eq!(worker.installed_this_run, 0);
        assert!(
            env.hub.sync_info()["last_error"]
                .as_str()
                .unwrap()
                .contains("unresolved installation intent")
        );
        let saved = load_book(&env.paths.state_dir, &|| true).unwrap();
        assert!(saved.installed.is_empty());
        assert_eq!(saved.intents.len(), 1);
    }

    #[test]
    fn published_intent_recovers_only_after_verifying_the_existing_copy() {
        let env = Env::new();
        let server = Server::new();
        let file = item("coach:42", b"published", 1);
        let parsed: SyncFile = serde_json::from_value(file.clone()).unwrap();
        let mut book = Book::default();
        book.intents.insert(
            parsed.relative_path.to_lowercase(),
            receipt_for(&parsed, CUSTOMER),
        );
        save_book(&env.paths.state_dir, &book, &|| true).unwrap();
        book.intents
            .get_mut(&parsed.relative_path.to_lowercase())
            .unwrap()
            .extra
            .insert("future-receipt".into(), json!({"preserve":true}));
        save_book(&env.paths.state_dir, &book, &|| true).unwrap();
        let path = dest(&env, &file);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, b"published").unwrap();
        server.page(MANIFEST, vec![file.clone()], None);
        let mut worker = env.worker(&server);
        worker.step();
        let saved = load_book(&env.paths.state_dir, &|| true).unwrap();
        assert!(saved.intents.is_empty());
        assert_eq!(saved.installed.len(), 1);
        assert_eq!(
            saved.installed[&parsed.relative_path.to_lowercase()].extra["future-receipt"]["preserve"],
            true
        );
        assert_eq!(server.count(route(&file)), 0);
        assert_eq!(env.hub.sync_info()["state"], "ok");
    }

    #[test]
    fn failed_receipt_save_does_not_acknowledge_intent_or_install_in_memory() {
        let env = Env::new();
        let server = Server::new();
        fs::write(&env.paths.state_dir, b"not a directory").unwrap();
        let file: SyncFile = serde_json::from_value(item("coach:43", b"bytes", 1)).unwrap();
        let mut worker = env.worker(&server);
        worker.book = Some(Book::default());
        assert!(worker.prepare(&file, CUSTOMER, &|| true).is_err());
        assert!(worker.book.as_ref().unwrap().intents.is_empty());
        assert!(worker.remember(&file, CUSTOMER, &|| true).is_err());
        assert!(worker.book.as_ref().unwrap().installed.is_empty());
        assert_eq!(fs::read(&env.paths.state_dir).unwrap(), b"not a directory");
    }

    #[test]
    fn devices_and_windows_path_edge_cases_are_rejected() {
        for component in [
            "CON",
            "con.sto",
            "AUX .sto",
            "COM1",
            "COM¹.txt",
            "LPT³",
            "NUL",
            "CONIN$",
            "a:",
            "a ",
            "..",
            "a/b",
            "a\\b",
            "a\0b",
        ] {
            assert!(!safe_component(component), "{component:?}");
        }
        let root = Path::new("C:/iRacing/setups");
        let file = item("coach:8", b"good", 1);
        validate_file(
            &serde_json::from_value::<SyncFile>(file.clone()).unwrap(),
            CUSTOMER,
            root,
        )
        .unwrap();
        let long_root = std::path::PathBuf::from(format!("C:/{}", "x".repeat(230)));
        assert!(
            validate_file(&serde_json::from_value(file).unwrap(), CUSTOMER, &long_root).is_err()
        );
    }
    #[cfg(unix)]
    #[test]
    fn symlink_ancestor_cannot_redirect_download_or_writes() {
        let env = Env::new();
        let server = Server::new();
        let file = item("coach:9", b"good", 1);
        let outside = env.root.join("outside");
        fs::create_dir(&outside).unwrap();
        std::os::unix::fs::symlink(&outside, env.paths.setups_dir().join("car")).unwrap();
        server.page(MANIFEST, vec![file.clone()], None);
        server.set(route(&file), 200, b"good".to_vec());
        let mut worker = env.worker(&server);
        worker.step();
        assert_eq!(server.count(route(&file)), 0);
        assert_eq!(fs::read_dir(outside).unwrap().count(), 0);
        assert_eq!(env.hub.sync_info()["state"], "error");
    }
}
