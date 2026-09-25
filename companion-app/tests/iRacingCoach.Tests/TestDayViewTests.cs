using System.Text.Json.Nodes;
using iRacingCoach.Contracts;
using iRacingCoach.UI;
using static iRacingCoach.Tests.TestDayMapperTests;

namespace iRacingCoach.Tests;

/// <summary>
/// The Test day view renders one table, one chart and one next-change card from
/// the backend review, and shows "Not measured" wherever the review has no value.
/// </summary>
[TestClass]
public sealed class TestDayViewTests
{
    private static RaceEventGroup Day(int runs = 3) => new("test-day-0123456789abcdef", "Synthetic Speedway", "Oval", "Synthetic car", "Aug 4",
        "Open", string.Empty,
        Enumerable.Range(1, runs).Select(run => new RecentRace($"local:stint-{run}", "Synthetic Speedway", "Oval", "Synthetic car", "Aug 4",
            "Open", "Recorded", "Recorded", false, false, 0, 0, SessionType: "Offline Testing", EventKey: $"local:stint-{run}",
            TestDayId: "test-day-0123456789abcdef", TestDayRunNumber: run, TestDayRunCount: runs)).ToArray());

    [TestMethod]
    public async Task MissingMeasurementsRenderAsNotMeasuredNeverZero()
    {
        var fixture = Fixture();
        var late = fixture["runs"]![0]!["late"]!.AsObject();
        late["status"] = "not_measured";
        late["median_s"] = null;
        late["clean_lap_count"] = 1;
        late["laps"] = new JsonArray(12);
        late["reason"] = "1 clean lap in laps 12–16; 3 are needed.";
        var review = Map(fixture);
        Assert.IsNull(review.Runs[0].Late.MedianSeconds);
        Assert.IsFalse(review.Runs[0].Late.Measured);

        var html = await RaceInvestigationPerformanceTests.Render<TestDayView>(new()
        {
            [nameof(TestDayView.Group)] = Day(),
            [nameof(TestDayView.Review)] = review
        });

        var runOne = Row(html, 1);
        StringAssert.Contains(runOne, "title=\"1 clean lap in laps 12–16; 3 are needed.\">Not measured");
        Assert.DoesNotContain("0.000", runOne);
        StringAssert.Contains(Row(html, 3), "Not measured", "Run 3 ended on track, so its tires at the stop are not measured.");
    }

    [TestMethod]
    public async Task ViewShowsOneTableOneChartAndOneNextChange()
    {
        var html = await RaceInvestigationPerformanceTests.Render<TestDayView>(new()
        {
            [nameof(TestDayView.Group)] = Day(),
            [nameof(TestDayView.Review)] = Map(Fixture())
        });

        Assert.AreEqual(1, Count(html, "<table"), "One table: a row per run.");
        Assert.AreEqual(1, Count(html, "class=\"ri-plot-svg\""), "One chart: every run overlaid.");
        Assert.AreEqual(1, Count(html, "data-next-change="), "Exactly one next-change card.");
        Assert.AreEqual(3, Count(html, "<tr data-run="), "One row per run.");
        StringAssert.Contains(html, "Lap of run");
        StringAssert.Contains(html, "Run 3 · lap 14 · ");
        StringAssert.Contains(html, "Within scatter");
        StringAssert.Contains(html, "Faster</span>");
        StringAssert.Contains(html, "−0.110 s · Laps 12–16");
        StringAssert.Contains(html, "Front brake bias 56.0% → 56.5%");
        StringAssert.Contains(html, "Keep front brake bias at 56.5%; try one more step");
        StringAssert.Contains(html, "Beat 24.068 s median on laps 12–16 by at least 0.030 s with 3+ clean laps.");
        StringAssert.Contains(html, "garage step size not verified");
        Assert.DoesNotContain("data-tires-run", html, "Tire detail appears only for a selected run.");
        Assert.DoesNotContain("Describe handling", html, "No handling link without a tunable setup.");
    }

    [TestMethod]
    public async Task ViewOffersToCompareWhenTheDayHasNotBeenRead()
    {
        var html = await RaceInvestigationPerformanceTests.Render<TestDayView>(new()
        {
            [nameof(TestDayView.Group)] = Day(2),
            [nameof(TestDayView.Loading)] = true,
            [nameof(TestDayView.Message)] = "Reading run 2 of 2…"
        });

        StringAssert.Contains(html, "data-test-day-state=\"loading\"");
        StringAssert.Contains(html, "Reading run 2 of 2…");
        Assert.DoesNotContain("<table", html);
    }

