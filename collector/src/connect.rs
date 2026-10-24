//! Gate-cancellable address resolution and nonblocking TCP connection establishment.
//! DNS uses one bounded global worker; abandoned requests never spawn more resolver threads.
use std::io;
use std::net::{SocketAddr, TcpStream, ToSocketAddrs};
use std::sync::{
    Arc, Mutex, OnceLock,
    atomic::{AtomicBool, Ordering},
    mpsc,
};
use std::time::{Duration, Instant};

const POLL: Duration = Duration::from_millis(2);
const CACHE_TTL: Duration = Duration::from_secs(60);
#[derive(Debug, Default)]
pub(crate) struct Cache {
    value: Mutex<Option<(String, Vec<SocketAddr>, Instant)>>,
}
#[derive(Debug)]
struct Lookup {
    authority: String,
    cancelled: Arc<AtomicBool>,
    reply: mpsc::SyncSender<io::Result<Vec<SocketAddr>>>,
}
struct Resolver {
    tx: mpsc::SyncSender<Lookup>,
}
type LookupFn = Arc<dyn Fn(&str) -> io::Result<Vec<SocketAddr>> + Send + Sync>;
impl Resolver {
    fn start(lookup: LookupFn) -> Self {
        let (tx, rx) = mpsc::sync_channel::<Lookup>(1);
        std::thread::Builder::new()
            .name("coach-dns".into())
            .spawn(move || {
                while let Ok(job) = rx.recv() {
                    if job.cancelled.load(Ordering::SeqCst) {
                        continue;
                    }
                    let result = lookup(&job.authority);
                    let _ = job.reply.send(result);
                }
            })
            .expect("spawn bounded DNS worker");
        Self { tx }
    }
    fn global() -> &'static Self {
        static RESOLVER: OnceLock<Resolver> = OnceLock::new();
        RESOLVER.get_or_init(|| {
            Self::start(Arc::new(|authority| {
                authority
                    .to_socket_addrs()
                    .map(|all| all.take(32).collect())
            }))
        })
    }
    fn resolve(
        &self,
        authority: &str,
        deadline: Instant,
        proceed: &dyn Fn() -> bool,
    ) -> io::Result<Vec<SocketAddr>> {
        check(proceed)?;
        let (tx, rx) = mpsc::sync_channel(1);
        let cancelled = Arc::new(AtomicBool::new(false));
        let _cancel = Cancel(cancelled.clone());
        self.tx
            .try_send(Lookup {
                authority: authority.into(),
                cancelled,
                reply: tx,
            })
            .map_err(|e| io::Error::other(format!("bounded DNS worker unavailable: {e}")))?;
        loop {
            check(proceed)?;
            match rx.try_recv() {
                Ok(result) => return result,
                Err(mpsc::TryRecvError::Disconnected) => {
                    return Err(io::Error::other("DNS worker stopped"));
                }
                Err(mpsc::TryRecvError::Empty) => {}
            }
            if Instant::now() >= deadline {
                return Err(io::ErrorKind::TimedOut.into());
            }
            std::thread::sleep(POLL);
        }
    }
}
struct Cancel(Arc<AtomicBool>);
impl Drop for Cancel {
    fn drop(&mut self) {
        self.0.store(true, Ordering::SeqCst);
    }
}
fn check(proceed: &dyn Fn() -> bool) -> io::Result<()> {
    if proceed() {
        Ok(())
    } else {
        Err(io::Error::other("connection abandoned by gate"))
    }
}
fn addresses(
    authority: &str,
    cache: &Cache,
    deadline: Instant,
    proceed: &dyn Fn() -> bool,
) -> io::Result<Vec<SocketAddr>> {
    check(proceed)?;
    // The normal LAN configuration is a literal IP: no DNS thread or name-service work.
    if let Ok(literal) = authority.parse::<SocketAddr>() {
        return Ok(vec![literal]);
    }
    if let Some((name, addrs, at)) = cache.value.lock().unwrap().as_ref()
        && name == authority
        && at.elapsed() < CACHE_TTL
    {
        return Ok(addrs.clone());
    }
    let resolved = Resolver::global().resolve(authority, deadline, proceed)?;
    check(proceed)?;
    *cache.value.lock().unwrap() = Some((authority.into(), resolved.clone(), Instant::now()));
    Ok(resolved)
}
pub(crate) fn connect(
    authority: &str,
    timeout: Duration,
    proceed: &dyn Fn() -> bool,
    cache: &Cache,
) -> io::Result<TcpStream> {
    let deadline = Instant::now() + timeout;
    let addrs = addresses(authority, cache, deadline, proceed)?;
    let mut last = io::Error::new(io::ErrorKind::NotFound, "no server address");
    for addr in addrs {
        check(proceed)?;
        if Instant::now() >= deadline {
            return Err(io::ErrorKind::TimedOut.into());
        }
        match begin(addr) {
            Ok(stream) => {
                let result = wait_ready(&mut || connected(&stream), deadline, proceed);
                match result {
                    Ok(()) => return Ok(stream),
                    Err(error) => last = error,
                }
            }
            Err(error) => last = error,
        }
        check(proceed)?;
    }
    Err(last)
}
fn wait_ready(
    probe: &mut dyn FnMut() -> io::Result<bool>,
    deadline: Instant,
    proceed: &dyn Fn() -> bool,
) -> io::Result<()> {
    loop {
        check(proceed)?;
        if probe()? {
            check(proceed)?;
            return Ok(());
        }
        if Instant::now() >= deadline {
            return Err(io::ErrorKind::TimedOut.into());
        }
        std::thread::sleep(POLL);
    }
}

