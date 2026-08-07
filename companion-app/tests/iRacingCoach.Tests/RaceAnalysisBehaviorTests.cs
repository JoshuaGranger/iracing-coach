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
    public void RaceAnalysisUi_ExposesRealOverviewStatsConfigurableRowsAndDomDrivenFrameSyncedCursor()
    {
        var ui = Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI");
        var analysis = File.ReadAllText(Path.Combine(ui, "AnalysisPage.razor"));
        var workspace = File.ReadAllText(Path.Combine(ui, "AnalysisWorkspacePage.razor"));
        var telemetry = File.ReadAllText(Path.Combine(ui, "TelemetryWorkspace.razor"));
        var cursor = File.ReadAllText(Path.Combine(ui, "wwwroot", "analysis-telemetry-cursor.js"));
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
        StringAssert.Contains(telemetry, "trace-signal-library");
        StringAssert.Contains(telemetry, "draggable=\"true\"");
        StringAssert.Contains(telemetry, "DropTraceSignal");
        StringAssert.Contains(telemetry, "CanUseAsSecondSignal");
        StringAssert.Contains(telemetry, "SignalRange(signal)");
        StringAssert.Contains(telemetry, "independent scales");
        StringAssert.Contains(telemetry, "@ondragover:preventDefault");
        StringAssert.Contains(telemetry, "trace-drop-feedback");
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
        Assert.DoesNotContain("DotNetObjectReference", telemetry);
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
        StringAssert.Contains(css, ".trace-signal-drop-target.drop-duplicate.drop-active");
        StringAssert.Contains(css, ".trace-signal-chip");
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
        StringAssert.Contains(css, ".telemetry-workstation-grid { width: 100%; min-width: 0; grid-template-columns: minmax(0,1fr); }");
        StringAssert.Contains(css, ".telemetry-grid { min-width: 0; grid-template-columns: minmax(180px,30%) minmax(400px,1fr); }");
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
