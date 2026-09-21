using System.Text.Json;
using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class DevelopmentRoundTests
{
    [TestMethod]
    public void PlanReadinessExplainsWhyTheActionIsDisabled()
    {
        using var state = State(new PlanBackend());
        Assert.IsFalse(state.CanBuildPlan);
        Assert.AreEqual("Choose a car", state.PlanReadinessText);
        state.SelectedPlanCarId = "car";
        Assert.AreEqual("Choose a track and layout", state.PlanReadinessText);
        state.SelectedPlanTrack = "track";
        foreach (var distance in new[] { 0d, -1d, double.NaN, double.PositiveInfinity, 1001d })
        {
            state.PlanDistanceValue = distance;
            Assert.IsFalse(state.CanBuildPlan);
            StringAssert.Contains(state.PlanReadinessText, "Enter a race distance");
        }
        state.PlanDistanceValue = 160;
        Assert.IsTrue(state.CanBuildPlan);
        Assert.AreEqual("Ready to build", state.PlanReadinessText);
    }

    [TestMethod]
    public void PackagedBackendWinsOverAnUnrelatedWorkingCheckout()
    {
        var root = Root();
        var payload = Path.Combine(root, "payload");
        var checkout = Path.Combine(root, "old-checkout");
        const string relative = "iracing-coach/backend.ps1";
        foreach (var directory in new[] { payload, checkout })
        {
            Directory.CreateDirectory(Path.Combine(directory, "iracing-coach"));
            File.WriteAllText(Path.Combine(directory, relative), "# fixture");
        }
        Assert.AreEqual(Path.Combine(payload, relative), BackendLocator.Find(relative, payload, checkout));
    }

    [TestMethod]
    public async Task UnrecordedEventBuildsFromExplicitAssumptionsWithoutAnalyzingAnUnrelatedRace()
    {
        var backend = new PlanBackend();
        using var state = State(backend);
        state.Cars.Add(new("new-car", "New car", "", "Installed"));
        state.Tracks.Add(new("new-layout", "New layout", "", "Installed"));
        state.SelectedPlanCarId = "new-car";
        state.SelectedPlanTrack = "installed:new-layout";
        state.PlanDistanceValue = 50;
        state.PlanGreenBurnGallons = .4;
        state.PlanStartFuelGallons = 20;

        await state.GeneratePlanAsync();

        CollectionAssert.AreEqual(new[] { "plan_iracing_race" }, backend.Calls.ToArray());
        Assert.IsTrue(state.PlanGenerated, state.PlanMessage);
        Assert.IsNotNull(state.PlanBriefing);
        Assert.AreEqual("New layout", state.PlanBriefing.Track);
        Assert.AreEqual("Estimate", state.PlanBriefing.Confidence);
        Assert.AreEqual(1, state.PlanBriefing.FuelPlan?.MinimumStops);
        Assert.IsFalse(state.PlanBriefing.FuelPlan!.NoStopLanguagePermitted);
        Assert.AreEqual(.4, backend.Arguments.GetProperty("green_burn_gallons").GetDouble());
        Assert.AreEqual(JsonValueKind.Null, backend.Arguments.GetProperty("analysis_path").ValueKind);

        state.PlanDistanceValue = 49;
        Assert.IsFalse(state.PlanGenerated, "A different event distance must hide the old plan immediately.");
        Assert.IsNull(state.PlanBriefing);
    }

    [TestMethod]
    public async Task ChangedAndRestoredEstimateCannotPublishAnOldPendingPlan()
    {
        var backend = new PlanBackend { Hold = true };
        using var state = State(backend);
        state.SelectedPlanCarId = "new-car";
        state.SelectedPlanTrack = "installed:new-layout";
        state.PlanDistanceValue = 50;
        state.PlanGreenBurnGallons = .4;
        var pending = state.GeneratePlanAsync();
        await backend.Started.Task.WaitAsync(TimeSpan.FromSeconds(3));
        state.PlanGreenBurnGallons = .5;
        state.PlanGreenBurnGallons = .4;
        backend.Release.TrySetResult();
        await pending;
        Assert.IsFalse(state.PlanGenerated);
        Assert.IsNull(state.PlanBriefing);
        Assert.IsFalse(state.PlanBusy);
    }

    [TestMethod]
    public void SetupEvidenceRequiresAnExplicitArchiveLinkAndLibraryFiltersKeepUnknownSeason()
    {
        using var state = State(new PlanBackend());
        var source = Path.Combine(Root(), "analysis.json");
        var setup = new LocalSetup("setup-a", "Baseline", "Test car", "Test track", "Recorded race", source, "fp-a", "", "", [], AnalysisPaths: [source]);
        state.Setups.Add(setup);
        state.Setups.Add(setup with { Id = "setup-b", Name = "Summer", Season = "2026S3", StoPath = "unlinked.sto", AnalysisPaths = [] });
        state.Races.Add(new("linked", "Test track", "Oval", "Test car", "Today", "Open", "Ready", "Recorded", false, true, 0, 0, AnalysisPath: source));
        state.Races.Add(new("same-names", "Test track", "Oval", "Test car", "Today", "Open", "Ready", "Recorded", false, true, 0, 0, AnalysisPath: "different.json"));
        Assert.AreEqual("linked", state.SetupEvidenceRaces(setup).Single().Id);
        Assert.IsEmpty(state.SetupEvidenceRaces(state.Setups[1]));
        state.SetupSeasonFilter = "Unknown";
        Assert.AreEqual("setup-a", state.FilteredSetups.Single().Id);
        state.SetupSearch = "does not exist";
        Assert.IsEmpty(state.FilteredSetups);
    }

    [TestMethod]
    public void SelectingAnInstalledCarDoesNotChangeTheChosenEventLayout()
    {
        using var state = State(new PlanBackend());
        state.SelectedPlanTrack = "installed:chosen-layout";
        state.SelectPlanCar("another-car");
        Assert.AreEqual("installed:chosen-layout", state.SelectedPlanTrack);
        Assert.AreEqual(string.Empty, state.SelectedPlanRaceId);
    }

    [TestMethod]
    public void InstalledCatalogUnlocksPlanningBeforeTheFirstRecording()
    {
        var capability = CapabilityRegistry.Evaluate(ProductCapability.RacePlanning,
            new CapabilityContext { HasPlanningEventCatalog = true });
        Assert.IsTrue(capability.Visible);
        Assert.IsTrue(capability.Active);
    }

    [TestMethod]
    public void BoundTuningIdentityUsesCatalogLabelsWithoutLosingExactBackendIdentity()
    {
        var race = new RecentRace("race-1", "Iowa", "Oval", "Toyota Supra Class B", "Today", "Fixed", "Ready", "", false, true, 0, 0,
            AnalysisPath: @"C:\test\analysis.json", EventKey: "8001", Selector: "subsession:8001:1");
        var sdk = new TuningSessionIdentity
        {
            Track = "Iowa Speedway", Layout = "Oval", Car = race.Car, AnalysisPath = race.AnalysisPath,
            TrackId = "88", TrackConfigurationKey = "88-oval", CarId = "99", SetupFingerprint = "source-fingerprint", SessionType = "Race"
        };
        var bound = ProgressiveTuningCoordinator.Bind(sdk, race);
        Assert.IsTrue(ProgressiveTuningCoordinator.Matches(race, bound));
        Assert.AreEqual("88-oval", bound.TrackConfigurationKey);
        Assert.AreEqual("source-fingerprint", bound.SetupFingerprint);
        Assert.IsFalse(ProgressiveTuningCoordinator.Matches(race with { Selector = "subsession:8002:1" }, bound));
    }

    private static string Root()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-round-tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root); return root;
    }
    private static CompanionState State(IBackendClient backend)
    {
        var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(Root(), "settings.json")));
        state.Settings.CoachHome = Root(); return state;
    }
    private sealed class PlanBackend : IBackendClient
    {
        public List<string> Calls { get; } = [];
        public JsonElement Arguments { get; private set; }
        public bool Hold { get; init; }
        public TaskCompletionSource Started { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public TaskCompletionSource Release { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default) =>
            Task.FromResult(new BackendHealthResult(true, "test", "1", "test", 1, TimeSpan.Zero));
        public async Task<JsonElement> CallToolAsync(BackendConfiguration configuration, string toolName, object arguments, CancellationToken cancellationToken = default)
        {
            Calls.Add(toolName); Arguments = JsonSerializer.SerializeToElement(arguments); Started.TrySetResult();
            if (Hold) await Release.Task.WaitAsync(cancellationToken);
            if (toolName != "plan_iracing_race") throw new InvalidOperationException($"Unexpected tool: {toolName}");
            using var document = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "fixtures", "event-plan-estimate.json")));
            return document.RootElement.Clone();
        }
    }
}