#[cfg(windows)]
fn connected(stream: &TcpStream) -> io::Result<bool> {
    use std::os::windows::io::AsRawSocket;
    use windows::Win32::Networking::WinSock::*;
    let mut writable = FD_SET {
        fd_count: 1,
        ..Default::default()
    };
    writable.fd_array[0] = SOCKET(stream.as_raw_socket() as _);
    let mut exceptional = writable;
    let timeout = TIMEVAL {
        tv_sec: 0,
        tv_usec: 0,
    };
    // SAFETY: these sets own valid socket values for this zero-time, nonblocking probe.
    let count = unsafe {
        select(
            0,
            None,
            Some(&mut writable),
            Some(&mut exceptional),
            Some(&timeout),
        )
    };
    if count < 0 {
        // SAFETY: read immediately after this thread's select failure.
        return Err(io::Error::from_raw_os_error(unsafe { WSAGetLastError() }.0));
    }
    if count == 0 {
        return Ok(false);
    }
    if let Some(error) = stream.take_error()? {
        return Err(error);
    }
    if exceptional.fd_count > 0 {
        return Err(io::Error::new(
            io::ErrorKind::ConnectionRefused,
            "connection failed",
        ));
    }
    Ok(writable.fd_count > 0)
}

#[cfg(unix)]
fn connected(stream: &TcpStream) -> io::Result<bool> {
    use std::os::fd::AsRawFd;
    let mut readiness = libc::pollfd {
        fd: stream.as_raw_fd(),
        events: libc::POLLOUT,
        revents: 0,
    };
    // SAFETY: readiness points to one valid descriptor entry and timeout zero never waits.
    let count = unsafe { libc::poll(&mut readiness, 1, 0) };
    if count < 0 {
        return Err(io::Error::last_os_error());
    }
    if count == 0 {
        return Ok(false);
    }
    if let Some(error) = stream.take_error()? {
        return Err(error);
    }
    if readiness.revents & (libc::POLLERR | libc::POLLHUP | libc::POLLNVAL) != 0 {
        return Err(io::Error::new(
            io::ErrorKind::ConnectionRefused,
            "connection failed",
        ));
    }
    Ok(readiness.revents & libc::POLLOUT != 0)
}

