//! A minimal blocking WebSocket client (RFC 6455) over `std::net`: the opening handshake with one
//! subprotocol, masked text and binary frames out, text, binary, ping and close in. Written by
//! hand because the collector's whole use of the protocol fits in a few hundred lines and a general
//! library costs a quarter of a megabyte in a binary that is held to 3 MB. Plain `ws://` only.
//!
//! Reading honours the socket's read timeout: `read` returns `ReadError::Timeout` when no complete
//! message arrived, and keeps the partial bytes for the next call.

use std::io::{self, Read, Write};
use std::net::TcpStream;

use crate::util::random_u64;

const GUID: &str = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";
/// Largest message accepted from the server (control and layout messages are tiny).
const MAX_MESSAGE: usize = 16 * 1024 * 1024;

const OP_CONT: u8 = 0x0;
const OP_TEXT: u8 = 0x1;
const OP_BINARY: u8 = 0x2;
const OP_CLOSE: u8 = 0x8;
const OP_PING: u8 = 0x9;
const OP_PONG: u8 = 0xA;

#[derive(Debug)]
pub enum Incoming {
    Text(String),
    Binary(Vec<u8>),
    /// The peer's close code, when it sent one.
    Close(Option<u16>),
}

#[derive(Debug)]
pub enum ReadError {
    /// No complete message within the read timeout; try again.
    Timeout,
    /// The connection ended without a close frame.
    Closed,
    Protocol(String),
    Io(io::Error),
}

impl std::fmt::Display for ReadError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ReadError::Timeout => write!(f, "timed out"),
            ReadError::Closed => write!(f, "connection closed"),
            ReadError::Protocol(m) => write!(f, "protocol error: {m}"),
            ReadError::Io(e) => write!(f, "{e}"),
        }
    }
}

pub struct WebSocket {
    stream: TcpStream,
    /// Bytes read but not yet parsed.
    inbox: Vec<u8>,
    /// Frames queued by `send_*`, written by `flush`.
    outbox: Vec<u8>,
    /// An unfinished fragmented message.
    fragments: Option<(u8, Vec<u8>)>,
}

impl WebSocket {
    /// Performs the opening handshake over a connected stream. `host` is the `Host` header value,
    /// `path` the request target, `subprotocol` the one protocol offered and required back.
    pub fn handshake(
        mut stream: TcpStream,
        host: &str,
        path: &str,
        subprotocol: &str,
    ) -> Result<WebSocket, String> {
        let mut key_bytes = [0u8; 16];
        key_bytes[..8].copy_from_slice(&random_u64().to_le_bytes());
        key_bytes[8..].copy_from_slice(&random_u64().to_le_bytes());
        let key = base64(&key_bytes);
        let request = format!(
            "GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\
             Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: {subprotocol}\r\n\r\n"
        );
        stream
            .write_all(request.as_bytes())
            .map_err(|e| format!("handshake write: {e}"))?;

        let mut head = Vec::new();
        let mut chunk = [0u8; 1024];
        let end = loop {
            if let Some(p) = find(&head, b"\r\n\r\n") {
                break p + 4;
            }
            if head.len() > 16 * 1024 {
                return Err("handshake response too long".into());
            }
            let n = stream
                .read(&mut chunk)
                .map_err(|e| format!("handshake read: {e}"))?;
            if n == 0 {
                return Err("connection closed during the handshake".into());
            }
            head.extend_from_slice(&chunk[..n]);
        };
        let text = String::from_utf8_lossy(&head[..end]).into_owned();
        let mut lines = text.split("\r\n");
        let status = lines.next().unwrap_or_default();
        if !status.starts_with("HTTP/1.1 101") {
            return Err(format!("server refused the upgrade: {status}"));
        }
        let header = |name: &str| {
            text.split("\r\n").skip(1).find_map(|l| {
                let (k, v) = l.split_once(':')?;
                k.trim()
                    .eq_ignore_ascii_case(name)
                    .then(|| v.trim().to_string())
            })
        };
        let expected = base64(&sha1(format!("{key}{GUID}").as_bytes()));
        if header("sec-websocket-accept").as_deref() != Some(expected.as_str()) {
            return Err("bad Sec-WebSocket-Accept".into());
        }
        if header("sec-websocket-protocol").as_deref() != Some(subprotocol) {
            return Err("the server did not accept the subprotocol".into());
        }
        Ok(WebSocket {
            stream,
            inbox: head[end..].to_vec(),
            outbox: Vec::new(),
            fragments: None,
        })
    }

