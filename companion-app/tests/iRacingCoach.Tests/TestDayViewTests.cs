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
        StringAssert.Contains(html, "<small>−0.110 s</small>");
        StringAssert.Contains(html, "Laps 12–16 · laps 2–5 −0.110 ±0.037", "The row states both windows of the comparison.");
        StringAssert.Contains(html, "Front brake bias 56.0% → 56.5%");
        StringAssert.Contains(html, "Keep front brake bias at 56.5%; try one more step");
        StringAssert.Contains(html, "Beat 24.068 s median on laps 12–16 by at least 0.033 s with 3+ clean laps in a run of 16+ laps.");
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
        StringAssert.Contains(html, "Tires at stop · I−O °F, tread");
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

    [TestMethod]
    public async Task RunsInsideOneRecordingSayWhereTheyAreAndThatTheirSetupChangeIsNotRecorded()
    {
        var fixture = Fixture();
        foreach (var (run, place) in new[] { (1, 1), (2, 2) })
        {
            fixture["runs"]![run]!["recording"]!["number"] = 2;
            fixture["runs"]![run]!["recording"]!["run_in_recording"] = place;
            fixture["runs"]![run]!["recording"]!["runs_in_recording"] = 2;
        }
        var change = fixture["runs"]![2]!["setup_change"]!.AsObject();
        change["status"] = "not_recorded";
        change["changes"] = new JsonArray();
        change["change_count"] = 0;

        var html = await RaceInvestigationPerformanceTests.Render<TestDayView>(new()
        {
            [nameof(TestDayView.Group)] = Day(),
            [nameof(TestDayView.Review)] = Map(fixture)
        });

        StringAssert.Contains(Row(html, 3), "2 of 2 in recording");
        StringAssert.Contains(Row(html, 3), "Not recorded · same recording");
        Assert.DoesNotContain("in recording", Row(html, 1), "A run alone in its recording needs no place.");
    }

    [TestMethod]
    public void PracticeAndTestingOpenTheInvestigationAndOnlyQualifyingKeepsTheOldPage()
    {
        var workspace = File.ReadAllText(Path.Combine(TestRepositoryPaths.CompanionAppRoot, "src", "iRacingCoach.UI", "AnalysisWorkspacePage.razor"));
        StringAssert.Contains(workspace, """: Workspace.SessionType?.Contains("Qual", StringComparison.OrdinalIgnoreCase) != true);""");
        Assert.DoesNotContain(""": string.Equals(Workspace.SessionType, "Race", StringComparison.OrdinalIgnoreCase));""", workspace);
    }

    [TestMethod]
    public async Task EachRowNamesItsSetupAndSaysWhichLapsWereExcludedAndWhy()
    {
        // Track A 4 and 9, IRP 9/24: a 19-lap run excluded on every lap said so only on chart hover,
        // and rows read "Run 12" with no setup name.
        var fixture = Fixture();
        foreach (var lap in fixture["runs"]![1]!["laps"]!.AsArray().Skip(1))
        {
            lap!["eligible"] = false;
            lap["exclusion_reasons"] = new JsonArray("damage_repair_context");
        }
        var html = await RaceInvestigationPerformanceTests.Render<TestDayView>(new()
        {
            [nameof(TestDayView.Group)] = Day(),
            [nameof(TestDayView.Review)] = Map(fixture)
        });

        var row = Row(html, 2);
        StringAssert.Contains(row, "<b>Run 2</b><span class=\"td-run-setup\" title=\"fixture-stint-2.sto\">fixture-stint-2</span>");
        StringAssert.Contains(row, "17 of 18 laps excluded · tow/repair review");
        StringAssert.Contains(row, "Excluded from clean laps: tow/repair review (17 laps).");
        Assert.DoesNotContain("data-excluded", Row(html, 1), "Lap 1 of every run is excluded and is not repeated.");
        var detail = Tires(await RenderSelected(fixture, 2));
        StringAssert.Contains(detail, "<strong>Excluded laps</strong>");
    }

    [TestMethod]
    public async Task GarageOnlyRecordingsAreOneQuietLineNotRows()
    {
        var html = await RaceInvestigationPerformanceTests.Render<TestDayView>(new()
        {
            [nameof(TestDayView.Group)] = Day(),
            [nameof(TestDayView.Review)] = Map(Fixture())
        });

        StringAssert.Contains(html, "data-untimed=\"1\"");
        StringAssert.Contains(html, "1 recording without a timed lap (");
        StringAssert.Contains(html, ") are not compared.");
        Assert.AreEqual(3, Count(html, "<tr data-run="), "The garage-only recording is not a run row.");
        Assert.DoesNotContain("no timed run", html);

        var garageOnly = Fixture();
        garageOnly.Remove("short_runs");
        var alone = await RaceInvestigationPerformanceTests.Render<TestDayView>(new()
        {
            [nameof(TestDayView.Group)] = Day(),
            [nameof(TestDayView.Review)] = Map(garageOnly)
        });
        StringAssert.Contains(alone, ") is not compared.");
        Assert.DoesNotContain("clean laps (", alone);
    }

    [TestMethod]
    public async Task RunsTooShortToCompareJoinTheQuietLineAndAreNeverPlotted()
    {
        // IRP 9/24: the 22:43 recording's four stubs between resets (28.2 s, 25.1 s, 23.8 s laps and a stop)
        // were "Not comparable" rows and set the chart's time axis to 24–28.8 s.
        var fixture = Fixture();
        var shortRuns = fixture["short_runs"]!.AsArray();
        var stub = shortRuns[0]!.AsObject();
        stub["recorded_at"] = "2026-09-25T03:43:02+00:00";
        stub["runs_in_recording"] = 5;
        foreach (var place in new[] { 2, 3, 4 })
        {
            var copy = stub.DeepClone().AsObject();
            copy["run_in_recording"] = place;
            shortRuns.Add(copy);
        }
        var review = Map(fixture);
        var html = await RaceInvestigationPerformanceTests.Render<TestDayView>(new()
        {
            [nameof(TestDayView.Group)] = Day(),
            [nameof(TestDayView.Review)] = review
        });

        StringAssert.Contains(html, "data-short-runs=\"4\"");
        var line = TestDayView.NotComparedLine(review);
        StringAssert.Contains(line, "4 runs with fewer than 3 clean laps (");
        StringAssert.Contains(line, " runs 1–4 of 5) and 1 recording without a timed lap (");
        StringAssert.EndsWith(line, ") are not compared.");
        Assert.AreEqual(3, Count(html, "<tr data-run="), "A stub is never a run row.");
        Assert.AreEqual(3, Count(html, "class=\"ri-run-key\""), "A stub is never a line on the chart.");
    }

    [TestMethod]
    public void TheTimeAxisTrimsACleanLapFarOutsideTheRest()
    {
        // A clean 28.2 s lap among 23 s laps sits on the edge instead of stretching the axis to 28.8 s.
        var laps = new[] { 23.52, 23.31, 23.08, 22.89, 23.19, 23.24, 23.49, 23.52, 23.47, 23.36, 23.99, 28.226 };
        var (min, max) = RunOverlayChart.RobustRange(laps);
        Assert.AreEqual(22.89, min);
        Assert.AreEqual(23.99, max, "Every lap inside three interquartile ranges keeps its place, the slowest clean lap included.");
        Assert.AreEqual((23.0, 23.1), RunOverlayChart.RobustRange([23.0, 23.1, 23.05]), "Too few laps to trim: the whole range.");
        Assert.AreEqual((23.0, 23.0), RunOverlayChart.RobustRange([23.0, 23.0, 23.0, 23.0, 23.0]));
    }

    [TestMethod]
    public async Task AnInconsistentRepeatIsDrawnButDoesNotSetTheTimeAxis()
    {
        // A flagged run 1.5 s off the day's pace stretched the axis so the consistent runs' clean laps
        // flattened, while the caption promised the axis covered "the runs' clean laps".
        var fixture = Fixture();
        var flagged = fixture["runs"]![2]!.AsObject();
        flagged["consistency"] = new JsonObject
        {
            ["status"] = "inconsistent_repeat", ["setup_runs"] = new JsonArray(1, 3), ["reason"] = "Run 3 repeated run 1's setup 1.5 s slower."
        };
        foreach (var lap in flagged["laps"]!.AsArray()) lap!["lap_time_s"] = lap["lap_time_s"]!.GetValue<double>() + 1.5;
        var plotted = flagged["laps"]!.AsArray().Count;

        var html = await RaceInvestigationPerformanceTests.Render<TestDayView>(new()
        {
            [nameof(TestDayView.Group)] = Day(),
            [nameof(TestDayView.Review)] = Map(fixture)
        });

        Assert.AreEqual(plotted, Count(html, "data-off-scale=\"high\" data-run=\"3\""), "Every lap of the flagged run is drawn, on the edge under an off-scale mark.");
        Assert.AreEqual(plotted, Count(html, "data-off-scale="), "No lap of a consistent run leaves the axis.");
        StringAssert.Contains(html, "The time axis covers the clean laps of every run except run 3, flagged as an inconsistent repeat;");
        Assert.DoesNotContain("The time axis covers the runs' clean laps", TestDayView.ChartFootNote([3]));
        StringAssert.Contains(TestDayView.ChartFootNote([4, 2]), "except runs 2 and 4,");
        StringAssert.Contains(TestDayView.ChartFootNote([]), "The time axis covers the runs' clean laps;");
    }

    [TestMethod]
    public void EnterOnTheSelectedRowOpensItsRecordingAndSpaceOnlySelects()
    {
        Assert.AreEqual("select", TestDayView.RowKeyAction("Enter", selected: false, openable: true), "The first Enter selects the run for the chart.");
        Assert.AreEqual("open", TestDayView.RowKeyAction("Enter", selected: true, openable: true), "Enter on the selected row opens its recording, as a double-click does.");
        Assert.AreEqual("select", TestDayView.RowKeyAction("Enter", selected: true, openable: false), "Without a recording to open, Enter still toggles the row.");
        Assert.AreEqual("select", TestDayView.RowKeyAction(" ", selected: true, openable: true), "Space only toggles the selection.");
        Assert.AreEqual("none", TestDayView.RowKeyAction("ArrowDown", selected: true, openable: true));
    }

    [TestMethod]
    public void TheNoiseLineNamesBothEstimatesAndLapsThatWanderTogether()
    {
        var noise = new TestDayNoise("measured", 0.1304, 18, "these_runs", null)
        {
            DifferenceNoiseSeconds = 0.0566, ResidualNoiseSeconds = 0.1304, ResidualBasis = "these_runs", Autocorrelation = 0.403
        };
        var line = TestDayView.NoiseLine(noise);
        StringAssert.StartsWith(line, "Lap noise 0.130 s: the larger of lap-to-lap 0.057 s (18 pairs of consecutive clean laps of both runs after lap 5) and fall-off fit residual 0.130 s.");
        StringAssert.Contains(line, "Laps wander together (autocorrelation 0.40), so a window's laps count as fewer.");
        StringAssert.EndsWith(line, "never less than two other same-setup runs of the day differed by on the same laps.");
        StringAssert.StartsWith(TestDayView.NoiseLine(new TestDayNoise("measured", 0.021, 22, "these_runs", null)),
            "Lap-to-lap noise 0.021 s from 22 pairs", "An older backend's noise keeps its own wording.");
    }

    [TestMethod]
    public async Task TiresShowHotPressureFromTheMatchingSheetAndColdPressureAsTheSetting()
    {
        // Track A 5, IRP 9/24: "psi at stop" read 14.0/28.0, the set cold pressures; the stop's hot pressures
        // (16.7/34.2/17.7/33.8 psi) were in the next recording's garage sheet.
        var detail = Tires(await RenderSelected(Fixture(), 1));

        StringAssert.Contains(detail, "Hot psi · garage sheet loaded with run 2, matched to this stop");
        StringAssert.Contains(detail, "<dt>Hot psi</dt><dd>20.2 · sheet</dd></div>");
        StringAssert.Contains(detail, "<dt>Cold set psi</dt><dd>17.0 set</dd></div>");
        Assert.DoesNotContain("<dt>psi</dt>", detail, "A cold setting is never shown as the pressure at the stop.");

        var fixture = Fixture();
        var hot = fixture["runs"]![0]!["tires"]!["stop_reading"]!["hot_pressure"]!.AsObject();
        hot["status"] = "not_measured";
        hot["source"] = null;
        hot["matched_run"] = null;
        hot["reason"] = "The next recording's garage sheet does not match this stop's temperatures and tread, so its hot pressures are not attributed here.";
        foreach (var corner in fixture["runs"]![0]!["tires"]!["corners"]!.AsObject()) corner.Value!["stop"]!["hot_pressure_psi"] = null;
        var missing = Tires(await RenderSelected(fixture, 1));
        StringAssert.Contains(missing, "Hot psi · Not measured");
        StringAssert.Contains(missing, "<dt>Hot psi</dt><dd>Not measured</dd></div>");
    }

    [TestMethod]
    public async Task TheHeadlineChangeReadsInGarageUnitsAndAReturnSaysSo()
    {
        // IRP 9/25 v5: the row led with a 1 mm LF perch move in mm and cut "Left front camber +6.0 deg → +…"
        // at 1280 px; the file is named for the RR track bar, 13 → 12 in on the garage (+330 → +305 mm recorded).
        var fixture = Fixture();
        var change = fixture["runs"]![1]!["setup_change"]!.AsObject();
        change["status"] = "changed";
        change["changes"] = new JsonArray(
            Setting("chassis.left_front.spring_perch_offset", "Left front spring perch offset", "LF perch", "-95 mm", "-96 mm", "-3.75 in", "-3.78 in"),
            Setting("chassis.right_rear.track_bar_height", "Right rear track bar height", "RR track bar", "+330 mm", "+305 mm", "+13.00 in", "+12.00 in"));
        change["change_count"] = 2;
        change["headline"] = new JsonObject { ["index"] = 1, ["field"] = "chassis.right_rear.track_bar_height", ["basis"] = "setup_name", ["steps"] = 7.9 };
        var back = fixture["runs"]![2]!["setup_change"]!.AsObject();
        back["status"] = "changed";
        back["changes"] = new JsonArray(Setting("chassis.right_rear.track_bar_height", "Right rear track bar height", "RR track bar", "+305 mm", "+330 mm", "+12.00 in", "+13.00 in"));
        back["change_count"] = 1;
        back["returned_to_runs"] = new JsonArray(1);
        fixture["runs"]![2]!["consistency"] = new JsonObject
        {
            ["status"] = "inconsistent_repeat", ["setup_runs"] = new JsonArray(1, 3), ["delta_s"] = 0.7, ["limit_s"] = 0.526,
            ["reason"] = "Inconsistent repeat: run 3 used the garage setup of run 1 but its laps 2–5 were 0.700 s slower."
        };
        var review = Map(fixture);
        Assert.AreEqual("RR track bar", review.Runs[1].SetupChange.Headline?.ShortLabel);
        Assert.IsTrue(review.Runs[2].Consistency?.Inconsistent);

        var html = await RaceInvestigationPerformanceTests.Render<TestDayView>(new()
        {
            [nameof(TestDayView.Group)] = Day(),
            [nameof(TestDayView.Review)] = review
        });
        var two = Row(html, 2);
        StringAssert.Contains(two, "data-headline=\"chassis.right_rear.track_bar_height\"><b>RR track bar</b><span class=\"td-setup-values\">+13.00 in → +12.00 in</span>");
        StringAssert.Contains(two, "+1 more");
        Assert.DoesNotContain("-95 mm", two);
        var three = Row(html, 3);
        StringAssert.Contains(three, "<b>Back to run 1's setup</b>");
        StringAssert.Contains(three, "data-inconsistent-repeat");
        StringAssert.Contains(three, "Inconsistent repeat · pace changed");
        Assert.DoesNotContain("+1 more", three);
    }

    private static JsonObject Setting(string field, string label, string shortLabel, string previous, string current, string garagePrevious, string garageCurrent) => new()
    {
        ["field"] = field, ["label"] = label, ["short_label"] = shortLabel, ["previous"] = previous, ["current"] = current,
        ["garage_previous"] = garagePrevious, ["garage_current"] = garageCurrent, ["delta"] = -1.0, ["unit"] = "mm"
    };

    [TestMethod]
    public async Task TheNextChangeNamesTheValueToSetSoNoArrowPointsBack()
    {
        // Track A 6, IRP 9/24: the revert list read "-98 mm → -95 mm" beside evidence reading "-95 → -98".
        var fixture = Fixture();
        var next = fixture["next_change"]!.AsObject();
        next["kind"] = "revert";
        next["changes"] = new JsonArray(new JsonObject
        {
            ["field"] = "chassis.right_front.spring_perch_offset", ["label"] = "Right front spring perch offset", ["from"] = "-98 mm", ["to"] = "-95 mm"
        });
        var html = await RaceInvestigationPerformanceTests.Render<TestDayView>(new()
        {
            [nameof(TestDayView.Group)] = Day(),
            [nameof(TestDayView.Review)] = Map(fixture)
        });

        StringAssert.Contains(html, "<li><span>Right front spring perch offset</span><b>set -95 mm</b><small>now -98 mm</small></li>");
        Assert.DoesNotContain("-98 mm → -95 mm", html);
        var kept = await RaceInvestigationPerformanceTests.Render<TestDayView>(new()
        {
            [nameof(TestDayView.Group)] = Day(),
            [nameof(TestDayView.Review)] = Map(Fixture())
        });
        StringAssert.Contains(kept, "<li><span>Front brake bias</span><b>keep 56.5%</b></li>");
    }

    [TestMethod]
    public async Task InsideMinusOutsideCarriesItsUnitEverywhere()
    {
        // Track A 10: the table's inside-outside figure read only "°".
        var html = await RenderSelected(Fixture(), 1);
        StringAssert.Contains(Row(html, 1), "<i>LF</i><b>−69 °F</b>");
        StringAssert.Contains(Tires(html), "I−O −69 °F");
        Assert.DoesNotContain("°<", Row(html, 1).Replace("°F<", "", StringComparison.Ordinal), "No bare degree sign.");
        Assert.AreEqual("+7 °F", TestDayView.Degrees(7.2));
    }

    [TestMethod]
    public async Task TheDetailComparesBothWindowsAndTheFallOffAtEqualAge()
    {
        // Track A 2 and 3: the verdict used one window of laps 2-10; the detail now shows laps 2-5, the last
        // five laps both runs reached, the fall-off over the same laps and the noise the thresholds use.
        var detail = Tires(await RenderSelected(Fixture(), 3));

        StringAssert.Contains(detail, "data-pair-window=\"early\"");
        StringAssert.Contains(detail, "data-pair-window=\"late\"");
        StringAssert.Contains(detail, "data-pair-window=\"fall_off\"");
        StringAssert.Contains(detail, "Laps 12–16<small> · 5 laps</small>");
        StringAssert.Contains(detail, "Faster · beyond ±0.033");
        StringAssert.Contains(detail, "Lap noise 0.021 s: the larger of lap-to-lap 0.021 s (22 pairs of consecutive clean laps of both runs after lap 5) and fall-off fit residual 0.011 s.");
        StringAssert.Contains(detail, "Fall-off · laps 6–16", "Fall-off is compared after laps 2–5, where the tires come in.");
        StringAssert.Contains(detail, "runs 1 and 2 used one garage setup and differed by 0.010 s on these laps");

        var fixture = Fixture();
        var fallOff = fixture["runs"]![2]!["fall_off"]!.AsObject();
        fallOff["status"] = "not_measured";
        fallOff["slope_s_per_lap"] = null;
        fallOff["reason"] = "Too short for fall-off: the last clean lap is lap 7; 8 are needed so laps 2–5, where the tires come in, are not read as fall-off.";
        StringAssert.Contains(Row(await RenderSelected(fixture, 3), 3), ">Too short</span>", "A short run's warm-up is not reported as fall-off.");
    }

    [TestMethod]
    public async Task TheChartScalesTimeToCleanLapsSoOneExcludedLapCannotFlattenIt()
    {
        // Track A 7, IRP 9/24: an excluded 62.5 s lap stretched the time axis to 0–60 s.
        var fixture = Fixture();
        var laps = fixture["runs"]![2]!["laps"]!.AsArray();
        var slow = laps[4]!.AsObject();
        slow["lap_time_s"] = 62.558;
        slow["eligible"] = false;
        slow["exclusion_reasons"] = new JsonArray("stopped_on_track");
        var html = await RaceInvestigationPerformanceTests.Render<TestDayView>(new()
        {
            [nameof(TestDayView.Group)] = Day(),
            [nameof(TestDayView.Review)] = Map(fixture)
        });

        var axis = System.Text.RegularExpressions.Regex.Matches(html, "class=\"ri-axis\">([0-9.]+)</text>")
            .Select(match => double.Parse(match.Groups[1].Value, System.Globalization.CultureInfo.InvariantCulture)).Where(value => value > 20).ToArray();
        Assert.IsNotEmpty(axis);
        Assert.IsLessThan(25.0, axis.Max(), "The time axis spans the clean laps, not the excluded 62.5 s lap.");
        StringAssert.Contains(html, "off-scale", "The excluded lap sits on the edge, marked as off the scale.");
    }

    [TestMethod]
    public void TheTabScrollsAsOnePageWithAFixedChartAndNoBoxedTable()
    {
        // Track A 7 and 8: the chart shrank to ~20 px when the fit or a run's detail opened, the table sat in a
        // 42vh box with both scrollbars, and the detail was cut off at 1280x800.
        var css = File.ReadAllText(Path.Combine(TestRepositoryPaths.CompanionAppRoot, "src", "iRacingCoach.UI", "wwwroot", "race-investigation.css"));
        StringAssert.Contains(css, ".td-view { display: flex; flex-direction: column; gap: 8px; min-height: 0; height: 100%; overflow-y: auto;");
        StringAssert.Contains(css, "height: clamp(300px, 44vh, 420px);");
        StringAssert.Contains(css, ".td-chart .ri-plot { min-height: 150px; }");
        StringAssert.Contains(css, ".td-chart .ri-run-legend { flex: 1 1 auto; max-height: 58px; overflow-y: auto;");
        StringAssert.Contains(css, ".td-table { width: 100%; table-layout: fixed;");
        Assert.DoesNotContain("max-height: 42vh", css);
        var index = File.ReadAllText(Path.Combine(TestRepositoryPaths.CompanionAppRoot, "src", "iRacingCoach.App", "wwwroot", "index.html"));
        StringAssert.Contains(index, "race-investigation.css?v=0.18.0&amp;build=18");
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
