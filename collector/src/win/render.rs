//! GDI drawing of the live monitor into any device context (the overlay window's, or a memory
//! bitmap in tests). Colors are the dark theme tokens of `config/theme.dark.json`.

use std::collections::HashMap;
use std::hash::{Hash, Hasher};

use coach_live::{LiveState, TileState, TileText, display_range, format_tile};
use coach_wire::live_channels::Priority;
use windows::Win32::Foundation::{COLORREF, POINT, RECT};
use windows::Win32::Graphics::Gdi::{
    AD_CLOCKWISE, Arc, CLEARTYPE_QUALITY, CLIP_DEFAULT_PRECIS, CreateFontW, CreatePen,
    CreateSolidBrush, DEFAULT_CHARSET, DEFAULT_PITCH, DRAW_TEXT_FORMAT, DT_CENTER, DT_END_ELLIPSIS,
    DT_LEFT, DT_NOPREFIX, DT_SINGLELINE, DT_VCENTER, DT_WORDBREAK, DeleteObject, DrawTextW,
    FF_DONTCARE, FillRect, HDC, HFONT, HGDIOBJ, OUT_DEFAULT_PRECIS, PS_SOLID, Polyline, RoundRect,
    SelectObject, SetArcDirection, SetBkMode, SetTextColor, TRANSPARENT,
};

use super::wide;
use crate::layout::{
    Accent, BASE_HEIGHT, BASE_WIDTH, GAP, Layout, PADDING, Rect as TileRect, Style, Tile,
    TrendBuffer,
};

const BG: u32 = 0x0B1015;
const SURFACE: u32 = 0x151E25;
const BORDER: u32 = 0x334752;
const TEXT: u32 = 0xF6F8FA;
const TEXT_MUTED: u32 = 0x96A6AE;
const UNAVAILABLE: u32 = 0x8E98A5;

pub fn colorref(rgb: u32) -> COLORREF {
    COLORREF(((rgb >> 16) & 0xFF) | (rgb & 0xFF00) | ((rgb & 0xFF) << 16))
}

/// Window size in pixels for a scale factor.
pub fn window_size(scale: f64) -> (i32, i32) {
    (
        (f64::from(BASE_WIDTH) * scale).round() as i32,
        (f64::from(BASE_HEIGHT) * scale).round() as i32,
    )
}

/// Everything `Renderer::paint` needs besides the device context.
#[derive(Clone, Copy)]
pub struct Frame<'a> {
    pub width: i32,
    pub height: i32,
    pub scale: f64,
    pub layout: &'a Layout,
    pub state: Option<&'a LiveState>,
    pub now_ms: u64,
}

/// What the overlay has to repaint to show a new state.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Dirty {
    /// Every visible figure is as drawn.
    Nothing,
    /// Only these tiles (indexes into `Layout::tiles`, with every tile they overlap) changed.
    Tiles(Vec<usize>),
    /// Nothing valid is on the surface yet (first paint, other layout, no state): paint it all.
    All,
}

pub struct Renderer {
    fonts: HashMap<(i32, i32), HFONT>,
    trends: HashMap<String, TrendBuffer>,
    last_epoch: Option<u32>,
    /// Per layout tile, a fingerprint of what was last drawn for it (empty: nothing drawn).
    marks: Vec<u64>,
}

impl Default for Renderer {
    fn default() -> Self {
        Self::new()
    }
}

impl Renderer {
    pub fn new() -> Renderer {
        Renderer {
            fonts: HashMap::new(),
            trends: HashMap::new(),
            last_epoch: None,
            marks: Vec::new(),
        }
    }

