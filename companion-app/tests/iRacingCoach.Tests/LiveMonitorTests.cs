using System.Diagnostics;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class LiveMonitorTests
{
    [TestMethod]
    public void BuiltInLayouts_DefaultIsTheOnlyImmutableDashboardAndRaceModesAreSeededEditable()
    {
        var preferences = new LiveMonitorLayout();
        var choices = LiveMonitorLayouts.Choices(preferences);

        CollectionAssert.AreEqual(new[] { "Default", "Race", "Qualifying" }, choices.Take(3).Select(choice => choice.Layout.Name).ToArray());
        Assert.IsTrue(choices.Single(choice => choice.Layout.Id == LiveMonitorLayout.FactoryDefaultId).IsFactory);
        Assert.IsFalse(choices.Single(choice => choice.Layout.Id == LiveMonitorLayouts.FactoryRaceId).IsFactory);
        Assert.IsFalse(choices.Single(choice => choice.Layout.Id == LiveMonitorLayouts.FactoryQualifyingId).IsFactory);
        Assert.IsTrue(choices.Take(3).All(choice => choice.Layout is { Rows: 2, Columns: 3 } && choice.Layout.Tiles.Count == 6));
        CollectionAssert.AreEquivalent(
            new[] { LiveMonitorLayouts.FactoryRaceId, LiveMonitorLayouts.FactoryQualifyingId },
            preferences.UserLayouts.Select(layout => layout.Id).ToArray());
    }

    [TestMethod]
    public void SeededRaceAndQualifyingDashboards_RenameAndDeletePersistWithoutReseeding()
    {
        var directory = TestDirectory();
        var portable = Path.Combine(directory, "settings.json");
        var machine = Path.Combine(directory, "machine.json");
        var store = new JsonSettingsStore(portable, new TestCredentialStore(Path.Combine(directory, "credential.dpapi")), machine);
        var settings = new CompanionSettings();
        _ = LiveMonitorLayouts.Choices(settings.LiveMonitor);
        var race = settings.LiveMonitor.UserLayouts.Single(layout => layout.Id == LiveMonitorLayouts.FactoryRaceId);
        var qualifying = settings.LiveMonitor.UserLayouts.Single(layout => layout.Id == LiveMonitorLayouts.FactoryQualifyingId);
        race.Name = "Long run";
        qualifying.Name = "Time attack";
        store.Save(settings);

        var renamed = store.Load();
        Assert.AreEqual("Long run", renamed.LiveMonitor.UserLayouts.Single(layout => layout.Id == LiveMonitorLayouts.FactoryRaceId).Name);
        Assert.AreEqual("Time attack", renamed.LiveMonitor.UserLayouts.Single(layout => layout.Id == LiveMonitorLayouts.FactoryQualifyingId).Name);

        renamed.LiveMonitor.ActiveLayoutId = LiveMonitorLayouts.FactoryRaceId;
        Assert.IsTrue(LiveMonitorLayouts.DeleteActive(renamed.LiveMonitor));
        store.Save(renamed);

        var afterDelete = store.Load();
        var choices = LiveMonitorLayouts.Choices(afterDelete.LiveMonitor);
        Assert.IsFalse(choices.Any(choice => choice.Layout.Id == LiveMonitorLayouts.FactoryRaceId), "A deleted seeded dashboard must stay deleted after reload.");
        Assert.AreEqual("Time attack", choices.Single(choice => choice.Layout.Id == LiveMonitorLayouts.FactoryQualifyingId).Layout.Name);
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
    public void TryPlaceTile_AtomicallyMovesAndResizesThenReflowsWithoutOverlap()
    {
        var layout = new LiveMonitorNamedLayout
        {
            Id = "atomic-layout",
            Name = "Atomic placement",
            Rows = 3,
            Columns = 3,
            Tiles =
            [
                Tile("tile-a", "speed", 0, 0),
                Tile("tile-b", "brake", 1, 1),
                Tile("tile-c", "throttle", 2, 2)
            ]
        };

        Assert.IsTrue(LiveMonitorLayouts.TryPlaceTile(layout, "tile-a", 1, 1, 2, 2));

        var placed = layout.Tiles.Single(tile => tile.Id == "tile-a");
        Assert.AreEqual(1, placed.Row);
        Assert.AreEqual(1, placed.Column);
        Assert.AreEqual(2, placed.RowSpan);
        Assert.AreEqual(2, placed.ColumnSpan);
        CollectionAssert.AreEquivalent(new[] { "tile-a", "tile-b", "tile-c" }, layout.Tiles.Select(tile => tile.Id).ToArray());
        AssertLayoutInBounds(layout);
        AssertNoOverlap(layout);
    }

    [TestMethod]
    public void TryPlaceTile_WhenPlacementCannotFit_LeavesTheEntireLayoutUnchanged()
    {
        var layout = new LiveMonitorNamedLayout
        {
            Id = "full-layout",
            Name = "Full layout",
            Rows = 2,
            Columns = 2,
            Tiles =
            [
                Tile("tile-a", "speed", 0, 0),
                Tile("tile-b", "brake", 0, 1),
                Tile("tile-c", "throttle", 1, 0),
                Tile("tile-d", "rpm", 1, 1)
            ]
        };
        var before = LayoutFingerprint(layout);

        Assert.IsFalse(LiveMonitorLayouts.TryPlaceTile(layout, "tile-a", 0, 0, 2, 2));
        Assert.AreEqual(before, LayoutFingerprint(layout));
        Assert.IsFalse(LiveMonitorLayouts.TryPlaceTile(layout, "missing", 0, 0, 1, 1));
        Assert.AreEqual(before, LayoutFingerprint(layout));
        AssertLayoutInBounds(layout);
        AssertNoOverlap(layout);
    }

    [TestMethod]
    public void TryPlaceTile_AtAnAlreadyClampedBoundary_IsANoOp()
    {
        var layout = new LiveMonitorNamedLayout
        {
            Id = "boundary-layout",
            Name = "Boundary layout",
            Rows = 2,
            Columns = 2,
            Tiles = [Tile("tile-a", "speed", 0, 0)]
        };
        var before = LayoutFingerprint(layout);

        Assert.IsFalse(LiveMonitorLayouts.TryPlaceTile(layout, "tile-a", -1, -1, 1, 1));

        Assert.AreEqual(before, LayoutFingerprint(layout));
    }

    [TestMethod]
    public void ResetActive_DefaultDashboardRemainsImmutable()
    {
        var preferences = new LiveMonitorLayout { ActiveLayoutId = LiveMonitorLayout.FactoryDefaultId };

        Assert.IsFalse(LiveMonitorLayouts.ResetActive(preferences));

        Assert.AreEqual(LiveMonitorLayout.FactoryDefaultId, preferences.ActiveLayoutId);
    }

    [TestMethod]
    public void PositionTargetedAdd_AllowsDuplicateMetricsUntilFullAndDoesNotMutateOnFailure()
    {
        var layout = new LiveMonitorNamedLayout { Id = "duplicates", Name = "Duplicates", Rows = 2, Columns = 2 };
        var addedIds = new List<string>();

        for (var index = 0; index < 4; index++)
        {
            Assert.IsTrue(LiveMonitorLayouts.TryAddMetric(layout, "speed", 1, 1, out var tileId));
            Assert.IsNotNull(tileId);
            addedIds.Add(tileId);
            var added = layout.Tiles.Single(tile => tile.Id == tileId);
            Assert.AreEqual(1, added.Row, "The newly added tile should win its requested target row.");
            Assert.AreEqual(1, added.Column, "The newly added tile should win its requested target column.");
            AssertLayoutInBounds(layout);
            AssertNoOverlap(layout);
        }

        Assert.AreEqual(4, layout.Tiles.Count(tile => tile.MetricId == "speed"));
        Assert.AreEqual(4, addedIds.Distinct(StringComparer.Ordinal).Count());
        var before = LayoutFingerprint(layout);
        Assert.IsFalse(LiveMonitorLayouts.TryAddMetric(layout, "speed", 0, 0, out var rejectedTileId));
        Assert.IsNull(rejectedTileId);
        Assert.AreEqual(before, LayoutFingerprint(layout));
        AssertLayoutInBounds(layout);
        AssertNoOverlap(layout);
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
    public void TelemetryCatalog_AppliesSemanticRangesToBarCapableReadings()
    {
        var now = DateTimeOffset.UtcNow;
        var snapshot = new LiveTelemetryEngine().Update(new LiveTelemetrySample
        {
            Connected = true,
            Timestamp = now,
            Tick = 1,
            TickRate = 60,
            BrakeBiasPercent = 54.7
        }, safeGlanceEnabled: true, coachingPaused: false);
        var state = new LiveMonitorState(snapshot, new LiveMonitorLayout(), false, 1, 0, 0, now);

        var reading = LiveTelemetryCatalog.Read("brake-bias", state);

        Assert.IsTrue(reading.Available);
        Assert.AreEqual(54.7, reading.NumericValue);
        Assert.AreEqual(40d, reading.Minimum);
        Assert.AreEqual(70d, reading.Maximum);
    }

    [TestMethod]
    public void TelemetryCatalog_EveryMetricSupportsEveryVisualFormAndTrendDuration()
    {
        var requiredStyles = new[]
        {
            LiveMonitorDisplayStyle.Number,
            LiveMonitorDisplayStyle.Bar,
            LiveMonitorDisplayStyle.Gauge,
            LiveMonitorDisplayStyle.Trend
        };
        var requiredDurations = Enum.GetValues<LiveMonitorTrendDuration>();

        foreach (var definition in LiveTelemetryCatalog.All)
        {
            foreach (var style in requiredStyles)
                Assert.Contains(style, definition.Styles, $"{definition.Name} must support {style}.");
            CollectionAssert.AreEquivalent(requiredDurations, LiveTelemetryCatalog.TrendDurations(definition.Id).ToArray(), $"{definition.Name} must expose every chart duration.");
        }
    }

    [TestMethod]
    public void TelemetryCatalog_BooleanAndCategoricalMetricsKeepTruthfulSteppedHistory()
    {
        var now = DateTimeOffset.UtcNow;
        var snapshot = new LiveTelemetryEngine().Update(new LiveTelemetrySample
        {
            Connected = true,
            Timestamp = now,
            Tick = 2,
            TickRate = 60,
            Flag = "Yellow",
            UnderCaution = true,
            OnPitRoad = true,
            FuelLiters = 10,
            FuelLevelPercent = .5
        }, safeGlanceEnabled: true, coachingPaused: false);
        var first = new LiveTracePoint(now.AddSeconds(-1), null, null, null, null, null, null, null, null, null, null, null, null, null, null,
            new LiveMetricHistoryFrame { OnPitRoad = false, FlagState = LiveFlagTrendState.Green, FuelLiters = 8 });
        var second = new LiveTracePoint(now, null, null, null, null, null, null, null, null, null, null, null, null, null, null,
            new LiveMetricHistoryFrame { OnPitRoad = true, FlagState = LiveFlagTrendState.Yellow, FuelLiters = 10 });
        var state = new LiveMonitorState(snapshot, new LiveMonitorLayout(), false, 2, 0, 0, now, [first, second], 60);

        var pitRoad = LiveTelemetryCatalog.Read("on-pit-road", state);
        var flag = LiveTelemetryCatalog.Read("flag", state);
        var fuel = LiveTelemetryCatalog.Read("fuel", state, "L", 1);

        CollectionAssert.AreEqual(new[] { 0d, 1d }, pitRoad.TrendValues.ToArray());
        CollectionAssert.AreEqual(new[] { 0d, 4d }, flag.TrendValues.ToArray());
        Assert.AreEqual(LiveMonitorTrendShape.Step, LiveTelemetryCatalog.Get("on-pit-road").TrendShape);
        Assert.AreEqual(LiveMonitorTrendShape.Step, LiveTelemetryCatalog.Get("flag").TrendShape);
        Assert.AreEqual(10d, fuel.NumericValue);
        Assert.AreEqual(0d, fuel.Minimum);
        Assert.AreEqual(20d, fuel.Maximum);
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
        var migrated = settings.LiveMonitor.UserLayouts.Single(layout => layout.Name == "Migrated 0.9.3");
        CollectionAssert.AreEqual(new[] { "leader-last-lap", "fuel", "track-temperature" }, migrated.Tiles.Select(tile => tile.MetricId).ToArray());
        CollectionAssert.AreEquivalent(
            new[] { LiveMonitorLayouts.FactoryRaceId, LiveMonitorLayouts.FactoryQualifyingId },
            settings.LiveMonitor.UserLayouts.Where(layout => layout.Id != migrated.Id).Select(layout => layout.Id).ToArray());
        Assert.IsTrue(settings.LiveMonitor.BuiltInDashboardsInitialized);
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
        CollectionAssert.AreEquivalent(
            new[] { LiveMonitorLayouts.FactoryRaceId, LiveMonitorLayouts.FactoryQualifyingId },
            settings.LiveMonitor.UserLayouts.Select(layout => layout.Id).ToArray());
        Assert.IsTrue(settings.LiveMonitor.BuiltInDashboardsInitialized);
        var rejected = machine + ".rejected-monitor.json";
        Assert.IsTrue(File.Exists(rejected));
        StringAssert.Contains(File.ReadAllText(rejected), "My broken layout");
    }

    [TestMethod]
    public void ValidateAndRepair_NormalizesDuplicateIdsAndUnsupportedWidgetOptions()
    {
        var preferences = new LiveMonitorLayout
        {
            ActiveLayoutId = "repairable",
            UserLayouts =
            [
                new LiveMonitorNamedLayout
                {
                    Id = "repairable",
                    Name = "Repairable dashboard",
                    Rows = 2,
                    Columns = 2,
                    Tiles =
                    [
                        Tile("duplicate", "brake", 0, 0),
                        new LiveMonitorTile
                        {
                            Id = "duplicate",
                            MetricId = "speed",
                            Row = 0,
                            Column = 1,
                            DisplayStyle = (LiveMonitorDisplayStyle)999,
                            Unit = "knots",
                            Precision = 12,
                            TrendDuration = (LiveMonitorTrendDuration)999,
                            Accent = "Neon"
                        }
                    ]
                }
            ]
        };

        var changed = LiveMonitorLayouts.ValidateAndRepair(preferences, out var corruptionFound);

        Assert.IsTrue(changed);
        Assert.IsTrue(corruptionFound);
        var repaired = preferences.UserLayouts.Single(layout => layout.Id == "repairable");
        CollectionAssert.AreEquivalent(
            new[] { LiveMonitorLayouts.FactoryRaceId, LiveMonitorLayouts.FactoryQualifyingId },
            preferences.UserLayouts.Where(layout => layout.Id != repaired.Id).Select(layout => layout.Id).ToArray());
        Assert.AreEqual(2, repaired.Tiles.Select(tile => tile.Id).Distinct(StringComparer.Ordinal).Count());
        var speed = repaired.Tiles.Single(tile => tile.MetricId == "speed");
        Assert.AreEqual(LiveMonitorDisplayStyle.Trend, speed.DisplayStyle);
        Assert.AreEqual("mph", speed.Unit);
        Assert.AreEqual(3, speed.Precision);
        Assert.AreEqual(LiveMonitorTrendDuration.Seconds30, speed.TrendDuration);
        Assert.AreEqual("default", speed.Accent);
        AssertLayoutInBounds(repaired);
        AssertNoOverlap(repaired);
    }

    [TestMethod]
    public void SettingsStore_RoundTripsTileAccentAndTrendDuration()
    {
        var directory = TestDirectory();
        var portable = Path.Combine(directory, "settings.json");
        var machine = Path.Combine(directory, "machine.json");
        var store = new JsonSettingsStore(portable, new TestCredentialStore(Path.Combine(directory, "credential.dpapi")), machine);
        var layout = new LiveMonitorNamedLayout
        {
            Id = "chart-layout",
            Name = "Chart layout",
            Rows = 3,
            Columns = 3,
            Tiles =
            [
                new LiveMonitorTile
                {
                    Id = "speed-chart",
                    MetricId = "speed",
                    Row = 1,
                    Column = 1,
                    RowSpan = 2,
                    ColumnSpan = 2,
                    DisplayStyle = LiveMonitorDisplayStyle.Trend,
                    Unit = "km/h",
                    Precision = 2,
                    TrendDuration = LiveMonitorTrendDuration.ThreeLaps,
                    Accent = "violet"
                }
            ]
        };
        var settings = new CompanionSettings
        {
            SettingsSchemaVersion = 4,
            LiveMonitor = new LiveMonitorLayout
            {
                ActiveLayoutId = layout.Id,
                UserLayouts = [layout]
            }
        };

        store.Save(settings);
        var restoredSettings = store.Load().LiveMonitor;
        var restored = restoredSettings.UserLayouts.Single(item => item.Id == layout.Id).Tiles.Single();
        CollectionAssert.AreEquivalent(
            new[] { LiveMonitorLayouts.FactoryRaceId, LiveMonitorLayouts.FactoryQualifyingId },
            restoredSettings.UserLayouts.Where(item => item.Id != layout.Id).Select(item => item.Id).ToArray());

        Assert.AreEqual("speed-chart", restored.Id);
        Assert.AreEqual(1, restored.Row);
        Assert.AreEqual(1, restored.Column);
        Assert.AreEqual(2, restored.RowSpan);
        Assert.AreEqual(2, restored.ColumnSpan);
        Assert.AreEqual(LiveMonitorDisplayStyle.Trend, restored.DisplayStyle);
        Assert.AreEqual("km/h", restored.Unit);
        Assert.AreEqual(2, restored.Precision);
        Assert.AreEqual(LiveMonitorTrendDuration.ThreeLaps, restored.TrendDuration);
        Assert.AreEqual("violet", restored.Accent);
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
        var root = CompanionAppRoot();
        var xaml = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.App", "LiveMonitorWindow.xaml"));
        foreach (var accessibleName in new[] { "Layout selector", "Unlock layout editing", "Grid settings", "Monitor scale", "Close live monitor", "Search telemetry catalog", "Tile decimal precision", "Chart history duration", "Tile accent color" })
            StringAssert.Contains(xaml, accessibleName);
        Assert.IsFalse(xaml.Contains("Grid and scale settings", StringComparison.Ordinal), "Grid dimensions and physical pop-out scale must have separate controls.");
        StringAssert.Contains(xaml, "x:Name=\"GridSettingsSurface\"");
        StringAssert.Contains(xaml, "x:Name=\"ScaleSettingsSurface\"");
        StringAssert.Contains(xaml, "x:Name=\"WorkspaceHost\"");
        StringAssert.Contains(xaml, "Width=\"444\" Height=\"296\"");
        StringAssert.Contains(xaml, "Alt+Arrow moves");
        StringAssert.Contains(xaml, "Shift+Arrow resizes");
        StringAssert.Contains(xaml, "ResetLayoutButton");
        var monitorCode = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.App", "LiveMonitorWindow.xaml.cs"));
        StringAssert.Contains(monitorCode, "CheckForExternalEditorChange");
        StringAssert.Contains(monitorCode, "EditorSignature(Preferences)");
        StringAssert.Contains(monitorCode, "Math.Min(GridViewportWidth / layout.Columns, GridViewportHeight / layout.Rows)");
        StringAssert.Contains(monitorCode, "RootScale.LayoutTransform = new ScaleTransform(scale, scale)");
        StringAssert.Contains(monitorCode, "_gridSettingsUndoBackup = _undo.ToArray()");
        StringAssert.Contains(monitorCode, "RestoreUndoHistory(_gridSettingsUndoBackup)");
        StringAssert.Contains(monitorCode, "for (var index = snapshots.Count - 1; index >= 0; index--) _undo.Push(snapshots[index])");
        StringAssert.Contains(monitorCode, "private void RollbackFailedMutation()");
        StringAssert.Contains(monitorCode, "if (!_undo.TryPop(out var snapshot)) return;");
        StringAssert.Contains(monitorCode, "private sealed record LayoutSnapshot(string ActiveLayoutId, IReadOnlyList<LiveMonitorNamedLayout> UserLayouts);");
        Assert.IsFalse(monitorCode.Contains("_undo.Pop()", StringComparison.Ordinal), "Failed edits must restore the captured layout, not merely discard its undo entry.");
        Assert.IsFalse(monitorCode.Contains("Preferences.OverallScale = snapshot.OverallScale", StringComparison.Ordinal), "Layout undo must not change the independent monitor scale.");
        StringAssert.Contains(monitorCode, "var retained = _undo.Take(20).Reverse().ToArray()");
    }

    [TestMethod]
    public void LiveTelemetryLayoutMarkup_ExposesPointerEditingDrawerAndPerTileControls()
    {
        var root = CompanionAppRoot();
        var ui = Path.Combine(root, "src", "iRacingCoach.UI");
        var razor = File.ReadAllText(Path.Combine(ui, "LiveTelemetryLayoutGrid.razor"));
        var monitorCode = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.App", "LiveMonitorWindow.xaml.cs"));
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));
        var scriptPath = Path.Combine(ui, "wwwroot", "live-telemetry-layout.js");
        var host = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.App", "wwwroot", "index.html"));
        var previewHost = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.Preview", "Components", "App.razor"));

        Assert.IsTrue(File.Exists(scriptPath), "The tile editor needs a dedicated pointer-interaction script.");
        var script = File.ReadAllText(scriptPath);
        foreach (var pointerContract in new[] { "pointerdown", "pointermove", "pointerup", "setPointerCapture", "getBoundingClientRect" })
            StringAssert.Contains(script, pointerContract);
        foreach (var snapContract in new[] { "snapHysteresis", "data-live-resize", "CommitTilePlacement", "DropMetric", "requestAnimationFrame", "Escape", "placementCanPack", "state.committing", "lostpointercapture", "windowBlur", "autoScroll" })
            StringAssert.Contains(script, snapContract);
        StringAssert.Contains(script, "event.target.closest(\"[data-live-drag-tile]\")");
        StringAssert.Contains(script, "const viewport = state.root.querySelector(\"[data-live-grid-viewport]\")");
        StringAssert.Contains(script, "Math.min(availableWidth / columns, availableHeight / rows)");
        StringAssert.Contains(script, "grid.style.width = `${width}px`");
        StringAssert.Contains(script, "grid.style.height = `${height}px`");
        StringAssert.Contains(script, "? `${placement.columnSpan} x ${placement.rowSpan}`");
        Assert.IsFalse(script.Contains("- ready", StringComparison.OrdinalIgnoreCase), "A resize preview should show only the occupied grid size.");
        StringAssert.Contains(script, "const noDragTarget = event.target.closest(\"[data-live-no-drag]\")");
        StringAssert.Contains(script, "const metricHandle = noDragTarget ? null : event.target.closest(\"[data-live-drag-metric]\")");
        StringAssert.Contains(script, "event.preventDefault()");
        StringAssert.Contains(host, "live-telemetry-layout.js");
        StringAssert.Contains(previewHost, "live-telemetry-layout.js");

        foreach (var interactionHook in new[]
        {
            "data-live-layout-studio", "data-live-grid", "data-live-tile", "data-tile-id", "data-live-drag-tile",
            "data-live-resize", "data-live-catalog-item", "data-metric-id", "data-live-drag-metric"
        })
            StringAssert.Contains(razor, interactionHook);

        StringAssert.Contains(razor, "live-toolbox");
        StringAssert.Contains(razor, "live-toolbox-backdrop");
        StringAssert.Contains(razor, "live-tile-action remove");
        StringAssert.Contains(razor, "live-tile-quick-menu");
        StringAssert.Contains(razor, "<div class=\"live-layout-viewport\" data-live-grid-viewport>");
        StringAssert.Contains(razor, "<article class=\"toolbox-metric-card\" data-live-catalog-item data-live-drag-metric=\"@metric.Id\"");
        StringAssert.Contains(razor, "class=\"metric-add-button\" data-live-no-drag");
        StringAssert.Contains(razor, "@onkeydown=\"HandleStudioKeyDown\"");
        StringAssert.Contains(razor, "role=\"group\"");
        StringAssert.Contains(razor, "editing");
        StringAssert.Contains(razor, "LiveMonitorDisplayStyle.Trend => \"Chart\"");
        StringAssert.Contains(css, ".live-layout-studio.editing .live-layout-tile");
        StringAssert.Contains(css, "border-style: dashed");
        StringAssert.Contains(css, "@container (max-width: 150px)");
        StringAssert.Contains(css, "grid-template-columns: minmax(0,1fr)");
        StringAssert.Contains(css, ".live-layout-viewport");
        StringAssert.Contains(css, "place-items: center");
        StringAssert.Contains(css, "height: clamp(");
        Assert.IsFalse(razor.Contains("live-editor-guidance", StringComparison.Ordinal), "The editor should not spend a header row on static drag instructions or success chatter.");
        Assert.IsFalse(razor.Contains("Undo available", StringComparison.OrdinalIgnoreCase), "Undo controls already communicate recoverability; removal actions should stay concise.");
        Assert.IsFalse(monitorCode.Contains("Undo is available", StringComparison.OrdinalIgnoreCase), "The native monitor should not add undo instructions to routine completion messages.");
        Assert.IsFalse(razor.Contains("OverallScale", StringComparison.Ordinal), "Physical scale belongs only to the pop-out monitor, not the fitted full-page dashboard.");
        Assert.IsFalse(css.Contains(".live-toolbox-backdrop { position: fixed", StringComparison.Ordinal), "The responsive drawer must not place a pointer-blocking backdrop over the editable grid.");
        Assert.IsFalse(css.Contains(".live-layout-studio.toolbox-open .live-layout-main { padding-right: 0", StringComparison.Ordinal), "Responsive edit mode must keep the grid out from under the fixed drawer.");
        Assert.IsFalse(razor.Contains("Ã", StringComparison.Ordinal), "Razor markup must not contain mojibake glyphs.");
        Assert.IsFalse(razor.Contains("Â", StringComparison.Ordinal), "Razor markup must not contain mojibake glyphs.");
        Assert.IsFalse(script.Contains("Ã", StringComparison.Ordinal), "Pointer script must not contain mojibake glyphs.");
        Assert.IsFalse(script.Contains("Â", StringComparison.Ordinal), "Pointer script must not contain mojibake glyphs.");
    }

    private static void AssertNoOverlap(LiveMonitorNamedLayout layout)
    {
        var occupied = new HashSet<(int Row, int Column)>();
        foreach (var tile in layout.Tiles)
            for (var row = tile.Row; row < tile.Row + tile.RowSpan; row++)
                for (var column = tile.Column; column < tile.Column + tile.ColumnSpan; column++)
                    Assert.IsTrue(occupied.Add((row, column)), $"Overlap at {row},{column}");
    }

    private static void AssertLayoutInBounds(LiveMonitorNamedLayout layout)
    {
        foreach (var tile in layout.Tiles)
        {
            Assert.IsGreaterThanOrEqualTo(0, tile.Row, $"{tile.Id} starts outside the grid rows.");
            Assert.IsGreaterThanOrEqualTo(0, tile.Column, $"{tile.Id} starts outside the grid columns.");
            Assert.IsGreaterThanOrEqualTo(1, tile.RowSpan, $"{tile.Id} has an invalid row span.");
            Assert.IsGreaterThanOrEqualTo(1, tile.ColumnSpan, $"{tile.Id} has an invalid column span.");
            Assert.IsLessThanOrEqualTo(layout.Rows, tile.Row + tile.RowSpan, $"{tile.Id} exceeds the grid rows.");
            Assert.IsLessThanOrEqualTo(layout.Columns, tile.Column + tile.ColumnSpan, $"{tile.Id} exceeds the grid columns.");
        }
    }

    private static string LayoutFingerprint(LiveMonitorNamedLayout layout) => string.Join("|",
        layout.Id,
        layout.Name,
        layout.Rows,
        layout.Columns,
        string.Join(";", layout.Tiles.Select(tile => string.Join(",",
            tile.Id, tile.MetricId, tile.Row, tile.Column, tile.RowSpan, tile.ColumnSpan,
            tile.DisplayStyle, tile.Unit, tile.Precision, tile.TrendDuration, tile.Accent))));

    private static LiveMonitorTile Tile(string id, string metricId, int row, int column) => new()
    {
        Id = id,
        MetricId = metricId,
        Row = row,
        Column = column,
        DisplayStyle = LiveTelemetryCatalog.Get(metricId).DefaultStyle,
        Unit = LiveTelemetryCatalog.Get(metricId).DefaultUnit,
        Precision = LiveTelemetryCatalog.Get(metricId).DefaultPrecision
    };

    private static string TestDirectory()
    {
        var directory = Path.Combine(Path.GetTempPath(), "iracing-coach-tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        return directory;
    }

    private static string CompanionAppRoot([System.Runtime.CompilerServices.CallerFilePath] string sourceFile = "")
    {
        if (!string.IsNullOrWhiteSpace(sourceFile))
        {
            var sourceRoot = Path.GetFullPath(Path.Combine(Path.GetDirectoryName(sourceFile)!, "..", ".."));
            if (File.Exists(Path.Combine(sourceRoot, "iRacingCoach.sln"))) return sourceRoot;
        }
        foreach (var start in new[] { AppContext.BaseDirectory, Environment.CurrentDirectory })
        {
            for (var directory = new DirectoryInfo(start); directory is not null; directory = directory.Parent)
            {
                if (File.Exists(Path.Combine(directory.FullName, "iRacingCoach.sln"))) return directory.FullName;
                var nested = Path.Combine(directory.FullName, "companion-app", "iRacingCoach.sln");
                if (File.Exists(nested)) return Path.GetDirectoryName(nested)!;
            }
        }
        throw new DirectoryNotFoundException("Could not locate the companion-app source root.");
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
