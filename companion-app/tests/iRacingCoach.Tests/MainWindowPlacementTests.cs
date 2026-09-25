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