    /// Paints the whole monitor: background, then every tile of `frame.layout`.
    pub fn paint(&mut self, hdc: HDC, frame: &Frame<'_>) {
        let Frame {
            width,
            height,
            scale,
            layout,
            state,
            now_ms,
        } = *frame;
        // SAFETY: every GDI object created below is deselected and deleted before returning.
        unsafe {
            fill(hdc, 0, 0, width, height, BG);
            SetBkMode(hdc, TRANSPARENT);
        }
        let Some(state) = state else {
            self.marks.clear();
            self.centered(
                hdc,
                "Waiting for iRacing",
                RectI::new(0, 0, width, height),
                TEXT_MUTED,
                18.0 * scale,
                500,
            );
            return;
        };
        self.sync_epoch(state);
        self.marks.clear();
        self.marks.resize(layout.tiles.len(), 0);
        for (i, (tile, rect)) in layout
            .tiles
            .iter()
            .zip(tile_rects(layout, width, height, scale))
            .enumerate()
        {
            self.marks[i] = self.draw_tile(hdc, tile, rect, state, now_ms, scale);
        }
    }

    /// What differs between `frame` and what the last paint drew, without drawing. A tile counts
    /// as changed when its text, accent or bar position differs; a trend tile with a value moves
    /// with the clock, so it counts as changed in every 100 ms step.
    pub fn dirty(&self, frame: &Frame<'_>) -> Dirty {
        let (Some(state), true) = (frame.state, self.marks.len() == frame.layout.tiles.len())
        else {
            return Dirty::All;
        };
        let mut changed: Vec<usize> = Vec::new();
        for (i, tile) in frame.layout.tiles.iter().enumerate() {
            let text = format_tile(&tile.metric, state, tile.unit.as_deref(), tile.precision);
            if tile_mark(tile, text.as_ref(), state, frame.now_ms) != self.marks[i] {
                changed.push(i);
            }
        }
        if changed.is_empty() {
            return Dirty::Nothing;
        }
        // A tile drawn over another one's pixels takes the other one with it.
        let rects = tile_rects(frame.layout, frame.width, frame.height, frame.scale);
        let overlaps = |a: &TileRect, b: &TileRect| {
            a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h
        };
        // Repeated until no tile joins: A under B under C repaints C although C meets only B.
        let mut all = changed.clone();
        loop {
            let before = all.len();
            for (j, other) in rects.iter().enumerate() {
                if !all.contains(&j) && all.iter().any(|&i| overlaps(&rects[i], other)) {
                    all.push(j);
                }
            }
            if all.len() == before {
                break;
            }
        }
        all.sort_unstable();
        if all.len() == frame.layout.tiles.len() {
            Dirty::All
        } else {
            Dirty::Tiles(all)
        }
    }

    /// Redraws only `tiles` over a surface that already holds the rest of the last paint.
    pub fn paint_tiles(&mut self, hdc: HDC, frame: &Frame<'_>, tiles: &[usize]) {
        let (Some(state), true) = (frame.state, self.marks.len() == frame.layout.tiles.len())
        else {
            self.paint(hdc, frame);
            return;
        };
        self.sync_epoch(state);
        let rects = tile_rects(frame.layout, frame.width, frame.height, frame.scale);
        // SAFETY: plain device-context state on `hdc`.
        unsafe {
            SetBkMode(hdc, TRANSPARENT);
        }
        for &i in tiles {
            let (Some(tile), Some(rect)) = (frame.layout.tiles.get(i), rects.get(i)) else {
                continue;
            };
            // The rounded corners show the background, which the old drawing covered.
            // SAFETY: fills inside `hdc`.
            unsafe {
                fill(hdc, rect.x, rect.y, rect.w, rect.h, BG);
            }
            self.marks[i] = self.draw_tile(hdc, tile, *rect, state, frame.now_ms, frame.scale);
        }
    }

    fn sync_epoch(&mut self, state: &LiveState) {
        if self.last_epoch != Some(state.session_epoch) {
            self.trends.clear();
            self.last_epoch = Some(state.session_epoch);
        }
    }

