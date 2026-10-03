//! A minimal blocking HTTP/1.1 client over `std::net` for the upload API (plain http, one request
//! per connection). Written by hand because the uploader needs exact control: a body that is read
//! from a gate-checked source in small pieces so an in-flight chunk can be abandoned within 100 ms,
//! and a known `Content-Length` (no chunked encoding) for the server's `Content-Range` handling.

use std::io::{self, BufRead, BufReader, Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::time::Duration;

/// Largest response body accepted (the API answers with small JSON).
const MAX_BODY: usize = 1 << 20;
const MAX_HEADER_BYTES: usize = 64 * 1024;
/// Request bodies are written in pieces of this size; the source is polled between pieces.
pub const WRITE_PIECE: usize = 32 * 1024;

#[derive(Debug, thiserror::Error)]
pub enum HttpError {
    #[error("connect to {authority} failed: {source}")]
    Connect {
        authority: String,
        source: io::Error,
    },
    #[error("I/O: {0}")]
    Io(#[from] io::Error),
    /// The body source failed (for the uploader: the on-track gate closed).
    #[error("request body source failed: {0}")]
    Body(io::Error),
    #[error("malformed response: {0}")]
    Malformed(String),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Response {
    pub status: u16,
    pub headers: Vec<(String, String)>,
    pub body: Vec<u8>,
}

impl Response {
    pub fn header(&self, name: &str) -> Option<&str> {
        self.headers
            .iter()
            .find(|(k, _)| k.eq_ignore_ascii_case(name))
            .map(|(_, v)| v.as_str())
    }

    pub fn json(&self) -> Option<serde_json::Value> {
        serde_json::from_slice(&self.body).ok()
    }
}

pub enum Body<'a> {
    None,
    Bytes(&'a [u8]),
    /// `len` bytes read from `reader` in pieces; an error from the reader abandons the request.
    Stream {
        len: u64,
        reader: &'a mut dyn Read,
    },
}

#[derive(Debug, Clone)]
pub struct HttpClient {
    authority: String,
    connect_timeout: Duration,
    io_timeout: Duration,
}

impl HttpClient {
    /// `authority` is `host:port`.
    pub fn new(authority: &str) -> HttpClient {
        HttpClient {
            authority: authority.to_string(),
            connect_timeout: Duration::from_secs(5),
            io_timeout: Duration::from_secs(120),
        }
    }

    pub fn with_timeouts(mut self, connect: Duration, io: Duration) -> HttpClient {
        self.connect_timeout = connect;
        self.io_timeout = io;
        self
    }

    pub fn authority(&self) -> &str {
        &self.authority
    }

    pub fn request(
        &self,
        method: &str,
        path: &str,
        headers: &[(&str, String)],
        mut body: Body<'_>,
    ) -> Result<Response, HttpError> {
        let mut stream = self.connect()?;
        let len = match &body {
            Body::None => 0,
            Body::Bytes(b) => b.len() as u64,
            Body::Stream { len, .. } => *len,
        };
        let mut head = format!(
            "{method} {path} HTTP/1.1\r\nHost: {}\r\nConnection: close\r\n",
            self.authority
        );
        for (k, v) in headers {
            head.push_str(&format!("{k}: {v}\r\n"));
        }
        if len > 0 || matches!(method, "POST" | "PUT") {
            head.push_str(&format!("Content-Length: {len}\r\n"));
        }
        head.push_str("\r\n");
        stream.write_all(head.as_bytes())?;
        match &mut body {
            Body::None => {}
            Body::Bytes(bytes) => stream.write_all(bytes)?,
            Body::Stream { len, reader } => {
                let mut remaining = *len;
                let mut piece = vec![0u8; WRITE_PIECE];
                while remaining > 0 {
                    let want = remaining.min(WRITE_PIECE as u64) as usize;
                    let n = match reader.read(&mut piece[..want]) {
                        Ok(0) => {
                            let _ = stream.shutdown(std::net::Shutdown::Both);
                            return Err(HttpError::Body(io::Error::new(
                                io::ErrorKind::UnexpectedEof,
                                "body source ended early",
                            )));
                        }
                        Ok(n) => n,
                        Err(e) => {
                            let _ = stream.shutdown(std::net::Shutdown::Both);
                            return Err(HttpError::Body(e));
                        }
                    };
                    stream.write_all(&piece[..n])?;
                    remaining -= n as u64;
                }
            }
        }
        stream.flush()?;
        read_response(stream, method == "HEAD")
    }

    fn connect(&self) -> Result<TcpStream, HttpError> {
        let connect_err = |source: io::Error| HttpError::Connect {
            authority: self.authority.clone(),
            source,
        };
        let addrs = self
            .authority
            .to_socket_addrs()
            .map_err(connect_err)?
            .collect::<Vec<_>>();
        let mut last = io::Error::new(io::ErrorKind::NotFound, "no address");
        for addr in addrs {
            match TcpStream::connect_timeout(&addr, self.connect_timeout) {
                Ok(stream) => {
                    stream.set_nodelay(true).ok();
                    bound_send_buffer(&stream);
                    stream.set_read_timeout(Some(self.io_timeout))?;
                    stream.set_write_timeout(Some(self.io_timeout))?;
                    return Ok(stream);
                }
                Err(e) => last = e,
            }
        }
        Err(connect_err(last))
    }
}

/// Keeps the kernel from swallowing a whole chunk: with a small send buffer the writer blocks as
/// soon as the network is the bottleneck, so a chunk abandoned on the gate stops within a few tens
/// of kilobytes instead of after the several megabytes an autotuned buffer can hold.
#[cfg(windows)]
fn bound_send_buffer(stream: &TcpStream) {
    use std::os::windows::io::AsRawSocket;
    use windows::Win32::Networking::WinSock::{SO_SNDBUF, SOCKET, SOL_SOCKET, setsockopt};
    let size = (128 * 1024i32).to_ne_bytes();
    // SAFETY: the socket is open for the duration of the call and `size` is a valid option value.
    unsafe {
        setsockopt(
            SOCKET(stream.as_raw_socket() as usize),
            SOL_SOCKET,
            SO_SNDBUF,
            Some(&size),
        );
    }
}

#[cfg(not(windows))]
fn bound_send_buffer(_: &TcpStream) {}

fn read_response(stream: TcpStream, head_only: bool) -> Result<Response, HttpError> {
    let mut reader = BufReader::new(stream);
    let mut line = String::new();
    reader.read_line(&mut line)?;
    let mut parts = line.trim_end().splitn(3, ' ');
    let version = parts.next().unwrap_or_default();
    if !version.starts_with("HTTP/1.") {
        return Err(HttpError::Malformed(format!("status line {line:?}")));
    }
    let status: u16 = parts
        .next()
        .and_then(|s| s.parse().ok())
        .ok_or_else(|| HttpError::Malformed(format!("status line {line:?}")))?;
    let mut headers = Vec::new();
    let mut header_bytes = 0;
    loop {
        line.clear();
        let n = reader.read_line(&mut line)?;
        header_bytes += n;
        if n == 0 || header_bytes > MAX_HEADER_BYTES {
            return Err(HttpError::Malformed("headers too long or cut off".into()));
        }
        let trimmed = line.trim_end();
        if trimmed.is_empty() {
            break;
        }
        if let Some((k, v)) = trimmed.split_once(':') {
            headers.push((k.trim().to_string(), v.trim().to_string()));
        }
    }
    let mut response = Response {
        status,
        headers,
        body: Vec::new(),
    };
    if head_only || status == 204 || status == 304 || (100..200).contains(&status) {
        return Ok(response);
    }
    let chunked = response
        .header("transfer-encoding")
        .is_some_and(|v| v.eq_ignore_ascii_case("chunked"));
    if chunked {
        response.body = read_chunked(&mut reader)?;
    } else if let Some(n) = response
        .header("content-length")
        .and_then(|v| v.parse::<usize>().ok())
    {
        if n > MAX_BODY {
            return Err(HttpError::Malformed(format!("body of {n} bytes")));
        }
        let mut body = vec![0u8; n];
        reader.read_exact(&mut body)?;
        response.body = body;
    } else {
        let mut body = Vec::new();
        reader.take(MAX_BODY as u64 + 1).read_to_end(&mut body)?;
        if body.len() > MAX_BODY {
            return Err(HttpError::Malformed("body too long".into()));
        }
        response.body = body;
    }
    Ok(response)
}

fn read_chunked(reader: &mut impl BufRead) -> Result<Vec<u8>, HttpError> {
    let mut body = Vec::new();
    loop {
        let mut line = String::new();
        reader.read_line(&mut line)?;
        let size = usize::from_str_radix(line.trim().split(';').next().unwrap_or(""), 16)
            .map_err(|_| HttpError::Malformed(format!("chunk size {line:?}")))?;
        if size == 0 {
            // Trailers up to the blank line.
            loop {
                line.clear();
                if reader.read_line(&mut line)? == 0 || line.trim().is_empty() {
                    return Ok(body);
                }
            }
        }
        if body.len() + size > MAX_BODY {
            return Err(HttpError::Malformed("body too long".into()));
        }
        let start = body.len();
        body.resize(start + size, 0);
        reader.read_exact(&mut body[start..])?;
        let mut crlf = [0u8; 2];
        reader.read_exact(&mut crlf)?;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;
    use std::thread;

    fn serve_once(reply: &'static [u8]) -> (String, thread::JoinHandle<Vec<u8>>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap().to_string();
        let handle = thread::spawn(move || {
            let (mut s, _) = listener.accept().unwrap();
            s.set_read_timeout(Some(Duration::from_millis(300)))
                .unwrap();
            let mut got = Vec::new();
            let mut buf = [0u8; 4096];
            while let Ok(n) = s.read(&mut buf) {
                if n == 0 {
                    break;
                }
                got.extend_from_slice(&buf[..n]);
            }
            s.write_all(reply).unwrap();
            got
        });
        (addr, handle)
    }

    #[test]
    fn posts_json_and_reads_a_content_length_body() {
        let (addr, h) =
            serve_once(b"HTTP/1.1 201 Created\r\nContent-Length: 2\r\nX-A: b\r\n\r\n{}");
        let r = HttpClient::new(&addr)
            .request(
                "POST",
                "/api/v1/uploads",
                &[("Content-Type", "application/json".into())],
                Body::Bytes(b"{\"a\":1}"),
            )
            .unwrap();
        assert_eq!((r.status, r.body.as_slice()), (201, &b"{}"[..]));
        assert_eq!(r.header("x-a"), Some("b"));
        let sent = String::from_utf8(h.join().unwrap()).unwrap();
        assert!(sent.starts_with("POST /api/v1/uploads HTTP/1.1\r\n"));
        assert!(sent.contains("Content-Length: 7\r\n") && sent.ends_with("{\"a\":1}"));
    }

    #[test]
    fn reads_chunked_and_head_responses() {
        let (addr, _h) = serve_once(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n2\r\nde\r\n0\r\n\r\n");
        let r = HttpClient::new(&addr)
            .request("GET", "/", &[], Body::None)
            .unwrap();
        assert_eq!(r.body, b"abcde");
        let (addr, _h) =
            serve_once(b"HTTP/1.1 200 OK\r\nUpload-Offset: 12\r\nContent-Length: 99\r\n\r\n");
        let r = HttpClient::new(&addr)
            .request("HEAD", "/x", &[], Body::None)
            .unwrap();
        assert_eq!(r.header("Upload-Offset"), Some("12"));
        assert!(r.body.is_empty());
    }

    #[test]
    fn a_failing_body_source_abandons_the_request() {
        struct Failing(usize);
        impl Read for Failing {
            fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
                if self.0 == 0 {
                    return Err(io::Error::other("gate closed"));
                }
                self.0 -= 1;
                buf.fill(7);
                Ok(buf.len())
            }
        }
        let (addr, _h) = serve_once(b"");
        let mut src = Failing(2);
        let err = HttpClient::new(&addr)
            .request(
                "PUT",
                "/x",
                &[],
                Body::Stream {
                    len: 1 << 20,
                    reader: &mut src,
                },
            )
            .unwrap_err();
        assert!(matches!(err, HttpError::Body(_)));
    }

    #[test]
    fn connection_refused_is_a_connect_error() {
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = l.local_addr().unwrap().to_string();
        drop(l);
        assert!(matches!(
            HttpClient::new(&addr).request("GET", "/", &[], Body::None),
            Err(HttpError::Connect { .. })
        ));
    }
}