    [TestMethod]
    public void WorkspaceNamesTheFirstViewTestDayOnlyForATestDayStint()
    {
        var workspace = File.ReadAllText(Path.Combine(TestRepositoryPaths.CompanionAppRoot, "src", "iRacingCoach.UI", "AnalysisWorkspacePage.razor"));
        StringAssert.Contains(workspace, "[(\"race\", \"Test day\"), (\"runs\", \"Runs\"), (\"sections\", \"Sections\")]");
        StringAssert.Contains(workspace, "TestDay is null ? PrimaryViews : TestDayViews");
        StringAssert.Contains(workspace, "State.TestDayGroupFor(State.SelectedRaceSession)");
    }

    [TestMethod]
    public void SessionsListDoesNotSplitOfflineTestingLapsIntoGreenAndCaution()
    {
        var sessions = File.ReadAllText(Path.Combine(TestRepositoryPaths.CompanionAppRoot, "src", "iRacingCoach.UI", "SessionsPage.razor"));
        StringAssert.Contains(sessions, "if (OfflineTesting(session) && overview.RecordedLaps > 0) parts.Add($\"{overview.RecordedLaps} laps\");");
        StringAssert.Contains(sessions, "OrderBy(session => session.TestDayRunNumber ?? int.MaxValue)");
    }

    private static Task<string> RenderSelected(JsonObject fixture, int run) => RaceInvestigationPerformanceTests.Render<TestDayView>(new()
    {
        [nameof(TestDayView.Group)] = Day(),
        [nameof(TestDayView.Review)] = Map(fixture),
        [nameof(TestDayView.SelectedRunNumber)] = run
    });

    private static string Tires(string html)
    {
        var start = html.IndexOf("data-tires-run=", StringComparison.Ordinal);
        Assert.IsGreaterThanOrEqualTo(0, start, "The selected run's detail is shown.");
        return html[start..];
    }

    [TestMethod]
    public async Task ATreadOnlySheetDifferenceIsNamedAsTread()
    {
        var fixture = Fixture();
        var agreement = fixture["runs"]![1]!["tires"]!["corners"]!["LF"]!["sheet_vs_stop"]!.AsObject();
        agreement["status"] = "disagree";
        agreement["disagrees_on"] = new JsonArray("tread");
        agreement["max_temperature_delta_f"] = 0.0;
        agreement["max_tread_delta_percent"] = 3.0;

        var detail = Tires(await RenderSelected(fixture, 2));

        StringAssert.Contains(detail, "differs tread 3%");
        Assert.DoesNotContain("differs 0°", detail, "Equal temperatures are never shown as a temperature difference.");
    }

    [TestMethod]
    public async Task TheGarageSheetIsOneNeutralLineApartFromTheStopCards()
    {
        var detail = Tires(await RenderSelected(Fixture(), 2));
        var cards = detail[detail.IndexOf("td-tire-grid", StringComparison.Ordinal)..detail.IndexOf("data-sheet", StringComparison.Ordinal)];

        Assert.DoesNotContain("Sheet", cards, "The sheet is not a row of this run's tire cards.");
        StringAssert.Contains(detail, "Garage sheet · timing unverified");
        StringAssert.Contains(detail, "Not attributed to this stop:");
        Assert.DoesNotContain("class=\"disagree\"", detail, "The sheet is not warning-coloured.");

        var fixture = Fixture();
        fixture["runs"]![1]!["tires"]!["sheet_reading"]!["uniform"] = true;
        StringAssert.Contains(Tires(await RenderSelected(fixture, 2)), "An untouched garage state (every band equal); not a reading of this run.");
    }

    [TestMethod]
    public async Task StopTemperaturesAreLabelledCarcass()
    {
        var html = await RenderSelected(Fixture(), 2);
        StringAssert.Contains(html, "Tires at stop · carcass I−O, tread");
        StringAssert.Contains(Tires(html), "<dt>Carcass °F</dt>");
    }

    [TestMethod]
    public async Task SelectingARowShowsEveryReasonAndCautionAsText()
    {
        var fixture = Fixture();
        var comparison = fixture["runs"]![2]!["comparison"]!.AsObject();
        comparison["flags"] = new JsonArray("Tire compound is not recorded for run 2 or run 3.",
            "Run 3 may have started on used tires: Tire odometers read up to 34,300 m when lap 1 began (over 250 m).");

        var html = await RenderSelected(fixture, 3);

        StringAssert.Contains(Row(html, 3), "+2 notes", "The row says there is more to read.");
        var notes = Tires(html);
        StringAssert.Contains(notes, "vs run 2");
        StringAssert.Contains(notes, "<li>Run 3 may have started on used tires: Tire odometers read up to 34,300 m when lap 1 began (over 250 m).</li>");
        StringAssert.Contains(notes, "<li>Tire compound is not recorded for run 2 or run 3.</li>");
        StringAssert.Contains(notes, "Front brake bias 56.0% → 56.5%");
    }