    fn draw_tile(
        &mut self,
        hdc: HDC,
        tile: &Tile,
        r: TileRect,
        state: &LiveState,
        now_ms: u64,
        scale: f64,
    ) -> u64 {
        let rect = RectI::new(r.x, r.y, r.w, r.h);
        let accent = tile_accent(tile, state);
        // SAFETY: GDI calls on `hdc` with objects created and released here.
        unsafe {
            round_rect(hdc, rect, SURFACE, BORDER, (6.0 * scale) as i32);
            fill(
                hdc,
                rect.x + 1,
                rect.y + (6.0 * scale) as i32,
                (3.0 * scale).max(2.0) as i32,
                rect.h - (12.0 * scale) as i32,
                accent,
            );
        }
        let text = format_tile(&tile.metric, state, tile.unit.as_deref(), tile.precision);
        let mark = tile_mark(tile, text.as_ref(), state, now_ms);
        let Some(text) = text else {
            self.centered(hdc, "Unknown tile", rect, UNAVAILABLE, 12.0 * scale, 400);
            return mark;
        };
        let compact = rect.h < (116.0 * scale) as i32 || rect.w < (132.0 * scale) as i32;
        let dense = rect.h < (64.0 * scale) as i32 || rect.w < (88.0 * scale) as i32;
        let inner = rect.inset((10.0 * scale) as i32, (6.0 * scale) as i32);
        let mut body = inner;
        if !dense {
            let label = label_of(&tile.metric);
            let label_h = (14.0 * scale) as i32;
            self.text(
                hdc,
                label,
                RectI {
                    h: label_h,
                    ..inner
                },
                TEXT_MUTED,
                (11.0 * scale).max(10.0),
                500,
                DT_LEFT | DT_SINGLELINE | DT_END_ELLIPSIS | DT_NOPREFIX,
            );
            body = RectI {
                y: inner.y + label_h,
                h: inner.h - label_h,
                ..inner
            };
        }
        let muted = text.state != TileState::Value;
        let main_color = if muted { UNAVAILABLE } else { TEXT };
        let style = if body.w < (62.0 * scale) as i32 || body.h < (46.0 * scale) as i32 {
            Style::Number
        } else {
            tile.style
        };

        match style {
            Style::Number => self.number(hdc, &text, body, main_color, scale, compact),
            Style::Status => self.status(
                hdc,
                &text,
                body,
                if muted { UNAVAILABLE } else { accent },
                scale,
            ),
            Style::Bar => self.bar(hdc, &text, body, main_color, accent, scale, tile),
            Style::Gauge => self.gauge(hdc, &text, body, main_color, accent, scale, tile),
            Style::Trend => {
                if let Some(v) = text.value {
                    self.trends
                        .entry(tile.tile_id.clone())
                        .or_default()
                        .push(now_ms, v);
                }
                let window = tile.trend_window.millis(state.last_lap.map(f64::from));
                let history: Vec<(u64, f64)> = self
                    .trends
                    .get(&tile.tile_id)
                    .map(|b| b.window(now_ms, window))
                    .unwrap_or_default();
                self.trend(
                    hdc, &text, body, main_color, accent, scale, tile, &history, now_ms, window,
                );
            }
        }
        mark
    }

    fn number(
        &mut self,
        hdc: HDC,
        text: &TileText,
        body: RectI,
        color: u32,
        scale: f64,
        compact: bool,
    ) {
        let muted = text.state != TileState::Value;
        let font = if muted {
            // "Not measured", "Insufficient evidence", "Waiting": short words, not a headline.
            (f64::from(body.h) * 0.2).clamp(12.0 * scale, 17.0 * scale)
        } else {
            (f64::from(body.h) * 0.43).clamp(17.0 * scale, 32.0 * scale)
        };
        let primary_flags = if muted {
            DT_CENTER | DT_VCENTER | DT_WORDBREAK | DT_END_ELLIPSIS | DT_NOPREFIX
        } else {
            DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS | DT_NOPREFIX
        };
        let secondary_h = if text.secondary.is_some() && !compact {
            (16.0 * scale) as i32
        } else {
            0
        };
        let main = RectI {
            h: body.h - secondary_h,
            ..body
        };
        self.text(
            hdc,
            &text.primary,
            main,
            color,
            font,
            if muted { 500 } else { 600 },
            primary_flags,
        );
        if let (Some(s), true) = (&text.secondary, secondary_h > 0) {
            let sec = RectI {
                y: body.y + main.h,
                h: secondary_h,
                ..body
            };
            self.text(
                hdc,
                s,
                sec,
                TEXT_MUTED,
                (11.0 * scale).max(10.0),
                400,
                DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS | DT_NOPREFIX,
            );
        }
    }