#[cfg(windows)]
fn begin(addr: SocketAddr) -> io::Result<TcpStream> {
    use std::os::windows::io::FromRawSocket;
    use windows::Win32::Networking::WinSock::*;
    static START: OnceLock<i32> = OnceLock::new();
    let started = *START.get_or_init(|| {
        let mut data = WSADATA::default();
        // SAFETY: data is writable WSADATA storage, and version 2.2 is requested once.
        unsafe { WSAStartup(0x202, &mut data) }
    });
    if started != 0 {
        return Err(io::Error::from_raw_os_error(started));
    }
    let family = if addr.is_ipv4() { AF_INET } else { AF_INET6 };
    // SAFETY: these are supported TCP socket constants; ownership transfers below.
    let socket = unsafe { socket(i32::from(family.0), SOCK_STREAM, IPPROTO_TCP.0) }
        .map_err(|e| io::Error::from_raw_os_error(e.code().0 & 0xffff))?;
    // SAFETY: socket is newly created, valid and has no other owner.
    let stream = unsafe { TcpStream::from_raw_socket(socket.0 as _) };
    stream.set_nonblocking(true)?;
    let code = match addr {
        SocketAddr::V4(addr) => {
            let native = SOCKADDR_IN {
                sin_family: AF_INET,
                sin_port: addr.port().to_be(),
                sin_addr: IN_ADDR {
                    S_un: IN_ADDR_0 {
                        S_addr: u32::from_ne_bytes(addr.ip().octets()),
                    },
                },
                sin_zero: [0; 8],
            };
            // SAFETY: native is an initialized sockaddr of the supplied length for this call.
            unsafe {
                connect(
                    socket,
                    (&native as *const SOCKADDR_IN).cast(),
                    std::mem::size_of_val(&native) as i32,
                )
            }
        }
        SocketAddr::V6(addr) => {
            let native = SOCKADDR_IN6 {
                sin6_family: AF_INET6,
                sin6_port: addr.port().to_be(),
                sin6_flowinfo: addr.flowinfo().to_be(),
                sin6_addr: IN6_ADDR {
                    u: IN6_ADDR_0 {
                        Byte: addr.ip().octets(),
                    },
                },
                Anonymous: SOCKADDR_IN6_0 {
                    sin6_scope_id: addr.scope_id(),
                },
            };
            // SAFETY: native is an initialized sockaddr of the supplied length for this call.
            unsafe {
                connect(
                    socket,
                    (&native as *const SOCKADDR_IN6).cast(),
                    std::mem::size_of_val(&native) as i32,
                )
            }
        }
    };
    if code != 0 {
        // SAFETY: queried immediately after this thread's failed WinSock connect.
        let error = unsafe { WSAGetLastError() };
        if ![WSAEWOULDBLOCK, WSAEINPROGRESS, WSAEALREADY, WSAEISCONN].contains(&error) {
            return Err(io::Error::from_raw_os_error(error.0));
        }
    }
    Ok(stream)
}
#[cfg(unix)]
fn begin(addr: SocketAddr) -> io::Result<TcpStream> {
    use std::os::fd::{AsRawFd, FromRawFd};
    // SAFETY: valid TCP constants create a new owned nonblocking close-on-exec descriptor.
    let fd = unsafe {
        libc::socket(
            if addr.is_ipv4() {
                libc::AF_INET
            } else {
                libc::AF_INET6
            },
            libc::SOCK_STREAM | libc::SOCK_NONBLOCK | libc::SOCK_CLOEXEC,
            libc::IPPROTO_TCP,
        )
    };
    if fd < 0 {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: fd is newly created and transferred to TcpStream exactly once.
    let stream = unsafe { TcpStream::from_raw_fd(fd) };
    let code = match addr {
        SocketAddr::V4(addr) => {
            let native = libc::sockaddr_in {
                sin_family: libc::AF_INET as _,
                sin_port: addr.port().to_be(),
                sin_addr: libc::in_addr {
                    s_addr: u32::from_ne_bytes(addr.ip().octets()),
                },
                sin_zero: [0; 8],
            };
            // SAFETY: native is an initialized sockaddr of the supplied length for this call.
            unsafe {
                libc::connect(
                    stream.as_raw_fd(),
                    (&native as *const libc::sockaddr_in).cast(),
                    std::mem::size_of_val(&native) as _,
                )
            }
        }
        SocketAddr::V6(addr) => {
            let native = libc::sockaddr_in6 {
                sin6_family: libc::AF_INET6 as _,
                sin6_port: addr.port().to_be(),
                sin6_flowinfo: addr.flowinfo().to_be(),
                sin6_addr: libc::in6_addr {
                    s6_addr: addr.ip().octets(),
                },
                sin6_scope_id: addr.scope_id(),
            };
            // SAFETY: native is an initialized sockaddr of the supplied length for this call.
            unsafe {
                libc::connect(
                    stream.as_raw_fd(),
                    (&native as *const libc::sockaddr_in6).cast(),
                    std::mem::size_of_val(&native) as _,
                )
            }
        }
    };
    if code < 0 {
        let error = io::Error::last_os_error();
        if !matches!(
            error.raw_os_error(),
            Some(libc::EINPROGRESS | libc::EALREADY | libc::EINTR)
        ) {
            return Err(error);
        }
    }
    Ok(stream)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;
    #[test]
    fn blocked_dns_returns_within_100ms_and_queued_jobs_are_bounded() {
        let (release_tx, release_rx) = mpsc::sync_channel(0);
        let rx = Mutex::new(release_rx);
        let entered = Arc::new(AtomicBool::new(false));
        let mark = entered.clone();
        let resolver = Resolver::start(Arc::new(move |_| {
            mark.store(true, Ordering::SeqCst);
            let _ = rx.lock().unwrap().recv();
            Ok(vec![])
        }));
        let allowed = Arc::new(AtomicBool::new(true));
        let gate = allowed.clone();
        let seen = entered.clone();
        let closer = std::thread::spawn(move || {
            while !seen.load(Ordering::SeqCst) {
                std::thread::sleep(POLL);
            }
            gate.store(false, Ordering::SeqCst);
            Instant::now()
        });
        let result = resolver.resolve(
            "blocked.invalid:80",
            Instant::now() + Duration::from_secs(3),
            &|| allowed.load(Ordering::SeqCst),
        );
        let closed = closer.join().unwrap();
        assert!(result.is_err());
        assert!(closed.elapsed() < Duration::from_millis(100));
        // One cancelled DNS lookup may finish, but repeated requests cannot spawn threads or grow the queue.
        let (reply, _) = mpsc::sync_channel(1);
        resolver
            .tx
            .try_send(Lookup {
                authority: "queued".into(),
                cancelled: Arc::new(AtomicBool::new(true)),
                reply,
            })
            .unwrap();
        let (reply, _) = mpsc::sync_channel(1);
        assert!(
            resolver
                .tx
                .try_send(Lookup {
                    authority: "overflow".into(),
                    cancelled: Arc::new(AtomicBool::new(true)),
                    reply
                })
                .is_err()
        );
        release_tx.send(()).unwrap();
    }
    #[test]
    fn pending_connect_probe_is_gate_cancellable_within_100ms() {
        let allowed = Arc::new(AtomicBool::new(true));
        let gate = allowed.clone();
        let closer = std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(10));
            gate.store(false, Ordering::SeqCst);
            Instant::now()
        });
        let result = wait_ready(
            &mut || Ok(false),
            Instant::now() + Duration::from_secs(3),
            &|| allowed.load(Ordering::SeqCst),
        );
        let closed = closer.join().unwrap();
        assert!(result.is_err());
        assert!(closed.elapsed() < Duration::from_millis(100));
    }
    #[test]
    fn native_nonblocking_connections_work_with_ipv4_and_available_ipv6() {
        for bind in ["127.0.0.1:0", "[::1]:0"] {
            let Ok(listener) = TcpListener::bind(bind) else {
                continue;
            };
            let addr = listener.local_addr().unwrap();
            let stream = connect(
                &addr.to_string(),
                Duration::from_secs(1),
                &|| true,
                &Cache::default(),
            )
            .unwrap();
            let (accepted, _) = listener.accept().unwrap();
            assert_eq!(stream.peer_addr().unwrap(), accepted.local_addr().unwrap());
        }
    }
}
