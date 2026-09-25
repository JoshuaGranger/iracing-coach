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
