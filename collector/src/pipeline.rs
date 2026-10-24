//! The reader thread's work between the SDK and everything else: one `Sample` in, one `LiveState`
//! out per tick; the live frame (decimated to at most 60 Hz) into the send queue while streaming;
//! the latest state to the overlay; presence to the status model; garage events to the journal
//! thread; the on-track evidence to the gate. Nothing here touches the disk or the network.

use std::sync::Arc;
use std::sync::atomic::Ordering;
use std::sync::mpsc::Sender;
use std::time::Duration;

use coach_live::{Engine, EngineConfig, LiveState, SessionContext};
use serde_json::{Value, json};

use crate::dry::DryLog;
use crate::garage::{GarageAction, GarageCapture, GarageTick};
use crate::gate::Evidence;
use crate::hub::{Hub, SimKind, StatusModel};
use crate::sdk::{Poll, SdkReader};
use crate::stream::StreamSessionKey;

/// Counters for tests and the dry-run summary.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct PipelineStats {
    pub ticks: u64,
    pub frames_encoded: u64,
    pub frame_bytes: u64,
    pub garage_actions: u64,
}

pub struct Pipeline {
    hub: Arc<Hub>,
    engine: Engine,
    garage: GarageCapture,
    garage_tx: Option<Sender<GarageAction>>,
    dry: Option<Arc<DryLog>>,
    wheel: Option<crate::wheel::WheelHandle>,
    field: Option<crate::field::Capture>,
    /// Encode frames even without viewer demand (dry run); they are counted, not queued.
    force_stream: bool,
    seq: u32,
    pending_marker: bool,
    first_frame_of_stream: bool,
    last_sent_tick: Option<u32>,
    was_streaming: bool,
    buf: Vec<u8>,
    model: StatusModel,
    session_cache: Option<(SessionKey, Value)>,
    driver_cache: Option<(usize, Option<Value>)>,
    stats: PipelineStats,
    summary_window: SummaryWindow,
    applied_paused: bool,
    applied_glance: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct SessionKey {
    epoch: u32,
    context: usize,
    session_num: Option<i32>,
}

#[derive(Debug, Default)]
struct SummaryWindow {
    start_ms: Option<u64>,
    frames: u64,
    bytes: u64,
    ticks: u64,
}

impl Pipeline {
    pub fn new(
        hub: Arc<Hub>,
        garage_tx: Option<Sender<GarageAction>>,
        dry: Option<Arc<DryLog>>,
    ) -> Pipeline {
        let glance = hub.safe_glance.load(Ordering::Relaxed);
        Pipeline {
            engine: Engine::new(EngineConfig {
                safe_glance: glance,
            }),
            hub,
            garage: GarageCapture::new(),
            garage_tx,
            force_stream: dry.is_some(),
            dry,
            wheel: None,
            field: None,
            seq: 0,
            pending_marker: false,
            first_frame_of_stream: false,
            last_sent_tick: None,
            was_streaming: false,
            buf: Vec::with_capacity(256),
            model: StatusModel::default(),
            session_cache: None,
            driver_cache: None,
            stats: PipelineStats::default(),
            summary_window: SummaryWindow::default(),
            applied_paused: false,
            applied_glance: glance,
        }
    }

    /// Reuse the collector's existing SDK feed; optional diagnostics add no second SDK reader.
    pub fn with_wheel(mut self, wheel: crate::wheel::WheelHandle) -> Self {
        self.wheel = Some(wheel);
        self
    }

    pub fn stats(&self) -> PipelineStats {
        self.stats
    }

    pub fn with_field(mut self, capture: crate::field::Capture) -> Self {
        self.field = Some(capture);
        self
    }

    /// The replay is over: close the garage channel and publish the sim as not running.
    pub fn finish(&mut self) {
        self.garage_tx = None;
        self.field = None;
        if let Some(wheel) = &self.wheel {
            wheel.set_sdk_connected(false);
        }
        let mut next = self.model.clone();
        next.sim = SimKind::NotRunning;
        next.connected = false;
        next.on_track = false;
        next.in_garage = false;
        next.on_pit_road = false;
        next.streaming = false;
        next.session = None;
        if next != self.model {
            self.model = next.clone();
            self.hub.set_status(next);
        }
        self.hub.frames.clear();
    }

