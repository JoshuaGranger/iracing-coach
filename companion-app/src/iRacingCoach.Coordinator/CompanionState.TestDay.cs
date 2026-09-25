using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

/// <summary>
/// Test day: consecutive offline stints of one car and layout, reviewed together.
/// The backend (review_test_day) computes every window, verdict and suggestion;
/// this partial only makes sure each stint has an archived analysis, asks for the
/// review, and keeps the result for the open day.
/// </summary>
public sealed partial class CompanionState
{
    /// <summary>The backend reviews 1–12 recordings; a longer day reviews its latest twelve.</summary>
    public const int MaximumTestDayRecordings = 12;

    private long _testDayEpoch;
    private string _testDaySignature = string.Empty;

    public TestDayReview? CurrentTestDay { get; private set; }
    public string CurrentTestDayId { get; private set; } = string.Empty;
    public bool TestDayLoading { get; private set; }
    public string TestDayMessage { get; private set; } = string.Empty;
    public RecoverableAppError? TestDayError { get; private set; }

    /// <summary>
    /// What a Test day view reviews for this session: the day discovery grouped it into
    /// (two or more recordings), or, for a practice or test recording on its own whose open
    /// analysis holds two or more runs, that one recording run by run. Otherwise null.
    /// </summary>
    public RaceEventGroup? TestDayGroupFor(RecentRace? session)
    {
        if (session is null) return null;
        if (session.IsTestDayStint)
        {
            lock (_inventoryGate)
            {
                var day = EventGroups.OfType<RaceEventGroup>().FirstOrDefault(group =>
                    group.IsTestDay && string.Equals(group.TestDayId, session.TestDayId, StringComparison.OrdinalIgnoreCase));
                if (day is not null) return day;
            }
        }
        if (session.IsRace || session.IsQualifying) return null;
        if (CurrentAnalysis is not { Runs.Count: >= 2 } || !string.Equals(SelectedRaceSessionId, session.Id, StringComparison.Ordinal)) return null;
        return new RaceEventGroup($"session:{session.Id}", session.Track, session.Layout, session.Car, session.Date, session.SetupType,
            session.EventScope, [session with { TestDayId = null, TestDayRunNumber = null, TestDayRunCount = null }])
        {
            SingleRecordingKey = $"session:{session.Id}"
        };
    }

    private (string DayId, int? Run) _testDaySelection;

    /// <summary>The Test day row the driver selected on this day; it survives opening another run's recording.</summary>
    public int? TestDaySelectedRun(RaceEventGroup? group) =>
        group?.TestDayKey is { } id && string.Equals(_testDaySelection.DayId, id, StringComparison.OrdinalIgnoreCase) ? _testDaySelection.Run : null;

    public void SelectTestDayRun(RaceEventGroup group, int? run)
    {
        ArgumentNullException.ThrowIfNull(group);
        _testDaySelection = (group.TestDayKey ?? string.Empty, run);
    }

    /// <summary>The loaded review for this day, or null while it has not been read.</summary>
    public TestDayReview? TestDayReviewFor(RaceEventGroup? group) =>
        group?.TestDayKey is { } id && CurrentTestDay is { } review && string.Equals(CurrentTestDayId, id, StringComparison.OrdinalIgnoreCase)
            ? review
            : null;

    /// <summary>The stint recording a review row came from, matched by its archived analysis.</summary>
    public RecentRace? TestDaySession(RaceEventGroup group, TestDayRun run)
    {
        if (string.IsNullOrWhiteSpace(run.AnalysisPath)) return null;
        var sessions = group.Sessions.OfType<RecentRace>().Select(session => session.Id).ToHashSet(StringComparer.Ordinal);
        lock (_inventoryGate)
        {
            return EventSessions.FirstOrDefault(session => sessions.Contains(session.Id)
                && !string.IsNullOrWhiteSpace(session.AnalysisPath) && SameFile(session.AnalysisPath, run.AnalysisPath));
        }
    }

