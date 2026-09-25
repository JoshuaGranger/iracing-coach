using System.Globalization;
using System.Text.Json;
using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

/// <summary>
/// Retained investigation selection and the saved-test loop: save one change
/// from a backend action, find a later compatible recording, and reopen the
/// measured before/after evidence without reassembling the selection.
/// </summary>
public sealed partial class CompanionState
{
    private static readonly string[] InvestigationViews = ["race", "runs", "sections", "traces", "replay", "details"];
    private long _trialListEpoch;

    public IReadOnlyList<DrivingTrial> DrivingTrials { get; private set; } = [];
    public DrivingTrial? OpenedTrial { get; private set; }
    public string? OpenedTrialEvaluationId { get; private set; }
    public bool TrialBusy { get; private set; }
    public string TrialMessage { get; private set; } = string.Empty;
    public bool DrivingTrialsLoaded { get; private set; }

    /// <summary>The selection for the open analysis; defaults when the bookmark belongs to another recording.</summary>
    public InvestigationBookmark InvestigationSelection =>
        Settings.LastInvestigation is { } bookmark && CurrentAnalysis is { } analysis
            && string.Equals(bookmark.AnalysisId, analysis.AnalysisId, StringComparison.Ordinal)
            ? bookmark
            : new InvestigationBookmark { SessionId = SelectedRaceSessionId, AnalysisId = CurrentAnalysis?.AnalysisId ?? string.Empty };

    public void SetInvestigationSelection(string view, string? comparisonId, string? sectionId)
    {
        if (CurrentAnalysis is null) return;
        var normalized = InvestigationViews.Contains(view, StringComparer.Ordinal) ? view : "race";
        var bookmark = Settings.LastInvestigation;
        if (bookmark is not null && bookmark.SessionId == SelectedRaceSessionId && bookmark.AnalysisId == CurrentAnalysis.AnalysisId
            && bookmark.View == normalized && bookmark.ComparisonId == comparisonId && bookmark.SectionId == sectionId) return;
        Settings.LastInvestigation = new InvestigationBookmark
        {
            SessionId = SelectedRaceSessionId,
            AnalysisId = CurrentAnalysis.AnalysisId,
            View = normalized,
            ComparisonId = comparisonId,
            SectionId = sectionId,
            ExtensionData = bookmark?.ExtensionData
        };
        PersistSettingsQuietly();
        RaiseChanged();
    }

    /// <summary>
    /// Reopens the last investigation only when its session still exists and its
    /// cached analysis is current. Anything else leaves the session library.
    /// </summary>
    private async Task RestoreInvestigationAsync(CancellationToken cancellationToken)
    {
        if (Settings.LastInvestigation is not { SessionId.Length: > 0 } bookmark || AnalysisWorkspaceOpen || CurrentPage != "home") return;
        var session = EventSessions.FirstOrDefault(candidate => string.Equals(candidate.Id, bookmark.SessionId, StringComparison.Ordinal));
        if (session is null) return;
        var epoch = Volatile.Read(ref _analysisSelectionEpoch);
        CachedAnalysis? cached;
        try { cached = await LoadCachedAnalysisAsync(session, cancellationToken); }
        catch (OperationCanceledException) { return; }
        if (cached is null || cached.Card is null || !string.Equals(cached.Analysis.AnalysisId, bookmark.AnalysisId, StringComparison.Ordinal)) return;
        // The driver may have opened something else while the cache was read.
        if (Volatile.Read(ref _analysisSelectionEpoch) != epoch || AnalysisWorkspaceOpen || CurrentPage != "home") return;
        SelectRaceSession(session);
        AnalysisWorkspaceOpen = true;
        PublishCachedAnalysis(session, cached);
        CurrentPage = "analysis";
        RaiseChanged();
    }

    private string _planContextSessionId = string.Empty;

    /// <summary>
    /// Plan follows the race the driver selected, once per selection. The newest
    /// recording is only a first-run default; it never replaces a chosen race.
    /// </summary>
    private void CarryPlanContext()
    {
        if (SelectedRaceSessionId.Length == 0 || SelectedRaceSessionId == _planContextSessionId) return;
        var race = Races.OfType<RecentRace>().FirstOrDefault(candidate => string.Equals(candidate.Id, SelectedRaceSessionId, StringComparison.Ordinal));
        if (race is null || !race.IsRace) return;
        _planContextSessionId = SelectedRaceSessionId;
        SelectedPlanCarId = race.CarPath.Length > 0 ? race.CarPath : race.Car;
        SelectedPlanTrack = $"{race.Track}|{race.Layout}";
        PlanSetupType = race.SetupType.Equals("Open", StringComparison.OrdinalIgnoreCase) ? "Open" : "Fixed";
        SelectedPlanRaceId = race.Id;
    }

