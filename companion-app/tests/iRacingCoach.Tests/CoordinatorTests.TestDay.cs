using System.Collections.Concurrent;
using System.Text.Json;
using System.Text.Json.Nodes;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

/// <summary>
/// Test day in the coordinator: discovery tags group stints without merging them,
/// opening a stint opens that stint and reviews its day, and background
/// maintenance prepares tagged stints like races.
/// </summary>
public sealed partial class CoordinatorTests
{
    private const string TestDayTag = "test-day-0123456789abcdef";

    private static object OfflineStint(string id, string start, int? run, int? count, string track = "New Hampshire Motor Speedway", string? file = null) => new
    {
        group_id = id, subsession_id = 0, session_id = 0, sim_session_type = "Offline Testing", event_type = "Test", is_race = false,
        valid = true, is_fixed_setup = false, track_display_name = track, track_config_name = "Oval", car_name = "Toyota Supra Class B",
        car_path = "stockcars2 supra2019", start_time_utc = start, file_count = 1, files = new[] { file ?? id + ".ibt" },
        test_day_id = run is null ? null : TestDayTag, test_day_run = run, test_day_run_count = count
    };

    private static JsonElement TestDayDiscovery(params object[] sessions) => JsonSerializer.SerializeToElement(new { sessions });

    [TestMethod]
    public void TestDayStintsShareOneRowGroupButKeepTheirOwnRecordingIdentity()
    {
        var discovery = TestDayDiscovery(
            OfflineStint("local:stint-b", "2026-08-06T23:50:00Z", 2, 3),
            OfflineStint("local:stint-a", "2026-08-06T23:00:00Z", 1, 3),
            OfflineStint("local:stint-c", "2026-08-07T00:35:00Z", 3, 3),
            OfflineStint("local:lone", "2026-08-02T18:30:00Z", null, null),
            new
            {
                group_id = "subsession:9001:2", subsession_id = 9001, session_id = 0, sim_session_type = "Race", event_type = "Race", is_race = true,
                valid = true, is_fixed_setup = false, track_display_name = "New Hampshire Motor Speedway", track_config_name = "Oval",
                car_name = "Toyota Supra Class B", car_path = "stockcars2 supra2019", start_time_utc = "2026-08-07T02:09:00Z", file_count = 1,
                files = new[] { "race.ibt" }, test_day_id = TestDayTag, test_day_run = 4, test_day_run_count = 4
            });

        var sessions = DashboardMapper.MapEvents(JsonSerializer.SerializeToElement(new { races = Array.Empty<object>() }), discovery);
        var groups = DashboardMapper.GroupEvents(sessions);

        Assert.HasCount(3, groups, "The test day is one group; the lone stint and the race stay separate.");
        var day = groups.Single(group => group.IsTestDay);
        Assert.AreEqual(TestDayTag, day.Id);
        CollectionAssert.AreEqual(new[] { "local:stint-a", "local:stint-b", "local:stint-c" }, day.Sessions.Select(session => session.Id).ToArray(),
            "A test day lists its stints in the order they were driven.");
        foreach (var stint in day.Sessions)
            Assert.AreEqual(stint.Id, stint.EventKey, "The tag must never become the EventKey: SameRace would treat every stint as one recording.");
        Assert.HasCount(3, day.Sessions.Select(session => session.EventKey).Distinct().ToArray());
        Assert.IsNull(sessions.Single(session => session.IsRace).TestDayId, "A race is never part of a test day, even if tagged.");
        Assert.IsFalse(groups.Single(group => group.Sessions.Any(session => session.Id == "local:lone")).IsTestDay);
    }

    [TestMethod]
    public async Task OpeningTestDayRunTwoOpensRunTwoAndReviewsTheDayInRunOrder()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-test-day-open", Guid.NewGuid().ToString("N"));
        var reports = Path.Combine(root, "data", "reports");
        string PathFor(string selector) => Path.Combine(reports, selector.Replace(':', '-'), "analysis.json");
        var analyzed = new ConcurrentQueue<string>();
        JsonElement? reviewArguments = null;
        var backend = new FakeBackend(
            discovery: TestDayDiscovery(
                OfflineStint("local:run-1", "2026-08-06T23:00:00Z", 1, 2),
                OfflineStint("local:run-2", "2026-08-06T23:50:00Z", 2, 2)),
            responseOverride: (tool, _, arguments) =>
            {
                var args = JsonSerializer.SerializeToElement(arguments);
                if (tool == "analyze_iracing_race")
                {
                    var selector = args.GetProperty("selector").GetString()!;
                    analyzed.Enqueue(selector);
                    Directory.CreateDirectory(Path.GetDirectoryName(PathFor(selector))!);
                    File.WriteAllText(PathFor(selector), "{}");
                    return HomeAnalysisResponse(PathFor(selector), "Offline Testing", selector);
                }
                if (tool == "review_test_day")
                {
                    reviewArguments = args;
                    return TestDayReviewFor(args.GetProperty("analysis_paths").EnumerateArray().Select(path => path.GetString()!).ToArray());
                }
                return null;
            });
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;
        await state.RefreshDashboardAsync();
        var runTwo = state.EventSessions.Single(session => session.Id == "local:run-2");

