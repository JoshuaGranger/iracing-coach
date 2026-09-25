using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

// The main window opened at a fixed 1440x900 centred on a 1280x800 screen: it came up
// at -80,-74 with the title bar and navigation off-screen, so it could not be moved or
// maximized with the mouse. Placement now fits a current monitor's work area.
[TestClass]
public sealed class MainWindowPlacementTests
{
    [TestMethod]
    public void Screen1280x800_FitsTheWorkAreaWithTheTitleBarOnScreen()
    {
        var display = new DisplayWorkArea(0, 0, 1280, 752, 1, IsPrimary: true);
        var plan = MainWindowPlacement.Resolve([display], saved: null);

        AssertInside(display, plan);
        Assert.AreEqual(0, plan.Left);
        Assert.AreEqual(0, plan.Top, "The title bar must not start above the screen.");
        Assert.AreEqual(1280, plan.Width);
        Assert.AreEqual(752, plan.Height);
        Assert.AreEqual(900, plan.MinWidth);
        Assert.AreEqual(640, plan.MinHeight);
        Assert.IsFalse(plan.Maximized);

        var noTaskbar = new DisplayWorkArea(0, 0, 1280, 800, 1, IsPrimary: true);
        var full = MainWindowPlacement.Resolve([noTaskbar], null);
        Assert.AreEqual((0d, 0d, 1280d, 800d), (full.Left, full.Top, full.Width, full.Height));
    }

    [TestMethod]
    public void Screen1920x1080_UsesTheDesignSizeCentredInTheWorkArea()
    {
        var display = new DisplayWorkArea(0, 0, 1920, 1032, 1, IsPrimary: true);
        var plan = MainWindowPlacement.Resolve([display], null);

        AssertInside(display, plan);
        Assert.AreEqual((240d, 66d, 1440d, 900d), (plan.Left, plan.Top, plan.Width, plan.Height));
    }

    [TestMethod]
    public void Screen2560x1440_UsesTheDesignSizeCentredInTheWorkArea()
    {
        var display = new DisplayWorkArea(0, 0, 2560, 1392, 1, IsPrimary: true);
        var plan = MainWindowPlacement.Resolve([display], null);

        AssertInside(display, plan);
        Assert.AreEqual((560d, 246d, 1440d, 900d), (plan.Left, plan.Top, plan.Width, plan.Height));
    }

    [TestMethod]
    public void Scale150Percent_ConvertsTheDesignSizeAndKeepsTheMinimumReachable()
    {
        // 1920x1080 at 150%: 1440x900 DIPs is 2160x1350 device pixels, larger than the screen.
        var hd = new DisplayWorkArea(0, 0, 1920, 1032, 1.5, IsPrimary: true);
        var hdPlan = MainWindowPlacement.Resolve([hd], null);
        AssertInside(hd, hdPlan);
        Assert.AreEqual((0d, 0d, 1920d, 1032d), (hdPlan.Left, hdPlan.Top, hdPlan.Width, hdPlan.Height));
        Assert.AreEqual(900, hdPlan.MinWidth);
        Assert.AreEqual(640, hdPlan.MinHeight);

        // 1280x800 at 150% is only 853x501 DIPs: the 900x640 minimum would force the
        // window off-screen, so the minimum shrinks to the work area.
        var small = new DisplayWorkArea(0, 0, 1280, 752, 1.5, IsPrimary: true);
        var smallPlan = MainWindowPlacement.Resolve([small], null);
        AssertInside(small, smallPlan);
        Assert.AreEqual(1280 / 1.5, smallPlan.MinWidth, 1e-9);
        Assert.AreEqual(752 / 1.5, smallPlan.MinHeight, 1e-9);
        Assert.IsLessThanOrEqualTo(small.Width, smallPlan.MinWidth * small.Scale - 1e-6);
        Assert.IsLessThanOrEqualTo(small.Height, smallPlan.MinHeight * small.Scale - 1e-6);

        var qhd = new DisplayWorkArea(0, 0, 2560, 1392, 1.5, IsPrimary: true);
        var qhdPlan = MainWindowPlacement.Resolve([qhd], null);
        AssertInside(qhd, qhdPlan);
        Assert.AreEqual((200d, 21d, 2160d, 1350d), (qhdPlan.Left, qhdPlan.Top, qhdPlan.Width, qhdPlan.Height));
    }

