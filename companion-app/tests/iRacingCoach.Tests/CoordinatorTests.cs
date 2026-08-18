using System.Collections.Concurrent;
using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class CoordinatorTests
{
    [TestMethod]
    public void RuntimeState_StartsWithoutSampleRacesCarsOrSetups()
    {
        using var state = new CompanionState(new FakeBackend());
        Assert.IsEmpty(state.Races);
        Assert.IsEmpty(state.Cars);
        Assert.IsEmpty(state.Setups);
        Assert.IsEmpty(state.StrategyScenarios);
        Assert.IsNull(state.TuningExperiment);
    }

    [TestMethod]
    public void Navigation_IsPersonalAndAccountFree()
    {
        using var state = new CompanionState(new FakeBackend());
        state.Navigate("analysis");
        state.Navigate("settings");
        Assert.AreEqual("settings", state.CurrentPage);
        Assert.IsFalse(state.Greeting.Contains("Joshua", StringComparison.OrdinalIgnoreCase));
    }

    [TestMethod]
    [DoNotParallelize]
    public async Task OpeningRace_ImmediatelyShowsItsTelemetryLoadingStateWithoutStaleAnalysis()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-opening-race", Guid.NewGuid().ToString("N"));
        using var state = new CompanionState(new FakeBackend(callDelay: TimeSpan.FromMilliseconds(100)), new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;
        var race = new RecentRace("race-loading", "Kentucky Speedway", "Oval", "Toyota Tundra", "Today", "Fixed", "Ready", "Recorded", false, true, 6, 11)
        {
            Selector = "8001"
        };

        var opening = state.AnalyzeRaceAsync(race, force: true);
        await Task.Delay(20);

        Assert.IsTrue(state.AnalysisWorkspaceOpen);
        Assert.IsTrue(state.AnalysisLoading);
        Assert.AreEqual("race-loading", state.SelectedRaceSessionId);
        Assert.IsNull(state.CurrentAnalysis);
        StringAssert.Contains(state.AnalysisMessage, "telemetry");

        await opening;
        Assert.IsFalse(state.AnalysisLoading);
    }

    [TestMethod]
    public void Troubleshooting_OpensOnlyOnRequestAndNavigationCommandsRemainFast()
    {
        using var state = new CompanionState(new FakeBackend());
        Assert.IsFalse(state.DiagnosticsExpanded);
        state.OpenTroubleshooting();
        Assert.AreEqual("settings", state.CurrentPage);
        Assert.IsTrue(state.DiagnosticsExpanded);

        var timer = Stopwatch.StartNew();
        for (var index = 0; index < 10_000; index++)
            state.Navigate(index % 2 == 0 ? "home" : "settings");
        timer.Stop();
        Assert.IsLessThan(TimeSpan.FromMilliseconds(500), timer.Elapsed, "Local navigation commands should remain comfortably inside an interaction frame budget.");
    }

    [TestMethod]
    public async Task StructuredTuningFeedback_BuildsOneHumanReadableRecordedSymptom()
    {
        using var tuning = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "fixtures", "setup-recommendation.json")));
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-tests", Guid.NewGuid().ToString("N"));
        var credentials = new FakeGarage61CredentialStore(Path.Combine(root, "garage61.dpapi"));
        var store = new JsonSettingsStore(Path.Combine(root, "settings.json"), credentials);
        using var state = new CompanionState(new FakeBackend(tuning: tuning.RootElement.Clone()), store, new DisconnectedLiveTelemetrySource(), new DisabledCoachEngineSupervisor(), credentials);
        var race = new RecentRace("race-1", "Test Track", "Oval", "Test Car", "Today", "Open", "Analyzed", "Recorded", false, true, 4, 2,
            CarPath: "test-car", AnalysisPath: Path.Combine(root, "analysis.json"), SourcePath: Path.Combine(root, "race.ibt"));
        state.Races.Add(race);
        state.SelectedTuningRaceId = race.Id;
        state.TuningRunPhase = "Late run";
        state.TuningCornerPhase = "Center";
        state.TuningBalance = "Tight / understeer";
        state.TuningCorner = "Turn 3";
        state.TuningNotes = "Builds after 12 green laps";

        await state.GenerateExperimentAsync();

        Assert.AreEqual("Moderate tight / understeer at center in Turn 3 during the late run. Driver confidence: medium. Builds after 12 green laps", state.SymptomText);
        Assert.IsNotNull(state.TuningExperiment);
        Assert.IsTrue(state.ExperimentGenerated);
    }

    [TestMethod]
    public void Settings_RejectNetworkPaths()
    {
        using var state = new CompanionState(new FakeBackend());
        state.Settings.IRacingRoot = @"\\server\share\iRacing";
        state.SaveSettings();
        StringAssert.Contains(state.SettingsMessage, "Network");
    }

    [TestMethod]
    public void DashboardMapper_MapsAnalysisAndSourcePaths()
    {
        using var populated = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "fixtures", "dashboard-populated.json")));
        var races = DashboardMapper.Map(populated.RootElement);
        Assert.HasCount(2, races);
        Assert.AreEqual("subsession:8001:1", races[0].Id);
        Assert.AreEqual("subsession:8001:1", races[0].EffectiveSelector);
        Assert.AreEqual("Synthetic Speedway", races[0].Track);
        Assert.IsTrue(races[0].Analyzed);
        Assert.AreEqual(8, races[0].StartPosition);
        Assert.IsNotNull(races[0].Overview);
        Assert.AreEqual(58, races[0].Overview!.GreenLaps);
        Assert.AreEqual(14, races[0].Overview!.CautionLaps);
        Assert.AreEqual(5, races[0].FinishPosition);
        Assert.IsFalse(string.IsNullOrWhiteSpace(races[0].CarPath));

        using var empty = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "fixtures", "dashboard-empty.json")));
        Assert.IsEmpty(DashboardMapper.Map(empty.RootElement));
    }

    [TestMethod]
    public void DashboardMapper_UsesTheSameFriendlyIdentityAsTheOpenedWorkspace()
    {
        using var dashboard = JsonDocument.Parse("""
        {"ok":true,"races":[{"group_id":"subsession:44:1","subsession_id":44,"sim_session_num":1,"sim_session_type":"Race","is_race":true,"track_name":"kentucky 2011 oval","track_config_name":"Oval 2011","car_path":"stockcars2 supra2019","start_time_utc":"2026-08-01T12:00:00Z"}]}
        """);

        var race = DashboardMapper.Map(dashboard.RootElement).Single();

        Assert.AreEqual("Kentucky Speedway", race.Track);
        Assert.AreEqual("Oval", race.Layout);
        Assert.AreEqual("Toyota Supra Class B", race.Car);
        Assert.AreEqual("subsession:44:1", race.EffectiveSelector);
    }

    [TestMethod]
    public void EventBrowser_GroupsQualifyingRaceAndReconnectFilesNewestFirst()
    {
        using var dashboard = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "fixtures", "dashboard-populated.json")));
        using var discovery = JsonDocument.Parse("""
        {"sessions":[
          {"group_id":"subsession:8001:0","subsession_id":8001,"session_id":7001,"sim_session_type":"Qualify","is_race":false,"track_name":"Synthetic Speedway","track_config_name":"Oval","car_path":"synthetic test car","is_fixed_setup":false,"start_time_utc":"2026-08-01T11:45:00Z","file_count":1,"files":["qualify.ibt"]},
          {"group_id":"subsession:8001:1","subsession_id":8001,"session_id":7001,"sim_session_type":"Race","is_race":true,"track_name":"Synthetic Speedway","track_config_name":"Oval","car_path":"synthetic test car","is_fixed_setup":false,"start_time_utc":"2026-08-01T12:00:00Z","file_count":2,"files":["race-a.ibt","race-b.ibt"]}
        ]}
        """);

        var sessions = DashboardMapper.MapEvents(dashboard.RootElement, discovery.RootElement);
        var groups = DashboardMapper.GroupEvents(sessions);

        Assert.HasCount(2, groups);
        var groupedEvent = groups.Single(group => group.Id == "8001");
        Assert.HasCount(2, groupedEvent.Sessions);
        Assert.IsTrue(groupedEvent.Sessions[0].IsQualifying);
        Assert.IsTrue(groupedEvent.Sessions[1].IsRace);
        Assert.AreEqual("subsession:8001:0", groupedEvent.Sessions[0].EffectiveSelector);
        Assert.AreEqual("subsession:8001:1", groupedEvent.Sessions[1].EffectiveSelector);
        Assert.AreNotEqual(groupedEvent.Sessions[0].EffectiveSelector, groupedEvent.Sessions[1].EffectiveSelector);
        Assert.IsTrue(groupedEvent.Sessions[1].Reconnected);
        Assert.IsTrue(groupedEvent.Sessions[1].Analyzed);
    }

    [TestMethod]
    public void EventBrowser_FiltersOnlyFromRecordedFields()
    {
        using var state = new CompanionState(new FakeBackend());
        state.EventGroups.Add(new RaceEventGroup("official", "Track A", "Oval", "Car A", "Today", "Fixed", "Official", [
            new RecentRace("a", "Track A", "Oval", "Car A", "Today", "Fixed", "Analyzed", "Recorded", false, true, 1, 1, EventScope: "Official")
        ]));
        state.EventGroups.Add(new RaceEventGroup("unknown", "Track B", "Road", "Car B", "Yesterday", "Open", string.Empty, [
            new RecentRace("b", "Track B", "Road", "Car B", "Yesterday", "Open", "Needs analysis", "Recorded", false, false, 0, 0)
        ]));

        state.RaceFilter = RaceBrowserFilter.Official;
        Assert.AreEqual("official", state.FilteredEventGroups.Single().Id);
        state.RaceFilter = RaceBrowserFilter.NeedsAnalysis;
        Assert.AreEqual("unknown", state.FilteredEventGroups.Single().Id);
        state.RaceFilter = RaceBrowserFilter.All;
        state.RaceSearchText = "Car B";
        Assert.AreEqual("unknown", state.FilteredEventGroups.Single().Id);
    }

    [TestMethod]
    public void EventBrowser_GroupsQualifyingAndRaceForTheSameEvent()
    {
        using var state = new CompanionState(new FakeBackend());
        var qualifying = new RecentRace("qualify", "Track", "Road", "Car", "Today", "Open", "Recorded", "Recorded", false, false, 0, 0, SessionType: "Qualify");
        var race = new RecentRace("race", "Track", "Road", "Car", "Today", "Open", "Needs analysis", "Recorded", false, false, 0, 0);
        state.EventGroups.Add(new RaceEventGroup("event", "Track", "Road", "Car", "Today", "Open", "Official", [qualifying, race]));

        Assert.HasCount(2, state.EventGroups.Single().Sessions);
        var visible = state.FilteredEventGroups.Single().Sessions;
        Assert.HasCount(2, visible);
        Assert.AreEqual("qualify", visible[0].Id);
        Assert.AreEqual("race", visible[1].Id);
    }

    [TestMethod]
    public void RaceCardMapper_PreservesEvidenceClassesAndUnavailableClaims()
    {
        using var analysis = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "fixtures", "analyze-repair-heavy.json")));
        var card = RuntimeMapper.RaceCard(analysis.RootElement);
        Assert.AreEqual(EvidenceKind.Derived, card.BottomLine.Kind);
        Assert.HasCount(2, card.Corners);
        Assert.AreEqual(EvidenceKind.Unavailable, card.Corners[0].Groove.Kind);
        Assert.HasCount(3, card.Triggers);
    }

    [TestMethod]
    public void AnalysisMapper_PreservesBoundedTracesAndTruthfulGeometryFallback()
    {
        using var response = JsonDocument.Parse("""
        {"analysis_id":"analysis-1","timing":{"total_ms":42.5},"analysis_view":{"schema_version":1,
        "identity":{"track_name":"Test Track","track_config":"Road","car_name":"Test Car","event_type":"Race","is_fixed_setup":false,"setup_fingerprint":"abc123"},
        "race_summary":{"recorded_laps":2,"scheduled_laps":10,"pit_stops_detected":0},
        "runs":[{"run_number":1,"lap_numbers":[1,2],"green_laps":2,"caution_laps":0,"fuel":{"used_gal":1.2},"pace":{"green_lap_time_slope_s_per_lap":0.1},"damage_repair_context":{"automatic_coaching_reference_eligible":true,"reason_codes":[]}}],
        "laps":[{"lap":1,"lap_time_s":31.2,"complete":true,"flag_state":"green","green_fraction":1,"caution_fraction":0,"pit_time_s":0,"position":{"start":4,"end":3},"damage_repair_context":{"automatic_coaching_reference_eligible":true,"exclusion_reason_codes":[]}}],
        "lap_traces":{"tire_stress_definition":"Relative proxy; not measured wear.","traces":[{"lap":1,"lap_time_s":31.2,"complete":true,"flag_state":"green","flag_states":["green","yellow","black"],"pit_entry":true,"pit_exit":false,"fuel_used_gal":0.42,"conditions":{"sky":"Partly cloudy","track_temperature_f":105.0,"air_temperature_f":77.2,"wind_speed_mph":14.0,"wind_direction_degrees":180,"relative_humidity_percent":83,"fog_percent":0,"air_pressure_inhg":28.8,"air_density_lb_ft3":0.071,"precipitation_percent":0,"track_usage":"moderately high usage","weather_declared_wet":false},"points":[{"lap_pct":0.25,"speed_mph":125,"brake":0.2,"tire_stress_proxy":0.4}]}]},
        "track_profile":{"shape":null,"detected_corner_segments":[]},"strategy":{"forecast":{"status":"insufficient_evidence"}},"damage_repair":{"status":"partial"},"setup_telemetry":{},"data_quality":{"confidence":"high"}}}
        """);

        var workspace = RuntimeMapper.Analysis(CurrentAnalysisEnvelope(response.RootElement, 42.5));
        Assert.AreEqual("Test Track", workspace.Track);
        Assert.AreEqual("normalized_distance_strip", workspace.GeometryMode);
        Assert.HasCount(1, workspace.Traces);
        Assert.AreEqual(125d, workspace.Traces[0].Points[0].SpeedMph);
        CollectionAssert.AreEqual(new[] { "green", "yellow", "black" }, workspace.Traces[0].FlagStates!.ToArray());
        Assert.IsTrue(workspace.Traces[0].PitEntry);
        Assert.IsFalse(workspace.Traces[0].PitExit);
        Assert.AreEqual(.42d, workspace.Traces[0].FuelUsedGallons);
        Assert.AreEqual("Partly cloudy", workspace.Traces[0].Conditions!.Sky);
        Assert.AreEqual(105d, workspace.Traces[0].Conditions!.TrackTemperatureF);
        Assert.AreEqual(180d, workspace.Traces[0].Conditions!.WindDirectionDegrees);
        Assert.AreEqual("moderately high usage", workspace.Traces[0].Conditions!.TrackUsage);
        StringAssert.Contains(workspace.TireStressDefinition, "not measured wear");
        Assert.AreEqual(42.5d, workspace.BackendElapsedMilliseconds);
    }

    [TestMethod]
    public void AnalysisMapper_UsesOnlyResolvedForecastDistanceForFuelUi()
    {
        using var response = JsonDocument.Parse("""
        {"analysis_view":{
          "analysis_profile_version":"post-race-foundations-v13",
          "identity":{"event_type":"Race"},
          "race_summary":{"recorded_laps":8,"scheduled_laps":500,"scheduled_minutes":30.5},
          "runs":[],"laps":[],"lap_traces":{"traces":[]},
          "track_profile":{"shape":[],"detected_corner_segments":[]},
          "strategy":{"forecast":{"status":"hybrid_finish_constraint_unresolved","scheduled_laps":null,"all_green_range_laps":34.7,"minimum_stops_all_green":14,"equal_stint_pit_targets_all_green":[33,67]}},
          "damage_repair":{},"setup_telemetry":{},"data_quality":{}}}
        """);

        var workspace = RuntimeMapper.Analysis(CurrentAnalysisEnvelope(response.RootElement));

        Assert.AreEqual(0, workspace.ScheduledLaps);
        Assert.AreEqual(0d, workspace.ScheduledMinutes);
        Assert.AreEqual(500, workspace.DeclaredLapLimit);
        Assert.AreEqual(30.5d, workspace.DeclaredTimeLimitMinutes);
        Assert.AreEqual(34.7d, workspace.Strategy.AllGreenRangeLaps);
        Assert.IsNull(workspace.Strategy.MinimumStopsAllGreen);
        Assert.IsEmpty(workspace.Strategy.EqualStintPitTargets);
    }

    [TestMethod]
    public void AnalysisMapper_UnmarkedResponseCannotRestoreLegacyDistanceClaims()
    {
        using var response = JsonDocument.Parse("""
        {"analysis_view":{"identity":{},"race_summary":{"recorded_laps":7,"scheduled_laps":80},
          "runs":[],"laps":[],"lap_traces":{"traces":[]},"track_profile":{"shape":[],"detected_corner_segments":[]},
          "strategy":{"forecast":{"status":"usable","scheduled_laps":7,"all_green_range_laps":34.7,"minimum_stops_all_green":1,"equal_stint_pit_targets_all_green":[4]}},
          "technical_insights":[{"key":"pit","label":"Pit","takeaway":"Plan for 7 laps"}],
          "damage_repair":{},"data_quality":{}}}
        """);

        var workspace = RuntimeMapper.Analysis(response.RootElement);

        Assert.AreEqual(0, workspace.ScheduledLaps);
        Assert.IsNull(workspace.Strategy.MinimumStopsAllGreen);
        Assert.IsEmpty(workspace.Strategy.EqualStintPitTargets);
        Assert.IsFalse((workspace.TechnicalInsights ?? []).Any(item => item.Key == "pit"));
        StringAssert.Contains(workspace.StrategyStatus, "Legacy scheduled distance unavailable");
    }

    [TestMethod]
    public void Overview_ExposesOnlyCurrentExactLapDistanceForPlanningPrefill()
    {
        static RaceOverview Map(string profile, string summary, string forecast)
        {
            using var response = JsonDocument.Parse(
                "{\"analysis_view\":{\"analysis_profile_version\":" +
                JsonSerializer.Serialize(profile) +
                ",\"identity\":{},\"race_summary\":" + summary +
                ",\"runs\":[],\"laps\":[],\"strategy\":{\"forecast\":" + forecast + "}}}");
            return RuntimeMapper.Overview(response.RootElement);
        }

        var exact = Map(
            "post-race-foundations-v13",
            "{\"recorded_laps\":7,\"scheduled_laps\":80}",
            "{\"status\":\"insufficient_evidence\",\"scheduled_laps\":80}");
        var hybrid = Map(
            "post-race-foundations-v13",
            "{\"recorded_laps\":7,\"scheduled_laps\":500,\"scheduled_minutes\":30.5}",
            "{\"status\":\"usable\",\"scheduled_laps\":7}");
        var legacy = Map(
            "post-race-foundations-v12",
            "{\"recorded_laps\":7,\"scheduled_laps\":80}",
            "{\"status\":\"usable\",\"scheduled_laps\":7}");
        var timed = Map(
            "post-race-foundations-v13",
            "{\"recorded_laps\":7,\"scheduled_laps\":null,\"scheduled_minutes\":30.5}",
            "{\"status\":\"insufficient_evidence\",\"scheduled_laps\":null}");

        Assert.AreEqual(80, exact.ScheduledLaps);
        Assert.AreEqual(0, hybrid.ScheduledLaps);
        Assert.AreEqual(0, legacy.ScheduledLaps);
        Assert.AreEqual(30.5d, timed.ScheduledMinutes);
        Assert.AreEqual(0d, hybrid.ScheduledMinutes);
        Assert.AreEqual(80, exact.DeclaredLapLimit);
        Assert.AreEqual(500, hybrid.DeclaredLapLimit);
        Assert.AreEqual(30.5d, hybrid.DeclaredTimeLimitMinutes);
        Assert.AreEqual(30.5d, timed.DeclaredTimeLimitMinutes);
        Assert.AreEqual(0, legacy.DeclaredLapLimit);
        Assert.AreEqual(0d, legacy.DeclaredTimeLimitMinutes);
        Assert.AreEqual(7, exact.RecordedLaps);
    }

    [TestMethod]
    public void AnalysisMapper_PrefersCanonicalTraceCoordinatesAndSteeringPeakWithLegacyFallback()
    {
        using var response = JsonDocument.Parse("""
        {"analysis_view":{
          "identity":{"event_type":"Race"},"race_summary":{"recorded_laps":1},"laps":[],"runs":[],
          "lap_traces":{"traces":[{"lap":1,"complete":true,"flag_state":"green","points":[
            {"lap_pct":0.1,"latitude":41.12345678,"longitude":-93.12345678,"lat":99.0,"lon":98.0,"steering_abs_peak_rad":0.42,"steering_peak_rad":9.0},
            {"lap_pct":0.2,"lat":40.25,"lon":-92.75,"steering_peak_rad":0.31}
          ]}]},
          "track_profile":{"shape":[],"detected_corner_segments":[]},"strategy":{},"damage_repair":{},"setup_telemetry":{},"data_quality":{}}}
        """);

        var points = RuntimeMapper.Analysis(response.RootElement).Traces.Single().Points;

        Assert.HasCount(2, points);
        Assert.AreEqual(41.12345678d, points[0].Latitude);
        Assert.AreEqual(-93.12345678d, points[0].Longitude);
        Assert.AreEqual(.42d, points[0].SteeringPeakRadians,
            "The analyzer's canonical absolute peak must win over the legacy key.");
        Assert.AreEqual(40.25d, points[1].Latitude);
        Assert.AreEqual(-92.75d, points[1].Longitude);
        Assert.AreEqual(.31d, points[1].SteeringPeakRadians,
            "Previously archived traces using the legacy keys must remain readable.");
    }

    [TestMethod]
    public void AnalysisMapper_MapsMeasuredPitTireBandsIntoDriverFacingOuterMiddleInnerOrder()
    {
        using var response = JsonDocument.Parse("""
        {"selection":{"sim_session_type":"Race"},"analysis_view":{"schema_version":1,
          "identity":{"event_type":"Race"},"race_summary":{"recorded_laps":1},"laps":[],
          "runs":[{"run_number":1,"lap_numbers":[1],"green_laps":1,"caution_laps":0,"ended_with_pit_stop":true,
            "pit_service":{"start_time":100,"end_time":112,"tires_changed_observed":["LF","RF"]},
            "tire_observation":{"tires":{
              "LF":{"average_remaining_percent":90,"remaining_percent":{"L":91,"M":90,"R":89},"carcass_temperature_f":{"CL":180,"CM":185,"CR":190},"surface_temperature_f":{"L":175,"M":181,"R":188},"pressure":{"kind":"live","psi":28.4}},
              "RF":{"average_remaining_percent":88,"remaining_percent":{"L":85,"M":88,"R":91},"carcass_temperature_f":{"CL":195,"CM":190,"CR":182},"surface_temperature_f":{"L":198,"M":191,"R":184},"pressure":{"kind":"cold","psi":25.2}}
            }}}],
          "lap_traces":{"additional_signal_catalog":[
            {"id":"lf-wheel-slip","name":"LF wheel slip","unit":"%","category":"Tires","evidence_type":"derived","description":"Wheel speed relative to vehicle speed.","source_channels":["LFspeed","Speed"]}
          ],"traces":[{"lap":7,"complete":true,"flag_state":"green","points":[
            {"lap_pct":0.5,"additional_signals":{"lf-wheel-slip":-8.25,"invalid":"not numeric","missing":null}}
          ]}]},"track_profile":{"shape":[],"detected_corner_segments":[]},
          "strategy":{"pit_assessments":[]},"damage_repair":{"episodes":[]},"setup_telemetry":{},"data_quality":{}}}
        """);

        var workspace = RuntimeMapper.Analysis(response.RootElement);
        var pitStop = workspace.Runs.Single().PitStop!;
        Assert.AreEqual(12d, pitStop.ServiceSeconds);
        Assert.AreEqual(10d, pitStop.LeftFrontTireWearPercent);
        Assert.AreEqual(12d, pitStop.RightFrontTireWearPercent);
        Assert.IsNotNull(pitStop.TireConditions);

        var leftFront = pitStop.TireConditions["LF"];
        Assert.AreEqual(9d, leftFront.WearPercent.Outer);
        Assert.AreEqual(10d, leftFront.WearPercent.Middle);
        Assert.AreEqual(11d, leftFront.WearPercent.Inner);
        Assert.AreEqual(180d, leftFront.CarcassTemperatureF.Outer);
        Assert.AreEqual(190d, leftFront.CarcassTemperatureF.Inner);
        Assert.AreEqual(28.4d, leftFront.PressurePsi);
        Assert.AreEqual("Live", leftFront.PressureKind);

        var rightFront = pitStop.TireConditions["RF"];
        Assert.AreEqual(9d, rightFront.WearPercent.Outer, "The outside of a right-side tire is its recorded R band.");
        Assert.AreEqual(15d, rightFront.WearPercent.Inner, "The inside of a right-side tire is its recorded L band.");
        Assert.AreEqual(182d, rightFront.CarcassTemperatureF.Outer);
        Assert.AreEqual(195d, rightFront.CarcassTemperatureF.Inner);
        Assert.AreEqual("Cold", rightFront.PressureKind);

        Assert.HasCount(1, workspace.AdditionalTraceSignals!);
        Assert.AreEqual("lf-wheel-slip", workspace.AdditionalTraceSignals![0].Id);
        Assert.AreEqual(EvidenceKind.Derived, workspace.AdditionalTraceSignals[0].Evidence);
        CollectionAssert.AreEqual(new[] { "LFspeed", "Speed" }, workspace.AdditionalTraceSignals[0].SourceChannels.ToArray());
        var additionalSignals = workspace.Traces.Single().Points.Single().AdditionalSignals!;
        Assert.AreEqual(-8.25d, additionalSignals["lf-wheel-slip"]);
        Assert.IsFalse(additionalSignals.ContainsKey("invalid"));
        Assert.IsFalse(additionalSignals.ContainsKey("missing"));
    }

    [TestMethod]
    public void AnalysisMapper_PreservesIndividualIncidentEventsAndExplicitZeroes()
    {
        using var response = JsonDocument.Parse("""
        {"selection":{"sim_session_type":"Race"},"analysis_view":{"schema_version":1,
          "identity":{"event_type":"Race"},"race_summary":{},"laps":[],"runs":[],
          "lap_traces":{"traces":[]},"track_profile":{"shape":[],"detected_corner_segments":[]},
          "strategy":{},"damage_repair":{"incident_points":{"events":[
            {"candidate_lap":7,"points_added":1,"session_time_s":100.5,"count_before":0,"count_after":1,"source_channel":"ExplicitIncidentFeed","event_type":"contact","contact_target":"wall","track_location":"Off track","on_pit_road":false,"speed_mph":88.4,"yaw_rate_deg_s":12.5,"slip_angle_deg":-3.2},
            {"candidate_lap":7,"points_added":2,"session_time_s":105.25,"count_before":1,"count_after":3,"source_channel":"PlayerCarMyIncidentCount"},
            {"candidate_lap":8,"points_added":0,"session_time_s":110,"count_before":3,"count_after":3,"source_channel":"ExplicitIncidentFeed"},
            {"points_added":4,"session_time_s":120},
            {"candidate_lap":9,"session_time_s":125},
            {"candidate_lap":10,"points_added":"2","session_time_s":130}
          ]}},"setup_telemetry":{},"data_quality":{}}}
        """);

        var incidents = RuntimeMapper.Analysis(response.RootElement).Damage.Incidents!;

        Assert.HasCount(3, incidents);
        Assert.HasCount(2, incidents.Where(item => item.Lap == 7));
        Assert.AreEqual(1, incidents[0].Points);
        Assert.AreEqual(100.5d, incidents[0].SessionTimeSeconds);
        Assert.AreEqual(0d, incidents[0].CountBefore);
        Assert.AreEqual(1d, incidents[0].CountAfter);
        Assert.AreEqual("ExplicitIncidentFeed", incidents[0].SourceChannel);
        Assert.AreEqual("contact", incidents[0].EventType);
        Assert.AreEqual("wall", incidents[0].ContactTarget);
        Assert.AreEqual("Off track", incidents[0].TrackLocation);
        Assert.IsFalse(incidents[0].OnPitRoad);
        Assert.AreEqual(88.4d, incidents[0].SpeedMph);
        Assert.AreEqual(12.5d, incidents[0].YawRateDegreesPerSecond);
        Assert.AreEqual(-3.2d, incidents[0].SlipAngleDegrees);
        Assert.AreEqual(2, incidents[1].Points);
        Assert.AreEqual(105.25d, incidents[1].SessionTimeSeconds);
        Assert.AreEqual(8, incidents[2].Lap);
        Assert.AreEqual(0, incidents[2].Points, "An explicitly recorded zero-point event must not be discarded or synthesized.");
        Assert.AreEqual("ExplicitIncidentFeed", incidents[2].SourceChannel);

    }

    [TestMethod]
    public void AnalysisMapper_KentuckyNullShape_MapsOptionalValuesWithoutInventingZeroes()
    {
        using var response = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "fixtures", "analysis-nullable-kentucky-shape.json")));

        var workspace = RuntimeMapper.Analysis(CurrentAnalysisEnvelope(response.RootElement));
        var card = RuntimeMapper.RaceCard(response.RootElement);
        var plan = RuntimeMapper.Plan(response.RootElement);

        Assert.AreEqual("Kentucky Speedway", workspace.Track);
        Assert.AreEqual("Toyota Tundra TRD Pro", workspace.Car);
        Assert.IsNull(workspace.Laps[0].LapTimeSeconds);
        Assert.IsNull(workspace.Laps[0].GreenFraction);
        Assert.IsNull(workspace.Runs[0].PaceSlopeSecondsPerLap);
        Assert.AreEqual("Tire reading unavailable", workspace.Runs[0].TireEndpoint);
        Assert.IsNull(workspace.Traces[0].Points[0].SpeedMph);
        Assert.IsNull(workspace.Traces[0].Points[0].Gear);
        Assert.IsGreaterThan(1, workspace.Traces[0].Points.Where(point => point.Rpm.HasValue).Select(point => point.Rpm).Distinct().Count());
        Assert.AreEqual(0d, workspace.BackendElapsedMilliseconds);
        Assert.AreEqual("Kentucky Race Card", card.Title);
        Assert.IsNull(plan.GreenFuelGallonsPerLap);
        StringAssert.Contains(plan.FuelRange, "58.0");
        Assert.IsEmpty(plan.PitTargets, "A pit target without a resolved distance must not become a plan.");
    }

    [TestMethod]
    public void AnalysisMapper_PropagatesLapAndRunComparisonEligibilityIntoOverview()
    {
        using var response = JsonDocument.Parse("""
        {"selection":{"sim_session_type":"Race"},"analysis_view":{"schema_version":1,
          "identity":{"event_type":"Race"},"race_summary":{"recorded_laps":2},
          "laps":[
            {"lap":1,"lap_time_s":29.0,"complete":true,"flag_state":"green","pit_time_s":0,"damage_repair_context":{"automatic_coaching_reference_eligible":false,"exclusion_reason_codes":["recorded_repair_evidence"]}},
            {"lap":2,"lap_time_s":30.0,"complete":true,"flag_state":"green","pit_time_s":0,"damage_repair_context":{"automatic_coaching_reference_eligible":true,"exclusion_reason_codes":[]}}],
          "lap_traces":{"traces":[
            {"lap":1,"lap_time_s":29.0,"complete":true,"flag_state":"green","pit_time_s":0,"points":[{"lap_pct":0.5,"speed_mph":140}]},
            {"lap":2,"lap_time_s":30.0,"complete":true,"flag_state":"green","pit_time_s":0,"points":[{"lap_pct":0.5,"speed_mph":138}]}]},
          "runs":[
            {"run_number":1,"lap_numbers":[1],"green_laps":5,"pace":{"green_lap_time_slope_s_per_lap":0.8},"damage_repair_context":{"automatic_coaching_reference_eligible":false,"reason_codes":["recorded_repair_evidence"]}},
            {"run_number":2,"lap_numbers":[2],"green_laps":3,"pace":{"green_lap_time_slope_s_per_lap":0.1},"damage_repair_context":{"automatic_coaching_reference_eligible":true,"reason_codes":[]}},
            {"run_number":3,"lap_numbers":[2],"green_laps":4,"pace":{"green_lap_time_slope_s_per_lap":-2.0},"damage_repair_context":{"automatic_coaching_reference_eligible":true,"reason_codes":["manual_review_after_tow_or_repair"]}}],
          "track_profile":{"shape":[],"detected_corner_segments":[]},"strategy":{},"damage_repair":{},"data_quality":{}}}
        """);

        var workspace = RuntimeMapper.Analysis(response.RootElement);
        var overview = RuntimeMapper.Overview(response.RootElement);

        Assert.IsFalse(workspace.Traces.Single(trace => trace.Lap == 1).ComparisonEligible);
        StringAssert.Contains(workspace.Traces.Single(trace => trace.Lap == 1).ExclusionReason, "Recorded Repair Evidence");
        Assert.IsTrue(workspace.Traces.Single(trace => trace.Lap == 2).IsComparable());
        Assert.IsFalse(workspace.Runs.Single(run => run.Number == 3).ComparisonEligible);
        Assert.AreEqual(30d, overview.BestCleanLapSeconds);
        Assert.AreEqual(5, overview.LongestGreenRun, "Observed run length remains factual even when that run is excluded from comparison.");
        Assert.AreEqual(.1d, overview.PaceSlopeSecondsPerLap!.Value, .0001, "Pace must come from the longest eligible run, not the longest disrupted run.");
    }

    [TestMethod]
    public void AnalysisMapper_ExcludesRestartNonRacingOffTrackTrafficAndRepairReasonLaps()
    {
        using var response = JsonDocument.Parse("""
        {"selection":{"sim_session_type":"Race"},"analysis_view":{"schema_version":1,
          "identity":{"event_type":"Race"},"race_summary":{"recorded_laps":9},
          "laps":[
            {"lap":1,"lap_time_s":200,"complete":true,"flag_state":"caution","pit_time_s":0},
            {"lap":2,"lap_time_s":10,"complete":true,"flag_state":"green","pit_time_s":0},
            {"lap":3,"lap_time_s":60,"complete":true,"flag_state":"green","pit_time_s":0,"racing_state_fraction":1,"clean_context":{"on_track_fraction":1,"traffic_proximity_fraction":0}},
            {"lap":4,"lap_time_s":11,"complete":true,"flag_state":"green","pit_time_s":0,"racing_state_fraction":0.5},
            {"lap":5,"lap_time_s":12,"complete":true,"flag_state":"green","pit_time_s":0,"clean_context":{"on_track_fraction":0.5,"traffic_proximity_fraction":0}},
            {"lap":6,"lap_time_s":13,"complete":true,"flag_state":"green","pit_time_s":0,"clean_context":{"on_track_fraction":1,"traffic_proximity_fraction":0.2}},
            {"lap":7,"lap_time_s":60.1,"complete":true,"flag_state":"green","pit_time_s":0},
            {"lap":8,"lap_time_s":60.2,"complete":true,"flag_state":"green","pit_time_s":0},
            {"lap":9,"lap_time_s":9,"complete":true,"flag_state":"green","pit_time_s":0,"damage_repair_context":{"automatic_coaching_reference_eligible":true,"exclusion_reason_codes":["repair_correlated_candidate"]}}],
          "lap_traces":{"traces":[
            {"lap":1,"points":[]},{"lap":2,"points":[]},{"lap":3,"points":[]},
            {"lap":4,"points":[]},{"lap":5,"points":[]},{"lap":6,"points":[]},
            {"lap":7,"points":[]},{"lap":8,"points":[]},{"lap":9,"points":[]}]},
          "runs":[],"track_profile":{"shape":[],"detected_corner_segments":[]},
          "strategy":{},"damage_repair":{},"data_quality":{}}}
        """);

        var workspace = RuntimeMapper.Analysis(response.RootElement);
        var overview = RuntimeMapper.Overview(response.RootElement);

        CollectionAssert.AreEqual(new[] { 3, 7, 8 }, workspace.Traces.Where(trace => trace.ComparisonEligible).Select(trace => trace.Lap).ToArray());
        Assert.AreEqual(60d, overview.BestCleanLapSeconds);
        StringAssert.Contains(workspace.Traces.Single(trace => trace.Lap == 2).ExclusionReason, "Restart");
        StringAssert.Contains(workspace.Traces.Single(trace => trace.Lap == 6).ExclusionReason, "Close Traffic");
    }

    [TestMethod]
    public void ArchivedAnalysis_UsesRecordedSessionPhaseInsteadOfWeekendEventType()
    {
        using var report = JsonDocument.Parse("""
        {
          "analysis_id":"qualifying-analysis",
          "identity":{"event_type":"Race","subsession_id":55,"track_name":"Test Track","car_name":"Test Car"},
          "source":{"selection":{"group_id":"subsession:55:0","subsession_id":55,"sim_session_num":0,"sim_session_type":"Qualify"}},
          "race_summary":{"recorded_laps":2},
          "laps":[],"runs":[],
          "lap_traces":{"traces":[]},
          "track_profile":{"shape":[],"detected_corner_segments":[]},
          "strategy":{},"damage_repair":{},"data_quality":{}
        }
        """);

        var workspace = RuntimeMapper.ArchivedAnalysis(report.RootElement);
        var race = RuntimeMapper.ArchivedRace(report.RootElement, @"C:\Coach\analysis.json");

        Assert.AreEqual("Qualify", workspace.SessionType);
        Assert.IsTrue(race.IsQualifying);
        Assert.AreEqual("subsession:55:0", race.Id);
        Assert.AreEqual("subsession:55:0", race.EffectiveSelector);
        Assert.AreEqual("55", race.EventKey);
    }

    [TestMethod]
    public void ArchivedAnalysis_WithholdsLegacyDistanceDependentClaims()
    {
        using var report = JsonDocument.Parse("""
        {
          "analysis_profile_version":"post-race-foundations-v12",
          "identity":{"event_type":"Race","track_name":"Hybrid Track","car_name":"Test Car"},
          "race_summary":{"recorded_laps":8,"scheduled_laps":500,"scheduled_minutes":30},
          "laps":[],
          "runs":[{"run_number":1,"ended_with_pit_stop":true,"fuel":{"used_gal":3.2},"pit_service":{"start_time":10,"end_time":20}}],
          "lap_traces":{"traces":[]},"track_profile":{"shape":[],"detected_corner_segments":[]},
          "strategy":{"measured_green_fuel_gal_per_lap":0.2,"pit_assessments":[{"run_number":1,"scheduled_race_laps_remaining_after_stop":493}],
            "forecast":{"status":"usable","scheduled_laps":7,"all_green_range_laps":34.7,"minimum_stops_all_green":14,"equal_stint_pit_targets_all_green":[33,67]}},
          "technical_insights":[
            {"key":"pit","label":"Pit strategy","status":"available","takeaway":"No-stop headroom for 500 laps"},
            {"key":"fuel","label":"Fuel","status":"available","takeaway":"Finish reserve for 500 laps"},
            {"key":"tires","label":"Tires","status":"available","takeaway":"Measured wear"}],
          "damage_repair":{},"data_quality":{}
        }
        """);

        var workspace = RuntimeMapper.ArchivedAnalysis(report.RootElement);

        Assert.AreEqual(0, workspace.ScheduledLaps);
        Assert.AreEqual(.2d, workspace.Strategy.GreenFuelGallonsPerLap);
        Assert.AreEqual(34.7d, workspace.Strategy.AllGreenRangeLaps);
        Assert.IsNull(workspace.Strategy.MinimumStopsAllGreen);
        Assert.IsEmpty(workspace.Strategy.EqualStintPitTargets);
        Assert.IsNull(workspace.Runs.Single().PitStop!.RaceLapsRemainingAfterStop);
        CollectionAssert.AreEqual(new[] { "tires" }, (workspace.TechnicalInsights ?? []).Select(item => item.Key).ToArray());
        StringAssert.Contains(workspace.StrategyStatus, "Legacy scheduled distance unavailable");
    }

    [TestMethod]
    public void ArchivedAnalysis_CurrentLapProfileKeepsResolvedDistance()
    {
        using var report = JsonDocument.Parse("""
        {"analysis_profile_version":"post-race-foundations-v13","identity":{},
         "race_summary":{"recorded_laps":80,"scheduled_laps":100},"laps":[],"runs":[],
         "lap_traces":{"traces":[]},"track_profile":{"shape":[],"detected_corner_segments":[]},
         "strategy":{"forecast":{"status":"usable","scheduled_laps":100,"minimum_stops_all_green":2,"equal_stint_pit_targets_all_green":[33,67]}},
         "technical_insights":[{"key":"pit","label":"Pit strategy","status":"available","takeaway":"Current result"}],
         "damage_repair":{},"data_quality":{}}
        """);

        var workspace = RuntimeMapper.ArchivedAnalysis(report.RootElement);

        Assert.AreEqual(100, workspace.ScheduledLaps);
        Assert.AreEqual(2, workspace.Strategy.MinimumStopsAllGreen);
        CollectionAssert.AreEqual(new[] { 33, 67 }, workspace.Strategy.EqualStintPitTargets.ToArray());
        Assert.IsTrue((workspace.TechnicalInsights ?? []).Any(item => item.Key == "pit"));
    }

    [TestMethod]
    public void AnalysisMapper_RejectsConflictingTransportAndRecordedSessionPhases()
    {
        using var response = JsonDocument.Parse("""
        {
          "selection":{"group_id":"subsession:55:1","sim_session_type":"Race"},
          "analysis_view":{
            "schema_version":1,
            "identity":{"event_type":"Race"},
            "source":{"selection":{"group_id":"subsession:55:0","sim_session_type":"Qualify"}},
            "race_summary":{},"laps":[],"runs":[],
            "lap_traces":{"traces":[]},
            "track_profile":{"shape":[],"detected_corner_segments":[]},
            "strategy":{},"damage_repair":{},"data_quality":{}
          }
        }
        """);

        var error = Assert.Throws<InvalidDataException>(() => RuntimeMapper.Analysis(response.RootElement));

        StringAssert.Contains(error.Message, "conflicting session phases");
    }

    [TestMethod]
    public void AnalysisMapper_UsesRecordedSessionPhaseWhenTransportSelectionIsEmpty()
    {
        using var response = JsonDocument.Parse("""
        {
          "selection":{},
          "analysis_view":{
            "schema_version":1,
            "identity":{"event_type":"Race"},
            "source":{"selection":{"group_id":"subsession:55:0","sim_session_type":"Qualify"}},
            "race_summary":{},"laps":[],"runs":[],
            "lap_traces":{"traces":[]},
            "track_profile":{"shape":[],"detected_corner_segments":[]},
            "strategy":{},"damage_repair":{},"data_quality":{}
          }
        }
        """);

        var workspace = RuntimeMapper.Analysis(response.RootElement);

        Assert.AreEqual("Qualify", workspace.SessionType);
    }

    [TestMethod]
    public void AnalysisMapper_LabelsMissingLegacyGradeWeightsAsUnavailable()
    {
        using var response = JsonDocument.Parse("""
        {"selection":{"sim_session_type":"Race"},"analysis_view":{"schema_version":1,
          "identity":{"event_type":"Race"},"race_summary":{},"laps":[],"runs":[],
          "lap_traces":{"traces":[]},"track_profile":{"shape":[],"detected_corner_segments":[]},
          "strategy":{},"damage_repair":{},"data_quality":{},
          "race_grades":{"rubric_version":"legacy-v1","overall_grade":"B","categories":[
            {"key":"pace","label":"Pace execution","grade":"B","score":84,"evidence_type":"derived"}],
            "unavailable_categories":[{"key":"consistency","label":"Consistency","reason":"Not recorded"}]}}}
        """);

        var workspace = RuntimeMapper.Analysis(response.RootElement);

        StringAssert.Contains(workspace.Grades.Single(grade => grade.Key == "pace").Calibration, "configured weight unavailable");
        StringAssert.Contains(workspace.Grades.Single(grade => grade.Key == "consistency").Calibration, "configured weight unavailable");
        Assert.IsFalse(workspace.Grades.Any(grade => grade.Calibration.Contains("0% configured", StringComparison.Ordinal)));
    }

    [TestMethod]
    public void PitServiceAssociation_UsesTheRunEndingAtTheStopForPitOutLaps()
    {
        var service = new AnalysisPitStop(31.2, 8.0, 25, ["LF", "RF"], 1, 2, 3, 4);
        var first = new AnalysisRun(1, [1, 2, 3, 4, 5, 6, 7, 8, 9], 9, 0, 4, null, "Recorded", true, "Recorded run", null, null, null, null, "", null, null, service);
        var second = new AnalysisRun(2, [10, 11, 12], 3, 0, 1.2, null, "Recorded", true, "Recorded run", null, null, null, null, "", null, null);
        AnalysisRun[] runs = [first, second];
        var pitIn = new AnalysisLapTrace(9, 31, true, "green", 1, 0, 2, [], PitEntry: true);
        var pitOut = new AnalysisLapTrace(10, 32, true, "green", 1, 0, 1, [], PitExit: true);

        Assert.AreSame(first, pitIn.PitServiceFor(runs, "in"));
        Assert.AreSame(first, pitOut.PitServiceFor(runs, "out"));
        Assert.AreNotSame(second, pitOut.PitServiceFor(runs, "out"));
    }

    [TestMethod]
    public void CapabilityContext_PartiallyIndexedSessions_AreExcludedWithoutThrowing()
    {
        using var state = new CompanionState(new FakeBackend());
        state.EventSessions.Add(null!);
        state.EventSessions.Add(new RecentRace(
            "partial", "Kentucky Speedway", string.Empty, "Toyota Tundra TRD Pro", "Today",
            null!, "Recorded", "Partially indexed", false, false, 0, 0,
            SessionType: null!, EventScope: null!));
        state.EventSessions.Add(new RecentRace(
            "race", "Kentucky Speedway", string.Empty, "Toyota Tundra TRD Pro", "Today",
            null!, "Recorded", "Partially indexed", false, false, 0, 0,
            SessionType: "Race", EventScope: null!));

        var context = state.CapabilityContext;

        Assert.IsFalse(context.HasOfficialEvents);
        Assert.IsFalse(context.HasHostedLeagueEvents);
        Assert.IsFalse(context.HasFixedEvents);
        Assert.IsFalse(context.HasOpenEvents);
        Assert.IsFalse(context.HasAnalyzedEvents);
    }

    [TestMethod]
    public async Task AnalysisJob_UnexpectedMapperOrBackendFailure_IsContainedAndReported()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-analysis-failure", Guid.NewGuid().ToString("N"));
        using var state = new CompanionState(new FakeBackend(failure: new InvalidOperationException("nullable analysis shape")), new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;
        var race = new RecentRace(
            "kentucky", "Kentucky Speedway", "Oval", "Toyota Tundra TRD Pro", "Today",
            "Fixed", "Needs analysis", "15 laps", false, false, 8, 6, Selector: "87624987");

        await state.AnalyzeRaceAsync(race, force: true);

        Assert.IsNull(state.CurrentAnalysis);
        Assert.IsNotNull(state.LastRecoverableError);
        Assert.AreEqual("failed", state.Jobs.Single().Status);
        StringAssert.Contains(state.LastRecoverableError.Scope, "Kentucky");
    }

    [TestMethod]
    public async Task FailedAnalysisJob_RetryRunsExactlyOnceAgainstTheCurrentRecording()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-analysis-retry", Guid.NewGuid().ToString("N"));
        var backend = new FakeBackend(analysis: HomeAnalysisResponse(), analysisFailuresBeforeSuccess: 1);
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;
        var race = new RecentRace(
            "retry-race", "Kentucky Speedway", "Oval", "Toyota Tundra TRD Pro", "Today",
            "Fixed", "Needs analysis", "15 laps", false, false, 8, 6, Selector: "87624987");
        state.Races.Add(race);

        await state.AnalyzeRaceAsync(race, force: true);
        var failed = state.Jobs.Single();

        await state.RetryJobAsync(failed);

        Assert.AreEqual(2, backend.AnalyzeCalls);
        Assert.HasCount(2, state.Jobs);
        Assert.AreEqual("failed", failed.Status);
        Assert.AreEqual("complete", state.Jobs[0].Status);
        Assert.IsNotNull(state.CurrentAnalysis);
    }

    [TestMethod]
    public async Task FailedAnalysisJob_RetryDoesNotRunWhenTheRecordingIsStale()
    {
        var backend = new FakeBackend(analysis: HomeAnalysisResponse());
        using var state = new CompanionState(backend);
        var failed = new JobItem
        {
            Id = "job-stale",
            Title = "Analyze missing recording",
            CanonicalKey = "session:missing",
            Status = "failed"
        };

        await state.RetryJobAsync(failed);

        Assert.AreEqual(0, backend.AnalyzeCalls);
        StringAssert.Contains(state.Toast, "no longer available");
    }

    [TestMethod]
    public async Task FailedAnalysisJob_RetryDoesNotDuplicateActiveWork()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-analysis-retry-active", Guid.NewGuid().ToString("N"));
        var backend = new FakeBackend(analysis: HomeAnalysisResponse(), analysisDelay: TimeSpan.FromMilliseconds(150));
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;
        var race = new RecentRace(
            "active-race", "Kentucky Speedway", "Oval", "Toyota Tundra TRD Pro", "Today",
            "Fixed", "Needs analysis", "15 laps", false, false, 8, 6, Selector: "87624987");
        state.Races.Add(race);
        var failed = new JobItem
        {
            Id = "job-old",
            Title = "Analyze Kentucky Speedway",
            CanonicalKey = "session:active-race",
            Status = "failed"
        };

        var active = state.AnalyzeRaceAsync(race, force: true);
        await WaitUntilAsync(() => state.Jobs.Any(candidate => candidate.Status == "running"), TimeSpan.FromSeconds(2));
        await state.RetryJobAsync(failed);
        await active;

        Assert.AreEqual(1, backend.AnalyzeCalls);
        Assert.HasCount(1, state.Jobs);
    }

    [TestMethod]
    public async Task AnalysisJob_RejectsTelemetryFromADifferentEventPhase()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-phase-response", Guid.NewGuid().ToString("N"));
        var backend = new FakeBackend(analysis: HomeAnalysisResponse(sessionType: "Race"));
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;
        var qualifying = new RecentRace(
            "subsession:8001:0", "Test Track", "Oval", "Test Car", "Today",
            "Open", "Recorded", "Qualifying", false, false, 0, 0,
            EventKey: "8001", SessionType: "Qualify", Selector: "subsession:8001:0");

        await state.AnalyzeRaceAsync(qualifying, force: true);

        Assert.IsNull(state.CurrentAnalysis);
        Assert.IsNotNull(state.LastRecoverableError);
        StringAssert.Contains(state.LastRecoverableError.Message, "requested Qualify session");
    }

    [TestMethod]
    [DoNotParallelize]
    public async Task AnalysisJob_RejectsTelemetryFromADifferentRecordingInTheSamePhase()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-selector-response", Guid.NewGuid().ToString("N"));
        var backend = new FakeBackend(analysis: HomeAnalysisResponse(selector: "subsession:9999:1"));
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;
        var race = new RecentRace(
            "subsession:8001:1", "Test Track", "Oval", "Test Car", "Today",
            "Open", "Recorded", "Race", false, false, 0, 0,
            EventKey: "8001", SessionType: "Race", Selector: "subsession:8001:1");

        await state.AnalyzeRaceAsync(race, force: true);

        Assert.IsNull(state.CurrentAnalysis);
        Assert.IsNotNull(state.LastRecoverableError);
        StringAssert.Contains(state.LastRecoverableError.Message, "requested recording subsession:8001:1");
    }

    [TestMethod]
    public async Task AnalysisCache_RejectsARaceResponseSavedUnderAQualifyingKey()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-phase-cache", Guid.NewGuid().ToString("N"));
        const string selector = "subsession:8001:0";
        WriteUiAnalysisCache(root, selector, HomeAnalysisResponse(sessionType: "Race"), sessionType: "Qualify");
        var backend = new FakeBackend(analysis: HomeAnalysisResponse(sessionType: "Qualify"));
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;
        var qualifying = new RecentRace(
            selector, "Test Track", "Oval", "Test Car", "Today",
            "Open", "Recorded", "Qualifying", false, false, 0, 0,
            EventKey: "8001", SessionType: "Qualify", Selector: selector);

        await state.AnalyzeRaceAsync(qualifying);

        Assert.AreEqual(1, backend.AnalyzeCalls, "A wrong-phase cache must be ignored and replaced from the requested recording.");
        Assert.AreEqual("Qualify", state.CurrentAnalysis?.SessionType);
        using var repaired = JsonDocument.Parse(File.ReadAllText(UiAnalysisCachePath(root, selector, "Qualify")));
        Assert.AreEqual("Qualify", repaired.RootElement.GetProperty("response").GetProperty("selection").GetProperty("sim_session_type").GetString());
    }

    [TestMethod]
    public async Task AnalysisCache_RejectsADifferentRecordingSelectorInTheSamePhase()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-selector-cache", Guid.NewGuid().ToString("N"));
        const string requested = "subsession:8001:1";
        const string wrong = "subsession:9999:1";
        WriteUiAnalysisCache(root, requested, HomeAnalysisResponse(selector: wrong), storedSelector: wrong);
        var backend = new FakeBackend(analysis: HomeAnalysisResponse());
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;
        var race = new RecentRace(
            requested, "Test Track", "Oval", "Test Car", "Today",
            "Open", "Recorded", "Race", false, false, 0, 0,
            EventKey: "8001", SessionType: "Race", Selector: requested);

        await state.AnalyzeRaceAsync(race);

        Assert.AreEqual(1, backend.AnalyzeCalls, "A same-phase cache for a different recording must be ignored and repaired.");
        Assert.IsNotNull(state.CurrentAnalysis);
        using var repaired = JsonDocument.Parse(File.ReadAllText(UiAnalysisCachePath(root, requested)));
        Assert.AreEqual(requested, repaired.RootElement.GetProperty("selector").GetString());
        Assert.AreEqual(requested, repaired.RootElement.GetProperty("response").GetProperty("selection").GetProperty("group_id").GetString());
    }

    [TestMethod]
    public async Task AnalyzeRace_ReopeningTheLoadedRaceCostsNoBackendCall()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-reopen-guard", Guid.NewGuid().ToString("N"));
        const string selector = "subsession:8001:1";
        var backend = new FakeBackend(analysis: HomeAnalysisResponse(selector: selector));
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;
        // The race metadata must describe the same session the analysis does, as it
        // always does in production - identity mismatch is what the guard screens for.
        var race = new RecentRace(
            selector, "Recorded Track", "", "Recorded Car", "Today",
            "Open", "Recorded", "Race", false, false, 0, 0,
            EventKey: "8001", SessionType: "Race", Selector: selector);

        await state.AnalyzeRaceAsync(race);
        Assert.AreEqual(1, backend.AnalyzeCalls);
        var loaded = state.CurrentAnalysis;
        Assert.IsNotNull(loaded);
        Assert.IsTrue(
            ProgressiveTuningCoordinator.Matches(race, loaded.TuningIdentity),
            "The reopen guard can only fire when the loaded analysis carries matching session identity.");

        // Navigating back into the race that is already open - the Progressive Tuning
        // to Race Analysis path - must be a pointer check, not a re-read.
        await state.AnalyzeRaceAsync(race);

        Assert.AreEqual(1, backend.AnalyzeCalls, "Reopening the loaded race must not re-analyze it.");
        Assert.AreSame(loaded, state.CurrentAnalysis, "The already-loaded evidence must be preserved, not rebuilt.");
        Assert.IsTrue(state.AnalysisWorkspaceOpen);
        Assert.IsFalse(state.AnalysisLoading);
    }

    [TestMethod]
    public async Task AnalyzeRace_ReopenGuardStillAnalyzesADifferentRace()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-reopen-guard-other", Guid.NewGuid().ToString("N"));
        const string first = "subsession:8001:1";
        const string second = "subsession:8002:1";
        var backend = new FakeBackend(responseOverride: (tool, _, arguments) =>
        {
            if (tool != "analyze_iracing_race") return null;
            var requested = JsonSerializer.SerializeToElement(arguments).GetProperty("selector").GetString() ?? first;
            return HomeAnalysisResponse(selector: requested);
        });
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;
        // Same track and car as the analysis fixture, so only the recording identity
        // differs - the case a too-loose guard would wrongly short-circuit.
        RecentRace Race(string selector, string eventKey) => new(
            selector, "Recorded Track", "", "Recorded Car", "Today",
            "Open", "Recorded", "Race", false, false, 0, 0,
            EventKey: eventKey, SessionType: "Race", Selector: selector);

        await state.AnalyzeRaceAsync(Race(first, "8001"));
        Assert.AreEqual(1, backend.AnalyzeCalls);

        // The guard compares full session identity. A different recording must never be
        // served the previous race's telemetry.
        await state.AnalyzeRaceAsync(Race(second, "8002"));

        Assert.AreEqual(2, backend.AnalyzeCalls, "A different race must still be analyzed.");
    }

    [TestMethod]
    public async Task AnalysisCache_RejectsADeadSchemaWithoutReadingTheWholeEntry()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-dead-schema", Guid.NewGuid().ToString("N"));
        const string selector = "subsession:8001:1";
        WriteUiAnalysisCache(root, selector, HomeAnalysisResponse(selector: selector), schemaVersion: 5);
        var backend = new FakeBackend(analysis: HomeAnalysisResponse(selector: selector));
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;
        var race = new RecentRace(
            selector, "Test Track", "Oval", "Test Car", "Today",
            "Open", "Recorded", "Race", false, false, 0, 0,
            EventKey: "8001", SessionType: "Race", Selector: selector);

        await state.AnalyzeRaceAsync(race);

        Assert.AreEqual(1, backend.AnalyzeCalls, "A cache entry from a retired schema must not be served.");
        using var rewritten = JsonDocument.Parse(File.ReadAllText(UiAnalysisCachePath(root, selector)));
        Assert.AreEqual(13, rewritten.RootElement.GetProperty("schemaVersion").GetInt32());
    }

    [TestMethod]
    public async Task AnalysisCache_ServesAStoredResponseWrittenByAnOlderProjection()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-projection", Guid.NewGuid().ToString("N"));
        const string selector = "subsession:8001:1";
        WriteUiAnalysisCache(root, selector, HomeAnalysisResponse(selector: selector), projectionVersion: 0);
        var backend = new FakeBackend(analysis: HomeAnalysisResponse(selector: selector));
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;
        var race = new RecentRace(
            selector, "Recorded Track", "", "Recorded Car", "Today",
            "Open", "Recorded", "Race", false, false, 0, 0,
            EventKey: "8001", SessionType: "Race", Selector: selector);

        await state.AnalyzeRaceAsync(race);

        // A mapping change must cost a re-map, never a re-analysis. Conflating the
        // two is what left 75% of a real cache orphaned.
        Assert.AreEqual(0, backend.AnalyzeCalls, "A stale projection must be re-mapped, not re-analyzed.");
        Assert.IsNotNull(state.CurrentAnalysis);
    }

    [TestMethod]
    public async Task AnalysisCache_EvictsARetiredEnvelopeInsteadOfRereadingItForever()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-evict", Guid.NewGuid().ToString("N"));
        const string selector = "subsession:8001:1";
        WriteUiAnalysisCache(root, selector, HomeAnalysisResponse(selector: selector), schemaVersion: 5);
        var cachePath = UiAnalysisCachePath(root, selector);
        var backend = new FakeBackend(analysis: HomeAnalysisResponse(selector: selector));
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;
        var race = new RecentRace(
            selector, "Recorded Track", "", "Recorded Car", "Today",
            "Open", "Recorded", "Race", false, false, 0, 0,
            EventKey: "8001", SessionType: "Race", Selector: selector);

        await state.AnalyzeRaceAsync(race);

        Assert.AreEqual(1, backend.AnalyzeCalls);
        using var rebuilt = JsonDocument.Parse(File.ReadAllText(cachePath));
        Assert.AreEqual(13, rebuilt.RootElement.GetProperty("schemaVersion").GetInt32(),
            "The retired entry must be replaced by a current one, not left alongside it.");
    }

    [TestMethod]
    public async Task AnalysisCache_IsWrittenCompactBecauseOnlyMachinesReadIt()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-compact-cache", Guid.NewGuid().ToString("N"));
        const string selector = "subsession:8001:1";
        var backend = new FakeBackend(analysis: HomeAnalysisResponse(selector: selector));
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;
        var race = new RecentRace(
            selector, "Test Track", "Oval", "Test Car", "Today",
            "Open", "Recorded", "Race", false, false, 0, 0,
            EventKey: "8001", SessionType: "Race", Selector: selector);

        await state.AnalyzeRaceAsync(race);

        // Indenting this artifact inflated a real 25 MB entry to 615,614 lines.
        var written = File.ReadAllText(UiAnalysisCachePath(root, selector));
        Assert.DoesNotContain("\n", written, "The analysis cache must be written compact.");
    }

    [TestMethod]
    public async Task AnalysisCache_DoesNotUseArchiveAsLiveCacheWhenSourceExists()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-archive-live-cache", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        const string selector = "subsession:8001:1";
        var sourcePath = Path.Combine(root, "race.ibt");
        var analysisPath = Path.Combine(root, "analysis.json");
        File.WriteAllBytes(sourcePath, [0]);
        WriteArchivedAnalysis(analysisPath, selector);
        var backend = new FakeBackend(analysis: HomeAnalysisResponse(selector: selector));
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;
        var race = new RecentRace(
            selector, "Test Track", "Oval", "Test Car", "Today",
            "Open", "Analyzed", "Recorded", false, true, 0, 0,
            AnalysisPath: analysisPath, SourcePath: sourcePath, EventKey: "8001",
            SessionType: "Race", Selector: selector);

        await state.AnalyzeRaceAsync(race);

        Assert.AreEqual(1, backend.AnalyzeCalls,
            "A versionless historical analysis must not bypass current analysis while its recording is available.");
        Assert.IsTrue(File.Exists(UiAnalysisCachePath(root, selector)));
    }

    [TestMethod]
    public async Task AnalysisCache_KeepsArchiveOnlyRaceViewableWithoutSource()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-archive-only", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        const string selector = "subsession:8001:1";
        var analysisPath = Path.Combine(root, "analysis.json");
        WriteArchivedAnalysis(analysisPath, selector);
        WriteUiAnalysisCache(
            root,
            selector,
            HomeAnalysisResponse(selector: selector),
            schemaVersion: 11);
        var backend = new FakeBackend(analysis: HomeAnalysisResponse(selector: selector));
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;
        var race = new RecentRace(
            selector, "Test Track", "Oval", "Test Car", "Today",
            "Open", "Analyzed", "Recorded", false, true, 0, 0,
            AnalysisPath: analysisPath, SourcePath: Path.Combine(root, "missing.ibt"),
            EventKey: "8001", SessionType: "Race", Selector: selector);

        await state.AnalyzeRaceAsync(race);

        Assert.AreEqual(0, backend.AnalyzeCalls);
        Assert.IsNotNull(state.CurrentAnalysis);
        Assert.AreEqual(7, state.CurrentAnalysis.RecordedLaps);
        Assert.IsNull(state.CurrentRaceCard, "Unversioned historical coaching cards must not be republished as current guidance.");
    }

    [TestMethod]
    public async Task DashboardRefresh_ReplacesRowsWithoutCreatingAUserJob()
    {
        using var dashboard = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "fixtures", "dashboard-empty.json")));
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-empty-dashboard", Guid.NewGuid().ToString("N"));
        using var state = new CompanionState(new FakeBackend(dashboard.RootElement.Clone()), new JsonSettingsStore(Path.Combine(root, "settings.json")));
        await state.RefreshDashboardAsync();
        Assert.IsEmpty(state.Races);
        Assert.IsEmpty(state.Jobs);
        StringAssert.Contains(state.DataMessage, "No finalized");
    }

    [TestMethod]
    public async Task HomeRefresh_LoadsPortableRaceSummariesWithoutReanalyzing()
    {
        using var dashboard = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "fixtures", "dashboard-populated.json")));
        var analysis = HomeAnalysisResponse();
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-home-cache", Guid.NewGuid().ToString("N"));
        WriteUiAnalysisCache(root, "subsession:8001:1", analysis);
        WriteUiAnalysisCache(root, "subsession:8000:1", analysis);
        var backend = new FakeBackend(dashboard: dashboard.RootElement.Clone(), analysis: analysis);
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;

        await state.RefreshDashboardAsync();
        await Task.Delay(50);

        Assert.IsTrue(state.HomeDataReady);
        Assert.HasCount(2, state.Races);
        Assert.IsTrue(state.Races.All(race => race.Overview?.BestCleanLapSeconds is > 0));
        Assert.IsTrue(state.Races.All(race => race.Overview?.FuelUsedGallons is > 0));
        Assert.AreEqual(0, backend.AnalyzeCalls, "Valid portable summaries should be used before any recording is re-read.");
        Assert.IsEmpty(state.Jobs);
    }

    [TestMethod]
    public async Task HomeRefresh_ProjectsSavedAnalysisOverviewIntoRaceAnalysisCatalogImmediately()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-shared-race-overview", Guid.NewGuid().ToString("N"));
        var analysisPath = Path.Combine(root, "archive", "reports", "race-9001", "analysis.json");
        Directory.CreateDirectory(Path.GetDirectoryName(analysisPath)!);
        var analysis = HomeAnalysisResponse(selector: "subsession:9001:1");
        File.WriteAllText(analysisPath, analysis.GetProperty("analysis_view").GetRawText());
        var dashboard = JsonSerializer.SerializeToElement(new
        {
            ok = true,
            races = new[]
            {
                new
                {
                    group_id = "subsession:9001:1",
                    subsession_id = 9001,
                    session_id = 8001,
                    sim_session_type = "Race",
                    event_type = "Race",
                    is_race = true,
                    valid = true,
                    is_fixed_setup = true,
                    track_name = "New Hampshire Motor Speedway",
                    track_config_name = "Oval",
                    car_path = "stockcars2 camaro2019",
                    start_time_utc = "2026-08-01T21:09:00Z",
                    file_count = 1,
                    files = new[] { "new-hampshire.ibt" },
                    analysis_status = "analyzed",
                    analysis = new
                    {
                        analysis_path = analysisPath,
                        summary = new
                        {
                            recorded_laps = 40,
                            green_laps_estimated = 35,
                            caution_laps_estimated = 5,
                            pit_stops_detected = 1,
                            runs_detected = 2
                        }
                    }
                }
            }
        });
        var backend = new FakeBackend(
            dashboard: dashboard,
            discovery: DiscoveryWithFinalizedRaces(1),
            analysis: analysis,
            analysisDelay: TimeSpan.FromSeconds(2));
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;

        await state.RefreshDashboardAsync();

        var homeOverview = state.Races.Single().Overview;
        var catalogOverview = state.EventSessions.Single(session => session.IsRace).Overview;
        var groupedOverview = state.EventGroups.Single().Sessions.Single(session => session.IsRace).Overview;
        Assert.IsNotNull(homeOverview);
        Assert.IsNotNull(catalogOverview);
        Assert.IsNotNull(groupedOverview);
        Assert.AreEqual(30.125, homeOverview.BestCleanLapSeconds);
        Assert.AreEqual(homeOverview.BestCleanLapSeconds, catalogOverview.BestCleanLapSeconds);
        Assert.AreEqual(homeOverview.LongestGreenRun, catalogOverview.LongestGreenRun);
        Assert.AreEqual(homeOverview.PaceSlopeSecondsPerLap, catalogOverview.PaceSlopeSecondsPerLap);
        Assert.AreEqual(homeOverview.LowestTireRemainingPercent, catalogOverview.LowestTireRemainingPercent);
        Assert.AreEqual(catalogOverview, groupedOverview,
            "The immutable Race Analysis group must be built from the enriched session projection.");
        Assert.AreEqual(0, backend.AnalyzeCalls,
            "Projecting an existing saved result must not start another analysis pass before the catalog can render it.");
    }

    [TestMethod]
    public async Task HomeRefresh_RegeneratesLegacyCacheToTheCurrentEnvelope()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-home-cache-upgrade", Guid.NewGuid().ToString("N"));
        var analysis = HomeAnalysisResponse();
        const string selector = "subsession:9001:1";
        WriteUiAnalysisCache(root, selector, analysis, schemaVersion: 11);
        var backend = new FakeBackend(
            dashboard: DashboardWithFinalizedRaces(1),
            analysis: analysis);
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;

        await state.RefreshDashboardAsync();
        await WaitUntilAsync(
            () => backend.AnalyzeCalls == 1 && state.Races.Single().Overview?.BestCleanLapSeconds is > 0,
            TimeSpan.FromSeconds(3));

        var cachePath = UiAnalysisCachePath(root, selector);
        using var regenerated = JsonDocument.Parse(File.ReadAllText(cachePath));
        Assert.AreEqual(13, regenerated.RootElement.GetProperty("schemaVersion").GetInt32());
        Assert.AreEqual(1, backend.AnalyzeCalls, "A schema-11 cache predates the corrected scheduled-distance and Garage61 projections and must be regenerated exactly once.");
        Assert.IsEmpty(state.Jobs, "Cache migration remains quiet background maintenance.");
    }

    [TestMethod]
    public void RaceOverview_AcceptsIntegralDecimalLapCountsFromBackendJson()
    {
        using var response = JsonDocument.Parse("""
        {
          "analysis_view": {
            "race_summary": {
              "recorded_laps": 55.0,
              "green_laps_estimated": 29.0,
              "caution_laps_estimated": 26.0,
              "pit_stops_detected": 3.0
            },
            "runs": [{"green_laps":1,"driving_load":{"early_brake_vs_late_percent":0,"early_steer_vs_late_percent":0},"damage_repair_context":{"automatic_coaching_reference_eligible":true,"reason_codes":[]}}],
            "laps": []
          }
        }
        """);

        var overview = RuntimeMapper.Overview(response.RootElement);

        Assert.AreEqual(55, overview.RecordedLaps);
        Assert.AreEqual(29, overview.GreenLaps);
        Assert.AreEqual(26, overview.CautionLaps);
        Assert.AreEqual(3, overview.PitStops);
        Assert.AreEqual(0d, overview.ControlLoadChangePercent);
    }

    [TestMethod]
    public async Task HomeRefresh_QueuesEveryFinalizedUncachedRaceOnce()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-home-background", Guid.NewGuid().ToString("N"));
        var backend = new FakeBackend(
            dashboard: DashboardWithFinalizedRaces(8),
            analysis: HomeAnalysisResponse(),
            callDelay: TimeSpan.FromMilliseconds(60));
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;

        await state.RefreshDashboardAsync();

        Assert.IsTrue(state.HomeDataReady);
        Assert.IsLessThan(6, backend.AnalyzeCalls, "The catalog refresh must not await the background summary queue.");
        Assert.IsEmpty(state.Jobs, "Automatic Home summaries are quiet maintenance, not user jobs.");
        state.SetPrimaryUiVisible(true);
        state.SetPrimaryUiVisible(true);
        state.SetPrimaryUiVisible(true);

        await WaitUntilAsync(
            () => backend.AnalyzeCalls == 8
                && state.Races.All(race => race.Overview?.BestCleanLapSeconds is > 0),
            TimeSpan.FromSeconds(5));

        Assert.AreEqual(8, backend.AnalyzeCalls, "Repeated window-open notifications must not duplicate analysis work.");
        Assert.IsTrue(state.Races.All(race => race.Overview?.BestCleanLapSeconds is > 0));
        Assert.IsEmpty(state.Jobs);
    }

    [TestMethod]
    public async Task HomeRefresh_RetriesOneTransientSummaryFailureQuietly()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-home-background-retry", Guid.NewGuid().ToString("N"));
        var backend = new FakeBackend(
            dashboard: DashboardWithFinalizedRaces(1),
            analysis: HomeAnalysisResponse(),
            analysisFailuresBeforeSuccess: 1);
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;

        await state.RefreshDashboardAsync();
        await WaitUntilAsync(
            () => backend.AnalyzeCalls == 2 && state.Races.Single().Overview?.BestCleanLapSeconds is > 0,
            TimeSpan.FromSeconds(3));
        await Task.Delay(150);

        Assert.AreEqual(2, backend.AnalyzeCalls, "A transient failure gets exactly one bounded background retry.");
        Assert.IsNotNull(state.Races.Single().Overview);
        Assert.IsEmpty(state.Jobs, "Automatic retry must remain quiet maintenance.");
    }

    [TestMethod]
    public async Task InteractiveAnalysis_ImmediatelyMarksEveryMatchingRaceRecordAnalyzed()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-interactive-state", Guid.NewGuid().ToString("N"));
        var analysisPath = Path.Combine(root, "archive", "reports", "interactive", "analysis.json");
        var backend = new FakeBackend(analysis: HomeAnalysisResponse(analysisPath));
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;
        var dashboardRace = new RecentRace(
            "dashboard-race", "Test Track", "Oval", "Test Car", "Today", "Open",
            "Needs analysis", "Finalized race recording", false, false, 1, 2,
            EventKey: "9001", SessionType: "Race", Selector: "9001");
        var eventRace = dashboardRace with { Id = "event-race" };
        var qualifying = dashboardRace with
        {
            Id = "event-qualifying",
            Status = "Recorded",
            SessionType = "Qualifying"
        };
        state.Races.Add(dashboardRace);
        state.EventSessions.Add(qualifying);
        state.EventSessions.Add(eventRace);
        state.EventGroups.AddRange(DashboardMapper.GroupEvents(state.EventSessions));

        await state.AnalyzeRaceAsync(dashboardRace, force: true);

        var updatedHomeRace = state.Races.Single();
        var updatedEventRace = state.EventSessions.Single(session => session.IsRace);
        var untouchedQualifying = state.EventSessions.Single(session => session.IsQualifying);
        Assert.IsTrue(updatedHomeRace.Analyzed);
        Assert.AreEqual("Analyzed", updatedHomeRace.Status);
        Assert.AreEqual(analysisPath, updatedHomeRace.AnalysisPath);
        Assert.IsNotNull(updatedHomeRace.Overview);
        Assert.IsTrue(updatedEventRace.Analyzed);
        Assert.AreEqual(analysisPath, updatedEventRace.AnalysisPath);
        Assert.IsFalse(untouchedQualifying.Analyzed, "A race result must not mark the event's qualifying session analyzed.");
        Assert.AreEqual(string.Empty, untouchedQualifying.AnalysisPath);
        Assert.AreEqual("dashboard-race", state.TuningRaces.Single().Id,
            "Planning and tuning selectors must see the successful analysis immediately.");
        state.RaceFilter = RaceBrowserFilter.Analyzed;
        Assert.AreEqual("9001", state.FilteredEventGroups.Single().Id,
            "The rebuilt event groups must immediately reflect the analyzed filter.");
    }

    [TestMethod]
    public async Task BackgroundAnalysis_ImmediatelyMarksCatalogAndHomeRecordsAnalyzed()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-background-state", Guid.NewGuid().ToString("N"));
        var analysisPath = Path.Combine(root, "archive", "reports", "background", "analysis.json");
        var backend = new FakeBackend(
            dashboard: DashboardWithFinalizedRaces(1, isFixedSetup: false),
            analysis: HomeAnalysisResponse(analysisPath));
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;

        await state.RefreshDashboardAsync();
        await WaitUntilAsync(
            () => state.Races.Single().Analyzed && state.EventSessions.Single(session => session.IsRace).Analyzed,
            TimeSpan.FromSeconds(3));

        Assert.AreEqual(1, backend.AnalyzeCalls);
        Assert.AreEqual(analysisPath, state.Races.Single().AnalysisPath);
        Assert.AreEqual(analysisPath, state.EventSessions.Single(session => session.IsRace).AnalysisPath);
        Assert.IsTrue(state.EventGroups.Single().Analyzed);
        Assert.AreEqual(state.Races.Single().Id, state.TuningRaces.Single().Id);
        state.RaceFilter = RaceBrowserFilter.Analyzed;
        Assert.HasCount(1, state.FilteredEventGroups.ToArray());
        state.RaceFilter = RaceBrowserFilter.NeedsAnalysis;
        Assert.IsEmpty(state.FilteredEventGroups.ToArray());
        Assert.IsEmpty(state.Jobs, "Automatic state synchronization remains background maintenance.");
    }

    [TestMethod]
    public async Task HomeRefresh_ReleasesFailedSummaryForALaterRefresh()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-home-background-later-retry", Guid.NewGuid().ToString("N"));
        var backend = new FakeBackend(
            dashboard: DashboardWithFinalizedRaces(1),
            analysis: HomeAnalysisResponse(),
            analysisFailuresBeforeSuccess: 2);
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;

        await state.RefreshDashboardAsync();
        await WaitUntilAsync(() => backend.AnalyzeCalls == 2, TimeSpan.FromSeconds(3));
        await Task.Delay(100);

        Assert.IsNull(state.Races.Single().Overview?.BestCleanLapSeconds,
            "The bounded first pass should stop after its one retry without inventing summary data.");
        await state.RefreshDashboardAsync();
        await WaitUntilAsync(
            () => backend.AnalyzeCalls == 3 && state.Races.Single().Overview?.BestCleanLapSeconds is > 0,
            TimeSpan.FromSeconds(3));
        await Task.Delay(100);

        Assert.AreEqual(3, backend.AnalyzeCalls, "A later refresh may schedule the race again after the active key is released.");
        Assert.IsEmpty(state.Jobs);
    }

    [TestMethod]
    public async Task HomeRefresh_AnalyzesEveryDiscoveredRaceAndRefreshesImmutableEventGroups()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-discovered-background", Guid.NewGuid().ToString("N"));
        var backend = new FakeBackend(
            dashboard: DashboardWithFinalizedRaces(2),
            discovery: DiscoveryWithFinalizedRaces(5),
            analysis: HomeAnalysisResponse(),
            callDelay: TimeSpan.FromMilliseconds(50));
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;

        await state.RefreshDashboardAsync();

        Assert.HasCount(2, state.Races, "The Home dashboard remains intentionally bounded.");
        Assert.HasCount(5, state.EventGroups, "Race Analysis should include the complete discovered race catalog.");
        var originalGroups = state.EventGroups.ToArray();

        await WaitUntilAsync(() => backend.AnalyzeCalls == 5, TimeSpan.FromSeconds(4));
        await WaitUntilAsync(
            () => state.EventGroups.All(group => group.Sessions.Single().Overview?.BestCleanLapSeconds is > 0),
            TimeSpan.FromSeconds(2));

        Assert.AreEqual(5, backend.AnalyzeCalls, "Background analysis must cover discovered races beyond the Home limit exactly once.");
        Assert.IsTrue(state.EventGroups.Zip(originalGroups).Any(pair => !ReferenceEquals(pair.First, pair.Second)),
            "Immutable event-group snapshots must be rebuilt as overview results arrive.");
        Assert.IsEmpty(state.Jobs, "Automatic catalog enrichment must not create user-facing jobs.");
    }

    [TestMethod]
    public async Task HomeRefresh_PausesBackgroundAnalysisWhileLiveTelemetryIsConnected()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-live-background-gate", Guid.NewGuid().ToString("N"));
        var source = new SwitchableLiveTelemetrySource { Connected = true };
        var backend = new FakeBackend(
            dashboard: DashboardWithFinalizedRaces(2),
            analysis: HomeAnalysisResponse());
        using var state = new CompanionState(
            backend,
            new JsonSettingsStore(Path.Combine(root, "settings.json")),
            source,
            new DisabledCoachEngineSupervisor(),
            new FakeGarage61CredentialStore(Path.Combine(root, "garage61.credential")));
        state.Settings.CoachHome = root;

        await state.InitializeAsync();
        await WaitUntilAsync(() => state.LiveState.Snapshot.Connected, TimeSpan.FromSeconds(1));
        await Task.Delay(300);

        Assert.AreEqual(0, backend.AnalyzeCalls, "Quiet maintenance must not start while live driving telemetry is connected.");
        Assert.IsEmpty(state.Jobs);

        source.Connected = false;
        await WaitUntilAsync(() => !state.LiveState.Snapshot.Connected, TimeSpan.FromSeconds(1));
        await WaitUntilAsync(() => backend.AnalyzeCalls == 2, TimeSpan.FromSeconds(3));

        Assert.AreEqual(2, backend.AnalyzeCalls, "The paused queue should resume once the live session disconnects.");
        Assert.IsEmpty(state.Jobs);
    }

    [TestMethod]
    [DoNotParallelize]
    public async Task LiveMonitorAutoReopen_ManualHideWinsUntilTheNextConnection()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-monitor-reopen", Guid.NewGuid().ToString("N"));
        var source = new SwitchableLiveTelemetrySource { Connected = false };
        var store = new JsonSettingsStore(
            Path.Combine(root, "settings.json"),
            new FakeGarage61CredentialStore(Path.Combine(root, "garage61.credential")),
            Path.Combine(root, "machine-settings.json"));
        using var state = new CompanionState(
            new FakeBackend(),
            store,
            source,
            new DisabledCoachEngineSupervisor(),
            new FakeGarage61CredentialStore(Path.Combine(root, "garage61.credential")));
        state.Settings.CoachHome = root;
        state.Settings.LiveMonitor.ReopenOnConnect = true;
        state.SetLiveMonitorVisible(false, requestHost: false);
        var requests = new ConcurrentQueue<(bool Visible, bool Activate)>();
        state.LiveMonitorVisibilityRequested += (visible, activate) => requests.Enqueue((visible, activate));

        await state.InitializeAsync();
        source.Connected = true;
        await WaitUntilAsync(() => state.LiveState.Snapshot.Connected && state.LiveMonitorVisible, TimeSpan.FromSeconds(1));
        await WaitUntilAsync(() => requests.Any(request => request.Visible), TimeSpan.FromSeconds(1));

        state.SetLiveMonitorVisible(false);
        var automaticOpenCount = requests.Count(request => request.Visible);
        await Task.Delay(150);

        Assert.IsFalse(state.LiveMonitorVisible, "A user close must suppress repeated auto-open requests for the active connection.");
        Assert.AreEqual(automaticOpenCount, requests.Count(request => request.Visible));
        Assert.IsFalse(store.Load().LiveMonitor.Visible, "The manual close must be the durable final setting.");

        source.Connected = false;
        await WaitUntilAsync(() => !state.LiveState.Snapshot.Connected, TimeSpan.FromSeconds(1));
        source.Connected = true;
        await WaitUntilAsync(() => state.LiveMonitorVisible && requests.Count(request => request.Visible) > automaticOpenCount, TimeSpan.FromSeconds(1));

        var reconnectRequest = requests.Last(request => request.Visible);
        Assert.IsFalse(reconnectRequest.Activate, "Automatic reopening must not steal focus.");
    }

    [TestMethod]
    public async Task HomeRefresh_PausesBackgroundAnalysisWhileInteractiveAnalysisIsLoading()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-interactive-background-gate", Guid.NewGuid().ToString("N"));
        var backend = new FakeBackend(
            dashboard: DashboardWithFinalizedRaces(2),
            analysis: HomeAnalysisResponse(),
            analysisDelay: TimeSpan.FromMilliseconds(400));
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;
        var interactiveRace = new RecentRace(
            "interactive-race", "Interactive Track", "Oval", "Test Car", "Today", "Fixed",
            "Needs analysis", "Recorded", false, false, 0, 0, Selector: "interactive-selector");

        var interactiveAnalysis = state.AnalyzeRaceAsync(interactiveRace, force: true);
        await WaitUntilAsync(() => state.AnalysisLoading && backend.AnalyzeCalls == 1, TimeSpan.FromSeconds(1));
        await state.RefreshDashboardAsync();
        await Task.Delay(200);

        Assert.AreEqual(1, backend.AnalyzeCalls, "Quiet maintenance must wait for the user-requested analysis to finish.");

        await interactiveAnalysis;
        await WaitUntilAsync(() => backend.AnalyzeCalls == 3, TimeSpan.FromSeconds(3));

        Assert.AreEqual(3, backend.AnalyzeCalls);
        Assert.HasCount(1, state.Jobs, "Only the explicit interactive analysis should appear in the job tray.");
    }

    [TestMethod]
    public void HomeSurface_UsesOneNativeTelemetryActionAndWaitsForStableWorkflows()
    {
        var home = File.ReadAllText(Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI", "HomePage.razor"));

        StringAssert.Contains(home, "State.SetLiveMonitorVisible(true)");
        StringAssert.Contains(home, "State.HomeDataReady");
        StringAssert.Contains(home, "Best clean lap");
        StringAssert.Contains(home, "FuelUsed(overview)");
        Assert.DoesNotContain("State.Navigate(\"live\")", home);
        Assert.DoesNotContain("State.ToggleLiveMonitor", home);
    }

    [TestMethod]
    public void HomeSurface_DoesNotClaimRaceHistoryIsEmptyBeforeDiscoveryCompletes()
    {
        var home = File.ReadAllText(Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI", "HomePage.razor"));
        var loadingBranch = home.IndexOf("@if (!State.HomeDataReady)", StringComparison.Ordinal);
        var emptyBranch = home.IndexOf("else if (State.Races.Count == 0)", StringComparison.Ordinal);

        Assert.IsGreaterThanOrEqualTo(0, loadingBranch);
        Assert.IsGreaterThan(loadingBranch, emptyBranch);
        StringAssert.Contains(home, "Loading recent races");
    }

    [TestMethod]
    public async Task NavigationAcrossEveryPageTwice_PerformsNoBackendOrGarage61Requests()
    {
        var backend = new FakeBackend();
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-navigation", Guid.NewGuid().ToString("N"));
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;
        await state.RefreshDashboardAsync();
        var backgroundAnalyses = state.Races.Count(race => race.IsRace && !string.IsNullOrWhiteSpace(race.EffectiveSelector));
        await WaitUntilAsync(() => backend.AnalyzeCalls == backgroundAnalyses, TimeSpan.FromSeconds(3));
        var callsAfterCatalogLoad = backend.ToolCalls;
        var garageAfterCatalogLoad = backend.Garage61Calls;

        var pages = new[] { "home", "live", "analysis", "planning", "setup", "tuning", "connections", "settings" };
        foreach (var page in pages.Concat(pages)) state.Navigate(page);

        Assert.AreEqual(callsAfterCatalogLoad, backend.ToolCalls);
        Assert.AreEqual(garageAfterCatalogLoad, backend.Garage61Calls);
        Assert.AreEqual(1, garageAfterCatalogLoad);
    }

    [TestMethod]
    public async Task ConcurrentCatalogRefreshes_CoalesceIntoOneTrailingDirtyPass()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-refresh-trailing", Guid.NewGuid().ToString("N"));
        var backend = new FakeBackend(callDelay: TimeSpan.FromMilliseconds(30));
        using var state = CreateIsolatedState(backend, root);

        var first = state.RefreshDashboardAsync();
        await WaitUntilAsync(() => backend.ToolCalls >= 4, TimeSpan.FromSeconds(2));
        await Task.WhenAll(first, state.RefreshDashboardAsync(), state.RefreshDashboardAsync());

        Assert.AreEqual(8, backend.ToolCalls, "Requests arriving during the active pass must coalesce into exactly one trailing pass.");
        Assert.AreEqual(2, backend.Garage61Calls);
        Assert.AreEqual(2, state.LocalInventoryGeneration);
    }

    [TestMethod]
    public async Task CatalogRefresh_RetainsLastKnownGoodSectionWhenOneProviderIsMalformed()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-refresh-lkg", Guid.NewGuid().ToString("N"));
        var backend = new FakeBackend(
            dashboard: DashboardWithFinalizedRaces(1),
            responseOverride: (tool, call, _) => tool == "iracing_companion_dashboard" && call == 2
                ? JsonSerializer.SerializeToElement(new { ok = false, status = "unavailable" })
                : null);
        using var state = CreateIsolatedState(backend, root);

        await state.RefreshDashboardAsync();
        var firstRace = state.Races.Single();
        await state.RefreshDashboardAsync();

        Assert.AreEqual(firstRace, state.Races.Single());
        Assert.AreEqual(2, state.LocalInventoryGeneration);
        Assert.IsFalse(state.LocalInventorySections.Single(section => section.Name == "Race recordings").Current);
        Assert.IsTrue(state.LocalInventorySections.Single(section => section.Name == "Local setups").Current);
        StringAssert.Contains(state.DataMessage, "last complete inventory");
    }

    [TestMethod]
    public async Task CatalogRefresh_RootChangeDiscardsTheOldPassAndPublishesTheTrailingPass()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-refresh-root", Guid.NewGuid().ToString("N"));
        var oldRoot = Path.Combine(root, "old");
        var newRoot = Path.Combine(root, "new");
        Directory.CreateDirectory(oldRoot);
        Directory.CreateDirectory(newRoot);
        var backend = new FakeBackend(
            callDelay: TimeSpan.FromMilliseconds(100),
            responseOverride: (tool, _, arguments) => tool == "iracing_companion_dashboard"
                ? DashboardNamed(Path.GetFileName(ArgumentText(arguments, "root")))
                : null);
        using var state = CreateIsolatedState(backend, root);
        state.Settings.IRacingRoot = oldRoot;

        var oldPass = state.RefreshDashboardAsync();
        await WaitUntilAsync(() => backend.ToolCalls >= 4, TimeSpan.FromSeconds(2));
        state.Settings.IRacingRoot = newRoot;
        var newPass = state.RefreshDashboardAsync();
        await Task.WhenAll(oldPass, newPass);

        Assert.AreEqual("New", state.Races.Single().Track);
        Assert.AreEqual(1, state.LocalInventoryGeneration, "A completed pass for an obsolete root must never be published.");
        Assert.AreEqual(8, backend.ToolCalls);
    }

    [TestMethod]
    public async Task CatalogRefresh_SubscriberCancellationDoesNotCancelTheSharedInventoryPass()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-refresh-cancel", Guid.NewGuid().ToString("N"));
        var backend = new FakeBackend(dashboard: DashboardWithFinalizedRaces(1), callDelay: TimeSpan.FromMilliseconds(100));
        using var state = CreateIsolatedState(backend, root);
        using var cancellation = new CancellationTokenSource();

        var subscriber = state.RefreshDashboardAsync(cancellation.Token);
        cancellation.Cancel();
        await Assert.ThrowsAsync<OperationCanceledException>(() => subscriber);
        await WaitUntilAsync(() => state.LocalInventoryGeneration == 1, TimeSpan.FromSeconds(3));

        Assert.HasCount(1, state.Races);
        Assert.IsTrue(state.HomeDataReady);
    }

    [TestMethod]
    public void SettingsStore_RoundTripsPortablePreferencesAndMigratesGarage61Key()
    {
        var directory = Path.Combine(Path.GetTempPath(), "iracing-coach-tests", Guid.NewGuid().ToString("N"));
        var path = Path.Combine(directory, "settings.json");
        var credentials = new FakeGarage61CredentialStore(Path.Combine(directory, "credential.dpapi"));
        var store = new JsonSettingsStore(path, credentials);
        var expected = new CompanionSettings
        {
            CoachHome = directory,
            IRacingRoot = @"C:\Local\iRacing",
            Garage61ApiKey = "portable-test-key",
            ThemeColor = "coral",
            LaunchAtSignIn = true,
            UseReducedMotion = true,
            LiveMonitor = new LiveMonitorLayout
            {
                ActiveLayoutId = "layout-personal",
                IsLocked = false,
                Left = 220,
                Top = 140,
                OverallScale = 1.4,
                ReopenOnConnect = true,
                GlobalHotkey = "Ctrl+Shift+L",
                UserLayouts =
                [
                    new LiveMonitorNamedLayout
                    {
                        Id = "layout-personal", Name = "Personal Race", Rows = 2, Columns = 3,
                        Tiles = [new LiveMonitorTile { Id = "tile-speed", MetricId = "speed", DisplayStyle = LiveMonitorDisplayStyle.Trend, Unit = "mph" }]
                    }
                ]
            }
        };

        store.Save(expected);
        var actual = store.Load();
        Assert.AreEqual(directory, actual.CoachHome);
        Assert.AreEqual(Path.Combine(directory, "data"), actual.ArchiveRoot);
        Assert.AreEqual(Path.Combine(directory, "setups"), actual.SetupsRoot);
        Assert.IsFalse(string.IsNullOrWhiteSpace(actual.IRacingInstallRoot));
        Assert.AreEqual(string.Empty, actual.Garage61ApiKey);
        Assert.AreEqual("portable-test-key", credentials.StoredToken);
        Assert.DoesNotContain("portable-test-key", File.ReadAllText(path));
        Assert.IsFalse(File.ReadAllText(path).Contains("garage61ApiKey", StringComparison.OrdinalIgnoreCase));
        Assert.IsFalse(File.ReadAllText(path).Contains("monitorDeviceName", StringComparison.OrdinalIgnoreCase));
        Assert.IsFalse(File.ReadAllText(path).Contains("\"left\"", StringComparison.OrdinalIgnoreCase));
        Assert.IsTrue(File.Exists(path + ".machine-local.json"));
        Assert.IsTrue(actual.LaunchAtSignIn);
        Assert.IsTrue(actual.UseReducedMotion);
        Assert.AreEqual("coral", actual.ThemeColor);
        Assert.AreEqual("layout-personal", actual.LiveMonitor.ActiveLayoutId);
        Assert.IsFalse(actual.LiveMonitor.IsLocked);
        Assert.AreEqual("Personal Race", actual.LiveMonitor.UserLayouts.Single(layout => layout.Id == "layout-personal").Name);
        CollectionAssert.AreEquivalent(
            new[] { LiveMonitorLayouts.FactoryRaceId, LiveMonitorLayouts.FactoryQualifyingId },
            actual.LiveMonitor.UserLayouts.Where(layout => layout.Id != "layout-personal").Select(layout => layout.Id).ToArray());
        Assert.IsTrue(actual.LiveMonitor.BuiltInDashboardsInitialized);
        Assert.AreEqual(220, actual.LiveMonitor.Left);
        Assert.AreEqual(140, actual.LiveMonitor.Top);
        Assert.AreEqual(1.4, actual.LiveMonitor.OverallScale);
        Assert.IsTrue(actual.LiveMonitor.ReopenOnConnect);
        Assert.AreEqual("Ctrl+Shift+L", actual.LiveMonitor.GlobalHotkey);
    }

    [TestMethod]
    public void SettingsStore_MigratesCustomizedLegacyAnalysisTraceLayoutOnceAndPersistsTheSelection()
    {
        var directory = Path.Combine(Path.GetTempPath(), "iracing-coach-analysis-layout-migration", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(directory);
        var path = Path.Combine(directory, "settings.json");
        var credentials = new FakeGarage61CredentialStore(Path.Combine(directory, "credential.dpapi"));
        var legacy = new CompanionSettings
        {
            CoachHome = directory,
            RaceAnalysisTraces = new AnalysisTraceLayout
            {
                Rows =
                [
                    new AnalysisTraceRow
                    {
                        Id = "legacy-row",
                        PrimarySignalId = "throttle",
                        SecondarySignalId = "brake"
                    }
                ]
            }
        };
        var jsonOptions = new JsonSerializerOptions(JsonSerializerDefaults.Web) { WriteIndented = true };
        var legacyJson = JsonNode.Parse(JsonSerializer.Serialize(legacy, jsonOptions))!.AsObject();
        Assert.IsTrue(legacyJson.Remove("raceAnalysisTraceLayouts"), "The fixture must emulate settings written before named Analysis layouts existed.");
        File.WriteAllText(path, legacyJson.ToJsonString(jsonOptions));

        var store = new JsonSettingsStore(path, credentials);
        var migrated = store.Load();
        var previous = migrated.RaceAnalysisTraceLayouts.UserLayouts.Single();
        Assert.AreEqual("Previous layout", previous.Name);
        Assert.AreEqual(previous.Id, migrated.RaceAnalysisTraceLayouts.ActiveLayoutId);
        Assert.AreEqual("throttle", previous.Layout.Rows.Single().PrimarySignalId);
        Assert.AreEqual("brake", previous.Layout.Rows.Single().SecondarySignalId);
        Assert.AreEqual(AnalysisTraceLayoutSet.FactoryDefaultId,
            AnalysisTraceLayoutSets.Choices(migrated.RaceAnalysisTraceLayouts).Single(choice => choice.IsFactory).Named.Id);

        var persisted = store.Load();
        Assert.HasCount(1, persisted.RaceAnalysisTraceLayouts.UserLayouts,
            "Loading migrated settings again must not duplicate the legacy layout.");
        Assert.AreEqual(previous.Id, persisted.RaceAnalysisTraceLayouts.ActiveLayoutId);
        Assert.IsTrue(JsonNode.Parse(File.ReadAllText(path))!.AsObject().ContainsKey("raceAnalysisTraceLayouts"));
    }

    [TestMethod]
    public async Task SettingsStore_ConcurrentSavesAreAtomicAndDoNotShareTemporaryFiles()
    {
        var directory = Path.Combine(Path.GetTempPath(), "iracing-coach-settings-concurrency", Guid.NewGuid().ToString("N"));
        var path = Path.Combine(directory, "settings.json");
        var machine = Path.Combine(directory, "machine.json");
        var store = new JsonSettingsStore(path, new FakeGarage61CredentialStore(Path.Combine(directory, "credential.dpapi")), machine);

        var saves = Enumerable.Range(0, 32).Select(index => Task.Run(() =>
        {
            var settings = new CompanionSettings { CoachHome = directory, FirstRunComplete = index % 2 == 0 };
            settings.LiveMonitor.ActiveLayoutId = LiveMonitorLayouts.FactoryRaceId;
            settings.LiveMonitor.OverallScale = 1 + index % 5 * .1;
            store.Save(settings);
        })).ToArray();

        await Task.WhenAll(saves);

        using var portable = JsonDocument.Parse(File.ReadAllText(path));
        using var local = JsonDocument.Parse(File.ReadAllText(machine));
        Assert.AreEqual(JsonValueKind.Object, portable.RootElement.ValueKind);
        Assert.AreEqual(JsonValueKind.Object, local.RootElement.ValueKind);
        Assert.IsEmpty(Directory.EnumerateFiles(directory, "*.tmp"), "Atomic settings writes must clean every unique temporary file.");
    }

    [TestMethod]
    public void SettingsStore_KeepsPhysicalMonitorPlacementOnOnePcOnly()
    {
        var directory = Path.Combine(Path.GetTempPath(), "iracing-coach-tests", Guid.NewGuid().ToString("N"));
        var portable = Path.Combine(directory, "settings.json");
        var credentials = new FakeGarage61CredentialStore(Path.Combine(directory, "credential.dpapi"));
        var firstMachine = Path.Combine(directory, "pc-one.json");
        var secondMachine = Path.Combine(directory, "pc-two.json");
        var first = new JsonSettingsStore(portable, credentials, firstMachine);
        var settings = new CompanionSettings { CoachHome = directory };
        settings.LiveMonitor.Left = 310;
        settings.LiveMonitor.Top = 180;
        settings.LiveMonitor.OverallScale = 1.6;
        settings.LiveMonitor.MonitorDeviceName = @"\\.\DISPLAY3";
        settings.LiveMonitor.UserLayouts.Add(new LiveMonitorNamedLayout { Id = "portable-layout", Name = "Portable layout", Tiles = [new LiveMonitorTile { MetricId = "speed" }] });
        settings.LiveMonitor.ActiveLayoutId = "portable-layout";
        first.Save(settings);

        var restoredOnFirstPc = first.Load();
        var restoredOnSecondPc = new JsonSettingsStore(portable, credentials, secondMachine).Load();

        Assert.AreEqual(310, restoredOnFirstPc.LiveMonitor.Left);
        Assert.AreEqual(1.6, restoredOnFirstPc.LiveMonitor.OverallScale);
        Assert.AreEqual(@"\\.\DISPLAY3", restoredOnFirstPc.LiveMonitor.MonitorDeviceName);
        Assert.IsNull(restoredOnSecondPc.LiveMonitor.Left);
        Assert.AreEqual(1, restoredOnSecondPc.LiveMonitor.OverallScale);
        Assert.AreEqual(string.Empty, restoredOnSecondPc.LiveMonitor.MonitorDeviceName);
        Assert.AreEqual("Portable layout", restoredOnSecondPc.LiveMonitor.UserLayouts.Single(layout => layout.Id == "portable-layout").Name);
        CollectionAssert.AreEquivalent(
            new[] { LiveMonitorLayouts.FactoryRaceId, LiveMonitorLayouts.FactoryQualifyingId },
            restoredOnSecondPc.LiveMonitor.UserLayouts.Where(layout => layout.Id != "portable-layout").Select(layout => layout.Id).ToArray());
        Assert.DoesNotContain("DISPLAY3", File.ReadAllText(portable));
    }

    [TestMethod]
    public void DurableArchive_PreparesAndRestoresAfterPathAndDriveStyleChange()
    {
        var testRoot = Path.Combine(Path.GetTempPath(), "iracing-coach-tests", Guid.NewGuid().ToString("N"));
        var source = Path.Combine(testRoot, "old-user", "Documents", "iRacing Coach");
        var destination = Path.Combine(testRoot, "redirected-documents", "iRacing Coach");
        Directory.CreateDirectory(Path.Combine(source, "data", "reports", "race-1"));
        File.WriteAllText(Path.Combine(source, "data", "reports", "race-1", "analysis.json"), "{\"race\":1}");
        Directory.CreateDirectory(Path.Combine(source, "data", "reports", "race-1", "garage61", "csv"));
        File.WriteAllText(Path.Combine(source, "data", "reports", "race-1", "garage61", "csv", "comparison.csv"), "lap,time\n1,30.0");
        Directory.CreateDirectory(Path.Combine(source, "data", "tire-models"));
        Directory.CreateDirectory(Path.Combine(source, "data", "driver-models"));
        Directory.CreateDirectory(Path.Combine(source, "data", "ai-coaching"));
        Directory.CreateDirectory(Path.Combine(source, "data", "tuning-experiments"));
        File.WriteAllText(Path.Combine(source, "data", "tire-models", "oval.json"), "{\"maturity\":\"observed\"}");
        File.WriteAllText(Path.Combine(source, "data", "driver-models", "braking.json"), "{\"samples\":12}");
        File.WriteAllText(Path.Combine(source, "data", "ai-coaching", "coach-1.json"), "{\"structuredResponse\":\"portable\"}");
        File.WriteAllText(Path.Combine(source, "data", "tuning-experiments", "experiment-1.json"), "{\"outcome\":\"improved\"}");
        Directory.CreateDirectory(Path.Combine(source, "setups"));
        File.WriteAllText(Path.Combine(source, "setups", "oval.sto"), "real setup bytes");
        File.WriteAllText(Path.Combine(source, "settings.json"), "{\"settingsSchemaVersion\":3}");
        var service = new DurableArchiveService();

        var initialized = service.Initialize(source, "0.6.0", "test");
        var prepared = service.PrepareForCopy(source, "0.6.0", "test");
        CopyDirectory(source, destination);
        var restored = service.Initialize(destination, "0.6.0", "test");

        Assert.IsTrue(prepared.SafeToCopy);
        Assert.AreEqual(initialized.ArchiveId, restored.ArchiveId);
        Assert.AreEqual(1, restored.Restored.Reports);
        Assert.AreEqual(1, restored.Restored.Setups);
        Assert.AreEqual(1, restored.Restored.Garage61Files);
        Assert.AreEqual(1, restored.Restored.TireModels);
        Assert.AreEqual(1, restored.Restored.DriverModels);
        Assert.AreEqual(1, restored.Restored.AiCoachingFiles);
        Assert.AreEqual(1, restored.Restored.TuningExperiments);
        StringAssert.Contains(restored.Message, "last copy check still matches every Coach file");
        Assert.DoesNotContain("portable", restored.Message, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("prepared copy", restored.Message, StringComparison.OrdinalIgnoreCase);
        Assert.IsTrue(File.Exists(Path.Combine(destination, DurableArchiveService.ManifestFileName)));
        Assert.IsTrue(File.Exists(Path.Combine(destination, DurableArchiveService.PortableStateFileName)));
        Assert.DoesNotContain(source, File.ReadAllText(Path.Combine(destination, DurableArchiveService.ManifestFileName)));
        Assert.IsTrue(File.Exists(Path.Combine(destination, "data", "reports", "race-1", "garage61", "csv", "comparison.csv")));
    }

    [TestMethod]
    public async Task DurableArchive_ConcurrentStateWritesAreSerializedAndLeaveNoTemporaryFiles()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-archive-concurrency", Guid.NewGuid().ToString("N"), "iRacing Coach");
        var initialized = new DurableArchiveService().Initialize(root, "0.14.0", "test");
        var start = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        var writers = Enumerable.Range(0, 32).Select(_ => Task.Run(async () =>
        {
            var service = new DurableArchiveService();
            await start.Task;
            for (var attempt = 0; attempt < 8; attempt++)
                service.MarkActive(root);
        })).ToArray();

        start.SetResult(true);
        await Task.WhenAll(writers);

        var statePath = Path.Combine(root, DurableArchiveService.PortableStateFileName);
        using var state = JsonDocument.Parse(File.ReadAllText(statePath));
        Assert.AreEqual(DurableArchiveService.CurrentSchemaVersion, state.RootElement.GetProperty("schemaVersion").GetInt32());
        Assert.AreEqual(initialized.ArchiveId, state.RootElement.GetProperty("archiveId").GetString());
        Assert.IsFalse(state.RootElement.GetProperty("safeToCopy").GetBoolean());
        Assert.AreEqual(
            0,
            Directory.EnumerateFiles(root, "*.tmp", SearchOption.AllDirectories).Count(),
            "Every temporary file must be removed after concurrent same-destination writes.");
    }

    [TestMethod]
    public void DefaultCoachHome_UsesTheCurrentWindowsDocumentsKnownFolder()
    {
        var documents = WindowsKnownFolders.Documents;
        Assert.AreEqual(Path.GetFullPath(Path.Combine(documents, "iRacing Coach")), Path.GetFullPath(CompanionSettings.DefaultCoachHome));
        Assert.IsFalse(CompanionSettings.DefaultCoachHome.Contains(@"C:\Users\joshu", StringComparison.OrdinalIgnoreCase) &&
            !documents.Contains(@"C:\Users\joshu", StringComparison.OrdinalIgnoreCase), "The durable root must follow redirected Documents rather than a baked-in user path.");
    }

    [TestMethod]
    public void DurableArchive_NewerSchemaStopsWithoutChangingArchive()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-tests", Guid.NewGuid().ToString("N"), "iRacing Coach");
        Directory.CreateDirectory(root);
        var manifestPath = Path.Combine(root, DurableArchiveService.ManifestFileName);
        var original = "{\"schemaVersion\":99,\"archiveId\":\"future-archive\"}";
        File.WriteAllText(manifestPath, original);

        Assert.Throws<ArchiveCompatibilityException>(() => new DurableArchiveService().Initialize(root, "0.6.0"));

        Assert.AreEqual(original, File.ReadAllText(manifestPath));
        Assert.IsFalse(File.Exists(Path.Combine(root, DurableArchiveService.PortableStateFileName)));
        Assert.IsFalse(Directory.Exists(Path.Combine(root, "data")));
    }

    [TestMethod]
    public void DurableArchive_MigrationIsNonDestructiveIdempotentAndBackedUp()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-tests", Guid.NewGuid().ToString("N"), "iRacing Coach");
        Directory.CreateDirectory(root);
        File.WriteAllText(Path.Combine(root, DurableArchiveService.ManifestFileName), "{\"schemaVersion\":0,\"archiveId\":\"legacy-id\",\"createdUtc\":\"2026-01-01T00:00:00Z\"}");
        var service = new DurableArchiveService();

        var first = service.Initialize(root, "0.6.0");
        var second = service.Initialize(root, "0.6.0");

        Assert.AreEqual("legacy-id", first.ArchiveId);
        Assert.AreEqual(first.ArchiveId, second.ArchiveId);
        Assert.AreEqual(1, Directory.EnumerateFiles(Path.Combine(root, "backups"), "archive-manifest-before-schema-1-*.json").Count());
        using var manifest = JsonDocument.Parse(File.ReadAllText(Path.Combine(root, DurableArchiveService.ManifestFileName)));
        Assert.AreEqual(1, manifest.RootElement.GetProperty("migrationHistory").GetArrayLength());
    }

    [TestMethod]
    public void DurableArchive_ResumesAnInterruptedMigrationJournal()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-tests", Guid.NewGuid().ToString("N"), "iRacing Coach");
        var backups = Path.Combine(root, "backups");
        Directory.CreateDirectory(backups);
        var legacy = "{\"schemaVersion\":0,\"archiveId\":\"interrupted-id\",\"createdUtc\":\"2026-01-01T00:00:00Z\"}";
        File.WriteAllText(Path.Combine(root, DurableArchiveService.ManifestFileName), legacy);
        File.WriteAllText(Path.Combine(backups, "archive-manifest-before-schema-1-backup.json"), legacy);
        File.WriteAllText(Path.Combine(backups, "archive-schema-1-migration.json"), "{\"schemaVersion\":1,\"fromVersion\":0,\"toVersion\":1,\"status\":\"started\"}");

        var restored = new DurableArchiveService().Initialize(root, "0.6.0");

        Assert.AreEqual("interrupted-id", restored.ArchiveId);
        Assert.AreEqual(1, restored.SchemaVersion);
        StringAssert.Contains(File.ReadAllText(Path.Combine(backups, "archive-schema-1-migration.json")), "complete");
        Assert.AreEqual(1, Directory.EnumerateFiles(backups, "archive-manifest-before-schema-1-*.json").Count());
    }

    [TestMethod]
    public void DurableArchive_MissingRawTelemetryKeepsReportAndRecordsRelocatableReference()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-tests", Guid.NewGuid().ToString("N"), "iRacing Coach");
        var report = Path.Combine(root, "data", "reports", "race-2");
        Directory.CreateDirectory(report);
        File.WriteAllText(Path.Combine(report, "analysis.json"), "{\"source\":\"Z:\\\\departed-user\\\\Documents\\\\iRacing\\\\telemetry\\\\race-2.ibt\",\"summary\":\"archived\"}");
        var service = new DurableArchiveService();

        var prepared = service.PrepareForCopy(root, "0.6.0", "test");
        var status = service.Initialize(root, "0.6.0", "test");

        Assert.IsTrue(prepared.SafeToCopy);
        Assert.AreEqual(1, status.Restored.UnresolvedSources);
        Assert.IsTrue(File.Exists(Path.Combine(report, "analysis.json")));
        var manifest = File.ReadAllText(Path.Combine(root, DurableArchiveService.ManifestFileName));
        Assert.Contains("race-2.ibt", manifest);
        Assert.DoesNotContain("departed-user", manifest);

        var relocated = Path.Combine(root, "located", "race-2.ibt");
        Directory.CreateDirectory(Path.GetDirectoryName(relocated)!);
        File.WriteAllText(relocated, "raw telemetry fixture");
        var locations = Path.Combine(root, "data", "race-index", "source-locations");
        Directory.CreateDirectory(locations);
        File.WriteAllText(Path.Combine(locations, "source-test.json"), JsonSerializer.Serialize(new { fileName = "race-2.ibt", currentPath = relocated, sha256 = "fixture" }));

        _ = service.PrepareForCopy(root, "0.6.0", "test");
        var remapped = service.Initialize(root, "0.6.0", "test");
        Assert.AreEqual(0, remapped.Restored.UnresolvedSources);
    }

    [TestMethod]
    public void SettingsStore_RemovesLegacyCredentialPropertyAfterLoadingIt()
    {
        var directory = Path.Combine(Path.GetTempPath(), "iracing-coach-tests", Guid.NewGuid().ToString("N"));
        var path = Path.Combine(directory, "settings.json");
        Directory.CreateDirectory(directory);
        File.WriteAllText(path, """{"coachHome":"ignored","garage61ApiKey":"legacy-test-key","settingsSchemaVersion":1}""");
        var credentials = new FakeGarage61CredentialStore(Path.Combine(directory, "credential.dpapi"));

        var actual = new JsonSettingsStore(path, credentials).Load();

        Assert.AreEqual("legacy-test-key", credentials.StoredToken);
        Assert.AreEqual(string.Empty, actual.Garage61ApiKey);
        Assert.DoesNotContain("legacy-test-key", File.ReadAllText(path));
        Assert.IsFalse(File.ReadAllText(path).Contains("garage61ApiKey", StringComparison.OrdinalIgnoreCase));
        Assert.AreEqual(5, actual.SettingsSchemaVersion);
    }

    [TestMethod]
    public void SavedGarage61Key_IsNeverPlacedInTheUiReplacementField()
    {
        using var state = new CompanionState(new FakeBackend());
        state.Settings.Garage61ApiKey = "already-saved-secret";
        Assert.AreEqual(string.Empty, state.Garage61KeyInput);
    }

    [TestMethod]
    public async Task ChatGptConnection_CancelsAnExistingLoginBeforeStartingAnother()
    {
        var coachEngine = new FakeCoachEngine();
        using var state = new CompanionState(
            new FakeBackend(),
            null,
            new DisconnectedLiveTelemetrySource(),
            coachEngine,
            new FakeGarage61CredentialStore(Path.Combine(Path.GetTempPath(), "unused.dpapi")));

        await state.ConnectChatGptAsync();
        await state.ConnectChatGptAsync(deviceCode: true);

        CollectionAssert.AreEqual(
            new[] { "start", "login:browser", "start", "cancel:login-1", "login:device" },
            coachEngine.Actions);
        Assert.AreEqual("login-2", state.PendingChatGptLoginId);
    }

    [TestMethod]
    public void HeaderAlert_IsOnlyShownForActionableProblems()
    {
        using var state = new CompanionState(new FakeBackend());

        state.Health.Clear();
        state.Health.Add(new("backend", "Race analysis", "ready", "Ready", true));
        state.Health.Add(new("garage61", "Garage61", "unavailable", "Not configured"));
        state.Health.Add(new("repository", "Coach data", "ready", "Portable folder ready"));
        Assert.IsNull(state.HeaderAlert, "Normal refresh state and an unconfigured optional service should remain silent.");

        state.Health[0] = new("backend", "Race analysis", "unavailable", "Could not start", true);
        Assert.AreEqual("Race analysis needs attention", state.HeaderAlert);

        state.Health[0] = new("backend", "Race analysis", "ready", "Ready", true);
        state.Health[1] = new("garage61", "Garage61", "warning", "Key saved · offline");
        Assert.AreEqual("Garage61 connection needs attention", state.HeaderAlert);
    }

    [TestMethod]
    public async Task HomeRaceAction_OpensTheMatchingEventSession()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-home-race-action", Guid.NewGuid().ToString("N"));
        using var state = new CompanionState(new FakeBackend(), new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;
        var dashboardRace = new RecentRace("dashboard-row", "Track", "Oval", "Car", "Today", "Open", "Analyzed", "Recorded", false, true, 4, 2)
        {
            EventKey = "8001",
            Selector = "8001"
        };
        var eventSession = dashboardRace with { Id = "session-row" };
        state.EventSessions.Add(eventSession);

        await state.OpenRaceFromHomeAsync(dashboardRace);

        Assert.AreEqual("analysis", state.CurrentPage);
        Assert.AreEqual("session-row", state.SelectedRaceSessionId);
    }

    [TestMethod]
    public void LiveTelemetry_PrioritizesCriticalStateAndUrgentSafeGlanceOverride()
    {
        var engine = new LiveTelemetryEngine();
        var now = DateTimeOffset.UtcNow;
        _ = engine.Update(new LiveTelemetrySample { Connected = true, Timestamp = now, Flag = "GREEN", Lap = 10, Brake = 0, SteeringWheelAngleRadians = 0, LateralAccelerationG = 0 }, true, false);
        var snapshot = engine.Update(new LiveTelemetrySample { Connected = true, Timestamp = now.AddMilliseconds(100), Flag = "BLACK FLAG", BlackFlag = true, Lap = 10, Brake = 0.8, SteeringWheelAngleRadians = 0.5, LateralAccelerationG = 1.8 }, true, false);

        Assert.AreEqual(LiveCuePriority.Critical, snapshot.PrimaryCue.Priority);
        StringAssert.Contains(snapshot.PrimaryCue.Message, "Black flag");
        Assert.IsTrue(snapshot.SafeGlance.UrgentOverride);
    }

    [TestMethod]
    public void LiveTelemetry_UsesRollingGapTrendAndSuppressesItUnderCaution()
    {
        var engine = new LiveTelemetryEngine();
        var start = DateTimeOffset.UtcNow.AddSeconds(-5);
        _ = engine.Update(new LiveTelemetrySample { Connected = true, Timestamp = start, Flag = "GREEN", Lap = 20, GapToAheadSeconds = 1.1, GapToBehindSeconds = 1.2, Brake = 0, SteeringWheelAngleRadians = 0, LateralAccelerationG = 0 }, true, false);
        var closing = engine.Update(new LiveTelemetrySample { Connected = true, Timestamp = start.AddSeconds(4), Flag = "GREEN", Lap = 20, GapToAheadSeconds = 0.7, GapToBehindSeconds = 1.8, Brake = 0, SteeringWheelAngleRadians = 0, LateralAccelerationG = 0 }, true, false);
        var caution = engine.Update(new LiveTelemetrySample { Connected = true, Timestamp = start.AddSeconds(4.1), Flag = "CAUTION", UnderCaution = true, Lap = 20, GapToAheadSeconds = 0.5 }, true, false);

        Assert.AreEqual(LiveGapTrend.Closing, closing.AheadGap.Trend);
        Assert.AreEqual(LiveGapTrend.Growing, closing.BehindGap.Trend, "A larger physical gap to the car behind means the gap is growing.");
        Assert.AreEqual(LiveGapTrend.Stale, caution.AheadGap.Trend);
        StringAssert.Contains(caution.AheadGap.UnavailableReason, "caution");
    }

    [TestMethod]
    public void LiveTelemetry_SeparatesFuelHardLimitFromStrategicWindow()
    {
        var engine = new LiveTelemetryEngine();
        var start = DateTimeOffset.UtcNow.AddSeconds(-3);
        _ = engine.Update(new LiveTelemetrySample { Connected = true, Timestamp = start, Flag = "GREEN", Lap = 1, FuelLiters = 6, LastLapSeconds = 31, Brake = 0 }, true, false);
        _ = engine.Update(new LiveTelemetrySample { Connected = true, Timestamp = start.AddSeconds(1), Flag = "GREEN", Lap = 2, FuelLiters = 5, LastLapSeconds = 31, Brake = 0 }, true, false);
        _ = engine.Update(new LiveTelemetrySample { Connected = true, Timestamp = start.AddSeconds(2), Flag = "GREEN", Lap = 3, FuelLiters = 4, LastLapSeconds = 31, Brake = 0 }, true, false);
        var snapshot = engine.Update(new LiveTelemetrySample { Connected = true, Timestamp = start.AddSeconds(3), Flag = "GREEN", Lap = 4, FuelLiters = 3, LastLapSeconds = 31, Brake = 0 }, true, false);

        Assert.AreEqual(2, snapshot.Pit.FuelHardLimitLaps);
        Assert.IsNull(snapshot.Pit.WindowOpensInLaps);
        Assert.IsNull(snapshot.Pit.WindowClosesInLaps);
        StringAssert.Contains(snapshot.Pit.UnavailableReason, "strategic");
    }

    [TestMethod]
    public void LiveTelemetry_RequiresThreeCleanLapsAndKeepsPaceSeparateFromRaceGap()
    {
        var engine = new LiveTelemetryEngine();
        var start = DateTimeOffset.UtcNow.AddSeconds(-5);
        _ = engine.Update(new LiveTelemetrySample { Connected = true, Timestamp = start, Flag = "GREEN", Lap = 1, LastLapSeconds = 31.1, LeaderLastLapSeconds = 30.4, GapToLeaderSeconds = 4.2 }, true, false);
        _ = engine.Update(new LiveTelemetrySample { Connected = true, Timestamp = start.AddSeconds(1), Flag = "GREEN", Lap = 2, LastLapSeconds = 31.0, LeaderLastLapSeconds = 30.4, GapToLeaderSeconds = 4.3 }, true, false);
        _ = engine.Update(new LiveTelemetrySample { Connected = true, Timestamp = start.AddSeconds(2), Flag = "GREEN", Lap = 3, LastLapSeconds = 30.9, LeaderLastLapSeconds = 30.4, GapToLeaderSeconds = 4.4 }, true, false);
        _ = engine.Update(new LiveTelemetrySample { Connected = true, Timestamp = start.AddSeconds(3), Flag = "GREEN", Lap = 4, LastLapSeconds = 30.8, LeaderLastLapSeconds = 30.4, GapToLeaderSeconds = 4.5 }, true, false);
        var snapshot = engine.Update(new LiveTelemetrySample { Connected = true, Timestamp = start.AddSeconds(4), Flag = "GREEN", Lap = 5, LastLapSeconds = 30.8, LeaderLastLapSeconds = 30.4, GapToLeaderSeconds = 4.5 }, true, false);

        Assert.IsNotNull(snapshot.PaceTarget.MinimumSeconds);
        Assert.IsNotNull(snapshot.PaceTarget.MaximumSeconds);
        Assert.AreEqual(EvidenceKind.Derived, snapshot.PaceTarget.Evidence);
        Assert.AreEqual(.4, snapshot.LastLapPaceDifferenceSeconds!.Value, .0001);
        Assert.AreEqual(4.5, snapshot.LeaderGap.Seconds!.Value, .0001, "The physical race gap must remain distinct from the lap-time pace difference.");
    }

    [TestMethod]
    public void LiveTelemetry_SafeGlanceDelaysOrdinaryTrafficCueUntilStraight()
    {
        var engine = new LiveTelemetryEngine();
        var start = DateTimeOffset.UtcNow.AddSeconds(-5);
        var initial = engine.Update(new LiveTelemetrySample { Connected = true, Timestamp = start, Flag = "GREEN", Lap = 8, GapToAheadSeconds = 1.2, Brake = 0, SteeringWheelAngleRadians = 0, LateralAccelerationG = 0 }, true, false);
        var corner = engine.Update(new LiveTelemetrySample { Connected = true, Timestamp = start.AddSeconds(4), Flag = "GREEN", Lap = 8, GapToAheadSeconds = .7, Brake = .35, SteeringWheelAngleRadians = .4, LateralAccelerationG = 1.5 }, true, false);
        var straight = engine.Update(new LiveTelemetrySample { Connected = true, Timestamp = start.AddSeconds(4.2), Flag = "GREEN", Lap = 8, GapToAheadSeconds = .68, Brake = 0, SteeringWheelAngleRadians = 0, LateralAccelerationG = .05 }, true, false);

        Assert.AreEqual(initial.PrimaryCue.Message, corner.PrimaryCue.Message);
        Assert.AreEqual(LiveCueSuppressionReason.SafeGlanceDelay, corner.PrimaryCue.SuppressionReason);
        StringAssert.Contains(straight.PrimaryCue.Message, "ahead");
        Assert.AreEqual(LiveCueSuppressionReason.None, straight.PrimaryCue.SuppressionReason);
    }

    [TestMethod]
    public void LiveTelemetry_MissingDynamicsCannotBeAssumedToBeAStraight()
    {
        var engine = new LiveTelemetryEngine();
        var start = DateTimeOffset.UtcNow.AddSeconds(-5);
        var initial = engine.Update(new LiveTelemetrySample
        {
            Connected = true, Timestamp = start, Flag = "GREEN", Lap = 8, GapToAheadSeconds = 1.2,
            Brake = 0, SteeringWheelAngleRadians = 0, LateralAccelerationG = 0
        }, true, false);
        var missingDynamics = engine.Update(new LiveTelemetrySample
        {
            Connected = true, Timestamp = start.AddSeconds(4), Flag = "GREEN", Lap = 8, GapToAheadSeconds = .7
        }, true, false);
        var measuredStraight = engine.Update(new LiveTelemetrySample
        {
            Connected = true, Timestamp = start.AddSeconds(4.2), Flag = "GREEN", Lap = 8, GapToAheadSeconds = .68,
            Brake = 0, SteeringWheelAngleRadians = 0, LateralAccelerationG = .05
        }, true, false);

        Assert.AreEqual(initial.PrimaryCue.Message, missingDynamics.PrimaryCue.Message);
        Assert.IsFalse(missingDynamics.SafeGlance.IsGlanceOpportunity);
        Assert.AreEqual(LiveCueSuppressionReason.SafeGlanceDelay, missingDynamics.PrimaryCue.SuppressionReason);
        StringAssert.Contains(measuredStraight.PrimaryCue.Message, "ahead");
    }

    [TestMethod]
    public void LiveTelemetry_UsesOnlyFullyObservedCleanLapsForPaceAndFuelEvidence()
    {
        var engine = new LiveTelemetryEngine();
        var start = DateTimeOffset.UtcNow.AddSeconds(-10);
        LiveRaceSnapshot Sample(int lap, int second, double fuel, bool caution = false) => engine.Update(new LiveTelemetrySample
        {
            Connected = true,
            Timestamp = start.AddSeconds(second),
            Flag = caution ? "YELLOW" : "GREEN",
            UnderCaution = caution,
            Lap = lap,
            LastLapSeconds = 30,
            FuelLiters = fuel,
            Brake = 0,
            SteeringWheelAngleRadians = 0,
            LateralAccelerationG = 0
        }, true, false);

        _ = Sample(10, 0, 10); // Connection began mid-lap; lap 10 is deliberately ineligible.
        var partialBoundary = Sample(11, 1, 9);
        var firstCleanLap = Sample(12, 2, 8);
        _ = Sample(12, 3, 7.8, caution: true);
        var confoundedBoundary = Sample(13, 4, 7);
        var secondCleanLap = Sample(14, 5, 6);
        var thirdCleanLap = Sample(15, 6, 5);

        Assert.IsNull(partialBoundary.FuelLapsRemaining, "A partial first lap must not seed fuel range.");
        Assert.IsNull(firstCleanLap.FuelLapsRemaining, "One measured burn is not a sufficient fuel baseline.");
        Assert.IsNull(confoundedBoundary.FuelLapsRemaining, "A caution observed anywhere on the lap must exclude that lap.");
        Assert.IsNotNull(secondCleanLap.FuelLapsRemaining, "Two fully observed clean burns support a range estimate.");
        Assert.IsNull(secondCleanLap.PaceTarget.MinimumSeconds, "Only two fully observed clean laps exist at this point.");
        Assert.IsNotNull(thirdCleanLap.PaceTarget.MinimumSeconds);
    }

    [TestMethod]
    public void LiveTelemetry_CurrentRunPhaseResetsWhenPitRoadIsObserved()
    {
        var engine = new LiveTelemetryEngine();
        var start = DateTimeOffset.UtcNow.AddSeconds(-10);
        LiveRaceSnapshot Lap(int lap, bool onPitRoad = false) => engine.Update(new LiveTelemetrySample
        {
            Connected = true,
            Timestamp = start.AddSeconds(lap),
            Flag = "GREEN",
            Lap = lap,
            LastLapSeconds = 30,
            FuelLiters = 20 - lap,
            OnPitRoad = onPitRoad,
            Brake = 0,
            SteeringWheelAngleRadians = 0,
            LateralAccelerationG = 0
        }, true, false);

        _ = Lap(1); _ = Lap(2); _ = Lap(3); _ = Lap(4); _ = Lap(5); _ = Lap(6);
        var establishedRun = Lap(7);
        var pitEntry = Lap(7, onPitRoad: true);

        Assert.AreEqual(5, establishedRun.GreenLapsOnTires);
        Assert.AreEqual("Middle run", establishedRun.TirePhase);
        Assert.AreEqual(0, pitEntry.GreenLapsOnTires);
        Assert.AreEqual(0, pitEntry.TotalLapsOnTires);
        Assert.AreEqual("Early run", pitEntry.TirePhase);
    }

    [TestMethod]
    public void LiveTelemetry_RunPhaseCountsOnlyFullyObservedCleanGreenLaps()
    {
        var engine = new LiveTelemetryEngine();
        var start = DateTimeOffset.UtcNow.AddSeconds(-8);
        LiveRaceSnapshot Sample(int lap, int second, bool repair = false, bool caution = false) => engine.Update(new LiveTelemetrySample
        {
            Connected = true,
            Timestamp = start.AddSeconds(second),
            Flag = caution ? "YELLOW" : "GREEN",
            Lap = lap,
            LastLapSeconds = 30,
            RepairFlag = repair,
            UnderCaution = caution,
            Brake = 0,
            SteeringWheelAngleRadians = 0,
            LateralAccelerationG = 0
        }, true, false);

        _ = Sample(1, 0); // Deliberately partial first lap.
        _ = Sample(2, 1);
        _ = Sample(2, 2, repair: true);
        var afterRepairLap = Sample(3, 3);
        _ = Sample(3, 4, caution: true);
        var afterCautionLap = Sample(4, 5);
        var afterCleanLap = Sample(5, 6);

        Assert.AreEqual(0, afterRepairLap.GreenLapsOnTires, "Repair-confounded laps cannot advance a clean run phase.");
        Assert.AreEqual(0, afterCautionLap.GreenLapsOnTires, "Caution-confounded laps cannot advance a clean run phase.");
        Assert.AreEqual(1, afterCleanLap.GreenLapsOnTires);
        Assert.AreEqual(1, afterCleanLap.CautionLapsOnTires);
    }

    [TestMethod]
    [DoNotParallelize]
    public async Task LiveTelemetryService_PublishesWithoutDroppedFramesAndTracksComputeLatency()
    {
        using var service = new LiveTelemetryService(new TestLiveTelemetrySource(), new LiveMonitorLayout());
        var capturedFrames = 0;
        service.FrameCaptured += _ => Interlocked.Increment(ref capturedFrames);
        service.Start();
        await WaitForFrames(5, TimeSpan.FromSeconds(2));
        var warmFrameCount = service.Current.FramesRead;
        var startupDrops = service.Current.DroppedFrames;
        await WaitForFrames(warmFrameCount + 20, TimeSpan.FromSeconds(2));

        Assert.IsGreaterThanOrEqualTo(warmFrameCount + 20, service.Current.FramesRead);
        Assert.IsGreaterThanOrEqualTo(warmFrameCount + 20, capturedFrames);
        Assert.AreEqual(startupDrops, service.Current.DroppedFrames, "The 60 Hz service must not drop frames after its one-time JIT/startup warmup.");
        Assert.IsGreaterThanOrEqualTo(0, service.Current.RenderLatencyMs);
        Assert.IsLessThan(25, service.Current.RenderLatencyMs);
        Assert.IsTrue(service.Current.Snapshot.Connected);
        Assert.AreEqual(60, service.Current.SourceTickRate);

        async Task WaitForFrames(long target, TimeSpan timeout)
        {
            var timer = Stopwatch.StartNew();
            while (service.Current.FramesRead < target && timer.Elapsed < timeout) await Task.Delay(10);
        }
    }

    [TestMethod]
    [DoNotParallelize]
    public async Task LiveTelemetryService_CapturesA240HzSourceWithoutTheOld125HzPollingCeiling()
    {
        using var service = new LiveTelemetryService(new CadencedLiveTelemetrySource(240), new LiveMonitorLayout());
        service.Start();
        var timer = Stopwatch.StartNew();
        await WaitUntilAsync(() => service.Current.FramesRead >= 170, TimeSpan.FromSeconds(1.25));
        timer.Stop();

        Assert.IsGreaterThanOrEqualTo(170, service.Current.FramesRead,
            "A 240 Hz SDK stream must not be capped near the previous 125 Hz polling rate.");
        Assert.IsLessThan(1.25, timer.Elapsed.TotalSeconds);
        Assert.AreEqual(240, service.Current.SourceTickRate);
        Assert.IsLessThanOrEqualTo(8, service.Current.DroppedFrames,
            "Normal scheduler jitter may lose a few ticks, but sustained source loss is not acceptable.");
    }

    [TestMethod]
    [DoNotParallelize]
    public async Task LiveTelemetryService_CountsSkippedSdkTicksAsDroppedFrames()
    {
        using var service = new LiveTelemetryService(new SkippingLiveTelemetrySource(), new LiveMonitorLayout());
        service.Start();
        await WaitUntilAsync(() => service.Current.FramesRead >= 5, TimeSpan.FromSeconds(1));

        Assert.IsGreaterThan(0, service.Current.DroppedFrames);
    }

    [TestMethod]
    [DoNotParallelize]
    public async Task LiveTelemetryService_UsesLowCostDiscoveryCadenceAndStopsCleanly()
    {
        var source = new CountingDisconnectedLiveTelemetrySource();
        var service = new LiveTelemetryService(source, new LiveMonitorLayout());
        service.Start();
        await Task.Delay(260);

        Assert.IsGreaterThanOrEqualTo(3, source.ReadCount, "Disconnected discovery should continue checking for iRacing.");
        Assert.IsLessThanOrEqualTo(10, source.ReadCount, "Disconnected discovery must not spin at the connected 500 Hz polling cadence.");

        service.Dispose();
        var readsAtDispose = source.ReadCount;
        await Task.Delay(80);
        Assert.AreEqual(readsAtDispose, source.ReadCount, "Disposal must stop and join the telemetry worker before returning.");
        Assert.IsTrue(source.Disposed);
    }

    [TestMethod]
    [DoNotParallelize]
    public async Task LiveTelemetryService_SessionEpochAdvancesOnlyAtStreamBoundaries()
    {
        var source = new SwitchableLiveTelemetrySource { Connected = false, Lap = 2 };
        using var service = new LiveTelemetryService(source, new LiveMonitorLayout());
        service.Start();
        await WaitUntilAsync(() => service.Current.FramesRead >= 2, TimeSpan.FromSeconds(1));
        var initialEpoch = service.Current.SessionEpoch;

        source.Connected = true;
        await WaitUntilAsync(() => service.Current.Snapshot.Connected, TimeSpan.FromSeconds(1));
        var connectedEpoch = service.Current.SessionEpoch;
        Assert.AreEqual(initialEpoch + 1, connectedEpoch);

        foreach (var lap in Enumerable.Range(3, 4))
        {
            source.Lap = lap;
            await WaitUntilAsync(() => service.Current.Snapshot.Lap == lap, TimeSpan.FromSeconds(1));
        }
        Assert.AreEqual(connectedEpoch, service.Current.SessionEpoch, "Ordinary forward lap progress must keep the current chart session.");
        Assert.IsNotNull(service.Current.Snapshot.PaceTarget.MinimumSeconds, "The pre-reset session should establish a clean pace baseline.");
        Assert.IsNotNull(service.Current.Snapshot.FuelLapsRemaining, "The pre-reset session should establish a fuel baseline.");

        source.Lap = 1;
        await WaitUntilAsync(() => service.Current.Snapshot.Lap == 1 && service.Current.SessionEpoch > connectedEpoch, TimeSpan.FromSeconds(1));
        var regressedEpoch = service.Current.SessionEpoch;
        Assert.AreEqual(connectedEpoch + 1, regressedEpoch, "A lap-counter reset marks a new telemetry session.");
        Assert.IsNull(service.Current.Snapshot.PaceTarget.MinimumSeconds, "Session-boundary resets must also clear engine evidence.");
        Assert.IsNull(service.Current.Snapshot.FuelLapsRemaining, "Fuel evidence must not cross a session boundary.");

        source.Connected = false;
        await WaitUntilAsync(() => !service.Current.Snapshot.Connected && service.Current.SessionEpoch > regressedEpoch, TimeSpan.FromSeconds(1));
        var disconnectedEpoch = service.Current.SessionEpoch;
        Assert.AreEqual(regressedEpoch + 1, disconnectedEpoch);

        source.Connected = true;
        await WaitUntilAsync(() => service.Current.Snapshot.Connected && service.Current.SessionEpoch > disconnectedEpoch, TimeSpan.FromSeconds(1));
        Assert.AreEqual(disconnectedEpoch + 1, service.Current.SessionEpoch);
    }

    [TestMethod]
    public void LiveTelemetry_DriverInputCueRequiresCleanPersonalBaselineAndPersistence()
    {
        var engine = new LiveTelemetryEngine();
        var start = DateTimeOffset.UtcNow.AddSeconds(-10);
        LiveRaceSnapshot Lap(int number, double brake) => engine.Update(new LiveTelemetrySample
        {
            Connected = true,
            Timestamp = start.AddSeconds(number),
            Flag = "GREEN",
            Lap = number,
            LapsRemaining = 20 - number,
            GapToAheadSeconds = 3,
            GapToBehindSeconds = 3,
            LastLapSeconds = 30,
            LeaderLastLapSeconds = 30,
            LapDistancePercent = .25,
            Brake = brake,
            SteeringWheelAngleRadians = 0,
            LateralAccelerationG = 0
        }, true, false);

        _ = Lap(1, .5);
        _ = Lap(2, .5);
        _ = Lap(3, .5);
        _ = Lap(4, .5);
        _ = Lap(5, .8);
        var transient = Lap(6, .8);
        _ = Lap(7, .8);
        var persistent = Lap(8, .8);

        Assert.AreNotEqual(LiveCuePriority.Coaching, transient.PrimaryCue.Priority, "One unusual pass must not trigger a driving cue.");
        Assert.AreEqual(LiveCuePriority.Coaching, persistent.PrimaryCue.Priority);
        StringAssert.Contains(persistent.PrimaryCue.Message, "Braking zone");
        StringAssert.Contains(persistent.PrimaryCue.Message, "clean baseline");
        StringAssert.Contains(persistent.PrimaryCue.Message, "3 comparable laps");
        Assert.AreEqual(EvidenceKind.Derived, persistent.PrimaryCue.Evidence);
    }

    [TestMethod]
    public void LiveTelemetry_RepairConfoundedLapsCannotCreateDriverInputCue()
    {
        var engine = new LiveTelemetryEngine();
        var start = DateTimeOffset.UtcNow.AddSeconds(-10);
        LiveRaceSnapshot Lap(int number, double brake, bool repair = false) => engine.Update(new LiveTelemetrySample
        {
            Connected = true,
            Timestamp = start.AddSeconds(number),
            Flag = "GREEN",
            Lap = number,
            LapsRemaining = 20 - number,
            GapToAheadSeconds = 3,
            GapToBehindSeconds = 3,
            LastLapSeconds = 30,
            LeaderLastLapSeconds = 30,
            LapDistancePercent = .25,
            Brake = brake,
            RepairFlag = repair,
            SteeringWheelAngleRadians = 0,
            LateralAccelerationG = 0
        }, true, false);

        _ = Lap(1, .5); _ = Lap(2, .5); _ = Lap(3, .5); _ = Lap(4, .8, repair: true);
        _ = Lap(5, .8, repair: true); _ = Lap(6, .8, repair: true); var result = Lap(7, .8, repair: true);

        Assert.AreNotEqual(LiveCuePriority.Coaching, result.PrimaryCue.Priority);
        Assert.DoesNotContain("Braking zone", result.PrimaryCue.Message);
    }

    [TestMethod]
    public void LiveTelemetry_DisconnectClearsSessionSpecificPaceAndInputBaselines()
    {
        var engine = new LiveTelemetryEngine();
        var start = DateTimeOffset.UtcNow.AddSeconds(-5);
        _ = engine.Update(new LiveTelemetrySample { Connected = true, Timestamp = start, Lap = 1, LastLapSeconds = 30, Flag = "GREEN" }, true, false);
        _ = engine.Update(new LiveTelemetrySample { Connected = true, Timestamp = start.AddSeconds(1), Lap = 2, LastLapSeconds = 30, Flag = "GREEN" }, true, false);
        _ = engine.Update(new LiveTelemetrySample { Connected = true, Timestamp = start.AddSeconds(2), Lap = 3, LastLapSeconds = 30, Flag = "GREEN" }, true, false);
        _ = engine.Update(new LiveTelemetrySample { Connected = true, Timestamp = start.AddSeconds(3), Lap = 4, LastLapSeconds = 30, Flag = "GREEN" }, true, false);
        var established = engine.Update(new LiveTelemetrySample { Connected = true, Timestamp = start.AddSeconds(4), Lap = 5, LastLapSeconds = 30, Flag = "GREEN" }, true, false);
        _ = engine.Update(new LiveTelemetrySample { Connected = false, Timestamp = start.AddSeconds(5) }, true, false);
        var reconnected = engine.Update(new LiveTelemetrySample { Connected = true, Timestamp = start.AddSeconds(6), Lap = 1, LastLapSeconds = 35, Flag = "GREEN" }, true, false);

        Assert.IsNotNull(established.PaceTarget.MinimumSeconds);
        Assert.IsNull(reconnected.PaceTarget.MinimumSeconds);
        Assert.AreEqual(EvidenceKind.Unavailable, reconnected.PaceTarget.Evidence);
    }

    private static void CopyDirectory(string source, string destination)
    {
        Directory.CreateDirectory(destination);
        foreach (var directory in Directory.EnumerateDirectories(source, "*", SearchOption.AllDirectories))
            Directory.CreateDirectory(Path.Combine(destination, Path.GetRelativePath(source, directory)));
        foreach (var file in Directory.EnumerateFiles(source, "*", SearchOption.AllDirectories))
            File.Copy(file, Path.Combine(destination, Path.GetRelativePath(source, file)), overwrite: true);
    }

    private static JsonElement HomeAnalysisResponse(string analysisPath = "", string sessionType = "Race", string? selector = null)
    {
        var partial = JsonSerializer.SerializeToElement(new
        {
        ok = true,
        analysis_id = "home-summary-test",
        selector = selector ?? "selector-test",
        analysis_path = analysisPath,
        selection = new { sim_session_type = sessionType, group_id = selector },
        race_card = new
        {
            title = "Recorded race",
            bottom_line = new { evidence_type = "measured", text = "Recorded telemetry is ready." },
            actions = Array.Empty<object>(),
            corner_playbook = new { rows = Array.Empty<object>() },
            race_triggers = Array.Empty<object>(),
            evidence_appendix = Array.Empty<object>()
        },
        analysis_view = new
        {
            schema_version = 1,
            analysis_profile_version = "post-race-foundations-v13",
            identity = new { event_type = sessionType, track_name = "Recorded Track", car_name = "Recorded Car" },
            race_summary = new
            {
                recorded_laps = 20,
                green_laps_estimated = 18,
                caution_laps_estimated = 2,
                pit_stops_detected = 1,
                fuel_used_gal = 6.4
            },
            runs = new[]
            {
                new
                {
                    green_laps = 10,
                    pace = new { green_lap_time_slope_s_per_lap = .012 },
                    tire_observation = new { lowest_remaining_percent = 91.5, lowest_remaining_tire = "RF" },
                    driving_load = new { early_brake_vs_late_percent = 4.2, early_steer_vs_late_percent = -2.1 }
                }
            },
            laps = new[]
            {
                new { complete = true, pit_time_s = 0d, flag_state = "green", lap_time_s = 30.125 },
                new { complete = true, pit_time_s = 0d, flag_state = "green", lap_time_s = 30.250 }
            }
            }
        });
        return CurrentAnalysisEnvelope(partial);
    }

    private static JsonElement CurrentAnalysisEnvelope(JsonElement partial, double totalMilliseconds = 0)
    {
        var root = JsonNode.Parse(partial.GetRawText())!.AsObject();
        root["ok"] = true;
        root["analysis_id"] ??= "analysis-test";
        root["selector"] ??= "selector-test";
        root["selection"] ??= new JsonObject();
        root["context"] ??= new JsonObject();
        root["analysis_cache"] ??= new JsonObject();
        root["knowledge_cache"] ??= new JsonObject();
        root["historical_runs_considered"] ??= 0;
        root["race_summary"] ??= new JsonObject();
        root["race_timeline"] ??= new JsonObject();
        root["damage_repair"] ??= new JsonObject();
        root["strategy_forecast"] ??= new JsonObject();
        root["data_quality"] ??= new JsonObject();
        root["source_files"] ??= new JsonArray();
        root["source_channel_coverage"] ??= new JsonObject();
        root["analysis_path"] ??= string.Empty;
        root["report_path"] ??= string.Empty;
        root["race_card_path"] ??= string.Empty;
        root["race_card"] ??= new JsonObject();
        root["timing"] = new JsonObject { ["contract_version"] = 1, ["total_ms"] = totalMilliseconds, ["analysis_cache_hit"] = false };
        root["artifacts"] ??= new JsonObject();

        var view = root["analysis_view"]!.AsObject();
        view["schema_version"] = 1;
        view["analysis_profile_version"] ??= null;
        view["identity"] ??= new JsonObject();
        view["race_summary"] ??= new JsonObject();
        view["race_grades"] ??= new JsonObject();
        view["runs"] ??= new JsonArray();
        view["laps"] ??= new JsonArray();
        view["lap_traces"] ??= new JsonObject();
        view["track_profile"] ??= new JsonObject();
        view["track_geometry"] ??= new JsonObject();
        view["race_replay"] ??= new JsonObject();
        view["tire_learning"] ??= new JsonObject();
        view["garage61_representative_laps"] ??= new JsonObject();
        view["technical_insights"] ??= new JsonArray();
        view["corner_tire_age"] ??= new JsonObject();
        view["groove_evolution"] ??= new JsonObject();
        view["strategy"] ??= new JsonObject();
        view["damage_repair"] ??= new JsonObject();
        view["setup_telemetry"] ??= new JsonObject();
        view["conditions"] ??= new JsonObject();
        view["data_quality"] ??= new JsonObject();
        return JsonSerializer.SerializeToElement(root);
    }

    private static void WriteArchivedAnalysis(string path, string selector)
    {
        File.WriteAllText(path, JsonSerializer.Serialize(new
        {
            schema_version = 2,
            analysis_id = "archived-analysis",
            identity = new
            {
                event_type = "Race",
                subsession_id = 8001,
                track_name = "Test Track",
                car_name = "Test Car"
            },
            source = new
            {
                selection = new
                {
                    group_id = selector,
                    subsession_id = 8001,
                    sim_session_num = 1,
                    sim_session_type = "Race"
                }
            },
            race_summary = new { recorded_laps = 7, pit_stops_detected = 0 },
            laps = Array.Empty<object>(),
            runs = Array.Empty<object>(),
            lap_traces = new { traces = Array.Empty<object>() },
            track_profile = new
            {
                shape = Array.Empty<object>(),
                detected_corner_segments = Array.Empty<object>()
            },
            strategy = new { },
            damage_repair = new { },
            data_quality = new { }
        }));
    }

    private static JsonElement DashboardWithFinalizedRaces(int count, bool isFixedSetup = true) => JsonSerializer.SerializeToElement(new
    {
        ok = true,
        races = Enumerable.Range(1, count).Select(index => new
        {
            group_id = $"subsession:{9000 + index}:1",
            subsession_id = 9000 + index,
            session_id = 8000 + index,
            sim_session_type = "Race",
            event_type = "Race",
            is_race = true,
            valid = true,
            is_fixed_setup = isFixedSetup,
            track_name = $"Recorded Track {index}",
            track_config_name = "Oval",
            car_path = "recorded-car",
            start_time_utc = $"2026-08-01T{index:00}:00:00Z",
            file_count = 1,
            files = new[] { $"recording-{index}.ibt" },
            analysis_status = "not_analyzed",
            analysis = (object?)null
        }).ToArray()
    });

    private static JsonElement DashboardNamed(string track) => JsonSerializer.SerializeToElement(new
    {
        ok = true,
        races = new[]
        {
            new
            {
                group_id = "subsession:9901:1",
                subsession_id = 9901,
                session_id = 8901,
                sim_session_type = "Race",
                event_type = "Race",
                is_race = true,
                valid = true,
                is_fixed_setup = true,
                track_name = track,
                track_config_name = "Oval",
                car_path = "recorded-car",
                start_time_utc = "2026-08-01T01:00:00Z",
                file_count = 1,
                files = new[] { "recording.ibt" },
                analysis_status = "not_analyzed",
                analysis = (object?)null
            }
        }
    });

    private static string ArgumentText(object arguments, string property)
    {
        var serialized = JsonSerializer.SerializeToElement(arguments);
        return serialized.GetProperty(property).GetString() ?? string.Empty;
    }

    private static CompanionState CreateIsolatedState(IBackendClient backend, string root) => new(
        backend: backend,
        settingsStore: null,
        liveTelemetrySource: new DisconnectedLiveTelemetrySource(),
        coachEngine: new DisabledCoachEngineSupervisor(),
        garage61Credentials: new FakeGarage61CredentialStore(Path.Combine(root, "garage61.dpapi")),
        archive: null,
        pathProvider: new IsolatedCompanionPathProvider(root),
        allowExternalHostActions: false);

    private static JsonElement DiscoveryWithFinalizedRaces(int count) => JsonSerializer.SerializeToElement(new
    {
        sessions = Enumerable.Range(1, count).Select(index => new
        {
            group_id = $"subsession:{9000 + index}:1",
            subsession_id = 9000 + index,
            session_id = 8000 + index,
            sim_session_type = "Race",
            event_type = "Race",
            is_race = true,
            valid = true,
            is_fixed_setup = true,
            track_name = $"Recorded Track {index}",
            track_config_name = "Oval",
            car_path = "recorded-car",
            start_time_utc = $"2026-08-01T{index:00}:00:00Z",
            file_count = 1,
            files = new[] { $"recording-{index}.ibt" },
            analysis_status = "not_analyzed",
            analysis = (object?)null
        }).ToArray()
    });

    private static void WriteUiAnalysisCache(string coachHome, string selector, JsonElement response, int schemaVersion = 13, string sessionType = "Race", string? storedSelector = null, int projectionVersion = 1)
    {
        var directory = Path.Combine(coachHome, "data", "ui-analysis-cache");
        Directory.CreateDirectory(directory);
        var persistedSelector = storedSelector ?? selector;
        response = WithRequestedSelector(response, persistedSelector);
        File.WriteAllText(UiAnalysisCachePath(coachHome, selector, sessionType), JsonSerializer.Serialize(new
        {
            schemaVersion,
            projectionVersion,
            sessionPhase = SessionPhase(sessionType),
            selector = persistedSelector,
            sourceLastWriteUtc = (string?)null,
            savedUtc = DateTimeOffset.UtcNow,
            response
        }));
    }

    private static string UiAnalysisCachePath(string coachHome, string selector, string sessionType = "Race")
    {
        var directory = Path.Combine(coachHome, "data", "ui-analysis-cache");
        Directory.CreateDirectory(directory);
        var key = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes($"{SessionPhase(sessionType)}|{selector}"))).ToLowerInvariant();
        return Path.Combine(directory, key + ".json");
    }

    private static string SessionPhase(string sessionType) =>
        sessionType.Contains("qual", StringComparison.OrdinalIgnoreCase) ? "qualifying" : sessionType.ToLowerInvariant();

    private static JsonElement WithRequestedSelector(JsonElement response, string selector)
    {
        var root = JsonNode.Parse(response.GetRawText())?.AsObject() ?? new JsonObject();
        if (root["selection"] is not JsonObject selection)
        {
            selection = new JsonObject();
            root["selection"] = selection;
        }
        if (selection["group_id"] is null) selection["group_id"] = selector;
        return JsonSerializer.SerializeToElement(root);
    }

    private static async Task WaitUntilAsync(Func<bool> predicate, TimeSpan timeout)
    {
        var deadline = DateTimeOffset.UtcNow + timeout;
        while (!predicate() && DateTimeOffset.UtcNow < deadline) await Task.Delay(20);
        Assert.IsTrue(predicate(), $"Condition was not met within {timeout}.");
    }

    private static string CompanionRoot() => TestRepositoryPaths.CompanionAppRoot;

    private sealed class FakeBackend(
        JsonElement? dashboard = null,
        JsonElement? tuning = null,
        Exception? failure = null,
        TimeSpan? callDelay = null,
        JsonElement? analysis = null,
        JsonElement? discovery = null,
        TimeSpan? analysisDelay = null,
        int analysisFailuresBeforeSuccess = 0,
        Func<string, int, object, JsonElement?>? responseOverride = null) : IBackendClient
    {
        private int _toolCalls;
        private int _garage61Calls;
        private int _analyzeCalls;
        private readonly ConcurrentDictionary<string, int> _callsByTool = new(StringComparer.Ordinal);
        public int ToolCalls => Volatile.Read(ref _toolCalls);
        public int Garage61Calls => Volatile.Read(ref _garage61Calls);
        public int AnalyzeCalls => Volatile.Read(ref _analyzeCalls);

        public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default) =>
            Task.FromResult(new BackendHealthResult(true, "iracing-coach-local", "0.3.0", "2025-06-18", 17, TimeSpan.FromMilliseconds(4)));

        public async Task<JsonElement> CallToolAsync(BackendConfiguration configuration, string toolName, object arguments, CancellationToken cancellationToken = default)
        {
            Interlocked.Increment(ref _toolCalls);
            var toolCall = _callsByTool.AddOrUpdate(toolName, 1, static (_, count) => count + 1);
            if (toolName.Contains("garage61", StringComparison.OrdinalIgnoreCase)) Interlocked.Increment(ref _garage61Calls);
            var analysisCall = toolName == "analyze_iracing_race" ? Interlocked.Increment(ref _analyzeCalls) : 0;
            if (toolName == "analyze_iracing_race" && analysisDelay is { } analysisPause)
                await Task.Delay(analysisPause, cancellationToken);
            else if (callDelay is { } delay)
                await Task.Delay(delay, cancellationToken);
            if (failure is not null && toolName == "analyze_iracing_race") throw failure;
            if (toolName == "analyze_iracing_race" && analysisCall <= analysisFailuresBeforeSuccess)
                throw new IOException("Transient analysis read failure.");
            if (responseOverride?.Invoke(toolName, toolCall, arguments) is { } overridden) return overridden;
            var value = toolName switch
            {
                "iracing_companion_dashboard" when dashboard.HasValue => dashboard.Value,
                "iracing_companion_dashboard" => JsonSerializer.SerializeToElement(new { ok = true, races = Array.Empty<object>() }),
                "discover_iracing_sessions" when discovery.HasValue => discovery.Value,
                "discover_iracing_sessions" => JsonSerializer.SerializeToElement(new { sessions = Array.Empty<object>() }),
                "catalog_iracing_setups" => JsonSerializer.SerializeToElement(new { ok = true, entries = Array.Empty<object>() }),
                "garage61_auth_status" => JsonSerializer.SerializeToElement(new { ok = false, configured = false, status = "not_configured" }),
                "recommend_open_setup_tuning" when tuning.HasValue => tuning.Value,
                "analyze_iracing_race" when analysis.HasValue => WithRequestedSelector(analysis.Value, RequestedSelector(arguments)),
                _ => JsonSerializer.SerializeToElement(new { ok = true })
            };
            return value;
        }

        private static string RequestedSelector(object arguments)
        {
            var serialized = JsonSerializer.SerializeToElement(arguments);
            return serialized.TryGetProperty("selector", out var selector) && selector.ValueKind == JsonValueKind.String
                ? selector.GetString() ?? string.Empty
                : string.Empty;
        }
    }

    private sealed class FakeGarage61CredentialStore(string credentialPath) : IGarage61CredentialStore
    {
        public bool IsConfigured => StoredToken is not null;
        public string CredentialPath { get; } = credentialPath;
        public string? StoredToken { get; private set; }
        public void Store(string token) => StoredToken = token;
        public void Remove() => StoredToken = null;
    }

    private sealed class FakeCoachEngine : ICoachEngineSupervisor
    {
        private int _loginCount;
        public List<string> Actions { get; } = [];
        public CoachEngineConnection Current { get; private set; } = new(true, true, false, "not_connected", "Connect ChatGPT to enable AI coaching.");
        public event Action<CoachEngineConnection>? Changed { add { } remove { } }
        public event Action<string>? CoachMessageDelta { add { } remove { } }

        public Task StartAsync(CompanionSettings settings, CancellationToken cancellationToken = default)
        {
            Actions.Add("start");
            return Task.CompletedTask;
        }

        public Task RefreshAccountAsync(CancellationToken cancellationToken = default) => Task.CompletedTask;

        public Task<CoachEngineLogin> BeginChatGptLoginAsync(bool deviceCode = false, CancellationToken cancellationToken = default)
        {
            var loginId = $"login-{++_loginCount}";
            Actions.Add(deviceCode ? "login:device" : "login:browser");
            return Task.FromResult(new CoachEngineLogin(deviceCode ? "chatgptDeviceCode" : "chatgpt", loginId, null, deviceCode ? "ABCD-EFGH" : null));
        }

        public Task CancelLoginAsync(string loginId, CancellationToken cancellationToken = default)
        {
            Actions.Add($"cancel:{loginId}");
            return Task.CompletedTask;
        }

        public Task<CoachEngineReply> AskCoachAsync(string? threadId, string question, string evidenceJson, CancellationToken cancellationToken = default) =>
            Task.FromResult(new CoachEngineReply(threadId ?? "thread-1", "turn-1", "{}"));

        public Task StopAsync(CancellationToken cancellationToken = default) => Task.CompletedTask;
        public ValueTask DisposeAsync() => ValueTask.CompletedTask;
    }

    private sealed class TestLiveTelemetrySource : ILiveTelemetrySource
    {
        private int _tick;
        public bool TryRead(out LiveTelemetrySample sample)
        {
            sample = new LiveTelemetrySample
            {
                Connected = true,
                Timestamp = DateTimeOffset.UtcNow,
                Tick = ++_tick,
                TickRate = 60,
                Flag = "GREEN",
                Lap = 2,
                LapsRemaining = 8,
                OverallPosition = 3,
                GapToLeaderSeconds = 1.2,
                GapToAheadSeconds = .4,
                GapToBehindSeconds = .9,
                LastLapSeconds = 31.2,
                LeaderLastLapSeconds = 31.0,
                FuelLiters = 20,
                Brake = 0,
                SteeringWheelAngleRadians = 0,
                LateralAccelerationG = 0
            };
            return true;
        }

        public void Dispose() { }
    }

    private sealed class SwitchableLiveTelemetrySource : ILiveTelemetrySource
    {
        private int _tick;
        public volatile bool Connected;
        public volatile int Lap = 2;

        public bool TryRead(out LiveTelemetrySample sample)
        {
            sample = new LiveTelemetrySample
            {
                Connected = Connected,
                Timestamp = DateTimeOffset.UtcNow,
                Tick = Interlocked.Increment(ref _tick),
                TickRate = 60,
                Flag = Connected ? "GREEN" : "Waiting",
                Lap = Connected ? Lap : null,
                LapDistancePercent = Connected ? .25 : null,
                LastLapSeconds = Connected ? 30 : null,
                FuelLiters = Connected ? 30 - Lap : null,
                Brake = Connected ? 0 : null,
                SteeringWheelAngleRadians = Connected ? 0 : null,
                LateralAccelerationG = Connected ? 0 : null
            };
            return true;
        }

        public void Dispose() { }
    }

    private sealed class CadencedLiveTelemetrySource(int rate) : ILiveTelemetrySource
    {
        private readonly Stopwatch _clock = Stopwatch.StartNew();
        private int _lastTick = -1;

        public bool TryRead(out LiveTelemetrySample sample)
        {
            var tick = (int)Math.Floor(_clock.Elapsed.TotalSeconds * rate);
            if (tick <= _lastTick)
            {
                sample = new LiveTelemetrySample();
                return false;
            }
            _lastTick = tick;
            sample = ConnectedSample(tick, rate);
            return true;
        }

        public void Dispose() { }
    }

    private sealed class CountingDisconnectedLiveTelemetrySource : ILiveTelemetrySource
    {
        private int _readCount;
        public int ReadCount => Volatile.Read(ref _readCount);
        public bool Disposed { get; private set; }

        public bool TryRead(out LiveTelemetrySample sample)
        {
            Interlocked.Increment(ref _readCount);
            sample = new LiveTelemetrySample();
            return false;
        }

        public void Dispose() => Disposed = true;
    }

    private sealed class SkippingLiveTelemetrySource : ILiveTelemetrySource
    {
        private int _tick;
        public bool TryRead(out LiveTelemetrySample sample)
        {
            _tick += 3;
            sample = ConnectedSample(_tick, 240);
            return true;
        }

        public void Dispose() { }
    }

    private static LiveTelemetrySample ConnectedSample(int tick, int tickRate) => new()
    {
        Connected = true,
        Timestamp = DateTimeOffset.UtcNow,
        Tick = tick,
        TickRate = tickRate,
        Flag = "GREEN",
        Lap = 2,
        LastLapSeconds = 30,
        FuelLiters = 20,
        Brake = 0,
        SteeringWheelAngleRadians = 0,
        LateralAccelerationG = 0
    };
}
