using System.Collections;
using System.Reflection;
using System.Text.Json;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;
using iRacingCoach.UI;

namespace iRacingCoach.Tests;

/// <summary>
/// Details of the 9/24 IRP Offline Testing recording of run D: "Pit strategy · One stop"
/// beside "No stop · Stops recorded 0" (a tow to the stall), "Positions held" on a test,
/// "Kept" on every tire although no tire change was confirmed, and the run's early-versus-late
/// pace read "+0.18 s" beside Runs' "+0.211" while its own takeaway quoted 0.211.
/// </summary>
[TestClass]
public sealed class OfflineDetailsTests
{
    [TestMethod]
    public void OfflineTestingDetailsLeaveOutRaceOnlyCardsAndFigures()
    {
        var test = Component(Workspace("Offline Testing"));
        Assert.IsFalse(Read<bool>(test, "IsRaceSession"));
        Assert.AreEqual("Pace", Read<string>(test, "RacecraftTitle"));
        Assert.AreNotEqual("Positions held", Read<string>(test, "PaceState"), "A test holds no positions.");
        CollectionAssert.DoesNotContain(Labels(Read<object>(test, "RacecraftCardMetrics")), "Net positions");

        var race = Component(Workspace("Race"));
        Assert.IsTrue(Read<bool>(race, "IsRaceSession"));
        Assert.AreEqual("Racecraft & pace", Read<string>(race, "RacecraftTitle"));
        Assert.AreEqual("Positions held", Read<string>(race, "PaceState"), "A race keeps its position outcome.");
        CollectionAssert.Contains(Labels(Read<object>(race, "RacecraftCardMetrics")), "Net positions");
        Assert.IsTrue(Read<bool>(Component(Workspace("")), "IsRaceSession"), "A legacy analysis without a session type keeps the race view.");

        var page = Source("RaceTechnicalData.razor");
        StringAssert.Contains(page, "@if (IsRaceSession)\n            {\n            <button type=\"button\" class=\"technical-overview-card pit\"",
            "The pit-strategy card is a race card.");
        StringAssert.Contains(page, "@if (IsRaceSession)\n                            {\n                            <section class=\"technical-fuel-decision\"",
            "The race-distance fuel decision needs a scheduled race.");
        StringAssert.Contains(page, "IsRaceSession ? \"Pit-stop tire condition\" : \"Tire condition in the stall\"",
            "A tow or reset to the stall is not a pit stop.");
        StringAssert.Contains(File.ReadAllText(Path.Combine(TestRepositoryPaths.UiRoot, "wwwroot", "iteration-ux.css")),
            ".technical-overview.without-pit .technical-overview-card.racecraft { grid-column: 1 / -1; }");
    }

    [TestMethod]
    public void ATireIsTaggedKeptOnlyBesideAConfirmedChangeOfOtherTires()
    {
        var run = Workspace("Offline Testing").Runs[0];
        var towed = run with { PitStop = run.PitStop! with { TiresChanged = [] } };
        var rightSides = run with { PitStop = run.PitStop! with { TiresChanged = ["RF", "RR"] } };
        Assert.IsNull(Tag(towed, "LF"), "No tire change was confirmed, so no corner reads 'Kept'.");
        Assert.AreEqual("changed", Tag(rightSides, "RF"));
        Assert.AreEqual("kept", Tag(rightSides, "LF"));
        var title = (string)typeof(RaceTechnicalData).GetMethod("TireServiceTitle", BindingFlags.Static | BindingFlags.NonPublic)!.Invoke(null, [towed, "LF"])!;
        Assert.AreEqual("No tire change was confirmed at this stop.", title);
        Assert.DoesNotContain("ChangedAtStop(SelectedTireRun, tire.Corner) ? \"Changed\" : \"Kept\"", Source("RaceTechnicalData.razor"));
    }

