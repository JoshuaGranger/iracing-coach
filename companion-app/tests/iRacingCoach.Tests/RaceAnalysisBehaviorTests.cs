using System.Runtime.CompilerServices;
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
        StringAssert.Contains(workspace, "analysis-context-compact");
        StringAssert.Contains(workspace, "race-review-grades");
        StringAssert.Contains(telemetry, "Average of selected laps");
        StringAssert.Contains(telemetry, "SpeedHeatmapStops");
        StringAssert.Contains(telemetry, "HeatmapColor(normalized, SpeedHeatmapStops)");
        StringAssert.Contains(telemetry, "analysis-trace-toolbox");
        Assert.DoesNotContain("analysis-trace-toolbox-backdrop", telemetry);
        StringAssert.Contains(telemetry, "inert=\"@(!TraceToolboxOpen ? string.Empty : null)\"");
        StringAssert.Contains(telemetry, "<ProductIcon Name=\"setup\" Size=\"15\" /> Customize");
        StringAssert.Contains(telemetry, "aria-label=\"Customize trace charts\"");
        Assert.DoesNotContain("aria-label=\"@(TraceToolboxOpen ? \"Hide trace toolbox\"", telemetry);
        Assert.DoesNotContain("<ProductIcon Name=\"settings\"", telemetry);
        StringAssert.Contains(telemetry, "analysis-trace-catalog");
        StringAssert.Contains(telemetry, "data-analysis-drag-signal");
        StringAssert.Contains(telemetry, "data-analysis-drag-row");
        StringAssert.Contains(telemetry, "MoveTraceRowToIndex");
        StringAssert.Contains(telemetry, "InsertTraceSignalRow");
        StringAssert.Contains(telemetry, "PlaceTraceSignal");
        StringAssert.Contains(telemetry, "tabindex=\"0\"");
        StringAssert.Contains(telemetry, "HandleTraceSignalKeyDown");
        StringAssert.Contains(telemetry, "@onkeydown:stopPropagation=\"true\"");
        StringAssert.Contains(telemetry, "CloseTraceToolbox");
        StringAssert.Contains(telemetry, "TraceToolboxButtonElement.FocusAsync");
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
        StringAssert.Contains(cursor, "setAttribute(\"viewBox\"");
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
        StringAssert.Contains(css, ".trace-panel.trace-panel-expanded { transition: right 260ms var(--ease); }");
        StringAssert.Contains(css, ".trace-panel.trace-panel-expanded.toolbox-open { right: var(--side-toolbox-width); }");
        StringAssert.Contains(css, ".trace-toolbox-button {");
        StringAssert.Contains(css, "min-width: 92px;");
        StringAssert.Contains(css, ".analysis-trace-metric-card:focus-visible");
        StringAssert.Contains(css, ".trace-selected-signal > i.unavailable");
    }

    [TestMethod]
    public void RaceAnalysisContextColumn_StacksTrackAboveLapsAndCollapsesEachPanelSmoothly()
    {
        var ui = Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI");
        var telemetry = File.ReadAllText(Path.Combine(ui, "TelemetryWorkspace.razor"));
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));

        StringAssert.Contains(telemetry, "private bool LapRailCollapsed { get; set; }");
        StringAssert.Contains(telemetry, "private bool TrackPanelCollapsed { get; set; }");
        StringAssert.Contains(telemetry, "LapRailCollapsed = false;");
        StringAssert.Contains(telemetry, "TrackPanelCollapsed = false;");
        StringAssert.Contains(telemetry, "private void ToggleLapRail() => LapRailCollapsed = !LapRailCollapsed;");
        StringAssert.Contains(telemetry, "private void ToggleTrackPanel() => TrackPanelCollapsed = !TrackPanelCollapsed;");
        StringAssert.Contains(telemetry, "[Parameter] public bool IsRaceWorkspace { get; set; }");
        StringAssert.Contains(telemetry, "IsRaceWorkspace ? \"race-workstation\" : \"qualifying-workstation\"");
        StringAssert.Contains(telemetry, "<div class=\"telemetry-context-column\">");
        Assert.IsLessThan(
            telemetry.IndexOf("<aside class=\"lap-rail telemetry-context-panel", StringComparison.Ordinal),
            telemetry.IndexOf("<section class=\"track-panel telemetry-context-panel", StringComparison.Ordinal),
            "Track position must be mounted above laps and runs in the shared context column.");
        StringAssert.Contains(telemetry, "aria-controls=\"race-track-panel-content\"");
        StringAssert.Contains(telemetry, "aria-expanded=\"@(!TrackPanelCollapsed)\"");
        StringAssert.Contains(telemetry, "aria-hidden=\"@TrackPanelCollapsed\"");
        StringAssert.Contains(telemetry, "inert=\"@(TrackPanelCollapsed ? string.Empty : null)\"");
        StringAssert.Contains(telemetry, "class=\"lap-rail-toggle\"");
        StringAssert.Contains(telemetry, "aria-controls=\"race-lap-rail-content\"");
        StringAssert.Contains(telemetry, "aria-expanded=\"@(!LapRailCollapsed)\"");
        StringAssert.Contains(telemetry, "aria-hidden=\"@LapRailCollapsed\"");
        StringAssert.Contains(telemetry, "inert=\"@(LapRailCollapsed ? string.Empty : null)\"");
        Assert.DoesNotContain("ToggleLapRail() => ResetSelection", telemetry);
        Assert.DoesNotContain("ToggleLapRail() => ClearSelection", telemetry);

        StringAssert.Contains(css, ".telemetry-context-column {");
        StringAssert.Contains(css, "flex-direction: column;");
        StringAssert.Contains(css, ".telemetry-context-column .track-panel.collapsed");
        StringAssert.Contains(css, ".track-open.laps-collapsed .track-panel");
        StringAssert.Contains(css, ".laps-open.track-collapsed .lap-rail");
        StringAssert.Contains(css, "transition: flex-grow 250ms var(--ease),flex-basis 250ms var(--ease)");
        StringAssert.Contains(css, ".track-panel-content {");
        StringAssert.Contains(css, "transition: opacity 180ms var(--ease) 45ms,transform 220ms var(--ease) 25ms");
        StringAssert.Contains(css, ".lap-rail-toggle:focus-visible");
        StringAssert.Contains(css, "@container (max-width: 1060px)");
        StringAssert.Contains(css, ".reduced-motion .telemetry-context-panel");
        StringAssert.Contains(css, ".telemetry-workstation-grid.qualifying-workstation {");
        StringAssert.Contains(css, ".qualifying-workstation .track-panel { grid-column: 2; grid-row: 1; }");
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
        StringAssert.Contains(telemetry, "<b>O</b><b>M</b><b>I</b>");
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
    public void TrackAndChartCursors_ShareOneFrameSynchronizedBrowserOwner()
    {
        var ui = Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI");
        var telemetry = File.ReadAllText(Path.Combine(ui, "TelemetryWorkspace.razor"));
        var cursor = File.ReadAllText(Path.Combine(ui, "wwwroot", "analysis-telemetry-cursor.js"));

        Assert.DoesNotContain("@onmousemove=\"TrackMoved\"", telemetry,
            "The track must not retain a second, asynchronous Blazor cursor owner.");
        Assert.DoesNotContain("private async Task TrackMoved", telemetry);
        Assert.DoesNotContain("iracingCoach.pointerViewBox", telemetry);

        StringAssert.Contains(cursor, "state.trackElement.addEventListener(\"pointermove\", state.trackMove)");
        StringAssert.Contains(cursor, "state.boundTrackElement.removeEventListener(\"pointermove\", state.trackMove)");
        StringAssert.Contains(cursor, "getScreenCTM");
        StringAssert.Contains(cursor, "projectedTrackFraction");
        StringAssert.Contains(cursor, "state.trackInside = true");
        StringAssert.Contains(cursor, "if (!cursorActive(state)) return");
        StringAssert.Contains(cursor, "if (!state.chartInside || !state.layer)");
        StringAssert.Contains(cursor, "state.layer.style.display = \"none\"");
        StringAssert.Contains(cursor, "requestAnimationFrame(() => renderCursor(state))");
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

        StringAssert.Contains(cursor, "getComputedTextLength()");
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

    private static string CompanionRoot([CallerFilePath] string source = "") =>
        Path.GetFullPath(Path.Combine(Path.GetDirectoryName(source)!, "..", ".."));
}