    /// <summary>
    /// Where "Describe handling" opens Progressive Tuning for a stint: the setup the
    /// stint recorded and that stint's own analyzed recording. Tuning checks the
    /// recording's setup fingerprint, so the recorded setup is the target; a .sto
    /// library file linked to the same recording carries only its file's fingerprint
    /// and would stop at "fingerprint changed" (IRP 9/24). Null when no recorded
    /// setup has tuning evidence for it, so no dead link is shown.
    /// </summary>
    public TestDayHandlingTarget? HandlingTargetFor(TestDayRun? run)
    {
        if (run?.AnalysisPath is not { Length: > 0 } path) return null;
        foreach (var setup in Setups.OfType<LocalSetup>())
        {
            if (Path.GetExtension(setup.StoPath).Equals(".sto", StringComparison.OrdinalIgnoreCase)) continue;
            if (!(setup.AnalysisPaths ?? []).Any(candidate => SameFile(candidate, path))) continue;
            var evidence = SetupEvidenceRaces(setup);
            if (evidence.Count == 0) continue;
            return new TestDayHandlingTarget(setup.Id,
                evidence.FirstOrDefault(race => !string.IsNullOrWhiteSpace(race.AnalysisPath) && SameFile(race.AnalysisPath, path))?.Id);
        }
        return null;
    }

    /// <summary>
    /// Reviews the day's stints as one job: analyze any stint without an archived
    /// analysis, then call review_test_day. Called when a stint is opened, never on
    /// navigation or bookmark restore; the view offers the same action explicitly.
    /// </summary>
    public async Task LoadTestDayAsync(RaceEventGroup group, CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(group);
        if (group.TestDayKey is not { } testDayId) return;
        var stints = group.Sessions.OfType<RecentRace>()
            .OrderBy(session => session.TestDayRunNumber ?? int.MaxValue)
            .ThenBy(session => session.StartTimeUtc, StringComparer.Ordinal)
            .ToArray();
        if (stints.Length > MaximumTestDayRecordings) stints = stints[^MaximumTestDayRecordings..];
        var signature = string.Join("|", stints.Select(session => session.Id));
        if (CurrentTestDay is not null && string.Equals(CurrentTestDayId, testDayId, StringComparison.OrdinalIgnoreCase)
            && string.Equals(_testDaySignature, signature, StringComparison.Ordinal)) return;
        if (TestDayLoading && string.Equals(CurrentTestDayId, testDayId, StringComparison.OrdinalIgnoreCase)) return;

        var epoch = Interlocked.Increment(ref _testDayEpoch);
        if (!string.Equals(CurrentTestDayId, testDayId, StringComparison.OrdinalIgnoreCase)) CurrentTestDay = null;
        CurrentTestDayId = testDayId;
        TestDayLoading = true;
        TestDayError = null;
        TestDayMessage = $"Reading {stints.Length} runs…";
        RaiseChanged();
        // One read per day: returning to a day whose review is still running waits for
        // that review instead of starting a second job that the tray would refuse.
        Task<TestDayReadResult> read;
        TaskCompletionSource<TestDayReadResult>? started = null;
        Task? earlier = null;
        lock (_testDayReadGate)
        {
            _testDayReads.TryGetValue(testDayId, out var running);
            if (running is null || running.Task.IsCompleted || !string.Equals(running.Signature, signature, StringComparison.Ordinal))
            {
                // Registered before any of its work runs, so a caller arriving meanwhile always finds it.
                earlier = running?.Task;
                started = new TaskCompletionSource<TestDayReadResult>(TaskCreationOptions.RunContinuationsAsynchronously);
                running = new TestDayRead(signature, started.Task);
                _testDayReads[testDayId] = running;
            }
            read = running.Task;
        }
        if (started is not null) _ = CompleteTestDayReadAsync(started, group, testDayId, stints, earlier, cancellationToken);
        var result = new TestDayReadResult(null, null);
        try
        {
            result = await read;
        }
        finally
        {
            lock (_testDayReadGate)
            {
                if (read.IsCompleted && _testDayReads.TryGetValue(testDayId, out var done) && done.Task == read) _testDayReads.Remove(testDayId);
            }
            if (TestDayStillCurrent(epoch))
            {
                TestDayLoading = false;
                if (result.Review is { } review)
                {
                    CurrentTestDay = review;
                    _testDaySignature = signature;
                    TestDayMessage = stints.Length < group.Sessions.Count
                        ? $"Showing the latest {stints.Length} of {group.Sessions.Count} recordings."
                        : string.Empty;
                }
                else
                {
                    TestDayError = result.Error;
                    if (CurrentTestDay is null)
                        TestDayMessage = result.Error is { } error ? BackendMessage(error.Message) : "The day's runs were not compared.";
                }
                RaiseChanged();
            }
        }
    }

