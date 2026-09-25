using System.Text.Json;

namespace iRacingCoach.Coordinator;

/// <summary>A monitor's work area (excluding the taskbar) in device pixels, and its scale in device pixels per DIP.</summary>
public readonly record struct DisplayWorkArea(double Left, double Top, double Width, double Height, double Scale = 1, bool IsPrimary = false)
{
    public double Right => Left + Width;
    public double Bottom => Top + Height;
}

/// <summary>The main window's last normal (restore) bounds in device pixels.</summary>
public sealed record SavedWindowPlacement(double Left, double Top, double Width, double Height, bool Maximized = false);

/// <summary>
/// A window's normal (restore) rectangle as GetWindowPlacement reports it, and the
/// monitor and work-area origins of the monitor the window is on, all in device pixels.
/// A top-level window that is not a tool window reports it in workspace coordinates.
/// </summary>
public readonly record struct NativeNormalPosition(
    double Left,
    double Top,
    double Right,
    double Bottom,
    double MonitorLeft,
    double MonitorTop,
    double WorkLeft,
    double WorkTop,
    bool WorkspaceCoordinates = true);

/// <summary>
/// Where the main window opens: restore bounds in device pixels, and the minimum size in
/// DIPs that still fits the chosen work area.
/// </summary>
public sealed record WindowPlacementPlan(
    double Left,
    double Top,
    double Width,
    double Height,
    bool Maximized,
    double MinWidth,
    double MinHeight,
    DisplayWorkArea Display,
    bool RestoredSaved)
{
    public double Right => Left + Width;
    public double Bottom => Top + Height;
}

/// <summary>
/// The main window used to open at a fixed 1440x900 centred on the screen. On a
/// 1280x800 display that put the title bar and navigation above the screen, where it
/// could not be moved or maximized with the mouse. The window now fits the work area
/// of a current monitor, and a remembered placement is used only while it is still on one.
/// </summary>
public static class MainWindowPlacement
{
    public const double DesiredWidth = 1440;
    public const double DesiredHeight = 900;
    public const double MinimumWidth = 900;
    public const double MinimumHeight = 640;

    public static WindowPlacementPlan Resolve(
        IReadOnlyList<DisplayWorkArea> displays,
        SavedWindowPlacement? saved,
        double desiredWidth = DesiredWidth,
        double desiredHeight = DesiredHeight,
        double minimumWidth = MinimumWidth,
        double minimumHeight = MinimumHeight)
    {
        ArgumentNullException.ThrowIfNull(displays);
        var usable = displays
            .Where(display => Finite(display.Left, display.Top, display.Width, display.Height) && display.Width >= 1 && display.Height >= 1)
            .Select(display => display with { Scale = double.IsFinite(display.Scale) && display.Scale > 0 ? display.Scale : 1 })
            .ToArray();
        if (usable.Length == 0)
            // No monitor information: an ordinary window at the origin is still reachable.
            return new WindowPlacementPlan(0, 0, desiredWidth, desiredHeight, false, minimumWidth, minimumHeight, new DisplayWorkArea(0, 0, desiredWidth, desiredHeight), false);

        if (IsUsable(saved) && Host(usable, saved!) is { } host)
            return Fit(host, saved!.Left, saved.Top, saved.Width, saved.Height, saved.Maximized, minimumWidth, minimumHeight, restoredSaved: true);

        var primary = usable.FirstOrDefault(display => display.IsPrimary);
        if (!primary.IsPrimary) primary = usable.FirstOrDefault(display => Contains(display, 0, 0));
        if (primary.Width < 1) primary = usable[0];
        var width = Math.Min(desiredWidth * primary.Scale, primary.Width);
        var height = Math.Min(desiredHeight * primary.Scale, primary.Height);
        return Fit(primary,
            primary.Left + (primary.Width - width) / 2,
            primary.Top + (primary.Height - height) / 2,
            width, height, maximized: false, minimumWidth, minimumHeight, restoredSaved: false);
    }

    /// <summary>
    /// The window's normal frame in screen device pixels, which is what is remembered.
    /// The window rectangle is not: while a maximize is in progress WPF still reports
    /// <c>WindowState.Normal</c> and the window rectangle is already the maximized frame,
    /// so remembering it reopened the window at full-screen size. The normal position is
    /// the restore frame in every state, maximized and minimized included.
    /// </summary>
    public static SavedWindowPlacement? NormalBoundsOnScreen(NativeNormalPosition position)
    {
        // Workspace coordinates leave out a taskbar docked to the left or top of the
        // window's monitor; add that offset back to reach screen coordinates.
        var offsetX = position.WorkspaceCoordinates ? position.WorkLeft - position.MonitorLeft : 0;
        var offsetY = position.WorkspaceCoordinates ? position.WorkTop - position.MonitorTop : 0;
        var bounds = new SavedWindowPlacement(
            position.Left + offsetX,
            position.Top + offsetY,
            position.Right - position.Left,
            position.Bottom - position.Top);
        return IsUsable(bounds) ? bounds : null;
    }

    private static bool IsUsable(SavedWindowPlacement? saved) =>
        saved is not null && Finite(saved.Left, saved.Top, saved.Width, saved.Height) && saved.Width >= 1 && saved.Height >= 1;

