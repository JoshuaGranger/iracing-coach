//! Shared native dialog appearance, read directly from the canonical Coach theme.

use std::{marker::PhantomData, rc::Rc, sync::OnceLock};
use windows::Win32::Foundation::{HWND, LPARAM, LRESULT, RECT, WPARAM};
use windows::Win32::Graphics::Dwm::{DWMWA_USE_IMMERSIVE_DARK_MODE, DwmSetWindowAttribute};
use windows::Win32::Graphics::Gdi::*;
use windows::Win32::UI::Controls::{
    DRAWITEMSTRUCT, ODS_DISABLED, ODS_FOCUS, ODS_SELECTED, SetWindowTheme,
};
use windows::Win32::UI::HiDpi::{
    DPI_AWARENESS_CONTEXT, DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2, SetThreadDpiAwarenessContext,
};
use windows::Win32::UI::WindowsAndMessaging::{GetWindowTextW, SendMessageW, WM_SETFONT};
use windows::core::PCWSTR;

use super::{render::colorref, wide};

/// Applies per-monitor-v2 awareness only while creating a native window. Windows retains that
/// window's awareness after this scope ends; the caller's thread and process settings stay intact.
/// The guard cannot move to another thread, because restoring a different thread would be wrong.
pub struct DpiAwarenessGuard {
    previous: DPI_AWARENESS_CONTEXT,
    _thread: PhantomData<Rc<()>>,
}

impl DpiAwarenessGuard {
    pub fn per_monitor_v2() -> windows::core::Result<Self> {
        // SAFETY: changes only this thread and records its previous valid context for Drop.
        let previous =
            unsafe { SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) };
        if previous.0.is_null() {
            return Err(windows::core::Error::from_thread());
        }
        Ok(Self {
            previous,
            _thread: PhantomData,
        })
    }
}

impl Drop for DpiAwarenessGuard {
    fn drop(&mut self) {
        // SAFETY: this non-Send guard is dropped on the thread that supplied this context.
        unsafe {
            SetThreadDpiAwarenessContext(self.previous);
        }
    }
}

pub struct Palette {
    pub app: u32,
    pub surface: u32,
    pub surface_hover: u32,
    pub border: u32,
    pub text: u32,
    pub muted: u32,
    pub accent: u32,
    pub accent_fill: u32,
    pub success: u32,
    pub danger: u32,
    pub warning: u32,
    pub hover_ms: u32,
}

pub fn palette() -> &'static Palette {
    static COLORS: OnceLock<Palette> = OnceLock::new();
    COLORS.get_or_init(|| {
        let source: serde_json::Value =
            serde_json::from_str(include_str!("../../../config/theme.dark.json"))
                .expect("canonical theme JSON");
        let color = |key: &str| {
            u32::from_str_radix(
                source["colors"][key]
                    .as_str()
                    .expect("canonical color")
                    .trim_start_matches('#'),
                16,
            )
            .expect("canonical RGB")
        };
        Palette {
            app: color("app"),
            surface: color("surface2"),
            surface_hover: color("surfaceHover"),
            border: color("borderStrong"),
            text: color("textPrimary"),
            muted: color("textMuted"),
            accent: color("accent"),
            accent_fill: color("accentFill"),
            success: color("success"),
            danger: color("danger"),
            warning: color("warning"),
            hover_ms: source["motionMs"]["hover"]
                .as_u64()
                .expect("canonical hover duration") as u32,
        }
    })
}

pub struct DialogTheme {
    pub background: HBRUSH,
    pub surface: HBRUSH,
    pub font: HFONT,
}

impl DialogTheme {
    pub fn new(dpi: u32) -> Self {
        let colors = palette();
        // SAFETY: owned GDI objects, deselected by their callers and deleted in Drop.
        unsafe {
            Self {
                background: CreateSolidBrush(colorref(colors.app)),
                surface: CreateSolidBrush(colorref(colors.surface)),
                font: CreateFontW(
                    -((14 * dpi / 96).max(11) as i32),
                    0,
                    0,
                    0,
                    400,
                    0,
                    0,
                    0,
                    DEFAULT_CHARSET,
                    OUT_DEFAULT_PRECIS,
                    CLIP_DEFAULT_PRECIS,
                    CLEARTYPE_QUALITY,
                    0,
                    PCWSTR(wide("Segoe UI").as_ptr()),
                ),
            }
        }
    }

