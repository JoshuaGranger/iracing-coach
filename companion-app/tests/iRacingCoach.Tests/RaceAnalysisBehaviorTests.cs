using System.Text.RegularExpressions;
using System.Text.Json;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class RaceAnalysisBehaviorTests
{
    [TestMethod]
    public void TraceLayout_DefaultsToTenOrderedRowsWithTruthfulPairedSignals()
    {
        var layout = new AnalysisTraceLayout();

        Assert.IsTrue(AnalysisTraceLayouts.ValidateAndRepair(layout));
        Assert.HasCount(10, layout.Rows);
        CollectionAssert.AreEqual(
            new[] { "speed", "delta", "throttle", "brake", "tire-wear", "gear", "rpm", "steering", "slip", "lateral-g" },
            layout.Rows.Select(row => row.PrimarySignalId).ToArray());
        Assert.AreEqual("yaw", layout.Rows[8].SecondarySignalId);
        Assert.AreEqual("longitudinal-g", layout.Rows[9].SecondarySignalId);
    }

    [TestMethod]
    public void TraceCatalog_CoversWorthwhileRecordedFamiliesWithoutConflatingPhysicalSignals()
    {
        var signals = AnalysisTraceLayouts.Signals.ToDictionary(signal => signal.Id, StringComparer.Ordinal);

        foreach (var id in new[]
        {
            "clutch", "abs-active", "abs-cut", "brake-bias", "steering-torque",
            "vertical-g", "pitch", "roll", "pitch-rate", "roll-rate",
            "fuel-level", "fuel-use-rate", "center-front-ride-height",
            "track-temperature", "air-temperature", "wind-speed", "humidity", "fog", "precipitation",
            "air-pressure", "air-density", "track-wetness", "track-usage", "weather-wet",
            "overall-position", "class-position", "distance-ahead", "distance-behind", "on-pit-road", "track-surface"
        })
            Assert.IsTrue(signals.ContainsKey(id), $"Expected truthful optional trace metadata for {id}.");

        foreach (var corner in new[] { "lf", "rf", "lr", "rr" })
        {
            foreach (var suffix in new[] { "wheel-speed", "wheel-slip", "pressure", "carcass-temp", "surface-temp", "ride-height", "shock-deflection", "shock-velocity" })
                Assert.IsTrue(signals.ContainsKey($"{corner}-{suffix}"), $"Expected {corner}-{suffix} in the optional trace catalog.");
        }

        Assert.AreEqual("deg", signals["slip"].Unit, "Vehicle sideslip keeps its angular unit and distinct identity.");
        Assert.AreEqual("%", signals["lf-wheel-slip"].Unit, "Derived wheel spin/lock remains a separate percentage signal.");
        Assert.AreNotEqual(signals["slip"].Id, signals["lf-wheel-slip"].Id);
        Assert.AreEqual("on / off", signals["abs-active"].Unit);
        Assert.AreEqual("kg/h", signals["fuel-use-rate"].Unit, "Native fuel-use rate is mass flow and must not be mislabeled as gal/h.");
        Assert.AreEqual("conditions", signals["track-temperature"].AccentGroup);
    }

    [TestMethod]
    public void TraceLayout_RepairsInvalidRowsAndAllowsAnyDistinctSecondSignal()
    {
        var duplicateId = "same-row";
        var layout = new AnalysisTraceLayout
        {
            Rows = Enumerable.Range(0, 12).Select(index => new AnalysisTraceRow
            {
                Id = index < 2 ? duplicateId : $"row-{index}",
                PrimarySignalId = index == 0 ? "missing" : "speed",
                SecondarySignalId = index == 1 ? "brake" : string.Empty
            }).ToList()
        };

        Assert.IsTrue(AnalysisTraceLayouts.ValidateAndRepair(layout));
        Assert.HasCount(AnalysisTraceLayouts.MaximumRows, layout.Rows);
        Assert.AreEqual(layout.Rows.Count, layout.Rows.Select(row => row.Id).Distinct(StringComparer.Ordinal).Count());
        Assert.AreEqual("speed", layout.Rows[0].PrimarySignalId);
        Assert.AreEqual("brake", layout.Rows[1].SecondarySignalId);
        Assert.IsTrue(AnalysisTraceLayouts.SetPrimary(layout, layout.Rows[0].Id, "throttle"));
        Assert.IsTrue(AnalysisTraceLayouts.SetSecondary(layout, layout.Rows[0].Id, "brake"));
        Assert.IsTrue(AnalysisTraceLayouts.SetSecondary(layout, layout.Rows[0].Id, "rpm"), "Signals with unrelated units may share a row because each receives an independent scale.");
        Assert.IsTrue(AnalysisTraceLayouts.SetPrimary(layout, layout.Rows[0].Id, "speed"));
        Assert.AreEqual("rpm", layout.Rows[0].SecondarySignalId);
        Assert.IsFalse(AnalysisTraceLayouts.SetSecondary(layout, layout.Rows[0].Id, "speed"), "The same signal cannot fill both slots.");
        Assert.IsTrue(AnalysisTraceLayouts.SecondarySignalOptions("gear").Any(signal => signal.Id == "rpm"));
        Assert.IsTrue(AnalysisTraceLayouts.SecondarySignalOptions("gear").Any(signal => signal.Id == "brake"));
    }

    [TestMethod]
    public void TraceLayout_AddMoveRemoveAndPortableRoundTripPreserveConfiguration()
    {
        var layout = new AnalysisTraceLayout { Rows = [new AnalysisTraceRow { PrimarySignalId = "speed" }] };
        Assert.IsTrue(AnalysisTraceLayouts.AddRow(layout));
        var addedId = layout.Rows[1].Id;
        Assert.IsTrue(AnalysisTraceLayouts.MoveRow(layout, addedId, -1));
        Assert.AreEqual(addedId, layout.Rows[0].Id);

        var settings = new CompanionSettings { RaceAnalysisTraces = layout };
        var restored = JsonSerializer.Deserialize<CompanionSettings>(JsonSerializer.Serialize(settings))!;
        Assert.IsFalse(AnalysisTraceLayouts.ValidateAndRepair(restored.RaceAnalysisTraces));
        Assert.AreEqual(addedId, restored.RaceAnalysisTraces.Rows[0].Id);
        Assert.IsTrue(AnalysisTraceLayouts.RemoveRow(restored.RaceAnalysisTraces, addedId));
        Assert.IsFalse(AnalysisTraceLayouts.RemoveRow(restored.RaceAnalysisTraces, restored.RaceAnalysisTraces.Rows[0].Id));
    }

    [TestMethod]
    public void NamedAnalysisLayouts_KeepDefaultImmutableAndLifecycleSeparateFromLiveDashboards()
    {
        var preferences = new AnalysisTraceLayoutSet();
        var factory = AnalysisTraceLayoutSets.Active(preferences);

        Assert.IsTrue(factory.IsFactory);
        Assert.AreEqual(AnalysisTraceLayoutSet.FactoryDefaultId, factory.Named.Id);
        Assert.AreEqual("Default", factory.Named.Name);
        Assert.IsFalse(AnalysisTraceLayoutSets.RenameActive(preferences, "Changed"));
        Assert.IsFalse(AnalysisTraceLayoutSets.DeleteActive(preferences));

        factory.Named.Layout.Rows[0].PrimarySignalId = "brake";
        Assert.AreEqual("speed", AnalysisTraceLayoutSets.Active(preferences).Named.Layout.Rows[0].PrimarySignalId,
            "The factory choice must be a clone that callers cannot mutate.");

        var editableDefault = AnalysisTraceLayoutSets.EnsureEditable(preferences);
        Assert.AreEqual("Default Copy", editableDefault.Name);
        Assert.AreNotEqual(AnalysisTraceLayoutSet.FactoryDefaultId, editableDefault.Id);
        editableDefault.Layout.Rows[0].PrimarySignalId = "throttle";
        Assert.AreEqual("speed", AnalysisTraceLayoutSets.Choices(preferences).Single(choice => choice.IsFactory).Named.Layout.Rows[0].PrimarySignalId);

        var first = AnalysisTraceLayoutSets.Create(preferences);
        var second = AnalysisTraceLayoutSets.Create(preferences);
        Assert.AreEqual("Custom", first.Name);
        Assert.AreEqual("Custom 2", second.Name);
        Assert.IsTrue(AnalysisTraceLayoutSets.RenameActive(preferences, "  Oval   review  "));
        Assert.AreEqual("Oval review", second.Name);
        Assert.IsTrue(AnalysisTraceLayoutSets.Select(preferences, first.Id));
        Assert.IsTrue(AnalysisTraceLayoutSets.RenameActive(preferences, "OVAL REVIEW"));
        Assert.AreEqual("OVAL REVIEW 2", first.Name, "Analysis layout names must remain unique without case-sensitive duplicates.");
        Assert.IsFalse(AnalysisTraceLayoutSets.Select(preferences, "missing-layout"));

        var live = new LiveMonitorLayout { ActiveLayoutId = LiveMonitorLayouts.FactoryRaceId };
        var settings = new CompanionSettings { LiveMonitor = live, RaceAnalysisTraceLayouts = preferences };
        var restored = JsonSerializer.Deserialize<CompanionSettings>(JsonSerializer.Serialize(settings))!;
        Assert.IsFalse(AnalysisTraceLayoutSets.ValidateAndRepair(restored.RaceAnalysisTraceLayouts));
        Assert.AreEqual(first.Id, restored.RaceAnalysisTraceLayouts.ActiveLayoutId);
        Assert.AreEqual(LiveMonitorLayouts.FactoryRaceId, restored.LiveMonitor.ActiveLayoutId,
            "Race Analysis layouts must not reuse or mutate Live Telemetry dashboard selection.");

        Assert.IsTrue(AnalysisTraceLayoutSets.DeleteActive(restored.RaceAnalysisTraceLayouts));
        Assert.AreEqual(AnalysisTraceLayoutSet.FactoryDefaultId, restored.RaceAnalysisTraceLayouts.ActiveLayoutId);
        Assert.IsFalse(restored.RaceAnalysisTraceLayouts.UserLayouts.Any(layout => layout.Id == first.Id));

        var telemetry = File.ReadAllText(Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI", "TelemetryWorkspace.razor"));
        foreach (var hook in new[] { "data-analysis-layout-select", "data-analysis-layout-new", "data-analysis-layout-duplicate", "data-analysis-layout-rename", "data-analysis-layout-delete", "data-analysis-layout-default" })
            StringAssert.Contains(telemetry, hook);
        StringAssert.Contains(telemetry, "disabled=\"@ActiveTraceLayout.IsFactory\"");
        StringAssert.Contains(telemetry, "Default is protected. Editing creates a copy.");
        StringAssert.Contains(telemetry, "private AnalysisTraceLayout EditableTraceLayout => AnalysisTraceLayoutSets.EnsureEditable(TraceLayoutPreferences).Layout;");
        StringAssert.Contains(telemetry, "private void ChangeTraceLayout(ChangeEventArgs args)");
        StringAssert.Contains(telemetry, "private void CreateTraceLayout()");
        StringAssert.Contains(telemetry, "private void DuplicateTraceLayout()");
        StringAssert.Contains(telemetry, "private void DeleteTraceLayout()");
        StringAssert.Contains(telemetry, "private void RenameTraceLayout(ChangeEventArgs args)");
    }

    [TestMethod]
    public void NamedAnalysisLayouts_DeletedDefaultCopyDoesNotReturnThroughLegacyBridgeOnRender()
    {
        var preferences = new AnalysisTraceLayoutSet();
        var legacy = new AnalysisTraceLayout();

        Assert.IsTrue(AnalysisTraceLayoutSets.ValidateAndRepair(preferences, legacy));
        Assert.IsTrue(preferences.LegacyLayoutImportCompleted);
        Assert.IsEmpty(preferences.UserLayouts, "An untouched legacy default should remain the immutable factory layout.");

        var editable = AnalysisTraceLayoutSets.EnsureEditable(preferences);
        editable.Layout.Rows[0].PrimarySignalId = "throttle";
        legacy = AnalysisTraceLayoutSets.CloneLayout(editable.Layout);
        Assert.IsTrue(AnalysisTraceLayoutSets.DeleteActive(preferences));
        Assert.AreEqual(AnalysisTraceLayoutSet.FactoryDefaultId, preferences.ActiveLayoutId);

        Assert.IsFalse(AnalysisTraceLayoutSets.ValidateAndRepair(preferences, legacy),
            "A stale mutable-layout bridge must not resurrect a deliberately deleted modern layout.");
        var renderedChoices = AnalysisTraceLayoutSets.Choices(preferences);
        Assert.HasCount(1, renderedChoices);
        Assert.IsTrue(renderedChoices.Single().IsFactory);
        Assert.IsTrue(AnalysisTraceLayoutSets.Active(preferences).IsFactory);

        var restored = JsonSerializer.Deserialize<AnalysisTraceLayoutSet>(JsonSerializer.Serialize(preferences))!;
        Assert.IsTrue(restored.LegacyLayoutImportCompleted, "The one-time migration decision must survive restart.");
        Assert.IsFalse(AnalysisTraceLayoutSets.ValidateAndRepair(restored, legacy));
        Assert.HasCount(1, AnalysisTraceLayoutSets.Choices(restored));
        Assert.IsEmpty(restored.UserLayouts);
    }

    [TestMethod]
    public void NamedAnalysisLayouts_CustomLegacyLayoutStillImportsExactlyOnce()
    {
        var legacy = new AnalysisTraceLayout
        {
            Rows =
            [
                new AnalysisTraceRow
                {
                    Id = "legacy-throttle",
                    PrimarySignalId = "throttle",
                    SecondarySignalId = "brake"
                }
            ]
        };
        var preferences = new AnalysisTraceLayoutSet();

        Assert.IsTrue(AnalysisTraceLayoutSets.ValidateAndRepair(preferences, legacy));
        var imported = preferences.UserLayouts.Single();
        Assert.AreEqual("Previous layout", imported.Name);
        Assert.AreEqual(imported.Id, preferences.ActiveLayoutId);
        Assert.IsTrue(preferences.LegacyLayoutImportCompleted);

        Assert.IsFalse(AnalysisTraceLayoutSets.ValidateAndRepair(preferences, legacy));
        Assert.HasCount(1, preferences.UserLayouts);
    }

    [TestMethod]
    public void RichTrackMap_DefaultsToTracesAndKeepsEveryPaletteAndLayerTruthful()
    {
        var ui = Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI");
        var telemetry = File.ReadAllText(Path.Combine(ui, "TelemetryWorkspace.razor"));
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));
        var performanceCss = File.ReadAllText(Path.Combine(ui, "wwwroot", "analysis-performance.css"));

        StringAssert.Contains(telemetry, "<span>Type</span><select @ref=\"MapTypeElement\" data-map-type value=\"@Mode\" @onchange=\"ChangeMapMode\"");
        StringAssert.Contains(telemetry, "[(\"traces\", \"Traces\"), (\"speed\", \"Speed\"), (\"throttle\", \"Throttle\"), (\"brake\", \"Brake\"), (\"stress\", \"Tire load\")]");
        StringAssert.Contains(telemetry, "Mode = \"traces\";");
        StringAssert.Contains(telemetry, "data-analysis-track-map data-map-type=\"@Mode\"");
        foreach (var layer in new[] { "main", "lap-trace", "pit-road", "pit-entry", "pit-exit", "commitment", "start-finish" })
            StringAssert.Contains(telemetry, $"data-map-layer=\"{layer}\"");
        StringAssert.Contains(telemetry, "@if (Mode != \"traces\")");
        StringAssert.Contains(telemetry, "data-map-legend data-mode=\"@Mode\"");
        Assert.DoesNotContain("--legend-color:{trace.Color}", telemetry, "Trace identity belongs on the line/cursor, not in a space-consuming map footer.");
        Assert.DoesNotContain("map-trace-unavailable", telemetry);
        StringAssert.Contains(telemetry, ".GroupBy(item => LapColor(item.Trace.Lap))");
        StringAssert.Contains(telemetry, "AnalysisRenderBudget.RepresentativeIndices(item.Points.Count, budget)");
        StringAssert.Contains(telemetry, "return SvgPath(points, item.Trace.Complete);");
        StringAssert.Contains(telemetry, "Quality.MainLoopComplete: true");
        StringAssert.Contains(telemetry, "if (CompleteVectorGeometry is { } vector)");
        StringAssert.Contains(telemetry, ".Select((point, index) => ProjectVectorPoint(point with { LapPercent = point.LapPercent ?? index / (double)vector.MainPath.Count }))");
        StringAssert.Contains(telemetry, "AnalysisTrackMapGeometry.IsPlausibleOverlayPath(vector.MainPath, vector.PitLane)");
        StringAssert.Contains(telemetry, "AnalysisTrackMapGeometry.IsPlausibleOverlayPath(vector.MainPath, vector.PitEntryPath)");
        StringAssert.Contains(telemetry, "AnalysisTrackMapGeometry.IsPlausibleOverlayPath(vector.MainPath, vector.PitExitPath)");
        StringAssert.Contains(telemetry, "AnalysisTrackMapGeometry.IsPlausibleOverlayLine(vector.MainPath, vector.PitCommitmentLine)");
        StringAssert.Contains(telemetry, "AnalysisTrackMapGeometry.IsPlausibleOverlayLine(vector.MainPath, vector.PitMergeLine)");
        StringAssert.Contains(telemetry, "? vector.MainPath.ToArray()");
        StringAssert.Contains(telemetry, "AnalysisTrackMapGeometry.IsCanonicalMainLoop(vector.MainPath)");
        StringAssert.Contains(telemetry, "AnalysisTrackMapGeometry.IsPlausibleOverlayPath(vector.MainPath, vector.PitEntryPath)");
        StringAssert.Contains(telemetry, "AnalysisTrackMapGeometry.IsPlausibleOverlayLine(vector.MainPath, recorded)");
        StringAssert.Contains(telemetry, "AnalysisTrackMapGeometry.IsPlausibleNormalizedPoint(vector.MainPath, normalizedX, normalizedY)");
        Assert.DoesNotContain(".Concat(vector.PitLane)", telemetry, "Pit geometry must never redefine the main-loop projection or Fit bounds.");
        Assert.DoesNotContain(".Concat(vector.PitEntryPath)", telemetry);
        Assert.DoesNotContain(".Concat(vector.PitExitPath)", telemetry);
        StringAssert.Contains(telemetry, "new(0.00, 232, 66, 78)");
        StringAssert.Contains(telemetry, "new(1.00, 34, 220, 116)");
        StringAssert.Contains(telemetry, "\"brake\" => MixColor(normalized, (72, 79, 88), (255, 69, 86))");
        StringAssert.Contains(telemetry, "\"throttle\" => MixColor(normalized, (72, 79, 88), (42, 226, 126))");
        StringAssert.Contains(telemetry, "\"stress\" => MixColor(normalized, (247, 210, 74), (247, 65, 76))");
        StringAssert.Contains(telemetry, ".Select(point => point.TireStressProxy)");
        Assert.DoesNotContain("Mode is \"throttle\" or \"brake\" or \"stress\"", telemetry,
            "The selected tire-load range must span yellow to red instead of being pinned to zero through one.");
        StringAssert.Contains(telemetry, "private void RebuildMapRenderCache()");
        StringAssert.Contains(telemetry, "_vectorProjection = CreateVectorProjection(_canonicalGeometryPoints);");
        StringAssert.Contains(telemetry, "CompleteVectorGeometry?.Transform ?? CompleteVectorGeometry?.GeometryProvenance?.NormalizationTransform");
        StringAssert.Contains(telemetry, "transform?.TryNormalize(point.Longitude!.Value, point.Latitude!.Value");
        StringAssert.Contains(telemetry, "140 + (point.Y - projection.CenterY) * projection.Scale");
        Assert.DoesNotContain("140 - (point.Y - projection.CenterY) * projection.Scale", telemetry,
            "Canonical vector Y is already screen-oriented by the backend transform and must not be reflected a second time.");
        StringAssert.Contains(telemetry, "_trackRibbonPath = SvgPath(_mapPoints, HasMapGeometry);");
        StringAssert.Contains(telemetry, "maximumGap <= .05 && span > 0 && closure <= span * .15");
        StringAssert.Contains(telemetry, "if (_mapTracePaths is not null && string.Equals(_mapTracePathsSignature, SelectionSignature");
        StringAssert.Contains(telemetry, "if (_metricRibbonCache.TryGetValue(key, out var cached)) return cached;");
        StringAssert.Contains(telemetry, "const int bins = 160;");
        StringAssert.Contains(telemetry, "var width = Math.Max(1, maximumOffset - minimumOffset);");
        StringAssert.Contains(telemetry, "MetricColor(Median([a.Metric, b.Metric]))");
        StringAssert.Contains(telemetry, "class=\"track-metric-ribbon-segment\"");
        Assert.DoesNotContain("class=\"track-metric-segment\"", telemetry);
        Assert.DoesNotContain("ProjectGpsPoint(AnalysisTracePoint point, IReadOnlyList<AnalysisTracePoint> geo)", telemetry,
            "GPS and vector bounds must be precomputed once per workspace, not rescanned for every rendered point.");

        StringAssert.Contains(css, ".map-scale-gradient.speed { background: linear-gradient(90deg,#e8424e,#f88440,#f2cb4c,#74de6c,#22dc74); }");
        StringAssert.Contains(css, ".map-scale-gradient.throttle { background: linear-gradient(90deg,#484f58,#2ae27e); }");
        StringAssert.Contains(css, ".map-scale-gradient.brake { background: linear-gradient(90deg,#484f58,#ff4556); }");
        StringAssert.Contains(css, ".map-scale-gradient.stress { background: linear-gradient(90deg,#f7d24a,#f7414c); }");
        Assert.IsTrue(Regex.IsMatch(performanceCss, @"\.track-lap-trace\s*\{[^}]*stroke-width:\s*1;", RegexOptions.Singleline));
        StringAssert.Contains(css, ".track-metric-ribbon-segment");
        Assert.IsTrue(Regex.IsMatch(css, @"\.track-pit-entry\s*\{[^}]*stroke:\s*#f0c94d;[^}]*stroke-dasharray:", RegexOptions.Singleline));
        Assert.IsTrue(Regex.IsMatch(css, @"\.track-pit-exit\s*\{[^}]*stroke:\s*#54a9ff;[^}]*stroke-dasharray:", RegexOptions.Singleline));
        var commitment = Regex.Match(css, @"\.track-pit-commitment\s*\{(?<body>[^}]*)\}");
        Assert.IsTrue(commitment.Success);
        Assert.DoesNotContain("stroke-dasharray", commitment.Groups["body"].Value, "Commitment and merge lines are solid.");
    }

    [TestMethod]
    public void RichTrackMap_RejectsPitBranchesAndKeepsNavigationScopedToTheRace()
    {
        var mainLoop = Enumerable.Range(0, 500)
            .Select(index =>
            {
                var percent = index / 500d;
                var angle = percent * Math.PI * 2;
                return new AnalysisVectorPoint(.5 + Math.Cos(angle) * .5, .42 + Math.Sin(angle) * .42, percent, 3);
            })
            .ToArray();
        var pitBranch = Enumerable.Range(0, 180)
            .Select(index =>
            {
                var percent = index / 179d;
                return new AnalysisVectorPoint(.04 + percent * .82, .21 + percent * .05, percent, 3);
            })
            .ToArray();

        Assert.IsTrue(iRacingCoach.UI.AnalysisTrackMapGeometry.IsCanonicalMainLoop(mainLoop),
            "A dense, complete Iowa-like oval must remain the projection anchor.");
        Assert.IsFalse(iRacingCoach.UI.AnalysisTrackMapGeometry.IsCanonicalMainLoop(pitBranch),
            "A dense pit branch with lap labels is not a closed canonical race surface.");
        var microscopicPoisonedLoop = mainLoop
            .Select(point => point with { X = point.X * .00005, Y = point.Y * .00005 })
            .ToArray();
        var continentalEntry = new[]
        {
            new AnalysisVectorPoint(.00004, .00004),
            new AnalysisVectorPoint(1, .44)
        };
        Assert.IsFalse(iRacingCoach.UI.AnalysisTrackMapGeometry.IsCanonicalMainLoop(microscopicPoisonedLoop),
            "A sentinel-compressed legacy loop must never become the projection anchor.");
        Assert.IsFalse(iRacingCoach.UI.AnalysisTrackMapGeometry.IsPlausibleOverlayPath(mainLoop, continentalEntry),
            "An off-canvas pit-entry discontinuity must be omitted defensively.");
        Assert.IsTrue(iRacingCoach.UI.AnalysisTrackMapGeometry.IsPlausibleNormalizedPoint(mainLoop, .5, .5));
        Assert.IsFalse(iRacingCoach.UI.AnalysisTrackMapGeometry.IsPlausibleNormalizedPoint(mainLoop, 17000, 8000));

        var root = CompanionRoot();
        var ui = Path.Combine(root, "src", "iRacingCoach.UI");
        var telemetry = File.ReadAllText(Path.Combine(ui, "TelemetryWorkspace.razor"));
        var map = File.ReadAllText(Path.Combine(ui, "wwwroot", "analysis-track-map.js"));
        var splitter = File.ReadAllText(Path.Combine(ui, "wwwroot", "analysis-context-splitter.js"));
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));
        var performanceCss = File.ReadAllText(Path.Combine(ui, "wwwroot", "analysis-performance.css"));
        var preview = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.Preview", "Components", "App.razor"));
        var app = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.App", "wwwroot", "index.html"));

        StringAssert.Contains(telemetry, "iracingCoachAnalysisTrackMap.initialize");
        StringAssert.Contains(telemetry, "iracingCoachAnalysisTrackMap.fit");
        StringAssert.Contains(telemetry, "iracingCoachAnalysisTrackMap.blur");
        StringAssert.Contains(telemetry, "Workspace.AnalysisId, TrackViewBox");
        StringAssert.Contains(telemetry, "aria-label=\"Fit track\"");
        StringAssert.Contains(map, "element.addEventListener(\"wheel\", state.wheel, { passive: false })");
        StringAssert.Contains(map, "const anchor = svgPoint(element, event.clientX, event.clientY)");
        StringAssert.Contains(map, "element.setPointerCapture?.(event.pointerId)");
        StringAssert.Contains(map, "const sameRace = state.raceKey === raceKey");
        StringAssert.Contains(map, "if (!sameRace) state.view = copyViewBox(base)");
        StringAssert.Contains(map, "else constrain(state, state.view)");
        StringAssert.Contains(map, "requestAnimationFrame(() => element.blur())");
        StringAssert.Contains(telemetry, "data-analysis-context-splitter");
        StringAssert.Contains(telemetry, "analysis-section-heading @(IsRaceWorkspace ? \"context-only\" : null)");
        StringAssert.Contains(telemetry, "@if (!IsRaceWorkspace)");
        StringAssert.Contains(telemetry, "role=\"separator\"");
        StringAssert.Contains(telemetry, "iracingCoachAnalysisContextSplitter.initialize");
        StringAssert.Contains(splitter, "let sharedRatio = 0.43");
        StringAssert.Contains(splitter, "const minimumRatio = 1 / 3");
        StringAssert.Contains(splitter, "const maximumRatio = 2 / 3");
        StringAssert.Contains(splitter, "splitter.setPointerCapture?.(event.pointerId)");
        Assert.DoesNotContain("localStorage", splitter, "The context split is intentionally one in-memory app-session preference.");
        StringAssert.Contains(css, "--analysis-context-track-share,.43fr");
        StringAssert.Contains(css, ".race-workstation.context-both .analysis-context-splitter");
        StringAssert.Contains(telemetry, "class=\"trace-row-remove\"");
        StringAssert.Contains(telemetry, "@onclick=\"() => RemoveTraceRow(panel.Preferences.Id)\"");
        Assert.DoesNotContain("Reset charts", telemetry);
        Assert.DoesNotContain("trace-layout-reset", telemetry);
        Assert.IsTrue(Regex.IsMatch(performanceCss, @"\.race-workstation \.lap-rail-row\s*\{[^}]*grid-template-columns:\s*max-content max-content minmax\(0,1fr\) max-content max-content max-content max-content;[^}]*grid-template-rows:\s*minmax\(36px,auto\);", RegexOptions.Singleline));
        StringAssert.Contains(performanceCss, ".race-workstation .lap-incident-column { grid-column: 5;");
        StringAssert.Contains(performanceCss, ".race-workstation .lap-pit-column { grid-column: 7;");
        StringAssert.Contains(preview, "analysis-performance.css");
        StringAssert.Contains(app, "analysis-performance.css");
        StringAssert.Contains(preview, "analysis-track-map.js");
        StringAssert.Contains(preview, "analysis-context-splitter.js");
        StringAssert.Contains(app, "analysis-track-map.js");
        StringAssert.Contains(app, "analysis-context-splitter.js");
    }

    [TestMethod]
    public void RaceTechnicalData_UsesFourFixedOverviewCardsAndTruthfulFullAreaInvestigations()
    {
        var ui = Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI");
        var page = File.ReadAllText(Path.Combine(ui, "AnalysisWorkspacePage.razor"));
        var technical = File.ReadAllText(Path.Combine(ui, "RaceTechnicalData.razor"));
        var unavailable = File.ReadAllText(Path.Combine(ui, "TechnicalUnavailable.razor"));
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));
        var iterationCss = File.ReadAllText(Path.Combine(ui, "wwwroot", "iteration-ux.css"));

        StringAssert.Contains(page, ">Telemetry</button>");
        StringAssert.Contains(page, ">Technical data</button>");
        StringAssert.Contains(page, ">Race replay</button>");
        Assert.DoesNotContain(">Race review</button>", page);
        StringAssert.Contains(page, "private string RaceDataSection { get; set; } = \"telemetry\";");
        StringAssert.Contains(page, "<RaceTechnicalData Workspace=\"Workspace\" Card=\"Card\" />");

        StringAssert.Contains(technical, "data-analysis-section=\"technical\"");
        StringAssert.Contains(technical, "@if (ActiveInvestigation is null)");
        StringAssert.Contains(technical, "data-technical-overview");
        foreach (var card in new[] { "pit", "tires", "fuel", "racecraft" })
            StringAssert.Contains(technical, $"data-technical-card=\"{card}\"");
        foreach (var label in new[] { "Pit strategy", "Tire management", "Fuel management", "Racecraft &amp; pace" })
            StringAssert.Contains(technical, label);
        StringAssert.Contains(technical, "data-technical-investigation=\"@ActiveInvestigation\"");
        StringAssert.Contains(technical, "data-technical-back @onclick=\"CloseInvestigation\"");
        Assert.AreEqual(3, technical.Split("aria-pressed=\"@(_selectedRunNumber == run.Number ? \"true\" : \"false\")\"", StringSplitOptions.None).Length - 1,
            "Every Technical data record selector must expose its selected state.");
        StringAssert.Contains(technical, "Workspace.TechnicalInsights");
        StringAssert.Contains(technical, "Workspace.DeclaredLapLimit");
        StringAssert.Contains(technical, "Workspace.DeclaredTimeLimitMinutes");
        StringAssert.Contains(technical, "minutes configured");
        StringAssert.Contains(technical, "{ } when Workspace.ScheduledLaps > 0 => \"Stop required\"");
        StringAssert.Contains(technical, "{ } => \"Consumption available\"");
        StringAssert.Contains(technical, "Workspace.TirePrediction");
        StringAssert.Contains(technical, "Workspace.Garage61References");
        StringAssert.Contains(technical, "Comparable reference laps");
        StringAssert.Contains(technical, "Find reference laps");
        StringAssert.Contains(technical, "Estimated life");
        StringAssert.Contains(technical, "new(\"Most wear\", MostTireWearReading");
        Assert.DoesNotContain("new(\"Lowest\", LowestTireReading", technical);
        StringAssert.Contains(technical, "PredictionUnavailableReason");
        Assert.AreEqual(3, technical.Split("class=\"technical-tire-vital\"", StringSplitOptions.None).Length - 1,
            "The measured tire snapshot must keep carcass, surface, and pressure in three stable compact cells.");

        StringAssert.Contains(unavailable, "data-technical-unavailable-reason");
        StringAssert.Contains(unavailable, "data-technical-unavailable-action");
        StringAssert.Contains(unavailable, "[Parameter, EditorRequired] public string Action");
        Assert.AreEqual(4, technical.Split("data-technical-card=", StringSplitOptions.None).Length - 1);
        Assert.IsGreaterThanOrEqualTo(4, technical.Split("Action=", StringSplitOptions.None).Length - 1,
            "Every missing Technical data branch must explain a useful next action.");

        StringAssert.Contains(css, ".race-technical-data { height: 100%; min-height: 0; }");
        StringAssert.Contains(css, "grid-template-columns: repeat(2,minmax(0,1fr)); grid-template-rows: repeat(2,minmax(0,1fr));");
        StringAssert.Contains(css, ".technical-investigation { height:100%;min-height:0;");
        StringAssert.Contains(css, ".tire-investigation .technical-tire-card{grid-template-rows:auto minmax(28px,1fr) auto;");
        StringAssert.Contains(css, ".tire-investigation .technical-tire-card footer{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));");
        StringAssert.Contains(css, ".tire-investigation .technical-tire-vital b{min-width:0;margin:0;overflow:hidden;font:600 var(--font-size-min)");
        StringAssert.Contains(technical, "data-technical-complete-findings");
        StringAssert.Contains(technical, "@foreach (var metric in ActiveMetrics)");
        Assert.DoesNotContain("Metrics.Take(3)", technical,
            "Technical data must not hide supported findings behind an arbitrary three-item cap.");
        Assert.DoesNotContain(".Take(", technical,
            "Neither overview values, graphical groups, nor reference comparisons may silently cap supported facts.");
        StringAssert.Contains(technical, "data-metric-count=\"@RacecraftCardMetrics.Count\"");
        StringAssert.Contains(technical, "data-tire-call=\"@TireCallKey(pitRun)\"");
        StringAssert.Contains(technical, "data-two-vs-four=\"supported\"");
        StringAssert.Contains(technical, "data-two-vs-four=\"inconclusive\"");
        StringAssert.Contains(technical, "data-two-vs-four=\"single-call\"");
        StringAssert.Contains(technical, "data-tire-dynamics");
        StringAssert.Contains(technical, "data-racecraft-story");
        StringAssert.Contains(css, ".technical-findings-list{min-width:0;display:grid;grid-template-columns:repeat(4,minmax(0,1fr));");
        StringAssert.Contains(css, ".analysis-page-frame:has(.race-analysis-toolbar) .race-technical-data");
        StringAssert.Contains(iterationCss, ".race-technical-data .technical-overview {");
        StringAssert.Contains(iterationCss, ".race-technical-data .technical-card-metrics.metric-density-dense");
        StringAssert.Contains(iterationCss, ".race-technical-data .technical-investigation-content.metric-density-dense");
        StringAssert.Contains(iterationCss, ".race-technical-data .technical-overview-card:hover,");
        StringAssert.Contains(iterationCss, "transform: none;");
    }

    [TestMethod]
    public void SpotlightMenu_KeepsKeyboardFocusInsideAndClosesOnEscapeOrOutside()
    {
        var root = CompanionRoot();
        var telemetry = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.UI", "TelemetryWorkspace.razor"));
        var interactionPolicy = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.UI", "wwwroot", "interaction-policy.js"));
        var previewHost = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.Preview", "Components", "App.razor"));
        var nativeHost = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.App", "wwwroot", "index.html"));

        StringAssert.Contains(telemetry, "@ref=\"SpotlightControlElement\"");
        StringAssert.Contains(telemetry, "@onfocusout=\"HandleSpotlightFocusOut\"");
        StringAssert.Contains(telemetry, "@ref=\"SpotlightTriggerElement\"");
        StringAssert.Contains(telemetry, "data-pointer-no-focus");
        StringAssert.Contains(telemetry, "iracingCoachInteractionPolicy.consumePointerFocusRelease");
        StringAssert.Contains(telemetry, "iracingCoach.elementContainsActiveElement");
        StringAssert.Contains(telemetry, "await SpotlightTriggerElement.FocusAsync();");
        StringAssert.Contains(telemetry, "<section class=\"telemetry-workspace\" aria-label=\"Telemetry lap comparison\" @onkeydown=\"HandleWorkspaceKeyDown\" @onclick=\"CloseSpotlightMenu\"");
        StringAssert.Contains(telemetry, "@onclick=\"() => SelectSpotlight(trace.Lap)\"");
        Assert.DoesNotContain("@onblur=\"CloseSpotlightMenu\"", telemetry,
            "Moving focus from the trigger into a Spotlight option must not unmount the menu.");
        var focusOutHandler = telemetry[telemetry.IndexOf("private async Task HandleSpotlightFocusOut()", StringComparison.Ordinal)..telemetry.IndexOf("private async Task SelectSpotlight", StringComparison.Ordinal)];
        Assert.IsLessThan(
            focusOutHandler.IndexOf("elementContainsActiveElement", StringComparison.Ordinal),
            focusOutHandler.IndexOf("consumePointerFocusRelease", StringComparison.Ordinal),
            "The policy-generated pointer blur must be consumed before the keyboard focus-within check can treat it as an outside transition.");
        StringAssert.Contains(interactionPolicy, "const pointerFocusReleases = new WeakSet();");
        StringAssert.Contains(interactionPolicy, "const control = target.closest(\"[data-pointer-no-focus]\");");
        StringAssert.Contains(interactionPolicy, "event.preventDefault();");
        StringAssert.Contains(interactionPolicy, "if (document.activeElement === control) control.blur();");
        StringAssert.Contains(interactionPolicy, "if (control.hasAttribute(\"data-pointer-no-focus\")) return;");
        StringAssert.Contains(interactionPolicy, "pointerFocusReleases.add(control);");
        StringAssert.Contains(interactionPolicy, "control.blur();");
        StringAssert.Contains(interactionPolicy, "consumePointerFocusRelease: function (control)");
        StringAssert.Contains(interactionPolicy, "pointerFocusReleases.delete(control);");
        var releaseFocusHandler = interactionPolicy[interactionPolicy.IndexOf("const releaseFocus", StringComparison.Ordinal)..interactionPolicy.IndexOf("const forgetPointerFocusRelease", StringComparison.Ordinal)];
        Assert.IsLessThan(
            releaseFocusHandler.IndexOf("control.blur();", StringComparison.Ordinal),
            releaseFocusHandler.IndexOf("pointerFocusReleases.add(control);", StringComparison.Ordinal),
            "The policy must mark its synthetic focus release before blur dispatches focusout synchronously.");
        StringAssert.Contains(previewHost, "elementContainsActiveElement: function (element)");
        StringAssert.Contains(nativeHost, "elementContainsActiveElement: function (element)");
    }

    [TestMethod]
    public void RaceTechnicalData_PreservesCompleteRacecraftFindingsWhenRunGraphIsUnavailable()
    {
        var ui = Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI");
        var technical = File.ReadAllText(Path.Combine(ui, "RaceTechnicalData.razor"));
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));

        var branchStart = technical.IndexOf("@if (ComparableRuns.Count == 0)", StringComparison.Ordinal);
        var branchEnd = technical.IndexOf("@if (Garage61Laps.Count > 0)", branchStart, StringComparison.Ordinal);
        Assert.IsGreaterThanOrEqualTo(0, branchStart);
        Assert.IsGreaterThan(branchStart, branchEnd);
        var unavailableComparisonBranch = technical[branchStart..branchEnd];

        StringAssert.Contains(unavailableComparisonBranch, "Clean-run pace comparison unavailable");
        StringAssert.Contains(technical, "private AnalysisTechnicalInsight? ActiveInsight");
        StringAssert.Contains(technical, "private IReadOnlyList<AnalysisTechnicalMetric> ActiveMetrics => ActiveInsight?.Metrics ?? [];");
        StringAssert.Contains(technical, "@foreach (var metric in ActiveMetrics)");
        Assert.DoesNotContain("technical-metric-evidence", technical,
            "Primary Technical data UI should not expose provenance tags as visual copy.");
        StringAssert.Contains(css, ".technical-racecraft-no-comparison{grid-column:1/-1;");
        StringAssert.Contains(css, ".technical-racecraft-no-comparison>.technical-unavailable{");
    }

    [TestMethod]
    public void RaceReplay_GatesIncompleteCoverageAndUsesOnlyCanonicalKnownFieldData()
    {
        var ui = Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI");
        var replay = File.ReadAllText(Path.Combine(ui, "RaceReplayWorkspace.razor"));
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"))
            + File.ReadAllText(Path.Combine(ui, "wwwroot", "replay-overhaul.css"));

        StringAssert.Contains(replay, "data-analysis-section=\"replay\" data-race-replay");
        StringAssert.Contains(replay, "@if (!ReplayAvailable)");
        StringAssert.Contains(replay, "data-replay-unavailable");
        Assert.DoesNotContain("private bool ReplayAvailable => false", replay);
        StringAssert.Contains(replay, "private AnalysisRaceReplay? Replay => Workspace.Replay;");
        StringAssert.Contains(replay, "replay.Status.Equals(\"usable\"");
        StringAssert.Contains(replay, "replay.Status.Equals(\"partial\"");
        StringAssert.Contains(replay, "replay.Participants.Count > 0");
        StringAssert.Contains(replay, "Frames.Count > 1");
        StringAssert.Contains(replay, "&& _hasPlayerFrame");
        StringAssert.Contains(replay, "&& HasTrackGeometry;");
        StringAssert.Contains(replay, "Replay.UnavailableReasons.FirstOrDefault");
        StringAssert.Contains(replay, "Replay.Coverage.FirstOrDefault(item => item.Status.Equals(\"unavailable\"");
        StringAssert.Contains(replay, "HumanizeReplayUnavailableReason(stated)");
        StringAssert.Contains(replay, "HumanizeReplayUnavailableReason(reason)");
        StringAssert.Contains(replay, "Race replay needs full-field car positions, which are unavailable for this race.");
        foreach (var rawChannel in new[] { "CarIdxLapDistPct", "CarIdxLap", "CarIdxPosition", "CarIdxClassPosition", "CarIdxOnPitRoad", "CarIdxTrackSurface", "CarIdxPaceFlags", "CarIdxLastLapTime", "CarIdxBestLapTime", "DriverInfo", "PlayerCarIdx", "SessionTime", "SessionState", "SessionFlags" })
            StringAssert.Contains(replay, $"text.Contains(\"{rawChannel}\"");
        Assert.DoesNotContain("if (!string.IsNullOrWhiteSpace(stated)) return stated;", replay,
            "Raw backend channel names must not leak into the replay empty state.");
        StringAssert.Contains(replay, "TemporalCoverageIsUsable(replay.TemporalCoverage)");
        StringAssert.Contains(replay, "ParticipantHasReplayData(playerIndex)");
        StringAssert.Contains(replay, "ReplayCoverageLabel");
        StringAssert.Contains(replay, "replay-coverage-note");
        StringAssert.Contains(replay, "Quality.MainLoopComplete: true");
        StringAssert.Contains(replay, "180 + (point.Y - projection.CenterY) * projection.Scale");
        Assert.DoesNotContain("180 - (point.Y - projection.CenterY) * projection.Scale", replay,
            "Replay must use the same canonical screen-oriented vector transform as telemetry.");
        StringAssert.Contains(replay, "_trackPath = SvgPath(TrackPoints, HasTrackGeometry);");

        foreach (var hook in new[] { "data-replay-leader-rail", "data-replay-map", "data-replay-car", "data-replay-playback", "data-replay-grid", "data-replay-timeline", "data-replay-comparison", "data-replay-telemetry", "data-replay-abs-toggle" })
            StringAssert.Contains(replay, hook);
        StringAssert.Contains(replay, "data-replay-car=\"@car.CarIndex\"");
        StringAssert.Contains(replay, "@(car.IsPlayer ? \"player\" : null)");
        StringAssert.Contains(replay, "@(car.IsClassLeader ? \"class-leader\" : null)");
        StringAssert.Contains(replay, "@(car.IsOverallLeader ? \"overall-leader\" : null)");
        StringAssert.Contains(replay, "@(car.IsPlayer || car.IsClassLeader ? 13 : 10)");
        Assert.DoesNotContain("@if (!AtChecker)", replay, "The running-order grid remains available across the supported replay interval.");
        var gridStart = replay.IndexOf("<section class=\"replay-session-data\" data-replay-grid>", StringComparison.Ordinal);
        var gridRows = replay.IndexOf("<div class=\"replay-grid-table\">", gridStart, StringComparison.Ordinal);
        Assert.IsTrue(gridStart >= 0 && gridRows > gridStart);
        Assert.DoesNotContain("@if", replay[gridStart..gridRows],
            "The recorded running-order grid must not be gated by checkered state.");
        StringAssert.Contains(replay, "data-replay-seek-rail");
        Assert.DoesNotContain("data-replay-flag-timeline", replay,
            "Replay status and seeking must be one segmented rail, not two stacked timelines.");
        Assert.DoesNotContain("ShowTimeline", replay,
            "The persistent flag timeline must not hide behind a button that unexpectedly seeks to the checker.");
        var playbackStart = replay.IndexOf("<footer class=\"replay-playback\" data-replay-playback>", StringComparison.Ordinal);
        var flagTimeline = replay.IndexOf("data-replay-seek-rail", playbackStart, StringComparison.Ordinal);
        Assert.IsTrue(playbackStart >= 0 && flagTimeline > playbackStart);
        Assert.DoesNotContain("@if", replay[playbackStart..flagTimeline],
            "The horizontal flag-state timeline must stay mounted throughout playback, including before the checker.");
        StringAssert.Contains(replay, "@foreach (var segment in FlagSegments)");
        StringAssert.Contains(replay, "class=\"replay-event-markers\"");
        StringAssert.Contains(replay, "foreach (var observed in frame.Events ?? [])");
        StringAssert.Contains(replay, "TimelinePosition(item.SessionTimeSeconds)");
        StringAssert.Contains(replay, "left:{segment.Left:0.###}%;width:{segment.Width:0.###}%");
        StringAssert.Contains(replay, "private IReadOnlyList<FlagSegment> FlagSegments");
        StringAssert.Contains(replay, "private static string FrameFlagKind(AnalysisReplayFrame frame)");
        StringAssert.Contains(replay, "TrimToPlayableInterval(phaseFrames, _playerCarIndex)");
        StringAssert.Contains(replay, "data-replay-instant-unavailable");
        StringAssert.Contains(replay, "private bool CurrentInstantAvailable");
        StringAssert.Contains(replay, "CurrentFrame.GlobalFlagLabels");
        StringAssert.Contains(replay, "var leader = ClassLeader(CurrentFrame);");
        StringAssert.Contains(replay, "player.ClassPosition ?? player.OverallPosition");
        StringAssert.Contains(replay, "private void RebuildReplayCache()");
        StringAssert.Contains(replay, "private void RebuildReplayMapCache()");
        StringAssert.Contains(replay, "private IReadOnlyList<AnalysisReplayFrame> Frames => _frames;");
        StringAssert.Contains(replay, "private IReadOnlyList<TimelineItem> TimelineEvents => _timelineEvents;");
        StringAssert.Contains(replay, "private IReadOnlyList<FlagSegment> FlagSegments => _flagSegments;");
        StringAssert.Contains(replay, "CanInterpolateFrameTransition(CurrentFrame, NextFrame, car.CarIndex)");
        StringAssert.Contains(replay, "if (next.GapBefore) return false;");
        StringAssert.Contains(replay, "Replay?.Representation?.DisplaySampleRateHz ?? Replay?.SampleRateHz");
        StringAssert.Contains(replay, "Replay?.Interpolation.StartsWith(\"linear\"");
        StringAssert.Contains(replay, "gap <= limit");
        StringAssert.Contains(replay, "string.Equals(current.SessionState, next.SessionState, StringComparison.OrdinalIgnoreCase)");
        Assert.DoesNotContain("Replay?.Frames.OrderBy", replay,
            "Playback renders must not sort and materialize the complete replay on every 60 fps update.");
        Assert.DoesNotContain("Frames.Take(CurrentFrameIndex + 1)", replay,
            "Laps-since-pit lookup must use the precomputed pit-event index.");
        StringAssert.Contains(replay, "var (min, max) = ReplayChartRange(currentLap, signal);");
        StringAssert.Contains(replay, "new(\"Tire age\", \"—\", PlayerTireAge)");
        Assert.DoesNotContain("PlayerTireInfo", replay,
            "A measured pit endpoint or whole-race model must not be carried forward as current replay tire condition.");
        StringAssert.Contains(replay, "trace.IsComparable() && trace.LapTimeSeconds.HasValue");
        StringAssert.Contains(replay, "class=\"replay-reference-trace\"");
        StringAssert.Contains(css, ".replay-reference-trace{stroke:");
        StringAssert.Contains(css, "stroke-dasharray:3 3");

        StringAssert.Contains(replay, "private static bool IsAbsActive(AnalysisTracePoint point) => Additional(point, \"abs-active\").GetValueOrDefault() > .5;");
        Assert.DoesNotContain("Additional(point, \"abs-cut\")", replay,
            "A recorded cut percentage must not be treated as proof that ABS was active.");
        StringAssert.Contains(replay, "chart.Id == \"brake\" && ShowAbs && HasAbsData");
        StringAssert.Contains(replay, "@foreach (var path in AbsTracePaths(CurrentTrace))");
        StringAssert.Contains(replay, "class=\"replay-abs-trace\"");
        StringAssert.Contains(css, ".replay-abs-trace");
        StringAssert.Contains(css, "stroke:#f4c24f");
        Assert.DoesNotContain("Competitor fuel", replay);
        Assert.DoesNotContain("Competitor tire", replay);
        Assert.IsFalse(replay.Contains("competitor throttle", StringComparison.OrdinalIgnoreCase));
        StringAssert.Contains(css, ".race-replay-workspace { position:relative;");
        StringAssert.Contains(css, ".replay-layout { height:100%;min-height:0;");
        StringAssert.Contains(css, ".replay-scrubber.replay-seek-rail");
        StringAssert.Contains(css, "background: transparent;");
        StringAssert.Contains(css, "@media (prefers-reduced-motion: reduce)");
    }

    [TestMethod]
    public void RaceReplay_DoesNotInventCrossClassLeadersOrPitEntriesAcrossCoverageGaps()
    {
        var replay = File.ReadAllText(Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI", "RaceReplayWorkspace.razor"))
            .ReplaceLineEndings("\n");

        var leaderStart = replay.IndexOf("private AnalysisReplayCarState? ClassLeader(AnalysisReplayFrame frame)", StringComparison.Ordinal);
        var leaderEnd = replay.IndexOf("private ReplayCarDisplay? CurrentClassLeader", leaderStart, StringComparison.Ordinal);
        Assert.IsTrue(leaderStart >= 0 && leaderEnd > leaderStart);
        var leaderMethod = replay[leaderStart..leaderEnd];
        StringAssert.Contains(leaderMethod, "if (classId.HasValue)\n        {\n            return frame.Cars.FirstOrDefault(car => ParticipantHasReplayData(car.CarIndex) && car.ClassPosition == 1 && Participant(car.CarIndex)?.ClassId == classId);\n        }");
        StringAssert.Contains(leaderMethod, "return frame.Cars.FirstOrDefault(car => ParticipantHasReplayData(car.CarIndex) && car.OverallPosition == 1);");
        Assert.DoesNotContain("if (classLeader is not null)", leaderMethod,
            "A missing same-class P1 must remain unavailable instead of silently substituting the overall leader from another class.");
        StringAssert.Contains(replay, "private string LeaderScopeLabel => PlayerParticipant?.ClassId.HasValue == true ? \"Class leader\" : \"Overall leader\";");
        StringAssert.Contains(replay, "<span class=\"eyebrow\">@LeaderScopeLabel</span>");
        StringAssert.Contains(replay, "<strong>@LeaderScopeLabel and you</strong>");

        var pitStart = replay.IndexOf("private IReadOnlyDictionary<int, IReadOnlyList<PitEvent>> BuildPitEventIndex()", StringComparison.Ordinal);
        var pitEnd = replay.IndexOf("private IReadOnlyList<TimelineItem> BuildTimelineEvents()", pitStart, StringComparison.Ordinal);
        Assert.IsTrue(pitStart >= 0 && pitEnd > pitStart);
        var pitIndex = replay[pitStart..pitEnd];
        StringAssert.Contains(pitIndex, "new Dictionary<int, PitRoadObservation>()");
        StringAssert.Contains(pitIndex, "previousPitState.TryGetValue(car.CarIndex, out var previous)");
        StringAssert.Contains(pitIndex, "previous.FrameIndex == frameIndex - 1");
        StringAssert.Contains(pitIndex, "ReplayFramesAreContinuous(previous.FrameIndex, frameIndex)");
        StringAssert.Contains(pitIndex, "!previous.OnPitRoad");
        StringAssert.Contains(pitIndex, "&& onPitRoad");
        StringAssert.Contains(pitIndex, "gap > 0 && gap <= limit");
        Assert.DoesNotContain("GetValueOrDefault(car.CarIndex)", pitIndex,
            "A first observation on pit road must not be treated as a known off-track-to-pit-road transition.");

        var timelineStart = replay.IndexOf("private IReadOnlyList<TimelineItem> BuildTimelineEvents()", StringComparison.Ordinal);
        var timelineEnd = replay.IndexOf("private IReadOnlyList<FlagSegment> BuildFlagSegments()", timelineStart, StringComparison.Ordinal);
        Assert.IsTrue(timelineStart >= 0 && timelineEnd > timelineStart);
        var timeline = replay[timelineStart..timelineEnd];
        StringAssert.Contains(timeline, "PitRoadObservation? playerPit = null;");
        StringAssert.Contains(timeline, "previous.FrameIndex == frameIndex - 1");
        StringAssert.Contains(timeline, "ReplayFramesAreContinuous(previous.FrameIndex, frameIndex)");
        StringAssert.Contains(timeline, "currentPit.Value != previous.OnPitRoad");
    }

    [TestMethod]
    public void RaceReplay_FastestCleanReferenceIsOptionalAndTruthfullyGated()
    {
        var ui = Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI");
        var replay = File.ReadAllText(Path.Combine(ui, "RaceReplayWorkspace.razor"));
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));

        StringAssert.Contains(replay, "private bool ShowFastestReference { get; set; } = true;");
        StringAssert.Contains(replay, "@if (FastestTrace is { } fastestReference)");
        StringAssert.Contains(replay, "data-replay-reference-toggle");
        StringAssert.Contains(replay, "aria-pressed=\"@(ShowFastestReference ? \"true\" : \"false\")\"");
        StringAssert.Contains(replay, "Compare with fastest clean recorded lap @fastestReference.Lap");
        StringAssert.Contains(replay, "@if (ShowFastestReference && FastestTrace is { } reference)");
        StringAssert.Contains(replay, "ShowFastestReference ? FastestTrace : null");
        StringAssert.Contains(replay, "(currentLap, trace.Lap, signal, ShowFastestReference)");
        StringAssert.Contains(replay, "(currentLap, signal, ShowFastestReference)");
        StringAssert.Contains(replay, "ShowFastestReference = !ShowFastestReference;");
        StringAssert.Contains(replay, "_replayChartPathCache.Clear();");
        StringAssert.Contains(replay, "_replayChartRangeCache.Clear();");
        Assert.DoesNotContain("No clean reference", replay,
            "The absent optional reference should not consume repeated chart-footer space.");
        StringAssert.Contains(css, ".replay-reference-toggle{");
        StringAssert.Contains(css, ".replay-reference-toggle.active{");
    }

    [TestMethod]
    public void RaceReplay_RecordedTelemetryGapsBreakChartPaths()
    {
        var replay = File.ReadAllText(Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI", "RaceReplayWorkspace.razor"));
        var pathStart = replay.IndexOf("private string ReplayChartPath(AnalysisLapTrace trace, string signal)", StringComparison.Ordinal);
        var pathEnd = replay.IndexOf("private (double Minimum, double Maximum) ReplayChartRange", pathStart, StringComparison.Ordinal);
        Assert.IsTrue(pathStart >= 0 && pathEnd > pathStart);
        var pathBuilder = replay[pathStart..pathEnd];

        StringAssert.Contains(pathBuilder, "double.IsFinite(value.Value)");
        StringAssert.Contains(pathBuilder, "var segmentStarted = false;");
        StringAssert.Contains(pathBuilder, "!double.IsFinite(trace.Points[index].LapPercent)");
        StringAssert.Contains(pathBuilder, "segmentStarted = false;");
        StringAssert.Contains(pathBuilder, "(segmentStarted ? \"L\" : \"M\")");
        StringAssert.Contains(pathBuilder, "segmentStarted = true;");
        Assert.DoesNotContain("commands.Count == 0 ? \"M\" : \"L\"", pathBuilder,
            "A later valid sample must start a new subpath after missing telemetry.");
    }

    [TestMethod]
    public void RaceReplay_MissingLapCountersNeverBecomeLapZeroOrLapBackEstimates()
    {
        var replay = File.ReadAllText(Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI", "RaceReplayWorkspace.razor"));

        StringAssert.Contains(replay, "private static int? RecordedLapCount(AnalysisReplayCarState car)");
        StringAssert.Contains(replay, "private static int? DisplayLap(AnalysisReplayCarState car)");
        StringAssert.Contains(replay, "private static double? CarDistance(AnalysisReplayCarState car)");
        Assert.DoesNotContain("car.CompletedLaps ?? car.Lap ?? 0", replay);
        Assert.DoesNotContain("L@DisplayLap", replay);
        StringAssert.Contains(replay, "@LapLabel(leader.State)");
        StringAssert.Contains(replay, "@LapNumberText(row.State)");
        StringAssert.Contains(replay, "data-replay-unavailable=\"leader-laps\"");
        StringAssert.Contains(replay, "if (DisplayLap(leader) is not { } leaderLap || DisplayLap(player) is not { } playerLap) return null;");
        StringAssert.Contains(replay, "if (CarDistance(leader) is not { } leaderDistance || CarDistance(player) is not { } playerDistance) return null;");
        StringAssert.Contains(replay, "RecordedLapCount(current) is not { } currentLapCount");
        StringAssert.Contains(replay, "car.LapPercent is { } percent && double.IsFinite(percent)");
        StringAssert.Contains(replay, "var distance = Wrap(car.LapPercent!.Value);");
        StringAssert.Contains(replay, "Position unavailable");
        Assert.DoesNotContain("var distance = CarDistance(car);", replay,
            "Track markers may use recorded lap percent without inventing a lap count.");
    }

    [TestMethod]
    public void TraceLayout_ArbitraryReorderAndSignalInsertionAreStableAndBounded()
    {
        var layout = new AnalysisTraceLayout
        {
            Rows =
            [
                new AnalysisTraceRow { Id = "row-a", PrimarySignalId = "speed" },
                new AnalysisTraceRow { Id = "row-b", PrimarySignalId = "throttle" },
                new AnalysisTraceRow { Id = "row-c", PrimarySignalId = "brake" },
                new AnalysisTraceRow { Id = "row-d", PrimarySignalId = "rpm" }
            ]
        };

        Assert.IsTrue(AnalysisTraceLayouts.MoveRowToIndex(layout, "row-b", 3));
        CollectionAssert.AreEqual(new[] { "row-a", "row-c", "row-d", "row-b" }, layout.Rows.Select(row => row.Id).ToArray());
        Assert.IsTrue(AnalysisTraceLayouts.MoveRowToIndex(layout, "row-d", 0));
        CollectionAssert.AreEqual(new[] { "row-d", "row-a", "row-c", "row-b" }, layout.Rows.Select(row => row.Id).ToArray());
        Assert.IsFalse(AnalysisTraceLayouts.MoveRowToIndex(layout, "row-a", -1));
        Assert.IsFalse(AnalysisTraceLayouts.MoveRowToIndex(layout, "missing", 1));
        CollectionAssert.AreEqual(new[] { "row-d", "row-a", "row-c", "row-b" }, layout.Rows.Select(row => row.Id).ToArray());

        Assert.IsTrue(AnalysisTraceLayouts.InsertSignal(layout, "steering", 2));
        Assert.AreEqual("steering", layout.Rows[2].PrimarySignalId);
        Assert.AreEqual("row-a", layout.Rows[1].Id);
        Assert.AreEqual("row-c", layout.Rows[3].Id);
        Assert.IsFalse(AnalysisTraceLayouts.InsertSignal(layout, "not-a-signal", 0));
        while (layout.Rows.Count < AnalysisTraceLayouts.MaximumRows)
            Assert.IsTrue(AnalysisTraceLayouts.InsertSignalRow(layout, "yaw", layout.Rows.Count));
        Assert.IsFalse(AnalysisTraceLayouts.InsertSignal(layout, "gear", layout.Rows.Count));
        Assert.HasCount(AnalysisTraceLayouts.MaximumRows, layout.Rows);
    }

    [TestMethod]
    public void TraceLayout_PlaceSignalPairsThenReplacesWithoutDuplicatingTheRow()
    {
        var layout = new AnalysisTraceLayout
        {
            Rows = [new AnalysisTraceRow { Id = "row-a", PrimarySignalId = "speed" }]
        };

        Assert.IsTrue(AnalysisTraceLayouts.PlaceSignal(layout, "row-a", "brake"));
        Assert.AreEqual("brake", layout.Rows[0].SecondarySignalId);
        Assert.IsFalse(AnalysisTraceLayouts.PlaceSignal(layout, "row-a", "speed"));
        Assert.IsFalse(AnalysisTraceLayouts.PlaceSignal(layout, "row-a", "brake"));
        Assert.IsTrue(AnalysisTraceLayouts.PlaceSignal(layout, "row-a", "rpm"));
        Assert.AreEqual("speed", layout.Rows[0].PrimarySignalId);
        Assert.AreEqual("rpm", layout.Rows[0].SecondarySignalId);
    }

    [TestMethod]
    public void TraceLayout_AssignAndRemoveSignalKeepEveryRowRenderable()
    {
        var layout = new AnalysisTraceLayout
        {
            Rows =
            [
                new AnalysisTraceRow { Id = "row-a", PrimarySignalId = "speed", SecondarySignalId = "brake" },
                new AnalysisTraceRow { Id = "row-b", PrimarySignalId = "rpm" }
            ]
        };

        Assert.IsTrue(AnalysisTraceLayouts.AssignSignal(layout, "row-a", TraceSignalSlot.Primary, "brake"));
        Assert.AreEqual("brake", layout.Rows[0].PrimarySignalId);
        Assert.AreEqual(string.Empty, layout.Rows[0].SecondarySignalId, "Replacing primary with the paired signal must remove the duplicate slot.");
        Assert.IsTrue(AnalysisTraceLayouts.AssignSignal(layout, "row-a", TraceSignalSlot.Secondary, "steering"));
        Assert.IsFalse(AnalysisTraceLayouts.AssignSignal(layout, "row-a", TraceSignalSlot.Secondary, "brake"));
        Assert.AreEqual("steering", layout.Rows[0].SecondarySignalId);
        Assert.IsTrue(AnalysisTraceLayouts.RemoveSignal(layout, "row-a", "steering"));
        Assert.AreEqual(string.Empty, layout.Rows[0].SecondarySignalId);
        Assert.IsTrue(AnalysisTraceLayouts.AssignSignal(layout, "row-a", TraceSignalSlot.Secondary, "steering"));

        Assert.IsTrue(AnalysisTraceLayouts.RemoveSignal(layout, "row-a", "brake"));
        Assert.AreEqual("steering", layout.Rows[0].PrimarySignalId, "Removing primary must promote the paired signal instead of leaving an invalid row.");
        Assert.AreEqual(string.Empty, layout.Rows[0].SecondarySignalId);
        Assert.IsTrue(AnalysisTraceLayouts.RemoveSignal(layout, "row-a", "steering"));
        Assert.HasCount(1, layout.Rows);
        Assert.AreEqual("row-b", layout.Rows[0].Id);
        Assert.IsFalse(AnalysisTraceLayouts.RemoveSignal(layout, "row-b", "rpm"), "The final row must remain renderable.");
        Assert.AreEqual("rpm", layout.Rows[0].PrimarySignalId);
    }

    [TestMethod]
    public void TraceLayout_MoveSignalBetweenRowsPromotesSourceAndReplacesOnlyTheTargetSlot()
    {
        var layout = new AnalysisTraceLayout
        {
            Rows =
            [
                new AnalysisTraceRow { Id = "source", PrimarySignalId = "speed", SecondarySignalId = "brake" },
                new AnalysisTraceRow { Id = "target", PrimarySignalId = "rpm", SecondarySignalId = "steering" },
                new AnalysisTraceRow { Id = "untouched", PrimarySignalId = "gear" }
            ]
        };

        Assert.IsTrue(AnalysisTraceLayouts.MoveSignal(layout, "source", TraceSignalSlot.Primary, "target", TraceSignalSlot.Secondary));
        Assert.AreEqual("brake", layout.Rows.Single(row => row.Id == "source").PrimarySignalId);
        Assert.AreEqual(string.Empty, layout.Rows.Single(row => row.Id == "source").SecondarySignalId);
        Assert.AreEqual("rpm", layout.Rows.Single(row => row.Id == "target").PrimarySignalId);
        Assert.AreEqual("speed", layout.Rows.Single(row => row.Id == "target").SecondarySignalId);
        Assert.AreEqual("gear", layout.Rows.Single(row => row.Id == "untouched").PrimarySignalId);

        Assert.IsTrue(AnalysisTraceLayouts.MoveSignal(layout, "source", TraceSignalSlot.Primary, "target", TraceSignalSlot.Primary));
        Assert.IsFalse(layout.Rows.Any(row => row.Id == "source"), "Moving the last signal out of a non-final row must remove that empty row.");
        Assert.AreEqual("brake", layout.Rows.Single(row => row.Id == "target").PrimarySignalId);
        Assert.AreEqual("speed", layout.Rows.Single(row => row.Id == "target").SecondarySignalId);
        Assert.AreEqual("gear", layout.Rows.Single(row => row.Id == "untouched").PrimarySignalId);

        Assert.IsTrue(AnalysisTraceLayouts.MoveSignal(layout, "target", TraceSignalSlot.Secondary, "target", TraceSignalSlot.Primary));
        Assert.AreEqual("speed", layout.Rows.Single(row => row.Id == "target").PrimarySignalId);
        Assert.AreEqual("brake", layout.Rows.Single(row => row.Id == "target").SecondarySignalId);
        Assert.IsFalse(AnalysisTraceLayouts.MoveSignal(layout, "target", TraceSignalSlot.Secondary, "target", TraceSignalSlot.Secondary));
        Assert.AreEqual("gear", layout.Rows.Single(row => row.Id == "untouched").PrimarySignalId);
    }

    [TestMethod]
    public void RaceAnalysisUi_ExposesRealOverviewStatsConfigurableRowsAndDomDrivenFrameSyncedCursor()
    {
        var ui = Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI");
        var analysis = File.ReadAllText(Path.Combine(ui, "AnalysisPage.razor"));
        var workspace = File.ReadAllText(Path.Combine(ui, "AnalysisWorkspacePage.razor"));
        var telemetry = File.ReadAllText(Path.Combine(ui, "TelemetryWorkspace.razor"));
        var cursor = File.ReadAllText(Path.Combine(ui, "wwwroot", "analysis-telemetry-cursor.js"));
        var traceLayout = File.ReadAllText(Path.Combine(ui, "wwwroot", "analysis-trace-layout.js"));
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));

        foreach (var hook in new[] { "race-analysis-row", "event-race-shape", "event-long-run", "event-pace", "event-tire" })
            StringAssert.Contains(analysis, hook);
        StringAssert.Contains(analysis, "overview?.GreenLaps");
        StringAssert.Contains(analysis, "overview?.PaceConsistencyPercent");
        StringAssert.Contains(workspace, "race-analysis-toolbar");
        StringAssert.Contains(workspace, "<RaceTechnicalData Workspace=\"Workspace\" Card=\"Card\" />");
        StringAssert.Contains(workspace, "<RaceReplayWorkspace Workspace=\"Workspace\" />");
        StringAssert.Contains(telemetry, "Average of selected laps");
        StringAssert.Contains(telemetry, "SpeedHeatmapStops");
        StringAssert.Contains(telemetry, "HeatmapColor(normalized, SpeedHeatmapStops)");
        StringAssert.Contains(telemetry, "analysis-trace-toolbox");
        Assert.DoesNotContain("analysis-trace-toolbox-backdrop", telemetry);
        StringAssert.Contains(telemetry, "inert=\"@(!TraceToolboxOpen ? string.Empty : null)\"");
        StringAssert.Contains(telemetry, "<ProductIcon Name=\"setup\" Size=\"15\" />");
        StringAssert.Contains(telemetry, "<span>Customize</span>");
        StringAssert.Contains(telemetry, "aria-label=\"Customize trace charts\"");
        Assert.DoesNotContain("aria-label=\"@(TraceToolboxOpen ? \"Hide trace toolbox\"", telemetry);
        Assert.DoesNotContain("<ProductIcon Name=\"settings\"", telemetry);
        StringAssert.Contains(telemetry, "analysis-trace-catalog");
        StringAssert.Contains(telemetry, "@foreach (var group in TraceSignalGroups)");
        StringAssert.Contains(telemetry, "class=\"trace-signal-category\"");
        StringAssert.Contains(telemetry, ".GroupBy(SignalCategory)");
        foreach (var category in new[] { "Pace", "Controls", "Vehicle", "Tires", "Conditions" })
            StringAssert.Contains(telemetry, $"\"{category}\"");
        StringAssert.Contains(telemetry, "data-analysis-drag-signal");
        StringAssert.Contains(telemetry, "data-analysis-drag-row");
        StringAssert.Contains(telemetry, "MoveTraceRowToIndex");
        StringAssert.Contains(telemetry, "InsertTraceSignalRow");
        StringAssert.Contains(telemetry, "PlaceTraceSignal");
        StringAssert.Contains(telemetry, "tabindex=\"0\"");
        StringAssert.Contains(telemetry, "HandleTraceSignalKeyDown");
        StringAssert.Contains(telemetry, "@onkeydown:stopPropagation=\"true\"");
        StringAssert.Contains(telemetry, "@onclick=\"() => CloseTraceToolbox(false)\"");
        StringAssert.Contains(telemetry, "else if (TraceToolboxOpen) await CloseTraceToolbox(true);");
        StringAssert.Contains(telemetry, "if (restoreKeyboardFocus) await TraceToolboxButtonElement.FocusAsync();");
        StringAssert.Contains(telemetry, "renderedSignalIds");
        StringAssert.Contains(telemetry, "\"unavailable\"");
        StringAssert.Contains(telemetry, "trace-chart-frame");
        StringAssert.Contains(telemetry, "trace-label-column");
        StringAssert.Contains(telemetry, "aria-describedby");
        StringAssert.Contains(telemetry, "role=\"tooltip\"");
        StringAssert.Contains(telemetry, "RowUnit(panel)");
        StringAssert.Contains(telemetry, "role=\"group\"");
        StringAssert.Contains(telemetry, "class=\"trace-row-label-shell\"");
        StringAssert.Contains(telemetry, "@key=\"panel.Preferences.Id\"");
        StringAssert.Contains(telemetry, "@onfocus=\"() => ActivateTraceRow(panel.Preferences.Id)\"");
        StringAssert.Contains(telemetry, "aria-label=\"@($\"{RowLabel(panel)} chart\")\"");
        Assert.DoesNotContain("@onclick=\"() => SelectTraceRow", telemetry);
        Assert.DoesNotContain("trace-row-label-shell @(SelectedTraceRow", telemetry);
        Assert.DoesNotContain("private void SelectTraceRow", telemetry);
        Assert.DoesNotContain("draggable=\"true\"", telemetry);
        Assert.DoesNotContain("@ondragstart", telemetry);
        Assert.DoesNotContain("@ondragover", telemetry);
        Assert.DoesNotContain("RowSubtitle(panel)", telemetry);
        StringAssert.Contains(telemetry, "SignalRange(signal)");
        StringAssert.Contains(telemetry, "point.AdditionalSignals is { } additional");
        StringAssert.Contains(telemetry, "additional.TryGetValue(signal.Id, out var value) && double.IsFinite(value)");
        StringAssert.Contains(telemetry, "private static bool IsStepSignal(AnalysisTraceSignalDefinition signal)");
        StringAssert.Contains(telemetry, "signal.Unit is \"on / off\" or \"state\" or \"position\"");
        StringAssert.Contains(telemetry, "RowHelp(panel)");
        StringAssert.Contains(telemetry, "rendered.Length == 1");
        StringAssert.Contains(telemetry, "$\"{item.Signal.Name} ({item.Signal.Unit}).\"");
        StringAssert.Contains(telemetry, "$\"Solid: {rendered[0].Signal.Name} ({rendered[0].Signal.Unit}).\"");
        StringAssert.Contains(telemetry, "$\"Dashed: {rendered[1].Signal.Name} ({rendered[1].Signal.Unit}).\"");
        StringAssert.Contains(telemetry, "parts.Add(\"Each line uses its own vertical scale.\");");
        StringAssert.Contains(telemetry, "return string.Join('\\n', parts);");
        Assert.DoesNotContain(". Each line uses its own vertical scale.\");", telemetry);
        StringAssert.Contains(telemetry, "Not recorded for this race");
        StringAssert.Contains(telemetry, "BuildCursorInteropConfiguration");
        StringAssert.Contains(telemetry, "ToString(\"+0.000;-0.000;0.000\")");
        Assert.DoesNotContain("<small>(+@((trace.LapTimeSeconds", telemetry);
        StringAssert.Contains(telemetry, "data-analysis-cursor-layer");
        StringAssert.Contains(telemetry, "data-analysis-trace-path");
        StringAssert.Contains(telemetry, "await DisposeCursorInteropAsync();");
        StringAssert.Contains(telemetry, "panel.Signals.Where(HasSignalData).Select");
        Assert.DoesNotContain("@onmousemove=\"ChartMoved\"", telemetry);
        Assert.DoesNotContain("AnalysisCursorMoved", telemetry);
        StringAssert.Contains(telemetry, "DotNetObjectReference<TelemetryWorkspace>");
        StringAssert.Contains(cursor, "requestAnimationFrame");
        StringAssert.Contains(cursor, "nearestIndex");
        StringAssert.Contains(cursor, "replaceChildren");
        StringAssert.Contains(cursor, "updateTrack");
        StringAssert.Contains(cursor, "resizeChartDom");
        StringAssert.Contains(cursor, "ResizeObserver");
        StringAssert.Contains(cursor, "setAttributeIfChanged(state.element, \"viewBox\"");
        StringAssert.Contains(cursor, "window.removeEventListener(\"scroll\", state.scrolled, true)");
        StringAssert.Contains(cursor, "getBoundingClientRect");
        StringAssert.Contains(cursor, "if (speed !== null) parts.push");
        StringAssert.Contains(cursor, "if (throttle !== null) parts.push");
        StringAssert.Contains(cursor, "if (brake !== null) parts.push");
        StringAssert.Contains(cursor, "brake === null && delta === null");
        Assert.DoesNotContain("speed ?? 0", cursor);
        Assert.DoesNotContain("throttle ?? 0", cursor);
        Assert.DoesNotContain("brake ?? 0", cursor);
        Assert.DoesNotContain("invokeMethodAsync", cursor);
        Assert.DoesNotContain("inFlight", cursor);
        foreach (var hook in new[] { "setPointerCapture", "dragThreshold = 5", "analysis-trace-drag-ghost", "analysis-trace-drop-preview", "autoScroll", "captureRects", "animateReflow", "lostpointercapture", "window.addEventListener(\"blur\"", "MoveTraceRowToIndex", "InsertTraceSignalRow", "PlaceTraceSignal" })
            StringAssert.Contains(traceLayout, hook);
        StringAssert.Contains(traceLayout, "document.elementFromPoint");
        StringAssert.Contains(traceLayout, "toolbox.contains(topmost)");
        StringAssert.Contains(traceLayout, "updateTarget(state, session, event);");
        StringAssert.Contains(traceLayout, "state.committing");
        StringAssert.Contains(traceLayout, "prefers-reduced-motion: reduce");
        StringAssert.Contains(traceLayout, "getPropertyValue(\"--motion-structure\")");
        StringAssert.Contains(traceLayout, "const duration = structuralMotionMs(state.root)");
        Assert.DoesNotContain("duration: 200", traceLayout, StringComparison.Ordinal);
        StringAssert.Contains(css, ".trace-row-label-copy > strong");
        StringAssert.Contains(css, "max-inline-size: 12ch");
        StringAssert.Contains(css, ".trace-row-unit");
        StringAssert.Contains(css, "white-space: pre-line;");
        StringAssert.Contains(css, "user-select: none;");
        Assert.DoesNotContain(".trace-row-label-shell.selected", css);
        Assert.DoesNotContain(".trace-row-label-trigger:hover { color: var(--text-primary); background:", css);
        StringAssert.Contains(css, ".analysis-trace-studio .analysis-trace-toolbox");
        Assert.DoesNotContain(".analysis-trace-toolbox-backdrop", css);
        StringAssert.Contains(css, ".analysis-page-frame:has(.analysis-trace-studio.toolbox-open)");
        StringAssert.Contains(css, "padding-right: calc(22px + var(--side-toolbox-width));");
        StringAssert.Contains(css, ".telemetry-workstation-grid.race-workstation {");
        StringAssert.Contains(css, "grid-template-columns: clamp(400px,29vw,500px) minmax(0,1fr);");
        Assert.DoesNotContain(".toolbox-open .telemetry-context-column", css,
            "Customize may reflow the trace region but must not resize the Track/Laps column.");
        StringAssert.Contains(css, ".trace-panel.trace-panel-expanded { transition: right var(--motion-structure) var(--ease); }");
        StringAssert.Contains(css, ".trace-panel.trace-panel-expanded.toolbox-open { right: var(--side-toolbox-width); }");
        StringAssert.Contains(css, "--command-bar-height: 28px;");
        StringAssert.Contains(css, "--motion-structure: 500ms;");
        Assert.DoesNotContain("--toolbox-motion", css);
        Assert.DoesNotContain("--analysis-motion", css);
        StringAssert.Contains(css, "inset: var(--command-bar-height) 0 0 auto;");
        StringAssert.Contains(css, "transition: opacity var(--motion-structure) var(--ease),transform var(--motion-structure) var(--ease),box-shadow var(--motion-structure) var(--ease),visibility 0s linear var(--motion-structure);");
        StringAssert.Contains(css, "transition: padding-right var(--motion-structure) var(--ease);");
        StringAssert.Contains(css, ".trace-toolbox-button {");
        StringAssert.Contains(css, "min-width: 92px;");
        StringAssert.Contains(css, ".analysis-trace-metric-card:focus-visible");
        StringAssert.Contains(css, ".trace-selected-signal > i.unavailable");
    }

    [TestMethod]
    public void RaceAnalysisContextColumn_HeaderBubblesKeepPanelsMountedAndSynchronizeBothAxes()
    {
        var ui = Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI");
        var telemetry = File.ReadAllText(Path.Combine(ui, "TelemetryWorkspace.razor"));
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));

        StringAssert.Contains(telemetry, "private bool LapRailCollapsed { get; set; }");
        StringAssert.Contains(telemetry, "private bool TrackPanelCollapsed { get; set; }");
        StringAssert.Contains(telemetry, "LapRailCollapsed = false;");
        StringAssert.Contains(telemetry, "TrackPanelCollapsed = false;");
        StringAssert.Contains(telemetry, "private void ToggleLapRail()");
        StringAssert.Contains(telemetry, "LapRailCollapsed = !LapRailCollapsed;");
        StringAssert.Contains(telemetry, "private void ToggleTrackPanel()");
        StringAssert.Contains(telemetry, "TrackPanelCollapsed = !TrackPanelCollapsed;");
        StringAssert.Contains(telemetry, "private string ContextColumnState =>");
        foreach (var state in new[] { "context-both", "context-track", "context-laps", "context-none" })
            StringAssert.Contains(telemetry, $"\"{state}\"");
        StringAssert.Contains(telemetry, "[Parameter] public bool IsRaceWorkspace { get; set; }");
        StringAssert.Contains(telemetry, "IsRaceWorkspace ? \"race-workstation\" : \"qualifying-workstation\"");
        StringAssert.Contains(telemetry, "<div @ref=\"ContextColumnElement\" class=\"telemetry-context-column\">");
        Assert.IsLessThan(
            telemetry.IndexOf("<aside class=\"lap-rail telemetry-context-panel", StringComparison.Ordinal),
            telemetry.IndexOf("<section class=\"track-panel telemetry-context-panel", StringComparison.Ordinal),
            "Track position must be mounted above laps and runs in the shared context column.");
        StringAssert.Contains(telemetry, "class=\"analysis-context-toggles\"");
        StringAssert.Contains(telemetry, "role=\"group\" aria-label=\"Telemetry context panels\"");
        StringAssert.Contains(telemetry, "class=\"context-toggle-chip");
        StringAssert.Contains(telemetry, "data-context-toggle=\"track\"");
        StringAssert.Contains(telemetry, "data-context-toggle=\"laps\"");
        StringAssert.Contains(telemetry, "aria-pressed=\"@AriaBoolean(!TrackPanelCollapsed)\"");
        StringAssert.Contains(telemetry, "aria-pressed=\"@AriaBoolean(!LapRailCollapsed)\"");
        StringAssert.Contains(telemetry, "aria-controls=\"race-track-panel-content\"");
        StringAssert.Contains(telemetry, "aria-controls=\"race-lap-rail-content\"");
        StringAssert.Contains(telemetry, "aria-hidden=\"@(IsRaceWorkspace && TrackPanelCollapsed ? \"true\" : null)\"");
        StringAssert.Contains(telemetry, "inert=\"@(IsRaceWorkspace && TrackPanelCollapsed ? string.Empty : null)\"");
        StringAssert.Contains(telemetry, "aria-hidden=\"@(IsRaceWorkspace && LapRailCollapsed ? \"true\" : null)\"");
        StringAssert.Contains(telemetry, "inert=\"@(IsRaceWorkspace && LapRailCollapsed ? string.Empty : null)\"");
        Assert.DoesNotContain("@if (!TrackPanelCollapsed)", telemetry, "Collapse must retain the track DOM so map/cursor state survives the motion.");
        Assert.DoesNotContain("@if (!LapRailCollapsed)", telemetry, "Collapse must retain the lap DOM so selection and scroll state survive the motion.");
        Assert.DoesNotContain("ToggleLapRail() => ResetSelection", telemetry);
        Assert.DoesNotContain("ToggleLapRail() => ClearSelection", telemetry);

        StringAssert.Contains(css, "--motion-structure: 500ms;");
        StringAssert.Contains(css, "@media (prefers-reduced-motion: reduce) { :root { --motion-structure: 0ms; } }");
        StringAssert.Contains(css, ".reduced-motion { --motion-structure: 0ms; }");
        StringAssert.Contains(css, ".analysis-context-toggles {");
        StringAssert.Contains(css, ".context-toggle-chip {");
        StringAssert.Contains(css, "transition: color var(--motion-hover) var(--ease),background var(--motion-hover) var(--ease),border-color var(--motion-hover) var(--ease),box-shadow var(--motion-hover) var(--ease),transform var(--motion-hover) var(--ease);");
        StringAssert.Contains(css, ".race-workstation.context-both");
        StringAssert.Contains(css, ".race-workstation.context-track");
        StringAssert.Contains(css, ".race-workstation.context-laps");
        StringAssert.Contains(css, ".race-workstation.context-none");
        StringAssert.Contains(css, "grid-template-columns: 0 minmax(0,1fr);");
        StringAssert.Contains(css, "grid-template-rows: minmax(0,var(--analysis-context-track-share,.43fr)) 9px minmax(0,var(--analysis-context-laps-share,.57fr));");
        StringAssert.Contains(css, "grid-template-rows: minmax(0,1fr) 0 minmax(0,0fr);");
        StringAssert.Contains(css, "grid-template-rows: minmax(0,0fr) 0 minmax(0,1fr);");
        StringAssert.Contains(css, "transition: grid-template-columns var(--motion-structure) var(--ease),gap var(--motion-structure) var(--ease);");
        StringAssert.Contains(css, "transition: grid-template-rows var(--motion-structure) var(--ease),gap var(--motion-structure) var(--ease),opacity var(--motion-structure) var(--ease)");
        StringAssert.Contains(css, "visibility 0s linear var(--motion-structure)");
        StringAssert.Contains(css, "translateX(-18px)");
        StringAssert.Contains(css, "@container (max-width: 1060px)");
        StringAssert.Contains(css, ".reduced-motion .race-workstation");
        StringAssert.Contains(css, ".telemetry-workstation-grid.qualifying-workstation {");
        StringAssert.Contains(css, ".qualifying-workstation .track-panel { grid-column: 2; grid-row: 1; }");
    }

    [TestMethod]
    public void RaceAnalysisLapFiltersAndSpotlight_AreTruthfulAndKeepTheSpotlightOnTop()
    {
        var ui = Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI");
        var telemetry = File.ReadAllText(Path.Combine(ui, "TelemetryWorkspace.razor"));
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));

        StringAssert.Contains(telemetry, "class=\"green-lap-filter\"");
        StringAssert.Contains(telemetry, "@onchange=\"GreenLapFilterChanged\" /> Green laps");
        StringAssert.Contains(telemetry, "class=\"clean-lap-filter\"");
        StringAssert.Contains(telemetry, "@onchange=\"CleanLapFilterChanged\" /> Clean laps");
        StringAssert.Contains(telemetry, "private bool GreenLapsOnly { get; set; }");
        StringAssert.Contains(telemetry, "private bool PassesLapFilters(AnalysisLapTrace trace)");
        StringAssert.Contains(telemetry, "(!CleanLapsOnly || IsClean(trace)) && (!GreenLapsOnly || IsGreenLap(trace))");
        StringAssert.Contains(telemetry, "Selected.RemoveWhere(lap =>");

        StringAssert.Contains(telemetry, "class=\"trace-spotlight-control\"");
        StringAssert.Contains(telemetry, "class=\"spotlight-menu-trigger\"");
        StringAssert.Contains(telemetry, "aria-haspopup=\"listbox\"");
        StringAssert.Contains(telemetry, "aria-expanded=\"@AriaBoolean(SpotlightMenuOpen)\"");
        StringAssert.Contains(telemetry, "class=\"spotlight-menu\" role=\"listbox\"");
        StringAssert.Contains(telemetry, "class=\"spotlight-option @(SpotlightLap == trace.Lap ? \"selected\" : null)\"");
        StringAssert.Contains(telemetry, "var spotlightFastest =");
        StringAssert.Contains(telemetry, "@(spotlightFastest ? \" · Fastest\" : string.Empty)");
        StringAssert.Contains(telemetry, "var sectorState = SectorState(trace, sector);");
        StringAssert.Contains(telemetry, "class=\"sector-square @sectorState.Class\"");
        StringAssert.Contains(telemetry, "private IReadOnlyList<AnalysisLapTrace> SpotlightCandidates");
        StringAssert.Contains(telemetry, ".Where(IsGreenLap)");
        StringAssert.Contains(telemetry, "@onclick=\"ClearSpotlight\">Clear</button>");
        StringAssert.Contains(telemetry, "@onclick=\"CloseSpotlightMenu\"");
        StringAssert.Contains(telemetry, "private async Task SelectSpotlight(int lap)");
        StringAssert.Contains(telemetry, "SpotlightMenuOpen = false;");
        StringAssert.Contains(telemetry, "await JS.InvokeVoidAsync(\"iracingCoach.blurActiveElement\")");
        StringAssert.Contains(telemetry, "private IEnumerable<AnalysisLapTrace> RenderedSelectedTraces");
        StringAssert.Contains(telemetry, ".OrderBy(trace => SpotlightLap == trace.Lap ? 1 : 0)");
        StringAssert.Contains(telemetry, "@foreach (var traceGroup in ChartTracePaths(panel, indexedSignal.Signal, indexedSignal.Index, top))");
        StringAssert.Contains(telemetry, "data-spotlight=\"@AriaBoolean(traceGroup.Spotlight)\"");
        StringAssert.Contains(telemetry, ".GroupBy(trace => new { Color = LapColor(trace.Lap), Spotlight = SpotlightLap == trace.Lap })");
        StringAssert.Contains(telemetry, "private string TraceStrokeWidth(AnalysisLapTrace trace, int signalIndex)");
        StringAssert.Contains(telemetry, "private string TraceOpacity(AnalysisLapTrace trace, int signalIndex)");
        StringAssert.Contains(telemetry, "return signalIndex == 0 ? \"2.35\" : \"1.8\";");
        StringAssert.Contains(telemetry, "return signalIndex == 0 ? \".42\" : \".30\";");
        StringAssert.Contains(css, ".trace-spotlight-control");
        StringAssert.Contains(css, ".spotlight-menu");
        StringAssert.Contains(css, ".spotlight-option");
        StringAssert.Contains(css, ".configurable-trace-chart [data-analysis-trace-path]");
    }

    [TestMethod]
    public void RaceHeader_UsesOneCompactRowWithDataTabsIdentityAndSessionSwitch()
    {
        var ui = Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI");
        var workspace = File.ReadAllText(Path.Combine(ui, "AnalysisWorkspacePage.razor"));
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));

        StringAssert.Contains(workspace, "<header class=\"analysis-context-bar race-analysis-toolbar\">");
        var tabs = workspace.IndexOf("analysis-data-primary", StringComparison.Ordinal);
        var identity = workspace.IndexOf("race-analysis-title", StringComparison.Ordinal);
        var session = workspace.IndexOf("race-session-actions", StringComparison.Ordinal);
        Assert.IsTrue(tabs >= 0 && tabs < identity && identity < session,
            "Race data tabs, centered event identity/back, and session switch must occupy one left-to-right header row.");
        StringAssert.Contains(workspace, "aria-label=\"Race analysis section\"");
        StringAssert.Contains(workspace, "aria-label=\"Back to race history\"");
        StringAssert.Contains(workspace, "class=\"race-analysis-title-cluster\"");
        StringAssert.Contains(workspace, "<span>Back</span>");
        StringAssert.Contains(workspace, "aria-label=\"Event session\"");
        StringAssert.Contains(css, ".analysis-context-bar.race-analysis-toolbar {");
        StringAssert.Contains(css, "grid-template-columns: minmax(188px,220px) minmax(260px,1fr) minmax(150px,220px);");
        StringAssert.Contains(css, ".race-analysis-title {");
        StringAssert.Contains(css, "justify-content: center;");
        StringAssert.Contains(css, ".race-analysis-title-cluster {");
        StringAssert.Contains(css, ".race-session-actions { justify-self: end; }");
    }

    [TestMethod]
    public void RaceTelemetry_UsesStableViewportHeightAndAFlowOrderedLapFooter()
    {
        var ui = Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI");
        var telemetry = File.ReadAllText(Path.Combine(ui, "TelemetryWorkspace.razor"));
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));

        var footerMatch = Regex.Match(
            telemetry,
            "<div class=\"lap-rail-footer\">(?<content>.*?)</div>",
            RegexOptions.Singleline);
        Assert.IsTrue(footerMatch.Success, "The lap-selection actions must remain in one stable footer.");
        var footer = footerMatch.Groups["content"].Value;
        var fastest = footer.IndexOf(">Fastest</button>", StringComparison.Ordinal);
        var bestThree = footer.IndexOf(">Best three</button>", StringComparison.Ordinal);
        var clear = footer.IndexOf(">Clear</button>", StringComparison.Ordinal);
        var showAll = footer.IndexOf(">Show all</button>", StringComparison.Ordinal);
        Assert.IsTrue(fastest >= 0 && fastest < bestThree && bestThree < clear && clear < showAll,
            "The footer flow must be Fastest, Best three, Clear, Show all.");
        StringAssert.Contains(telemetry, "class=\"telemetry-empty-selection\"");
        StringAssert.Contains(telemetry, "No laps selected");
        StringAssert.Contains(telemetry, "Select Fastest, Best three");
        var layoutInterop = telemetry.IndexOf("await ConfigureTraceLayoutInteropAsync();", StringComparison.Ordinal);
        var emptySelectionReturn = telemetry.IndexOf("if (Selected.Count == 0) return;", layoutInterop, StringComparison.Ordinal);
        Assert.IsTrue(layoutInterop >= 0 && emptySelectionReturn > layoutInterop,
            "Clear must leave trace-layout drag/drop initialized even while no telemetry laps are selected.");

        StringAssert.Contains(css, ".analysis-page-frame:has(.race-analysis-toolbar)");
        StringAssert.Contains(css, ".analysis-page-frame:has(.race-analysis-toolbar) .analysis-workspace-page");
        StringAssert.Contains(css, ".analysis-page-frame:has(.race-analysis-toolbar) .race-telemetry-page");
        StringAssert.Contains(css, ".analysis-page-frame:has(.race-analysis-toolbar) .telemetry-workspace");
        StringAssert.Contains(css, ".analysis-page-frame:has(.race-analysis-toolbar) .trace-chart-frame");
        StringAssert.Contains(css, "overflow: hidden;");
        Assert.DoesNotContain(".telemetry-empty-selection { min-height: 720px", css,
            "Clearing selection must not swap in a differently sized workbench.");
        Assert.IsTrue(Regex.IsMatch(css, @"\.analysis-page-frame:has\(\.race-analysis-toolbar\) \.telemetry-empty-selection\s*\{[^}]*position:\s*absolute;[^}]*inset:\s*0;[^}]*min-height:\s*0;", RegexOptions.Singleline),
            "The no-selection surface must fill the same viewport-derived station height as selected telemetry.");
    }

    [TestMethod]
    public void RaceAnalysisTrackAndRunSummary_ShowStartFinishAndSemanticFlagCounts()
    {
        var ui = Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI");
        var telemetry = File.ReadAllText(Path.Combine(ui, "TelemetryWorkspace.razor"));
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));

        StringAssert.Contains(telemetry, "class=\"track-start-line-halo\"");
        StringAssert.Contains(telemetry, "class=\"track-start-line\"");
        StringAssert.Contains(telemetry, "private MapLine StartFinishLine");
        StringAssert.Contains(css, ".track-start-line {");
        StringAssert.Contains(css, ".track-start-line-halo {");
        StringAssert.Contains(telemetry, "<i class=\"metric-dot green\"");
        StringAssert.Contains(telemetry, "@summaryRun.GreenLaps green");
        StringAssert.Contains(telemetry, "<i class=\"metric-dot yellow\"");
        StringAssert.Contains(telemetry, "@summaryRun.CautionLaps caution");
        StringAssert.Contains(css, ".run-summary-metrics .metric-dot");
    }

    [TestMethod]
    public void PitStopPopover_ShowsMeasuredOmiConditionWithoutInventingMissingRows()
    {
        var ui = Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI");
        var telemetry = File.ReadAllText(Path.Combine(ui, "TelemetryWorkspace.razor"));
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));

        StringAssert.Contains(telemetry, "var tireConditions = PitTireConditions(activePitRun.PitStop);");
        StringAssert.Contains(telemetry, "Measured tire condition");
        StringAssert.Contains(telemetry, "var bandLabels = TireBandLabels(tire.Corner);");
        StringAssert.Contains(telemetry, "private static IReadOnlyList<string> TireBandLabels(string corner)");
        StringAssert.Contains(telemetry, "corner.StartsWith(\"R\", StringComparison.OrdinalIgnoreCase) ? [\"I\", \"M\", \"O\"] : [\"O\", \"M\", \"I\"]");
        StringAssert.Contains(telemetry, "private static IReadOnlyList<double?> OrientedBands(string corner, AnalysisTireBands bands)");
        StringAssert.Contains(telemetry, "? [bands.Inner, bands.Middle, bands.Outer]");
        StringAssert.Contains(telemetry, ": [bands.Outer, bands.Middle, bands.Inner]");
        StringAssert.Contains(telemetry, "O/M/I on the left, I/M/O on the right");
        StringAssert.Contains(telemetry, "HasBandValues(tire.WearPercent)");
        StringAssert.Contains(telemetry, "HasBandValues(tire.CarcassTemperatureF)");
        StringAssert.Contains(telemetry, "HasBandValues(tire.SurfaceTemperatureF)");
        StringAssert.Contains(telemetry, "@PressureKind(tire.PressureKind) pressure");
        StringAssert.Contains(telemetry, "Recorded repair work");
        StringAssert.Contains(telemetry, "role=\"dialog\"");
        StringAssert.Contains(telemetry, "aria-controls=\"@PitPopoverId(trace.Lap, direction)\"");
        StringAssert.Contains(telemetry, "@onfocus=\"() => ShowPitPopoverFromFocus(trace.Lap, direction)\"");
        StringAssert.Contains(telemetry, "private async Task SchedulePitPopoverHide()");
        StringAssert.Contains(css, ".pit-tire-grid { display: grid; grid-template-columns: repeat(2,minmax(0,1fr));");
        StringAssert.Contains(css, ".pit-band-row {");
        StringAssert.Contains(css, "pointer-events: auto;");
        Assert.DoesNotContain("Hot pressure", telemetry);
    }

    [TestMethod]
    public void CursorMarkerPool_IsBoundedByTooltipCapacityForHighLapSelections()
    {
        var cursor = File.ReadAllText(Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI", "wwwroot", "analysis-telemetry-cursor.js"));

        StringAssert.Contains(cursor, "Array.from({ length: state.config.tooltipCapacity }");
        StringAssert.Contains(cursor, "const visibleTraces = state.config.traces.slice");
        StringAssert.Contains(cursor, "const trace = visibleTraces[slotIndex]");
        Assert.DoesNotContain("state.config.traces.map((trace) => row.signals", cursor);
        Assert.DoesNotContain("state.config.traces.forEach((trace, traceIndexValue)", cursor);
    }

    [TestMethod]
    public void RaceAnalysisPerformance_BoundsDomAndCursorWorkAtFiveHundredLogicalLaps()
    {
        var ui = Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI");
        var telemetry = File.ReadAllText(Path.Combine(ui, "TelemetryWorkspace.razor"));
        var cursor = File.ReadAllText(Path.Combine(ui, "wwwroot", "analysis-telemetry-cursor.js"));
        var map = File.ReadAllText(Path.Combine(ui, "wwwroot", "analysis-track-map.js"));
        var layout = File.ReadAllText(Path.Combine(ui, "wwwroot", "analysis-trace-layout.js"));
        var performanceCss = File.ReadAllText(Path.Combine(ui, "wwwroot", "analysis-performance.css"));
        var geometry = File.ReadAllText(Path.Combine(ui, "AnalysisTrackMapGeometry.cs"));

        StringAssert.Contains(telemetry, "private const int CursorSampleBins = AnalysisRenderBudget.CursorSampleBins;");
        StringAssert.Contains(telemetry, "private const int CursorComparisonBudget = AnalysisRenderBudget.CursorComparisonBudget;");
        StringAssert.Contains(telemetry, "AnalysisRenderBudget.PointBudgetPerTrace(");
        StringAssert.Contains(telemetry, "AnalysisRenderBudget.RepresentativeIndices(trace.Points.Count, pointBudget)");
        StringAssert.Contains(geometry, "public const int TraceVertexBudgetPerSignal = 48_000;");
        StringAssert.Contains(telemetry, "var allTraces = CursorSelectedTraces.ToArray();");
        StringAssert.Contains(telemetry, "var traces = BoundedCursorTraces(allTraces);");
        StringAssert.Contains(telemetry, "logicalTraceCount = allTraces.Length");
        StringAssert.Contains(telemetry, "logicalLaps = allTraces.Select(trace => trace.Lap).ToArray()");
        StringAssert.Contains(telemetry, "aggregatePoints");
        StringAssert.Contains(telemetry, "data-analysis-trace-render-layer");
        StringAssert.Contains(telemetry, ".GroupBy(trace => new { Color = LapColor(trace.Lap), Spotlight = SpotlightLap == trace.Lap })");
        StringAssert.Contains(telemetry, ".GroupBy(item => LapColor(item.Trace.Lap))");
        StringAssert.Contains(cursor, "state.config.aggregate");
        StringAssert.Contains(cursor, "logicalTraceCount: state.config.logicalTraceCount");
        StringAssert.Contains(cursor, "renderedPathNodes:");
        StringAssert.Contains(cursor, "aggregateBins:");
        StringAssert.Contains(cursor, "data-analysis-cursor-tooltips");
        StringAssert.Contains(performanceCss, ".analysis-cursor-tooltip-card");
        StringAssert.Contains(performanceCss, "font-stretch: normal;");
        StringAssert.Contains(map, "[data-map-cursor-radius]");
        StringAssert.Contains(map, "baseRadius / zoom");
        StringAssert.Contains(layout, "benchmarkStructuralMotion(root, cycles = 50)");
        StringAssert.Contains(layout, "for (let cycle = 0; cycle < count; cycle++)");
        StringAssert.Contains(layout, "framesOver25ms");
        StringAssert.Contains(layout, "layoutShiftScore");

        var expected = new Dictionary<int, (int ColorGroups, int CursorTraces, int PointsPerTrace)>
        {
            [1] = (1, 1, 500),
            [3] = (3, 3, 500),
            [20] = (20, 20, 500),
            [82] = (20, 24, 500),
            [500] = (20, 24, 96),
        };
        foreach (var (logicalCount, budget) in expected)
        {
            Assert.AreEqual(budget.ColorGroups, iRacingCoach.UI.AnalysisRenderBudget.ColorGroupCount(logicalCount));
            Assert.AreEqual(budget.CursorTraces, Math.Min(logicalCount, iRacingCoach.UI.AnalysisRenderBudget.CursorComparisonBudget));
            var pointBudget = iRacingCoach.UI.AnalysisRenderBudget.PointBudgetPerTrace(logicalCount, 1_000);
            Assert.HasCount(
                budget.PointsPerTrace,
                iRacingCoach.UI.AnalysisRenderBudget.RepresentativeIndices(500, pointBudget));
        }

        var syntheticFiveHundred = Enumerable.Range(1, 500).ToArray();
        Assert.HasCount(20, syntheticFiveHundred.GroupBy(lap => (lap - 1) % 20).ToArray());
        var fiveHundredPointBudget = iRacingCoach.UI.AnalysisRenderBudget.PointBudgetPerTrace(500, 1_000);
        Assert.IsLessThanOrEqualTo(
            iRacingCoach.UI.AnalysisRenderBudget.TraceVertexBudgetPerSignal,
            syntheticFiveHundred.Length * iRacingCoach.UI.AnalysisRenderBudget.RepresentativeIndices(500, fiveHundredPointBudget).Count);
    }

    [TestMethod]
    public void AnalysisCursor_CoalescesPointerAndResizeWorkWithoutForcedLayout()
    {
        var cursor = File.ReadAllText(Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI", "wwwroot", "analysis-telemetry-cursor.js"));

        Assert.DoesNotContain("getComputedTextLength", cursor,
            "A synchronous SVG measurement per visible lap and row makes cursor latency grow with selection size.");
        Assert.DoesNotContain("setTimeout", cursor,
            "Cursor and ResizeObserver work must remain animation-frame owned, not timer-debounced.");
        Assert.DoesNotContain("resizeTimer", cursor);
        StringAssert.Contains(cursor, "if (!state.frame) state.frame = requestAnimationFrame(timestamp => renderCursor(state, timestamp));");
        StringAssert.Contains(cursor, "if (state.resizePending)");
        StringAssert.Contains(cursor, "state.resizePending = true;");
        StringAssert.Contains(cursor, "state.pathBasePlotWidth = Math.max(1, state.config.plotWidth || state.plotWidth)");
        StringAssert.Contains(cursor, "[data-analysis-trace-render-layer]");
        StringAssert.Contains(cursor, "setAttributeIfChanged(layer, \"transform\", pathTransform)");
        Assert.DoesNotContain("state.config.traces.find(candidate => candidate.lap", cursor,
            "ResizeObserver work must never search every selected lap for every rendered path.");
        StringAssert.Contains(cursor, "state.inputSource = \"chart\"");
        StringAssert.Contains(cursor, "state.inputSource = \"track\"");
        StringAssert.Contains(cursor, "if (state.inputSource === \"track\" && state.trackInside) updateFractionFromTrackPointer(state);");
        StringAssert.Contains(cursor, "else if (state.chartInside) updateFractionFromPointer(state);");

        var chartMove = Regex.Match(cursor, @"state\.move = \(event\) => \{(?<body>.*?)\n\s*\};", RegexOptions.Singleline);
        Assert.IsTrue(chartMove.Success);
        StringAssert.Contains(chartMove.Groups["body"].Value, "schedule(state);");
        Assert.DoesNotContain("updateFraction", chartMove.Groups["body"].Value,
            "Raw pointer events should only capture the latest coordinates; one frame computes the cursor.");

        var trackMove = Regex.Match(cursor, @"state\.trackMove = \(event\) => \{(?<body>.*?)\n\s*\};", RegexOptions.Singleline);
        Assert.IsTrue(trackMove.Success);
        StringAssert.Contains(trackMove.Groups["body"].Value, "schedule(state);");
        Assert.DoesNotContain("updateFraction", trackMove.Groups["body"].Value);
    }

    [TestMethod]
    public void TrackAndChartCursors_ShareOneFrameSynchronizedBrowserOwner()
    {
        var ui = Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI");
        var telemetry = File.ReadAllText(Path.Combine(ui, "TelemetryWorkspace.razor"));
        var cursor = File.ReadAllText(Path.Combine(ui, "wwwroot", "analysis-telemetry-cursor.js"));
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));

        Assert.DoesNotContain("@onmousemove=\"TrackMoved\"", telemetry,
            "The track must not retain a second, asynchronous Blazor cursor owner.");
        Assert.DoesNotContain("private async Task TrackMoved", telemetry);
        Assert.DoesNotContain("iracingCoach.pointerViewBox", telemetry);
        StringAssert.Contains(telemetry, "<circle data-analysis-track-cursor-point");
        Assert.DoesNotContain("data-analysis-track-cursor-line", telemetry, "The map cursor is a dot, not a crosshair or guide line.");
        StringAssert.Contains(cursor, "querySelectorAll(\"[data-analysis-track-cursor-point]\")");
        Assert.IsTrue(Regex.IsMatch(css, @"\.track-map\s*\{[^}]*cursor:\s*default;", RegexOptions.Singleline),
            "Track-map hover must retain the normal pointer while the synchronized dot follows it.");

        StringAssert.Contains(cursor, "state.trackElement.addEventListener(\"pointermove\", state.trackMove)");
        StringAssert.Contains(cursor, "state.boundTrackElement.removeEventListener(\"pointermove\", state.trackMove)");
        StringAssert.Contains(cursor, "getScreenCTM");
        StringAssert.Contains(cursor, "projectedTrackFraction");
        StringAssert.Contains(cursor, "state.trackInside = true");
        StringAssert.Contains(cursor, "if (!cursorActive(state)) return");
        StringAssert.Contains(cursor, "if (!state.chartInside || !state.layer)");
        StringAssert.Contains(cursor, "if (state.layer) setVisible(state.layer, false)");
        StringAssert.Contains(cursor, "requestAnimationFrame(timestamp => renderCursor(state, timestamp))");
    }

    [TestMethod]
    public void RaceAnalysisResponsiveLayoutAndCursorTooltipsHonorNarrowWindowGeometry()
    {
        var root = CompanionRoot();
        var ui = Path.Combine(root, "src", "iRacingCoach.UI");
        var mainWindow = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.App", "MainWindow.xaml"));
        var telemetry = File.ReadAllText(Path.Combine(ui, "TelemetryWorkspace.razor"));
        var cursor = File.ReadAllText(Path.Combine(ui, "wwwroot", "analysis-telemetry-cursor.js"));
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));

        StringAssert.Contains(mainWindow, "MinWidth=\"900\"");
        StringAssert.Contains(css, "@container (max-width: 1060px)");
        StringAssert.Contains(css, ".telemetry-workstation-grid { grid-template-columns: minmax(410px,40%) minmax(0,1fr); }");
        StringAssert.Contains(css, ".telemetry-grid { grid-template-columns: minmax(0,1fr); }");
        StringAssert.Contains(css, "@container (max-width: 600px)");
        Assert.DoesNotContain("grid-template-columns: minmax(280px,38%)", css);

        StringAssert.Contains(telemetry, "Math.Max(40, _chartWidth - PlotLeft - 20)");
        Assert.DoesNotContain("size[0] < 400", telemetry);
        StringAssert.Contains(cursor, "elementWidth <= 0");
        StringAssert.Contains(cursor, "Math.max(40, elementWidth - state.plotLeft - 20)");
        Assert.DoesNotContain("elementWidth < 400", cursor);

        Assert.DoesNotContain("getComputedTextLength", cursor,
            "Responsive tooltip sizing must remain frame-local and avoid forced SVG layout.");
        StringAssert.Contains(cursor, "widest * state.config.tooltipCharacterWidth");
        StringAssert.Contains(cursor, "desiredTooltipWidth");
        StringAssert.Contains(cursor, "availableTooltipWidth");
        StringAssert.Contains(cursor, "rightCandidate + tooltipWidth <= plotEnd");
        Assert.DoesNotContain(", 102, 184", cursor);

        const double narrowThreePaneWidth = 1061;
        var lapRailWidth = Math.Max(424, narrowThreePaneWidth * .38);
        var telemetryPaneWidth = narrowThreePaneWidth - lapRailWidth - 9;
        var trackPaneWidth = Math.Max(210, telemetryPaneWidth * .32);
        var tracePaneWidth = telemetryPaneWidth - trackPaneWidth - 9;
        Assert.IsGreaterThanOrEqualTo(400, tracePaneWidth, "The last three-pane width must preserve the trace pane's usable geometry.");
    }

    [TestMethod]
    public void FullScreenTracePanel_CapturesEscapeWithoutLeavingPointerButtonSelected()
    {
        var ui = Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI");
        var telemetry = File.ReadAllText(Path.Combine(ui, "TelemetryWorkspace.razor"));
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));

        StringAssert.Contains(telemetry, "data-analysis-trace-studio\n                 tabindex=\"-1\"");
        StringAssert.Contains(telemetry, "@onclick=\"() => ToggleTraceExpanded(false)\"");
        StringAssert.Contains(telemetry, "if (TraceExpanded) await TraceStudioElement.FocusAsync();");
        StringAssert.Contains(telemetry, "else if (TraceExpanded) await ToggleTraceExpanded(true);");
        StringAssert.Contains(telemetry, "else if (restoreKeyboardFocus) await TraceExpandButtonElement.FocusAsync();");
        StringAssert.Contains(css, ".analysis-trace-studio:focus { outline: none; }");
    }

    private static string CompanionRoot() => TestRepositoryPaths.CompanionAppRoot;
}
