using System.Runtime.CompilerServices;
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
        StringAssert.Contains(razor["TuningPage.razor"], "feedback-builder");
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
    public void Corrective092_PrimaryWorkflowsContainTheQaRequiredImplementations()
    {
        var root = CompanionRoot();
        var ui = Path.Combine(root, "src", "iRacingCoach.UI");
        var telemetry = File.ReadAllText(Path.Combine(ui, "TelemetryWorkspace.razor"));
        StringAssert.Contains(telemetry, "<b>@trace.Lap</b>");
        foreach (var channel in new[] { "Speed", "Time Delta", "Throttle / brake", "Gear", "RPM", "Steering", "Slip / yaw", "Lateral / long. G" })
            StringAssert.Contains(telemetry, $"\"{channel}\"");
        StringAssert.Contains(telemetry, "PanelRange(panel)");
        Assert.DoesNotContain("Gear / RPM", telemetry);

        var analysis = File.ReadAllText(Path.Combine(ui, "AnalysisWorkspacePage.razor"));
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
        StringAssert.Contains(analysis, ">Race review</button>");
        StringAssert.Contains(analysis, "RaceDataSection == \"telemetry\"");
        StringAssert.Contains(analysis, "RaceDataSection == \"review\"");
        StringAssert.Contains(analysis, "analysis-review-grid");
        StringAssert.Contains(analysis, "qualifying-review-shell");
        StringAssert.Contains(analysis, "BuildCornerAreas");
        Assert.DoesNotContain("new TrackSegment", analysis);
        StringAssert.Contains(analysis, "UsefulAction");
        StringAssert.Contains(telemetry, "trace-cursor-tooltip");
        StringAssert.Contains(telemetry, "@onmouseleave=\"HideChartCursor\"");
        StringAssert.Contains(telemetry, "@if (ChartHovered)");
        StringAssert.Contains(telemetry, "DirectionalDegrees");
        StringAssert.Contains(telemetry, "pit-lap-popover");
        StringAssert.Contains(telemetry, "ShowConditionsPopover");
        StringAssert.Contains(telemetry, "lap-conditions-popover");
        StringAssert.Contains(telemetry, "TrackUsage(conditions)");
        StringAssert.Contains(telemetry, "ProjectedMapPercent");
        StringAssert.Contains(telemetry, "MapPointAt(Cursor)");
        StringAssert.Contains(telemetry, "CleanLapFilterChanged");
        StringAssert.Contains(telemetry, "Selected.RemoveWhere");
        StringAssert.Contains(telemetry, "SectorDuration");
        StringAssert.Contains(telemetry, "session-fastest");
        StringAssert.Contains(telemetry, "$\"S{sector + 1} · {duration.Value:0.000} s\"");
        Assert.DoesNotContain("lap excluded from clean comparison", telemetry);
        Assert.DoesNotContain("new best at this point", telemetry);
        StringAssert.Contains(telemetry, "IncidentPoints");
        StringAssert.Contains(telemetry, "@($\"x{points}\")");
        StringAssert.Contains(telemetry, "ShowPitPopover");
        StringAssert.Contains(telemetry, "PitPopoverAbove");
        StringAssert.Contains(telemetry, "translateY(-100%)");
        StringAssert.Contains(telemetry, "Damage repaired");
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
        StringAssert.Contains(telemetry, "Pit (@direction)");
        StringAssert.Contains(telemetry, "LapFlags(trace)");
        StringAssert.Contains(telemetry, "if (!trace.Complete) return [new LapFlag(\"incomplete\", \"Incomplete lap\")]");
        StringAssert.Contains(telemetry, "states.RemoveAll(value => value.Equals(\"white\"");
        StringAssert.Contains(telemetry, ">Pit (@direction)</span>");
        StringAssert.Contains(telemetry, "trace.Lap == ordered[0].Lap");
        StringAssert.Contains(telemetry, "ClearSelection");
        StringAssert.Contains(telemetry, "No laps selected");
        Assert.DoesNotContain("Selected.Count >= 5", telemetry);
        StringAssert.Contains(telemetry, "var hue = 280 * fraction");
        StringAssert.Contains(telemetry, "Math.Abs(lapTime - FastestLapTime) < .0001");
        StringAssert.Contains(telemetry, "return \"#F05CDB\"");
        StringAssert.Contains(telemetry, "@onwheel=\"ChartWheel\"");
        StringAssert.Contains(telemetry, "@onwheel:preventDefault");
        StringAssert.Contains(telemetry, "TooltipCapacity");
        StringAssert.Contains(telemetry, "@SelectedAverageLabel");
        StringAssert.Contains(telemetry, "private AggregateSample AverageAt(double percent)");
        StringAssert.Contains(telemetry, "Average(samples.Select(sample => sample.Point!.SpeedMph))");
        StringAssert.Contains(telemetry, "Average(samples.Select(sample => TimeDelta(sample.Trace, sample.Point!)))");
        Assert.DoesNotContain("<span>Lap @PrimaryTrace.Lap</span>", telemetry);
        StringAssert.Contains(telemetry, "TooltipCharacterWidth = 7.1");
        StringAssert.Contains(telemetry, "private double TooltipWidth(PanelSpec panel)");
        StringAssert.Contains(telemetry, "widestValue * TooltipCharacterWidth");
        Assert.DoesNotContain(">Lap @trace.value.Lap</svg:text>", telemetry);
        StringAssert.Contains(telemetry, "CursorChartX - width - 12 >= PlotLeft");
        StringAssert.Contains(telemetry, "private const int ChartRowHeight = 82");
        StringAssert.Contains(telemetry, "iracingCoach.elementSize");
        Assert.DoesNotContain("Other laps", telemetry);
        Assert.DoesNotContain("lap-focus-action", telemetry);

        var coachCss = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));
        StringAssert.Contains(coachCss, "--font-size-min: 11.67px");
        StringAssert.Contains(coachCss, ".pit-lap-popover { position: fixed");
        StringAssert.Contains(coachCss, ".lap-conditions-popover { position: fixed");
        StringAssert.Contains(coachCss, ".conditions-grid { display: grid; grid-template-columns: repeat(3");
        StringAssert.Contains(coachCss, "grid-template-columns: 70px 42px 90px 34px 64px 30px 72px");
        StringAssert.Contains(coachCss, "grid-template-columns: 64px 36px 84px 32px 58px 26px 66px");
        StringAssert.Contains(coachCss, "justify-content: center; gap: 0 5px;");
        StringAssert.Contains(coachCss, ".pit-badge { box-sizing: border-box; max-width: 100%; white-space: nowrap; }");
        StringAssert.Contains(coachCss, "grid-template-columns: 474px minmax(0,1fr)");
        StringAssert.Contains(coachCss, ".telemetry-workstation-grid > .telemetry-grid { align-self: start; }");
        StringAssert.Contains(coachCss, ".lap-rail { align-self: stretch; height: auto; min-height: 0; max-height: none; contain: size; }");
        Assert.DoesNotContain(".lap-rail { align-self: stretch; height: 100%", coachCss);
        StringAssert.Contains(coachCss, ".race-telemetry-page { min-width: 0; container-type: inline-size; }");
        StringAssert.Contains(coachCss, "@container (max-width: 1280px)");
        StringAssert.Contains(coachCss, "grid-template-columns: minmax(424px,38%) minmax(0,1fr)");
        StringAssert.Contains(coachCss, "@container (max-width: 960px)");
        StringAssert.Contains(coachCss, "width: 100%; grid-template-columns: minmax(424px,38%) minmax(0,1fr); zoom: .75;");
        StringAssert.Contains(coachCss, "@container (max-width: 760px)");
        StringAssert.Contains(coachCss, "width: 100%; zoom: .65;");
        StringAssert.Contains(coachCss, ".lap-flag.checkered");
        StringAssert.Contains(coachCss, ".sector-square { box-sizing: border-box; flex: 0 0 8px;");
        StringAssert.Contains(coachCss, ".sector-square.unavailable { background: #4E5660; border: 0;");
        StringAssert.Contains(coachCss, ".lap-fuel-column { color: var(--text-primary);");
        Assert.DoesNotContain(".lap-time.condition-hover:hover", coachCss);
        Assert.DoesNotContain("border-style: dashed", coachCss[coachCss.IndexOf(".sector-square.unavailable", StringComparison.Ordinal)..]);
        StringAssert.Contains(coachCss, ".lap-rail-scroll { overflow-x: hidden; overflow-y: auto;");
        StringAssert.Contains(coachCss, "justify-content: flex-end; gap: 2px;");
        Assert.DoesNotContain(".lap-flags .lap-flag + .lap-flag", coachCss);
        StringAssert.Contains(coachCss, ".telemetry-empty-selection");
        Assert.IsFalse(System.Text.RegularExpressions.Regex.IsMatch(coachCss, "font-size:\\s*(9|10)px"));
        Assert.DoesNotContain("<span class=\"lap-state", telemetry);
        StringAssert.Contains(telemetry, "stroke-width=\"1.35\"");
        StringAssert.Contains(telemetry, "140 - (p.Y");
        StringAssert.Contains(telemetry, "\"brake\" => $\"hsl");
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));
        StringAssert.Contains(css, ".event-session-switch .segmented");
        StringAssert.Contains(css, ".analysis-data-switch");
        StringAssert.Contains(css, ".analysis-review-grid");
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
        StringAssert.Contains(tuning, "State.TuningFeedback");
        Assert.IsTrue(File.Exists(Path.Combine(ui, "TuningTrackSelector.razor")));

        var live = File.ReadAllText(Path.Combine(ui, "LiveTelemetryPage.razor"));
        StringAssert.Contains(live, "LiveTelemetryLayoutGrid");
        var liveVisuals = File.ReadAllText(Path.Combine(ui, "LiveTelemetryVisuals.razor"));
        StringAssert.Contains(liveVisuals, "<canvas");
        StringAssert.Contains(liveVisuals, "LiveTelemetryFrame");
        StringAssert.Contains(liveVisuals, "display-synced");
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
    public void TrayExit_MarshalsFromTheTrayLoopThenAlwaysReachesShutdown()
    {
        var appRoot = Path.Combine(CompanionRoot(), "src", "iRacingCoach.App");
        var window = File.ReadAllText(Path.Combine(appRoot, "MainWindow.xaml.cs"));
        var requestStart = window.IndexOf("private void RequestExit()", StringComparison.Ordinal);
        var disposeStart = window.IndexOf("public void DisposeApplication()", StringComparison.Ordinal);
        var request = window[requestStart..disposeStart];
        StringAssert.Contains(request, "if (!Dispatcher.CheckAccess())");
        StringAssert.Contains(request, "new Action(RequestExit)");
        StringAssert.Contains(request, "if (_exitRequested) return;");
        StringAssert.Contains(request, "HideForApplicationExit();");
        StringAssert.Contains(request, "Dispatcher.BeginInvoke(DispatcherPriority.Send");

        var disposal = window[disposeStart..window.IndexOf("private void ApplyDarkTitleBar()", disposeStart, StringComparison.Ordinal)];
        StringAssert.Contains(disposal, "TryCleanup(Close, \"close main window\")");
        StringAssert.Contains(disposal, "TryCleanup(_state.Dispose, \"stop application services\")");
        Assert.IsLessThan(disposal.IndexOf("TryCleanup(_state.Dispose", StringComparison.Ordinal), disposal.IndexOf("TryCleanup(Close", StringComparison.Ordinal));

        var application = File.ReadAllText(Path.Combine(appRoot, "App.xaml.cs"));
        StringAssert.Contains(application, "Interlocked.Exchange(ref _exitStarted, 1)");
        StringAssert.Contains(application, "TimeSpan.FromSeconds(5)");
        StringAssert.Contains(application, "Shutdown(0)");
        Assert.HasCount(2, System.Text.RegularExpressions.Regex.Matches(application, "Environment\\.Exit\\(0\\)"));
        StringAssert.Contains(application, "finally");
        Assert.IsLessThan(application.LastIndexOf("Environment.Exit(0)", StringComparison.Ordinal), application.IndexOf("Shutdown(0)", StringComparison.Ordinal));
    }

    [TestMethod]
    public void AiSchema_AllowsContextualSectionsAndForbidsMissingEvidenceClass()
    {
        using var schema = JsonDocument.Parse(File.ReadAllText(Path.Combine(RepositoryRoot(), "companion-app-handoff", "contracts", "ai-coaching-output.schema.json")));
        var required = schema.RootElement.GetProperty("required").EnumerateArray().Select(item => item.GetString()).ToArray();
        CollectionAssert.AreEquivalent(new[] { "summary", "actions", "followUpNeeded" }, required);
        var classes = schema.RootElement.GetProperty("$defs").GetProperty("evidenceClass").GetProperty("enum").EnumerateArray().Select(item => item.GetString()).ToArray();
        CollectionAssert.DoesNotContain(classes, "unavailable");
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

    private static string CompanionRoot([CallerFilePath] string source = "") => Path.GetFullPath(Path.Combine(Path.GetDirectoryName(source)!, "..", ".."));
    private static string RepositoryRoot([CallerFilePath] string source = "") => Path.GetFullPath(Path.Combine(Path.GetDirectoryName(source)!, "..", "..", ".."));
}
