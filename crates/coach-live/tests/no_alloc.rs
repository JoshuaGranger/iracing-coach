//! `Engine::on_tick` and `LiveState::encode_frame` never allocate after warm-up (contract 4.4):
//! a counting global allocator watches a full replay of generated race scenarios, including a
//! disconnect, a pause, a lap-counter reset and every cue.

mod common;

use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};

use coach_live::{Engine, EngineConfig, Sample};
use common::{context, load, sample_from};
use serde_json::Map;

struct Counting;

static ALLOCATIONS: AtomicUsize = AtomicUsize::new(0);

// SAFETY: every call forwards unchanged to the system allocator; the counter is the only addition.
unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        ALLOCATIONS.fetch_add(1, Ordering::Relaxed);
        unsafe { System.alloc(layout) }
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        unsafe { System.dealloc(ptr, layout) }
    }

    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        ALLOCATIONS.fetch_add(1, Ordering::Relaxed);
        unsafe { System.realloc(ptr, layout, new_size) }
    }
}

#[global_allocator]
static COUNTING: Counting = Counting;

#[test]
fn ticks_and_frames_do_not_allocate_after_warm_up() {
    let doc = load("engine.json");
    let mut samples: Vec<(Sample, bool)> = Vec::new();
    for name in ["race_sim_seed_1", "race_sim_seed_2", "race_sim_seed_3"] {
        let scenario = doc["scenarios"]
            .as_array()
            .unwrap()
            .iter()
            .find(|s| s["name"] == name)
            .unwrap();
        let session = context(&scenario["session"]);
        let mut values = Map::new();
        for step in scenario["steps"].as_array().unwrap() {
            for (key, value) in step["set"].as_object().unwrap() {
                values.insert(key.clone(), value.clone());
            }
            let mut sample = sample_from(&values);
            sample.connected = step["connected"].as_bool().unwrap();
            sample.captured_unix_ms = step["t"].as_u64().unwrap();
            sample.tick = u32::try_from(step["tick"].as_u64().unwrap()).unwrap();
            sample.tick_rate = 60;
            sample.session = Some(Arc::clone(&session));
            samples.push((sample, step["paused"].as_bool().unwrap()));
        }
    }
    assert!(samples.len() > 300);

    let mut engine = Engine::new(EngineConfig::default());
    let mut frame = Vec::new();
    let mut run = |engine: &mut Engine| {
        for (seq, (sample, paused)) in samples.iter().enumerate() {
            engine.set_paused(*paused);
            let state = engine.on_tick(sample);
            state.encode_frame(seq as u32, &mut frame);
        }
    };
    run(&mut engine); // warm-up
    let before = ALLOCATIONS.load(Ordering::Relaxed);
    run(&mut engine);
    run(&mut engine);
    let after = ALLOCATIONS.load(Ordering::Relaxed);
    assert_eq!(
        after - before,
        0,
        "allocations during {} steady-state ticks",
        2 * samples.len()
    );
}
