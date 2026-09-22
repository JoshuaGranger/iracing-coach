using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class RaceOverviewConsistencyTests
{
    [TestMethod]
    public async Task OpeningAndReopeningRaceKeepCompleteRaceTotalsIncludingServiceBoundaryLap()
    {
        var response = Response();
        var backend = new SummaryBackend(response);
        using var state = new CompanionState(backend, null, new DisconnectedLiveTelemetrySource(), allowExternalHostActions: false);
        var testRoot = Path.Combine(Path.GetTempPath(), "iracing-race-overview-tests", Guid.NewGuid().ToString("N"));
        state.Settings.CoachHome = testRoot;
        state.Settings.LocalStateRootOverride = Path.Combine(testRoot, "local");
        var overview = RuntimeMapper.Overview(response);
        var race = new RecentRace("service-boundary", "Fixture track", "Oval", "Fixture car", "Today", "Open", "Analyzed", "Recorded race", true, true, 1, 3,
            Selector: "service-boundary", Overview: overview);
        state.Races.Add(race);
        state.EventSessions.Add(race);
        state.EventGroups.AddRange(DashboardMapper.GroupEvents(state.EventSessions));

        await state.OpenRaceAsync(race);
        Assert.IsNull(state.AnalysisError, state.AnalysisError?.Message);
        Assert.IsNotNull(state.CurrentAnalysis);
        Assert.IsFalse(state.CurrentAnalysis.Runs.SelectMany(run => run.Laps).Contains(103));
        Assert.AreEqual(18.314, state.CurrentAnalysis.Runs.Sum(run => run.FuelUsedGallons ?? 0), .00001);
        Assert.AreEqual(30, state.CurrentAnalysis.Runs.Sum(run => run.CautionLaps));
        AssertOverview(state, overview);

        state.CloseAnalysisWorkspace();
        await state.OpenRaceAsync(state.Races.Single());
        Assert.IsNull(state.AnalysisError, state.AnalysisError?.Message);
        Assert.AreEqual(1, backend.Calls, "The second open must test the persisted-cache path.");
        AssertOverview(state, overview);
    }

    [TestMethod]
    public void MissingRaceFuelRemainsUnknownEvenWhenRunFuelExists()
    {
        var root = JsonNode.Parse(Response().GetRawText())!.AsObject();
        root["analysis_view"]!["race_summary"]!.AsObject().Remove("fuel_used_gal");
        var workspace = RuntimeMapper.Analysis(JsonSerializer.SerializeToElement(root));
        Assert.IsNotNull(workspace.Overview);
        Assert.IsNull(workspace.Overview.FuelUsedGallons);
        Assert.IsNull(PublishedOverview(workspace).FuelUsedGallons,
            "A partial sum of run fuel cannot fill an unknown whole-race fuel total.");

        root["analysis_view"]!.AsObject().Remove("race_summary");
        Assert.Throws<InvalidDataException>(() => RuntimeMapper.Analysis(JsonSerializer.SerializeToElement(root)),
            "A missing required backend summary must still fail contract validation.");
        Assert.IsNull(PublishedOverview(workspace with { Overview = null }).FuelUsedGallons,
            "A legacy typed workspace must not silently substitute run fuel for whole-race fuel.");
    }

    private static RaceOverview PublishedOverview(AnalysisWorkspace workspace) =>
        (RaceOverview)typeof(CompanionState).GetMethod("BuildRaceOverview", BindingFlags.Static | BindingFlags.NonPublic)!.Invoke(null, [workspace])!;

    private static void AssertOverview(CompanionState state, RaceOverview expected)
    {
        foreach (var race in state.Races.Concat(state.EventSessions).Concat(state.EventGroups.SelectMany(group => group.Sessions)))
        {
            Assert.AreEqual(expected, race.Overview, "Home and race browser must retain the same backend summary after opening.");
            Assert.AreEqual(128, race.Overview!.GreenLaps);
            Assert.AreEqual(31, race.Overview.CautionLaps);
            Assert.AreEqual(18.451, race.Overview.FuelUsedGallons);
        }
    }

    private static JsonElement Response()
    {
        var root = new JsonObject
        {
            ["ok"] = true, ["analysis_id"] = "service-boundary", ["selector"] = "service-boundary",
            ["analysis_path"] = "", ["report_path"] = "", ["race_card_path"] = "",
            ["selection"] = new JsonObject { ["sim_session_type"] = "Race", ["group_id"] = "service-boundary" },
            ["historical_runs_considered"] = 0, ["source_files"] = new JsonArray(),
            ["timing"] = new JsonObject { ["contract_version"] = 1, ["total_ms"] = 1, ["analysis_cache_hit"] = false },
            ["race_card"] = JsonSerializer.SerializeToNode(new { title = "Recorded race", bottom_line = new { evidence_type = "measured", text = "Recorded race." }, actions = Array.Empty<object>(), corner_playbook = new { rows = Array.Empty<object>() }, race_triggers = Array.Empty<object>(), evidence_appendix = Array.Empty<object>() })
        };
        foreach (var name in new[] { "context", "analysis_cache", "knowledge_cache", "race_summary", "race_timeline", "damage_repair", "strategy_forecast", "data_quality", "source_channel_coverage", "artifacts" }) root[name] = new JsonObject();
        var view = new JsonObject
        {
            ["schema_version"] = 1, ["analysis_profile_version"] = "post-race-foundations-v19",
            ["identity"] = new JsonObject { ["event_type"] = "Race", ["track_name"] = "Fixture track", ["car_name"] = "Fixture car" },
            ["race_summary"] = new JsonObject { ["recorded_laps"] = 159, ["green_laps_estimated"] = 128, ["caution_laps_estimated"] = 31, ["fuel_used_gal"] = 18.451, ["pit_stops_detected"] = 1 },
            ["runs"] = JsonSerializer.SerializeToNode(new[] {
                new { run_number = 1, lap_numbers = Enumerable.Range(1, 102).ToArray(), green_laps = 72, caution_laps = 30, fuel = new { used_gal = 12.0 } },
                new { run_number = 2, lap_numbers = Enumerable.Range(104, 56).ToArray(), green_laps = 56, caution_laps = 0, fuel = new { used_gal = 6.314 } }
            }),
            ["laps"] = JsonSerializer.SerializeToNode(new[] { new { lap = 103, complete = true, flag_state = "caution", pit_time_s = 22.767, fuel = new { used_gal = .137 } } }),
            ["technical_insights"] = new JsonArray()
        };
        foreach (var name in new[] { "race_grades", "lap_traces", "track_profile", "track_geometry", "race_replay", "tire_learning", "garage61_representative_laps", "corner_tire_age", "groove_evolution", "strategy", "damage_repair", "setup_telemetry", "conditions", "data_quality" }) view[name] = new JsonObject();
        root["analysis_view"] = view;
        return JsonSerializer.SerializeToElement(root);
    }

    private sealed class SummaryBackend(JsonElement response) : IBackendClient
    {
        public int Calls { get; private set; }
        public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default) => throw new NotSupportedException();
        public Task<JsonElement> CallToolAsync(BackendConfiguration configuration, string toolName, object arguments, CancellationToken cancellationToken = default)
        {
            Assert.AreEqual("analyze_iracing_race", toolName);
            Calls++;
            return Task.FromResult(response);
        }
    }
}