    private readonly object _testDayReadGate = new();
    private readonly Dictionary<string, TestDayRead> _testDayReads = new(StringComparer.OrdinalIgnoreCase);
    private sealed record TestDayRead(string Signature, Task<TestDayReadResult> Task);
    private sealed record TestDayReadResult(TestDayReview? Review, RecoverableAppError? Error);

    private async Task CompleteTestDayReadAsync(TaskCompletionSource<TestDayReadResult> completion, RaceEventGroup group, string testDayId,
        IReadOnlyList<RecentRace> stints, Task? earlier, CancellationToken cancellationToken)
    {
        try { completion.SetResult(await ReadTestDayAsync(group, testDayId, stints, earlier, cancellationToken)); }
        catch (Exception ex) { completion.SetException(ex); }
    }

    /// <summary>
    /// The day's one job: analyze any stint without an archived analysis, then call
    /// review_test_day. Its result goes to every caller waiting for this day; each
    /// shows it only if the day is still the one open.
    /// </summary>
    private async Task<TestDayReadResult> ReadTestDayAsync(RaceEventGroup group, string testDayId, IReadOnlyList<RecentRace> stints,
        Task? earlier, CancellationToken cancellationToken)
    {
        if (earlier is not null)
        {
            try { await earlier; }
            catch (Exception ex) when (ex is OperationCanceledException or InvalidOperationException) { }
        }
        TestDayReview? review = null;
        RecoverableAppError? failure = null;
        var jobKey = $"test-day:{testDayId}";
        void Stage(string message)
        {
            SetJobStage(jobKey, message);
            if (!TestDayLoading || !string.Equals(CurrentTestDayId, testDayId, StringComparison.OrdinalIgnoreCase)) return;
            TestDayMessage = message;
            RaiseChanged();
        }
        await RunJobAsync($"Test day · {group.Track}", jobKey, "Reading the day's runs", async token =>
        {
            var paths = new List<string>(stints.Count);
            for (var index = 0; index < stints.Count; index++)
            {
                Stage(stints.Count == 1 ? "Reading the recording…" : $"Reading recording {index + 1} of {stints.Count}…");
                var path = await EnsureAnalyzedAsync(stints[index], token);
                if (string.IsNullOrWhiteSpace(path))
                    throw new InvalidOperationException($"Recording {index + 1} has no saved analysis to compare.");
                paths.Add(path);
            }
            Stage("Comparing the runs…");
            var response = await CallBackendAsync("review_test_day", new
            {
                analysis_paths = paths,
                archive_root = Settings.ArchiveRoot
            }, token);
            review = TestDayMapper.Map(response);
            AdoptArchivedStints(paths);
        }, cancellationToken, error => failure = error);
        return new TestDayReadResult(review, failure);
    }

    /// <summary>Opens another stint of the open day; the day's review stays loaded.</summary>
    public async Task OpenTestDayRunAsync(RecentRace session, CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(session);
        if (string.Equals(session.Id, SelectedRaceSessionId, StringComparison.Ordinal) && CurrentAnalysis is not null) return;
        await AnalyzeRaceAsync(session, cancellationToken);
        if (CurrentAnalysis is not null && string.Equals(SelectedRaceSessionId, session.Id, StringComparison.Ordinal)
            && TestDayGroupFor(session) is { } day)
            await LoadTestDayAsync(day, cancellationToken);
    }

    private bool TestDayStillCurrent(long epoch) => Volatile.Read(ref _testDayEpoch) == epoch;

