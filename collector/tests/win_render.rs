//! The overlay renderer into a memory bitmap: no window is created or shown. Set
//! `COACH_RENDER_OUT=<dir>` to keep the images (BMP) for a look.
#![cfg(windows)]

mod support;

use std::path::PathBuf;

use coach_collector::fixture::{MapBuilder, SessionSpec, Step};
use coach_collector::layout::Layout;
use coach_collector::win::render::{Dirty, Frame, Renderer, window_size};
use serde_json::json;
use support::*;
use windows::Win32::Graphics::Gdi::{
    BI_RGB, BITMAPINFO, BITMAPINFOHEADER, CreateCompatibleDC, CreateDIBSection, DIB_RGB_COLORS,
    DeleteDC, DeleteObject, GdiFlush, HDC, HGDIOBJ, SelectObject,
};

struct Image {
    width: i32,
    height: i32,
    /// BGRA, top-down.
    pixels: Vec<u8>,
}

fn render(
    layout: &Layout,
    state: Option<&coach_live::LiveState>,
    scale: f64,
    history: usize,
) -> Image {
    let (w, h) = window_size(scale);
    let mut renderer = Renderer::new();
    // SAFETY: a memory DC and a DIB section owned by this function.
    unsafe {
        let dc = CreateCompatibleDC(None);
        let info = BITMAPINFO {
            bmiHeader: BITMAPINFOHEADER {
                biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
                biWidth: w,
                biHeight: -h,
                biPlanes: 1,
                biBitCount: 32,
                biCompression: BI_RGB.0,
                ..BITMAPINFOHEADER::default()
            },
            ..BITMAPINFO::default()
        };
        let mut bits = std::ptr::null_mut();
        let bitmap = CreateDIBSection(Some(dc), &info, DIB_RGB_COLORS, &mut bits, None, 0).unwrap();
        let old = SelectObject(dc, HGDIOBJ(bitmap.0));
        for i in 0..=history {
            // Earlier paints feed the trend tiles' history.
            renderer.paint(
                dc,
                &Frame {
                    width: w,
                    height: h,
                    scale,
                    layout,
                    state,
                    now_ms: 1_000 + i as u64 * 100,
                },
            );
        }
        let _ = GdiFlush();
        let pixels = std::slice::from_raw_parts(bits as *const u8, (w * h * 4) as usize).to_vec();
        SelectObject(dc, old);
        let _ = DeleteObject(HGDIOBJ(bitmap.0));
        let _ = DeleteDC(dc);
        Image {
            width: w,
            height: h,
            pixels,
        }
    }
}

fn save_bmp(img: &Image, name: &str) {
    let Some(dir) = std::env::var_os("COACH_RENDER_OUT").map(PathBuf::from) else {
        return;
    };
    std::fs::create_dir_all(&dir).unwrap();
    let row = (img.width * 3 + 3) & !3;
    let size = 54 + row * img.height;
    let mut out = Vec::with_capacity(size as usize);
    out.extend_from_slice(b"BM");
    out.extend_from_slice(&(size as u32).to_le_bytes());
    out.extend_from_slice(&[0, 0, 0, 0]);
    out.extend_from_slice(&54u32.to_le_bytes());
    out.extend_from_slice(&40u32.to_le_bytes());
    out.extend_from_slice(&img.width.to_le_bytes());
    out.extend_from_slice(&img.height.to_le_bytes());
    out.extend_from_slice(&1u16.to_le_bytes());
    out.extend_from_slice(&24u16.to_le_bytes());
    out.extend_from_slice(&[0; 24]);
    for y in (0..img.height).rev() {
        let mut line = Vec::new();
        for x in 0..img.width {
            let i = ((y * img.width + x) * 4) as usize;
            line.extend_from_slice(&img.pixels[i..i + 3]);
        }
        line.resize(row as usize, 0);
        out.extend_from_slice(&line);
    }
    std::fs::write(dir.join(name), out).unwrap();
}

fn distinct_colors(img: &Image) -> usize {
    let mut seen = std::collections::HashSet::new();
    for p in img.pixels.as_chunks::<4>().0 {
        seen.insert([p[0], p[1], p[2]]);
    }
    seen.len()
}