    fn status(&mut self, hdc: HDC, text: &TileText, body: RectI, color: u32, scale: f64) {
        let font = (f64::from(body.h) * 0.17).clamp(11.0 * scale, 16.0 * scale);
        let mut rect = body;
        if let Some(s) = &text.secondary {
            let sec_h = (14.0 * scale) as i32;
            rect.h -= sec_h;
            let sec = RectI {
                y: body.y + rect.h,
                h: sec_h,
                ..body
            };
            self.text(
                hdc,
                s,
                sec,
                TEXT_MUTED,
                (10.0 * scale).max(9.0),
                400,
                DT_LEFT | DT_SINGLELINE | DT_END_ELLIPSIS | DT_NOPREFIX,
            );
        }
        self.text(
            hdc,
            &text.primary,
            rect,
            color,
            font,
            600,
            DT_LEFT | DT_WORDBREAK | DT_END_ELLIPSIS | DT_NOPREFIX,
        );
    }

    #[allow(clippy::too_many_arguments)]
    fn bar(
        &mut self,
        hdc: HDC,
        text: &TileText,
        body: RectI,
        color: u32,
        accent: u32,
        scale: f64,
        tile: &Tile,
    ) {
        let track_h = ((9.0 * scale) as i32).max(5);
        let gap = (5.0 * scale) as i32;
        let sec_h = if text.secondary.is_some() && body.h >= (78.0 * scale) as i32 {
            (13.0 * scale) as i32
        } else {
            0
        };
        let value_h = (body.h - track_h - gap - sec_h).max(10);
        self.text(
            hdc,
            &text.primary,
            RectI { h: value_h, ..body },
            color,
            (f64::from(value_h) * 0.8).clamp(14.0 * scale, 26.0 * scale),
            600,
            DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS | DT_NOPREFIX,
        );
        let track = RectI {
            x: body.x,
            y: body.y + body.h - track_h,
            w: body.w,
            h: track_h,
        };
        let fraction = range_fraction(&tile.metric, text, &[]);
        // SAFETY: GDI calls with objects created and released here.
        unsafe {
            round_rect(hdc, track, BORDER, BORDER, track_h);
            if let Some(f) = fraction.filter(|f| *f > 0.0) {
                let w = ((f64::from(track.w) * f.clamp(0.0, 1.0)) as i32).max(track_h.min(track.w));
                round_rect(hdc, RectI { w, ..track }, accent, accent, track_h);
            }
        }
        if let (Some(s), true) = (&text.secondary, sec_h > 0) {
            let sec = RectI {
                x: body.x,
                y: body.y + value_h,
                w: body.w,
                h: sec_h,
            };
            self.text(
                hdc,
                s,
                sec,
                TEXT_MUTED,
                (10.0 * scale).max(9.0),
                400,
                DT_LEFT | DT_SINGLELINE | DT_END_ELLIPSIS | DT_NOPREFIX,
            );
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn gauge(
        &mut self,
        hdc: HDC,
        text: &TileText,
        body: RectI,
        color: u32,
        accent: u32,
        scale: f64,
        tile: &Tile,
    ) {
        let size = (body.w.min(body.h) - 2).clamp((34.0 * scale) as i32, (108.0 * scale) as i32);
        let stroke = (f64::from(size) * 0.085)
            .clamp(4.0 * scale, 8.0 * scale)
            .round() as i32;
        let cx = body.x + body.w / 2;
        let cy = body.y + body.h / 2;
        let r = size / 2 - stroke;
        let fraction = range_fraction(&tile.metric, text, &[])
            .unwrap_or(0.0)
            .clamp(0.0, 0.9999);
        // SAFETY: GDI calls with objects created and released here.
        unsafe {
            SetArcDirection(hdc, AD_CLOCKWISE);
            arc(hdc, cx, cy, r, stroke, 135.0, 405.0 - 0.01, BORDER);
            if fraction > 0.0 {
                arc(
                    hdc,
                    cx,
                    cy,
                    r,
                    stroke,
                    135.0,
                    135.0 + 270.0 * fraction,
                    accent,
                );
            }
        }
        self.text(
            hdc,
            &text.primary,
            RectI::new(cx - r, cy - r / 2, 2 * r, r),
            color,
            (f64::from(size) * 0.28).max(11.0),
            600,
            DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX,
        );
    }

    #[allow(clippy::too_many_arguments)]
    fn trend(
        &mut self,
        hdc: HDC,
        text: &TileText,
        body: RectI,
        color: u32,
        accent: u32,
        scale: f64,
        tile: &Tile,
        history: &[(u64, f64)],
        now_ms: u64,
        window_ms: u64,
    ) {
        let head_h = (body.h as f64 * 0.38).clamp(16.0 * scale, 28.0 * scale) as i32;
        self.text(
            hdc,
            &text.primary,
            RectI { h: head_h, ..body },
            color,
            f64::from(head_h) * 0.7,
            600,
            DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS | DT_NOPREFIX,
        );
        let area = RectI {
            y: body.y + head_h,
            h: body.h - head_h,
            ..body
        };
        if history.len() < 2 || area.h < 8 {
            return;
        }
        let values: Vec<f64> = history.iter().map(|(_, v)| *v).collect();
        let (lo, hi) =
            display_range(&tile.metric, text, &values).unwrap_or_else(|| min_max(&values));
        let span = if (hi - lo).abs() < f64::EPSILON {
            1.0
        } else {
            hi - lo
        };
        let start = now_ms.saturating_sub(window_ms);
        let points: Vec<POINT> = history
            .iter()
            .map(|(t, v)| POINT {
                x: area.x
                    + ((t.saturating_sub(start)) as f64 / window_ms.max(1) as f64
                        * f64::from(area.w)) as i32,
                y: area.y + area.h - (((v - lo) / span).clamp(0.0, 1.0) * f64::from(area.h)) as i32,
            })
            .collect();
        // SAFETY: pen created, selected and deleted here.
        unsafe {
            let pen = CreatePen(
                PS_SOLID,
                ((1.5 * scale).round() as i32).max(1),
                colorref(accent),
            );
            let old = SelectObject(hdc, HGDIOBJ(pen.0));
            let _ = Polyline(hdc, &points);
            SelectObject(hdc, old);
            let _ = DeleteObject(HGDIOBJ(pen.0));
        }
    }

    fn centered(&mut self, hdc: HDC, s: &str, rect: RectI, color: u32, px: f64, weight: i32) {
        self.text(
            hdc,
            s,
            rect,
            color,
            px,
            weight,
            DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX,
        );
    }

    #[allow(clippy::too_many_arguments)]
    fn text(
        &mut self,
        hdc: HDC,
        s: &str,
        rect: RectI,
        color: u32,
        px: f64,
        weight: i32,
        flags: DRAW_TEXT_FORMAT,
    ) {
        if rect.w <= 0 || rect.h <= 0 {
            return;
        }
        let font = self.font(px.round() as i32, weight);
        let mut buf: Vec<u16> = s.encode_utf16().collect();
        let mut r = RECT {
            left: rect.x,
            top: rect.y,
            right: rect.x + rect.w,
            bottom: rect.y + rect.h,
        };
        // SAFETY: the font is owned by the cache; the previous object is restored.
        unsafe {
            let old = SelectObject(hdc, HGDIOBJ(font.0));
            SetTextColor(hdc, colorref(color));
            DrawTextW(hdc, &mut buf, &mut r, flags);
            SelectObject(hdc, old);
        }
    }

    fn font(&mut self, px: i32, weight: i32) -> HFONT {
        *self.fonts.entry((px, weight)).or_insert_with(|| {
            let face = wide("Segoe UI");
            // SAFETY: the face buffer is NUL-terminated and outlives the call.
            unsafe {
                CreateFontW(
                    -px.max(8),
                    0,
                    0,
                    0,
                    weight,
                    0,
                    0,
                    0,
                    DEFAULT_CHARSET,
                    OUT_DEFAULT_PRECIS,
                    CLIP_DEFAULT_PRECIS,
                    CLEARTYPE_QUALITY,
                    (DEFAULT_PITCH.0 | FF_DONTCARE.0) as u32,
                    windows::core::PCWSTR(face.as_ptr()),
                )
            }
        })
    }
}

impl Drop for Renderer {
    fn drop(&mut self) {
        for font in self.fonts.values() {
            // SAFETY: each font was created by `font` and is deleted once.
            unsafe {
                let _ = DeleteObject(HGDIOBJ(font.0));
            }
        }
    }
}

#[derive(Debug, Clone, Copy)]
struct RectI {
    x: i32,
    y: i32,
    w: i32,
    h: i32,
}

impl RectI {
    fn new(x: i32, y: i32, w: i32, h: i32) -> RectI {
        RectI { x, y, w, h }
    }
    fn inset(self, dx: i32, dy: i32) -> RectI {
        RectI {
            x: self.x + dx,
            y: self.y + dy,
            w: self.w - 2 * dx,
            h: self.h - 2 * dy,
        }
    }
}

/// # Safety
/// `hdc` must be a valid device context.
unsafe fn fill(hdc: HDC, x: i32, y: i32, w: i32, h: i32, rgb: u32) {
    let rect = RECT {
        left: x,
        top: y,
        right: x + w.max(0),
        bottom: y + h.max(0),
    };
    // SAFETY: the brush is created and deleted here.
    unsafe {
        let brush = CreateSolidBrush(colorref(rgb));
        FillRect(hdc, &rect, brush);
        let _ = DeleteObject(HGDIOBJ(brush.0));
    }
}

/// # Safety
/// `hdc` must be a valid device context.
unsafe fn round_rect(hdc: HDC, r: RectI, fill_rgb: u32, border_rgb: u32, radius: i32) {
    // SAFETY: pen and brush are selected, used and restored here.
    unsafe {
        let pen = CreatePen(PS_SOLID, 1, colorref(border_rgb));
        let brush = CreateSolidBrush(colorref(fill_rgb));
        let old_pen = SelectObject(hdc, HGDIOBJ(pen.0));
        let old_brush = SelectObject(hdc, HGDIOBJ(brush.0));
        let _ = RoundRect(
            hdc,
            r.x,
            r.y,
            r.x + r.w,
            r.y + r.h,
            radius.max(2),
            radius.max(2),
        );
        SelectObject(hdc, old_pen);
        SelectObject(hdc, old_brush);
        let _ = DeleteObject(HGDIOBJ(pen.0));
        let _ = DeleteObject(HGDIOBJ(brush.0));
    }
}

/// An arc from `from_deg` to `to_deg` (clockwise on screen, measured from the +x axis).
///
/// # Safety
/// `hdc` must be a valid device context.
#[allow(clippy::too_many_arguments)]
unsafe fn arc(
    hdc: HDC,
    cx: i32,
    cy: i32,
    r: i32,
    stroke: i32,
    from_deg: f64,
    to_deg: f64,
    rgb: u32,
) {
    let point = |deg: f64| {
        let rad = deg.to_radians();
        (
            cx + (f64::from(r) * rad.cos()).round() as i32,
            cy + (f64::from(r) * rad.sin()).round() as i32,
        )
    };
    let (x3, y3) = point(from_deg);
    let (x4, y4) = point(to_deg);
    // SAFETY: the pen is selected, used and restored here.
    unsafe {
        let pen = CreatePen(PS_SOLID, stroke, colorref(rgb));
        let old = SelectObject(hdc, HGDIOBJ(pen.0));
        let _ = Arc(hdc, cx - r, cy - r, cx + r, cy + r, x3, y3, x4, y4);
        SelectObject(hdc, old);
        let _ = DeleteObject(HGDIOBJ(pen.0));
    }
}

/// The rectangle of every tile of `layout` in a `width` x `height` window at `scale`.
pub fn tile_rects(layout: &Layout, width: i32, height: i32, scale: f64) -> Vec<TileRect> {
    let pad = (f64::from(PADDING) * scale).round() as i32;
    let gap = (f64::from(GAP) * scale).round() as i32;
    layout
        .tiles
        .iter()
        .map(|tile| layout.tile_rect(tile, width, height, pad, gap))
        .collect()
}

/// A fingerprint of everything `draw_tile` puts on the surface for this tile besides its fixed
/// layout: the text, the state, the accent and the bar or gauge position (to 1/256 of the range).
/// A trend tile with a value also carries the time in 100 ms steps, because its line scrolls.
fn tile_mark(tile: &Tile, text: Option<&TileText>, state: &LiveState, now_ms: u64) -> u64 {
    let mut h = std::collections::hash_map::DefaultHasher::new();
    tile_accent(tile, state).hash(&mut h);
    match text {
        None => 0u8.hash(&mut h),
        Some(text) => {
            1u8.hash(&mut h);
            text.primary.hash(&mut h);
            text.secondary.hash(&mut h);
            (text.state as u8).hash(&mut h);
            match tile.style {
                Style::Bar | Style::Gauge => {
                    range_fraction(&tile.metric, text, &[])
                        .map(|f| (f.clamp(0.0, 1.0) * 256.0) as i32)
                        .hash(&mut h);
                }
                Style::Trend if text.value.is_some() => (now_ms / 100).hash(&mut h),
                _ => {}
            }
        }
    }
    h.finish()
}

fn min_max(values: &[f64]) -> (f64, f64) {
    let lo = values.iter().copied().fold(f64::INFINITY, f64::min);
    let hi = values.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    let pad = (hi - lo).max(1e-9) * 0.08;
    (lo - pad, hi + pad)
}

/// Position of the tile's value in its display range (0 to 1), None without a value.
fn range_fraction(tile_id: &str, text: &TileText, trend: &[f64]) -> Option<f64> {
    let value = text.value?;
    let (lo, hi) = display_range(tile_id, text, trend)?;
    (hi > lo).then(|| (value - lo) / (hi - lo))
}

fn tile_accent(tile: &Tile, state: &LiveState) -> u32 {
    if tile.metric == "coach-cue"
        && let Some(cue) = state.cue
    {
        return match cue.priority {
            Priority::Critical => Accent::Coral,
            Priority::Strategy | Priority::PitService => Accent::Amber,
            Priority::Traffic => Accent::Violet,
            Priority::Pace => Accent::Blue,
            Priority::Coaching => Accent::Green,
            _ => tile.accent,
        }
        .rgb();
    }
    tile.accent.rgb()
}

/// A short label for a catalog tile id (`last-lap` -> `Last lap`).
fn label_of(metric: &str) -> &str {
    coach_wire::live_channels::TILES
        .iter()
        .find(|t| t.id == metric)
        .map_or(metric, |t| t.label)
}