    public DrivingTrial? SavedTrialFor(string analysisId, string comparisonId, string title) =>
        DrivingTrials.FirstOrDefault(trial => string.Equals(trial.Source.AnalysisId, analysisId, StringComparison.Ordinal)
            && string.Equals(trial.ComparisonId, comparisonId, StringComparison.Ordinal)
            && string.Equals(trial.Title, title, StringComparison.Ordinal));

    public async Task SaveDrivingTrialAsync(string comparisonId, int priority, CancellationToken cancellationToken = default)
    {
        if (TrialBusy) return;
        if (!TryGetAnalysisPath(SelectedRaceSession, out var analysisPath))
        {
            Notify("This recording's saved analysis is not available yet. Refresh the analysis, then save the test.");
            return;
        }
        TrialBusy = true;
        RaiseChanged();
        try
        {
            var result = await CallBackendAsync("save_driving_trial", new
            {
                analysis_path = analysisPath,
                comparison_id = comparisonId,
                action_priority = priority,
                archive_root = Settings.ArchiveRoot
            }, cancellationToken);
            var trial = RaceInvestigationMapper.Trial(result.GetProperty("trial"));
            DrivingTrials = [trial, .. DrivingTrials.Where(item => item.TrialId != trial.TrialId)];
            Notify(result.TryGetProperty("created", out var created) && created.ValueKind == JsonValueKind.False
                ? "This test was already saved."
                : "Test saved. It will be checked against your next compatible recording.");
        }
        catch (OperationCanceledException) { }
        catch (Exception ex) when (ex is BackendDomainException or JsonException or KeyNotFoundException or InvalidOperationException)
        {
            Notify(ex is BackendDomainException ? Bound(BackendMessage(ex.Message)) : "The test could not be saved. Refresh the analysis and try again.");
        }
        finally
        {
            TrialBusy = false;
            RaiseChanged();
        }
    }

    public async Task RefreshDrivingTrialsAsync(CancellationToken cancellationToken = default)
    {
        var epoch = Interlocked.Increment(ref _trialListEpoch);
        try
        {
            var result = await CallBackendAsync("list_driving_trials", new { archive_root = Settings.ArchiveRoot, limit = 200 }, cancellationToken);
            var trials = result.GetProperty("trials").EnumerateArray().Select(item =>
            {
                try { return RaceInvestigationMapper.Trial(item); }
                catch (JsonException) { return null; }
            }).OfType<DrivingTrial>().ToArray();
            if (Volatile.Read(ref _trialListEpoch) != epoch) return;
            DrivingTrials = trials;
            DrivingTrialsLoaded = true;
            RaiseChanged();
        }
        catch (OperationCanceledException) { }
        catch (Exception ex) when (ex is BackendDomainException or JsonException or KeyNotFoundException or InvalidOperationException or BackendProtocolException)
        {
            DrivingTrialsLoaded = true;
            TrialMessage = "Saved tests could not be read.";
            RaiseChanged();
        }
    }

    /// <summary>Later recordings of the same car and layout, newest first; the backend decides comparability.</summary>
    public IReadOnlyList<RecentRace> TrialCandidates(DrivingTrial trial)
    {
        var after = DateTimeOffset.TryParse(trial.Source.RecordedAt, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out var recorded)
            ? recorded : DateTimeOffset.MinValue;
        return EventSessions
            .Where(session => SameText(session.Track, trial.Source.Track) && SameText(session.Layout, trial.Source.Layout)
                && SameText(session.Car, trial.Source.Car) && !session.IsQualifying
                && DateTimeOffset.TryParse(session.StartTimeUtc, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out var start)
                && start > after.AddMinutes(1))
            .OrderByDescending(session => session.StartTimeUtc, StringComparer.Ordinal)
            .ToArray();
    }

