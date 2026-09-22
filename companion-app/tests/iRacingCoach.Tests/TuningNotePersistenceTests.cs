using System.Reflection;
using System.Text.Json;
using Microsoft.AspNetCore.Components;
using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;
using iRacingCoach.UI;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class TuningNotePersistenceTests
{
    [TestMethod]
    public async Task CornerNote_TurnAndPhaseSwitchFlushTheLatestTextToItsOriginalContext()
    {
        var saved = new List<ProgressiveTuningFeedbackBatch>();
        var editor = new ProgressiveTuningFeedbackEditor();
        Set(editor, "Turn", Turn("turn-1"));
        Set(editor, "RunPhase", "early");
        Set(editor, "FeedbackChanged", EventCallback.Factory.Create<ProgressiveTuningFeedbackBatch>(new object(), (ProgressiveTuningFeedbackBatch batch) => saved.Add(batch)));
        await Invoke(editor, "OnParametersSetAsync");

        var first = Invoke(editor, "NoteChanged", new ChangeEventArgs { Value = "Pushes on entry" });
        Set(editor, "RunPhase", "late");
        await Invoke(editor, "OnParametersSetAsync");
        await first;
        var partial = Invoke(editor, "NoteChanged", new ChangeEventArgs { Value = "Needs" });
        var last = Invoke(editor, "NoteChanged", new ChangeEventArgs { Value = "Needs more steering late" });
        Set(editor, "Turn", Turn("turn-2"));
        await Invoke(editor, "OnParametersSetAsync");
        await Task.WhenAll(partial, last);
        await editor.DisposeAsync();

        Assert.HasCount(2, saved);
        Assert.AreEqual("turn-1", saved[0].CornerId);
        Assert.AreEqual("early", saved[0].RunPhase);
        Assert.AreEqual("Pushes on entry", saved[0].Feedback.Single().Note);
        Assert.AreEqual("turn-1", saved[1].CornerId);
        Assert.AreEqual("late", saved[1].RunPhase);
        Assert.AreEqual("Needs more steering late", saved[1].Feedback.Single().Note);
    }

    [TestMethod]
    public async Task CornerNote_DisposalRetainsTheCallbackBoundAtTypingTime()
    {
        var original = new List<ProgressiveTuningFeedbackBatch>();
        var destination = new List<ProgressiveTuningFeedbackBatch>();
        var editor = new ProgressiveTuningFeedbackEditor();
        Set(editor, "Turn", Turn("turn-1"));
        Set(editor, "FeedbackChanged", EventCallback.Factory.Create<ProgressiveTuningFeedbackBatch>(new object(), (ProgressiveTuningFeedbackBatch batch) => original.Add(batch)));
        await Invoke(editor, "OnParametersSetAsync");
        var pending = Invoke(editor, "NoteChanged", new ChangeEventArgs { Value = "Last keystroke" });
        Set(editor, "FeedbackChanged", EventCallback.Factory.Create<ProgressiveTuningFeedbackBatch>(new object(), (ProgressiveTuningFeedbackBatch batch) => destination.Add(batch)));
        await editor.DisposeAsync();
        await pending;
        Assert.HasCount(1, original);
        Assert.AreEqual("Last keystroke", original[0].Feedback.Single().Note);
        Assert.IsEmpty(destination);
    }

    [TestMethod]
    public async Task GeneralNote_RapidRaceSwitchAndDisposalPreserveTheOriginalRecording()
    {
        var home = Path.Combine(Path.GetTempPath(), $"coach-note-{Guid.NewGuid():N}");
        try
        {
            using var state = NewState(home);
            var first = Draft(home, "first");
            var second = Draft(home, "second") with { GeneralNote = "Destination note" };
            Activate(state, first);
            var page = new TuningPage();
            Set(page, "State", state);
            var pending = Invoke(page, "GeneralNoteChanged", new ChangeEventArgs { Value = "Lost grip after the stop" });
            await state.SelectTuningRaceAsync(null);
            Activate(state, second);
            await page.DisposeAsync();
            await pending;

            Assert.AreEqual("Lost grip after the stop", new PortableTuningDraftStore(home).Load(first.RepresentativeSession)?.GeneralNote);
            Assert.AreEqual("Destination note", state.TuningDraft.GeneralNote);
            Assert.AreEqual(second.DraftId, state.TuningDraft.DraftId);
        }
        finally { if (Directory.Exists(home)) Directory.Delete(home, true); }
    }

    [TestMethod]
    public async Task QueuedEdits_MergeIntoTheOriginalDraftAfterAnotherRaceBecomesCurrent()
    {
        var home = Path.Combine(Path.GetTempPath(), $"coach-note-{Guid.NewGuid():N}");
        try
        {
            using var state = NewState(home);
            var source = Draft(home, "first");
            var destination = Draft(home, "second") with { GeneralNote = "Keep destination" };
            Activate(state, source);
            var gate = (SemaphoreSlim)typeof(CompanionState).GetField("_tuningDraftMutationGate", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(state)!;
            await gate.WaitAsync();
            Task general;
            Task corner;
            try
            {
                general = state.SaveTuningGeneralNoteAsync("Queued source note");
                corner = state.ReplaceTuningFeedbackBatchAsync(source, Turn("turn-1"), "late", [Feedback("Queued turn note")]);
                Assert.IsFalse(general.IsCompleted);
                Assert.IsFalse(corner.IsCompleted);
                await state.SelectTuningRaceAsync(null);
                Activate(state, destination);
            }
            finally { gate.Release(); }
            await Task.WhenAll(general, corner);

            var saved = new PortableTuningDraftStore(home).Load(source.RepresentativeSession);
            Assert.IsNotNull(saved);
            Assert.AreEqual("Queued source note", saved.GeneralNote);
            Assert.AreEqual("Queued turn note", saved.Feedback.Single().Note);
            Assert.AreEqual("late", saved.Feedback.Single().RunPhase);
            Assert.AreEqual("Keep destination", state.TuningDraft.GeneralNote);
            Assert.IsEmpty(state.TuningDraft.Feedback);
        }
        finally { if (Directory.Exists(home)) Directory.Delete(home, true); }
    }

    [TestMethod]
    public async Task PendingEdit_DoesNotOverwriteAReplacementMapDraft()
    {
        var home = Path.Combine(Path.GetTempPath(), $"coach-note-{Guid.NewGuid():N}");
        try
        {
            using var state = NewState(home);
            var source = Draft(home, "first");
            var changedMap = source with { MapIdentity = "replacement-map", GeneralNote = "Keep replacement" };
            await new PortableTuningDraftStore(home).SaveAsync(changedMap);
            await Assert.ThrowsAsync<InvalidOperationException>(() => state.SaveTuningGeneralNoteAsync(source, "Stale edit"));
            Assert.AreEqual("Keep replacement", new PortableTuningDraftStore(home).Load(source.RepresentativeSession)?.GeneralNote);
        }
        finally { if (Directory.Exists(home)) Directory.Delete(home, true); }
    }

    private static CompanionState NewState(string home)
    {
        var state = new CompanionState(new NoBackend(), new JsonSettingsStore(Path.Combine(home, "settings.json")));
        state.Settings.CoachHome = home;
        return state;
    }

    private static void Activate(CompanionState state, ProgressiveTuningDraft draft)
    {
        Set(state, "TuningDraft", draft);
        Set(state, "SelectedTuningRaceId", draft.RepresentativeSession.RaceId);
    }

    private static ProgressiveTuningDraft Draft(string home, string id) => new()
    {
        DraftId = id,
        MapIdentity = "map-1",
        RepresentativeSession = new TuningSessionIdentity
        {
            RaceId = id,
            AnalysisId = $"analysis-{id}",
            AnalysisPath = Path.Combine(home, id, "analysis.json"),
            TrackConfigurationKey = "bristol-single-pit",
            CarPath = "stockcars2/supra2019",
            SetupFingerprint = $"setup-{id}",
            SessionType = "Race",
            SetupType = "Open"
        }
    };

    private static TuningTurn Turn(string id) => new(id, id, .1, .2, .3, false, "recorded", string.Empty);
    private static ProgressiveTuningFeedback Feedback(string note) => new()
    {
        FeedbackId = "note-1", CornerId = "turn-1", CornerLabel = "Turn 1", StartPct = .1, ApexPct = .2, EndPct = .3,
        RunPhase = "late", CornerPhases = ["whole"], SymptomId = "other", Note = note
    };

    private static void Set(object target, string property, object? value) => target.GetType().GetProperty(property)!.SetValue(target, value);
    private static Task Invoke(object target, string method, params object[] args) => (Task)target.GetType().GetMethod(method, BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(target, args)!;

    private sealed class NoBackend : IBackendClient
    {
        public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default) => throw new InvalidOperationException("No backend request expected.");
        public Task<JsonElement> CallToolAsync(BackendConfiguration configuration, string toolName, object arguments, CancellationToken cancellationToken = default) => throw new InvalidOperationException("No backend request expected.");
    }
}