    /// Handles one reader result: a connected tick or a disconnected pulse.
    pub fn on_poll(&mut self, poll: Poll, reader: &SdkReader) {
        if poll == Poll::NoData {
            return;
        }
        self.sync_settings();
        let sample = reader.sample();
        if let Some(field) = &mut self.field {
            field.observe(reader, poll == Poll::Tick);
        }
        if let Some(wheel) = &self.wheel {
            if poll == Poll::Tick {
                wheel.observe_sdk(
                    sample.captured_unix_ms,
                    sample.steering_wheel_angle,
                    sample.connected,
                );
            } else {
                wheel.set_sdk_connected(false);
            }
        }
        let state = self.engine.on_tick(sample);
        self.stats.ticks += 1;
        self.hub.publish_live(&state);

        let ctx = sample.session.as_deref();
        let key = StreamSessionKey {
            subsession_id: ctx.and_then(|c| c.subsession_id),
            session_num: sample.session_num,
            session_unique_id: sample.session_unique_id,
            user_id: ctx.and_then(|c| c.driver_user_id),
        };
        let streaming = {
            let mut stream = self.hub.stream();
            stream.observe_session(state.connected, key);
            stream.streaming(state.connected)
        };
        if streaming && !self.was_streaming {
            self.first_frame_of_stream = true;
            self.last_sent_tick = None;
        }
        if !streaming && self.was_streaming {
            self.hub.frames.clear();
        }
        self.was_streaming = streaming;

        if state.first_of_epoch {
            self.pending_marker = true;
        }
        if streaming || (self.force_stream && state.connected) {
            self.emit_frame(&state);
        }
        self.update_status(&state, reader, sample.player_car_idx, streaming);
        self.run_garage(
            reader,
            sample.is_in_garage,
            sample.captured_unix_ms,
            state.connected,
        );
        self.write_summary(sample.captured_unix_ms, &state);
    }

    fn sync_settings(&mut self) {
        let paused = self.hub.paused.load(Ordering::Relaxed);
        if paused != self.applied_paused {
            self.engine.set_paused(paused);
            self.applied_paused = paused;
        }
        let glance = self.hub.safe_glance.load(Ordering::Relaxed);
        if glance != self.applied_glance {
            self.engine.set_safe_glance(glance);
            self.applied_glance = glance;
        }
    }

    fn emit_frame(&mut self, state: &LiveState) {
        let step = frame_step(state.tick_rate);
        let due = match self.last_sent_tick {
            None => true,
            Some(last) => {
                let delta = state.tick.wrapping_sub(last) as i32;
                delta >= step || delta < 0
            }
        };
        if !due {
            return;
        }
        self.last_sent_tick = Some(state.tick);
        let mut out = *state;
        out.first_of_epoch = self.pending_marker || self.first_frame_of_stream;
        self.pending_marker = false;
        self.first_frame_of_stream = false;
        out.encode_frame(self.seq, &mut self.buf);
        self.seq = self.seq.wrapping_add(1);
        self.stats.frames_encoded += 1;
        self.stats.frame_bytes += self.buf.len() as u64;
        self.summary_window.frames += 1;
        self.summary_window.bytes += self.buf.len() as u64;
        if self.dry.is_none() {
            self.hub.frames.push(self.buf.clone());
        }
    }

    fn update_status(
        &mut self,
        state: &LiveState,
        reader: &SdkReader,
        player_car_idx: Option<i32>,
        streaming: bool,
    ) {
        let ctx = reader.context();
        let sim = if !state.connected {
            SimKind::NotRunning
        } else if player_car_idx.is_some_and(|i| i >= 0)
            && ctx.is_some_and(|c| c.driver_car_idx.is_some())
        {
            SimKind::InSession
        } else {
            SimKind::Menus
        };
        // Nothing is cloned or allocated while the status is unchanged (this runs every tick).
        let mut dirty = false;
        let on_pit_road = state.on_pit_road == Some(true);
        let m = &mut self.model;
        if m.sim != sim
            || m.connected != state.connected
            || m.on_track != state.on_track
            || m.in_garage != state.in_garage
            || m.on_pit_road != on_pit_road
            || m.streaming != streaming
        {
            m.sim = sim;
            m.connected = state.connected;
            m.on_track = state.on_track;
            m.in_garage = state.in_garage;
            m.on_pit_road = on_pit_road;
            m.streaming = streaming;
            dirty = true;
        }

        let ctx_id = ctx.map_or(0, |c| Arc::as_ptr(c) as usize);
        let session_num = reader.sample().session_num;
        let key = SessionKey {
            epoch: state.session_epoch,
            context: ctx_id,
            session_num,
        };
        let mut session_rebuilt = false;
        if self.session_cache.as_ref().map(|(k, _)| *k) != Some(key) {
            let value = session_ref(state.session_epoch, session_num, ctx.map(|c| c.as_ref()));
            self.session_cache = Some((key, value));
            session_rebuilt = true;
        }
        if session_rebuilt || self.model.session.is_some() != state.connected {
            self.model.session = if state.connected {
                self.session_cache.as_ref().map(|(_, v)| v.clone())
            } else {
                None
            };
            dirty = true;
        }
        if self.driver_cache.as_ref().map(|(id, _)| *id) != Some(ctx_id) {
            let driver = ctx.and_then(|c| {
                c.driver_user_id
                    .map(|id| json!({"customer_id": id, "name": c.driver_user_name}))
            });
            self.model.driver = driver.clone();
            self.driver_cache = Some((ctx_id, driver));
            dirty = true;
        }
        if dirty {
            self.hub.set_status(self.model.clone());
        }
    }

