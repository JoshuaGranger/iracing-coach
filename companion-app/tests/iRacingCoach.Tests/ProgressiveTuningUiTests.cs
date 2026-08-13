using iRacingCoach.UI;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class ProgressiveTuningUiTests
{
    [TestMethod]
    public void ExactCatalog_SeedsCurrentNascarConfigsWithoutClaimingVerifiedBounds()
    {
        var catalog = ProgressiveTurnCatalog.Default;

        var iowa = catalog.ResolveExact("559-oval", "Iowa Speedway", "Oval", requireMapIdentity: true);
        Assert.IsNotNull(iowa);
        Assert.IsFalse(iowa.Verified);
        Assert.AreEqual("nascar-official", iowa.SourceType);
        CollectionAssert.AreEqual(new[] { "Turn 1", "Turn 2", "Turn 3", "Turn 4" }, iowa.Turns.Select(turn => turn.Label).ToArray());
        Assert.IsTrue(iowa.Turns.All(turn => turn.IsOfficial && turn.Confidence == "low"));
        Assert.AreEqual(.16, iowa.Turns[0].StartPct, .00001);
        Assert.AreEqual(.845, iowa.Turns[^1].EndPct, .00001);

        var newHampshire = catalog.ResolveExact("131-oval", "New Hampshire Motor Speedway", "Oval", requireMapIdentity: true);
        Assert.IsNotNull(newHampshire);
        Assert.IsFalse(newHampshire.Verified);
        Assert.AreEqual("venue-official", newHampshire.SourceType);
        Assert.AreEqual(.135, newHampshire.Turns[0].StartPct, .00001);
        Assert.AreEqual(.855, newHampshire.Turns[^1].EndPct, .00001);
    }

    [TestMethod]
    public void ExactCatalog_DoesNotCrossApplyDisplayNameMatchToDifferentConfigKey()
    {
        var catalog = ProgressiveTurnCatalog.Default;

        Assert.IsNull(catalog.ResolveExact("different-layout", "Iowa Speedway", "Oval", requireMapIdentity: true));
        Assert.IsNotNull(catalog.ResolveExact(string.Empty, "Iowa Speedway", "Oval"));
    }

    [TestMethod]
    public void ExactCatalog_UsesHalfOpenNormalizedLapPercentContract()
    {
        Assert.IsTrue(ProgressiveTurnCatalog.IsNormalizedPercent(0));
        Assert.IsTrue(ProgressiveTurnCatalog.IsNormalizedPercent(.999999));
        Assert.IsFalse(ProgressiveTurnCatalog.IsNormalizedPercent(1));
        Assert.IsFalse(ProgressiveTurnCatalog.IsNormalizedPercent(-.000001));
        Assert.IsFalse(ProgressiveTurnCatalog.IsNormalizedPercent(double.NaN));
    }

    [TestMethod]
    public void TurnBounds_RequireForwardEntryApexExitOrderWithWrapSupport()
    {
        Assert.IsTrue(ProgressiveTurnBounds.TryValidate(.15, .2, .3, out var normalError), normalError);
        Assert.IsTrue(ProgressiveTurnBounds.TryValidate(.85, .95, .1, out var wrapError), wrapError);
        Assert.IsFalse(ProgressiveTurnBounds.TryValidate(.2, .8, .4, out var orderError));
        StringAssert.Contains(orderError, "driving order");
        Assert.IsFalse(ProgressiveTurnBounds.TryValidate(.2, .2, .3, out _));
        Assert.IsFalse(ProgressiveTurnBounds.TryValidate(.2, .25, .2, out _));
        Assert.IsFalse(ProgressiveTurnBounds.TryValidate(.2, .25, 1, out _));
    }

    [TestMethod]
    public void SameCornerIds_AreResolvedIndependentlyForIowaAndNewHampshire()
    {
        var catalog = ProgressiveTurnCatalog.Default;
        var iowa = catalog.ResolveExact("559-oval", "Iowa Speedway", "Oval", requireMapIdentity: true)!;
        var newHampshire = catalog.ResolveExact("131-oval", "New Hampshire Motor Speedway", "Oval", requireMapIdentity: true)!;

        Assert.AreEqual("turn-1", iowa.Turns[0].CornerId);
        Assert.AreEqual("turn-1", newHampshire.Turns[0].CornerId);
        Assert.AreNotEqual(iowa.MapIdentity, newHampshire.MapIdentity);
        Assert.AreNotEqual(iowa.Turns[0].StartPct, newHampshire.Turns[0].StartPct);
    }

    [TestMethod]
    public void Projection_RangePathUsesNormalizedLapPercentAndWrapsStartFinish()
    {
        ProgressiveMapPoint[] path =
        [
            new(0, 0, 0),
            new(.25, 100, 0),
            new(.5, 100, 100),
            new(.75, 0, 100)
        ];

        var normal = ProgressiveTrackProjection.PathForRange(path, .2, .55);
        var wrapped = ProgressiveTrackProjection.PathForRange(path, .8, .1);

        StringAssert.StartsWith(normal, "M ");
        StringAssert.Contains(normal, "L 100 0");
        StringAssert.StartsWith(wrapped, "M ");
        Assert.IsGreaterThanOrEqualTo(1, wrapped.Count(character => character == 'L'));
        var apex = ProgressiveTrackProjection.PointAt(path, .25);
        Assert.AreEqual(100, apex.X, .001);
        Assert.AreEqual(0, apex.Y, .001);
    }

    [TestMethod]
    public void ProgressiveTuningSource_UsesDurableStructuredFeedbackAndRichCorrectionHooks()
    {
        var ui = UiRoot();
        var page = File.ReadAllText(Path.Combine(ui, "TuningPage.razor"));
        var editor = File.ReadAllText(Path.Combine(ui, "ProgressiveTuningFeedbackEditor.razor"));
        var selector = File.ReadAllText(Path.Combine(ui, "TuningTrackSelector.razor"));
        var catalog = File.ReadAllText(Path.Combine(ui, "Data", "turn-map-catalog.v1.json"));

        StringAssert.Contains(page, "ApplyTuningMapAsync");
        StringAssert.Contains(page, "SaveTuningRepresentativeRunsAsync");
        StringAssert.Contains(page, "SaveTuningGoalAsync");
        StringAssert.Contains(page, "SaveTuningTurnCorrectionAsync");
        StringAssert.Contains(page, "_selectedTurn = State.SelectedTuningMap?.Turns.FirstOrDefault");
        StringAssert.Contains(page, "ReplaceTuningFeedbackBatchAsync");
        StringAssert.Contains(page, "_loadedRaceId = null");
        StringAssert.Contains(editor, "new(\"good\"");
        StringAssert.Contains(editor, "new(\"unstable-braking\"");
        StringAssert.Contains(editor, "new(\"wheel-hop-lock\"");
        StringAssert.Contains(editor, "new(\"cant-take-throttle\"");
        StringAssert.Contains(editor, "Priority = state.Priority");
        StringAssert.Contains(editor, "CancelPendingNote");
        StringAssert.Contains(selector, "CorrectionRequested");
        StringAssert.Contains(selector, "UserVerified");
        StringAssert.Contains(selector, "max=\"99.9\"");
        Assert.DoesNotContain("_localCorrections", selector);
        StringAssert.Contains(catalog, "iracing-hud-capture");
    }

    [TestMethod]
    public void ProgressiveTuningWorkspace_IsTrackFirstPhaseAwareAndViewportBounded()
    {
        var ui = UiRoot();
        var page = File.ReadAllText(Path.Combine(ui, "TuningPage.razor"));
        var editor = File.ReadAllText(Path.Combine(ui, "ProgressiveTuningFeedbackEditor.razor"));
        var selector = File.ReadAllText(Path.Combine(ui, "TuningTrackSelector.razor"));
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));
        var iterationCss = File.ReadAllText(Path.Combine(ui, "wwwroot", "iteration-ux.css"));
        var state = File.ReadAllText(Path.Combine(Directory.GetParent(ui)!.FullName, "iRacingCoach.Coordinator", "CompanionState.cs"));

        StringAssert.Contains(page, "tuning-workbench-v3");
        StringAssert.Contains(page, "tuning-session-bar");
        StringAssert.Contains(page, "data-active-corner-editor");
        StringAssert.Contains(page, "tuning-toolbox");
        StringAssert.Contains(page, "Open setup · can receive changes");
        StringAssert.Contains(page, "Fixed setup · driving evidence only");
        StringAssert.Contains(page, "Run phase for all turns");
        StringAssert.Contains(page, "State.TuningActiveRunPhase");
        Assert.DoesNotContain("_activeRunPhase", page);
        StringAssert.Contains(state, "public string TuningActiveRunPhase { get; set; } = \"early\";");
        Assert.DoesNotContain("TuningActiveRunPhase", File.ReadAllText(Path.Combine(Directory.GetParent(ui)!.FullName, "iRacingCoach.Contracts", "Models.cs")), "The phase selector is launch-session UI state, not a persisted setting contract.");
        StringAssert.Contains(page, "Recorded tire wear");
        StringAssert.Contains(page, "Begin analysis");
        StringAssert.Contains(page, "SetPriorityCorner");
        StringAssert.Contains(page, "CorrectionModeChanged=\"HandleCorrectionModeChanged\"");
        StringAssert.Contains(page, "if (open) _editorOpen = false");
        StringAssert.Contains(editor, "(\"tight\", \"Tight\")");
        StringAssert.Contains(editor, "(\"good\", \"Comfortable\")");
        StringAssert.Contains(editor, "(\"loose\", \"Loose\")");
        StringAssert.Contains(editor, "aria-label=\"Add symptom\"");
        StringAssert.Contains(editor, "aria-label=\"Add note\"");
        StringAssert.Contains(editor, "Severity: how much this behavior hurt");
        StringAssert.Contains(editor, "Confidence: how certain you are");
        Assert.DoesNotContain("ToggleRatingsPanel", editor);
        Assert.DoesNotContain("Not assessed", editor, StringComparison.OrdinalIgnoreCase);
        StringAssert.Contains(selector, "ActiveRunPhase");
        StringAssert.Contains(selector, "feedback-comfortable");
        StringAssert.Contains(selector, "feedback-tight");
        StringAssert.Contains(selector, "feedback-loose");
        StringAssert.Contains(selector, "CorrectionModeChanged.InvokeAsync(true)");
        StringAssert.Contains(css, ".page-frame:has(.tuning-workbench-v3)");
        StringAssert.Contains(css, "overflow: hidden");
        StringAssert.Contains(css, "grid-template-columns: minmax(0, 2fr) minmax(318px, 1fr)");
        StringAssert.Contains(css, "grid-template-columns: minmax(0, 1fr) clamp(238px, 34vw, 300px)");
        Assert.DoesNotContain(".tuning-workbench-v3 { height: auto; min-height: 900px; }", css);
        StringAssert.Contains(css, "var(--motion-structure)");
        StringAssert.Contains(iterationCss, ".tuning-active-corner-panel");
        StringAssert.Contains(iterationCss, ".tuning-turn-segment.active:not(.selected)");
    }

    [TestMethod]
    public void FixedOnlyEvidence_KeepsTuningVisibleAndRequestsAnOpenSetupTarget()
    {
        var ui = UiRoot();
        var src = Directory.GetParent(ui)!.FullName;
        var state = File.ReadAllText(Path.Combine(src, "iRacingCoach.Coordinator", "CompanionState.cs"));
        var page = File.ReadAllText(Path.Combine(ui, "TuningPage.razor"));

        StringAssert.Contains(state, "HasOpenAnalyzedRace = TuningEvidenceRaces.Any()");
        Assert.DoesNotContain("HasOpenAnalyzedRace = TuningRaces.Any()", state);
        StringAssert.Contains(page, "NeedsOpenTarget");
        StringAssert.Contains(page, "Open-setup target");
        StringAssert.Contains(page, "Choose matching race");
    }

    private static string UiRoot() => TestRepositoryPaths.UiRoot;
}