/// A connected, mid-race state from the real engine.
fn live_state() -> coach_live::LiveState {
    let b = MapBuilder::new(60).engine_vars();
    let mut steps = vec![Step::Static {
        status: 1,
        update: 1,
        session_info: SessionSpec::default().yaml(),
    }];
    for i in 0..300 {
        let mut row = driving_row(&b, 55.0 + (i as f64 / 20.0).sin() * 8.0);
        row.set_f64("Throttle", 0.5 + (i as f64 / 15.0).sin() * 0.4)
            .set_f64("Brake", 0.0)
            .set_i32("Gear", 4)
            .set_f64("RPM", 7200.0)
            .set_f64("FuelLevel", 38.5)
            .set_f64("FuelLevelPct", 0.62)
            .set_i32("PlayerCarPosition", 4)
            .set_i32("PlayerCarClassPosition", 4)
            .set_f64("LapLastLapTime", 24.318)
            .set_f64("LatAccel", 14.0)
            .set_f64("SteeringWheelAngle", 0.3)
            .set_f64("TrackTemp", 38.0)
            .set_f64("AirTemp", 27.0);
        steps.push(Step::Tick {
            t_ms: i * 16,
            tick: i as i32,
            row,
        });
    }
    let bytes = b.write_steps(Vec::new(), &steps).unwrap();
    let mut h = Harness::new(&bytes);
    h.run();
    h.hub.live().unwrap()
}

fn rich_layout() -> Layout {
    Layout::from_json(&json!({
        "rows": 3, "columns": 3,
        "tiles": [
            {"tile_id": "a", "metric": "position", "row": 0, "column": 0, "row_span": 1, "column_span": 1, "style": "number"},
            {"tile_id": "b", "metric": "last-lap", "row": 0, "column": 1, "row_span": 1, "column_span": 1, "style": "number", "accent": "blue"},
            {"tile_id": "c", "metric": "throttle", "row": 0, "column": 2, "row_span": 1, "column_span": 1, "style": "gauge", "accent": "green"},
            {"tile_id": "d", "metric": "speed", "row": 1, "column": 0, "row_span": 1, "column_span": 2, "style": "trend", "unit": "mph", "precision": 1},
            {"tile_id": "e", "metric": "fuel", "row": 1, "column": 2, "row_span": 1, "column_span": 1, "style": "bar", "accent": "amber"},
            {"tile_id": "f", "metric": "gear", "row": 2, "column": 0, "row_span": 1, "column_span": 1, "style": "number"},
            {"tile_id": "g", "metric": "steering", "row": 2, "column": 1, "row_span": 1, "column_span": 1, "style": "trend"},
            {"tile_id": "h", "metric": "coach-cue", "row": 2, "column": 2, "row_span": 1, "column_span": 1, "style": "status"}
        ]
    }))
    .unwrap()
}

#[test]
fn the_default_layout_renders_text_and_tiles_over_the_background() {
    let state = live_state();
    assert!(state.connected);
    let img = render(&Layout::default_layout(), Some(&state), 1.0, 0);
    save_bmp(&img, "default.bmp");
    assert_eq!((img.width, img.height), (540, 360));
    // Background, tile surfaces, borders, text anti-aliasing and accents: many distinct colors.
    assert!(
        distinct_colors(&img) > 40,
        "{} colors",
        distinct_colors(&img)
    );
    // The corner is background (0B1015); the centre of the first tile is not.
    let px = |x: i32, y: i32| {
        let i = ((y * img.width + x) * 4) as usize;
        [img.pixels[i + 2], img.pixels[i + 1], img.pixels[i]]
    };
    assert_eq!(px(1, 1), [0x0B, 0x10, 0x15]);
    assert_eq!(px(12, 12), [0x15, 0x1E, 0x25], "tile surface");
}

#[test]
fn every_style_draws_at_three_scales() {
    let state = live_state();
    let layout = rich_layout();
    let mut last = 0;
    for (name, scale) in [("compact", 0.8), ("standard", 1.0), ("expanded", 1.25)] {
        let img = render(&layout, Some(&state), scale, 40);
        save_bmp(&img, &format!("rich-{name}.bmp"));
        assert_eq!(img.width, (540.0 * scale).round() as i32);
        assert!(distinct_colors(&img) > 60);
        assert!(img.pixels.len() > last);
        last = img.pixels.len();
    }
}

