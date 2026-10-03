//! The overlay's layout model: the server's `LiveLayoutBody` parsed into plain types, the built-in
//! default, grid geometry and trend history. Portable (no Win32) so it is tested everywhere.

use std::collections::VecDeque;

use serde_json::Value;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Style {
    Number,
    Gauge,
    Bar,
    Trend,
    Status,
}

impl Style {
    fn parse(s: &str) -> Option<Style> {
        Some(match s {
            "number" => Style::Number,
            "gauge" => Style::Gauge,
            "bar" => Style::Bar,
            "trend" => Style::Trend,
            "status" => Style::Status,
            _ => return None,
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Accent {
    Default,
    Blue,
    Green,
    Amber,
    Coral,
    Violet,
}

impl Accent {
    fn parse(s: &str) -> Accent {
        match s {
            "blue" => Accent::Blue,
            "green" => Accent::Green,
            "amber" => Accent::Amber,
            "coral" => Accent::Coral,
            "violet" => Accent::Violet,
            _ => Accent::Default,
        }
    }

    /// Colors from `config/theme.dark.json` (`accent`, `info`, `success`, `warning`, `danger`,
    /// steering violet), as 0xRRGGBB.
    pub fn rgb(self) -> u32 {
        match self {
            Accent::Default => 0x5CE8C3,
            Accent::Blue => 0x61DAF3,
            Accent::Green => 0x57EEA0,
            Accent::Amber => 0xFFD05C,
            Accent::Coral => 0xFF7187,
            Accent::Violet => 0xC89CFF,
        }
    }
}

/// Trend history window; `s15`, `s30`, `s60`, `one_lap`, `three_laps`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TrendWindow {
    S15,
    S30,
    S60,
    OneLap,
    ThreeLaps,
}

impl TrendWindow {
    fn parse(s: &str) -> TrendWindow {
        match s {
            "s15" => TrendWindow::S15,
            "s60" => TrendWindow::S60,
            "one_lap" => TrendWindow::OneLap,
            "three_laps" => TrendWindow::ThreeLaps,
            _ => TrendWindow::S30,
        }
    }

    /// Window length in ms; laps use the last lap time when known, else 90 s.
    pub fn millis(self, last_lap_s: Option<f64>) -> u64 {
        let lap = last_lap_s
            .filter(|s| s.is_finite() && *s > 5.0)
            .unwrap_or(90.0);
        match self {
            TrendWindow::S15 => 15_000,
            TrendWindow::S30 => 30_000,
            TrendWindow::S60 => 60_000,
            TrendWindow::OneLap => (lap * 1000.0) as u64,
            TrendWindow::ThreeLaps => (3.0 * lap * 1000.0) as u64,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Tile {
    pub tile_id: String,
    /// Tile id in the live catalog (`live-channels.json` `tiles[].id`).
    pub metric: String,
    pub row: u32,
    pub column: u32,
    pub row_span: u32,
    pub column_span: u32,
    pub style: Style,
    pub unit: Option<String>,
    pub precision: Option<u8>,
    pub trend_window: TrendWindow,
    pub accent: Accent,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Layout {
    pub rows: u32,
    pub columns: u32,
    pub tiles: Vec<Tile>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Rect {
    pub x: i32,
    pub y: i32,
    pub w: i32,
    pub h: i32,
}

/// Overlay size at scale 1.0 (the old monitor's grid viewport).
pub const BASE_WIDTH: i32 = 540;
pub const BASE_HEIGHT: i32 = 360;
pub const PADDING: i32 = 8;
pub const GAP: i32 = 6;

impl Layout {
    /// The built-in default (`factory-default`): 2 rows x 3 columns.
    pub fn default_layout() -> Layout {
        let tile = |metric: &str, row, column, style, accent| Tile {
            tile_id: metric.to_string(),
            metric: metric.to_string(),
            row,
            column,
            row_span: 1,
            column_span: 1,
            style,
            unit: None,
            precision: None,
            trend_window: TrendWindow::S30,
            accent,
        };
        Layout {
            rows: 2,
            columns: 3,
            tiles: vec![
                tile("position", 0, 0, Style::Number, Accent::Default),
                tile("last-lap", 0, 1, Style::Number, Accent::Default),
                tile("leader-gap", 0, 2, Style::Number, Accent::Default),
                tile("fuel", 1, 0, Style::Bar, Accent::Amber),
                tile("pit-window", 1, 1, Style::Number, Accent::Default),
                tile("coach-cue", 1, 2, Style::Status, Accent::Default),
            ],
        }
    }

    /// Parses a `LiveLayoutBody`. A grid outside 1 to 8 is rejected; a tile that does not fit the
    /// grid or has an unknown style is skipped. None when nothing usable remains.
    pub fn from_json(value: &Value) -> Option<Layout> {
        let rows = value
            .get("rows")?
            .as_u64()
            .filter(|r| (1..=8).contains(r))? as u32;
        let columns = value
            .get("columns")?
            .as_u64()
            .filter(|c| (1..=8).contains(c))? as u32;
        let mut tiles = Vec::new();
        for t in value.get("tiles")?.as_array()?.iter().take(64) {
            let get_u = |k: &str| t.get(k).and_then(Value::as_u64);
            let (Some(row), Some(column), Some(row_span), Some(column_span)) = (
                get_u("row"),
                get_u("column"),
                get_u("row_span"),
                get_u("column_span"),
            ) else {
                continue;
            };
            let Some(style) = t
                .get("style")
                .and_then(Value::as_str)
                .and_then(Style::parse)
            else {
                continue;
            };
            let metric = t.get("metric").and_then(Value::as_str).unwrap_or_default();
            if metric.is_empty()
                || row_span == 0
                || column_span == 0
                || row + row_span > u64::from(rows)
                || column + column_span > u64::from(columns)
            {
                continue;
            }
            tiles.push(Tile {
                tile_id: t
                    .get("tile_id")
                    .and_then(Value::as_str)
                    .unwrap_or(metric)
                    .to_string(),
                metric: metric.to_string(),
                row: row as u32,
                column: column as u32,
                row_span: row_span as u32,
                column_span: column_span as u32,
                style,
                unit: t.get("unit").and_then(Value::as_str).map(str::to_string),
                precision: t
                    .get("precision")
                    .and_then(Value::as_u64)
                    .map(|p| p.min(3) as u8),
                trend_window: TrendWindow::parse(
                    t.get("trend_window").and_then(Value::as_str).unwrap_or(""),
                ),
                accent: Accent::parse(t.get("accent").and_then(Value::as_str).unwrap_or("")),
            });
        }
        (!tiles.is_empty()).then_some(Layout {
            rows,
            columns,
            tiles,
        })
    }

    /// The tile's rectangle inside a window of `width` x `height`.
    pub fn tile_rect(&self, tile: &Tile, width: i32, height: i32, padding: i32, gap: i32) -> Rect {
        let rows = self.rows as i32;
        let cols = self.columns as i32;
        let cell_w = (width - 2 * padding - gap * (cols - 1)) as f64 / f64::from(cols);
        let cell_h = (height - 2 * padding - gap * (rows - 1)) as f64 / f64::from(rows);
        let x = padding as f64 + f64::from(tile.column as i32) * (cell_w + f64::from(gap));
        let y = padding as f64 + f64::from(tile.row as i32) * (cell_h + f64::from(gap));
        let w = cell_w * f64::from(tile.column_span as i32)
            + f64::from(gap * (tile.column_span as i32 - 1));
        let h =
            cell_h * f64::from(tile.row_span as i32) + f64::from(gap * (tile.row_span as i32 - 1));
        Rect {
            x: x.round() as i32,
            y: y.round() as i32,
            w: w.round() as i32,
            h: h.round() as i32,
        }
    }
}

/// A rolling `(time, value)` history for trend tiles; only finite values are kept.
#[derive(Debug, Default, Clone)]
pub struct TrendBuffer {
    points: VecDeque<(u64, f64)>,
}

/// Longest history kept (the longest window is three laps; 10 minutes bounds it).
const MAX_HISTORY_MS: u64 = 10 * 60 * 1000;

impl TrendBuffer {
    pub fn push(&mut self, t_ms: u64, value: f64) {
        if !value.is_finite() {
            return;
        }
        self.points.push_back((t_ms, value));
        while self
            .points
            .front()
            .is_some_and(|(t, _)| t_ms.saturating_sub(*t) > MAX_HISTORY_MS)
        {
            self.points.pop_front();
        }
        while self.points.len() > 12_000 {
            self.points.pop_front();
        }
    }

    pub fn clear(&mut self) {
        self.points.clear();
    }

    /// Points within the last `window_ms` before `now_ms`.
    pub fn window(&self, now_ms: u64, window_ms: u64) -> Vec<(u64, f64)> {
        self.points
            .iter()
            .filter(|(t, _)| now_ms.saturating_sub(*t) <= window_ms)
            .copied()
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn parses_a_server_layout_and_skips_bad_tiles() {
        let layout = Layout::from_json(&json!({
            "rows": 2, "columns": 2,
            "tiles": [
                {"tile_id": "a", "metric": "speed", "row": 0, "column": 0, "row_span": 1, "column_span": 2, "style": "trend", "unit": "mph", "precision": 1, "trend_window": "s60", "accent": "blue"},
                {"tile_id": "b", "metric": "gear", "row": 1, "column": 1, "row_span": 2, "column_span": 1, "style": "number"},
                {"tile_id": "c", "metric": "rpm", "row": 1, "column": 0, "row_span": 1, "column_span": 1, "style": "sparkle"},
                {"tile_id": "d", "metric": "lap", "row": 1, "column": 0, "row_span": 1, "column_span": 1, "style": "number"}
            ]
        }))
        .unwrap();
        assert_eq!(layout.tiles.len(), 2);
        assert_eq!(layout.tiles[0].style, Style::Trend);
        assert_eq!(layout.tiles[0].trend_window, TrendWindow::S60);
        assert_eq!(layout.tiles[0].accent, Accent::Blue);
        assert_eq!(layout.tiles[1].metric, "lap");
        assert!(Layout::from_json(&json!({"rows": 9, "columns": 2, "tiles": []})).is_none());
        assert!(Layout::from_json(&json!({"rows": 1, "columns": 1, "tiles": []})).is_none());
    }

    #[test]
    fn default_layout_is_the_factory_default() {
        let l = Layout::default_layout();
        assert_eq!((l.rows, l.columns, l.tiles.len()), (2, 3, 6));
        assert_eq!(l.tiles[5].metric, "coach-cue");
        assert_eq!(l.tiles[5].style, Style::Status);
    }

    #[test]
    fn tile_rects_tile_the_window() {
        let l = Layout::default_layout();
        let a = l.tile_rect(&l.tiles[0], 540, 360, 8, 6);
        let last = l.tile_rect(&l.tiles[5], 540, 360, 8, 6);
        assert_eq!((a.x, a.y), (8, 8));
        assert_eq!(last.x + last.w, 540 - 8);
        assert_eq!(last.y + last.h, 360 - 8);
        let wide = Tile {
            column_span: 3,
            ..l.tiles[0].clone()
        };
        let r = l.tile_rect(&wide, 540, 360, 8, 6);
        assert_eq!(r.x + r.w, 532);
    }

    #[test]
    fn trend_buffer_windows_and_ignores_non_finite() {
        let mut b = TrendBuffer::default();
        for i in 0..100u64 {
            b.push(i * 1000, i as f64);
        }
        b.push(100_000, f64::NAN);
        assert_eq!(b.window(99_000, 15_000).len(), 16);
        assert_eq!(TrendWindow::OneLap.millis(Some(60.0)), 60_000);
        assert_eq!(TrendWindow::ThreeLaps.millis(None), 270_000);
        b.clear();
        assert!(b.window(0, 1_000_000).is_empty());
    }
}