    [TestMethod]
    public void NegativeOriginSecondaryMonitor_KeepsItsPlacementAndDefaultsToThePrimary()
    {
        var secondary = new DisplayWorkArea(-1280, -200, 1280, 984, 1);
        var primary = new DisplayWorkArea(0, 0, 1920, 1032, 1, IsPrimary: true);
        IReadOnlyList<DisplayWorkArea> displays = [secondary, primary];

        var fresh = MainWindowPlacement.Resolve(displays, null);
        Assert.AreEqual(primary, fresh.Display, "Without a remembered placement the window opens on the primary monitor.");
        AssertInside(primary, fresh);

        var onSecondary = MainWindowPlacement.Resolve(displays, new SavedWindowPlacement(-1200, -150, 1000, 700));
        Assert.IsTrue(onSecondary.RestoredSaved);
        Assert.AreEqual(secondary, onSecondary.Display);
        Assert.AreEqual((-1200d, -150d, 1000d, 700d), (onSecondary.Left, onSecondary.Top, onSecondary.Width, onSecondary.Height));

        var hangingOff = MainWindowPlacement.Resolve(displays, new SavedWindowPlacement(-1400, -260, 1000, 700));
        Assert.IsTrue(hangingOff.RestoredSaved);
        Assert.AreEqual(secondary, hangingOff.Display);
        AssertInside(secondary, hangingOff);
        Assert.AreEqual((-1280d, -200d), (hangingOff.Left, hangingOff.Top));

        var tooBig = MainWindowPlacement.Resolve(displays, new SavedWindowPlacement(-1270, -190, 2000, 1400));
        Assert.AreEqual(secondary, tooBig.Display);
        AssertInside(secondary, tooBig);
    }

    [TestMethod]
    public void SavedPlacementOffEveryCurrentMonitor_IsForgotten()
    {
        var primary = new DisplayWorkArea(0, 0, 1920, 1032, 1, IsPrimary: true);

        // Saved on a monitor that is no longer connected.
        var disconnected = MainWindowPlacement.Resolve([primary], new SavedWindowPlacement(2200, 100, 1440, 900, Maximized: true));
        Assert.IsFalse(disconnected.RestoredSaved);
        Assert.IsFalse(disconnected.Maximized, "A maximized state from a missing monitor is not restored either.");
        Assert.AreEqual((240d, 66d, 1440d, 900d), (disconnected.Left, disconnected.Top, disconnected.Width, disconnected.Height));

        // The exact state reported on the 1280x800 sim screen is pulled back on-screen.
        var small = new DisplayWorkArea(0, 0, 1280, 752, 1, IsPrimary: true);
        var reported = MainWindowPlacement.Resolve([small], new SavedWindowPlacement(-80, -74, 1300, 820));
        Assert.IsTrue(reported.RestoredSaved);
        AssertInside(small, reported);
        Assert.AreEqual((0d, 0d, 1280d, 752d), (reported.Left, reported.Top, reported.Width, reported.Height));

        foreach (var invalid in new[]
        {
            new SavedWindowPlacement(double.NaN, 0, 1000, 700),
            new SavedWindowPlacement(0, double.PositiveInfinity, 1000, 700),
            new SavedWindowPlacement(0, 0, 0, 700),
            new SavedWindowPlacement(0, 0, 1000, -5)
        })
        {
            var plan = MainWindowPlacement.Resolve([primary], invalid);
            Assert.IsFalse(plan.RestoredSaved);
            AssertInside(primary, plan);
        }
    }

    [TestMethod]
    public void SavedPlacementOnACurrentMonitor_RestoresSizePositionAndMaximizedState()
    {
        var primary = new DisplayWorkArea(0, 0, 2560, 1392, 1, IsPrimary: true);
        var saved = MainWindowPlacement.Resolve([primary], new SavedWindowPlacement(100, 80, 1600, 1000, Maximized: true));
        Assert.IsTrue(saved.RestoredSaved);
        Assert.IsTrue(saved.Maximized);
        Assert.AreEqual((100d, 80d, 1600d, 1000d), (saved.Left, saved.Top, saved.Width, saved.Height));

        var tiny = MainWindowPlacement.Resolve([primary], new SavedWindowPlacement(100, 80, 200, 100));
        Assert.AreEqual((900d, 640d), (tiny.Width, tiny.Height), "A remembered size below the minimum is raised to it.");
    }