#[test]
fn without_a_state_the_monitor_says_it_is_waiting() {
    let img = render(&Layout::default_layout(), None, 1.0, 0);
    save_bmp(&img, "waiting.bmp");
    assert!(distinct_colors(&img) > 3, "text on the background");
    let plain = render(&Layout::default_layout(), None, 1.0, 0);
    assert_eq!(img.pixels, plain.pixels, "rendering is deterministic");
}

/// A memory surface that keeps its pixels between paints, like the overlay's back buffer.
struct Surface {
    dc: HDC,
    bitmap: windows::Win32::Graphics::Gdi::HBITMAP,
    old: HGDIOBJ,
    bits: *mut core::ffi::c_void,
    width: i32,
    height: i32,
}

impl Surface {
    fn new(scale: f64) -> Surface {
        let (width, height) = window_size(scale);
        // SAFETY: a memory DC and a DIB section released in Drop.
        unsafe {
            let dc = CreateCompatibleDC(None);
            let info = BITMAPINFO {
                bmiHeader: BITMAPINFOHEADER {
                    biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
                    biWidth: width,
                    biHeight: -height,
                    biPlanes: 1,
                    biBitCount: 32,
                    biCompression: BI_RGB.0,
                    ..BITMAPINFOHEADER::default()
                },
                ..BITMAPINFO::default()
            };
            let mut bits = std::ptr::null_mut();
            let bitmap =
                CreateDIBSection(Some(dc), &info, DIB_RGB_COLORS, &mut bits, None, 0).unwrap();
            let old = SelectObject(dc, HGDIOBJ(bitmap.0));
            Surface {
                dc,
                bitmap,
                old,
                bits,
                width,
                height,
            }
        }
    }

    fn pixels(&self) -> Vec<u8> {
        // SAFETY: the DIB section holds width x height x 4 bytes while `self` lives.
        unsafe {
            let _ = GdiFlush();
            std::slice::from_raw_parts(
                self.bits as *const u8,
                (self.width * self.height * 4) as usize,
            )
            .to_vec()
        }
    }
}

impl Drop for Surface {
    fn drop(&mut self) {
        // SAFETY: released once, in the reverse order of creation.
        unsafe {
            SelectObject(self.dc, self.old);
            let _ = DeleteObject(HGDIOBJ(self.bitmap.0));
            let _ = DeleteDC(self.dc);
        }
    }
}

fn frame<'a>(
    s: &Surface,
    layout: &'a Layout,
    state: &'a coach_live::LiveState,
    now_ms: u64,
) -> Frame<'a> {
    Frame {
        width: s.width,
        height: s.height,
        scale: 1.0,
        layout,
        state: Some(state),
        now_ms,
    }
}

/// What the overlay does for one new state: nothing, the changed tiles, or everything.
fn present(r: &mut Renderer, s: &Surface, f: &Frame<'_>) -> Dirty {
    let dirty = r.dirty(f);
    match &dirty {
        Dirty::Nothing => {}
        Dirty::Tiles(tiles) => r.paint_tiles(s.dc, f, tiles),
        Dirty::All => r.paint(s.dc, f),
    }
    dirty
}

#[test]
fn a_partial_repaint_leaves_exactly_the_pixels_a_full_repaint_would() {
    let layout = rich_layout();
    let a = live_state();
    let mut b = a;
    b.gear = Some(5);
    b.position = Some(2);
    b.last_lap = Some(23.912);
    b.throttle = Some(0.91);
    b.speed = a.speed.map(|v| v + 3.0);
    b.steering_wheel_angle = Some(-0.4);

    let partial = Surface::new(1.0);
    let mut r = Renderer::new();
    r.paint(partial.dc, &frame(&partial, &layout, &a, 1_000));
    assert_eq!(
        r.dirty(&frame(&partial, &layout, &a, 1_000)),
        Dirty::Nothing,
        "the state just painted needs nothing"
    );
    let first = partial.pixels();
    let d = present(&mut r, &partial, &frame(&partial, &layout, &b, 1_100));
    let Dirty::Tiles(tiles) = d else {
        panic!("some tiles changed, not all: {d:?}");
    };
    assert!(
        tiles.len() < layout.tiles.len() && tiles.len() >= 3,
        "{tiles:?}"
    );
    assert_ne!(first, partial.pixels(), "the new state is on the surface");

    let full = Surface::new(1.0);
    let mut fr = Renderer::new();
    fr.paint(full.dc, &frame(&full, &layout, &a, 1_000));
    fr.paint(full.dc, &frame(&full, &layout, &b, 1_100));
    assert_eq!(
        partial.pixels(),
        full.pixels(),
        "tiles {tiles:?} redrawn in place equal a repaint of everything"
    );
    // And the next 100 ms with nothing new moves only the scrolling trend tiles.
    let next = present(&mut r, &partial, &frame(&partial, &layout, &b, 1_200));
    let trend_tiles: Vec<usize> = layout
        .tiles
        .iter()
        .enumerate()
        .filter(|(_, t)| t.style == coach_collector::layout::Style::Trend)
        .map(|(i, _)| i)
        .collect();
    assert_eq!(next, Dirty::Tiles(trend_tiles));
}