    [TestMethod]
    public void DetailsStateTheEarlyVersusLateGapRunsShowsAndKeepSlashLapForFallOff()
    {
        var comparison = new RaceInvestigationComparison("run-1-clean", "within_run", "Run 1: clean early versus late",
            "Laps 38–42: median lap 0.211 s slower than laps 24–28.",
            Cohort("Laps 24–28", [24, 25, 26, 27, 28], 23.192), Cohort("Laps 38–42", [38, 39, 40, 41, 42], 23.403),
            0.211, "eligible", [], [], [], []);
        var investigation = new RaceInvestigation(2, "available", comparison.Summary, comparison.Id, [comparison], [], [], null, [],
            JsonSerializer.SerializeToElement(new { }), ProjectionVersion: RaceInvestigationMapper.CurrentProjectionVersion);
        var workspace = Workspace("Offline Testing") with { Investigation = investigation };
        var run = workspace.Runs[0] with { EarlyToLateDeltaSeconds = 0.18 };
        var component = Component(workspace);

        var chosen = typeof(RaceTechnicalData).GetMethod("CleanEarlyLate", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(component, [run]);
        Assert.AreSame(comparison, chosen, "Details reads the run's early-versus-late pace from the comparison Runs shows.");
        Assert.AreEqual("+0.211 s median lap", InvestigationPresentation.PaceResult(comparison), "A lap-time gap is in seconds, never s/lap.");
        var trend = (string)typeof(RaceTechnicalData).GetMethod("PaceTrend", BindingFlags.Static | BindingFlags.NonPublic)!
            .Invoke(null, [run with { PaceSlopeSecondsPerLap = 0.018 }])!;
        Assert.AreEqual("fall-off +0.018 s/lap", trend, "s/lap is kept for the slope and named as fall-off.");
        Assert.IsNull(typeof(RaceTechnicalData).GetMethod("CleanEarlyLate", BindingFlags.Instance | BindingFlags.NonPublic)!
            .Invoke(Component(Workspace("Offline Testing")), [run]), "Without the comparison the vector falls back to labelled averages.");
        StringAssert.Contains(Source("RaceTechnicalData.razor"), "\"Early · average\"");
        Assert.DoesNotContain("s/lap by median", Source("RaceProgressionChart.razor"));
    }

    [TestMethod]
    public void SettingsNameThePopoutDashboardInBodyText()
    {
        // The card showed the dashboard name "Default Copy" alone in purple monospace.
        var page = Source("SettingsPage.razor");
        StringAssert.Contains(page, "<span class=\"settings-layout-name\" data-popout-dashboard");
        StringAssert.Contains(page, "<small>Dashboard</small>@LiveMonitorLayouts.Active(State.Settings.LiveMonitor).Layout.Name");
        var css = File.ReadAllText(Path.Combine(TestRepositoryPaths.UiRoot, "wwwroot", "coach.css"));
        var rule = css.Split('\n').Single(line => line.StartsWith(".settings-layout-name {", StringComparison.Ordinal));
        Assert.DoesNotContain("telemetry-steering", rule);
        Assert.DoesNotContain("monospace", rule);
        Assert.DoesNotContain("metric-font", rule);
    }

    private static RaceInvestigationCohort Cohort(string label, int[] laps, double median) =>
        new(label, 1, laps, laps.Length, laps, [], median, null, null, null, null, null);

    private static string? Tag(AnalysisRun run, string corner) =>
        (string?)typeof(RaceTechnicalData).GetMethod("TireServiceTag", BindingFlags.Static | BindingFlags.NonPublic)!.Invoke(null, [run, corner]);

    private static string[] Labels(object metrics) => ((IEnumerable)metrics).Cast<object>()
        .Select(item => (string)item.GetType().GetProperty("Label")!.GetValue(item)!).ToArray();

    private static string Source(string file) => File.ReadAllText(Path.Combine(TestRepositoryPaths.UiRoot, file)).Replace("\r\n", "\n");

    private static RaceTechnicalData Component(AnalysisWorkspace workspace)
    {
        var component = new RaceTechnicalData();
        typeof(RaceTechnicalData).GetProperty(nameof(RaceTechnicalData.Workspace))!.SetValue(component, workspace);
        return component;
    }

    private static T Read<T>(RaceTechnicalData component, string property) =>
        (T)typeof(RaceTechnicalData).GetProperty(property, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(component)!;

    private static AnalysisWorkspace Workspace(string sessionType)
    {
        using var document = JsonDocument.Parse(Response);
        var racecraft = new AnalysisTechnicalInsight("racecraft", "Racecraft & pace", "available", "stable",
            "Laps 38–42: median lap 0.211 s slower than laps 24–28.",
            [
                new("Net positions", "0 positions", EvidenceKind.Measured, "", "", "neutral", "result"),
                new("Long-run change", "+0.211 s", EvidenceKind.Derived, "", "", "attention", "pace"),
                new("Fastest clean", "22.951 s", EvidenceKind.Measured, "", "", "neutral", "pace")
            ], [], []);
        return RuntimeMapper.Analysis(document.RootElement) with { SessionType = sessionType, TechnicalInsights = [racecraft] };
    }

    // IRP 9/24 run D as recorded: one run of 19 green laps ending in a tow to the stall
    // (48.6 s, no fuel, no confirmed tire change).
    private const string Response = """
    {
      "analysis_view":{
        "schema_version":1,"analysis_profile_version":"post-race-foundations-v21",
        "identity":{"track_name":"Lucas Oil Indianapolis Raceway Park","track_config":"IRP","car_name":"Toyota Supra Class B","event_type":"Test"},
        "race_summary":{"recorded_laps":42},
        "runs":[
          {"run_number":1,"lap_numbers":[24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42],"green_laps":19,"caution_laps":0,
           "ended_with_pit_stop":true,"tire_measurement_status":"measured_at_stop",
           "pace":{"early_average_lap_s":23.2,"late_average_lap_s":23.38,"early_to_late_delta_s":0.18,"green_lap_time_slope_s_per_lap":0.018},
           "pit_service":{"start_time":1000.0,"end_time":1048.6,"fuel_added_l":0.0,"tires_changed_observed":[]}}],
        "lap_traces":{"traces":[]},
        "strategy":{"forecast":{}}
      }
    }
    """;
}
