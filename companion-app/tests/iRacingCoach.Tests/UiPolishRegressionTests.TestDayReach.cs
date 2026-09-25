using System.Text.Json;
using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;
using iRacingCoach.UI;

namespace iRacingCoach.Tests;

public sealed partial class UiPolishRegressionTests
{
    // ---------- Test-day reachability (IRP 9/23 native finding) ----------
    // A test saved from the IRP race needed three clear laps at 77.5–81.5 green laps into a
    // run. A test day's 20-lap runs could never check it, and nothing said so.

    private static RaceTrialSpec ReachSpec(double start, double end, RaceTrialReach? reach) =>
        new("section_s", "Section time", .6, .9, "B", "B", .6, .9, 9.36, [86, 87, 88, 89, 90], 9.06, [1, 2, 3, 4, 5],
            start, end, .15, 3, "rule", 20, 80, "detected/abc", reach);

    private static RaceInvestigationAction ReachAction(RaceTrialSpec? trial, string title = "Repeat section B at the same run age") =>
        new(1, title, "Drive three clear laps at the same run age.", [], null, [1, 90], .6, .9, ["speed"], SectionId: "B", Trial: trial);

    [TestMethod]
    public async Task TheSaveCardSaysHowLongARunTheTestNeeds()
    {
        using var state = NewState();
        var late = await Render<TrialAction>(state, new()
        {
            [nameof(TrialAction.Action)] = ReachAction(ReachSpec(77.5, 81.5, new RaceTrialReach(78, 20, false))),
            [nameof(TrialAction.ComparisonId)] = "run-1-clean",
            [nameof(TrialAction.State)] = state
        });
        StringAssert.Contains(late, "Needs a run of about 78 green laps without a pit stop; a 20-lap test run cannot check it.");
        StringAssert.Contains(late, "data-test-day-reachable=\"false\"");
        StringAssert.Contains(late, "Save test", "The line informs; it adds no control.");

        var early = await Render<TrialAction>(state, new()
        {
            [nameof(TrialAction.Action)] = ReachAction(ReachSpec(16, 20, new RaceTrialReach(16, 20, true))),
            [nameof(TrialAction.ComparisonId)] = "run-1-early",
            [nameof(TrialAction.State)] = state
        });
        StringAssert.Contains(early, "Can be checked in a 20-lap test run (needs about 16 green laps without a pit stop).");
        Assert.AreEqual(1, CountOccurrences(early, "<button"), "Still exactly one Save test button and no new control.");

        var legacy = await Render<TrialAction>(state, new()
        {
            [nameof(TrialAction.Action)] = ReachAction(ReachSpec(77.5, 81.5, null)),
            [nameof(TrialAction.ComparisonId)] = "run-1-clean",
            [nameof(TrialAction.State)] = state
        });
        Assert.DoesNotContain("ri-test-reach", legacy, "Without a backend statement nothing is guessed.");
    }

    [TestMethod]
    public async Task AnEarlyRunVersionIsLabelledAndNamesTheLateRunItStandsFor()
    {
        using var state = NewState();
        var html = await Render<TrialAction>(state, new()
        {
            [nameof(TrialAction.Action)] = ReachAction(ReachSpec(16, 20, new RaceTrialReach(16, 20, true)) with { BaselineLaps = [16, 17, 18, 19, 20] }),
            [nameof(TrialAction.ComparisonId)] = "run-1-early",
            [nameof(TrialAction.LateVersion)] = ReachAction(ReachSpec(86, 90, new RaceTrialReach(86, 20, false)), "Carry more speed into section B")
        });
        StringAssert.Contains(html, "data-test-version=\"early-run\"");
        StringAssert.Contains(html, "data-test-comparison=\"run-1-early\"");
        StringAssert.Contains(html, "Early-run test");
        StringAssert.Contains(html, "Early-run version: the same section, laps 16–20 against laps 1–5. The late-run test needs a run of about 86 green laps without a pit stop.");
        StringAssert.Contains(html, "Can be checked in a 20-lap test run");
        StringAssert.Contains(html, "Repeat section B at the same run age", "The offered test keeps its own title.");
        Assert.DoesNotContain("Carry more speed into section B", html, "The late action's own claim is not attached to the early test.");
    }