#[test]
fn a_tile_under_another_repaints_the_one_that_covers_it_in_turn() {
    // A (column 0) lies under B (columns 0-2), which lies under C (columns 2-3), while A and C
    // never meet. Only A's position changes; B is redrawn over A and C must be redrawn over B.
    let layout = Layout::from_json(&json!({
        "rows": 1, "columns": 4,
        "tiles": [
            {"tile_id": "a", "metric": "position", "row": 0, "column": 0, "row_span": 1, "column_span": 1, "style": "number"},
            {"tile_id": "b", "metric": "last-lap", "row": 0, "column": 0, "row_span": 1, "column_span": 3, "style": "number"},
            {"tile_id": "c", "metric": "gear", "row": 0, "column": 2, "row_span": 1, "column_span": 2, "style": "number"}
        ]
    }))
    .unwrap();
    let a = live_state();
    let mut b = a;
    b.position = Some(2);

    let partial = Surface::new(1.0);
    let mut r = Renderer::new();
    r.paint(partial.dc, &frame(&partial, &layout, &a, 1_000));
    let d = present(&mut r, &partial, &frame(&partial, &layout, &b, 1_100));
    assert_eq!(d, Dirty::All, "A, B over A, and C over B: every tile");

    let full = Surface::new(1.0);
    let mut fr = Renderer::new();
    fr.paint(full.dc, &frame(&full, &layout, &b, 1_100));
    assert_eq!(partial.pixels(), full.pixels());
}

#[test]
fn nothing_is_repainted_until_a_tile_shows_something_new() {
    let layout = Layout::default_layout();
    let a = live_state();
    let s = Surface::new(1.0);
    let mut r = Renderer::new();
    assert_eq!(
        r.dirty(&frame(&s, &layout, &a, 0)),
        Dirty::All,
        "a first paint draws everything"
    );
    r.paint(s.dc, &frame(&s, &layout, &a, 0));
    // The car moves (speed, throttle, rpm change) but no default tile shows them.
    let mut moving = a;
    for i in 0..50 {
        moving.speed = a.speed.map(|v| v + i as f32);
        moving.throttle = Some(0.3 + i as f32 * 0.01);
        moving.rpm = Some(6000.0 + i as f32 * 10.0);
        assert_eq!(
            r.dirty(&frame(&s, &layout, &moving, 100 * i)),
            Dirty::Nothing,
            "tick {i}"
        );
    }
    // A new last lap changes one tile only.
    moving.last_lap = Some(23.5);
    match r.dirty(&frame(&s, &layout, &moving, 6_000)) {
        Dirty::Tiles(t) => assert_eq!(t, [1], "the last-lap tile"),
        other => panic!("{other:?}"),
    }
    // Another layout (or no state) is a full paint.
    assert_eq!(
        r.dirty(&frame(&s, &rich_layout(), &moving, 6_000)),
        Dirty::All
    );
    let none = Frame {
        state: None,
        ..frame(&s, &layout, &moving, 6_000)
    };
    assert_eq!(r.dirty(&none), Dirty::All);
}

#[test]
#[ignore = "timing; run in release"]
fn a_full_repaint_of_a_busy_layout_costs_about_a_millisecond() {
    let layout = rich_layout();
    let state = live_state();
    let s = Surface::new(1.0);
    let mut r = Renderer::new();
    r.paint(s.dc, &frame(&s, &layout, &state, 0));
    let started = std::time::Instant::now();
    let n = 300;
    for i in 0..n {
        r.paint(s.dc, &frame(&s, &layout, &state, 100 * (i + 1)));
    }
    let per = started.elapsed().as_secs_f64() * 1e6 / n as f64;
    println!("perf-render full_paint_us={per:.0} (rich layout, 540x360)");
}