    pub fn control_colors(&self, dc: HDC, surface: bool) -> LRESULT {
        // SAFETY: WM_CTLCOLOR supplies a borrowed valid DC; brushes outlive dialog children.
        unsafe {
            SetTextColor(dc, colorref(palette().text));
            SetBkColor(
                dc,
                colorref(if surface {
                    palette().surface
                } else {
                    palette().app
                }),
            );
            LRESULT(if surface {
                self.surface.0 as isize
            } else {
                self.background.0 as isize
            })
        }
    }

    pub fn apply_font(&self, window: HWND) {
        // SAFETY: the dialog owns the control and keeps this font alive.
        unsafe {
            SendMessageW(
                window,
                WM_SETFONT,
                Some(WPARAM(self.font.0 as usize)),
                Some(LPARAM(1)),
            );
        }
    }

    pub fn paint_button(&self, item: &DRAWITEMSTRUCT, primary: bool) {
        let colors = palette();
        let disabled = item.itemState.0 & ODS_DISABLED.0 != 0;
        let pressed = item.itemState.0 & ODS_SELECTED.0 != 0;
        let background = if pressed {
            colors.surface_hover
        } else if primary {
            colors.accent_fill
        } else {
            colors.surface
        };
        let foreground = if disabled { colors.muted } else { colors.text };
        // SAFETY: owner-draw button supplies a valid DC and bounds. Temporary objects restored.
        unsafe {
            FillRect(item.hDC, &item.rcItem, self.background);
            let brush = CreateSolidBrush(colorref(background));
            let pen = CreatePen(
                PS_SOLID,
                1,
                colorref(if disabled {
                    colors.surface
                } else {
                    colors.border
                }),
            );
            let old_brush = SelectObject(item.hDC, HGDIOBJ(brush.0));
            let old_pen = SelectObject(item.hDC, HGDIOBJ(pen.0));
            let _ = RoundRect(
                item.hDC,
                item.rcItem.left,
                item.rcItem.top,
                item.rcItem.right,
                item.rcItem.bottom,
                8,
                8,
            );
            SelectObject(item.hDC, old_brush);
            SelectObject(item.hDC, old_pen);
            let _ = DeleteObject(HGDIOBJ(brush.0));
            let _ = DeleteObject(HGDIOBJ(pen.0));
            let font = SelectObject(item.hDC, HGDIOBJ(self.font.0));
            SetBkMode(item.hDC, TRANSPARENT);
            SetTextColor(item.hDC, colorref(foreground));
            let mut buffer = [0u16; 256];
            let len = GetWindowTextW(item.hwndItem, &mut buffer).max(0) as usize;
            let mut bounds = item.rcItem;
            DrawTextW(
                item.hDC,
                &mut buffer[..len],
                &mut bounds,
                DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS,
            );
            if item.itemState.0 & ODS_FOCUS.0 != 0 {
                let focus = RECT {
                    left: bounds.left + 4,
                    top: bounds.top + 4,
                    right: bounds.right - 4,
                    bottom: bounds.bottom - 4,
                };
                let _ = DrawFocusRect(item.hDC, &focus);
            }
            SelectObject(item.hDC, font);
        }
    }
}

impl Drop for DialogTheme {
    fn drop(&mut self) {
        // SAFETY: dialog children have been destroyed before the theme is dropped.
        unsafe {
            let _ = DeleteObject(HGDIOBJ(self.background.0));
            let _ = DeleteObject(HGDIOBJ(self.surface.0));
            let _ = DeleteObject(HGDIOBJ(self.font.0));
        }
    }
}

pub fn apply_titlebar(window: HWND) {
    let dark: i32 = 1;
    // SAFETY: copies a BOOL immediately; older Windows versions can ignore this appearance hint.
    unsafe {
        let _ = DwmSetWindowAttribute(
            window,
            DWMWA_USE_IMMERSIVE_DARK_MODE,
            &dark as *const _ as _,
            std::mem::size_of_val(&dark) as u32,
        );
    }
}

