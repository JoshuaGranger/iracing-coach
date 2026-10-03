//! Live stream control (`collector-protocol.md` section 6). Three states, each with one owner:
//! viewer demand (server), the session latch (this module) and the engine epoch (the live engine,
//! never consulted here).

/// Identity of a stream session. It ends when the sim disconnects or any known part changes.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct StreamSessionKey {
    pub subsession_id: Option<i64>,
    pub session_num: Option<i32>,
    pub session_unique_id: Option<i64>,
    pub user_id: Option<i64>,
}

impl StreamSessionKey {
    /// True when some part is known on both sides and differs.
    fn differs_from(&self, other: &StreamSessionKey) -> bool {
        fn changed<T: PartialEq>(a: Option<T>, b: Option<T>) -> bool {
            matches!((a, b), (Some(x), Some(y)) if x != y)
        }
        changed(self.subsession_id, other.subsession_id)
            || changed(self.session_num, other.session_num)
            || changed(self.session_unique_id, other.session_unique_id)
            || changed(self.user_id, other.user_id)
    }

    fn fill_from(&mut self, newer: &StreamSessionKey) {
        self.subsession_id = newer.subsession_id.or(self.subsession_id);
        self.session_num = newer.session_num.or(self.session_num);
        self.session_unique_id = newer.session_unique_id.or(self.session_unique_id);
        self.user_id = newer.user_id.or(self.user_id);
    }
}

#[derive(Debug, Default)]
pub struct StreamControl {
    /// Server side: at least one browser is on the live page for the current driver.
    demand: bool,
    /// Streaming was wanted at some time during the current stream session.
    latch: bool,
    /// The identity of the current stream session while the sim is connected.
    current: Option<StreamSessionKey>,
}

impl StreamControl {
    pub fn new() -> StreamControl {
        StreamControl::default()
    }

    /// `stream.start`: demand and latch both set.
    pub fn on_start(&mut self) {
        self.demand = true;
        self.latch = true;
    }

    /// `stream.release`: demand ends; a set latch keeps streaming until the stream session ends.
    pub fn on_release(&mut self) {
        self.demand = false;
    }

    /// `welcome.stream_wanted` reports viewer demand only: true acts as `stream.start`, false as
    /// `stream.release` (an existing latch is kept).
    pub fn on_welcome(&mut self, stream_wanted: bool) {
        if stream_wanted {
            self.on_start();
        } else {
            self.on_release();
        }
    }

    /// Reports the sim state once per reader pass. Returns true when a stream session ended on
    /// this call (the latch was cleared).
    pub fn observe_session(&mut self, connected: bool, key: StreamSessionKey) -> bool {
        if !connected {
            let ended = self.current.take().is_some();
            if ended {
                self.latch = false;
            }
            return ended;
        }
        match &mut self.current {
            None => {
                self.current = Some(key);
                false
            }
            Some(known) => {
                if known.differs_from(&key) {
                    *known = key;
                    self.latch = false;
                    true
                } else {
                    known.fill_from(&key);
                    false
                }
            }
        }
    }

    pub fn wants_stream(&self) -> bool {
        self.demand || self.latch
    }

    /// The collector streams while the sim is connected and demand or the latch holds.
    pub fn streaming(&self, sim_connected: bool) -> bool {
        sim_connected && self.wants_stream()
    }

    pub fn demand(&self) -> bool {
        self.demand
    }

    pub fn latch(&self) -> bool {
        self.latch
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn key(sub: i64, num: i32) -> StreamSessionKey {
        StreamSessionKey {
            subsession_id: Some(sub),
            session_num: Some(num),
            session_unique_id: Some(7),
            user_id: Some(42),
        }
    }

    #[test]
    fn collector_starts_with_the_latch_clear() {
        let c = StreamControl::new();
        assert!(!c.streaming(true));
    }

    #[test]
    fn release_keeps_streaming_until_the_session_ends() {
        let mut c = StreamControl::new();
        c.observe_session(true, key(1, 0));
        c.on_start();
        assert!(c.streaming(true));
        c.on_release();
        assert!(c.streaming(true), "latch holds");
        assert!(!c.observe_session(true, key(1, 0)));
        assert!(c.streaming(true));
        assert!(c.observe_session(true, key(1, 1)), "session number changed");
        assert!(!c.streaming(true), "latch cleared, no demand");
    }

    #[test]
    fn demand_survives_the_session_boundary() {
        let mut c = StreamControl::new();
        c.observe_session(true, key(1, 0));
        c.on_start();
        assert!(c.observe_session(true, key(2, 0)));
        assert!(c.streaming(true), "someone is still on the live page");
    }

    #[test]
    fn start_before_the_end_cancels_a_release() {
        let mut c = StreamControl::new();
        c.observe_session(true, key(1, 0));
        c.on_start();
        c.on_release();
        c.on_start();
        assert!(c.demand());
        c.on_release();
        assert!(c.observe_session(true, key(1, 1)));
        assert!(!c.wants_stream());
    }

    #[test]
    fn welcome_true_acts_as_start_and_false_as_release() {
        let mut c = StreamControl::new();
        c.on_welcome(true);
        assert!(c.demand() && c.latch());
        c.on_welcome(false);
        assert!(!c.demand() && c.latch(), "existing latch is kept");
    }

    #[test]
    fn sim_disconnect_ends_the_session() {
        let mut c = StreamControl::new();
        c.observe_session(true, key(1, 0));
        c.on_start();
        c.on_release();
        assert!(c.observe_session(false, StreamSessionKey::default()));
        assert!(!c.latch());
        assert!(!c.observe_session(false, StreamSessionKey::default()));
    }

    #[test]
    fn driver_change_ends_the_session_but_a_missing_value_does_not() {
        let mut c = StreamControl::new();
        c.observe_session(true, key(1, 0));
        c.on_start();
        c.on_release();
        let mut partial = key(1, 0);
        partial.user_id = None;
        assert!(!c.observe_session(true, partial));
        let mut other = key(1, 0);
        other.user_id = Some(43);
        assert!(c.observe_session(true, other));
    }
}
