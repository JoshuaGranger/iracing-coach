using System.Globalization;
using System.Text.Json;
using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

/// <summary>
/// The Setups page's library search, the recordings each setup links to, the backend
/// comparison of two setups, and keeping the library current once new recordings are
/// analysed.
/// </summary>
public sealed partial class CompanionState
{
    private string _setupComparisonKey = string.Empty;
    private string _setupComparisonFailedKey = string.Empty;
    private int _setupLibraryStale;

    /// <summary>
    /// Every word of the search appears somewhere in the setup's name, file, car (display
    /// name or iRacing car path), track, season or session, in any order: "supra IRP"
    /// found nothing when the whole phrase had to appear in one place.
    /// </summary>
    public static bool MatchesSetupSearch(LocalSetup setup, string? query)
    {
        var words = (query ?? string.Empty).Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries);
        if (words.Length == 0) return true;
        var haystack = string.Join(' ', setup.Name, Path.GetFileName(setup.StoPath), setup.Car, setup.CarKey, setup.Track,
            setup.Season, setup.Role);
        return words.All(word => haystack.Contains(word, StringComparison.CurrentCultureIgnoreCase));
    }

    /// <summary>
    /// The analysed recordings that loaded this setup: archived races and the sessions this
    /// app analysed since the last refresh (an offline test stint is a session, not a race),
    /// one per recording.
    /// </summary>
    public IReadOnlyList<RecentRace> SetupEvidenceRaces(LocalSetup setup)
    {
        var paths = (setup.AnalysisPaths ?? []).Append(setup.StoPath)
            .Where(path => !string.IsNullOrWhiteSpace(path)).ToHashSet(StringComparer.OrdinalIgnoreCase);
        RecentRace[] sessions;
        lock (_inventoryGate) sessions = Races.Concat(EventSessions).ToArray();
        return sessions
            .Where(race => !string.IsNullOrWhiteSpace(race.AnalysisPath) && paths.Contains(race.AnalysisPath))
            // One analysis listed as an archived race and as a session is one entry, kept
            // with the start time the session carries.
            .GroupBy(race => race.AnalysisPath, StringComparer.OrdinalIgnoreCase)
            .Select(group => group.FirstOrDefault(race => !string.IsNullOrWhiteSpace(race.StartTimeUtc)) ?? group.First())
            // Two analyses of one recording start at the same instant; list the recording once,
            // as the analysis whose values the setup shows when it is one of them.
            .GroupBy(RecordingKey, StringComparer.Ordinal)
            .Select(group => group.FirstOrDefault(race => string.Equals(race.AnalysisPath, setup.ValuesAnalysisPath, StringComparison.OrdinalIgnoreCase))
                ?? group.First())
            .ToArray();

        static string RecordingKey(RecentRace race) =>
            DateTimeOffset.TryParse(race.StartTimeUtc, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out var start)
                ? "start:" + start.UtcTicks.ToString(CultureInfo.InvariantCulture)
                : "analysis:" + race.AnalysisPath.ToUpperInvariant();
    }

    /// <summary>The backend comparison of these two setups, when it has been read for their current values.</summary>
    public SetupValueComparison? SetupComparisonFor(LocalSetup left, LocalSetup right) =>
        SetupComparison is { } comparison && string.Equals(_setupComparisonKey, SetupComparisonKey(left, right), StringComparison.Ordinal)
            ? comparison : null;

    public SetupValueComparison? SetupComparison { get; private set; }
    public bool SetupComparisonLoading { get; private set; }
    public string SetupComparisonError { get; private set; } = string.Empty;

    public bool IsSetupComparisonPending(LocalSetup left, LocalSetup right) =>
        SetupComparisonLoading && string.Equals(_setupComparisonKey, SetupComparisonKey(left, right), StringComparison.Ordinal);

    /// <summary>Whether this pair's comparison has failed; it is not retried until the values or the pair change.</summary>
    public bool SetupComparisonFailed(LocalSetup left, LocalSetup right) =>
        string.Equals(_setupComparisonFailedKey, SetupComparisonKey(left, right), StringComparison.Ordinal);

    /// <summary>
    /// Asks the backend to compare the two setups' displayed values. The backend decides
    /// which values differ (-0 equals 0, a unit conversion within half a display step) and
    /// which are computed garage readouts, with the same readout set the Test day uses.
    /// </summary>
    public async Task CompareSetupsAsync(LocalSetup left, LocalSetup right, CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(left);
        ArgumentNullException.ThrowIfNull(right);
        var key = SetupComparisonKey(left, right);
        if (string.Equals(_setupComparisonKey, key, StringComparison.Ordinal) && (SetupComparison is not null || SetupComparisonLoading)) return;
        _setupComparisonKey = key;
        _setupComparisonFailedKey = string.Empty;
        SetupComparison = null;
        SetupComparisonError = string.Empty;
        SetupComparisonLoading = true;
        RaiseChanged();
        try
        {
            var response = await CallBackendAsync("compare_iracing_setups", new
            {
                scheme = SetupComparability.ValueScheme(left),
                left = DisplayedValues(left),
                right = DisplayedValues(right)
            }, cancellationToken);
            var comparison = RuntimeMapper.SetupComparison(response, left.Id, right.Id);
            if (string.Equals(_setupComparisonKey, key, StringComparison.Ordinal)) SetupComparison = comparison;
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            if (string.Equals(_setupComparisonKey, key, StringComparison.Ordinal)) _setupComparisonKey = string.Empty;
        }
        catch (Exception ex)
        {
            if (string.Equals(_setupComparisonKey, key, StringComparison.Ordinal))
            {
                _setupComparisonFailedKey = key;
                SetupComparisonError = "The local analysis service could not compare these setups. Choose No comparison to list this setup's values.";
            }
            _ = StructuredAppLog.Record("setup comparison", ex, AppVersion, Settings.LogsRoot, _pathProvider.UserProfile);
        }
        finally
        {
            if (string.Equals(_setupComparisonKey, key, StringComparison.Ordinal) || _setupComparisonKey.Length == 0) SetupComparisonLoading = false;
            RaiseChanged();
        }
    }

    /// <summary>What a comparison sends for one setup: its garage values as displayed, without run readings.</summary>
    internal static Dictionary<string, string> DisplayedValues(LocalSetup setup) =>
        SetupReadings.Values(setup).GroupBy(field => field.Key, StringComparer.Ordinal)
            .ToDictionary(group => group.Key, group => group.First().Value, StringComparer.Ordinal);

    private static string SetupComparisonKey(LocalSetup left, LocalSetup right) =>
        $"{left.Id}\u001f{right.Id}\u001f{ValuesSignature(left)}\u001f{ValuesSignature(right)}";

    private static string ValuesSignature(LocalSetup setup) =>
        JsonSerializer.Serialize(SetupReadings.Values(setup).Select(field => new[] { field.Key, field.Value }));

    /// <summary>
    /// An analysis added a recording the setup library has not seen. The library and its
    /// file links are built by the inventory refresh (the backend links a .sto to the
    /// recordings that loaded it), so they are refreshed once the background analyses have
    /// finished: the IRP test day's setup files read "Values unknown · 0 linked recordings"
    /// beside their own recordings until the app restarted.
    /// </summary>
    private void NoteNewSetupEvidence()
    {
        Interlocked.Exchange(ref _setupLibraryStale, 1);
        RefreshSetupLibraryIfIdle();
    }

    private void RefreshSetupLibraryIfIdle()
    {
        if (PendingRaceAnalysisCount > 0) return; // The last background analysis calls this again.
        if (Interlocked.Exchange(ref _setupLibraryStale, 0) == 0) return;
        MarkRefreshDirty();
    }
}