    // The monitor that holds the saved window's centre, else the one it overlaps most.
    // A placement that overlaps no current monitor is forgotten.
    private static DisplayWorkArea? Host(IReadOnlyList<DisplayWorkArea> displays, SavedWindowPlacement saved)
    {
        var centreX = saved.Left + saved.Width / 2;
        var centreY = saved.Top + saved.Height / 2;
        foreach (var display in displays)
            if (Contains(display, centreX, centreY)) return display;
        var best = displays
            .Select(display => (Display: display, Overlap: Overlap(display, saved)))
            .OrderByDescending(item => item.Overlap)
            .First();
        return best.Overlap > 0 ? best.Display : null;
    }

    private static WindowPlacementPlan Fit(
        DisplayWorkArea display,
        double left,
        double top,
        double width,
        double height,
        bool maximized,
        double minimumWidth,
        double minimumHeight,
        bool restoredSaved)
    {
        // Whole device pixels, never larger than the work area, and a minimum size (in
        // DIPs, as WPF enforces it) that the work area can actually hold.
        var maxWidth = Math.Floor(display.Width);
        var maxHeight = Math.Floor(display.Height);
        var minWidthDips = Math.Min(minimumWidth, maxWidth / display.Scale);
        var minHeightDips = Math.Min(minimumHeight, maxHeight / display.Scale);
        var minWidth = Math.Min(Math.Ceiling(minWidthDips * display.Scale - 1e-6), maxWidth);
        var minHeight = Math.Min(Math.Ceiling(minHeightDips * display.Scale - 1e-6), maxHeight);
        var fittedWidth = Math.Floor(SafeClamp(width, minWidth, maxWidth));
        var fittedHeight = Math.Floor(SafeClamp(height, minHeight, maxHeight));
        var fittedLeft = SafeClamp(Math.Round(left), Math.Ceiling(display.Left), Math.Floor(display.Right - fittedWidth));
        var fittedTop = SafeClamp(Math.Round(top), Math.Ceiling(display.Top), Math.Floor(display.Bottom - fittedHeight));
        return new WindowPlacementPlan(fittedLeft, fittedTop, fittedWidth, fittedHeight, maximized,
            minWidthDips, minHeightDips, display, restoredSaved);
    }

    private static double SafeClamp(double value, double minimum, double maximum) =>
        maximum < minimum ? minimum : Math.Clamp(value, minimum, maximum);

    private static bool Contains(DisplayWorkArea display, double x, double y) =>
        x >= display.Left && x < display.Right && y >= display.Top && y < display.Bottom;

    private static double Overlap(DisplayWorkArea display, SavedWindowPlacement saved)
    {
        var width = Math.Min(display.Right, saved.Left + saved.Width) - Math.Max(display.Left, saved.Left);
        var height = Math.Min(display.Bottom, saved.Top + saved.Height) - Math.Max(display.Top, saved.Top);
        return width > 0 && height > 0 ? width * height : 0;
    }

    private static bool Finite(params double[] values) => values.All(double.IsFinite);
}

/// <summary>
/// Machine-local memory of the main window's last placement. It lives in app-owned
/// local state, and anything unreadable is simply not used.
/// </summary>
public sealed class MainWindowPlacementStore(string path)
{
    private const int SchemaVersion = 1;
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);

    public string Path { get; } = System.IO.Path.GetFullPath(path);

    public SavedWindowPlacement? Load()
    {
        try
        {
            if (!File.Exists(Path)) return null;
            using var document = JsonDocument.Parse(File.ReadAllBytes(Path));
            var root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object
                || !root.TryGetProperty("schemaVersion", out var version)
                || !version.TryGetInt32(out var schemaVersion)
                || schemaVersion != SchemaVersion)
                return null;
            var saved = root.Deserialize<SavedWindowPlacement>(Json);
            return saved is not null
                && double.IsFinite(saved.Left) && double.IsFinite(saved.Top)
                && double.IsFinite(saved.Width) && double.IsFinite(saved.Height)
                && saved.Width >= 1 && saved.Height >= 1
                    ? saved
                    : null;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException or InvalidOperationException or FormatException) { return null; }
    }

    public void Save(SavedWindowPlacement placement)
    {
        ArgumentNullException.ThrowIfNull(placement);
        if (!double.IsFinite(placement.Left) || !double.IsFinite(placement.Top)
            || !double.IsFinite(placement.Width) || !double.IsFinite(placement.Height)
            || placement.Width < 1 || placement.Height < 1)
            return;
        var temporary = Path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            Directory.CreateDirectory(System.IO.Path.GetDirectoryName(Path)!);
            File.WriteAllText(temporary, JsonSerializer.Serialize(new
            {
                schemaVersion = SchemaVersion,
                left = placement.Left,
                top = placement.Top,
                width = placement.Width,
                height = placement.Height,
                maximized = placement.Maximized
            }, Json));
            File.Move(temporary, Path, overwrite: true);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
        finally
        {
            try { if (File.Exists(temporary)) File.Delete(temporary); }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
        }
    }
}