    // Maximize, then minimize to the tray: the window rectangle read during the maximize
    // (WindowState still Normal) was the maximized frame, and it was saved as the restore
    // bounds, so the next launch or restore opened a full-screen-sized normal window.
    [TestMethod]
    public void MaximizeThenTray_RemembersTheNormalFrameNotTheMaximizedOne()
    {
        var display = new DisplayWorkArea(0, 0, 2560, 1392, 1, IsPrimary: true);
        // GetWindowPlacement while maximized: the normal position is still the 1600x1000
        // frame the driver chose, even though GetWindowRect reports the whole work area.
        var remembered = MainWindowPlacement.NormalBoundsOnScreen(new NativeNormalPosition(
            300, 200, 1900, 1200, MonitorLeft: 0, MonitorTop: 0, WorkLeft: 0, WorkTop: 0));
        Assert.AreEqual(new SavedWindowPlacement(300, 200, 1600, 1000), remembered);

        var reopened = MainWindowPlacement.Resolve([display], remembered! with { Maximized = true });
        Assert.IsTrue(reopened.Maximized);
        Assert.AreEqual((300d, 200d, 1600d, 1000d), (reopened.Left, reopened.Top, reopened.Width, reopened.Height),
            "Un-maximizing after a restart returns to the driver's normal frame, not a full-screen one.");
    }

    [TestMethod]
    public void NormalBounds_ConvertWorkspaceCoordinatesToScreenCoordinates()
    {
        // Taskbar docked left (48 px): workspace x 100 is screen x 148.
        Assert.AreEqual(new SavedWindowPlacement(148, 80, 1000, 700), MainWindowPlacement.NormalBoundsOnScreen(
            new NativeNormalPosition(100, 80, 1100, 780, MonitorLeft: 0, MonitorTop: 0, WorkLeft: 48, WorkTop: 0)));

        // Negative-origin secondary monitor with its taskbar on top (40 px).
        Assert.AreEqual(new SavedWindowPlacement(-1200, -150, 1000, 700), MainWindowPlacement.NormalBoundsOnScreen(
            new NativeNormalPosition(-1200, -190, -200, 510, MonitorLeft: -1280, MonitorTop: -200, WorkLeft: -1280, WorkTop: -160)));

        // Tool windows report screen coordinates already.
        Assert.AreEqual(new SavedWindowPlacement(100, 80, 1000, 700), MainWindowPlacement.NormalBoundsOnScreen(
            new NativeNormalPosition(100, 80, 1100, 780, 0, 0, 48, 40, WorkspaceCoordinates: false)));

        Assert.IsNull(MainWindowPlacement.NormalBoundsOnScreen(new NativeNormalPosition(100, 80, 100, 780, 0, 0, 0, 0)));
        Assert.IsNull(MainWindowPlacement.NormalBoundsOnScreen(new NativeNormalPosition(double.NaN, 80, 1100, 780, 0, 0, 0, 0)));
    }

    [TestMethod]
    public void AppComposition_RemembersTheNormalFrameFromWindowPlacement()
    {
        var root = TestRepositoryPaths.CompanionAppRoot;
        var window = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.App", "MainWindow.xaml.cs"));
        var displays = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.App", "DisplayWorkAreas.cs"));

        var remember = Method(window, "private void RememberNormalBounds");
        StringAssert.Contains(remember, "DisplayWorkAreas.TryGetNormalBounds(");
        Assert.DoesNotContain("TryGetWindowBounds", remember, "The window rectangle is the maximized frame during a maximize.");
        Assert.DoesNotContain("WindowState", remember, "WindowState still reads Normal while the maximize is in progress.");
        StringAssert.Contains(Method(window, "private void SavePlacement"), "RememberNormalBounds();");

        var normal = Method(displays, "public static bool TryGetNormalBounds");
        StringAssert.Contains(normal, "GetWindowPlacement(window, ref placement)");
        StringAssert.Contains(normal, "placement.NormalPosition");
        StringAssert.Contains(normal, "MainWindowPlacement.NormalBoundsOnScreen(");
        Assert.DoesNotContain("GetWindowRect", normal);
    }