    pub fn stream(&self) -> &TcpStream {
        &self.stream
    }

    /// Queues a masked text frame.
    pub fn queue_text(&mut self, text: &str) {
        self.queue(OP_TEXT, text.as_bytes());
    }

    /// Queues a masked binary frame.
    pub fn queue_binary(&mut self, data: &[u8]) {
        self.queue(OP_BINARY, data);
    }

    /// Queues a close frame with `code`.
    pub fn queue_close(&mut self, code: u16) {
        self.queue(OP_CLOSE, &code.to_be_bytes());
    }

    fn queue(&mut self, opcode: u8, payload: &[u8]) {
        let mask = (random_u64() as u32).to_le_bytes();
        let out = &mut self.outbox;
        out.push(0x80 | opcode);
        match payload.len() {
            n if n < 126 => out.push(0x80 | n as u8),
            n if n <= u16::MAX as usize => {
                out.push(0x80 | 126);
                out.extend_from_slice(&(n as u16).to_be_bytes());
            }
            n => {
                out.push(0x80 | 127);
                out.extend_from_slice(&(n as u64).to_be_bytes());
            }
        }
        out.extend_from_slice(&mask);
        out.extend(payload.iter().enumerate().map(|(i, b)| b ^ mask[i % 4]));
    }

    /// Writes everything queued.
    pub fn flush(&mut self) -> io::Result<()> {
        if self.outbox.is_empty() {
            return Ok(());
        }
        let result = self
            .stream
            .write_all(&self.outbox)
            .and_then(|()| self.stream.flush());
        self.outbox.clear();
        result
    }

    /// Queues and writes one text frame.
    pub fn send_text(&mut self, text: &str) -> io::Result<()> {
        self.queue_text(text);
        self.flush()
    }

    /// The next complete data or close message. Pings are answered here; pongs are dropped.
    pub fn read(&mut self) -> Result<Incoming, ReadError> {
        loop {
            match self.parse_frame()? {
                Some(Frame {
                    opcode,
                    fin,
                    payload,
                }) => {
                    if let Some(incoming) = self.on_frame(opcode, fin, payload)? {
                        return Ok(incoming);
                    }
                }
                None => self.fill()?,
            }
        }
    }

    fn on_frame(
        &mut self,
        opcode: u8,
        fin: bool,
        payload: Vec<u8>,
    ) -> Result<Option<Incoming>, ReadError> {
        match opcode {
            OP_PING => {
                self.queue(OP_PONG, &payload);
                self.flush().map_err(ReadError::Io)?;
                Ok(None)
            }
            OP_PONG => Ok(None),
            OP_CLOSE => {
                let code =
                    (payload.len() >= 2).then(|| u16::from_be_bytes([payload[0], payload[1]]));
                // Echo the close so the peer can finish; failure to write is of no interest now.
                if let Some(c) = code {
                    self.queue_close(c);
                } else {
                    self.queue(OP_CLOSE, &[]);
                }
                let _ = self.flush();
                Ok(Some(Incoming::Close(code)))
            }
            OP_TEXT | OP_BINARY => {
                if self.fragments.is_some() {
                    return Err(ReadError::Protocol(
                        "new message inside a fragmented one".into(),
                    ));
                }
                if fin {
                    self.finish(opcode, payload).map(Some)
                } else {
                    self.fragments = Some((opcode, payload));
                    Ok(None)
                }
            }
            OP_CONT => {
                let Some((first, mut data)) = self.fragments.take() else {
                    return Err(ReadError::Protocol("continuation without a message".into()));
                };
                data.extend_from_slice(&payload);
                if data.len() > MAX_MESSAGE {
                    return Err(ReadError::Protocol("message too large".into()));
                }
                if fin {
                    self.finish(first, data).map(Some)
                } else {
                    self.fragments = Some((first, data));
                    Ok(None)
                }
            }
            other => Err(ReadError::Protocol(format!("unknown opcode {other}"))),
        }
    }

