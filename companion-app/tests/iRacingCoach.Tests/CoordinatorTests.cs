using System.Diagnostics;
using System.Text.Json;
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
        Assert.AreEqual("8001", races[0].EffectiveSelector);
        Assert.AreEqual("Synthetic Speedway", races[0].Track);
        Assert.IsTrue(races[0].Analyzed);
        Assert.AreEqual(8, races[0].StartPosition);
        Assert.AreEqual(5, races[0].FinishPosition);
        Assert.IsFalse(string.IsNullOrWhiteSpace(races[0].CarPath));

        using var empty = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "fixtures", "dashboard-empty.json")));
        Assert.IsEmpty(DashboardMapper.Map(empty.RootElement));
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
        "lap_traces":{"tire_stress_definition":"Relative proxy; not measured wear.","traces":[{"lap":1,"lap_time_s":31.2,"complete":true,"flag_state":"green","points":[{"lap_pct":0.25,"speed_mph":125,"brake":0.2,"tire_stress_proxy":0.4}]}]},
        "track_profile":{"shape":null,"detected_corner_segments":[]},"strategy":{"forecast":{"status":"insufficient_evidence"}},"damage_repair":{"status":"partial"},"setup_telemetry":{},"data_quality":{"confidence":"high"}}}
        """);

        var workspace = RuntimeMapper.Analysis(response.RootElement);
        Assert.AreEqual("Test Track", workspace.Track);
        Assert.AreEqual("normalized_distance_strip", workspace.GeometryMode);
        Assert.HasCount(1, workspace.Traces);
        Assert.AreEqual(125d, workspace.Traces[0].Points[0].SpeedMph);
        StringAssert.Contains(workspace.TireStressDefinition, "not measured wear");
        Assert.AreEqual(42.5d, workspace.BackendElapsedMilliseconds);
    }

    [TestMethod]
    public void AnalysisMapper_KentuckyNullShape_MapsOptionalValuesWithoutInventingZeroes()
    {
        using var response = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "fixtures", "analysis-nullable-kentucky-shape.json")));

        var workspace = RuntimeMapper.Analysis(response.RootElement);
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
        CollectionAssert.AreEqual(new[] { 38 }, plan.PitTargets.ToArray());
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
        using var state = new CompanionState(new FakeBackend(failure: new InvalidOperationException("nullable analysis shape")));
        var race = new RecentRace(
            "kentucky", "Kentucky Speedway", "Oval", "Toyota Tundra TRD Pro", "Today",
            "Fixed", "Needs analysis", "15 laps", false, false, 8, 6, Selector: "87624987");

        await state.AnalyzeRaceAsync(race);

        Assert.IsNull(state.CurrentAnalysis);
        Assert.IsNotNull(state.LastRecoverableError);
        Assert.AreEqual("failed", state.Jobs.Single().Status);
        StringAssert.Contains(state.LastRecoverableError.Scope, "Kentucky");
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
    public async Task NavigationAcrossEveryPageTwice_PerformsNoBackendOrGarage61Requests()
    {
        var backend = new FakeBackend();
        using var state = new CompanionState(backend);
        await state.RefreshDashboardAsync();
        var callsAfterCatalogLoad = backend.ToolCalls;
        var garageAfterCatalogLoad = backend.Garage61Calls;

        var pages = new[] { "home", "live", "analysis", "planning", "setup", "tuning", "connections", "settings" };
        foreach (var page in pages.Concat(pages)) state.Navigate(page);

        Assert.AreEqual(callsAfterCatalogLoad, backend.ToolCalls);
        Assert.AreEqual(garageAfterCatalogLoad, backend.Garage61Calls);
        Assert.AreEqual(1, garageAfterCatalogLoad);
    }

    [TestMethod]
    public async Task ConcurrentCatalogRefreshes_AreCoalescedByTheRefreshGate()
    {
        var backend = new FakeBackend(callDelay: TimeSpan.FromMilliseconds(30));
        using var state = new CompanionState(backend);

        await Task.WhenAll(state.RefreshDashboardAsync(), state.RefreshDashboardAsync(), state.RefreshDashboardAsync());

        Assert.AreEqual(4, backend.ToolCalls, "One dashboard, discovery, setup, and Garage61 call should serve concurrent refresh requests.");
        Assert.AreEqual(1, backend.Garage61Calls);
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
        Assert.AreEqual("layout-personal", actual.LiveMonitor.ActiveLayoutId);
        Assert.IsFalse(actual.LiveMonitor.IsLocked);
        Assert.AreEqual("Personal Race", actual.LiveMonitor.UserLayouts.Single().Name);
        Assert.AreEqual(220, actual.LiveMonitor.Left);
        Assert.AreEqual(140, actual.LiveMonitor.Top);
        Assert.AreEqual(1.4, actual.LiveMonitor.OverallScale);
        Assert.IsTrue(actual.LiveMonitor.ReopenOnConnect);
        Assert.AreEqual("Ctrl+Shift+L", actual.LiveMonitor.GlobalHotkey);
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
        Assert.AreEqual("Portable layout", restoredOnSecondPc.LiveMonitor.UserLayouts.Single().Name);
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
        Assert.IsTrue(File.Exists(Path.Combine(destination, DurableArchiveService.ManifestFileName)));
        Assert.IsTrue(File.Exists(Path.Combine(destination, DurableArchiveService.PortableStateFileName)));
        Assert.DoesNotContain(source, File.ReadAllText(Path.Combine(destination, DurableArchiveService.ManifestFileName)));
        Assert.IsTrue(File.Exists(Path.Combine(destination, "data", "reports", "race-1", "garage61", "csv", "comparison.csv")));
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
        Assert.AreEqual(4, actual.SettingsSchemaVersion);
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
        using var state = new CompanionState(new FakeBackend());
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
        _ = engine.Update(new LiveTelemetrySample { Connected = true, Timestamp = start, Flag = "GREEN", Lap = 1, FuelLiters = 5, LastLapSeconds = 31, Brake = 0 }, true, false);
        _ = engine.Update(new LiveTelemetrySample { Connected = true, Timestamp = start.AddSeconds(1), Flag = "GREEN", Lap = 2, FuelLiters = 4, LastLapSeconds = 31, Brake = 0 }, true, false);
        var snapshot = engine.Update(new LiveTelemetrySample { Connected = true, Timestamp = start.AddSeconds(2), Flag = "GREEN", Lap = 3, FuelLiters = 3, LastLapSeconds = 31, Brake = 0 }, true, false);

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
        var snapshot = engine.Update(new LiveTelemetrySample { Connected = true, Timestamp = start.AddSeconds(3), Flag = "GREEN", Lap = 4, LastLapSeconds = 30.8, LeaderLastLapSeconds = 30.4, GapToLeaderSeconds = 4.5 }, true, false);

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
    public async Task LiveTelemetryService_PublishesWithoutDroppedFramesAndTracksComputeLatency()
    {
        using var service = new LiveTelemetryService(new TestLiveTelemetrySource(), new LiveMonitorLayout());
        service.Start();
        await Task.Delay(750);

        Assert.IsGreaterThanOrEqualTo(5, service.Current.FramesRead);
        Assert.AreEqual(0, service.Current.DroppedFrames);
        Assert.IsGreaterThanOrEqualTo(0, service.Current.RenderLatencyMs);
        Assert.IsLessThan(25, service.Current.RenderLatencyMs);
        Assert.IsTrue(service.Current.Snapshot.Connected);
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
        _ = Lap(4, .8);
        var transient = Lap(5, .8);
        _ = Lap(6, .8);
        var persistent = Lap(7, .8);

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
        var established = engine.Update(new LiveTelemetrySample { Connected = true, Timestamp = start.AddSeconds(3), Lap = 4, LastLapSeconds = 30, Flag = "GREEN" }, true, false);
        _ = engine.Update(new LiveTelemetrySample { Connected = false, Timestamp = start.AddSeconds(4) }, true, false);
        var reconnected = engine.Update(new LiveTelemetrySample { Connected = true, Timestamp = start.AddSeconds(5), Lap = 1, LastLapSeconds = 35, Flag = "GREEN" }, true, false);

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

    private sealed class FakeBackend(JsonElement? dashboard = null, JsonElement? tuning = null, Exception? failure = null, TimeSpan? callDelay = null) : IBackendClient
    {
        private int _toolCalls;
        private int _garage61Calls;
        public int ToolCalls => Volatile.Read(ref _toolCalls);
        public int Garage61Calls => Volatile.Read(ref _garage61Calls);

        public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default) =>
            Task.FromResult(new BackendHealthResult(true, "iracing-coach-local", "0.3.0", "2025-06-18", 16, TimeSpan.FromMilliseconds(4)));

        public async Task<JsonElement> CallToolAsync(BackendConfiguration configuration, string toolName, object arguments, CancellationToken cancellationToken = default)
        {
            Interlocked.Increment(ref _toolCalls);
            if (toolName.Contains("garage61", StringComparison.OrdinalIgnoreCase)) Interlocked.Increment(ref _garage61Calls);
            if (callDelay is { } delay) await Task.Delay(delay, cancellationToken);
            if (failure is not null && toolName == "analyze_iracing_race") throw failure;
            var value = toolName switch
            {
                "iracing_companion_dashboard" when dashboard.HasValue => dashboard.Value,
                "iracing_companion_dashboard" => JsonSerializer.SerializeToElement(new { ok = true, races = Array.Empty<object>() }),
                "discover_iracing_sessions" => JsonSerializer.SerializeToElement(new { sessions = Array.Empty<object>() }),
                "catalog_iracing_setups" => JsonSerializer.SerializeToElement(new { ok = true, entries = Array.Empty<object>() }),
                "garage61_auth_status" => JsonSerializer.SerializeToElement(new { ok = false, configured = false, status = "not_configured" }),
                "recommend_open_setup_tuning" when tuning.HasValue => tuning.Value,
                _ => JsonSerializer.SerializeToElement(new { ok = true })
            };
            return value;
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
}