    /// <summary>
    /// The inventory lists archived analyses as tuning evidence only at its next refresh, so a
    /// stint this job just analyzed was not evidence for "Describe handling". Each stint's
    /// archived entry and recorded setup join the lists now, exactly as a refresh would add them.
    /// </summary>
    private void AdoptArchivedStints(IReadOnlyList<string> paths)
    {
        foreach (var path in paths)
        {
            ArchivedRaceCatalog.Entry? entry;
            try { entry = File.Exists(path) ? ReadArchivedCatalog(path) : null; }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or System.Text.Json.JsonException or InvalidDataException or InvalidOperationException)
            {
                continue;
            }
            if (entry is null) continue;
            lock (_inventoryGate)
            {
                if (entry.Race is { } race && !Races.Any(item => !string.IsNullOrWhiteSpace(item.AnalysisPath) && SameFile(item.AnalysisPath, path)))
                    Races.Add(race);
                if (entry.Setup is { } setup)
                {
                    var index = Setups.FindIndex(item => string.Equals(item.Id, setup.Id, StringComparison.OrdinalIgnoreCase));
                    if (index < 0) Setups.Add(setup);
                    else Setups[index] = Setups[index] with
                    {
                        AnalysisPaths = (Setups[index].AnalysisPaths ?? []).Concat(setup.AnalysisPaths ?? []).Distinct(StringComparer.OrdinalIgnoreCase).ToArray()
                    };
                }
            }
        }
    }

    /// <summary>A forced re-analysis of a stint makes the loaded day stale; the next open re-reads it.</summary>
    private void InvalidateTestDay(RecentRace race)
    {
        if ((race.IsTestDayStint && string.Equals(race.TestDayId, CurrentTestDayId, StringComparison.OrdinalIgnoreCase))
            || string.Equals($"session:{race.Id}", CurrentTestDayId, StringComparison.OrdinalIgnoreCase))
            _testDaySignature = string.Empty;
    }

    private void SetJobStage(string canonicalKey, string stage)
    {
        var job = Jobs.FirstOrDefault(item => item.CanonicalKey == canonicalKey && item.Status is "queued" or "running");
        if (job is not null) job.Stage = stage;
    }

    /// <summary>
    /// The archived analysis for a recording, analyzing it only when neither the
    /// session nor a current UI cache entry names an existing analysis.json. A
    /// stint that background maintenance is reading right now is waited for,
    /// not analyzed twice.
    /// </summary>
    private async Task<string> EnsureAnalyzedAsync(RecentRace session, CancellationToken token)
    {
        await WaitForBackgroundAnalysisOfAsync(session, token);
        RecentRace current;
        lock (_inventoryGate)
        {
            current = EventSessions.FirstOrDefault(candidate => string.Equals(candidate.Id, session.Id, StringComparison.Ordinal)) ?? session;
        }
        if (!string.IsNullOrWhiteSpace(current.AnalysisPath) && File.Exists(current.AnalysisPath)) return current.AnalysisPath;
        if (TryReadUiAnalysisCache(current, out var overview, out var cachedPath)
            && !string.IsNullOrWhiteSpace(cachedPath) && File.Exists(cachedPath))
        {
            if (ApplySuccessfulRaceAnalysis(current, overview, cachedPath)) RaiseChanged();
            return cachedPath;
        }

        var analysis = await CallBackendAsync("analyze_iracing_race", new
        {
            selector = current.EffectiveSelector,
            iracing_root = Settings.IRacingRoot,
            archive_root = Settings.ArchiveRoot,
            target_hz = (double?)null
        }, token);
        EnsureResponseMatchesSession(current, analysis);
        SaveUiAnalysisCache(current, analysis);
        var analysisPath = AnalysisPathFromResponse(analysis);
        var mapped = RuntimeMapper.Analysis(analysis);
        ApplySuccessfulRaceAnalysis(current, mapped, analysisPath);
        if (mapped.VectorGeometry is { TrackConfigurationKey.Length: > 0 } geometry)
            _geometryByConfiguration[geometry.TrackConfigurationKey] = geometry;
        RaiseChanged();
        return analysisPath;
    }

    /// <summary>
    /// Waits while background maintenance is inside its backend call for this
    /// recording. A worker parked between attempts (it yields to a running test-day
    /// review, interactive analysis or live driving) is not waited for: it waits for
    /// this job, and rereads the cache this job writes before it tries again.
    /// </summary>
    private async Task WaitForBackgroundAnalysisOfAsync(RecentRace session, CancellationToken token)
    {
        var key = UiAnalysisCacheKey(session);
        while (true)
        {
            lock (_homeAnalysisSync)
            {
                if (!string.Equals(_homeAnalysisCallKey, key, StringComparison.OrdinalIgnoreCase)) return;
            }
            await Task.Delay(TimeSpan.FromMilliseconds(100), token);
        }
    }

    private static bool SameFile(string left, string right)
    {
        try { return string.Equals(Path.GetFullPath(left), Path.GetFullPath(right), StringComparison.OrdinalIgnoreCase); }
        catch (Exception ex) when (ex is ArgumentException or NotSupportedException or PathTooLongException)
        {
            return string.Equals(left, right, StringComparison.OrdinalIgnoreCase);
        }
    }
}

/// <summary>A setup-library entry and the recording Progressive Tuning should open with it.</summary>
public sealed record TestDayHandlingTarget(string SetupId, string? RaceId);
