using System.Runtime.InteropServices;
using iRacingCoach.Coordinator;

namespace iRacingCoach.App;

// Monitor work areas and scales in this process's own device-pixel space, so they
// can be compared directly with GetWindowRect and applied with SetWindowPos.
internal static class DisplayWorkAreas
{
    private const uint MonitorInfoPrimary = 1;
    private const int EffectiveDpi = 0;

    public static IReadOnlyList<DisplayWorkArea> Current(double fallbackScale)
    {
        var result = new List<DisplayWorkArea>();
        try
        {
            EnumDisplayMonitors(IntPtr.Zero, IntPtr.Zero, (IntPtr monitor, IntPtr deviceContext, ref Rect bounds, IntPtr data) =>
            {
                var info = new MonitorInfo { Size = Marshal.SizeOf<MonitorInfo>() };
                if (!GetMonitorInfo(monitor, ref info)) return true;
                var scale = fallbackScale;
                try
                {
                    if (GetDpiForMonitor(monitor, EffectiveDpi, out var dpiX, out _) == 0 && dpiX > 0) scale = dpiX / 96d;
                }
                catch (Exception ex) when (ex is DllNotFoundException or EntryPointNotFoundException) { }
                var work = info.Work;
                result.Add(new DisplayWorkArea(work.Left, work.Top, work.Right - work.Left, work.Bottom - work.Top,
                    scale, (info.Flags & MonitorInfoPrimary) != 0));
                return true;
            }, IntPtr.Zero);
        }
        catch (Exception ex) when (ex is DllNotFoundException or EntryPointNotFoundException) { }
        return result;
    }

    public static bool TryGetWindowBounds(IntPtr window, out SavedWindowPlacement bounds)
    {
        bounds = new SavedWindowPlacement(0, 0, 0, 0);
        if (window == IntPtr.Zero || !GetWindowRect(window, out var rect)) return false;
        bounds = new SavedWindowPlacement(rect.Left, rect.Top, rect.Right - rect.Left, rect.Bottom - rect.Top);
        return bounds.Width > 0 && bounds.Height > 0;
    }

    public static void MoveWindow(IntPtr window, WindowPlacementPlan plan)
    {
        const uint noZOrder = 0x0004, noActivate = 0x0010;
        if (window == IntPtr.Zero) return;
        _ = SetWindowPos(window, IntPtr.Zero, (int)plan.Left, (int)plan.Top, (int)plan.Width, (int)plan.Height, noZOrder | noActivate);
    }

    private delegate bool MonitorEnumProc(IntPtr monitor, IntPtr deviceContext, ref Rect bounds, IntPtr data);

    [StructLayout(LayoutKind.Sequential)]
    private struct Rect { public int Left, Top, Right, Bottom; }

    [StructLayout(LayoutKind.Sequential)]
    private struct MonitorInfo { public int Size; public Rect Monitor; public Rect Work; public uint Flags; }

    [DllImport("user32.dll")] private static extern bool EnumDisplayMonitors(IntPtr deviceContext, IntPtr clip, MonitorEnumProc callback, IntPtr data);
    [DllImport("user32.dll")] private static extern bool GetMonitorInfo(IntPtr monitor, ref MonitorInfo info);
    [DllImport("shcore.dll")] private static extern int GetDpiForMonitor(IntPtr monitor, int dpiType, out uint dpiX, out uint dpiY);
    [DllImport("user32.dll", SetLastError = true)] private static extern bool GetWindowRect(IntPtr window, out Rect rect);
    [DllImport("user32.dll", SetLastError = true)] private static extern bool SetWindowPos(IntPtr window, IntPtr insertAfter, int x, int y, int width, int height, uint flags);
}
