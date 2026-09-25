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

    private static string Row(string html, int run)
    {
        var start = html.IndexOf($"<tr data-run=\"{run}\"", StringComparison.Ordinal);
        Assert.IsGreaterThanOrEqualTo(0, start, $"Run {run} has a row.");
        var end = html.IndexOf("</tr>", start, StringComparison.Ordinal);
        return html[start..end];
    }

    private static int Count(string text, string value) => text.Split(value, StringSplitOptions.None).Length - 1;
}