    fn run_garage(
        &mut self,
        reader: &SdkReader,
        in_garage: Option<bool>,
        now_ms: u64,
        connected: bool,
    ) {
        if self.garage_tx.is_none() || !(in_garage == Some(true) || self.garage.in_visit()) {
            return;
        }
        let mut view = reader.setup().clone();
        view.session = self.session_cache.as_ref().map(|(_, v)| v.clone());
        let actions = self.garage.observe(&GarageTick {
            now_ms,
            connected,
            in_garage,
            setup: &view,
        });
        for action in actions {
            self.stats.garage_actions += 1;
            if let Some(tx) = &self.garage_tx {
                let _ = tx.send(action);
            }
        }
    }

    fn write_summary(&mut self, now_ms: u64, state: &LiveState) {
        let w = &mut self.summary_window;
        w.ticks += 1;
        let start = *w.start_ms.get_or_insert(now_ms);
        if now_ms.saturating_sub(start) < 1_000 {
            return;
        }
        if let Some(dry) = &self.dry {
            dry.write(&json!({
                "kind": "frames",
                "unix_ms": now_ms,
                "window_ms": now_ms - start,
                "ticks": w.ticks,
                "frames": w.frames,
                "bytes": w.bytes,
                "tick": state.tick,
                "session_epoch": state.session_epoch,
                "connected": state.connected,
                "on_track": state.on_track,
                "in_garage": state.in_garage,
            }));
        }
        *w = SummaryWindow {
            start_ms: Some(now_ms),
            ..SummaryWindow::default()
        };
    }
}

/// Every `ceil(tick_rate / 60)`-th tick goes on the wire, so the wire never exceeds 60 frames/s.
pub fn frame_step(tick_rate: u16) -> i32 {
    i32::from(tick_rate.div_ceil(60)).max(1)
}

fn session_ref(epoch: u32, session_num: Option<i32>, ctx: Option<&SessionContext>) -> Value {
    let session_type = ctx
        .zip(session_num)
        .and_then(|(c, n)| c.session_type(i64::from(n)))
        .map(str::to_string);
    json!({
        "session_epoch": epoch,
        "subsession_id": ctx.and_then(|c| c.subsession_id),
        "session_num": session_num,
        "session_type": session_type,
        "track": ctx.and_then(|c| c.track.clone()),
        "track_config": ctx.and_then(|c| c.track_config.clone()),
        "car_path": ctx.and_then(|c| c.car_path.clone()),
    })
}

/// The reader loop: waits for the data-valid event, reads, runs the pipeline, reports the
/// off-track evidence to the gate. Returns when shutdown is requested. With `exit_at_end`, a
/// consumed replay counts as the sim having quit: the pipeline closes its channels, `replay_done`
/// is raised and the gate is told there is no map, so the uploader may finish its queue.
pub fn run_reader_loop(
    reader: &mut SdkReader,
    pipeline: &mut Pipeline,
    hub: &Hub,
    exit_at_end: bool,
) {
    while !hub.is_shutdown() {
        reader.wait(Duration::from_millis(100));
        let poll = reader.poll(hub.clock.mono_ms(), hub.clock.unix_ms());
        hub.gate
            .observe_with_session(reader.evidence(), reader.in_race());
        hub.refresh_bulk_gate_status();
        pipeline.on_poll(poll, reader);
        if exit_at_end && reader.at_end() {
            pipeline.finish();
            hub.replay_done.store(true, Ordering::SeqCst);
            while !hub.is_shutdown() {
                hub.gate.observe(Evidence::NoMap);
                std::thread::sleep(Duration::from_millis(100));
            }
            return;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn frames_never_exceed_sixty_per_second() {
        assert_eq!(frame_step(0), 1);
        assert_eq!(frame_step(30), 1);
        assert_eq!(frame_step(60), 1);
        assert_eq!(frame_step(120), 2);
        assert_eq!(frame_step(240), 4);
        assert_eq!(frame_step(360), 6);
        assert_eq!(frame_step(100), 2);
    }
}