        await state.OpenRaceFromHomeAsync(runTwo);

        Assert.AreEqual("local:run-2", state.SelectedRaceSessionId, "Opening run 2 must open run 2, not the day's first stint.");
        Assert.IsNotNull(state.CurrentAnalysis);
        Assert.IsTrue(analyzed.Contains("local:run-2"));
        Assert.IsNotNull(state.CurrentTestDay, state.TestDayMessage);
        Assert.AreEqual(TestDayTag, state.CurrentTestDayId);
        CollectionAssert.AreEqual(new[] { PathFor("local:run-1"), PathFor("local:run-2") },
            reviewArguments!.Value.GetProperty("analysis_paths").EnumerateArray().Select(path => path.GetString()).ToArray(),
            "The review receives every stint's own analysis, oldest first.");
        var runOne = state.EventSessions.Single(session => session.Id == "local:run-1");
        runTwo = state.EventSessions.Single(session => session.Id == "local:run-2");
        Assert.AreEqual(PathFor("local:run-1"), runOne.AnalysisPath, "Run 2's analysis must not be stamped onto run 1.");
        Assert.AreEqual(PathFor("local:run-2"), runTwo.AnalysisPath);
        var day = state.TestDayGroupFor(runTwo)!;
        Assert.AreEqual("local:run-1", state.TestDaySession(day, state.CurrentTestDay!.Runs[0])?.Id);
        Assert.AreEqual("local:run-2", state.TestDaySession(day, state.CurrentTestDay.Runs[1])?.Id);

