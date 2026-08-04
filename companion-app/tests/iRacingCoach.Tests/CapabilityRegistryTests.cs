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
    public void TrackViewAndSetupComparison_AreTruthfullyConditionalAndReachable()
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
        StringAssert.Contains(File.ReadAllText(Path.Combine(ui, "SetupPage.razor")), "ProductCapability.SetupComparison");
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
        StringAssert.Contains(setup, "Find baseline source");
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
        StringAssert.Contains(telemetry, "<b>Lap @trace.Lap</b>");
        foreach (var channel in new[] { "Speed", "Time Delta", "Throttle / Brake", "Gear", "RPM", "Steering", "Slip Angle / Yaw Rate", "Lateral / Longitudinal G" })
            StringAssert.Contains(telemetry, $"\"{channel}\"");
        foreach (var dynamicsChannel in new[] { "Slip Angle", "Yaw Rate", "Lateral Acceleration", "Longitudinal Acceleration" })
            StringAssert.Contains(telemetry, dynamicsChannel);
        StringAssert.Contains(telemetry, "PanelRange(panel)");
        Assert.DoesNotContain("Gear / RPM", telemetry);

        var analysis = File.ReadAllText(Path.Combine(ui, "AnalysisWorkspacePage.razor"));
        Assert.DoesNotContain("<RaceCardPanel", analysis);
        StringAssert.Contains(analysis, "Recorded range and race-distance outlook");
        StringAssert.Contains(analysis, "Recorded pit, tow, and repair timeline");
        foreach (var tab in new[] { "Overview", "Telemetry", "Corner Coaching", "Runs & Tires", "Fuel & Strategy", "Damage & Repairs", "Setup & Evidence" })
            StringAssert.Contains(analysis, $"\"{tab}\"");
        StringAssert.Contains(analysis, "BuildCornerRows");

        var navigation = File.ReadAllText(Path.Combine(ui, "NavRail.razor"));
        StringAssert.Contains(navigation, "Setup Library");

        var tuning = File.ReadAllText(Path.Combine(ui, "TuningPage.razor"));
        StringAssert.Contains(tuning, "TuningTrackSelector");
        StringAssert.Contains(tuning, "State.TuningFeedback");
        Assert.IsTrue(File.Exists(Path.Combine(ui, "TuningTrackSelector.razor")));

        var live = File.ReadAllText(Path.Combine(ui, "LiveTelemetryPage.razor"));
        StringAssert.Contains(live, "live-disconnected-preview");
        var monitor = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.App", "LiveMonitorWindow.xaml"));
        StringAssert.Contains(monitor, "ShowInTaskbar=\"True\"");

        var installer = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.Installer", "Program.cs"));
        StringAssert.Contains(installer, "SpecialFolder.LocalApplicationData");
        StringAssert.Contains(installer, "argument.Equals(\"/S\"");
        StringAssert.Contains(installer, "Registry.CurrentUser.CreateSubKey");
        Assert.DoesNotContain("Verb = \"runas\"", installer);
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