    fn finish(&mut self, opcode: u8, data: Vec<u8>) -> Result<Incoming, ReadError> {
        if opcode == OP_TEXT {
            String::from_utf8(data)
                .map(Incoming::Text)
                .map_err(|_| ReadError::Protocol("text frame is not UTF-8".into()))
        } else {
            Ok(Incoming::Binary(data))
        }
    }

    /// One frame from the front of `inbox`, if it is complete.
    fn parse_frame(&mut self) -> Result<Option<Frame>, ReadError> {
        let b = &self.inbox;
        if b.len() < 2 {
            return Ok(None);
        }
        let (fin, opcode) = (b[0] & 0x80 != 0, b[0] & 0x0F);
        if b[0] & 0x70 != 0 {
            return Err(ReadError::Protocol("reserved bits set".into()));
        }
        let masked = b[1] & 0x80 != 0;
        let (len, mut at) = match b[1] & 0x7F {
            126 if b.len() >= 4 => (usize::from(u16::from_be_bytes([b[2], b[3]])), 4),
            127 if b.len() >= 10 => (
                usize::try_from(u64::from_be_bytes(b[2..10].try_into().unwrap()))
                    .map_err(|_| ReadError::Protocol("frame too large".into()))?,
                10,
            ),
            126 | 127 => return Ok(None),
            n => (usize::from(n), 2),
        };
        if len > MAX_MESSAGE {
            return Err(ReadError::Protocol("frame too large".into()));
        }
        let mask = if masked {
            if b.len() < at + 4 {
                return Ok(None);
            }
            let m = [b[at], b[at + 1], b[at + 2], b[at + 3]];
            at += 4;
            Some(m)
        } else {
            None
        };
        if b.len() < at + len {
            return Ok(None);
        }
        let mut payload = b[at..at + len].to_vec();
        if let Some(m) = mask {
            for (i, byte) in payload.iter_mut().enumerate() {
                *byte ^= m[i % 4];
            }
        }
        if opcode >= 0x8 && (!fin || len > 125) {
            return Err(ReadError::Protocol("bad control frame".into()));
        }
        self.inbox.drain(..at + len);
        Ok(Some(Frame {
            opcode,
            fin,
            payload,
        }))
    }

    fn fill(&mut self) -> Result<(), ReadError> {
        let mut chunk = [0u8; 8192];
        match self.stream.read(&mut chunk) {
            Ok(0) => Err(ReadError::Closed),
            Ok(n) => {
                self.inbox.extend_from_slice(&chunk[..n]);
                Ok(())
            }
            Err(e)
                if matches!(
                    e.kind(),
                    io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut
                ) =>
            {
                Err(ReadError::Timeout)
            }
            Err(e) => Err(ReadError::Io(e)),
        }
    }
}

struct Frame {
    opcode: u8,
    fin: bool,
    payload: Vec<u8>,
}

fn find(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack.windows(needle.len()).position(|w| w == needle)
}

/// Standard base64 with padding.
pub fn base64(data: &[u8]) -> String {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(data.len().div_ceil(3) * 4);
    for chunk in data.chunks(3) {
        let n = (u32::from(chunk[0]) << 16)
            | (u32::from(*chunk.get(1).unwrap_or(&0)) << 8)
            | u32::from(*chunk.get(2).unwrap_or(&0));
        out.push(ALPHABET[(n >> 18) as usize & 63] as char);
        out.push(ALPHABET[(n >> 12) as usize & 63] as char);
        out.push(if chunk.len() > 1 {
            ALPHABET[(n >> 6) as usize & 63] as char
        } else {
            '='
        });
        out.push(if chunk.len() > 2 {
            ALPHABET[n as usize & 63] as char
        } else {
            '='
        });
    }
    out
}