        var reviews = backend.CallsFor("review_test_day");
        await state.OpenRaceFromHomeAsync(state.EventSessions.Single(session => session.Id == "local:run-1"));
        Assert.AreEqual("local:run-1", state.SelectedRaceSessionId);
        Assert.AreEqual(reviews, backend.CallsFor("review_test_day"), "Switching stints of an unchanged day keeps the loaded review.");
    }

    private static RecentRace CappedStint(int run, int? laps) => new(
        $"local:run-{run}", "New Hampshire Motor Speedway", "Oval", "Toyota Supra Class B", "2026-09-24", "Open", "Analyzed", "", false, laps is not null, 0, 0,
        StartTimeUtc: new DateTimeOffset(2026, 9, 25, 2, 19, 0, TimeSpan.Zero).AddMinutes(5 * (run - 1)).ToString("O"),
        SessionType: "Offline Testing", Overview: laps is { } count ? new RaceOverview(RecordedLaps: count) : null,
        TestDayId: TestDayTag, TestDayRunNumber: run, TestDayRunCount: 13);

    [TestMethod]
    public void ThirteenRecordingDayDropsAStubBeforeTheRecordingItWasOpenedFrom()
    {
        // Joshua's 13-recording day: run 1 is the untimed 10:19 PM stub the day was opened from,
        // run 3 another stub. Keeping "the latest twelve" dropped run 1 from its own Test day.
        var day = Enumerable.Range(1, 13).Select(run => CappedStint(run, run is 1 or 3 ? 0 : 20)).ToArray();

        var (kept, leftOut) = CompanionState.ChooseTestDayRecordings(day, "local:run-1");

        Assert.HasCount(CompanionState.MaximumTestDayRecordings, kept);
        Assert.IsTrue(kept.Any(session => session.Id == "local:run-1"), "The recording the day was opened from always stays.");
        CollectionAssert.AreEqual(new[] { "local:run-3" }, leftOut.Select(session => session.Id).ToArray(), "A stub leaves before any timed recording.");
        CollectionAssert.AreEqual(day.Where(session => session.Id != "local:run-3").Select(session => session.Id).ToArray(),
            kept.Select(session => session.Id).ToArray(), "Kept recordings stay in driving order.");
        var message = CompanionState.TestDayLeftOutMessage(kept.Count, leftOut);
        StringAssert.StartsWith(message, "Showing 12 of 13 recordings, the most one review can compare. ");
        StringAssert.Contains(message, "(no timed lap)", "The message names what was left out and why.");

        var noStubs = Enumerable.Range(1, 13).Select(run => CappedStint(run, 20)).ToArray();
        var (keptTimed, leftTimed) = CompanionState.ChooseTestDayRecordings(noStubs, "local:run-1");
        CollectionAssert.AreEqual(new[] { "local:run-2" }, leftTimed.Select(session => session.Id).ToArray(),
            "Without stubs the oldest recording other than the opened one leaves.");
        Assert.IsTrue(keptTimed.Any(session => session.Id == "local:run-1"));
        Assert.AreEqual(string.Empty, CompanionState.TestDayLeftOutMessage(12, []), "Nothing left out, nothing said.");
    }

    [TestMethod]
    public async Task OpeningTheOldestRecordingOfAThirteenRecordingDayReviewsIt()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-test-day-cap", Guid.NewGuid().ToString("N"));
        string PathFor(string selector) => Path.Combine(root, "data", "reports", selector.Replace(':', '-'), "analysis.json");
        JsonElement? reviewArguments = null;
        var stints = Enumerable.Range(1, 13)
            .Select(run => OfflineStint($"local:run-{run}", new DateTimeOffset(2026, 9, 25, 2, 19, 0, TimeSpan.Zero).AddMinutes(5 * (run - 1)).ToString("O"), run, 13))
            .ToArray();
        var backend = new FakeBackend(
            discovery: TestDayDiscovery(stints),
            responseOverride: (tool, _, arguments) =>
            {
                var args = JsonSerializer.SerializeToElement(arguments);
                if (tool == "analyze_iracing_race")
                {
                    var selector = args.GetProperty("selector").GetString()!;
                    Directory.CreateDirectory(Path.GetDirectoryName(PathFor(selector))!);
                    File.WriteAllText(PathFor(selector), "{}");
                    return HomeAnalysisResponse(PathFor(selector), "Offline Testing", selector);
                }
                if (tool == "review_test_day")
                {
                    reviewArguments = args;
                    return TestDayReviewFor(args.GetProperty("analysis_paths").EnumerateArray().Select(path => path.GetString()!).ToArray());
                }
                return null;
            });
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;
        await state.RefreshDashboardAsync();

        await state.OpenRaceFromHomeAsync(state.EventSessions.Single(session => session.Id == "local:run-1"));

        Assert.IsNotNull(reviewArguments, state.TestDayMessage);
        var paths = reviewArguments!.Value.GetProperty("analysis_paths").EnumerateArray().Select(path => path.GetString()).ToArray();
        Assert.HasCount(12, paths);
        Assert.AreEqual(PathFor("local:run-1"), paths[0], "The recording the day was opened from is in its own Test day.");
        CollectionAssert.DoesNotContain(paths, PathFor("local:run-2"));
        StringAssert.Contains(state.TestDayMessage, "Left out:", "The day says which recording it left out.");
    }

    [TestMethod]
    public async Task TheSelectedTestDayRowLinksToItsRecording()
    {
        // Clicking a row only selected it for the chart; the recording's link sat below the table.
        var fixture = TestDayMapperTests.Fixture();
        var runs = fixture["runs"]!.AsArray();
        var sessions = new List<RecentRace>();
        for (var index = 0; index < runs.Count; index++)
        {
            var path = Path.Combine(Path.GetTempPath(), "iracing-coach-test-day-row", $"run-{index + 1}", "analysis.json");
            runs[index]!["analysis_path"] = path;
            sessions.Add(new RecentRace($"local:row-{index + 1}", "Synthetic Speedway", "Oval", "Synthetic car", "Aug 4", "Open", "Analyzed", "", false, true, 0, 0,
                AnalysisPath: path, SessionType: "Offline Testing", EventKey: $"local:row-{index + 1}", TestDayId: TestDayTag,
                TestDayRunNumber: index + 1, TestDayRunCount: runs.Count));
        }
        using var state = new CompanionState(new FakeBackend());
        state.EventSessions.AddRange(sessions);
        var day = new RaceEventGroup(TestDayTag, "Synthetic Speedway", "Oval", "Synthetic car", "Aug 4", "Open", string.Empty, sessions);
        var opened = new List<string>();
        async Task<string> RenderWith(int? selected, string openSession) => await RaceInvestigationPerformanceTests.Render<iRacingCoach.UI.TestDayView>(new()
        {
            [nameof(iRacingCoach.UI.TestDayView.Group)] = day,
            [nameof(iRacingCoach.UI.TestDayView.Review)] = TestDayMapperTests.Map(fixture),
            [nameof(iRacingCoach.UI.TestDayView.State)] = state,
            [nameof(iRacingCoach.UI.TestDayView.OpenSessionId)] = openSession,
            [nameof(iRacingCoach.UI.TestDayView.SelectedRunNumber)] = selected,
            [nameof(iRacingCoach.UI.TestDayView.OnOpenRun)] = Microsoft.AspNetCore.Components.EventCallback.Factory.Create<RecentRace>(this, (RecentRace race) => opened.Add(race.Id))
        });

        var html = await RenderWith(2, "local:row-1");
        Assert.AreEqual(1, Count(html, "data-open-run="), "Only the selected row carries the link.");
        var row = html[html.IndexOf("data-run=\"2\" tabindex=", StringComparison.Ordinal)..];
        row = row[..row.IndexOf("</tr>", StringComparison.Ordinal)];
        StringAssert.Contains(row, "data-open-run=\"2\"");
        StringAssert.Contains(row, ">Open recording</button>");
        StringAssert.Contains(row, "double-click, or press Enter on the selected row, to open its recording", "The row names its keyboard and double-click path.");

        var alreadyOpen = await RenderWith(1, "local:row-1");
        Assert.AreEqual(0, Count(alreadyOpen, "data-open-run="), "The open recording's row says open instead of linking to itself.");
    }

    private static int Count(string text, string value)
    {
        var count = 0;
        for (var index = text.IndexOf(value, StringComparison.Ordinal); index >= 0; index = text.IndexOf(value, index + value.Length, StringComparison.Ordinal)) count++;
        return count;
    }

    [TestMethod]
    public async Task BackgroundAnalysisPreparesTestDayStintsButNotLonePractice()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-test-day-background", Guid.NewGuid().ToString("N"));
        string PathFor(string selector) => Path.Combine(root, "data", "reports", selector.Replace(':', '-'), "analysis.json");
        var analyzed = new ConcurrentQueue<string>();
        var backend = new FakeBackend(
            discovery: TestDayDiscovery(
                OfflineStint("local:run-1", "2026-08-06T23:00:00Z", 1, 2),
                OfflineStint("local:run-2", "2026-08-06T23:50:00Z", 2, 2),
                OfflineStint("local:lone", "2026-08-02T18:30:00Z", null, null)),
            responseOverride: (tool, _, arguments) =>
            {
                if (tool != "analyze_iracing_race") return null;
                var selector = JsonSerializer.SerializeToElement(arguments).GetProperty("selector").GetString()!;
                analyzed.Enqueue(selector);
                return HomeAnalysisResponse(PathFor(selector), "Offline Testing", selector);
            });
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;

        await state.RefreshDashboardAsync();
        await WaitUntilAsync(() => state.EventSessions.Where(session => session.IsTestDayStint).All(session => session.Analyzed), TimeSpan.FromSeconds(4));
        await Task.Delay(150);

        CollectionAssert.AreEquivalent(new[] { "local:run-1", "local:run-2" }, analyzed.ToArray(),
            "Tagged stints are prepared once each; practice outside a test day still waits to be opened.");
        Assert.IsFalse(state.EventSessions.Single(session => session.Id == "local:lone").Analyzed);
        foreach (var stint in state.EventSessions.Where(session => session.IsTestDayStint))
            Assert.AreEqual(PathFor(stint.Id), stint.AnalysisPath, "Each stint keeps its own analysis.");
        Assert.IsEmpty(state.Jobs, "Preparing stints is quiet maintenance, not a job.");
    }

    [TestMethod]
    public async Task BackgroundAnalysisWaitsWhileATestDayIsBeingReviewed()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-test-day-yield", Guid.NewGuid().ToString("N"));
        string PathFor(string selector) => Path.Combine(root, "data", "reports", selector.Replace(':', '-'), "analysis.json");
        var releaseReview = new TaskCompletionSource();
        var reviewStarted = new TaskCompletionSource();
        var newRaceRecorded = false;
        var stints = new[] { OfflineStint("local:run-1", "2026-08-06T23:00:00Z", 1, 2), OfflineStint("local:run-2", "2026-08-06T23:50:00Z", 2, 2) };
        var race = new
        {
            group_id = "subsession:9101:1", subsession_id = 9101, session_id = 8101, sim_session_type = "Race", event_type = "Race", is_race = true,
            valid = true, is_fixed_setup = true, track_name = "Recorded Track", track_config_name = "Oval", car_path = "recorded-car",
            start_time_utc = "2026-08-07T02:00:00Z", file_count = 1, files = new[] { "race.ibt" }
        };
        var backend = new FakeBackend(responseOverride: (tool, _, arguments) =>
        {
            var args = JsonSerializer.SerializeToElement(arguments);
            switch (tool)
            {
                case "discover_iracing_sessions":
                    return Volatile.Read(ref newRaceRecorded) ? TestDayDiscovery([.. stints, race]) : TestDayDiscovery(stints);
                case "analyze_iracing_race":
                    var selector = args.GetProperty("selector").GetString()!;
                    Directory.CreateDirectory(Path.GetDirectoryName(PathFor(selector))!);
                    File.WriteAllText(PathFor(selector), "{}");
                    return HomeAnalysisResponse(PathFor(selector), selector.StartsWith("local:", StringComparison.Ordinal) ? "Offline Testing" : "Race", selector);
                case "review_test_day":
                    reviewStarted.TrySetResult();
                    releaseReview.Task.Wait(TimeSpan.FromSeconds(5));
                    return TestDayReviewFor(args.GetProperty("analysis_paths").EnumerateArray().Select(path => path.GetString()!).ToArray());
                default:
                    return null;
            }
        });
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;
        await state.RefreshDashboardAsync();
        await WaitUntilAsync(() => state.EventSessions.Count(session => session.IsTestDayStint && session.Analyzed) == 2, TimeSpan.FromSeconds(4));

        var day = state.TestDayGroupFor(state.EventSessions.First(session => session.IsTestDayStint))!;
        var review = Task.Run(() => state.LoadTestDayAsync(day));
        await reviewStarted.Task.WaitAsync(TimeSpan.FromSeconds(3));
        Assert.IsTrue(state.TestDayLoading);
        var analyzeCalls = backend.AnalyzeCalls;
        Volatile.Write(ref newRaceRecorded, true);
        await state.RefreshDashboardAsync();
        await Task.Delay(400);
        Assert.AreEqual(analyzeCalls, backend.AnalyzeCalls, "Maintenance yields while the driver's test-day review runs.");

        releaseReview.SetResult();
        await review;
        Assert.IsFalse(state.TestDayLoading);
        Assert.IsNotNull(state.CurrentTestDay);
        await WaitUntilAsync(() => backend.AnalyzeCalls == analyzeCalls + 1, TimeSpan.FromSeconds(3));
    }

    [TestMethod]
    public async Task TestDayReviewDoesNotWaitForAParkedBackgroundRetry()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-test-day-retry", Guid.NewGuid().ToString("N"));
        string PathFor(string selector) => Path.Combine(root, "data", "reports", selector.Replace(':', '-'), "analysis.json");
        var reviewCalls = 0;
        // Background maintenance's first attempt fails (an IBT still being written, a timeout);
        // its retry then yields to the driver's test-day review.
        var backend = new FakeBackend(
            discovery: TestDayDiscovery(
                OfflineStint("local:run-1", "2026-08-06T23:00:00Z", 1, 2),
                OfflineStint("local:run-2", "2026-08-06T23:50:00Z", 2, 2)),
            analysisDelay: TimeSpan.FromMilliseconds(600),
            analysisFailuresBeforeSuccess: 1,
            responseOverride: (tool, _, arguments) =>
            {
                var args = JsonSerializer.SerializeToElement(arguments);
                if (tool == "analyze_iracing_race")
                {
                    var selector = args.GetProperty("selector").GetString()!;
                    Directory.CreateDirectory(Path.GetDirectoryName(PathFor(selector))!);
                    File.WriteAllText(PathFor(selector), "{}");
                    return HomeAnalysisResponse(PathFor(selector), "Offline Testing", selector);
                }
                if (tool != "review_test_day") return null;
                Interlocked.Increment(ref reviewCalls);
                return TestDayReviewFor(args.GetProperty("analysis_paths").EnumerateArray().Select(path => path.GetString()!).ToArray());
            });
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;
        await state.RefreshDashboardAsync();
        await WaitUntilAsync(() => backend.AnalyzeCalls >= 1, TimeSpan.FromSeconds(3));
        var day = state.TestDayGroupFor(state.EventSessions.First(session => session.IsTestDayStint))!;

        var load = state.LoadTestDayAsync(day);
        var finished = await Task.WhenAny(load, Task.Delay(TimeSpan.FromSeconds(10))) == load;

        Assert.IsTrue(finished, $"The review waited for a background retry that waits for the review ({state.TestDayMessage}).");
        await load;
        Assert.AreEqual(1, Volatile.Read(ref reviewCalls));
        Assert.IsNotNull(state.CurrentTestDay, state.TestDayMessage);
        Assert.IsFalse(state.TestDayLoading);
        // The parked retry finds the analysis the review wrote instead of analyzing the stint again.
        await WaitUntilAsync(() => state.EventSessions.Where(session => session.IsTestDayStint).All(session => session.Analyzed), TimeSpan.FromSeconds(4));
        await Task.Delay(400);
        Assert.AreEqual(3, backend.AnalyzeCalls, "One failed background attempt, then one analysis per stint.");
    }

    [TestMethod]
    public async Task ReturningToADayWhoseReviewIsStillRunningShowsThatReview()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-test-day-return", Guid.NewGuid().ToString("N"));
        string PathFor(string selector) => Path.Combine(root, "data", "reports", selector.Replace(':', '-'), "analysis.json");
        var releaseDayA = new TaskCompletionSource();
        var dayAStarted = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        const string otherDay = "test-day-fedcba9876543210";
        object Stint(string id, string start, int run, string tag) => new
        {
            group_id = id, subsession_id = 0, session_id = 0, sim_session_type = "Offline Testing", event_type = "Test", is_race = false,
            valid = true, is_fixed_setup = false, track_display_name = tag == TestDayTag ? "New Hampshire Motor Speedway" : "Iowa Speedway",
            track_config_name = "Oval", car_name = "Toyota Supra Class B", car_path = "stockcars2 supra2019", start_time_utc = start,
            file_count = 1, files = new[] { id + ".ibt" }, test_day_id = tag, test_day_run = run, test_day_run_count = 2
        };
        var backend = new FakeBackend(
            discovery: TestDayDiscovery(
                Stint("local:a-1", "2026-08-06T23:00:00Z", 1, TestDayTag), Stint("local:a-2", "2026-08-06T23:50:00Z", 2, TestDayTag),
                Stint("local:b-1", "2026-08-08T23:00:00Z", 1, otherDay), Stint("local:b-2", "2026-08-08T23:50:00Z", 2, otherDay)),
            responseOverride: (tool, _, arguments) =>
            {
                var args = JsonSerializer.SerializeToElement(arguments);
                if (tool == "analyze_iracing_race")
                {
                    var selector = args.GetProperty("selector").GetString()!;
                    Directory.CreateDirectory(Path.GetDirectoryName(PathFor(selector))!);
                    File.WriteAllText(PathFor(selector), "{}");
                    return HomeAnalysisResponse(PathFor(selector), "Offline Testing", selector);
                }
                if (tool != "review_test_day") return null;
                var paths = args.GetProperty("analysis_paths").EnumerateArray().Select(path => path.GetString()!).ToArray();
                if (paths[0].Contains("a-1", StringComparison.Ordinal))
                {
                    dayAStarted.TrySetResult();
                    releaseDayA.Task.Wait(TimeSpan.FromSeconds(5));
                }
                return TestDayReviewFor(paths);
            });
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;
        await state.RefreshDashboardAsync();
        await WaitUntilAsync(() => state.EventSessions.All(session => session.Analyzed), TimeSpan.FromSeconds(4));
        RaceEventGroup Day(string tag) => state.EventGroups.OfType<RaceEventGroup>().Single(group => group.TestDayId == tag);

        var firstVisit = Task.Run(() => state.LoadTestDayAsync(Day(TestDayTag)));
        await dayAStarted.Task.WaitAsync(TimeSpan.FromSeconds(3));
        await state.LoadTestDayAsync(Day(otherDay));
        Assert.AreEqual(otherDay, state.CurrentTestDayId);
        Assert.IsNotNull(state.CurrentTestDay);

        var secondVisit = state.LoadTestDayAsync(Day(TestDayTag));
        Assert.IsTrue(state.TestDayLoading, "Day A is still being compared.");
        Assert.AreEqual(2, backend.CallsFor("review_test_day"), "Day A's first review and day B's; returning to day A starts no second one.");
        releaseDayA.SetResult();
        await Task.WhenAll(firstVisit, secondVisit);

        Assert.AreEqual(TestDayTag, state.CurrentTestDayId);
        Assert.IsNotNull(state.CurrentTestDay, state.TestDayMessage);
        Assert.AreEqual(string.Empty, state.TestDayMessage, "The first visit's review is shown, not a failure.");
        Assert.IsFalse(state.TestDayLoading);
        Assert.AreEqual(2, backend.CallsFor("review_test_day"));
        Assert.IsFalse(state.Jobs.Any(job => job.Status == "failed"));
    }

    [TestMethod]
    public async Task APracticeRecordingWithSeveralRunsIsReviewedRunByRun()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-test-day-practice", Guid.NewGuid().ToString("N"));
        var analysisPath = Path.Combine(root, "data", "reports", "practice", "analysis.json");
        string[]? reviewed = null;
        var practice = new
        {
            group_id = "subsession:77001:0", subsession_id = 77001, session_id = 0, sim_session_num = 0, sim_session_type = "Practice",
            event_type = "Practice", is_race = false, valid = true, is_fixed_setup = false, track_display_name = "Indianapolis Raceway Park",
            track_config_name = "Oval", car_name = "Toyota Supra Class B", car_path = "stockcars2 supra2019", start_time_utc = "2026-09-23T20:34:00Z",
            file_count = 2, files = new[] { "irp-1.ibt", "irp-2.ibt" }
        };
        var backend = new FakeBackend(
            discovery: TestDayDiscovery(practice),
            responseOverride: (tool, _, arguments) =>
            {
                var args = JsonSerializer.SerializeToElement(arguments);
                if (tool == "analyze_iracing_race")
                {
                    Directory.CreateDirectory(Path.GetDirectoryName(analysisPath)!);
                    File.WriteAllText(analysisPath, "{}");
                    // One recording, split into two runs by a pit visit.
                    var response = JsonNode.Parse(HomeAnalysisResponse(analysisPath, "Practice", "subsession:77001:0").GetRawText())!.AsObject();
                    var runs = response["analysis_view"]!["runs"]!.AsArray();
                    runs.Add(runs[0]!.DeepClone());
                    return JsonSerializer.SerializeToElement(response);
                }
                if (tool != "review_test_day") return null;
                reviewed = args.GetProperty("analysis_paths").EnumerateArray().Select(path => path.GetString()!).ToArray();
                var review = JsonNode.Parse(TestDayReviewFor([analysisPath, analysisPath]).GetRawText())!;
                return JsonSerializer.SerializeToElement(review);
            });
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;
        await state.RefreshDashboardAsync();
        var session = state.EventSessions.Single();
        Assert.IsFalse(session.IsTestDayStint, "A lone practice is not tagged by discovery.");

        await state.OpenRaceFromHomeAsync(session);

        var day = state.TestDayGroupFor(session);
        Assert.IsNotNull(day, "A practice recording with two runs opens a Test day of its runs.");
        Assert.AreEqual("session:subsession:77001:0", day.TestDayKey);
        Assert.IsFalse(day.IsTestDay, "It is one recording, not a day of several.");
        CollectionAssert.AreEqual(new[] { analysisPath }, reviewed, "The one recording is reviewed; its runs are the rows.");
        Assert.AreEqual(day.TestDayKey, state.CurrentTestDayId);
        Assert.IsNotNull(state.TestDayReviewFor(day), state.TestDayMessage);
        Assert.AreEqual(session.Id, state.TestDaySession(day, state.CurrentTestDay!.Runs[1])?.Id, "Every run of the recording belongs to it.");
    }

    private static string ArchivedStintReport(string analysisId, string groupId, string start, int subsession = 0) => $$$"""
        {
          "analysis_id":"{{{analysisId}}}",
          "identity":{"event_type":"Test","subsession_id":{{{subsession}}},"session_start":"{{{start}}}","track_name":"New Hampshire Motor Speedway",
                      "track_config":"Oval","car_name":"Toyota Supra Class B","car_path":"stockcars2 supra2019","is_fixed_setup":false},
          "source":{"selection":{"group_id":"{{{groupId}}}","subsession_id":{{{subsession}}},"session_id":0,"sim_session_type":"Offline Testing"}},
          "race_summary":{"recorded_laps":12},
          "laps":[],"runs":[],
          "lap_traces":{"traces":[]},
          "track_profile":{"shape":[],"detected_corner_segments":[]},
          "strategy":{},"damage_repair":{},"data_quality":{}
        }
        """;

    [TestMethod]
    public void ArchivedOfflineRecordingsNeverShareAnEventKey()
    {
        using var first = JsonDocument.Parse(ArchivedStintReport("stint-a", "local:run-1", "2026-08-06T23:00:00Z"));
        using var second = JsonDocument.Parse(ArchivedStintReport("stint-b", "local:run-2", "2026-08-06T23:50:00Z"));
        using var hosted = JsonDocument.Parse(ArchivedStintReport("hosted", "subsession:9001:0", "2026-08-06T21:00:00Z", subsession: 9001));

        var a = RuntimeMapper.ArchivedRace(first.RootElement, @"C:\Coach\reports\a\analysis.json");
        var b = RuntimeMapper.ArchivedRace(second.RootElement, @"C:\Coach\reports\b\analysis.json");

        Assert.AreEqual("local:run-1", a.EventKey, "Subsession 0 is not an event; the recording is its own key.");
        Assert.AreEqual("local:run-2", b.EventKey);
        Assert.AreEqual("9001", RuntimeMapper.ArchivedRace(hosted.RootElement, @"C:\Coach\reports\h\analysis.json").EventKey);
        var groups = DashboardMapper.GroupEvents([a, b]);
        Assert.HasCount(2, groups, "Two orphaned offline analyses are two rows, not one event keyed \"0\".");
    }

    [TestMethod]
    public async Task ArchivedOfflineStintsKeepTheirOwnAnalysisAcrossRefreshes()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-test-day-archived", Guid.NewGuid().ToString("N"));
        var reports = Path.Combine(root, "data", "reports", "season-unknown");
        var pathA = Path.Combine(reports, "stint-a", "analysis.json");
        var pathB = Path.Combine(reports, "stint-b", "analysis.json");
        Directory.CreateDirectory(Path.GetDirectoryName(pathA)!);
        Directory.CreateDirectory(Path.GetDirectoryName(pathB)!);
        File.WriteAllText(pathA, ArchivedStintReport("stint-a", "local:run-1", "2026-08-06T23:00:00Z"));
        File.WriteAllText(pathB, ArchivedStintReport("stint-b", "local:run-2", "2026-08-06T23:50:00Z"));
        // Stint B was written last, so the archived catalog lists it first.
        File.SetLastWriteTimeUtc(pathA, DateTime.UtcNow.AddMinutes(-10));
        File.SetLastWriteTimeUtc(pathB, DateTime.UtcNow.AddMinutes(-5));
        // Both recordings are still in the telemetry folder, so their archived summaries are not re-read;
        // they wait for background maintenance, which waits while the driver is in the sim.
        var ibtA = Path.Combine(root, "telemetry", "a.ibt");
        var ibtB = Path.Combine(root, "telemetry", "b.ibt");
        Directory.CreateDirectory(Path.GetDirectoryName(ibtA)!);
        File.WriteAllText(ibtA, "ibt");
        File.WriteAllText(ibtB, "ibt");
        string[]? reviewed = null;
        var source = new SwitchableLiveTelemetrySource { Connected = true };
        var backend = new FakeBackend(
            discovery: TestDayDiscovery(
                OfflineStint("local:run-2", "2026-08-06T23:50:00Z", 2, 2, file: ibtB),
                OfflineStint("local:run-1", "2026-08-06T23:00:00Z", 1, 2, file: ibtA)),
            responseOverride: (tool, _, arguments) =>
            {
                if (tool != "review_test_day") return null;
                reviewed = JsonSerializer.SerializeToElement(arguments).GetProperty("analysis_paths").EnumerateArray().Select(path => path.GetString()!).ToArray();
                return TestDayReviewFor(reviewed);
            });
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")), source,
            new DisabledCoachEngineSupervisor(), new FakeGarage61CredentialStore(Path.Combine(root, "garage61.credential")));
        state.Settings.CoachHome = root;
        await state.InitializeAsync();
        await WaitUntilAsync(() => state.LiveState.Snapshot.Connected, TimeSpan.FromSeconds(2));

        for (var refresh = 0; refresh < 3; refresh++) { await state.RefreshDashboardAsync(); foreach (var r in state.Races) Console.WriteLine($"R {r.Id} key={r.EventKey} type={r.SessionType} ov={r.Overview is not null} an={r.Analyzed} path={r.AnalysisPath}"); foreach (var r in state.EventSessions) Console.WriteLine($"E {r.Id} key={r.EventKey} path={r.AnalysisPath}"); }

        foreach (var (id, path) in new[] { ("local:run-1", pathA), ("local:run-2", pathB) })
        {
            Assert.AreEqual(path, state.EventSessions.Single(session => session.Id == id).AnalysisPath, $"{id} must keep its own analysis.");
            Assert.AreEqual(path, state.Races.Single(race => race.Id == id).AnalysisPath, $"The archived entry for {id} must keep its own analysis.");
        }
        var day = state.TestDayGroupFor(state.EventSessions.First(session => session.IsTestDayStint))!;
        await state.LoadTestDayAsync(day);
        CollectionAssert.AreEqual(new[] { pathA, pathB }, reviewed, "Each run is reviewed from its own analysis.");
        Assert.AreEqual(0, backend.AnalyzeCalls);
    }

    /// <summary>The backend fixture cut to one row per requested stint, pointing at those stints' analyses.</summary>
    [TestMethod]
    public async Task DescribeHandlingOpensTheLatestStintsRecordedSetupOnceTheDaysJobHasAnalyzedIt()
    {
        // Track A 9, IRP 9/24: the link was missing because the stints the day's job analyzed joined the
        // tuning evidence only at the next inventory refresh, and when it did appear it chose the .sto
        // file, whose file fingerprint stopped tuning at "The race setup fingerprint changed".
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-test-day-handling", Guid.NewGuid().ToString("N"));
        var reports = Path.Combine(root, "data", "reports");
        string PathFor(string selector) => Path.Combine(reports, selector.Replace(':', '-'), "analysis.json");
        var backend = new FakeBackend(
            discovery: TestDayDiscovery(
                OfflineStint("local:run-1", "2026-08-06T23:00:00Z", 1, 2),
                OfflineStint("local:run-2", "2026-08-06T23:50:00Z", 2, 2)),
            responseOverride: (tool, _, arguments) =>
            {
                var args = JsonSerializer.SerializeToElement(arguments);
                if (tool == "analyze_iracing_race")
                {
                    var selector = args.GetProperty("selector").GetString()!;
                    Directory.CreateDirectory(Path.GetDirectoryName(PathFor(selector))!);
                    File.WriteAllText(PathFor(selector), JsonSerializer.Serialize(new
                    {
                        analysis_id = selector, analysis_profile_version = "post-race-foundations-v23",
                        identity = new
                        {
                            track_name = "New Hampshire Motor Speedway", event_type = "Test", car_path = "stockcars2 supra2019",
                            setup_name = "claude test v4.sto", setup_fingerprint = "recorded-" + selector,
                            setup = new { Chassis = new { Front = new { CrossWeight = "51.5%" } } }
                        },
                        race_summary = new { recorded_laps = 7 }
                    }));
                    return HomeAnalysisResponse(PathFor(selector), "Offline Testing", selector);
                }
                if (tool == "review_test_day")
                    return TestDayReviewFor(args.GetProperty("analysis_paths").EnumerateArray().Select(path => path.GetString()!).ToArray());
                return null;
            });
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;
        await state.RefreshDashboardAsync();
        // The library's .sto file linked to the same recording carries only its file bytes' fingerprint.
        state.Setups.Add(new LocalSetup("file:C:/SETUPS/CLAUDE TEST V4.STO", "claude test v4", "Supra", "New Hampshire", "User",
            @"C:\setups\claude test v4.sto", "sto-file-sha256", "sto_only", "Local setup file.", [], AnalysisPaths: [PathFor("local:run-2")]));

        await state.OpenRaceFromHomeAsync(state.EventSessions.Single(session => session.Id == "local:run-2"));

        Assert.IsNotNull(state.CurrentTestDay, state.TestDayMessage);
        var target = state.HandlingTargetFor(state.CurrentTestDay.Runs[^1]);
        Assert.IsNotNull(target, "The day's job makes its analyzed stints tuning evidence without waiting for a refresh.");
        Assert.AreEqual("recorded-local:run-2", target.SetupId, "The recorded setup, whose fingerprint tuning checks, not the .sto file.");
        Assert.IsTrue(state.Races.Any(race => race.AnalysisPath == PathFor("local:run-2")));
        Assert.IsNull(state.HandlingTargetFor(state.CurrentTestDay.Runs[^1] with { AnalysisPath = @"C:\elsewhere\analysis.json" }),
            "No dead link for a run without recorded evidence.");
    }

    private static JsonElement TestDayReviewFor(IReadOnlyList<string> analysisPaths)
    {
        var review = JsonNode.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "fixtures", "test-day-review.json")))!.AsObject();
        var runs = review["runs"]!.AsArray();
        while (runs.Count > analysisPaths.Count) runs.RemoveAt(runs.Count - 1);
        for (var index = 0; index < runs.Count; index++) runs[index]!["analysis_path"] = analysisPaths[index];
        review["run_count"] = runs.Count;
        return JsonSerializer.SerializeToElement(review);
    }
}