pub fn style_control(window: HWND) {
    // SAFETY: native style hint only. WM_CTLCOLOR / owner draw supply canonical dialog colors.
    unsafe {
        let _ = SetWindowTheme(
            window,
            PCWSTR(wide("DarkMode_Explorer").as_ptr()),
            PCWSTR::null(),
        );
    }
}

#[cfg(test)]
pub(crate) fn save_test_bitmap(window: HWND, path: &std::path::Path) {
    use windows::Win32::UI::WindowsAndMessaging::GetClientRect;
    // SAFETY: captures only a test-created window. All temporary GDI objects are released.
    unsafe {
        let mut bounds = RECT::default();
        GetClientRect(window, &mut bounds).unwrap();
        let dc = GetDC(Some(window));
        let memory = CreateCompatibleDC(Some(dc));
        let bitmap = CreateCompatibleBitmap(dc, bounds.right, bounds.bottom);
        let old = SelectObject(memory, HGDIOBJ(bitmap.0));
        BitBlt(
            memory,
            0,
            0,
            bounds.right,
            bounds.bottom,
            Some(dc),
            0,
            0,
            SRCCOPY,
        )
        .unwrap();
        SelectObject(memory, old);
        let mut info = BITMAPINFO {
            bmiHeader: BITMAPINFOHEADER {
                biSize: 40,
                biWidth: bounds.right,
                biHeight: -bounds.bottom,
                biPlanes: 1,
                biBitCount: 32,
                biCompression: BI_RGB.0,
                ..Default::default()
            },
            ..Default::default()
        };
        let mut pixels = vec![0u8; (bounds.right * bounds.bottom * 4) as usize];
        assert_ne!(
            GetDIBits(
                memory,
                bitmap,
                0,
                bounds.bottom as u32,
                Some(pixels.as_mut_ptr().cast()),
                &mut info,
                DIB_RGB_COLORS
            ),
            0
        );
        let mut bytes = Vec::new();
        bytes.extend_from_slice(b"BM");
        bytes.extend_from_slice(&(54 + pixels.len() as u32).to_le_bytes());
        bytes.extend_from_slice(&0u32.to_le_bytes());
        bytes.extend_from_slice(&54u32.to_le_bytes());
        bytes.extend_from_slice(&40u32.to_le_bytes());
        bytes.extend_from_slice(&bounds.right.to_le_bytes());
        bytes.extend_from_slice(&(-bounds.bottom).to_le_bytes());
        bytes.extend_from_slice(&1u16.to_le_bytes());
        bytes.extend_from_slice(&32u16.to_le_bytes());
        bytes.extend_from_slice(&[0; 24]);
        bytes.extend_from_slice(&pixels);
        std::fs::write(path, bytes).unwrap();
        let _ = DeleteObject(HGDIOBJ(bitmap.0));
        let _ = DeleteDC(memory);
        ReleaseDC(Some(window), dc);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use windows::Win32::UI::HiDpi::{
        AreDpiAwarenessContextsEqual, DPI_AWARENESS_CONTEXT_SYSTEM_AWARE,
        GetThreadDpiAwarenessContext,
    };

    #[test]
    fn scoped_per_monitor_v2_restores_the_callers_native_thread_context() {
        // SAFETY: this test changes only its own GUI test thread. Both nested guards restore
        // their predecessors even if an assertion fails; no process-wide setting is changed.
        unsafe {
            let previous = SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_SYSTEM_AWARE);
            assert!(!previous.0.is_null());
            let _restore = DpiAwarenessGuard {
                previous,
                _thread: PhantomData,
            };
            {
                let _dpi = DpiAwarenessGuard::per_monitor_v2().unwrap();
                assert!(
                    AreDpiAwarenessContextsEqual(
                        GetThreadDpiAwarenessContext(),
                        DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
                    )
                    .as_bool()
                );
                {
                    let _nested = DpiAwarenessGuard::per_monitor_v2().unwrap();
                }
                assert!(
                    AreDpiAwarenessContextsEqual(
                        GetThreadDpiAwarenessContext(),
                        DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
                    )
                    .as_bool()
                );
            }
            assert!(
                AreDpiAwarenessContextsEqual(
                    GetThreadDpiAwarenessContext(),
                    DPI_AWARENESS_CONTEXT_SYSTEM_AWARE,
                )
                .as_bool()
            );
        }
    }
}
