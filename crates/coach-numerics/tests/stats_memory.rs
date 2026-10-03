//! The exact `statistics` paths keep two running sums, not one big integer per item
//! (review finding 11, port-phase1-review-codex-sol.md). A counting allocator measures the
//! peak heap growth of each call over a million items; the input itself is allocated first.
//! One test function, so no other test thread allocates during a measurement.

use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::atomic::{AtomicUsize, Ordering};

use coach_numerics as py;
use py::Num;

struct Counting;

static LIVE: AtomicUsize = AtomicUsize::new(0);
static PEAK: AtomicUsize = AtomicUsize::new(0);

unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        let ptr = unsafe { System.alloc(layout) };
        if !ptr.is_null() {
            let live = LIVE.fetch_add(layout.size(), Ordering::SeqCst) + layout.size();
            PEAK.fetch_max(live, Ordering::SeqCst);
        }
        ptr
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        unsafe { System.dealloc(ptr, layout) };
        LIVE.fetch_sub(layout.size(), Ordering::SeqCst);
    }
}

#[global_allocator]
static ALLOCATOR: Counting = Counting;

/// Peak bytes allocated above the level at entry while `f` runs.
fn peak_growth<T>(f: impl FnOnce() -> T) -> (T, usize) {
    let base = LIVE.load(Ordering::SeqCst);
    PEAK.store(base, Ordering::SeqCst);
    let value = f();
    (value, PEAK.load(Ordering::SeqCst) - base)
}

const ITEMS: usize = 1_000_000;
/// Far below the 40 MB that one retained ratio per item costs here.
const LIMIT: usize = 64 * 1024;

#[test]
fn exact_statistics_memory_does_not_grow_with_the_data() {
    // CPython 3.12: mean, pvariance, pstdev of [0.1] * 1_000_000
    let same: Vec<Num> = vec![Num::Float(0.1); ITEMS];
    // denominators 2**k that keep growing (0.5, 0.25, ...), then ints: every rescale path
    let mixed: Vec<Num> = (0..ITEMS)
        .map(|i| match i % 3 {
            0 => Num::Float(1.0 / f64::from(1u32 << (i % 31))),
            1 => Num::Int(i as i64),
            _ => Num::Float(i as f64 * 0.001),
        })
        .collect();

    // (function, data, CPython 3.12.14 result bits)
    let cases: [(&str, &[Num], u64); 7] = [
        ("mean", &same, 0x3FB9_9999_9999_999A),
        ("pvariance", &same, 0),
        ("pstdev", &same, 0),
        ("mean", &mixed, 0x4104_5D86_D65E_C2A8),
        ("variance", &mixed, 0x4233_63BB_6505_DB02),
        ("pvariance", &mixed, 0x4233_63BA_1FB8_405B),
        ("stdev", &mixed, 0x4111_9D0A_5ED0_3301),
    ];
    for (name, data, want) in cases {
        let (got, grew) = peak_growth(|| match name {
            "mean" => py::mean(data).map(Num::to_f64),
            "variance" => py::variance(data).map(Num::to_f64),
            "pvariance" => py::pvariance(data).map(Num::to_f64),
            "stdev" => py::stdev(data),
            "pstdev" => py::pstdev(data),
            other => unreachable!("{other}"),
        });
        let got = got.unwrap_or_else(|e| panic!("{name}: {e:?}"));
        assert_eq!(got.to_bits(), want, "{name}: {got:?}");
        assert!(grew < LIMIT, "{name} grew the heap by {grew} bytes");
    }
}
