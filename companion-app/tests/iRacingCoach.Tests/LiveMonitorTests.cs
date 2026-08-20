using System.Diagnostics;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class LiveMonitorTests
{
    [TestMethod]
    public void LapsRemaining_TreatsTheIRacingUnlimitedSentinelAsUnknown()
    {
        // 32767 is iRacing's "no lap limit" sentinel and must never reach the UI
        // as a literal count - this is the "32767 laps remaining" defect.
        Assert.IsNull(IRacingSdkTelemetrySource.LapsRemainingOrUnknown(32767));
        Assert.IsNull(IRacingSdkTelemetrySource.LapsRemainingOrUnknown(32768));
        Assert.IsNull(IRacingSdkTelemetrySource.LapsRemainingOrUnknown(50000));
        Assert.IsNull(IRacingSdkTelemetrySource.LapsRemainingOrUnknown(-1));
        Assert.IsNull(IRacingSdkTelemetrySource.LapsRemainingOrUnknown(double.NaN));
        Assert.IsNull(IRacingSdkTelemetrySource.LapsRemainingOrUnknown(null));

        // A real, finite count still rounds up as before.
        Assert.AreEqual(0, IRacingSdkTelemetrySource.LapsRemainingOrUnknown(0));
        Assert.AreEqual(5, IRacingSdkTelemetrySource.LapsRemainingOrUnknown(4.2));
        Assert.AreEqual(43, IRacingSdkTelemetrySource.LapsRemainingOrUnknown(43));
    }


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
    public void ReplaceMetric_PreservesFootprintAndResetsDisplayForTheNewTelemetry()
    {
        var layout = new LiveMonitorNamedLayout
        {
            Id = "replacement-layout",
            Name = "Replacement layout",
            Rows = 3,
            Columns = 3,
            Tiles =
            [
                new LiveMonitorTile
                {
                    Id = "target",
                    MetricId = "speed",
                    Row = 1,
                    Column = 1,
                    RowSpan = 2,
                    ColumnSpan = 2,
                    DisplayStyle = LiveMonitorDisplayStyle.Trend,
                    Unit = "kph",
                    Precision = 3,
                    TrendDuration = LiveMonitorTrendDuration.ThreeLaps,
                    Accent = "violet",
                    HighlightAbsIntervention = true
                },
                Tile("neighbor", "rpm", 0, 0)
            ]
        };
        var definition = LiveTelemetryCatalog.Get("brake");

        Assert.IsTrue(LiveMonitorLayouts.TryReplaceMetric(layout, "target", "brake"));

        var replaced = layout.Tiles.Single(tile => tile.Id == "target");
        Assert.AreEqual("brake", replaced.MetricId);
        Assert.AreEqual(1, replaced.Row);
        Assert.AreEqual(1, replaced.Column);
        Assert.AreEqual(2, replaced.RowSpan);
        Assert.AreEqual(2, replaced.ColumnSpan);
        Assert.AreEqual(definition.DefaultStyle, replaced.DisplayStyle);
        Assert.AreEqual(definition.DefaultUnit, replaced.Unit);
        Assert.AreEqual(definition.DefaultPrecision, replaced.Precision);
        Assert.AreEqual(LiveMonitorTrendDuration.Seconds30, replaced.TrendDuration);
        Assert.AreEqual("default", replaced.Accent);
        Assert.IsFalse(replaced.HighlightAbsIntervention, "A metric replacement must not retain Brake-only ABS highlighting on unrelated telemetry.");
        AssertNoOverlap(layout);

        var beforeInvalidReplacement = LayoutFingerprint(layout);
        Assert.IsFalse(LiveMonitorLayouts.TryReplaceMetric(layout, "missing", "throttle"));
        Assert.IsFalse(LiveMonitorLayouts.TryReplaceMetric(layout, "target", "missing"));
        Assert.AreEqual(beforeInvalidReplacement, LayoutFingerprint(layout));
    }

    [TestMethod]
    public void ReplaceTile_MovesDraggedWidgetIntoTargetFootprintAndRemovesTargetAtomically()
    {
        var source = new LiveMonitorTile
        {
            Id = "source",
            MetricId = "brake",
            Row = 0,
            Column = 0,
            RowSpan = 1,
            ColumnSpan = 1,
            DisplayStyle = LiveMonitorDisplayStyle.Trend,
            Unit = "%",
            Precision = 2,
            TrendDuration = LiveMonitorTrendDuration.Seconds60,
            Accent = "coral",
            HighlightAbsIntervention = true
        };
        var layout = new LiveMonitorNamedLayout
        {
            Id = "tile-replacement-layout",
            Name = "Tile replacement layout",
            Rows = 3,
            Columns = 3,
            Tiles =
            [
                source,
                new LiveMonitorTile { Id = "target", MetricId = "speed", Row = 1, Column = 1, RowSpan = 2, ColumnSpan = 2 },
                Tile("neighbor", "rpm", 0, 2)
            ]
        };

        Assert.IsTrue(LiveMonitorLayouts.TryReplaceTile(layout, "source", "target"));

        Assert.HasCount(2, layout.Tiles);
        Assert.IsFalse(layout.Tiles.Any(tile => tile.Id == "target"));
        var moved = layout.Tiles.Single(tile => tile.Id == "source");
        Assert.AreEqual("brake", moved.MetricId);
        Assert.AreEqual(1, moved.Row);
        Assert.AreEqual(1, moved.Column);
        Assert.AreEqual(2, moved.RowSpan);
        Assert.AreEqual(2, moved.ColumnSpan);
        Assert.AreEqual(LiveMonitorDisplayStyle.Trend, moved.DisplayStyle);
        Assert.AreEqual("%", moved.Unit);
        Assert.AreEqual(2, moved.Precision);
        Assert.AreEqual(LiveMonitorTrendDuration.Seconds60, moved.TrendDuration);
        Assert.AreEqual("coral", moved.Accent);
        Assert.IsTrue(moved.HighlightAbsIntervention, "Moving the configured Brake tile must preserve its explicit ABS overlay preference.");
        AssertNoOverlap(layout);

        var beforeInvalidReplacement = LayoutFingerprint(layout);
        Assert.IsFalse(LiveMonitorLayouts.TryReplaceTile(layout, "source", "source"));
        Assert.IsFalse(LiveMonitorLayouts.TryReplaceTile(layout, "missing", "source"));
        Assert.IsFalse(LiveMonitorLayouts.TryReplaceTile(layout, "source", "missing"));
        Assert.AreEqual(beforeInvalidReplacement, LayoutFingerprint(layout));
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
    public void TelemetryTrendValue_ConvertsAccelerationUnitsWithoutInventingMissingValues()
    {
        var point = new LiveTracePoint(
            DateTimeOffset.UtcNow, 4, .5, 120, null, null, null, 3, 6400, null,
            1, -.5, null, null, null, default);

        Assert.AreEqual(9.80665, LiveTelemetryCatalog.TrendValue("lateral-acceleration", point, "m/s²")!.Value, 0.00001);
        Assert.AreEqual(-4.903325, LiveTelemetryCatalog.TrendValue("longitudinal-acceleration", point, "m/s²")!.Value, 0.00001);
        Assert.AreEqual(1d, LiveTelemetryCatalog.TrendValue("lateral-acceleration", point, "g"));
        Assert.IsNull(LiveTelemetryCatalog.TrendValue("brake", point, "%"));
    }

    [TestMethod]
    public void TelemetryProjection_SkipsScalarHistoryAndBoundsChartSeedsWithoutLosingLatestExtremaOrGaps()
    {
        var now = DateTimeOffset.UtcNow;
        var history = Enumerable.Range(0, 1_000).Select(index =>
        {
            double? speed = index switch { 123 => -50, 321 => null, 456 => 500, 999 => 42, _ => index % 10 };
            return new LiveTracePoint(now.AddMilliseconds(index), 1, index / 1000d, speed, null, null, null, null, null, null, null, null, null, null, null, default);
        }).ToArray();
        var snapshot = new LiveTelemetryEngine().Update(new LiveTelemetrySample
        {
            Connected = true,
            Timestamp = now.AddSeconds(1),
            Tick = 1_000,
            TickRate = 240,
            SpeedMetersPerSecond = 18.77568
        }, safeGlanceEnabled: true, coachingPaused: false);
        var state = new LiveMonitorState(snapshot, new LiveMonitorLayout(), false, 1_000, 0, 0, now, history, 240);

        var scalar = LiveTelemetryCatalog.Read("speed", state, includeTrend: false);
        var projection = LiveTelemetryCatalog.ProjectTrend("speed", history, LiveMonitorTrendDuration.Seconds60, maximumPoints: 25);

        Assert.IsEmpty(scalar.TrendValues);
        Assert.IsLessThanOrEqualTo(25, projection.Count);
        Assert.IsTrue(projection.Any(point => point.Value == -50), "A brief minimum must survive seed compaction.");
        Assert.IsTrue(projection.Any(point => point.Value == 500), "A brief maximum must survive seed compaction.");
        Assert.IsTrue(projection.Any(point => point.Value is null), "A positioned missing sample must survive as a truthful chart gap.");
        Assert.AreEqual(42d, projection[^1].Value, "The newest source sample must always survive compaction.");
    }

    [TestMethod]
    public void LiveTelemetryTrendCanvas_PreservesTruthAndBoundsDisplayWork()
    {
        var root = CompanionAppRoot();
        var ui = Path.Combine(root, "src", "iRacingCoach.UI");
        var razor = File.ReadAllText(Path.Combine(ui, "LiveTelemetryLayoutGrid.razor"));
        var script = File.ReadAllText(Path.Combine(ui, "wwwroot", "live-telemetry-tile-charts.js"));

        StringAssert.Contains(script, "value === null || value === undefined || value === \"\"");
        StringAssert.Contains(script, "shape === \"step\"");
        StringAssert.Contains(script, "const lap = finite(structured ? point.lap : null)");
        StringAssert.Contains(script, "const at = finite(structured ? point.atUnixMilliseconds : null)");
        StringAssert.Contains(script, "A structured missing sample is retained as a gap marker");
        StringAssert.Contains(script, "if (point.value === null) continue;");
        StringAssert.Contains(script, "if (point.value === null) {");
        StringAssert.Contains(script, "finishSegment();");
        StringAssert.Contains(script, "previousAt !== null && point.at !== null && point.at - previousAt > gapThreshold");
        Assert.IsFalse(script.Contains("point.value || 0", StringComparison.Ordinal), "Missing chart samples must not be coerced to zero.");

        StringAssert.Contains(script, "function stableRange(chart, observed, now)");
        StringAssert.Contains(script, "domainShrinkDelayMilliseconds");
        StringAssert.Contains(script, "domainDecayIntervalMilliseconds");
        StringAssert.Contains(script, "domainDecayFactor");
        StringAssert.Contains(script, "Never decay through a currently visible sample");
        Assert.IsFalse(script.Contains("chart.points.map(point => point.value)", StringComparison.Ordinal), "Domain calculation must not allocate and rescale an unbounded values array per sample.");
        Assert.IsFalse(script.Contains("Math.min(...values)", StringComparison.Ordinal), "Large telemetry histories must not be spread into function arguments.");

        StringAssert.Contains(script, "function buildPixelEnvelope(chart, studio, valueRange)");
        StringAssert.Contains(script, "bucketCount * verticesPerPixel");
        StringAssert.Contains(script, "first/minimum/maximum/last preserves brief extrema");
        StringAssert.Contains(script, "preserves brief extrema");
        StringAssert.Contains(script, "resetLapCharts(studio)");
        StringAssert.Contains(script, "isLapRegression(studio.latestLapProgress, progress)");
        StringAssert.Contains(script, "observeClock(studio, clockPoint, arrivedAt)) resetLapCharts(studio)");
        StringAssert.Contains(script, "const scrolling = !studio.reducedMotion");
        StringAssert.Contains(script, "if (!chart.dirty && !scrolling && !chart.resizeDirty) return false;");
        StringAssert.Contains(script, "chart.palette = palette(canvas)");
        StringAssert.Contains(script, "secondsWindow(chart.configuration)");
        StringAssert.Contains(script, "context.translate(-shift, 0)");
        StringAssert.Contains(razor, "tile.DisplayStyle == LiveMonitorDisplayStyle.Trend)");
        StringAssert.Contains(razor, "seed = TrendSeed(tile)");
        StringAssert.Contains(razor, "LiveTelemetryCatalog.ProjectTrend");
        StringAssert.Contains(razor, "atUnixMilliseconds = point.At.ToUnixTimeMilliseconds()");
        StringAssert.Contains(razor, "value = point.Value");
        StringAssert.Contains(razor, "includeTrend: false");
        StringAssert.Contains(razor, "CompactPendingFrames");
        StringAssert.Contains(razor, "LiveTelemetryFrameCompaction.Compact");
        Assert.IsFalse(razor.Contains(".Where(item => item.Value.HasValue)", StringComparison.Ordinal), "Seed history must preserve positioned missing samples as explicit chart gaps.");
        Assert.IsFalse(razor.Contains("LiveMonitorDisplayStyle.Trend && reading.TrendValues.Count", StringComparison.Ordinal));
    }

    [TestMethod]
    public void BrakeAbsHighlight_UsesOnlyExplicitSdkEvidenceAndPaintsRecordedSegmentsYellow()
    {
        var root = CompanionAppRoot();
        var ui = Path.Combine(root, "src", "iRacingCoach.UI");
        var razor = File.ReadAllText(Path.Combine(ui, "LiveTelemetryLayoutGrid.razor"));
        var script = File.ReadAllText(Path.Combine(ui, "wwwroot", "live-telemetry-tile-charts.js"));
        var sdk = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.Coordinator", "IRacingSdkTelemetrySource.cs"));

        StringAssert.Contains(sdk, "ReadBool(\"BrakeABSactive\", bestOffset)");
        StringAssert.Contains(sdk, "ReadDouble(\"BrakeABSactive\", bestOffset)");
        StringAssert.Contains(sdk, "Percentage(ReadDouble(\"BrakeABScutPct\", bestOffset))");
        StringAssert.Contains(razor, "private bool HasAbsHistory => (State.LiveState.History ?? []).Any(point => point.Metrics.BrakeAbsActive.HasValue)");
        StringAssert.Contains(razor, "highlightAbs = IsBrakeTrend(tile) && tile.HighlightAbsIntervention && HasAbsHistory");
        StringAssert.Contains(razor, "disabled=\"@(!HasAbsHistory)\"");
        StringAssert.Contains(razor, "brakeAbsActive = frame.Metrics.BrakeAbsActive");

        StringAssert.Contains(script, "const absActive = rawAbsActive === true;");
        StringAssert.Contains(script, "if (point.absActive === true)");
        StringAssert.Contains(script, "if (!chart.configuration.highlightAbs) return { path: null, vertexCount: 0 }");
        StringAssert.Contains(script, "chart.absPath = abs.vertexCount > 1 ? abs.path : null");
        StringAssert.Contains(script, "context.strokeStyle = \"#f4c24f\"");
        Assert.DoesNotContain("absCut", script, "A cut percentage must never be promoted into an ABS activation event.");
        Assert.DoesNotContain("brakeAbsCutPercent", razor, "The chart renderer needs only the explicit BrakeABSactive evidence channel.");
        Assert.DoesNotContain("brake >", script, "ABS intervention must not be inferred from pedal pressure.");
        Assert.DoesNotContain("wheelSlip", script, "The yellow overlay is gated by explicit ABS channels, not wheel-slip heuristics.");
    }

    [TestMethod]
    public void PendingFrameCompaction_PreservesExplicitGapsForEveryVisibleSignal()
    {
        var now = DateTimeOffset.UtcNow;
        var frames = Enumerable.Range(0, 1_000)
            .Select(index => new LiveTracePoint(
                now.AddMilliseconds(index), 1, index / 1000d,
                index == 111 ? null : index,
                index == 222 ? null : index / 1000d,
                null, null, null, null, null, null, null, null, null, null, default))
            .ToArray();
        Func<LiveTracePoint, double?>[] selectors = [point => point.SpeedMph, point => point.Throttle];

        var compacted = LiveTelemetryFrameCompaction.Compact(frames, 30, selectors);

        Assert.IsLessThanOrEqualTo(30, compacted.Count);
        Assert.AreEqual(frames[^1].At, compacted[^1].At, "The newest frame must survive compaction.");
        Assert.IsTrue(compacted.Any(point => point.At == frames[111].At && point.SpeedMph is null), "A speed-channel gap must remain a chart break.");
        Assert.IsTrue(compacted.Any(point => point.At == frames[222].At && point.Throttle is null), "A throttle-channel gap must remain a chart break.");
    }

    [TestMethod]
    public void LegacyMonitorPreferences_MigrateToNamedLayoutAndKeepOriginalForSupport()
    {
        var directory = TestDirectory();
        var portable = Path.Combine(directory, "settings.json");
        var machine = Path.Combine(directory, "machine.json");
        File.WriteAllText(portable, """{"settingsSchemaVersion":3,"liveMonitor":{"positionLocked":false,"left":44,"top":55,"width":700,"monitorDeviceName":"DISPLAY-OLD","secondaryFields":["LeaderLap","Fuel","Weather"]}}""");
        var settings = new JsonSettingsStore(portable, new TestCredentialStore(Path.Combine(directory, "credential.dpapi")), machine).Load();
        Assert.AreEqual(5, settings.SettingsSchemaVersion);
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
                    Accent = "violet",
                    HighlightAbsIntervention = true
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
        Assert.IsTrue(restored.HighlightAbsIntervention);
    }

    [TestMethod]
    public void BrakeAbsHighlight_PersistsThroughCloneAndInvalidatesTheEditorSignature()
    {
        var layout = new LiveMonitorNamedLayout
        {
            Id = "abs-layout",
            Name = "ABS review",
            Rows = 1,
            Columns = 1,
            Tiles =
            [
                new LiveMonitorTile
                {
                    Id = "brake-chart",
                    MetricId = "brake",
                    DisplayStyle = LiveMonitorDisplayStyle.Trend,
                    Unit = "%",
                    HighlightAbsIntervention = false
                }
            ]
        };
        var preferences = new LiveMonitorLayout
        {
            ActiveLayoutId = layout.Id,
            BuiltInDashboardsInitialized = true,
            UserLayouts = [layout]
        };
        var before = LiveMonitorLayouts.EditorSignature(preferences);

        layout.Tiles.Single().HighlightAbsIntervention = true;
        var after = LiveMonitorLayouts.EditorSignature(preferences);
        var clone = LiveMonitorLayouts.Clone(layout);

        Assert.AreNotEqual(before, after, "Changing the ABS overlay must invalidate the rendered-layout signature.");
        Assert.IsTrue(clone.Tiles.Single().HighlightAbsIntervention);
        clone.Tiles.Single().HighlightAbsIntervention = false;
        Assert.IsTrue(layout.Tiles.Single().HighlightAbsIntervention, "Cloning must not share mutable tile state.");
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
    public void LiveMonitorMarkup_IsAReadOnlyEqualSplitViewerWithStableSizePresets()
    {
        var root = CompanionAppRoot();
        var xaml = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.App", "LiveMonitorWindow.xaml"));
        foreach (var accessibleName in new[] { "Layout selector", "Popout size", "Return to full app", "Close telemetry popout" })
            StringAssert.Contains(xaml, accessibleName);
        StringAssert.Contains(xaml, "x:Name=\"ScaleSettingsSurface\"");
        StringAssert.Contains(xaml, "x:Name=\"WorkspaceHost\"");
        StringAssert.Contains(xaml, "Width=\"540\" Height=\"360\"");
        StringAssert.Contains(xaml, "MouseLeftButtonDown=\"ControlStrip_MouseLeftButtonDown\"");
        StringAssert.Contains(xaml, "TextTrimming=\"CharacterEllipsis\"");
        StringAssert.Contains(xaml, "Topmost=\"True\"");
        StringAssert.Contains(xaml, "Color=\"{StaticResource AccentColor}\"");
        StringAssert.Contains(xaml, "Color=\"{StaticResource ChartBackgroundColor}\"");
        StringAssert.Contains(xaml, "AllowsTransparency=\"False\"");
        StringAssert.Contains(xaml, "<ColumnDefinition Width=\"3*\"/><ColumnDefinition Width=\"10*\"/><ColumnDefinition Width=\"3*\"/>");
        foreach (var preset in new[] { "CompactSizeButton", "StandardSizeButton", "ExpandedSizeButton", "CommandParameter=\"0.8\"", "CommandParameter=\"1.0\"", "CommandParameter=\"1.25\"" })
            StringAssert.Contains(xaml, preset);
        Assert.DoesNotContain("<Slider", xaml, "The size chooser must not resize a transformed control while it owns pointer capture.");
        foreach (var removedEditorContract in new[] { "Unlock layout editing", "Grid settings", "GridSettingsSurface", "EditorPanel", "Search telemetry catalog", "Telemetry catalog", "Tile display style", "AllowDrop=\"True\"", "DragGripButton" })
            Assert.IsFalse(xaml.Contains(removedEditorContract, StringComparison.Ordinal), $"The pop-out must not duplicate main-app editing: {removedEditorContract}");

        var monitorCode = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.App", "LiveMonitorWindow.xaml.cs"));
        StringAssert.Contains(monitorCode, "CheckForExternalEditorChange");
        StringAssert.Contains(monitorCode, "EditorSignature(Preferences)");
        StringAssert.Contains(monitorCode, "TileGrid.Width = GridViewportWidth");
        StringAssert.Contains(monitorCode, "TileGrid.Height = GridViewportHeight");
        StringAssert.Contains(monitorCode, "new RowDefinition { Height = new GridLength(1, GridUnitType.Star) }");
        StringAssert.Contains(monitorCode, "new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) }");
        StringAssert.Contains(monitorCode, "RootScale.LayoutTransform = new ScaleTransform(scale, scale)");
        StringAssert.Contains(monitorCode, "SizePresetButton_Click");
        StringAssert.Contains(monitorCode, "Dispatcher.BeginInvoke(() => ApplySizePreset(requested), DispatcherPriority.ContextIdle)");
        StringAssert.Contains(monitorCode, "Math.Clamp(requested, .8, 1.25)");
        Assert.DoesNotContain("ScaleSlider_ValueChanged", monitorCode);
        StringAssert.Contains(monitorCode, "private const double MinimumFontSize = 11.67");
        StringAssert.Contains(monitorCode, "var dense = tileWidth < 88 || tileHeight < 64");
        StringAssert.Contains(monitorCode, "Visibility = dense ? Visibility.Collapsed : Visibility.Visible");
        StringAssert.Contains(monitorCode, "var showUnit = height >= 34");
        StringAssert.Contains(monitorCode, "ControlStrip_MouseLeftButtonDown");
        StringAssert.Contains(monitorCode, "DragMove()");
        StringAssert.Contains(monitorCode, "FittedValue");
        StringAssert.Contains(monitorCode, "StretchDirection = StretchDirection.DownOnly");
        StringAssert.Contains(monitorCode, "Canvas.SetBottom(label, 2)");
        StringAssert.Contains(monitorCode, "MonitorChartGridBrush");
        Assert.DoesNotContain("accentHeight", monitorCode, "Native cards must not add a visual strip that the full dashboard does not use.");
        StringAssert.Contains(monitorCode, "CompositionTarget.Rendering += OnCompositionRendering");
        StringAssert.Contains(monitorCode, "CompositionTarget.Rendering -= OnCompositionRendering");
        StringAssert.Contains(monitorCode, "Interlocked.Exchange(ref _renderDirty, 0)");
        StringAssert.Contains(monitorCode, "foreach (var visual in _tileVisuals)");
        StringAssert.Contains(monitorCode, "visual.Update(reading, liveState)");
        StringAssert.Contains(monitorCode, "private sealed class TrendBuffer");
        StringAssert.Contains(monitorCode, "if (state.FramesRead == _lastFrame)");
        StringAssert.Contains(monitorCode, "liveState with { History = [] }");
        StringAssert.Contains(monitorCode, "includeTrend: false");
        StringAssert.Contains(monitorCode, "public void ShowMonitor(bool activate = true)");
        StringAssert.Contains(monitorCode, "ShowActivated = activate");
        StringAssert.Contains(monitorCode, "public void CloseMonitor()");
        StringAssert.Contains(monitorCode, "Closing += OnClosing");
        Assert.IsFalse(monitorCode.Contains("TimeSpan.FromMilliseconds(200)", StringComparison.Ordinal), "The native monitor must not cap fresh telemetry at five updates per second.");
        Assert.AreEqual(1, monitorCode.Split("TileGrid.Children.Clear()", StringSplitOptions.None).Length - 1, "The tile tree should be rebuilt only by the layout path, not on telemetry frames.");
        Assert.DoesNotContain("FontSize = 9,", monitorCode);
        Assert.DoesNotContain("FontSize = 10,", monitorCode);
        Assert.DoesNotContain("FontSize = compact ?", monitorCode);
        foreach (var removedEditorContract in new[] { "LiveMonitorLayouts.EnsureEditable", "LiveMonitorLayouts.TryResizeGrid", "LiveMonitorLayouts.TryAddMetric", "DragDrop.DoDragDrop", "TileGrid_Drop", "RefreshCatalog", "RefreshTileEditor" })
            Assert.IsFalse(monitorCode.Contains(removedEditorContract, StringComparison.Ordinal), $"Native layout editing belongs in the main app: {removedEditorContract}");
    }

    [TestMethod]
    public void NativeTrendRendering_CoastsCachedGeometryAtDisplayCadenceWithoutInventingTelemetry()
    {
        var root = CompanionAppRoot();
        var monitorCode = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.App", "LiveMonitorWindow.xaml.cs"));

        foreach (var displayCadenceContract in new[]
        {
            "Stopwatch.GetTimestamp()",
            "Stopwatch.GetElapsedTime(_lastArrivalTimestamp, frameTimestamp)",
            "visual.AnimateTrend(frameTimestamp, motionEnabled)",
            "var maximumCoast = 1.5d / _sourceTickRate",
            "var target = -motion.Shift",
            "RenderTransform = translation",
            "private readonly record struct TrendMotion(double Shift, bool Continue)"
        })
            StringAssert.Contains(monitorCode, displayCadenceContract);

        foreach (var cachedGeometryContract in new[]
        {
            "var trace = new Path",
            "BuildTrendGeometry(buffer, width, height",
            "var geometry = new StreamGeometry()",
            "trace.Data = values.Count < 2",
            "if (!buffer.Update(reading, state)) return;"
        })
            StringAssert.Contains(monitorCode, cachedGeometryContract);

        foreach (var sourceIntegrityContract in new[]
        {
            "LiveTelemetryCatalog.TrendValue(_metricId, point, _unit)",
            "state.History ?? []",
            "Nullable.Equals(latest.Value, value)",
            "if (!sample.Value.HasValue || !double.IsFinite(sample.Value.Value))",
            "sample.At - previousAt.Value > buffer.GapThreshold",
            "lapRegressed || IsClockRegression(at, progress)"
        })
            StringAssert.Contains(monitorCode, sourceIntegrityContract);

        StringAssert.Contains(monitorCode, "var motionEnabled = _state.LiveState.Snapshot.Connected && !_state.Settings.UseReducedMotion");
        StringAssert.Contains(monitorCode, "if (!IsVisible || _scaleSettingsOpen) return;");
        StringAssert.Contains(monitorCode, "if (!enabled || _activeNumericCount < 2 || _lastArrivalTimestamp <= 0)");
        Assert.IsFalse(monitorCode.Contains("new Polyline", StringComparison.Ordinal), "A display frame should translate cached geometry instead of rebuilding a point collection.");
        Assert.IsFalse(monitorCode.Contains("_trendTimer", StringComparison.Ordinal), "Trend motion must follow composition frames, not a coarse synthetic timer.");
    }

    [TestMethod]
    public void LiveTelemetryPresentation_LazyMountsDetailsAndKeepsManualHideUntilTheNextConnection()
    {
        var root = CompanionAppRoot();
        var page = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.UI", "LiveTelemetryPage.razor"));
        var grid = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.UI", "LiveTelemetryLayoutGrid.razor"));
        var state = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.Coordinator", "CompanionState.cs"));
        var mainWindow = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.App", "MainWindow.xaml.cs"));

        StringAssert.Contains(page, "@if (_tracesMounted)");
        StringAssert.Contains(page, "if (!State.Settings.UseReducedMotion) await Task.Delay(500);");
        StringAssert.Contains(page, "<LiveTelemetryVisuals State=\"State\" />");
        Assert.AreEqual(1, page.Split("Waiting for iRacing", StringSplitOptions.None).Length - 1, "The disconnected dashboard needs one explanation, not repeated status copy.");
        Assert.DoesNotContain("Waiting for iRacing", grid);
        StringAssert.Contains(grid, "live-tile-placeholder");
        StringAssert.Contains(grid, "catalogReading.Available");

        StringAssert.Contains(state, "Action<bool, bool>? LiveMonitorVisibilityRequested");
        StringAssert.Contains(state, "_liveMonitorAutoReopenSuppressed");
        StringAssert.Contains(state, "!Settings.LiveMonitor.Visible && !_liveMonitorAutoReopenSuppressed");
        StringAssert.Contains(state, "Settings.LiveMonitor.Visible = true;");
        StringAssert.Contains(state, "PersistSettingsQuietly();");
        StringAssert.Contains(state, "LiveMonitorVisibilityRequested?.Invoke(true, false)");
        StringAssert.Contains(mainWindow, "OnMonitorVisibilityRequested(bool visible, bool activate)");
        StringAssert.Contains(mainWindow, "if (visible && !_state.LiveMonitorVisible) return;");
        StringAssert.Contains(mainWindow, "_liveMonitor.ShowMonitor(activate)");
        StringAssert.Contains(mainWindow, "HideToTray(false)");
        StringAssert.Contains(mainWindow, "var returnToPrimary = _liveMonitor.IsVisible && !_restoringPrimaryUi;");
        StringAssert.Contains(mainWindow, "if (returnToPrimary) ShowFromTray()");
        StringAssert.Contains(mainWindow, "_trayIcon.MouseClick");
        StringAssert.Contains(mainWindow, "args.Button == Forms.MouseButtons.Left");
        Assert.DoesNotContain("_trayIcon.DoubleClick", mainWindow);
        StringAssert.Contains(mainWindow, "_liveMonitor.CloseMonitor");
    }

    [TestMethod]
    public void LiveTelemetryViewportAndCharts_FitThePageAndShareInstrumentStyling()
    {
        var root = CompanionAppRoot();
        var ui = Path.Combine(root, "src", "iRacingCoach.UI");
        var page = File.ReadAllText(Path.Combine(ui, "LiveTelemetryPage.razor"));
        var razor = File.ReadAllText(Path.Combine(ui, "LiveTelemetryLayoutGrid.razor"));
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "live-telemetry.css"));
        var layout = File.ReadAllText(Path.Combine(ui, "wwwroot", "live-telemetry-layout.js"));
        var charts = File.ReadAllText(Path.Combine(ui, "wwwroot", "live-telemetry-tile-charts.js"));
        var host = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.App", "wwwroot", "index.html"));
        var previewHost = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.Preview", "Components", "App.razor"));

        StringAssert.Contains(page, "class=\"live-telemetry-page\" data-live-telemetry-page");
        StringAssert.Contains(page, "data-live-trailing-panel");
        StringAssert.Contains(css, ".page-frame:has(.live-telemetry-page)");
        StringAssert.Contains(css, "height: calc(100dvh - var(--command-bar-height))");
        StringAssert.Contains(css, "overflow: hidden");
        StringAssert.Contains(css, ".live-telemetry-page > .live-layout-studio");
        StringAssert.Contains(css, "flex: 1 1 0");
        StringAssert.Contains(css, ".live-layout-tile.style-trend .live-tile-content");
        StringAssert.Contains(css, "grid-template-rows: minmax(0, 1fr) auto");
        StringAssert.Contains(css, ".live-layout-tile.style-trend .live-tile-chart");
        StringAssert.Contains(css, "height: 100%");
        StringAssert.Contains(layout, "state.root.closest(\"[data-live-telemetry-page]\")");
        StringAssert.Contains(layout, "viewport.style.removeProperty(\"height\")");
        StringAssert.Contains(charts, "for (let index = 1; index < 4; index++)");
        StringAssert.Contains(host, "_content/iRacingCoach.UI/live-telemetry.css");
        StringAssert.Contains(previewHost, "_content/iRacingCoach.UI/live-telemetry.css");
        StringAssert.Contains(razor, "class=\"live-tile-chart\"");
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
        foreach (var snapContract in new[] { "snapHysteresis", "data-live-resize", "CommitTilePlacement", "DropMetric", "ReplaceMetric", "ReplaceTile", "requestAnimationFrame", "Escape", "placementCanPack", "state.committing", "lostpointercapture", "windowBlur", "autoScroll", "captureTileGeometry", "state.latestPointer", "session.metrics", "tileAtPointer", "tileAtCell", "excludedTileId", "replacementTileId", "Replace ${target.name} with ${session.metricName}" })
            StringAssert.Contains(script, snapContract);
        Assert.AreEqual(2, script.Split("const target = inside ? tileAtPointer", StringSplitOptions.None).Length - 1, "Both an existing widget move and a toolbox metric drop must detect an occupied target.");
        StringAssert.Contains(script, "await state.dotnet.invokeMethodAsync(\"ReplaceTile\", session.original.id, session.replacementTileId)");
        StringAssert.Contains(razor, "public async Task<bool> ReplaceTile(string sourceTileId, string targetTileId)");
        StringAssert.Contains(script, "event.target.closest(\"[data-live-drag-tile]\")");
        StringAssert.Contains(script, "const viewport = state.root.querySelector(\"[data-live-grid-viewport]\")");
        StringAssert.Contains(script, "grid.style.width = \"100%\"");
        StringAssert.Contains(script, "grid.style.height = \"100%\"");
        StringAssert.Contains(script, "const scrollHost = state.root.closest(\".workspace\")");
        StringAssert.Contains(script, "const visibleTop = Math.max(0, viewportTop, hostTop)");
        StringAssert.Contains(script, "const visibleBottom = Math.min(window.innerHeight, hostBottom)");
        StringAssert.Contains(script, "visibleBottom - visibleTop - 14");
        StringAssert.Contains(script, "viewport.style.height = `${availableHeight}px`");
        var tileChartScript = File.ReadAllText(Path.Combine(ui, "wwwroot", "live-telemetry-tile-charts.js"));
        StringAssert.Contains(tileChartScript, "clamp(point.lapDistance, 0, 1)");
        StringAssert.Contains(razor, "State.LiveState.SessionEpoch");
        StringAssert.Contains(tileChartScript, "nextSessionEpoch");
        StringAssert.Contains(tileChartScript, "resetCharts(studio)");
        StringAssert.Contains(tileChartScript, "if (!updateOptions(studio, options)) return;");
        var liveChartScript = File.ReadAllText(Path.Combine(ui, "wwwroot", "live-telemetry-chart.js"));
        StringAssert.Contains(liveChartScript, "nextSessionEpoch");
        StringAssert.Contains(liveChartScript, "clearPoints(state)");
        StringAssert.Contains(css, "repeat(var(--live-columns),minmax(0,1fr))");
        Assert.IsFalse(script.Contains("Math.min(availableWidth / columns, availableHeight / rows)", StringComparison.Ordinal), "Dashboard rows and columns must share the full container instead of forcing square cells.");
        StringAssert.Contains(script, "replacementMessage || `${placement.columnSpan} x ${placement.rowSpan}`");
        Assert.IsFalse(script.Contains("- ready", StringComparison.OrdinalIgnoreCase), "A resize preview should show only the occupied grid size.");
        StringAssert.Contains(script, "const noDragTarget = event.target.closest(\"[data-live-no-drag]\")");
        StringAssert.Contains(script, "const metricHandle = noDragTarget ? null : event.target.closest(\"[data-live-drag-metric]\")");
        StringAssert.Contains(script, "event.preventDefault()");
        StringAssert.Contains(host, "live-telemetry-layout.js");
        StringAssert.Contains(previewHost, "live-telemetry-layout.js");
        StringAssert.Contains(host, "live-telemetry-layout.js?v=0.16.0-viewport-fit");
        StringAssert.Contains(host, "live-telemetry-tile-charts.js?v=0.16.0-instrument-grid");
        StringAssert.Contains(previewHost, "@Assets[\"_content/iRacingCoach.UI/live-telemetry-layout.js\"]");
        StringAssert.Contains(previewHost, "@Assets[\"_content/iRacingCoach.UI/coach.css\"]");
        Assert.DoesNotContain("?v=0.16.0", previewHost,
            "The Preview host uses generated static-asset fingerprints instead of manual query-string versions.");

        foreach (var interactionHook in new[]
        {
            "data-live-layout-studio", "data-live-grid", "data-live-tile", "data-tile-id", "data-live-drag-tile",
            "data-live-resize", "data-live-catalog-item", "data-metric-id", "data-live-drag-metric"
        })
            StringAssert.Contains(razor, interactionHook);

        StringAssert.Contains(razor, "live-toolbox");
        Assert.DoesNotContain("live-toolbox-backdrop", razor);
        StringAssert.Contains(razor, "inert=\"@(!_toolboxOpen ? string.Empty : null)\"");
        StringAssert.Contains(razor, "private bool _editing;");
        StringAssert.Contains(razor, "FinishCustomize");
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
        StringAssert.Contains(css, "@container (max-height: 76px)");
        StringAssert.Contains(css, "grid-template-columns: minmax(0,1fr)");
        StringAssert.Contains(css, ".live-layout-viewport");
        StringAssert.Contains(css, "place-items: center");
        StringAssert.Contains(css, ".live-layout-viewport { min-width: 0; min-height: 320px; height: clamp(");
        StringAssert.Contains(script, "const visibleTop = Math.max(0, viewportTop, hostTop)");
        StringAssert.Contains(script, "const visibleBottom = Math.min(window.innerHeight, hostBottom)");
        StringAssert.Contains(script, "visibleBottom - visibleTop - 14");
        StringAssert.Contains(script, "viewport.style.height = `${availableHeight}px`");
        StringAssert.Contains(script, "viewport.style.minHeight = `${Math.min(320, availableHeight)}px`");
        StringAssert.Contains(script, "getPropertyValue(\"--motion-structure\")");
        StringAssert.Contains(script, "const duration = motionMilliseconds(state.root)");
        Assert.DoesNotContain("duration: 180", script);
        Assert.DoesNotContain("duration: 200", script);
        Assert.IsFalse(razor.Contains("live-editor-guidance", StringComparison.Ordinal), "The editor should not spend a header row on static drag instructions or success chatter.");
        Assert.IsFalse(razor.Contains("Undo available", StringComparison.OrdinalIgnoreCase), "Undo controls already communicate recoverability; removal actions should stay concise.");
        Assert.IsFalse(monitorCode.Contains("Undo is available", StringComparison.OrdinalIgnoreCase), "The native monitor should not add undo instructions to routine completion messages.");
        Assert.IsFalse(razor.Contains("OverallScale", StringComparison.Ordinal), "Physical scale belongs only to the pop-out monitor, not the fitted full-page dashboard.");
        Assert.DoesNotContain(".live-toolbox-backdrop", css);
        StringAssert.Contains(css, "--command-bar-height: 28px;");
        StringAssert.Contains(css, "--motion-structure: 500ms;");
        Assert.DoesNotContain("--toolbox-motion", css);
        StringAssert.Contains(css, ".live-toolbox { position: fixed;");
        StringAssert.Contains(css, "top: var(--command-bar-height);");
        StringAssert.Contains(css, "transition: opacity var(--motion-structure) var(--ease),transform var(--motion-structure) var(--ease),box-shadow var(--motion-structure) var(--ease),visibility 0s linear var(--motion-structure);");
        StringAssert.Contains(css, ".page-frame:has(.live-layout-studio),");
        StringAssert.Contains(css, ".analysis-page-frame:has(.analysis-trace-studio)");
        StringAssert.Contains(css, "transition: padding-right var(--motion-structure) var(--ease);");
        StringAssert.Contains(css, ".page-frame:has(.live-layout-studio.toolbox-open)");
        StringAssert.Contains(css, "padding-right: calc(var(--space-7) + var(--side-toolbox-width));");
        StringAssert.Contains(css, ".analysis-page-frame:has(.analysis-trace-studio.toolbox-open)");
        StringAssert.Contains(css, "padding-right: calc(22px + var(--side-toolbox-width));");
        StringAssert.Contains(css, "--live-toolbox-width:var(--side-toolbox-width)");
        Assert.IsFalse(razor.Contains("Ã", StringComparison.Ordinal), "Razor markup must not contain mojibake glyphs.");
        Assert.IsFalse(razor.Contains("Â", StringComparison.Ordinal), "Razor markup must not contain mojibake glyphs.");
        Assert.IsFalse(script.Contains("Ã", StringComparison.Ordinal), "Pointer script must not contain mojibake glyphs.");
        Assert.IsFalse(script.Contains("Â", StringComparison.Ordinal), "Pointer script must not contain mojibake glyphs.");
    }

    [TestMethod]
    public void LiveTelemetryRendering_HotPathsAreFrameSyncedAndDoNotForceLayoutPerFrame()
    {
        var ui = Path.Combine(CompanionAppRoot(), "src", "iRacingCoach.UI");
        var tileCharts = File.ReadAllText(Path.Combine(ui, "wwwroot", "live-telemetry-tile-charts.js"));
        var drivingChart = File.ReadAllText(Path.Combine(ui, "wwwroot", "live-telemetry-chart.js"));
        var layout = File.ReadAllText(Path.Combine(ui, "wwwroot", "live-telemetry-layout.js"));
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));

        StringAssert.Contains(tileCharts, "chart.pointStart");
        StringAssert.Contains(tileCharts, "buildPixelEnvelope");
        StringAssert.Contains(tileCharts, "requestStudioDraw(studio)");
        StringAssert.Contains(tileCharts, "resizeSettleMilliseconds");
        Assert.DoesNotContain("chart.points.splice(0", tileCharts,
            "A rolling telemetry window must advance an index instead of shifting the whole history every SDK tick.");
        Assert.DoesNotContain("const unique = new Map()", tileCharts,
            "Pixel-envelope construction must not allocate a Map for every plotted pixel.");
        Assert.DoesNotContain("offsetParent", tileCharts,
            "A display-frame loop must not force style/layout through offsetParent.");
        Assert.DoesNotContain("requestAnimationFrame(next => drawStudio", tileCharts,
            "An idle or hidden telemetry dashboard must not retain a permanent animation loop.");

        StringAssert.Contains(drivingChart, "pendingWidth");
        StringAssert.Contains(drivingChart, "ResizeObserver(entries =>");
        Assert.DoesNotContain("offsetParent", drivingChart);
        Assert.AreEqual(1, drivingChart.Split("getBoundingClientRect()", StringSplitOptions.None).Length - 1,
            "The driving chart may measure once at initialization; display frames use ResizeObserver dimensions.");

        StringAssert.Contains(layout, "state.pointerMoveFrame = requestAnimationFrame");
        StringAssert.Contains(layout, "captureTileGeometry(state)");
        StringAssert.Contains(layout, "translate3d(");
        Assert.DoesNotContain("session.ghost.offsetWidth", layout);
        Assert.DoesNotContain("session.preview.style.left", layout);
        Assert.DoesNotContain("transition: left var(--motion-hover) var(--ease),top var(--motion-hover)", css,
            "The drop preview follows the pointer directly rather than trailing a geometry tween.");
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
            tile.DisplayStyle, tile.Unit, tile.Precision, tile.TrendDuration, tile.Accent, tile.HighlightAbsIntervention))));

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

    private static string CompanionAppRoot() => TestRepositoryPaths.CompanionAppRoot;

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