    /// <summary>Candidates not yet measured; a repeat check of the same recording replaces its entry.</summary>
    public IReadOnlyList<RecentRace> UncheckedTrialCandidates(DrivingTrial trial) => TrialCandidates(trial)
        .Where(session => !trial.Evaluations.Concat(trial.LatestEvaluation is { } latest ? [latest] : [])
            .Any(evaluation => SameRecording(evaluation.Recording, session))).ToArray();

    public static bool SameRecording(DrivingTrialRecording recording, RecentRace session) =>
        DateTimeOffset.TryParse(recording.RecordedAt, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out var recorded)
        && DateTimeOffset.TryParse(session.StartTimeUtc, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out var start)
        && Math.Abs((recorded - start).TotalMinutes) < 1
        && string.Equals(recording.SessionType, session.SessionType, StringComparison.OrdinalIgnoreCase);

    private readonly Dictionary<string, AnalysisTrackGeometry> _geometryByConfiguration = new(StringComparer.Ordinal);

    /// <summary>Recorded layout for a saved test's map; absent until a recording of that layout has been opened.</summary>
    public AnalysisTrackGeometry? TrackGeometryFor(string? configurationKey)
    {
        if (string.IsNullOrWhiteSpace(configurationKey)) return null;
        if (CurrentAnalysis?.VectorGeometry is { } current && string.Equals(current.TrackConfigurationKey, configurationKey, StringComparison.Ordinal))
        {
            _geometryByConfiguration[configurationKey] = current;
            return current;
        }
        return _geometryByConfiguration.GetValueOrDefault(configurationKey);
    }

    /// <summary>The human sentence inside a backend error envelope; never raw JSON.</summary>
    private static string BackendMessage(string message)
    {
        var trimmed = message.Trim();
        if (!trimmed.StartsWith('{')) return message;
        try
        {
            using var document = JsonDocument.Parse(trimmed);
            return document.RootElement.TryGetProperty("message", out var text) && text.ValueKind == JsonValueKind.String
                ? text.GetString() ?? message
                : message;
        }
        catch (JsonException) { return message; }
    }

    private static bool SameText(string? left, string? right) =>
        !string.IsNullOrWhiteSpace(left) && string.Equals(left.Trim(), right?.Trim(), StringComparison.OrdinalIgnoreCase);

    public async Task OpenDrivingTrialAsync(string trialId, CancellationToken cancellationToken = default)
    {
        try
        {
            var result = await CallBackendAsync("load_driving_trial", new { trial_id = trialId, archive_root = Settings.ArchiveRoot }, cancellationToken);
            OpenedTrial = RaceInvestigationMapper.Trial(result.GetProperty("trial"), result.GetProperty("evaluations"));
            OpenedTrialEvaluationId = OpenedTrial.Evaluations.LastOrDefault()?.AnalysisId;
            RaiseChanged();
        }
        catch (OperationCanceledException) { }
        catch (Exception ex) when (ex is BackendDomainException or JsonException or KeyNotFoundException or InvalidOperationException)
        {
            Notify(ex is BackendDomainException ? Bound(BackendMessage(ex.Message)) : "That test could not be opened.");
        }
    }

    public void CloseDrivingTrial()
    {
        OpenedTrial = null;
        OpenedTrialEvaluationId = null;
        RaiseChanged();
    }

    public void SelectTrialEvaluation(string? analysisId)
    {
        OpenedTrialEvaluationId = analysisId;
        RaiseChanged();
    }

    /// <summary>Analyzes the later recording when needed, then measures the saved test on it.</summary>
    public async Task CheckDrivingTrialAsync(DrivingTrial trial, RecentRace session, CancellationToken cancellationToken = default)
    {
        await RunJobAsync($"Check test · {session.Track}", $"trial:{trial.TrialId}:{session.Id}", "Reading the later recording", async token =>
        {
            var analysisPath = await EnsureAnalyzedAsync(session, token);
            if (string.IsNullOrWhiteSpace(analysisPath)) throw new InvalidOperationException("The later recording has no saved analysis to measure.");
            var result = await CallBackendAsync("evaluate_driving_trial", new
            {
                trial_id = trial.TrialId,
                analysis_path = analysisPath,
                archive_root = Settings.ArchiveRoot
            }, token);
            var evaluation = RaceInvestigationMapper.Evaluation(result.GetProperty("evaluation"));
            await OpenDrivingTrialAsync(trial.TrialId, token);
            OpenedTrialEvaluationId = evaluation.AnalysisId;
            await RefreshDrivingTrialsAsync(token);
        }, cancellationToken);
    }
}