    private static string Method(string source, string signature)
    {
        var start = source.IndexOf(signature, StringComparison.Ordinal);
        Assert.IsGreaterThanOrEqualTo(0, start, signature);
        var body = source[start..];
        return body[..body.IndexOf("\n    }", StringComparison.Ordinal)];
    }

    [TestMethod]
    public void NoMonitorInformation_StillReturnsAReachablePlacement()
    {
        var plan = MainWindowPlacement.Resolve([], new SavedWindowPlacement(5000, 5000, 1440, 900));
        Assert.AreEqual((0d, 0d), (plan.Left, plan.Top));
        Assert.IsFalse(plan.RestoredSaved);
    }

    [TestMethod]
    public void PlacementStore_RoundTripsAndIgnoresUnreadableOrInvalidState()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-placement-" + Guid.NewGuid().ToString("N"));
        var store = new MainWindowPlacementStore(Path.Combine(root, "main-window-placement.json"));
        try
        {
            Assert.IsNull(store.Load());
            store.Save(new SavedWindowPlacement(-1200, -150, 1000, 700, Maximized: true));
            Assert.AreEqual(new SavedWindowPlacement(-1200, -150, 1000, 700, Maximized: true), store.Load());

            store.Save(new SavedWindowPlacement(double.NaN, 0, 1000, 700));
            Assert.AreEqual(-1200, store.Load()!.Left, "Invalid bounds are never written.");

            File.WriteAllText(store.Path, "{broken");
            Assert.IsNull(store.Load());
            File.WriteAllText(store.Path, "{\"schemaVersion\":2,\"left\":1,\"top\":1,\"width\":800,\"height\":600}");
            Assert.IsNull(store.Load());
            File.WriteAllText(store.Path, "{\"schemaVersion\":1,\"left\":1,\"top\":1,\"width\":0,\"height\":600}");
            Assert.IsNull(store.Load());
        }
        finally
        {
            if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
        }
    }

    [TestMethod]
    public void AppComposition_SizesTheMainWindowToTheWorkAreaAndActivatesOnFirstShow()
    {
        var root = TestRepositoryPaths.CompanionAppRoot;
        var xaml = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.App", "MainWindow.xaml"));
        var window = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.App", "MainWindow.xaml.cs"));

        Assert.DoesNotContain("Width=\"1440\"", xaml, "A fixed design width opens off-screen on smaller displays.");
        Assert.DoesNotContain("Height=\"900\"", xaml, "A fixed design height opens off-screen on smaller displays.");
        Assert.DoesNotContain("CenterScreen", xaml, "Centring an oversized window puts its title bar above the screen.");
        StringAssert.Contains(xaml, "WindowStartupLocation=\"Manual\"");
        StringAssert.Contains(window, "MainWindowPlacement.Resolve(");
        StringAssert.Contains(window, "ApplyStartupPlacement();");
        StringAssert.Contains(window, "ContentRendered += OnFirstContentRendered;");
        var firstShow = window[window.IndexOf("private void OnFirstContentRendered", StringComparison.Ordinal)..];
        firstShow = firstShow[..firstShow.IndexOf("\n    }", StringComparison.Ordinal)];
        StringAssert.Contains(firstShow, "KeepOnCurrentMonitor();");
        StringAssert.Contains(firstShow, "Activate();");
        StringAssert.Contains(firstShow, "Topmost = true; Topmost = false;");
    }

    private static void AssertInside(DisplayWorkArea display, WindowPlacementPlan plan)
    {
        Assert.IsGreaterThanOrEqualTo(display.Left, plan.Left, "left edge");
        Assert.IsGreaterThanOrEqualTo(display.Top, plan.Top, "title bar");
        Assert.IsLessThanOrEqualTo(display.Right, plan.Right, "right edge");
        Assert.IsLessThanOrEqualTo(display.Bottom, plan.Bottom, "bottom edge");
        Assert.IsGreaterThan(0, plan.Width);
        Assert.IsGreaterThan(0, plan.Height);
        Assert.IsLessThanOrEqualTo(display.Width, plan.MinWidth * display.Scale - 1e-6, "The minimum width must fit the work area.");
        Assert.IsLessThanOrEqualTo(display.Height, plan.MinHeight * display.Scale - 1e-6, "The minimum height must fit the work area.");
    }
}
