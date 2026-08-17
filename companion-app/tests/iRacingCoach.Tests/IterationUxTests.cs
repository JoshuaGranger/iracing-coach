using System.Text.Json;
using System.Text.RegularExpressions;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class IterationUxTests
{
    [TestMethod]
    public void TechnicalData_UsesTheAvailableCanvasForDecisionsAndExplanation()
    {
        var ui = UiRoot();
        var page = File.ReadAllText(Path.Combine(ui, "RaceTechnicalData.razor"));
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "iteration-ux.css"));

        StringAssert.Contains(page, "What to carry forward");
        StringAssert.Contains(page, "metric.Detail");
        StringAssert.Contains(page, "metric.Action");
        StringAssert.Contains(page, "data-technical-no-stop");
        StringAssert.Contains(page, "data-fuel-decision");
        StringAssert.Contains(page, "technical-pit-timeline");
        StringAssert.Contains(page, "Stay out: backend margin");
        StringAssert.Contains(page, "NoStopAuthorized");
        Assert.DoesNotContain("range >= Workspace.ScheduledLaps", page);
        StringAssert.Contains(page, "2 tires");
        StringAssert.Contains(page, "4 tires");
        StringAssert.Contains(page, "data-metric-count=\"@PitCardMetrics.Count\"");
        StringAssert.Contains(page, "data-metric-count=\"@RacecraftCardMetrics.Count\"");
        StringAssert.Contains(page, "MetricDensityClass(RacecraftCardMetrics.Count)");
        StringAssert.Contains(page, "MetricTooltip(metric)");
        StringAssert.Contains(page, "data-tire-call=\"@TireCallKey(pitRun)\"");
        StringAssert.Contains(page, "data-two-vs-four=\"supported\"");
        StringAssert.Contains(page, "data-two-vs-four=\"inconclusive\"");
        StringAssert.Contains(page, "data-two-vs-four=\"single-call\"");
        StringAssert.Contains(page, "TireDriverMetrics");
        StringAssert.Contains(page, "data-racecraft-story");
        Assert.DoesNotContain(".Take(", page, "Technical metrics and graphical groups must never use an arbitrary item cap.");
        Assert.DoesNotContain("data-evidence=", page, "The primary UI should not display or encode provenance as a visual badge contract.");
        Assert.DoesNotContain("Garage61 reference laps", page);
        Assert.DoesNotContain("@Garage61Evidence", page);
        StringAssert.Contains(css, ".technical-no-stop-story");
        StringAssert.Contains(css, ".technical-fuel-decision");
        StringAssert.Contains(css, ".technical-findings-list");
        StringAssert.Contains(css, ".race-technical-data .technical-overview {");
        StringAssert.Contains(css, "grid-template-rows: repeat(2, minmax(0, 1fr));");
        StringAssert.Contains(css, ".race-technical-data .technical-card-metrics.metric-density-dense { grid-template-columns: repeat(4, minmax(0, 1fr)); }");
        StringAssert.Contains(css, ".race-technical-data .technical-investigation-content.metric-density-dense");
        StringAssert.Contains(css, ".race-technical-data .technical-tire-call-car");
        StringAssert.Contains(css, ".race-technical-data .technical-dynamics-grid");
        StringAssert.Contains(css, ".race-technical-data .technical-racecraft-groups");
        StringAssert.Contains(css, "transform: none;");
    }

    [TestMethod]
    public void PlanningAndStartingTune_AreDecisionFirstAndBrowseable()
    {
        var ui = UiRoot();
        var planning = File.ReadAllText(Path.Combine(ui, "PlanningPage.razor"));
        var setup = File.ReadAllText(Path.Combine(ui, "SetupPage.razor"));

        Assert.DoesNotContain(">Start over</button>", planning);
        StringAssert.Contains(planning, "data-planning-decision");
        StringAssert.Contains(planning, "No scheduled fuel stop");
        StringAssert.Contains(planning, "Respond during the race");
        Assert.DoesNotContain("<EvidenceBadge", planning);
        Assert.DoesNotContain("Local recordings", planning);
        StringAssert.Contains(setup, "starting-tune-step-rail");
        StringAssert.Contains(setup, "Type or browse installed cars");
        StringAssert.Contains(setup, "Type or browse installed layouts");
        StringAssert.Contains(setup, "<datalist id=\"starting-tune-cars\"");
        StringAssert.Contains(setup, "<datalist id=\"starting-tune-tracks\"");
        Assert.DoesNotContain("How this briefing was calculated", planning);
        Assert.DoesNotContain("@package.DonorReason", setup);
        Assert.DoesNotContain("<summary>Technical record</summary>", setup);
    }

    [TestMethod]
    public void PrimaryUi_HidesInternalProvenanceWhileKeepingMachineReadableContracts()
    {
        var ui = UiRoot();
        var analysis = File.ReadAllText(Path.Combine(ui, "AnalysisWorkspacePage.razor"));
        var setup = File.ReadAllText(Path.Combine(ui, "SetupPage.razor"));
        var live = File.ReadAllText(Path.Combine(ui, "LiveTelemetryVisuals.razor"));

        Assert.DoesNotContain("How this was graded", analysis);
        Assert.DoesNotContain("<dt>Evidence</dt>", analysis);
        Assert.DoesNotContain("<dt>Calibration</dt>", analysis);
        Assert.DoesNotContain("<dt>Provenance</dt>", analysis);
        StringAssert.Contains(analysis, "data-grade-evidence");
        StringAssert.Contains(analysis, "data-grade-calibration");
        StringAssert.Contains(analysis, "data-grade-provenance");
        StringAssert.Contains(setup, "data-setup-source");
        StringAssert.Contains(setup, "data-setup-fingerprint");
        Assert.DoesNotContain(">Source</span><strong>@package.Source", setup);
        Assert.DoesNotContain("@package.Fingerprint</p>", setup);
        Assert.DoesNotContain("ChartRateLabel", live);
        Assert.DoesNotContain("Hz source", live);
        StringAssert.Contains(live, "data-source-rate");
    }

    [TestMethod]
    public void ProgressiveTuning_UsesOneToolboxAndDistinguishesPreviewFromSelection()
    {
        var ui = UiRoot();
        var page = File.ReadAllText(Path.Combine(ui, "TuningPage.razor"));
        var editor = File.ReadAllText(Path.Combine(ui, "ProgressiveTuningFeedbackEditor.razor"));
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "iteration-ux.css"));

        Assert.DoesNotContain("tuning-feedback-popover", page);
        StringAssert.Contains(page, "data-active-corner-editor");
        StringAssert.Contains(page, "OPEN");
        StringAssert.Contains(page, "FIXED");
        StringAssert.Contains(editor, "Severity: how much this behavior hurt");
        StringAssert.Contains(editor, "Confidence: how certain you are");
        Assert.DoesNotContain("ToggleRatingsPanel", editor);
        StringAssert.Contains(css, ".tuning-turn-segment.active:not(.selected)");
        StringAssert.Contains(css, ".tuning-turn-segment.selected");
    }

    [TestMethod]
    public void ThemeContract_UsesHighContrastGraphiteAndTroubleshootingMatchesConnections()
    {
        var root = CompanionRoot();
        var themePath = Path.Combine(root, "..", "config", "theme.dark.json");
        using var theme = JsonDocument.Parse(File.ReadAllText(themePath));
        var colors = theme.RootElement.GetProperty("colors");
        Assert.AreEqual("#0B1015", colors.GetProperty("app").GetString());
        Assert.AreEqual("#172028", colors.GetProperty("chartBackground").GetString());
        Assert.AreEqual(500, theme.RootElement.GetProperty("motionMs").GetProperty("structure").GetInt32());

        var ui = UiRoot();
        var diagnostics = File.ReadAllText(Path.Combine(ui, "DiagnosticsPage.razor"));
        var settings = File.ReadAllText(Path.Combine(ui, "SettingsPage.razor"));
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "iteration-ux.css"));
        StringAssert.Contains(diagnostics, "surface-card settings-accordion");
        StringAssert.Contains(settings, "surface-card settings-accordion");
        StringAssert.Contains(css, ".diagnostics-section.settings-accordion");
    }

    [TestMethod]
    public void ToastStatus_DoesNotBlockControlsBehindIt()
    {
        var css = File.ReadAllText(Path.Combine(UiRoot(), "wwwroot", "coach.css"));

        StringAssert.Contains(css, ".toast { position: fixed;");
        StringAssert.Contains(css, "box-shadow: 0 10px 32px var(--shadow); pointer-events: none;");
        StringAssert.Contains(css, ".toast button {");
        StringAssert.Contains(css, "cursor: pointer; pointer-events: auto;");
    }

    [TestMethod]
    public void EveryUiStylesheet_RespectsTheGlobalMinimumTextSize()
    {
        var cssRoot = Path.Combine(UiRoot(), "wwwroot");
        var fontSize = new Regex(@"font-size\s*:\s*(?<size>\d+(?:\.\d+)?)px", RegexOptions.IgnoreCase);
        foreach (var path in Directory.EnumerateFiles(cssRoot, "*.css", SearchOption.TopDirectoryOnly))
        {
            var source = File.ReadAllText(path);
            foreach (Match match in fontSize.Matches(source))
            {
                var size = double.Parse(match.Groups["size"].Value, System.Globalization.CultureInfo.InvariantCulture);
                Assert.IsGreaterThanOrEqualTo(11.67d, size, $"{Path.GetFileName(path)} uses {size}px text below --font-size-min.");
            }
        }
    }

    private static string UiRoot() => Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI");

    private static string CompanionRoot() => TestRepositoryPaths.CompanionAppRoot;
}
