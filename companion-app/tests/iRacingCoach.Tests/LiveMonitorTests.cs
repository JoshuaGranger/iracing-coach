using System.Diagnostics;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class LiveMonitorTests
{
    [TestMethod]
    public void FactoryLayouts_AreNamedAndDefaultToSixTilesInAThreeByTwoGrid()
    {
        var choices = LiveMonitorLayouts.Choices(new LiveMonitorLayout());
        CollectionAssert.AreEqual(new[] { "Default", "Race", "Qualifying" }, choices.Take(3).Select(choice => choice.Layout.Name).ToArray());
        Assert.IsTrue(choices.Take(3).All(choice => choice.IsFactory));
        Assert.IsTrue(choices.Take(3).All(choice => choice.Layout is { Rows: 2, Columns: 3 } && choice.Layout.Tiles.Count == 6));
    }

    [TestMethod]
    public void LayoutOperations_ReflowDeterministicallyWithoutOverlap()
    {
        var preferences = new LiveMonitorLayout();
        var layout = LiveMonitorLayouts.EnsureEditable(preferences);
        Assert.IsTrue(LiveMonitorLayouts.TryResizeGrid(layout, 3, 3, out _));
        Assert.IsTrue(LiveMonitorLayouts.TryResizeTile(layout, layout.Tiles[0].Id, 1, 2));
        Assert.IsTrue(LiveMonitorLayouts.TryMoveTile(layout, layout.Tiles[0].Id, 1, 1));
        AssertNoOverlap(layout);
        var firstPlacement = layout.Tiles.Select(tile => (tile.Id, tile.Row, tile.Column, tile.RowSpan, tile.ColumnSpan)).ToArray();
        var clone = LiveMonitorLayouts.Clone(layout);
        Assert.IsTrue(LiveMonitorLayouts.TryResizeGrid(clone, 3, 3, out _));
        CollectionAssert.AreEqual(firstPlacement, clone.Tiles.Select(tile => (tile.Id, tile.Row, tile.Column, tile.RowSpan, tile.ColumnSpan)).ToArray());
    }

    [TestMethod]
    public void TelemetryCatalog_IsAlphabetizedTypedAndNeverSubstitutesZeroForMissingData()
    {
        var names = LiveTelemetryCatalog.All.Select(definition => definition.Name).ToArray();
        CollectionAssert.AreEqual(names.OrderBy(name => name, StringComparer.CurrentCultureIgnoreCase).ToArray(), names);
        Assert.IsGreaterThanOrEqualTo(25, LiveTelemetryCatalog.All.Count);
        Assert.IsTrue(LiveTelemetryCatalog.All.All(definition => definition.Styles.Count > 0 && definition.Units.Count > 0));
        var missing = LiveTelemetryCatalog.Read("speed", MissingState());
        Assert.IsFalse(missing.Available);
        Assert.AreNotEqual("0", missing.DisplayValue);
        StringAssert.Contains(missing.AvailabilityMessage, "Waiting");
    }

    [TestMethod]
    public void LegacyMonitorPreferences_MigrateToNamedLayoutAndKeepOriginalForSupport()
    {
        var directory = TestDirectory();
        var portable = Path.Combine(directory, "settings.json");
        var machine = Path.Combine(directory, "machine.json");
        File.WriteAllText(portable, """{"settingsSchemaVersion":3,"liveMonitor":{"positionLocked":false,"left":44,"top":55,"width":700,"monitorDeviceName":"DISPLAY-OLD","secondaryFields":["LeaderLap","Fuel","Weather"]}}""");
        var settings = new JsonSettingsStore(portable, new TestCredentialStore(Path.Combine(directory, "credential.dpapi")), machine).Load();
        Assert.AreEqual(4, settings.SettingsSchemaVersion);
        Assert.IsFalse(settings.LiveMonitor.IsLocked);
        Assert.AreEqual("Migrated 0.9.3", settings.LiveMonitor.UserLayouts.Single().Name);
        CollectionAssert.AreEqual(new[] { "leader-last-lap", "fuel", "track-temperature" }, settings.LiveMonitor.UserLayouts.Single().Tiles.Select(tile => tile.MetricId).ToArray());
        Assert.AreEqual(44d, settings.LiveMonitor.Left);
        Assert.AreEqual(1.25d, settings.LiveMonitor.OverallScale, .001);
        Assert.IsTrue(File.Exists(machine + ".v0.9.3-monitor.json"));
        Assert.DoesNotContain("DISPLAY-OLD", File.ReadAllText(portable));
    }

    [TestMethod]
    public void CorruptCustomLayout_FallsBackToFactoryAndPreservesRejectedData()
    {
        var directory = TestDirectory();
        var portable = Path.Combine(directory, "settings.json");
        var machine = Path.Combine(directory, "machine.json");
        File.WriteAllText(portable, """{"settingsSchemaVersion":4,"liveMonitor":{"activeLayoutId":"broken","userLayouts":[{"id":"broken","name":"My broken layout","rows":0,"columns":9,"tiles":[{"id":"x","metricId":"not-real"}]}]}}""");
        var settings = new JsonSettingsStore(portable, new TestCredentialStore(Path.Combine(directory, "credential.dpapi")), machine).Load();
        Assert.AreEqual(LiveMonitorLayout.FactoryDefaultId, settings.LiveMonitor.ActiveLayoutId);
        Assert.IsEmpty(settings.LiveMonitor.UserLayouts);
        var rejected = machine + ".rejected-monitor.json";
        Assert.IsTrue(File.Exists(rejected));
        StringAssert.Contains(File.ReadAllText(rejected), "My broken layout");
    }

    [TestMethod]
    public void CatalogAndLayoutOperations_StayWithinInteractivePerformanceBudget()
    {
        var preferences = new LiveMonitorLayout();
        var timer = Stopwatch.StartNew();
        for (var index = 0; index < 2_000; index++)
        {
            var layout = LiveMonitorLayouts.EnsureEditable(preferences);
            _ = LiveMonitorLayouts.TryMoveTile(layout, layout.Tiles[index % layout.Tiles.Count].Id, index % layout.Rows, index % layout.Columns);
            _ = LiveTelemetryCatalog.Read("speed", MissingState());
        }
        timer.Stop();
        Assert.IsLessThan(TimeSpan.FromSeconds(2), timer.Elapsed);
    }

    [TestMethod]
    public void LiveMonitorMarkup_ExposesRequiredIconControlsAndKeyboardGuidance()
    {
        var root = Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", ".."));
        var xaml = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.App", "LiveMonitorWindow.xaml"));
        foreach (var accessibleName in new[] { "Layout selector", "Unlock layout editing", "Grid and scale settings", "Close live monitor", "Search telemetry catalog", "Tile decimal precision", "Trend history duration", "Tile accent color" })
            StringAssert.Contains(xaml, accessibleName);
        StringAssert.Contains(xaml, "Alt+Arrow moves");
        StringAssert.Contains(xaml, "Shift+Arrow resizes");
    }

    private static void AssertNoOverlap(LiveMonitorNamedLayout layout)
    {
        var occupied = new HashSet<(int Row, int Column)>();
        foreach (var tile in layout.Tiles)
            for (var row = tile.Row; row < tile.Row + tile.RowSpan; row++)
                for (var column = tile.Column; column < tile.Column + tile.ColumnSpan; column++)
                    Assert.IsTrue(occupied.Add((row, column)), $"Overlap at {row},{column}");
    }

    private static string TestDirectory()
    {
        var directory = Path.Combine(Path.GetTempPath(), "iracing-coach-tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        return directory;
    }

    private static LiveMonitorState MissingState() => new(
        LiveTelemetryEngine.Disconnected(), new LiveMonitorLayout(), false, 0, 0, 0, DateTimeOffset.UtcNow);

    private sealed class TestCredentialStore(string path) : IGarage61CredentialStore
    {
        public bool IsConfigured => false;
        public string CredentialPath { get; } = path;
        public void Store(string token) { }
        public void Remove() { }
    }
}