/// SHA-1 (RFC 3174), for the handshake's accept token only.
pub fn sha1(data: &[u8]) -> [u8; 20] {
    let mut h: [u32; 5] = [
        0x6745_2301,
        0xEFCD_AB89,
        0x98BA_DCFE,
        0x1032_5476,
        0xC3D2_E1F0,
    ];
    let mut message = data.to_vec();
    message.push(0x80);
    while message.len() % 64 != 56 {
        message.push(0);
    }
    message.extend_from_slice(&((data.len() as u64) * 8).to_be_bytes());
    let (blocks, _) = message.as_chunks::<64>();
    for block in blocks {
        let mut w = [0u32; 80];
        let (words, _) = block.as_chunks::<4>();
        for (i, word) in words.iter().enumerate() {
            w[i] = u32::from_be_bytes(*word);
        }
        for i in 16..80 {
            w[i] = (w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16]).rotate_left(1);
        }
        let [mut a, mut b, mut c, mut d, mut e] = h;
        for (i, wi) in w.iter().enumerate() {
            let (f, k) = match i {
                0..=19 => ((b & c) | (!b & d), 0x5A82_7999),
                20..=39 => (b ^ c ^ d, 0x6ED9_EBA1),
                40..=59 => ((b & c) | (b & d) | (c & d), 0x8F1B_BCDC),
                _ => (b ^ c ^ d, 0xCA62_C1D6u32),
            };
            let t = a
                .rotate_left(5)
                .wrapping_add(f)
                .wrapping_add(e)
                .wrapping_add(k)
                .wrapping_add(*wi);
            e = d;
            d = c;
            c = b.rotate_left(30);
            b = a;
            a = t;
        }
        for (x, y) in h.iter_mut().zip([a, b, c, d, e]) {
            *x = x.wrapping_add(y);
        }
    }
    let mut out = [0u8; 20];
    for (i, word) in h.iter().enumerate() {
        out[i * 4..i * 4 + 4].copy_from_slice(&word.to_be_bytes());
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::util::hex;

    #[test]
    fn sha1_matches_the_standard_vectors() {
        assert_eq!(
            hex(&sha1(b"abc")),
            "a9993e364706816aba3e25717850c26c9cd0d89d"
        );
        assert_eq!(hex(&sha1(b"")), "da39a3ee5e6b4b0d3255bfef95601890afd80709");
        assert_eq!(
            hex(&sha1(
                b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"
            )),
            "84983e441c3bd26ebaae4aa1f95129e5e54670f1"
        );
    }

    #[test]
    fn the_rfc_6455_accept_token_is_reproduced() {
        let accept = base64(&sha1(
            b"dGhlIHNhbXBsZSBub25jZQ==258EAFA5-E914-47DA-95CA-C5AB0DC85B11",
        ));
        assert_eq!(accept, "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=");
    }

    #[test]
    fn base64_pads_correctly() {
        assert_eq!(base64(b""), "");
        assert_eq!(base64(b"f"), "Zg==");
        assert_eq!(base64(b"fo"), "Zm8=");
        assert_eq!(base64(b"foo"), "Zm9v");
        assert_eq!(base64(b"foobar"), "Zm9vYmFy");
    }

    /// A socket pair over loopback with a stub "server" that speaks raw frames.
    fn pair() -> (WebSocket, TcpStream) {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let (mut s, _) = listener.accept().unwrap();
            let mut head = Vec::new();
            let mut b = [0u8; 1];
            while !head.ends_with(b"\r\n\r\n") {
                s.read_exact(&mut b).unwrap();
                head.push(b[0]);
            }
            let text = String::from_utf8(head).unwrap();
            let key = text
                .lines()
                .find_map(|l| l.strip_prefix("Sec-WebSocket-Key: "))
                .unwrap()
                .to_string();
            let accept = base64(&sha1(format!("{key}{GUID}").as_bytes()));
            write!(
                s,
                "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\nSec-WebSocket-Protocol: coach-collector.1\r\n\r\n"
            )
            .unwrap();
            s
        });
        let client = TcpStream::connect(addr).unwrap();
        let ws = WebSocket::handshake(client, "x", "/ws/collector", "coach-collector.1").unwrap();
        (ws, server.join().unwrap())
    }

    fn server_frame(opcode: u8, fin: bool, payload: &[u8]) -> Vec<u8> {
        let mut f = vec![if fin { 0x80 } else { 0 } | opcode];
        if payload.len() < 126 {
            f.push(payload.len() as u8);
        } else {
            f.push(126);
            f.extend_from_slice(&(payload.len() as u16).to_be_bytes());
        }
        f.extend_from_slice(payload);
        f
    }

    #[test]
    fn handshake_then_masked_frames_out_and_messages_in() {
        let (mut ws, mut server) = pair();
        ws.send_text("hello").unwrap();
        ws.queue_binary(&[1, 2, 3]);
        ws.flush().unwrap();
        // The server sees masked frames (mask bit set on both).
        let mut got = [0u8; 2];
        server.read_exact(&mut got).unwrap();
        assert_eq!(got[0], 0x81);
        assert_eq!(got[1], 0x80 | 5);
        let mut rest = [0u8; 4 + 5 + 2 + 4 + 3];
        server.read_exact(&mut rest).unwrap();
        let mask = [rest[0], rest[1], rest[2], rest[3]];
        let text: Vec<u8> = rest[4..9]
            .iter()
            .enumerate()
            .map(|(i, b)| b ^ mask[i % 4])
            .collect();
        assert_eq!(text, b"hello");

        // In: a split text message with a ping in the middle, then a binary frame and a close.
        ws.stream()
            .set_read_timeout(Some(std::time::Duration::from_millis(300)))
            .unwrap();
        server
            .write_all(&server_frame(OP_TEXT, false, b"ab"))
            .unwrap();
        server
            .write_all(&server_frame(OP_PING, true, b"p"))
            .unwrap();
        server
            .write_all(&server_frame(OP_CONT, true, b"cd"))
            .unwrap();
        server
            .write_all(&server_frame(OP_BINARY, true, &[9, 8]))
            .unwrap();
        server
            .write_all(&server_frame(OP_CLOSE, true, &4001u16.to_be_bytes()))
            .unwrap();
        assert!(matches!(ws.read(), Ok(Incoming::Text(t)) if t == "abcd"));
        assert!(matches!(ws.read(), Ok(Incoming::Binary(b)) if b == [9, 8]));
        assert!(matches!(ws.read(), Ok(Incoming::Close(Some(4001)))));
        // The ping was answered with a pong.
        let mut pong = [0u8; 2 + 4 + 1];
        server.read_exact(&mut pong).unwrap();
        assert_eq!(pong[0], 0x8A);
    }

    #[test]
    fn a_partial_frame_waits_for_the_rest_across_timeouts() {
        let (mut ws, mut server) = pair();
        ws.stream()
            .set_read_timeout(Some(std::time::Duration::from_millis(50)))
            .unwrap();
        let frame = server_frame(OP_TEXT, true, &[b'x'; 300]);
        server.write_all(&frame[..100]).unwrap();
        assert!(matches!(ws.read(), Err(ReadError::Timeout)));
        server.write_all(&frame[100..]).unwrap();
        assert!(matches!(ws.read(), Ok(Incoming::Text(t)) if t.len() == 300));
        drop(server);
        assert!(matches!(ws.read(), Err(ReadError::Closed)));
    }

    #[test]
    fn a_refused_upgrade_is_an_error() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        std::thread::spawn(move || {
            let (mut s, _) = listener.accept().unwrap();
            let mut b = [0u8; 512];
            let _ = s.read(&mut b);
            let _ = s.write_all(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n");
        });
        let c = TcpStream::connect(addr).unwrap();
        assert!(WebSocket::handshake(c, "x", "/", "p").is_err());
    }
}
