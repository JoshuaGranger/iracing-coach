using System.Security.Cryptography;
using System.Text.Json;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class FinalProductFixtureTests
{
    private static readonly BackendConfiguration Configuration = new("", "", "", "", "", "");

    [TestMethod]
    public void FinalProductPacket_AllManifestEntriesExistHashAndParse()
    {
        var root = PacketRoot();
        using var manifest = JsonDocument.Parse(File.ReadAllText(Path.Combine(root, "fixture-manifest.json")));
        var entries = manifest.RootElement.GetProperty("entries").EnumerateArray().ToArray();
        Assert.HasCount(23, entries);
        var nullCount = 0;
        foreach (var entry in entries)
        {
            var relative = entry.GetProperty("path").GetString()!;
            var path = Path.Combine(root, relative.Replace('/', Path.DirectorySeparatorChar));
            Assert.IsTrue(File.Exists(path), relative);
            var bytes = File.ReadAllBytes(path);
            Assert.AreEqual(entry.GetProperty("length").GetInt64(), bytes.LongLength, relative);
            Assert.AreEqual(entry.GetProperty("sha256").GetString(), Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant(), relative);
            if (path.EndsWith(".json", StringComparison.OrdinalIgnoreCase))
            {
                using var document = JsonDocument.Parse(bytes, new JsonDocumentOptions { MaxDepth = 256 });
                nullCount += CountNulls(document.RootElement);
            }
        }
        Assert.IsGreaterThan(0, nullCount, "The packet must retain optional null shapes for regression coverage.");
    }

    [TestMethod]
    public async Task FixtureBackend_KentuckyAndNhmsMapThroughProductionMappers()
    {
        var backend = new QaFixtureBackendClient(PacketRoot());
        var health = await backend.CheckHealthAsync(Configuration);
        Assert.IsTrue(health.Ok);
        Assert.AreEqual(16, health.ToolCount);

        var dashboard = await backend.CallToolAsync(Configuration, "iracing_companion_dashboard", new { });
        var discovery = await backend.CallToolAsync(Configuration, "discover_iracing_sessions", new { });
        var races = DashboardMapper.MapEvents(dashboard, discovery);
        Assert.IsTrue(races.Any(race => race.Track == "Kentucky Speedway" && race.Car.Contains("Toyota Tundra", StringComparison.Ordinal)));
        Assert.IsTrue(races.Any(race => race.Track == "New Hampshire Motor Speedway" && race.SetupType == "Open"));
        Assert.IsFalse(races.Any(race => race.Car.Contains("toyotatundra2022", StringComparison.OrdinalIgnoreCase)));

        var kentucky = await backend.CallToolAsync(Configuration, "analyze_iracing_race", new { selector = "fixture://kentucky-race" });
        var workspace = RuntimeMapper.Analysis(kentucky);
        var card = RuntimeMapper.RaceCard(kentucky);
        Assert.AreEqual("Kentucky Speedway", workspace.Track);
        Assert.AreEqual("Toyota Tundra TRD Pro", workspace.Car);
        Assert.IsGreaterThanOrEqualTo(3, workspace.Traces.Count);
        Assert.IsGreaterThanOrEqualTo(100, workspace.TrackShape.Count);
        Assert.HasCount(2, workspace.Segments);
        Assert.IsGreaterThan(100, workspace.Traces.SelectMany(trace => trace.Points).Where(point => point.Rpm.HasValue).Select(point => point.Rpm!.Value).Distinct().Count());
        Assert.IsGreaterThan(100, workspace.Traces.SelectMany(trace => trace.Points).Count(point => point.SessionTimeSeconds.HasValue));
        Assert.IsGreaterThan(100, workspace.Traces.SelectMany(trace => trace.Points).Count(point => point.SlipAngleDegrees.HasValue));
        Assert.IsTrue(workspace.Traces.SelectMany(trace => trace.Points).Any(point => point.YawRateDegreesPerSecond.HasValue && point.LateralG.HasValue && point.LongitudinalG.HasValue));
        Assert.IsGreaterThanOrEqualTo(2, card.Corners.Count);
        Assert.IsNotNull(workspace.Strategy.AllGreenRangeLaps);
        Assert.IsGreaterThan(0d, workspace.Strategy.AllGreenRangeLaps.GetValueOrDefault());
        var fiftyLapPlan = RuntimeMapper.Plan(kentucky, 50, "Laps");
        Assert.AreEqual(50, fiftyLapPlan.ScheduledLaps);
        StringAssert.Contains(fiftyLapPlan.StopCount, "1 stop");
        StringAssert.Contains(fiftyLapPlan.StopCount, "50 all-green laps");
        CollectionAssert.AreEqual(new[] { 25 }, fiftyLapPlan.PitTargets.ToArray());

        var nhms = await backend.CallToolAsync(Configuration, "analyze_iracing_race", new { selector = "fixture://nhms-race" });
        var openWorkspace = RuntimeMapper.Analysis(nhms);
        Assert.AreEqual("Open", openWorkspace.SetupType);
        Assert.AreEqual("New Hampshire Motor Speedway", openWorkspace.Track);
        Assert.IsNotEmpty(openWorkspace.Runs);
    }

    [TestMethod]
    public async Task FixtureBackend_ExercisesQualifyingRepairTuningAndContainedFailureStates()
    {
        var qualifyingBackend = new QaFixtureBackendClient(PacketRoot(), "with-qualifying");
        var dashboard = await qualifyingBackend.CallToolAsync(Configuration, "iracing_companion_dashboard", new { });
        var discovery = await qualifyingBackend.CallToolAsync(Configuration, "discover_iracing_sessions", new { });
        var sessions = DashboardMapper.MapEvents(dashboard, discovery);
        Assert.AreEqual(1, sessions.Count(session => session.IsQualifying));
        Assert.AreEqual(2, sessions.Count(session => session.IsRace));
        var qualifying = await qualifyingBackend.CallToolAsync(Configuration, "analyze_iracing_race", new { selector = "fixture://kentucky-qualifying" });
        Assert.AreEqual("Qualifying", RuntimeMapper.Analysis(qualifying).SessionType);

        var repairBackend = new QaFixtureBackendClient(PacketRoot(), "repair");
        var repair = await repairBackend.CallToolAsync(Configuration, "analyze_iracing_race", new { selector = "fixture://repair-race" });
        var repairWorkspace = RuntimeMapper.Analysis(repair);
        Assert.IsTrue(repairWorkspace.Damage.PitRoadEpisodes > 0 || repairWorkspace.Damage.RepairEpisodes > 0 || repairWorkspace.Damage.TowEpisodes > 0);

        var tuning = await qualifyingBackend.CallToolAsync(Configuration, "recommend_open_setup_tuning", new { });
        Assert.IsFalse(string.IsNullOrWhiteSpace(RuntimeMapper.Tuning(tuning).Change));
        var blockedBackend = new QaFixtureBackendClient(PacketRoot(), "damage-blocked");
        var blocked = await blockedBackend.CallToolAsync(Configuration, "recommend_open_setup_tuning", new { });
        Assert.Throws<InvalidDataException>(() => RuntimeMapper.Tuning(blocked));

        var temp = Path.Combine(Path.GetTempPath(), "iRacingCoach-FixtureTests", Guid.NewGuid().ToString("N"));
        try
        {
            using var state = new CompanionState(
                new QaFixtureBackendClient(PacketRoot(), "backend-error"),
                new QaFixtureSettingsStore(temp),
                new DisconnectedLiveTelemetrySource(),
                new DisabledCoachEngineSupervisor(),
                new QaFixtureCredentialStore(),
                qaFixtureMode: true);
            await state.InitializeAsync();
            Assert.IsTrue(state.QaFixtureMode);
            Assert.IsNotNull(state.LastRecoverableError);
            Assert.IsGreaterThan(0, state.ServiceFailureCount);
        }
        finally
        {
            if (Directory.Exists(temp)) Directory.Delete(temp, recursive: true);
        }
    }

    [TestMethod]
    public void LiveReplay_MapsAllThreeGreenLapsAndCautionIntoLiveSamples()
    {
        var path = Path.Combine(PacketRoot(), "fixtures", "synthetic", "live-sdk-replay.json");
        using var replay = new ReplayFileLiveTelemetrySource(path, 10);
        Assert.AreEqual(2981, replay.FrameCount);
        Assert.AreEqual(149d, replay.DurationSeconds, .001);
        var start = replay.SampleAt(0);
        var middle = replay.SampleAt(70);
        var finish = replay.SampleAt(149);
        Assert.IsTrue(start.Connected);
        Assert.AreEqual(1, start.Lap);
        Assert.IsTrue(middle.Lap is >= 2 and <= 3);
        Assert.AreEqual(4, finish.Lap);
        Assert.IsTrue(finish.UnderCaution);
        Assert.IsNotNull(start.Rpm);
        Assert.IsNotNull(finish.Rpm);
        Assert.IsGreaterThan(finish.Rpm.GetValueOrDefault(), start.Rpm.GetValueOrDefault());
        Assert.IsNotNull(start.GapToLeaderSeconds);
        Assert.IsGreaterThan(0d, start.GapToLeaderSeconds.GetValueOrDefault());
        Assert.AreEqual("QA fixture replay · live-sdk-replay.json", start.Source);
    }

    [TestMethod]
    public void FixtureSettings_ConfinePortableAndMachineStateToTemporaryRoot()
    {
        var root = Path.Combine(Path.GetTempPath(), "iRacingCoach-FixtureSettings", Guid.NewGuid().ToString("N"));
        try
        {
            var settings = new QaFixtureSettingsStore(root).Load();
            var canonical = Path.GetFullPath(root) + Path.DirectorySeparatorChar;
            Assert.IsTrue(Path.GetFullPath(settings.CoachHome).StartsWith(canonical, StringComparison.OrdinalIgnoreCase) || Path.GetFullPath(settings.CoachHome) + Path.DirectorySeparatorChar == canonical);
            Assert.IsTrue(Path.GetFullPath(settings.ArchiveRoot).StartsWith(canonical, StringComparison.OrdinalIgnoreCase));
            Assert.IsTrue(Path.GetFullPath(settings.LocalStateRoot).StartsWith(canonical, StringComparison.OrdinalIgnoreCase));
            Assert.IsFalse(Path.GetFullPath(settings.CoachHome).StartsWith(Path.GetFullPath(CompanionSettings.DefaultCoachHome), StringComparison.OrdinalIgnoreCase));
        }
        finally
        {
            if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
        }
    }

    [TestMethod]
    public async Task FixtureState_StaysOfflineAndKeepsRacePlanningSelectionsMatched()
    {
        var root = Path.Combine(Path.GetTempPath(), "iRacingCoach-FixtureState", Guid.NewGuid().ToString("N"));
        try
        {
            using var state = new CompanionState(
                new QaFixtureBackendClient(PacketRoot()),
                new QaFixtureSettingsStore(root),
                new DisconnectedLiveTelemetrySource(),
                new DisabledCoachEngineSupervisor(),
                new QaFixtureCredentialStore(),
                qaFixtureMode: true);
            await state.InitializeAsync();

            Assert.AreEqual(0, state.Garage61RequestCount, "An offline fixture status read must not appear as a Garage61 production request.");
            Assert.IsTrue(state.Diagnostics.Any(item => item.Label == "Contract compatibility" && item.State == "ready"));

            var supra = state.Cars.Single(car => car.Name.Contains("Supra", StringComparison.OrdinalIgnoreCase));
            state.SelectPlanCar(supra.Id);
            Assert.IsTrue(state.SelectedPlanTrack.Contains("New Hampshire", StringComparison.OrdinalIgnoreCase));
            Assert.AreEqual("Open", state.PlanSetupType);
            Assert.IsNotNull(state.SelectedPlanRace);
            Assert.IsTrue(state.SelectedPlanRace.Car.Contains("Supra", StringComparison.OrdinalIgnoreCase));

            state.SelectPlanTrack("Kentucky Speedway|Oval");
            Assert.IsNull(state.SelectedPlanRace, "A mismatched car/track recording must not remain selected as evidence.");

            var tundra = state.Cars.Single(car => car.Name.Contains("Tundra", StringComparison.OrdinalIgnoreCase));
            state.SelectPlanCar(tundra.Id);
            Assert.AreEqual("Kentucky Speedway|Oval", state.SelectedPlanTrack);
            Assert.AreEqual("Fixed", state.PlanSetupType);
            Assert.IsNotNull(state.SelectedPlanRace);

            state.TuningCorner = "Turns 3–4";
            state.TuningRunPhase = "Late run";
            state.TuningCornerPhase = "Center";
            state.TuningBalance = "Tight / understeer";
            state.TuningSeverity = "Moderate";
            state.TuningConfidence = "High";
            state.TuningPriority = true;
            state.AddTuningFeedback();
            state.TuningCorner = "Turns 1–2";
            state.TuningRunPhase = "Early run";
            state.TuningCornerPhase = "Entry";
            state.TuningBalance = "Loose / oversteer";
            state.TuningSeverity = "Mild";
            state.TuningConfidence = "Medium";
            state.TuningPriority = false;
            state.AddTuningFeedback();
            await state.GenerateExperimentAsync();
            Assert.HasCount(2, state.TuningFeedback);
            StringAssert.Contains(state.SymptomText, "Issue 1:");
            StringAssert.Contains(state.SymptomText, "Issue 2:");
            StringAssert.Contains(state.SymptomText, "highest-priority issue");
            Assert.IsNotNull(state.TuningExperiment);

            await state.VerifyInstallationAsync();
            StringAssert.Contains(state.Toast?.ToLowerInvariant() ?? string.Empty, "fixture passed");
            Assert.AreEqual(0, state.Garage61RequestCount);
        }
        finally
        {
            if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
        }
    }

    private static string PacketRoot()
    {
        var configured = Environment.GetEnvironmentVariable("IRACING_COACH_QA_PACKET_ROOT");
        if (!string.IsNullOrWhiteSpace(configured) && File.Exists(Path.Combine(configured, "fixture-manifest.json"))) return Path.GetFullPath(configured);
        var shared = @"\\192.168.1.82\Joshua\iRacing Temp\developer-input\final-product-v1";
        if (File.Exists(Path.Combine(shared, "fixture-manifest.json"))) return shared;
        Assert.Inconclusive("Set IRACING_COACH_QA_PACKET_ROOT to the final-product-v1 packet before running the offline QA tests.");
        return string.Empty;
    }

    private static int CountNulls(JsonElement element)
    {
        if (element.ValueKind == JsonValueKind.Null) return 1;
        if (element.ValueKind == JsonValueKind.Array) return element.EnumerateArray().Sum(CountNulls);
        if (element.ValueKind == JsonValueKind.Object) return element.EnumerateObject().Sum(property => CountNulls(property.Value));
        return 0;
    }
}
