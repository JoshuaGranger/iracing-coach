using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;
using iRacingCoach.UI;
using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Components.Web;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class RaceInvestigationHandoffTests
{
    [TestMethod]
    public void ShippingBackendInvestigationPassesThroughAnalysisMapperWithoutLosingEvidence()
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        while (directory is not null && !File.Exists(Path.Combine(directory.FullName, "test-data", "race-investigation.json"))) directory = directory.Parent;
        Assert.IsNotNull(directory, "The shipping investigation fixture must be available in the repository.");
        using var fixture = JsonDocument.Parse(File.ReadAllText(Path.Combine(directory.FullName, "test-data", "race-investigation.json")));
        var response = JsonSerializer.SerializeToElement(new { analysis_id = "shipping-fixture", analysis_view = new {
            schema_version = 1, identity = new { event_type = "Race", track_name = "Fixture track", car_name = "Fixture car" },
            race_summary = new { }, runs = Array.Empty<object>(), laps = Array.Empty<object>(),
            race_investigation = fixture.RootElement.Clone()
        } });
        var workspace = RuntimeMapper.Analysis(response);
        Assert.IsNotNull(workspace.Investigation);
        Assert.IsNull(workspace.Investigation.UnavailableReason, workspace.Investigation.UnavailableReason);
        Assert.HasCount(fixture.RootElement.GetProperty("comparisons").GetArrayLength(), workspace.Investigation.Comparisons);
        Assert.HasCount(fixture.RootElement.GetProperty("laps").GetArrayLength(), workspace.Investigation.Laps);
        Assert.IsTrue(workspace.Investigation.Laps.Any(lap => !lap.Eligible && lap.PlotPace));
        Assert.IsTrue(RaceCoachPacketBuilder.Build(workspace).SupportedSubjects.Contains("corner_window"));
    }

    [TestMethod]
    public void TelemetryInspectionKeepsExplicitExcludedLapsAndRegion()
    {
        using var state = CreateState(new NeverBackend());
        var workspace = Workspace("race-a") with { Traces = [
            new(12, 15.5, true, "green", 1, 0, 0, []),
            new(33, 16.9, true, "green", 1, 0, 0, [], ComparisonEligible: false, ExclusionReason: "Close traffic")
        ] };
        var component = new TelemetryWorkspace();
        Set(component, "State", state);
        Set(component, "Workspace", workspace);
        Invoke(component, "OnParametersSet");
        var savedLayout = JsonSerializer.Serialize(state.Settings.RaceAnalysisTraceLayouts);
        Set(component, "Inspection", new RaceInvestigationInspection([12, 33], .1, .4, ["speed", "steering"]));
        Invoke(component, "OnParametersSet");
        CollectionAssert.AreEquivalent(new[] { 12, 33 }, ((HashSet<int>)Field(component, "Selected")!).ToArray());
        Assert.IsFalse((bool)Property(component, "CleanLapsOnly")!);
        Assert.IsFalse((bool)Property(component, "GreenLapsOnly")!);
        Assert.AreEqual(.25d, (double)Property(component, "Cursor")!);
        CollectionAssert.AreEqual(new[] { "speed", "steering" }, ((AnalysisTraceLayout)Property(component, "TraceLayout")!).Rows.Select(row => row.PrimarySignalId).ToArray(),
            "A focused inspection must contain only its requested signals, not a factory layout inserted while repairing an empty temporary layout.");
        Assert.AreEqual(savedLayout, JsonSerializer.Serialize(state.Settings.RaceAnalysisTraceLayouts), "Inspection must not mutate the saved trace layout.");
        Invoke(component, "OnParametersSet");
        CollectionAssert.AreEquivalent(new[] { 12, 33 }, ((HashSet<int>)Field(component, "Selected")!).ToArray(), "A repeated render must not replace the explicit excluded-lap selection with defaults.");
        Set(component, "Workspace", workspace with { Garage61References = workspace.Garage61References });
        Invoke(component, "OnParametersSet");
        CollectionAssert.AreEquivalent(new[] { 12, 33 }, ((HashSet<int>)Field(component, "Selected")!).ToArray(), "A same-race reference refresh must reapply the active inspection.");
        CollectionAssert.AreEqual(new[] { "speed", "steering" }, ((AnalysisTraceLayout)Property(component, "TraceLayout")!).Rows.Select(row => row.PrimarySignalId).ToArray());
        Assert.AreEqual(savedLayout, JsonSerializer.Serialize(state.Settings.RaceAnalysisTraceLayouts));
    }

    [TestMethod]
    public void InvestigationViewAndSelectionAreRetainedPerRecordingAndSurviveInspection()
    {
        using var state = CreateState(new NeverBackend());
        Publish(state, "race-a");
        var component = new AnalysisWorkspacePage();
        Set(component, "State", state);
        Set(component, "Workspace", state.CurrentAnalysis!);
        Set(component, "Card", Card());
        Invoke(component, "OnParametersSet");
        Assert.AreEqual("race", Property(component, "RaceDataSection"), "A newly opened race starts on its progression.");
        state.SetInvestigationSelection("sections", "later-run", "turn-pair");
        var request = new RaceInvestigationInspection([12, 33], .1, .4);
        Invoke(component, "InspectFinding", request);
        Assert.AreEqual("traces", Property(component, "RaceDataSection"));
        Assert.AreEqual("later-run", state.InvestigationSelection.ComparisonId, "Opening traces keeps the chosen comparison.");
        Assert.AreEqual("turn-pair", state.InvestigationSelection.SectionId);
        Invoke(component, "ReturnToFindings");
        Assert.AreEqual("sections", Property(component, "RaceDataSection"));
        CollectionAssert.AreEqual(request.LapNumbers.ToArray(), ((RaceInvestigationInspection)Field(component, "_inspection")!).LapNumbers.ToArray());
        Assert.AreEqual("race-a", state.Settings.LastInvestigation!.AnalysisId, "The selection is bookmarked for a validated restore.");
        Publish(state, "race-b");
        Set(component, "Workspace", state.CurrentAnalysis!);
        Invoke(component, "OnParametersSet");
        Assert.IsNull(Field(component, "_inspection"));
        Assert.AreEqual("race", Property(component, "RaceDataSection"), "Another recording does not inherit the previous selection.");
        Assert.IsNull(state.InvestigationSelection.ComparisonId);
    }

    [TestMethod]
    public async Task CoachSnapshotsQuestionAndKeepsLateAnswerAttachedToOriginalRace()
    {
        var coach = new DelayedCoach();
        using var state = CreateState(new NeverBackend(), coach);
        Publish(state, "race-a");
        state.CoachQuestion = "Why did the final stint fade?";
        var pending = state.AskRaceCoachAsync();
        await coach.Started.Task.WaitAsync(TimeSpan.FromSeconds(5));
        state.CoachQuestion = "A different question typed later";
        Publish(state, "race-b");
        coach.Release.SetResult(new("thread-a", "turn-a", "{\"summary\":\"Original race answer\",\"actions\":[],\"limitations\":[],\"followUpNeeded\":false}"));
        await pending;
        Assert.AreEqual("Why did the final stint fade?", coach.Question);
        var history = state.CoachingHistory.Single();
        Assert.AreEqual("Why did the final stint fade?", history.Question);
        Assert.AreEqual("race:race-a", history.WorkflowKey);
        Assert.AreEqual("race-a", state.CoachAnswerAnalysisId);
        Assert.AreEqual("race-b", state.CurrentAnalysis!.AnalysisId);
        var html = await RenderCoach(state, state.CurrentAnalysis);
        Assert.IsFalse(html.Contains("Original race answer", StringComparison.Ordinal));
        using var evidence = JsonDocument.Parse(coach.Evidence);
        Assert.AreEqual("available", evidence.RootElement.GetProperty("investigation").GetProperty("Status").GetString());
        Assert.IsFalse(coach.Evidence.Contains("future_evidence", StringComparison.Ordinal));
    }

    [TestMethod]
    public async Task InvalidCoachAnswerIsNotPublishedAsAdviceOrPersisted()
    {
        var coach = new DelayedCoach();
        using var state = CreateState(new NeverBackend(), coach);
        Publish(state, "race-a");
        state.CoachQuestion = "What should I change?";
        var pending = state.AskRaceCoachAsync();
        await coach.Started.Task.WaitAsync(TimeSpan.FromSeconds(5));
        coach.Release.SetResult(new("thread-a", "invalid-turn", """
            {"summary":"The pace changed.","actions":[{"text":"Try a different release.","evidenceClass":"measured","evidenceIds":["not-this-race"]}],"followUpNeeded":false}
            """));
        await pending;
        Assert.IsEmpty(state.CoachingHistory);
        Assert.IsFalse(state.Settings.CoachThreadIds.ContainsKey("race:race-a"));
        var archive = Path.Combine(state.Settings.ArchiveRoot, "ai-coaching");
        Assert.IsFalse(Directory.Exists(archive) && Directory.EnumerateFiles(archive, "*.json").Any());
        Assert.IsFalse(state.IsCoaching);
        StringAssert.Contains(state.CoachAnswer, "evidence checks");
        Assert.DoesNotContain("Try a different release", state.CoachAnswer);
        Assert.IsNotNull(state.CurrentAnalysis!.Investigation, "A rejected AI answer must leave deterministic local findings available.");
    }

    [TestMethod]
    public async Task CompletedJobOpensItsRecordingRatherThanThePreviouslySelectedRace()
    {
        var backend = new RecordingBackend();
        using var state = CreateState(backend);
        var first = Race("race-a");
        var second = Race("race-b");
        state.Races.Add(first);
        state.Races.Add(second);
        Publish(state, first.Id);
        await state.OpenArtifactAsync(new JobItem { Id = "job-b", Title = "Analyze B", CanonicalKey = "session:race-b", Status = "complete" });
        Assert.AreEqual("race-b", state.SelectedRaceSessionId);
        Assert.AreEqual("race-b", backend.LastSelector);
        Assert.AreEqual("analysis", state.CurrentPage);
        Assert.IsNull(state.AnalysisError, state.AnalysisError?.Message);
        Assert.AreEqual("race-b", state.CurrentAnalysis!.AnalysisId);
    }

    [TestMethod]
    public async Task StartingTuneNeverFallsBackToAnUnrelatedSetupOrEvidenceRace()
    {
        var backend = new NeverBackend();
        using var state = CreateState(backend);
        var selectedPackage = new SetupPackageView("package", "exact", "source", "intended-fingerprint", "", "confirmed", [], [], Car: "Car", Track: "Track");
        Set(state, "StartingTunePackage", selectedPackage);
        state.Setups.Add(new LocalSetup("unrelated", "Other", "Car", "Track", "Race", "other.sto", "other-fingerprint", "paired", "", [], AnalysisPaths: ["other-analysis.json"]));
        state.Races.Add(Race("other-race") with { AnalysisPath = "other-analysis.json" });
        Assert.IsFalse(state.CanContinueStartingTuneTuning);
        await state.ContinueStartingTuneTuningAsync();
        Assert.AreEqual(0, backend.CallCount);
        Assert.AreEqual(string.Empty, state.TuningSourceSetupId);
        StringAssert.Contains(state.Toast!, "selected starting setup");

        state.Setups.Add(new LocalSetup("matching", "Intended", "Car", "Track", "Race", "intended.sto", "intended-fingerprint", "paired", "", [], AnalysisPaths: ["intended-analysis.json"]));
        Assert.IsFalse(state.CanContinueStartingTuneTuning, "Same fingerprint without its own recorded evidence is insufficient.");
        state.Races.Add(Race("intended-race") with { AnalysisPath = "intended-analysis.json" });
        Assert.IsTrue(state.CanContinueStartingTuneTuning);
    }

    private static CompanionState CreateState(IBackendClient backend, ICoachEngineSupervisor? coach = null)
    {
        var state = new CompanionState(backend, null, new DisconnectedLiveTelemetrySource(), coach, allowExternalHostActions: false);
        var root = Path.Combine(Path.GetTempPath(), "iracing-investigation-handoff-tests", Guid.NewGuid().ToString("N"));
        state.Settings.CoachHome = root;
        state.Settings.LocalStateRootOverride = Path.Combine(root, "local");
        return state;
    }

    private static void Publish(CompanionState state, string id)
    {
        state.SelectRaceSession(Race(id));
        Set(state, "CurrentAnalysis", Workspace(id));
        Set(state, "CurrentRaceCard", Card());
    }
    private static RecentRace Race(string id) => new(id, "Track", "Oval", "Car", "Today", "Open", "Recorded", "Recorded", false, false, 1, 3, Selector: id, SessionType: "Race");
    private static RaceCard Card() => new("Recorded race", new(EvidenceKind.Measured, "Measured race"), [], [], [], []);
    private static AnalysisWorkspace Workspace(string id)
    {
        using var document = JsonDocument.Parse("""
            {"analysis_id":"template","analysis_view":{"schema_version":1,"identity":{"track_name":"Track","car_name":"Car","event_type":"Race"},"race_summary":{},"runs":[],"laps":[],"lap_traces":{"traces":[]},"track_profile":{"detected_corner_segments":[]},"strategy":{},"damage_repair":{},"setup_telemetry":{},"data_quality":{}}}
            """);
        using var investigation = JsonDocument.Parse(RaceInvestigationMapperTests.InvestigationJson.Replace("\"status\":\"usable\"", "\"status\":\"available\"", StringComparison.Ordinal));
        return RuntimeMapper.Analysis(document.RootElement) with { AnalysisId = id, Investigation = RaceInvestigationMapper.Map(investigation.RootElement) };
    }
    private static void Set(object target, string name, object? value) => target.GetType().GetProperty(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)!.SetValue(target, value);
    private static object? Property(object target, string name) => target.GetType().GetProperty(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)!.GetValue(target);
    private static object? Field(object target, string name) => target.GetType().GetField(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)!.GetValue(target);
    private static void Invoke(object target, string name, params object[] arguments) => target.GetType().GetMethod(name, BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(target, arguments);
    private static async Task<string> RenderCoach(CompanionState state, AnalysisWorkspace workspace)
    {
        using var services = new ServiceCollection().AddSingleton<ILoggerFactory>(Microsoft.Extensions.Logging.Abstractions.NullLoggerFactory.Instance).BuildServiceProvider();
        await using var renderer = new HtmlRenderer(services, services.GetRequiredService<ILoggerFactory>());
        return await renderer.Dispatcher.InvokeAsync(async () => (await renderer.RenderComponentAsync<RaceCoachPanel>(ParameterView.FromDictionary(
            new Dictionary<string, object?> { [nameof(RaceCoachPanel.State)] = state, [nameof(RaceCoachPanel.Workspace)] = workspace }))).ToHtmlString());
    }

    private sealed class DelayedCoach : ICoachEngineSupervisor
    {
        public CoachEngineConnection Current { get; } = new(true, true, true, "ready", "Connected");
        public event Action<CoachEngineConnection>? Changed { add { } remove { } }
        public event Action<string>? CoachMessageDelta { add { } remove { } }
        public TaskCompletionSource<bool> Started { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public TaskCompletionSource<CoachEngineReply> Release { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public string Question { get; private set; } = "";
        public string Evidence { get; private set; } = "";
        public Task<CoachEngineReply> AskCoachAsync(string? threadId, string question, string evidenceJson, CancellationToken cancellationToken = default)
        { Question = question; Evidence = evidenceJson; Started.TrySetResult(true); return Release.Task; }
        public Task StartAsync(CompanionSettings settings, CancellationToken cancellationToken = default) => Task.CompletedTask;
        public Task RefreshAccountAsync(CancellationToken cancellationToken = default) => Task.CompletedTask;
        public Task<CoachEngineLogin> BeginChatGptLoginAsync(bool deviceCode = false, CancellationToken cancellationToken = default) => throw new NotSupportedException();
        public Task CancelLoginAsync(string loginId, CancellationToken cancellationToken = default) => Task.CompletedTask;
        public Task StopAsync(CancellationToken cancellationToken = default) => Task.CompletedTask;
        public ValueTask DisposeAsync() => ValueTask.CompletedTask;
    }
    private sealed class NeverBackend : IBackendClient
    {
        public int CallCount { get; private set; }
        public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default) => throw new NotSupportedException();
        public Task<JsonElement> CallToolAsync(BackendConfiguration configuration, string toolName, object arguments, CancellationToken cancellationToken = default)
        { CallCount++; throw new InvalidOperationException("No backend call expected."); }
    }
    private sealed class RecordingBackend : IBackendClient
    {
        public string? LastSelector { get; private set; }
        public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default) => throw new NotSupportedException();
        public Task<JsonElement> CallToolAsync(BackendConfiguration configuration, string toolName, object arguments, CancellationToken cancellationToken = default)
        {
            Assert.AreEqual("analyze_iracing_race", toolName);
            LastSelector = JsonSerializer.SerializeToElement(arguments).GetProperty("selector").GetString();
            var payload = JsonSerializer.SerializeToElement(new {
                ok = true, analysis_id = LastSelector, selector = LastSelector,
                selection = new { sim_session_type = "Race", group_id = LastSelector },
                analysis_view = new { schema_version = 1, analysis_profile_version = "post-race-foundations-v21", identity = new { event_type = "Race", track_name = "Track", car_name = "Car" }, race_summary = new { recorded_laps = 2 }, runs = Array.Empty<object>(), laps = Array.Empty<object>() },
                race_card = new { title = "Recorded race", bottom_line = new { evidence_type = "measured", text = "Recorded race." }, actions = Array.Empty<object>(), corner_playbook = new { rows = Array.Empty<object>() }, race_triggers = Array.Empty<object>(), evidence_appendix = Array.Empty<object>() }
            });
            var root = JsonNode.Parse(payload.GetRawText())!.AsObject();
            foreach (var name in new[] { "context", "analysis_cache", "knowledge_cache", "race_summary", "race_timeline", "damage_repair", "strategy_forecast", "data_quality", "source_channel_coverage", "artifacts" }) root[name] = new JsonObject();
            root["source_files"] = new JsonArray();
            root["historical_runs_considered"] = 0;
            root["analysis_path"] = "";
            root["report_path"] = "";
            root["race_card_path"] = "";
            root["timing"] = new JsonObject { ["contract_version"] = 1, ["total_ms"] = 1, ["analysis_cache_hit"] = false };
            var view = root["analysis_view"]!.AsObject();
            foreach (var name in new[] { "race_grades", "lap_traces", "track_profile", "track_geometry", "race_replay", "tire_learning", "garage61_representative_laps", "corner_tire_age", "groove_evolution", "strategy", "damage_repair", "setup_telemetry", "conditions", "data_quality" }) view[name] = new JsonObject();
            view["technical_insights"] = new JsonArray();
            return Task.FromResult(JsonSerializer.SerializeToElement(root));
        }
    }
}
