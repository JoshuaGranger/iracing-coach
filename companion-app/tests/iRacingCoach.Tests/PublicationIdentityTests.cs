using System.Collections.Concurrent;
using System.Text.Json;
using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class PublicationIdentityTests
{
    [TestMethod]
    public async Task Analysis_SlowOldRaceCannotOverwriteFastNewRace()
    {
        var root = TestRoot();
        var backend = new BarrierBackend();
        using var state = State(backend, root);
        var raceA = Race("race-a", "selector-a", "Track A");
        var raceB = Race("race-b", "selector-b", "Track B");

        var slow = state.AnalyzeRaceAsync(raceA);
        await backend.WaitForAsync("analyze_iracing_race|selector-a");
        var fast = state.AnalyzeRaceAsync(raceB);
        await backend.WaitForAsync("analyze_iracing_race|selector-b");

        backend.Complete("analyze_iracing_race|selector-b", AnalysisEnvelope("selector-b", "analysis-b", "Track B"));
        await fast;
        backend.Complete("analyze_iracing_race|selector-a", AnalysisEnvelope("selector-a", "analysis-a", "Track A"));
        await slow;

        Assert.AreEqual("race-b", state.SelectedRaceSessionId);
        Assert.AreEqual("analysis-b", state.CurrentAnalysis?.AnalysisId);
        Assert.AreEqual("Track B", state.CurrentAnalysis?.Track);
    }

    [TestMethod]
    public async Task Planning_AnyInputMutationInvalidatesAStagedResultEvenWhenValueReturns()
    {
        var root = TestRoot();
        var backend = new BarrierBackend();
        using var state = State(backend, root);
        var race = Race("race-plan", "selector-plan", "Plan Track") with { Analyzed = true, AnalysisPath = Path.Combine(root, "analysis.json") };
        state.Races.Add(race);
        state.SelectedPlanRaceId = race.Id;
        state.SelectedPlanCarId = race.CarPath;
        state.SelectedPlanTrack = $"{race.Track}|{race.Layout}";
        state.PlanDistanceMode = "Laps";
        state.PlanDistanceValue = 50;

        var pending = state.GeneratePlanAsync();
        await backend.WaitForAsync("iracing_strategy_history|");
        await backend.WaitForAsync("analyze_iracing_race|selector-plan");
        state.PlanDistanceValue = 60;
        state.PlanDistanceValue = 50;
        backend.Complete("iracing_strategy_history|", JsonSerializer.SerializeToElement(Array.Empty<object>()));
        backend.Complete("analyze_iracing_race|selector-plan", AnalysisEnvelope("selector-plan", "analysis-plan", "Plan Track"));
        await pending;

        Assert.IsFalse(state.PlanGenerated);
        Assert.IsNull(state.PlanBriefing);
        Assert.IsEmpty(state.StrategyScenarios);
    }

    [TestMethod]
    public async Task Tuning_ChangedAndRestoredSymptomCannotPublishOldRecommendation()
    {
        var root = TestRoot();
        var backend = new BarrierBackend();
        using var state = State(backend, root);
        var race = Race("race-tune", "selector-tune", "Tune Track") with
        {
            Analyzed = true,
            AnalysisPath = Path.Combine(root, "analysis.json"),
            SetupType = "Open"
        };
        state.Races.Add(race);
        state.SelectedTuningRaceId = race.Id;
        state.TuningBalance = "Understeer";

        var pending = state.GenerateExperimentAsync();
        await backend.WaitForAsync("recommend_open_setup_tuning|");
        state.TuningBalance = "Oversteer";
        state.TuningBalance = "Understeer";
        backend.Complete("recommend_open_setup_tuning|", JsonSerializer.SerializeToElement(new
        {
            ok = true,
            experiment_id = "stale-experiment",
            primary_recommendation = new { system = "front_arb", change = "one click softer", predicted_effect = "more rotation", risk = "low", verify = Array.Empty<string>() },
            recommendation = new { setup = new { fingerprint = "setup-a" } }
        }));
        await pending;

        Assert.IsFalse(state.ExperimentGenerated);
        Assert.IsNull(state.TuningExperiment);
    }

    private static CompanionState State(IBackendClient backend, string root)
    {
        var settings = new CompanionSettings
        {
            CoachHome = root,
            IRacingRoot = Path.Combine(root, "iRacing"),
            LocalStateRootOverride = Path.Combine(root, "local")
        };
        Directory.CreateDirectory(settings.IRacingRoot);
        return new CompanionState(backend, new StaticSettingsStore(settings));
    }

    private static RecentRace Race(string id, string selector, string track) => new(
        id, track, "Layout", "Test Car", "Today", "Fixed", "Ready", "Recorded", false, false, 1, 2,
        CarPath: "test-car", EventKey: id, SessionType: "Race", Selector: selector);

    private static JsonElement AnalysisEnvelope(string selector, string analysisId, string track) => JsonSerializer.SerializeToElement(new
    {
        ok = true,
        analysis_id = analysisId,
        selector,
        selection = new { group_id = selector, sim_session_type = "Race" },
        context = new { },
        analysis_cache = new { },
        knowledge_cache = new { },
        historical_runs_considered = 0,
        race_summary = new { },
        race_timeline = new { },
        damage_repair = new { },
        strategy_forecast = new { },
        data_quality = new { },
        source_files = Array.Empty<string>(),
        source_channel_coverage = new { },
        analysis_path = string.Empty,
        report_path = string.Empty,
        race_card_path = string.Empty,
        race_card = new { },
        analysis_view = new
        {
            schema_version = 1,
            analysis_profile_version = (string?)null,
            identity = new { track_name = track, car_name = "Test Car", event_type = "Race" },
            race_summary = new { },
            race_grades = new { },
            runs = Array.Empty<object>(),
            laps = Array.Empty<object>(),
            lap_traces = new { },
            track_profile = new { },
            track_geometry = new { },
            race_replay = new { },
            tire_learning = new { },
            garage61_representative_laps = new { },
            technical_insights = Array.Empty<object>(),
            corner_tire_age = new { },
            groove_evolution = new { },
            strategy = new { },
            damage_repair = new { },
            setup_telemetry = new { },
            conditions = new { },
            data_quality = new { }
        },
        timing = new { contract_version = 1, total_ms = 1, analysis_cache_hit = false },
        artifacts = new { }
    });

    private static string TestRoot()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-publication", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        return root;
    }

    private sealed class StaticSettingsStore(CompanionSettings settings) : ISettingsStore
    {
        public CompanionSettings Load() => settings;
        public void Save(CompanionSettings value) { }
    }

    private sealed class BarrierBackend : IBackendClient
    {
        private readonly ConcurrentDictionary<string, TaskCompletionSource<JsonElement>> _calls = new(StringComparer.Ordinal);

        public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default) =>
            Task.FromResult(new BackendHealthResult(true, "test", "1", "test", 1, TimeSpan.Zero));

        public Task<JsonElement> CallToolAsync(BackendConfiguration configuration, string toolName, object arguments, CancellationToken cancellationToken = default)
        {
            var serialized = JsonSerializer.SerializeToElement(arguments);
            var discriminator = serialized.TryGetProperty("selector", out var selector) && selector.ValueKind == JsonValueKind.String
                ? selector.GetString()
                : string.Empty;
            var key = $"{toolName}|{discriminator}";
            return _calls.GetOrAdd(key, static _ => new(TaskCreationOptions.RunContinuationsAsynchronously)).Task.WaitAsync(cancellationToken);
        }

        public async Task WaitForAsync(string key)
        {
            var deadline = DateTimeOffset.UtcNow + TimeSpan.FromSeconds(5);
            while (!_calls.ContainsKey(key) && DateTimeOffset.UtcNow < deadline) await Task.Delay(10);
            Assert.IsTrue(_calls.ContainsKey(key), $"Backend call {key} was not observed.");
        }

        public void Complete(string key, JsonElement value) =>
            _calls[key].TrySetResult(value.Clone());
    }
}
