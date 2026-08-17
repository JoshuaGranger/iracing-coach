using System.Text.Json;
using System.Text.RegularExpressions;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class CapabilityRegistryTests
{
    [TestMethod]
    public void Inventory_ClassifiesEveryProductCapabilityExactlyOnce()
    {
        var inventory = CapabilityRegistry.Inventory;
        Assert.HasCount(Enum.GetValues<ProductCapability>().Length, inventory);
        Assert.HasCount(inventory.Count, inventory.Select(item => item.Id).Distinct());
        Assert.IsTrue(inventory.All(item =>
            !string.IsNullOrWhiteSpace(item.UserValue)
            && !string.IsNullOrWhiteSpace(item.DataSource)
            && !string.IsNullOrWhiteSpace(item.Validation)
            && !string.IsNullOrWhiteSpace(item.TemporaryFailureStates)
            && !string.IsNullOrWhiteSpace(item.SupportedFallback)
            && !string.IsNullOrWhiteSpace(item.ProductionVisibilityDecision)));
    }

    [TestMethod]
    public void PermanentAndIncompleteCapabilities_AreNeverVisibleOrActiveForAi()
    {
        var context = FullyPopulated();
        var hidden = new[]
        {
            ProductCapability.Garage61GlobalComparison, ProductCapability.QualifyingAnalysis,
            ProductCapability.SetupExperimentTab,
            ProductCapability.ExactTargetTrace, ProductCapability.PushToPass,
            ProductCapability.WeightJacker, ProductCapability.WetWeatherAnalysis,
            ProductCapability.MulticlassAnalysis
        };
        foreach (var capability in hidden) Assert.IsFalse(CapabilityRegistry.Evaluate(capability, context).Visible, capability.ToString());
        var active = CapabilityRegistry.ActiveForAi(context).Select(item => item.Definition.Id).ToHashSet();
        foreach (var capability in hidden) Assert.DoesNotContain(capability, active, capability.ToString());
    }

    [TestMethod]
    public void RacePlanningAndTuning_AppearOnlyWithRealEligibleHistory()
    {
        var empty = new CapabilityContext();
        Assert.IsFalse(CapabilityRegistry.Evaluate(ProductCapability.RacePlanning, empty).Visible);
        Assert.IsFalse(CapabilityRegistry.Evaluate(ProductCapability.ProgressiveTuning, empty).Visible);

        var populated = new CapabilityContext { HasRaceRecordings = true, HasOpenAnalyzedRace = true };
        Assert.IsTrue(CapabilityRegistry.Evaluate(ProductCapability.RacePlanning, populated).Visible);
        Assert.IsTrue(CapabilityRegistry.Evaluate(ProductCapability.ProgressiveTuning, populated).Visible);
    }

    [TestMethod]
    public void LiveCapabilities_UseOnlyValuesPresentInCurrentSnapshot()
    {
        var partial = new CapabilityContext { LiveConnected = true, HasAheadGap = true, HasLastLap = true };
        Assert.IsTrue(CapabilityRegistry.Evaluate(ProductCapability.LiveAheadGap, partial).Visible);
        Assert.IsTrue(CapabilityRegistry.Evaluate(ProductCapability.LiveLastLap, partial).Visible);
        Assert.IsFalse(CapabilityRegistry.Evaluate(ProductCapability.LiveLeaderGap, partial).Visible);
        Assert.IsFalse(CapabilityRegistry.Evaluate(ProductCapability.LivePaceRange, partial).Visible);
        Assert.IsFalse(CapabilityRegistry.Evaluate(ProductCapability.LivePitWindow, partial).Visible);
    }

    [TestMethod]
    public void SetupAndEventCapabilities_AdaptAcrossFixedOpenAndFinalizationContexts()
    {
        var nonFinalized = new CapabilityContext { HasOpenEvents = true };
        Assert.IsFalse(CapabilityRegistry.Evaluate(ProductCapability.RacePlanning, nonFinalized).Visible);
        Assert.IsFalse(CapabilityRegistry.Evaluate(ProductCapability.ProgressiveTuning, nonFinalized).Visible);
        Assert.IsTrue(CapabilityRegistry.Evaluate(ProductCapability.OpenSetupFilter, nonFinalized).Visible);
        Assert.IsFalse(CapabilityRegistry.Evaluate(ProductCapability.FixedSetupFilter, nonFinalized).Visible);

        var finalizedFixed = new CapabilityContext { HasRaceRecordings = true, HasFixedEvents = true, HasAnalyzedEvents = true };
        Assert.IsTrue(CapabilityRegistry.Evaluate(ProductCapability.RacePlanning, finalizedFixed).Visible);
        Assert.IsFalse(CapabilityRegistry.Evaluate(ProductCapability.ProgressiveTuning, finalizedFixed).Visible);
        Assert.IsTrue(CapabilityRegistry.Evaluate(ProductCapability.FixedSetupFilter, finalizedFixed).Visible);

        var analyzedOpen = new CapabilityContext { HasRaceRecordings = true, HasOpenEvents = true, HasOpenAnalyzedRace = true };
        Assert.IsTrue(CapabilityRegistry.Evaluate(ProductCapability.ProgressiveTuning, analyzedOpen).Visible);
    }

    [TestMethod]
    public void TrackViewAndSetupComparison_AreTruthfullyConditionalWhileLibraryUiIsRemoved()
    {
        var inventory = CapabilityRegistry.Inventory.ToDictionary(item => item.Id);
        Assert.AreEqual(CapabilityClass.ConditionallyApplicable, inventory[ProductCapability.TrackMap].Classification);
        Assert.AreEqual(CapabilityClass.ConditionallyApplicable, inventory[ProductCapability.SetupComparison].Classification);
        Assert.IsFalse(CapabilityRegistry.Evaluate(ProductCapability.TrackMap, new CapabilityContext()).Visible);
        Assert.IsTrue(CapabilityRegistry.Evaluate(ProductCapability.TrackMap, new CapabilityContext { HasTrackView = true }).Visible);
        Assert.IsFalse(CapabilityRegistry.Evaluate(ProductCapability.SetupComparison, new CapabilityContext()).Visible);
        Assert.IsTrue(CapabilityRegistry.Evaluate(ProductCapability.SetupComparison, new CapabilityContext { HasComparableSetups = true }).Visible);

        var ui = Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI");
        StringAssert.Contains(File.ReadAllText(Path.Combine(ui, "TelemetryWorkspace.razor")), "track-map");
        var setup = File.ReadAllText(Path.Combine(ui, "SetupPage.razor"));
        Assert.DoesNotContain("ProductCapability.SetupComparison", setup);
        Assert.DoesNotContain("Setup library", setup);
    }

    [TestMethod]
    public void UnvalidatedWetMulticlassAndGarage61GlobalScopes_StayAbsentInEveryContext()
    {
        foreach (var context in new[] { new CapabilityContext(), FullyPopulated() })
        {
            Assert.IsFalse(CapabilityRegistry.Evaluate(ProductCapability.WetWeatherAnalysis, context).Visible);
            Assert.IsFalse(CapabilityRegistry.Evaluate(ProductCapability.MulticlassAnalysis, context).Visible);
            Assert.IsFalse(CapabilityRegistry.Evaluate(ProductCapability.Garage61GlobalComparison, context).Visible);
        }
        StringAssert.Contains(
            CapabilityRegistry.Inventory.Single(item => item.Id == ProductCapability.Garage61GlobalComparison).SupportedFallback,
            "personal local history");
    }

    [TestMethod]
    public void TemporaryOutages_ExposeOnlyARecoveryState()
    {
        var signedOut = CapabilityRegistry.Evaluate(ProductCapability.ChatGptCoaching, new CapabilityContext { CoachEngineInstalled = true, CoachEngineRunning = true });
        Assert.IsTrue(signedOut.Visible);
        Assert.AreEqual(CapabilityClass.TemporarilyUnavailable, signedOut.Classification);
        StringAssert.Contains(signedOut.RecoveryAction!, "Reconnect");

        var missingEngine = CapabilityRegistry.Evaluate(ProductCapability.ChatGptCoaching, new CapabilityContext());
        Assert.IsFalse(missingEngine.Visible);
        StringAssert.Contains(missingEngine.RecoveryAction!, "Repair");

        var retrying = CapabilityRegistry.Evaluate(ProductCapability.Garage61Connection, new CapabilityContext { Garage61Configured = true });
        Assert.IsTrue(retrying.Visible);
        StringAssert.Contains(retrying.StateMessage!, "retrying");
    }

    [TestMethod]
    public void EvidenceTruth_RemainsInternalWhileMissingClaimsAreNotPresentable()
    {
        var claim = new EvidenceText(EvidenceKind.Unavailable, "Raw analysis retained this missing field.");
        Assert.IsFalse(CapabilityRegistry.IsSupported(claim));
        Assert.IsTrue(CapabilityRegistry.IsSupported(new EvidenceText(EvidenceKind.Derived, "Calculated from clean laps.")));
    }

    [TestMethod]
    public void ProductionUi_HasNoFakeSetupTabsQualifyingMilestoneOrMissingValueLabels()
    {
        var ui = Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI");
        var production = Directory.EnumerateFiles(ui, "*.razor").Select(File.ReadAllText).ToArray();
        var visibleMissingValue = new Regex(@">\s*(?:N/A|Unavailable|Unsupported|Coming soon|Not implemented)\b", RegexOptions.IgnoreCase);
        Assert.IsFalse(production.Any(text => visibleMissingValue.IsMatch(text)));
        Assert.IsFalse(production.Any(text => text.Contains("[U]", StringComparison.Ordinal)));

        var setup = File.ReadAllText(Path.Combine(ui, "SetupPage.razor"));
        StringAssert.Contains(setup, "Starting Tune");
        StringAssert.Contains(setup, "Find starting point");
        Assert.DoesNotContain("Setup library", setup);
        Assert.DoesNotContain(">Compare<", setup);
        Assert.DoesNotContain("section-tabs", setup);
        Assert.IsFalse(File.Exists(Path.Combine(ui, "TrackTelemetryView.razor")));

        var analysis = File.ReadAllText(Path.Combine(ui, "AnalysisPage.razor"));
        Assert.DoesNotContain("Qualifying is grouped", analysis);
        Assert.DoesNotContain("not enabled in this milestone", analysis);

        var monitor = File.ReadAllText(Path.Combine(CompanionRoot(), "src", "iRacingCoach.App", "LiveMonitorWindow.xaml"));
        Assert.DoesNotContain("Text=\"Unavailable\"", monitor);
        var monitorCode = File.ReadAllText(Path.Combine(CompanionRoot(), "src", "iRacingCoach.App", "LiveMonitorWindow.xaml.cs"));
        Assert.DoesNotContain(" : \"—\"", monitorCode);
        Assert.DoesNotContain("Target pace unavailable", File.ReadAllText(Path.Combine(CompanionRoot(), "src", "iRacingCoach.Coordinator", "LiveTelemetry.cs")));

        var settings = File.ReadAllText(Path.Combine(ui, "SettingsPage.razor"));
        foreach (var unfinishedSetting in new[] { "push-to-pass", "weight-jacker", "wet-weather", "multiclass", "global-field", "AI provider", "export format" })
            Assert.IsFalse(settings.Contains(unfinishedSetting, StringComparison.OrdinalIgnoreCase), unfinishedSetting);

        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));
        StringAssert.Contains(css, ".workflow-grid { display: grid; grid-template-columns: repeat(auto-fit");
        StringAssert.Contains(css, ".scenario-grid, .race-corner-grid { display: grid; grid-template-columns: repeat(auto-fit");
        StringAssert.Contains(css, ".race-browser-empty { min-height:");
    }

    [TestMethod]
    public void ProductionUi_UsesSharedIconsHumanCopyAndResponsiveAccessibilityRules()
    {
        var ui = Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI");
        var razor = Directory.EnumerateFiles(ui, "*.razor").ToDictionary(path => Path.GetFileName(path)!, File.ReadAllText);
        var primaryCopy = string.Join("\n", razor.Where(item => item.Key != "DiagnosticsPage.razor").Select(item => item.Value));
        foreach (var technicalTerm in new[] { "MCP", "JSON", "schema", "Codex", "private runtime", "local workspace", "provisional donor", "UI gallery" })
            Assert.IsFalse(primaryCopy.Contains(technicalTerm, StringComparison.OrdinalIgnoreCase), $"Primary UI contains technical term: {technicalTerm}");
        foreach (var glyph in new[] { "⌂", "◉", "⌁", "◇", "↗", "⇄", "⊙", "×", "◎", "◐", "▣", "⋯" })
            Assert.IsFalse(primaryCopy.Contains(glyph, StringComparison.Ordinal), $"Primary UI contains a text glyph used as an icon: {glyph}");

        StringAssert.Contains(razor["NavRail.razor"], "<ProductIcon");
        StringAssert.Contains(razor["TuningPage.razor"], "ProgressiveTuningFeedbackEditor");
        StringAssert.Contains(razor["DiagnosticsPage.razor"], "DiagnosticsExpanded");
        StringAssert.Contains(razor["SettingsPage.razor"], "<details class=\"settings-disclosure\"");

        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));
        StringAssert.Contains(css, "@media (max-width: 1020px)");
        StringAssert.Contains(css, "@media (prefers-reduced-motion: reduce)");
        StringAssert.Contains(css, "@media (forced-colors: active)");
        StringAssert.Contains(css, "@media (prefers-contrast: more)");
        StringAssert.Contains(css, ":focus-visible");

        var theme = File.ReadAllText(Path.Combine(ui, "wwwroot", "theme.generated.css"));
        StringAssert.Contains(theme, "--space-5: 20px");
        StringAssert.Contains(theme, "--space-7: 28px");
    }

    [TestMethod]
    public void PrimaryWorkflows_ContainTheCurrentRaceAnalysisAndTelemetryImplementations()
    {
        var root = CompanionRoot();
        var ui = Path.Combine(root, "src", "iRacingCoach.UI");
        var telemetry = File.ReadAllText(Path.Combine(ui, "TelemetryWorkspace.razor"));
        var incidentPresentation = File.ReadAllText(Path.Combine(ui, "IncidentPresentation.cs"));
        var traceLayouts = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.Coordinator", "AnalysisTraceLayouts.cs"));
        var cursorInterop = File.ReadAllText(Path.Combine(ui, "wwwroot", "analysis-telemetry-cursor.js"));
        StringAssert.Contains(telemetry, "<b>@trace.Lap</b>");
        foreach (var channel in new[] { "Speed", "Time delta", "Throttle", "Brake", "Calculated tire wear", "Gear", "RPM", "Steering", "Slip angle", "Yaw rate", "Lateral G", "Longitudinal G" })
            StringAssert.Contains(traceLayouts, $"\"{channel}\"");
        StringAssert.Contains(traceLayouts, "public const int MaximumRows = 10");
        StringAssert.Contains(traceLayouts, "Row(\"slip\", \"yaw\")");
        StringAssert.Contains(traceLayouts, "Row(\"lateral-g\", \"longitudinal-g\")");
        Assert.DoesNotContain("Throttle / brake", telemetry);
        StringAssert.Contains(telemetry, "CalculatedTireWear.Build");
        StringAssert.Contains(telemetry, "SignalPointValue(signal, trace, point)");
        StringAssert.Contains(telemetry, "SignalRange(signal)");
        StringAssert.Contains(telemetry, "Each line uses its own vertical scale");
        StringAssert.Contains(telemetry, "trace-panel-expanded");
        StringAssert.Contains(telemetry, "View traces full screen");
        StringAssert.Contains(telemetry, "args.Key != \"Escape\"");
        Assert.DoesNotContain("Gear / RPM", telemetry);

        var traceCss = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));
        StringAssert.Contains(traceCss, ".trace-panel.trace-panel-expanded");
        StringAssert.Contains(traceCss, "position: fixed");

        var analysis = File.ReadAllText(Path.Combine(ui, "AnalysisWorkspacePage.razor"));
        var technical = File.ReadAllText(Path.Combine(ui, "RaceTechnicalData.razor"));
        var replay = File.ReadAllText(Path.Combine(ui, "RaceReplayWorkspace.razor"));
        Assert.DoesNotContain("<RaceCardPanel", analysis);
        StringAssert.Contains(analysis, "analysis-one-screen");
        StringAssert.Contains(analysis, "analysis-insight-rail");
        StringAssert.Contains(analysis, "Corner comparison");
        StringAssert.Contains(analysis, "Pit &amp; repairs");
        Assert.DoesNotContain("analysis-tabs", analysis);
        Assert.DoesNotContain("DetailTabs", analysis);
        Assert.DoesNotContain("Relative estimate, not measured wear", analysis);
        StringAssert.Contains(analysis, "analysis-context-bar");
        StringAssert.Contains(analysis, "analysis-focus-strip");
        StringAssert.Contains(analysis, "IsRaceWorkspace");
        StringAssert.Contains(analysis, "SelectedEventSession");
        StringAssert.Contains(analysis, "!session.IsQualifying");
        StringAssert.Contains(analysis, "RaceDataSection");
        StringAssert.Contains(analysis, "analysis-data-switch");
        StringAssert.Contains(analysis, ">Telemetry</button>");
        StringAssert.Contains(analysis, ">Technical data</button>");
        StringAssert.Contains(analysis, ">Race replay</button>");
        Assert.DoesNotContain(">Race review</button>", analysis);
        StringAssert.Contains(analysis, "RaceDataSection == \"telemetry\"");
        StringAssert.Contains(analysis, "RaceDataSection == \"technical\"");
        StringAssert.Contains(analysis, "RaceDataSection == \"replay\"");
        StringAssert.Contains(analysis, "<RaceTechnicalData Workspace=\"Workspace\" Card=\"Card\" />");
        StringAssert.Contains(analysis, "<RaceReplayWorkspace Workspace=\"Workspace\" />");
        StringAssert.Contains(technical, "data-technical-overview");
        StringAssert.Contains(replay, "data-replay-unavailable");
        StringAssert.Contains(analysis, "qualifying-review-shell");
        StringAssert.Contains(analysis, "RaceReviewMetrics.BuildCornerSummaries");
        Assert.DoesNotContain("new TrackSegment", analysis);
        StringAssert.Contains(analysis, "UsefulAction");
        StringAssert.Contains(telemetry, "data-analysis-cursor-layer");
        Assert.DoesNotContain("@onmousemove=\"ChartMoved\"", telemetry);
        StringAssert.Contains(cursorInterop, "requestAnimationFrame");
        StringAssert.Contains(cursorInterop, "analysis-cursor-tooltip-card");
        StringAssert.Contains(cursorInterop, "updateTrack");
        StringAssert.Contains(cursorInterop, "replaceChildren");
        Assert.DoesNotContain("invokeMethodAsync", cursorInterop);
        Assert.DoesNotContain("AnalysisCursorVisibilityChanged", cursorInterop);
        Assert.DoesNotContain("AnalysisCursorMoved", cursorInterop);
        Assert.DoesNotContain("ChartHovered", telemetry);
        StringAssert.Contains(telemetry, "pit-lap-popover");
        StringAssert.Contains(telemetry, "ShowConditionsPopover");
        StringAssert.Contains(telemetry, "lap-conditions-popover");
        StringAssert.Contains(telemetry, "TrackUsage(conditions)");
        StringAssert.Contains(cursorInterop, "projectedTrackFraction");
        StringAssert.Contains(telemetry, "MapPointAt(Cursor)");
        StringAssert.Contains(telemetry, "CleanLapFilterChanged");
        StringAssert.Contains(telemetry, "GreenLapFilterChanged");
        StringAssert.Contains(telemetry, "PassesLapFilters");
        StringAssert.Contains(telemetry, "Selected.RemoveWhere");
        StringAssert.Contains(telemetry, "SectorDuration");
        StringAssert.Contains(telemetry, "session-fastest");
        StringAssert.Contains(telemetry, "$\"S{sector + 1} · {duration.Value:0.000} s\"");
        Assert.DoesNotContain("lap excluded from clean comparison", telemetry);
        Assert.DoesNotContain("new best at this point", telemetry);
        StringAssert.Contains(telemetry, "var lapIncidents = LapIncidents(trace.Lap)");
        StringAssert.Contains(telemetry, "var incidentPoints = lapIncidents.Sum(incident => incident.Points)");
        StringAssert.Contains(telemetry, "@if (lapIncidents.Count > 0)");
        StringAssert.Contains(telemetry, "@($\"x{incidentPoints}\")");
        StringAssert.Contains(telemetry, "aria-controls=\"@IncidentPopoverId(trace.Lap)\"");
        StringAssert.Contains(telemetry, "aria-label=\"@($\"Lap {trace.Lap}, x{incidentPoints} incident details\")\"");
        StringAssert.Contains(telemetry, "@onmouseenter=\"args => ShowIncidentPopover(args, trace.Lap)\"");
        StringAssert.Contains(telemetry, "@onfocus=\"() => ShowIncidentPopoverFromFocus(trace.Lap)\"");
        StringAssert.Contains(telemetry, "class=\"incident-popover\"");
        StringAssert.Contains(telemetry, "style=\"@IncidentPopoverStyle\"");
        StringAssert.Contains(telemetry, "@foreach (var incident in lapIncidents)");
        StringAssert.Contains(telemetry, "IncidentEventDescription(incident, lapIncidents.Count > 1)");
        StringAssert.Contains(telemetry, "@if (!string.IsNullOrWhiteSpace(description))");
        StringAssert.Contains(telemetry, "IncidentPresentation.Describe(incident, includePoints)");
        StringAssert.Contains(incidentPresentation, "incident.EventType");
        StringAssert.Contains(incidentPresentation, "incident.ContactTarget");
        StringAssert.Contains(incidentPresentation, "incident.TrackLocation");
        StringAssert.Contains(incidentPresentation, "IsMeasuredOffTrack(incident.TrackLocation)");
        StringAssert.Contains(incidentPresentation, "string.Equals(Normalize(value), \"off track\"");
        Assert.DoesNotContain("incident.OnPitRoad", telemetry);
        Assert.DoesNotContain("incident.SpeedMph", telemetry);
        Assert.DoesNotContain("incident.YawRateDegreesPerSecond", telemetry);
        Assert.DoesNotContain("incident.SlipAngleDegrees", telemetry);
        Assert.DoesNotContain("IncidentLapPercent", telemetry);
        Assert.DoesNotContain("The recording identifies incident-point changes", telemetry);
        Assert.DoesNotContain("A zero-point contact appears only when the source records an explicit event", telemetry);
        Assert.DoesNotContain("incident.SourceChannel", telemetry,
            "Technical source provenance belongs in diagnostics, not the driving incident popover.");
        Assert.DoesNotContain("incident.SourceChannel", incidentPresentation,
            "Technical source provenance belongs in diagnostics, not the driving incident popover formatter.");
        StringAssert.Contains(traceCss, ".incident-popover {");
        StringAssert.Contains(traceCss, "position: fixed;");
        Assert.DoesNotContain("incidentPoints > 0", telemetry,
            "An explicitly recorded x0 event must remain visible even though an absent event must not be synthesized.");
        StringAssert.Contains(telemetry, "ShowPitPopover");
        StringAssert.Contains(telemetry, "PitPopoverAbove");
        StringAssert.Contains(telemetry, "translateY(-100%)");
        StringAssert.Contains(telemetry, "Recorded repair work");
        StringAssert.Contains(telemetry, "Race remaining");
        StringAssert.Contains(telemetry, "Show all");
        Assert.DoesNotContain("Pit stop recorded", telemetry);
        Assert.DoesNotContain("lap-row-signals", telemetry);
        StringAssert.Contains(telemetry, "class=\"lap-identity\"");
        StringAssert.Contains(telemetry, "class=\"lap-sector-column\"");
        StringAssert.Contains(telemetry, "class=\"lap-pace-value\"");
        StringAssert.Contains(telemetry, "class=\"lap-fuel-column\"");
        StringAssert.Contains(telemetry, "class=\"lap-incident-column\"");
        StringAssert.Contains(telemetry, "class=\"lap-pit-column\"");
        StringAssert.Contains(telemetry, "@(direction == \"in\" ? \"Pit entry\" : \"Pit exit\")");
        StringAssert.Contains(telemetry, "aria-label=\"@($\"Pit {(direction == \"in\" ? \"entry\" : \"exit\")} details\")\"");
        StringAssert.Contains(telemetry, "title=\"@(direction == \"in\" ? \"Pit entry\" : \"Pit exit\")\"");
        Assert.DoesNotContain("Name=\"@(direction == \"in\" ? \"pit-in\" : \"pit-out\")\"", telemetry,
            "Pit direction must be visible text, not an icon-only symbol.");
        StringAssert.Contains(telemetry, "LapFlags(trace)");
        StringAssert.Contains(telemetry, "if (!trace.Complete) return [new LapFlag(\"incomplete\", \"Incomplete lap\")]");
        StringAssert.Contains(telemetry, "states.RemoveAll(value => value.Equals(\"white\"");
        Assert.DoesNotContain("Pit (@direction)", telemetry);
        StringAssert.Contains(telemetry, "aria-haspopup=\"dialog\"");
        StringAssert.Contains(telemetry, "ShowPitPopoverFromFocus");
        StringAssert.Contains(telemetry, "SchedulePitPopoverHide");
        StringAssert.Contains(telemetry, "@onwheel:stopPropagation=\"true\"");
        StringAssert.Contains(telemetry, "trace.Lap <= 0");
        StringAssert.Contains(telemetry, "trace.PitServiceFor(Workspace.Runs, direction)");
        StringAssert.Contains(telemetry, "ClearSelection");
        StringAssert.Contains(telemetry, "No laps selected");
        Assert.DoesNotContain("Selected.Count >= 5", telemetry);
        StringAssert.Contains(telemetry, "TracePalette[index % TracePalette.Length]");
        StringAssert.Contains(telemetry, "private static readonly string[] TracePalette");
        StringAssert.Contains(telemetry, "Math.Abs(lapTime - _fastestLapTime) < .0001");
        StringAssert.Contains(telemetry, "new KeyValuePair<int, string>(lap, \"#F05CDB\")");
        StringAssert.Contains(cursorInterop, "{ passive: false }");
        StringAssert.Contains(telemetry, "TooltipCapacity");
        StringAssert.Contains(telemetry, "Average of selected laps");
        StringAssert.Contains(telemetry, "private AggregateSample AverageAt(double percent)");
        StringAssert.Contains(telemetry, "Average(samples.Select(sample => sample.Point!.SpeedMph))");
        StringAssert.Contains(telemetry, "Average(samples.Select(sample => TimeDelta(sample.Trace, sample.Point!)))");
        Assert.DoesNotContain("<span>Lap @PrimaryTrace.Lap</span>", telemetry);
        StringAssert.Contains(telemetry, "TooltipCharacterWidth = 7.1");
        StringAssert.Contains(cursorInterop, "widest * state.config.tooltipCharacterWidth");
        Assert.DoesNotContain(">Lap @trace.value.Lap</svg:text>", telemetry);
        StringAssert.Contains(cursorInterop, "leftCandidate >= plotStart");
        Assert.DoesNotContain("getComputedTextLength", cursorInterop,
            "Cursor frames must not force synchronous SVG text layout.");
        StringAssert.Contains(cursorInterop, "setTextIfChanged(slot.value, formatted[slotIndex])");
        StringAssert.Contains(cursorInterop, "availableTooltipWidth");
        StringAssert.Contains(telemetry, "private const double TraceRowsHeight = 820");
        StringAssert.Contains(telemetry, "TraceRowsHeight / Math.Max(1, TraceRows.Count)");
        StringAssert.Contains(telemetry, "iracingCoach.elementSize");
        StringAssert.Contains(telemetry, "analysis-trace-catalog");
        StringAssert.Contains(telemetry, "data-analysis-drag-signal");
        StringAssert.Contains(telemetry, "data-analysis-drag-row");
        StringAssert.Contains(telemetry, "PlaceTraceSignal");
        Assert.DoesNotContain("draggable=\"true\"", telemetry);
        Assert.DoesNotContain("@ondrop", telemetry);
        Assert.DoesNotContain("Other laps", telemetry);
        Assert.DoesNotContain("lap-focus-action", telemetry);

        var coachCss = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));
        StringAssert.Contains(coachCss, "--font-size-min: 11.67px");
        StringAssert.Contains(coachCss, ".pit-lap-popover { position: fixed");
        StringAssert.Contains(coachCss, "pointer-events: auto;");
        StringAssert.Contains(coachCss, ".lap-select-button {");
        StringAssert.Contains(coachCss, ".lap-conditions-popover { position: fixed");
        StringAssert.Contains(coachCss, ".conditions-grid { display: grid; grid-template-columns: repeat(3");
        StringAssert.Contains(coachCss, "grid-template-columns: 50px 42px minmax(80px,1fr) 34px 28px 64px 64px;");
        StringAssert.Contains(coachCss, ".race-workstation .lap-incident-column { grid-column: 5; grid-row: 1; }");
        StringAssert.Contains(coachCss, ".race-workstation .lap-pit-column { grid-column: 7; grid-row: 1;");
        StringAssert.Contains(coachCss, "justify-content: center; gap: 0 5px;");
        StringAssert.Contains(coachCss, ".pit-badge {");
        StringAssert.Contains(coachCss, "width: auto;");
        StringAssert.Contains(coachCss, "white-space: nowrap;");
        StringAssert.Contains(coachCss, "grid-template-columns: minmax(0,1fr); justify-self: end;");
        StringAssert.Contains(coachCss, ".lap-time small { display: block; width: 100%;");
        StringAssert.Contains(coachCss, ".lap-time { display: flex; flex-direction: column; align-items: flex-end; justify-self: center; }");
        StringAssert.Contains(coachCss, ".lap-pace-value,.lap-time small { width: max-content; transform: none; }");
        StringAssert.Contains(coachCss, "grid-template-columns: clamp(400px,29vw,500px) minmax(0,1fr);");
        StringAssert.Contains(coachCss, "align-self: stretch;");
        StringAssert.Contains(coachCss, ".race-workstation .telemetry-context-column {");
        StringAssert.Contains(coachCss, ".race-workstation.context-track .telemetry-context-column");
        StringAssert.Contains(coachCss, ".race-workstation.context-laps .telemetry-context-column");
        StringAssert.Contains(coachCss, ".race-workstation.context-none { grid-template-columns: 0 minmax(0,1fr); gap: 0; }");
        StringAssert.Contains(coachCss, ".race-telemetry-page { min-width: 0; container-type: inline-size; }");
        StringAssert.Contains(coachCss, "@container (max-width: 1060px)");
        StringAssert.Contains(coachCss, ".analysis-page-frame:has(.race-analysis-toolbar) {");
        StringAssert.Contains(coachCss, "height: calc(100dvh - var(--command-bar-height));");
        StringAssert.Contains(coachCss, "@container (max-width: 960px)");
        StringAssert.Contains(coachCss, "grid-template-columns: 48px 38px minmax(70px,1fr) 34px 58px");
        StringAssert.Contains(coachCss, "@container (max-width: 760px)");
        StringAssert.Contains(coachCss, "grid-template-columns: minmax(150px,29%) minmax(400px,1fr)");
        StringAssert.Contains(coachCss, "@container (max-width: 600px)");
        Assert.DoesNotContain("zoom:", coachCss, "Responsive analysis must reflow without shrinking text below the app-wide minimum.");
        StringAssert.Contains(coachCss, ".lap-flag.checkered");
        StringAssert.Contains(coachCss, ".sector-square { box-sizing: border-box; flex: 0 0 8px;");
        StringAssert.Contains(coachCss, ".sector-square.unavailable { background: #4E5660; border: 0;");
        StringAssert.Contains(coachCss, ".lap-fuel-column { color: var(--text-primary);");
        Assert.DoesNotContain(".lap-time.condition-hover:hover", coachCss);
        var unavailableSectorRule = Regex.Match(coachCss, @"\.sector-square\.unavailable\s*\{(?<body>[^}]*)\}");
        Assert.IsTrue(unavailableSectorRule.Success);
        Assert.DoesNotContain("border-style: dashed", unavailableSectorRule.Groups["body"].Value);
        StringAssert.Contains(coachCss, ".lap-rail-scroll { overflow-x: hidden; overflow-y: auto;");
        StringAssert.Contains(coachCss, "justify-content: flex-end; gap: 2px;");
        Assert.DoesNotContain(".lap-flags .lap-flag + .lap-flag", coachCss);
        StringAssert.Contains(coachCss, ".telemetry-empty-selection");
        Assert.IsFalse(System.Text.RegularExpressions.Regex.IsMatch(coachCss, "font-size:\\s*(9|10)px"));
        Assert.DoesNotContain("<span class=\"lap-state", telemetry);
        StringAssert.Contains(telemetry, "stroke-width=\"@traceGroup.StrokeWidth\"");
        StringAssert.Contains(telemetry, "vector-effect=\"non-scaling-stroke\"");
        StringAssert.Contains(telemetry, "return signalIndex == 0 ? \"1.35\" : \"1.05\";");
        StringAssert.Contains(telemetry, "140 + (point.Y - projection.CenterY) * projection.Scale");
        StringAssert.Contains(telemetry, "HeatmapColor(normalized, SpeedHeatmapStops)");
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));
        StringAssert.Contains(css, ".event-session-switch .segmented");
        StringAssert.Contains(css, ".analysis-data-switch");
        StringAssert.Contains(css, ".technical-overview");
        StringAssert.Contains(css, ".race-replay-workspace");
        StringAssert.Contains(css, ".qualifying-review-shell { display: contents; }");
        StringAssert.Contains(css, ".lap-time.fastest");
        StringAssert.Contains(css, "white-space: nowrap");

        var appHost = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.App", "wwwroot", "index.html"));
        StringAssert.Contains(appHost, "getScreenCTM");
        StringAssert.Contains(appHost, "viewportSize");
        StringAssert.Contains(appHost, "elementSize");

        var navigation = File.ReadAllText(Path.Combine(ui, "NavRail.razor"));
        StringAssert.Contains(navigation, "\"Setups\"");

        var tuning = File.ReadAllText(Path.Combine(ui, "TuningPage.razor"));
        StringAssert.Contains(tuning, "TuningTrackSelector");
        StringAssert.Contains(tuning, "State.TuningDraft.Feedback");
        Assert.IsTrue(File.Exists(Path.Combine(ui, "TuningTrackSelector.razor")));

        var live = File.ReadAllText(Path.Combine(ui, "LiveTelemetryPage.razor"));
        StringAssert.Contains(live, "LiveTelemetryLayoutGrid");
        var liveVisuals = File.ReadAllText(Path.Combine(ui, "LiveTelemetryVisuals.razor"));
        StringAssert.Contains(liveVisuals, "<canvas");
        StringAssert.Contains(liveVisuals, "LiveTelemetryFrame");
        StringAssert.Contains(liveVisuals, "data-source-rate");
        StringAssert.Contains(liveVisuals, "sourceRate = State.LiveState.SourceTickRate");
        Assert.DoesNotContain("Hz source", liveVisuals);
        StringAssert.Contains(liveVisuals, "Snapshot.LapDistancePercent is { } lapDistance");
        StringAssert.Contains(liveVisuals, "Lap position unavailable");
        Assert.DoesNotContain("Snapshot.LapDistancePercent ?? 0", liveVisuals, StringComparison.Ordinal);
        Assert.DoesNotContain("* 100).ToString(\"0\")%", liveVisuals);
        Assert.DoesNotContain("<svg class=\"live-trend-chart\"", liveVisuals);
        var liveChart = File.ReadAllText(Path.Combine(ui, "wwwroot", "live-telemetry-chart.js"));
        StringAssert.Contains(liveChart, "requestAnimationFrame");
        StringAssert.Contains(liveChart, "Float64Array");
        StringAssert.Contains(liveChart, "minima");
        StringAssert.Contains(liveChart, "maxima");
        var liveGrid = File.ReadAllText(Path.Combine(ui, "LiveTelemetryLayoutGrid.razor"));
        StringAssert.Contains(liveGrid, "LiveMonitorLayouts.Active");
        StringAssert.Contains(liveGrid, "live-toolbox");
        StringAssert.Contains(liveGrid, "toolbox-metric-catalog");
        StringAssert.Contains(liveGrid, "reading.NumericValue.HasValue");
        Assert.DoesNotContain("\"connections\", \"Connections\"", navigation);
        StringAssert.Contains(File.ReadAllText(Path.Combine(ui, "SettingsPage.razor")), "<ConnectionsPage State=\"State\" Embedded=\"true\"");
        var home = File.ReadAllText(Path.Combine(ui, "HomePage.razor"));
        StringAssert.Contains(home, "Find a starting tune");
        StringAssert.Contains(home, "home-race-row");
        StringAssert.Contains(home, "TireMetric");
        Assert.DoesNotContain("Automatic discovery", home);
        Assert.DoesNotContain("Open Race Analysis", home);
        Assert.DoesNotContain("Browse setups", home);
        var monitor = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.App", "LiveMonitorWindow.xaml"));
        StringAssert.Contains(monitor, "ShowInTaskbar=\"True\"");

        var installer = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.Installer", "Program.cs"));
        StringAssert.Contains(installer, "SpecialFolder.LocalApplicationData");
        StringAssert.Contains(installer, "argument.Equals(\"/S\"");
        StringAssert.Contains(installer, "Registry.CurrentUser.CreateSubKey");
        Assert.DoesNotContain("Verb = \"runas\"", installer);
    }

    [TestMethod]
    public void PointerActivatedControls_ReleaseTransientFocusWithoutBreakingKeyboardFocus()
    {
        var source = File.ReadAllText(Path.Combine(RepositoryRoot(), "companion-app", "src", "iRacingCoach.UI", "wwwroot", "interaction-policy.js"));
        var nativeHost = File.ReadAllText(Path.Combine(RepositoryRoot(), "companion-app", "src", "iRacingCoach.App", "wwwroot", "index.html"));
        var previewHost = File.ReadAllText(Path.Combine(RepositoryRoot(), "companion-app", "src", "iRacingCoach.Preview", "Components", "App.razor"));

        Assert.Contains("document.addEventListener(\"pointerdown\"", source, StringComparison.Ordinal);
        Assert.Contains("document.addEventListener(\"pointerup\"", source, StringComparison.Ordinal);
        Assert.Contains("document.addEventListener(\"change\"", source, StringComparison.Ordinal);
        Assert.Contains("control instanceof HTMLSelectElement", source, StringComparison.Ordinal);
        Assert.Contains("const pointerSelectOrigins = new WeakSet();", source, StringComparison.Ordinal);
        Assert.Contains("const pointerSelectAt = new WeakMap();", source, StringComparison.Ordinal);
        Assert.Contains("pointerSelectOrigins.has(control)", source, StringComparison.Ordinal);
        Assert.Contains("pointerSelectAt.get(control)", source, StringComparison.Ordinal);
        Assert.DoesNotContain("lastPointerAt", source, StringComparison.Ordinal);
        Assert.Contains("button,[role='button'],[role='tab']", source, StringComparison.Ordinal);
        Assert.Contains("document.activeElement === control", source, StringComparison.Ordinal);
        Assert.Contains("control.blur()", source, StringComparison.Ordinal);
        Assert.DoesNotContain("keydown", source, StringComparison.Ordinal);
        Assert.Contains("interaction-policy.js", nativeHost, StringComparison.Ordinal);
        Assert.Contains("interaction-policy.js", previewHost, StringComparison.Ordinal);
    }

    [TestMethod]
    public void HomeWorkflowCards_HighlightWithoutMovingOnHover()
    {
        var css = File.ReadAllText(Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI", "wwwroot", "coach.css"));
        var hoverRule = css.Split(".workflow-card:hover", StringSplitOptions.None)[1]
            .Split('}', 2, StringSplitOptions.None)[0];

        Assert.Contains("border-color:", hoverRule, StringComparison.Ordinal);
        Assert.DoesNotContain("transform:", hoverRule, StringComparison.Ordinal);
    }

    [TestMethod]
    public void InteractiveAriaStates_RenderExplicitLowercaseBooleans()
    {
        var ui = Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI");
        var live = File.ReadAllText(Path.Combine(ui, "LiveTelemetryPage.razor"));
        var settings = File.ReadAllText(Path.Combine(ui, "SettingsPage.razor"));

        Assert.Contains("aria-expanded=\"@(_tracesExpanded ? \"true\" : \"false\")\"", live, StringComparison.Ordinal);
        Assert.Contains("aria-hidden=\"@(_tracesExpanded ? \"false\" : \"true\")\"", live, StringComparison.Ordinal);
        Assert.Contains("aria-pressed=\"@(selected ? \"true\" : \"false\")\"", settings, StringComparison.Ordinal);
        Assert.Contains("aria-expanded=\"@(_connectionsOpen ? \"true\" : \"false\")\"", settings, StringComparison.Ordinal);
    }

    [TestMethod]
    public void AppStartup_DoesNotBlockTheInteractiveCircuitWhileCatalogingLocalData()
    {
        var shell = File.ReadAllText(Path.Combine(RepositoryRoot(), "companion-app", "src", "iRacingCoach.UI", "CompanionShell.razor"));

        Assert.Contains("if (firstRender) _ = Task.Run(InitializeStateAsync);", shell, StringComparison.Ordinal);
        Assert.Contains("await State.InitializeAsync();", shell, StringComparison.Ordinal);
        Assert.Contains("State.ReportUnhandledException(\"app startup\", error);", shell, StringComparison.Ordinal);
        Assert.DoesNotContain("if (firstRender) await State.InitializeAsync();", shell, StringComparison.Ordinal);
    }

    [TestMethod]
    public void NativeDisclosurePanels_UseTheSharedStructuralMotionToken()
    {
        var css = File.ReadAllText(Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI", "wwwroot", "coach.css"));

        StringAssert.Contains(css, "html { interpolate-size: allow-keywords; }");
        StringAssert.Contains(css, "details::details-content");
        StringAssert.Contains(css, "block-size var(--motion-structure) var(--ease)");
        StringAssert.Contains(css, "details[open]::details-content { block-size: auto; opacity: 1; }");
    }

    [TestMethod]
    public void LiveDrivingTraceCanvas_CachesHistoryAndStopsRenderingWhileHidden()
    {
        var ui = Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI");
        var liveChart = File.ReadAllText(Path.Combine(ui, "wwwroot", "live-telemetry-chart.js"));
        var liveVisuals = File.ReadAllText(Path.Combine(ui, "LiveTelemetryVisuals.razor"));

        StringAssert.Contains(liveChart, "requestAnimationFrame");
        StringAssert.Contains(liveChart, "lowerBound");
        StringAssert.Contains(liveChart, "dataDirty");
        StringAssert.Contains(liveChart, "gapThreshold");
        StringAssert.Contains(liveChart, "cache.segments[bucket] !== previousSegment");
        StringAssert.Contains(liveChart, "state.disclosure && !state.disclosure.open");
        StringAssert.Contains(liveChart, "IntersectionObserver");
        StringAssert.Contains(liveChart, "stopRendering");
        Assert.DoesNotContain("state.points.filter", liveChart);
        Assert.DoesNotContain("state.animationFrame = requestAnimationFrame(next => draw", liveChart);
        StringAssert.Contains(liveVisuals, "InvokeAsync<bool>(\"iracingCoachLiveTelemetryChart.append\"");
        StringAssert.Contains(liveVisuals, "if (chartVisible && Stopwatch.GetElapsedTime");
    }

    [TestMethod]
    public void TrayExit_ArmsAnUncancellableHardDeadlineBeforeCleanup()
    {
        var appRoot = Path.Combine(CompanionRoot(), "src", "iRacingCoach.App");
        var window = File.ReadAllText(Path.Combine(appRoot, "MainWindow.xaml.cs"));
        var requestStart = window.IndexOf("private void RequestExit()", StringComparison.Ordinal);
        var disposeStart = window.IndexOf("public void DisposeApplication()", StringComparison.Ordinal);
        var request = window[requestStart..disposeStart];
        StringAssert.Contains(request, "App.ArmExitDeadline();");
        StringAssert.Contains(request, "if (!Dispatcher.CheckAccess())");
        StringAssert.Contains(request, "new Action(RequestExit)");
        StringAssert.Contains(request, "if (_exitRequested) return;");
        StringAssert.Contains(request, "ExitRequested?.Invoke();");
        Assert.DoesNotContain("HideForApplicationExit();", request);
        StringAssert.Contains(request, "Dispatcher.BeginInvoke(DispatcherPriority.Send");

        var disposal = window[disposeStart..window.IndexOf("private void ApplyDarkTitleBar()", disposeStart, StringComparison.Ordinal)];
        StringAssert.Contains(disposal, "HideForApplicationExit();");
        StringAssert.Contains(disposal, "TryCleanup(Close, \"close main window\")");
        StringAssert.Contains(disposal, "TryCleanup(_state.Dispose, \"stop application services\")");
        Assert.IsLessThan(disposal.IndexOf("TryCleanup(_state.Dispose", StringComparison.Ordinal), disposal.IndexOf("TryCleanup(Close", StringComparison.Ordinal));

        var application = File.ReadAllText(Path.Combine(appRoot, "App.xaml.cs"));
        StringAssert.Contains(application, "Interlocked.Exchange(ref _exitStarted, 1)");
        StringAssert.Contains(application, "TimeSpan.FromSeconds(5)");
        StringAssert.Contains(application, "Shutdown(0)");
        StringAssert.Contains(application, "static _ => ForceTerminateProcess()");
        StringAssert.Contains(application, "current.Kill(entireProcessTree: true)");
        StringAssert.Contains(application, "current.Kill();");
        StringAssert.Contains(application, "if (Volatile.Read(ref _exitDeadlineArmed) == 0) _exitWatchdog?.Dispose()");
        Assert.HasCount(2, System.Text.RegularExpressions.Regex.Matches(application, "Environment\\.Exit\\(0\\)"));
        StringAssert.Contains(application, "finally");
        var exitStart = application.IndexOf("private void ExitApplication()", StringComparison.Ordinal);
        var onExitStart = application.IndexOf("protected override void OnExit", exitStart, StringComparison.Ordinal);
        var exitMethod = application[exitStart..onExitStart];
        Assert.IsLessThan(exitMethod.IndexOf("_mainWindow?.DisposeApplication()", StringComparison.Ordinal), exitMethod.IndexOf("ArmExitDeadline();", StringComparison.Ordinal));
        Assert.IsLessThan(request.IndexOf("if (!Dispatcher.CheckAccess())", StringComparison.Ordinal), request.IndexOf("App.ArmExitDeadline();", StringComparison.Ordinal));
        var onExitEnd = application.IndexOf("private static void ForceTerminateProcess()", onExitStart, StringComparison.Ordinal);
        var onExitMethod = application[onExitStart..onExitEnd];
        Assert.DoesNotContain("\n            _exitWatchdog?.Dispose();", onExitMethod);
    }

    [TestMethod]
    public void ReleaseBuild_RefusesMixedApplicationInstallerAndUninstallerVersions()
    {
        var script = File.ReadAllText(Path.Combine(CompanionRoot(), "tools", "BuildRelease.ps1"));

        StringAssert.Contains(script, "function Get-ProjectVersion");
        StringAssert.Contains(script, "iRacingCoach.App.csproj");
        StringAssert.Contains(script, "iRacingCoach.Installer.csproj");
        StringAssert.Contains(script, "iRacingCoach.Uninstaller.csproj");
        StringAssert.Contains(script, "iRacingCoach.Installer\\Program.cs");
        StringAssert.Contains(script, "iRacingCoach.Coordinator\\CompanionState.cs");
        StringAssert.Contains(script, "iRacingCoach.Coordinator\\CoachEngine.cs");
        StringAssert.Contains(script, "iRacingCoach.Contracts\\Models.cs");
        StringAssert.Contains(script, "$sourceIdentityMismatches");
        StringAssert.Contains(script, "status --porcelain --untracked-files=all");
        StringAssert.Contains(script, "Release source is not clean.");
        StringAssert.Contains(script, "$releaseCommit");
        StringAssert.Contains(script, "sourceCommit = $releaseCommit");
        StringAssert.Contains(script, "Release identity mismatch.");
        StringAssert.Contains(script, "[switch]$IncludePortable");
        StringAssert.Contains(script, "if ($IncludePortable)");
        var mismatchGuard = script.IndexOf("Release identity mismatch.", StringComparison.Ordinal);
        var destructiveReset = script.IndexOf("Reset-ReleaseDirectory $artifactRoot", StringComparison.Ordinal);
        Assert.IsTrue(mismatchGuard >= 0 && destructiveReset >= 0 && mismatchGuard < destructiveReset,
            "A mixed-version release must fail before any prior release output is reset or rewritten.");
    }

    [TestMethod]
    public void ReleaseIdentity_IsSynchronizedAcrossPackagingAndRepairSurfaces()
    {
        const string version = "0.16.0";
        var root = CompanionRoot();
        var releaseScript = File.ReadAllText(Path.Combine(root, "tools", "BuildRelease.ps1"));
        var appProject = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.App", "iRacingCoach.App.csproj"));
        var installerProject = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.Installer", "iRacingCoach.Installer.csproj"));
        var uninstallerProject = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.Uninstaller", "iRacingCoach.Uninstaller.csproj"));
        var installer = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.Installer", "Program.cs"));
        var coordinator = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.Coordinator", "CompanionState.cs"));
        var coachEngine = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.Coordinator", "CoachEngine.cs"));
        var contracts = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.Contracts", "Models.cs"));
        var appHost = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.App", "wwwroot", "index.html"));

        StringAssert.Contains(releaseScript, $"$version = '{version}'");
        StringAssert.Contains(appProject, $"<Version>{version}</Version>");
        StringAssert.Contains(installerProject, $"<Version>{version}</Version>");
        StringAssert.Contains(uninstallerProject, $"<Version>{version}</Version>");
        StringAssert.Contains(installer, $"internal const string ProductVersion = \"{version}\";");
        StringAssert.Contains(installer, "$\"iRacingCoach-{ProductVersion}-Setup.exe\"");
        StringAssert.Contains(installer, "key.SetValue(\"DisplayVersion\", Program.ProductVersion)");
        StringAssert.Contains(installer, "Program.RepairInstallerFileName");
        StringAssert.Contains(coordinator, $"private const string AppVersion = \"{version}\";");
        StringAssert.Contains(coordinator, "$\"iRacingCoach-{AppVersion}-Setup.exe\"");
        StringAssert.Contains(coachEngine, $"version = \"{version}\"");
        StringAssert.Contains(contracts, $"string ClientVersion = \"{version}\"");
        StringAssert.Contains(appHost, $"coach.css?v={version}");
        StringAssert.Contains(appHost, $"analysis-telemetry-cursor.js?v={version}-bounded-cursor");
        StringAssert.Contains(appHost, $"live-telemetry-layout.js?v={version}-viewport-fit");

        foreach (var liveIdentitySource in new[]
        {
            releaseScript,
            appProject,
            installerProject,
            uninstallerProject,
            installer,
            coordinator,
            coachEngine,
            contracts
        })
        {
            Assert.DoesNotContain("0.14.2", liveIdentitySource,
                "Historical release identities must not remain in active packaging or repair surfaces.");
            Assert.DoesNotContain("0.15.0", liveIdentitySource,
                "The prior development candidate must not remain in active 0.16.0 packaging or repair surfaces.");
        }
    }

    [TestMethod]
    public void AiSchema_AllowsContextualSectionsAndForbidsMissingEvidenceClass()
    {
        using var schema = JsonDocument.Parse(File.ReadAllText(Path.Combine(RepositoryRoot(), "contracts", "ai-coaching-output.schema.json")));
        var required = schema.RootElement.GetProperty("required").EnumerateArray().Select(item => item.GetString()).ToArray();
        CollectionAssert.AreEquivalent(new[] { "summary", "actions", "followUpNeeded" }, required);
        var classes = schema.RootElement.GetProperty("$defs").GetProperty("evidenceClass").GetProperty("enum").EnumerateArray().Select(item => item.GetString()).ToArray();
        CollectionAssert.DoesNotContain(classes, "unavailable");
    }

    [TestMethod]
    public void RaceAnalysis_DefaultSelectionAndViewportWidthRemainStable()
    {
        var root = CompanionRoot();
        var ui = Path.Combine(root, "src", "iRacingCoach.UI");
        var telemetry = File.ReadAllText(Path.Combine(ui, "TelemetryWorkspace.razor"));
        var raceBrowser = File.ReadAllText(Path.Combine(ui, "AnalysisPage.razor"));
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));

        StringAssert.Contains(telemetry, "Workspace.Traces.Where(IsClean).Where(HasUsableLapTime).OrderBy(trace => trace.LapTimeSeconds!.Value).ThenBy(trace => trace.Lap).Take(3)");
        StringAssert.Contains(telemetry, "trace.LapTimeSeconds is { } value && double.IsFinite(value) && value > 0");
        Assert.DoesNotContain("trace.IsComparable() && IncidentPoints(trace.Lap) == 0", telemetry,
            "The visual Best three selection must not inherit stricter coaching-reference exclusions.");

        var workspaceRule = Regex.Match(css, @"\.workspace\s*\{(?<body>[^}]*)\}");
        Assert.IsTrue(workspaceRule.Success);
        StringAssert.Contains(workspaceRule.Groups["body"].Value, "scrollbar-gutter: stable");

        StringAssert.Contains(raceBrowser, "event-race-shape race-condition-stat");
        StringAssert.Contains(raceBrowser, "metric-dot green");
        StringAssert.Contains(raceBrowser, "metric-dot yellow");
        StringAssert.Contains(css, ".race-analysis-row .event-race-shape strong { color: var(--text-primary); }");
        StringAssert.Contains(css, ".race-analysis-row .event-race-shape small { color: var(--text-muted); }");
    }

    private static CapabilityContext FullyPopulated() => new()
    {
        HasRaceRecordings = true, HasOpenAnalyzedRace = true, HasSetupFiles = true, HasMissingRawTelemetry = true,
        HasComparableSetups = true, HasTrackView = true,
        LiveConnected = true, HasLeaderGap = true, HasAheadGap = true, HasBehindGap = true, HasPaceRange = true,
        HasPitWindow = true, HasFuelLimit = true, HasLastLap = true, HasLeaderLastLap = true, HasTirePhase = true,
        HasWeather = true, HasBrakeBias = true, HasRepair = true, HasOfficialEvents = true, HasHostedLeagueEvents = true,
        HasAiEvents = true, HasFixedEvents = true, HasOpenEvents = true, HasAnalyzedEvents = true, HasUnanalyzedEvents = true,
        CoachEngineInstalled = true, CoachEngineRunning = true, ChatGptConnected = true, Garage61Configured = true, Garage61Available = true
    };

    private static string CompanionRoot() => TestRepositoryPaths.CompanionAppRoot;
    private static string RepositoryRoot() => TestRepositoryPaths.RepositoryRoot;
}