    [TestMethod]
    public async Task TheChartIsNotATabStopAndItsFitIsTheTablesFallOff()
    {
        var html = await RaceInvestigationPerformanceTests.Render<TestDayView>(new()
        {
            [nameof(TestDayView.Group)] = Day(),
            [nameof(TestDayView.Review)] = Map(Fixture())
        });

        Assert.DoesNotContain("tabindex=\"0\" data-lap=", html, "Lap dots are not tab stops ahead of the run table.");
        StringAssert.Contains(html, "tabindex=\"-1\" data-lap=");
        StringAssert.Contains(html, ">Fall-off fit</button>");
        Assert.DoesNotContain("Screened fit", html);
        Assert.DoesNotContain("green", html[..html.IndexOf("<table", StringComparison.Ordinal)].Replace("\"green\"", "", StringComparison.Ordinal),
            "No race wording on a lap-of-run chart.");
        var view = File.ReadAllText(Path.Combine(TestRepositoryPaths.CompanionAppRoot, "src", "iRacingCoach.UI", "TestDayView.razor"));
        StringAssert.Contains(view, "$\"fall-off {Signed(fit.SlopeSecondsPerGreenLap)} s/lap · {fit.EligibleCount} clean laps");
        StringAssert.Contains(view, "await row.FocusAsync();", "Closing the detail returns the keyboard to its row.");
    }

    [TestMethod]
    public async Task TheSelectedRowRingsTheLapsTheBackendCompared()
    {
        var fixture = Fixture();
        var comparison = fixture["runs"]![2]!["comparison"]!.AsObject();
        comparison["run_laps"] = new JsonArray(12, 14, 16);
        comparison["previous_laps"] = new JsonArray(12, 14, 16);
        comparison["current_laps"] = new JsonArray(12, 14, 16);

        var html = await RenderSelected(fixture, 3);

        Assert.AreEqual(6, Count(html, "class=\"ri-ring\""), "Three shared laps of each run are ringed, not every clean lap of the window.");
    }

    [TestMethod]
    public void ShowingARunsLapsOpensItInRuns()
    {
        var view = File.ReadAllText(Path.Combine(TestRepositoryPaths.CompanionAppRoot, "src", "iRacingCoach.UI", "TestDayView.razor"));
        var workspace = File.ReadAllText(Path.Combine(TestRepositoryPaths.CompanionAppRoot, "src", "iRacingCoach.UI", "AnalysisWorkspacePage.razor"));
        StringAssert.Contains(view, "Show run @chosen.Run's laps in Runs");
        StringAssert.Contains(workspace, "await State.OpenTestDayRunAsync(session);");
        StringAssert.Contains(workspace, "ShowView(\"runs\");");
        StringAssert.Contains(workspace, "SelectedRunNumber=\"TestDaySelectedRun(day)\"", "The selected row survives the round trip.");
    }

    [TestMethod]
    public async Task AnOfflineTestingStintsEmptyRunsAndSectionsSayWhyWithoutBlamingTheLaps()
    {
        var empty = new RaceInvestigation(2, "measured", "", null, [], [], [], null, [], default, Runs: []);
        async Task<string> Render(string view, bool offline) => await RaceInvestigationPerformanceTests.Render<RaceInvestigationView>(new()
        {
            [nameof(RaceInvestigationView.Investigation)] = empty,
            [nameof(RaceInvestigationView.View)] = view,
            [nameof(RaceInvestigationView.OfflineTesting)] = offline,
            [nameof(RaceInvestigationView.TestDayAvailable)] = offline
        });

        var runs = await Render("runs", offline: true);
        StringAssert.Contains(runs, "No green laps to place here");
        StringAssert.Contains(runs, "one-lap-to-green flag");
        StringAssert.Contains(runs, "Test day compares them by lap of run.");
        Assert.DoesNotContain("Runs need complete green laps", runs);
        StringAssert.Contains(await Render("sections", offline: true), "No green-lap comparison to inspect");
        StringAssert.Contains(await Render("runs", offline: false), "Runs need complete green laps", "A race keeps its own wording.");
    }

    private static string Row(string html, int run)
    {
        var start = html.IndexOf($"data-run=\"{run}\" tabindex=", StringComparison.Ordinal);
        Assert.IsGreaterThanOrEqualTo(0, start, $"Run {run} has a row.");
        var end = html.IndexOf("</tr>", start, StringComparison.Ordinal);
        return html[start..end];
    }

    private static int Count(string text, string value) => text.Split(value, StringSplitOptions.None).Length - 1;
}