    [TestMethod]
    public async Task TheChecksListSaysARecordingCouldNotReachTheWindow()
    {
        using var state = NewState(new TrialListBackend());
        await state.RefreshDrivingTrialsAsync();
        Assert.HasCount(3, state.DrivingTrials);
        var html = await Render<SessionsPage>(state, new() { [nameof(SessionsPage.State)] = state });
        StringAssert.Contains(html, "Not reachable in this recording (longest clean run 20 laps; needs ~78).");
        StringAssert.Contains(html, "Needs a run of about 78 green laps without a pit stop: 3 clear laps at 78–82 green laps into a run");
        StringAssert.Contains(html, "Drive 3 clear laps at 16–20 green laps into a run; a 20-lap test run can check it");

        var css = File.ReadAllText(Path.Combine(UiRoot(), "wwwroot", "race-investigation.css"));
        StringAssert.Contains(css, ".test-row .test-status small { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; white-space: normal;",
            "The status wraps so the run lengths at the end of the sentence are never cut.");

        var unreached = state.DrivingTrials.Single(trial => trial.LatestEvaluation is not null);
        var detail = await Render<TrialDetail>(state, new()
        {
            [nameof(TrialDetail.State)] = state,
            [nameof(TrialDetail.Trial)] = unreached with { Evaluations = [unreached.LatestEvaluation!] }
        });
        StringAssert.Contains(detail, "Needs a run of about 78 green laps without a pit stop; a 20-lap test run cannot check it.");
        StringAssert.Contains(detail, "Not reachable in this recording (longest clean run 20 laps; needs ~78).");
        StringAssert.Contains(detail, "Run 1: clean early versus late");

        var waiting = state.DrivingTrials.Single(trial => trial.TrialId == "trial-00000000000000000002");
        var waitingDetail = await Render<TrialDetail>(state, new()
        {
            [nameof(TrialDetail.State)] = state,
            [nameof(TrialDetail.Trial)] = waiting
        });
        StringAssert.Contains(waitingDetail, "in one run of about 78 green laps without a pit stop.");
    }

    private static int CountOccurrences(string text, string value)
    {
        var count = 0;
        for (var index = text.IndexOf(value, StringComparison.Ordinal); index >= 0; index = text.IndexOf(value, index + value.Length, StringComparison.Ordinal)) count++;
        return count;
    }

    private sealed class TrialListBackend : IBackendClient
    {
        private static string Trial(string id, string title, string window, string reach, string latest) => $$"""
            {"schema_version":1,"trial_id":"{{id}}","created_at":"2026-09-23T23:00:00Z","title":"{{title}}","instruction":"Drive three clear laps.","evidence":[],
             "source":{"analysis_id":"irp-race","recorded_at":"2026-09-23T20:00:00+00:00","track":"Lucas Oil Indianapolis Raceway Park","layout":"Oval","car":"Toyota Supra Class B"},
             "comparison":{"id":"run-1-clean","title":"Run 1: clean early versus late"},
             "metric":{"key":"section_s","label":"Section time","unit":"s","start_pct":0.6,"end_pct":0.9},"section":{"id":"B","label":"B","start_pct":0.6,"end_pct":0.9},
             "baseline":{"laps":[78,79,80,81,82],"median_s":9.36},"reference":{"laps":[1,2,3,4,5],"median_s":9.06},"exposure_window":{{window}},"reachability":{{reach}},
             "success":{"threshold_s":0.15,"minimum_laps":3,"rule":"rule"},"evaluation_count":{{(latest == "null" ? 0 : 1)}},"latest_evaluation":{{latest}}}
            """;

        private const string Unreached = """
            {"evaluated_at":"2026-09-24T19:00:00Z","analysis_id":"test-day-1","analysis_path":null,
             "recording":{"analysis_id":"test-day-1","recorded_at":"2026-09-24T18:00:00+00:00","track":"Lucas Oil Indianapolis Raceway Park","layout":"Oval","session_type":"Offline Testing"},
             "verdict":"not_comparable","reasons":["Not reachable in this recording (longest clean run 20 laps; needs ~78)."],"confounds":[],"window":[75.5,83.5],
             "reachability":{"reachable":false,"longest_clean_run_laps":20,"run_laps_needed":78}}
            """;

        public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default) =>
            Task.FromResult(new BackendHealthResult(true, "test", "1", "test", 1, TimeSpan.Zero));

        public Task<JsonElement> CallToolAsync(BackendConfiguration configuration, string toolName, object arguments, CancellationToken cancellationToken = default)
        {
            if (toolName != "list_driving_trials") throw new InvalidOperationException($"Unexpected tool: {toolName}");
            const string late = """{"run_laps_needed":78,"test_day_run_laps":20,"test_day_reachable":false}""";
            const string early = """{"run_laps_needed":16,"test_day_run_laps":20,"test_day_reachable":true}""";
            var json = $$"""
                {"ok":true,"schema_version":1,"unreadable_count":0,"trials":[
                  {{Trial("trial-00000000000000000001", "Carry more speed into section B", "[77.5,81.5]", late, Unreached)}},
                  {{Trial("trial-00000000000000000002", "Test a smoother brake release", "[77.5,81.5]", late, "null")}},
                  {{Trial("trial-00000000000000000003", "Repeat section B at the same run age", "[16.0,20.0]", early, "null")}}
                ]}
                """;
            using var document = JsonDocument.Parse(json);
            return Task.FromResult(document.RootElement.Clone());
        }
    }
}
