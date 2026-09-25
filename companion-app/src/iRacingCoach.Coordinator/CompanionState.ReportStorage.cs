using System.Diagnostics;
using System.Text.Json;
using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

/// <summary>
/// Older copies of saved analyses (GAP-025, PORT-007). The backend
/// (reclaim_report_storage) decides which copies may move and moves them; this
/// partial asks for the plan, opens the review only when the plan lists something,
/// sends the driver's confirmation of exactly that plan, and afterwards drops app
/// caches that named a moved copy.
/// </summary>
public sealed partial class CompanionState
{
    private const string ReportStorageTool = "reclaim_report_storage";
    private static readonly TimeSpan ReportStorageRefreshInterval = TimeSpan.FromMinutes(5);
    private DateTimeOffset _reportStorageCheckedUtc = DateTimeOffset.MinValue;
    private Task? _reportStorageRefresh;

    /// <summary>The backend's latest plan; null until it has been read or when it cannot be.</summary>
    public ReportStoragePlan? ReportStorage { get; private set; }

    /// <summary>The outcome of the last confirmed removal, shown in the review until it closes.</summary>
    public ReportStorageResult? ReportStorageOutcome { get; private set; }

    /// <summary>
    /// The plan the open review shows, captured when it opened: the confirmation sends
    /// exactly this plan even if a newer one is read meanwhile. Null when no review is open.
    /// </summary>
    public ReportStoragePlan? ReportStorageReview { get; private set; }

    public bool ReportStorageReviewOpen => ReportStorageReview is not null;

    /// <summary>True while a confirmed removal runs.</summary>
    public bool ReportStorageBusy { get; private set; }

    /// <summary>Why the confirmation could not run now (a job in progress, or a failure).</summary>
    public string ReportStorageMessage { get; private set; } = string.Empty;

    /// <summary>Whether the app may open folders in Explorer (not in the isolated profile).</summary>
    public bool CanOpenFolders => _allowExternalHostActions;

    /// <summary>
    /// Reads the plan in the background. Settings calls it when it opens; a plan read
    /// in the last few minutes is kept unless <paramref name="force"/>. Nothing is read
    /// while copies move.
    /// </summary>
    public Task RefreshReportStorageAsync(bool force = false, CancellationToken cancellationToken = default)
    {
        if (_disposed || ReportStorageBusy || !_backend.SupportsTool(ReportStorageTool)) return Task.CompletedTask;
        if (_reportStorageRefresh is { IsCompleted: false } running) return running;
        if (!force && DateTimeOffset.UtcNow - _reportStorageCheckedUtc < ReportStorageRefreshInterval)
            return Task.CompletedTask;
        _reportStorageCheckedUtc = DateTimeOffset.UtcNow;
        return _reportStorageRefresh = ReadReportStoragePlanAsync(cancellationToken);
    }

    private async Task ReadReportStoragePlanAsync(CancellationToken cancellationToken)
    {
        try
        {
            var response = await CallBackendAsync(ReportStorageTool, new
            {
                archive_root = Settings.ArchiveRoot,
                keep_analysis_paths = ReportStorageKeepPaths()
            }, cancellationToken);
            var plan = ReportStorageMapper.MapPlan(response);
            if (ReportStorageBusy) return;
            ReportStorage = plan;
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            _reportStorageCheckedUtc = DateTimeOffset.MinValue;
        }
        catch (Exception ex)
        {
            // Measuring is optional: Settings simply shows no storage line, and the
            // failure is logged once per interval rather than on every visit.
            _ = StructuredAppLog.Record("saved-analysis storage plan", ex, AppVersion, Settings.LogsRoot, _pathProvider.UserProfile);
            ReportStorage = null;
        }
        finally
        {
            RaiseChanged();
        }
    }

    /// <summary>Opens the review of the current plan. Does nothing when the plan lists no copy.</summary>
    public void ReviewReportStorage()
    {
        if (ReportStorageBusy || ReportStorage is not { HasReclaimable: true } plan) return;
        ReportStorageOutcome = null;
        ReportStorageMessage = string.Empty;
        ReportStorageReview = plan;
        RaiseChanged();
    }

    public void CloseReportStorageReview()
    {
        if (ReportStorageBusy) return;
        ReportStorageReview = null;
        ReportStorageOutcome = null;
        ReportStorageMessage = string.Empty;
        RaiseChanged();
    }

    /// <summary>
    /// The driver's confirmation: sends exactly the reviewed plan. The backend re-checks
    /// every copy under the history-index lock and moves only those still eligible; the
    /// app never chooses a copy itself. Runs only from an open review.
    /// </summary>
    public async Task ConfirmReportStorageRemovalAsync(CancellationToken cancellationToken = default)
    {
        if (ReportStorageBusy || ReportStorageOutcome is not null || ReportStorageReview is not { HasReclaimable: true } plan) return;
        var blockers = Jobs.Where(job => job.Status is "queued" or "running").Select(job => job.Title).ToList();
        if (AnalysisLoading) blockers.Add("the analysis being opened");
        if (TestDayLoading) blockers.Add("the test-day review");
        if (blockers.Count > 0)
        {
            ReportStorageMessage = $"Wait for {string.Join(", ", blockers.Distinct(StringComparer.OrdinalIgnoreCase))} to finish, then remove the copies.";
            RaiseChanged();
            return;
        }

        ReportStorageBusy = true;
        ReportStorageMessage = string.Empty;
        RaiseChanged();
        var refresh = false;
        try
        {
            // No inventory pass reads the reports folder while copies move out of it.
            await _refreshGate.WaitAsync(cancellationToken);
            try
            {
                var response = await CallBackendAsync(ReportStorageTool, new
                {
                    confirm = true,
                    plan_id = plan.PlanId,
                    directories = plan.Remove.Directories.Select(copy => copy.Directory).ToArray(),
                    keep_analysis_paths = ReportStorageKeepPaths(),
                    archive_root = Settings.ArchiveRoot
                }, CancellationToken.None, DetachedBackendOperationPolicy.CompleteForCache);
                var outcome = ReportStorageMapper.MapResult(response);
                if (!string.Equals(outcome.ConfirmedPlanId, plan.PlanId, StringComparison.Ordinal))
                    throw new InvalidDataException("The backend answered a different confirmation.");
                ReportStorageOutcome = outcome;
                refresh = outcome.MovedCount > 0;
                if (refresh) DropUiCachesNaming(outcome.Moved.Select(copy => copy.Directory).ToArray());
            }
            finally
            {
                _refreshGate.Release();
            }
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            var error = StructuredAppLog.Record("remove older saved-analysis copies", ex, AppVersion, Settings.LogsRoot, _pathProvider.UserProfile);
            LastRecoverableError = error;
            ReportStorageMessage = "The removal could not finish. Nothing was deleted; details are in the app log.";
            refresh = true;
        }
        finally
        {
            ReportStorageBusy = false;
            RaiseChanged();
        }
        if (!refresh) return;
        // The library and setups no longer see moved copies, and Settings shows what remains.
        _ = RefreshDataAsync(showToast: false, CancellationToken.None);
        _ = RefreshReportStorageAsync(force: true);
    }

    public void OpenRemovedReportsFolder()
    {
        var folder = ReportStorageOutcome?.Recovery.Path ?? ReportStorage?.Recovery.Path;
        if (string.IsNullOrWhiteSpace(folder)) return;
        if (!_allowExternalHostActions)
        {
            Notify("Opening folders is disabled in the isolated host profile.", ToastTone.Warning);
            return;
        }
        if (!Directory.Exists(folder)) return;
        _ = Process.Start(new ProcessStartInfo("explorer.exe") { UseShellExecute = true, ArgumentList = { folder } });
    }

    /// <summary>The backend accepts at most this many paths to keep.</summary>
    public const int MaximumReportStorageKeepPaths = 500;

    /// <summary>
    /// Saved analyses the app holds: the selected recording, the open test day's recordings
    /// and every analysis a Progressive Tuning draft (app-owned, beside the archive) names.
    /// Each is sent as the same copy below this archive's reports folder, so a draft written
    /// before the Coach folder moved still protects its copy. The backend keeps them; this
    /// can only add protection.
    /// </summary>
    private IReadOnlyList<string> ReportStorageKeepPaths()
    {
        string reports;
        try
        {
            reports = Path.TrimEndingDirectorySeparator(Path.GetFullPath(Path.Combine(Settings.ArchiveRoot, "reports")));
        }
        catch (Exception ex) when (ex is ArgumentException or NotSupportedException or PathTooLongException)
        {
            return [];
        }
        var candidates = new List<string?> { SelectedRaceSession?.AnalysisPath };
        foreach (var run in CurrentTestDay?.Runs ?? [])
        {
            candidates.Add(run.AnalysisPath);
            candidates.Add(run.Recording?.AnalysisPath);
        }
        candidates.AddRange(ReportStorageCaches.AnalysisPathsIn(Path.Combine(Settings.CoachHome, "portable-settings", "tuning-drafts")));
        var result = new List<string>();
        foreach (var candidate in candidates)
        {
            if (string.IsNullOrWhiteSpace(candidate)
                || !candidate.EndsWith("analysis.json", StringComparison.OrdinalIgnoreCase)
                || ReportStorageCaches.RelativeBelowReports(candidate) is not { } relative) continue;
            string full;
            try { full = Path.GetFullPath(Path.Combine(reports, relative, "analysis.json")); }
            catch (Exception ex) when (ex is ArgumentException or NotSupportedException or PathTooLongException) { continue; }
            if (!full.StartsWith(reports + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)
                || result.Contains(full, StringComparer.OrdinalIgnoreCase)) continue;
            result.Add(full);
            if (result.Count == MaximumReportStorageKeepPaths) break;
        }
        return result;
    }

    /// <summary>
    /// Deletes ui-analysis-cache entries that name a moved copy, so no view opens a
    /// report that is no longer in reports. The entries are app caches, rebuilt from the
    /// recording or the kept report; nothing else in the cache folder is touched.
    /// </summary>
    private int DropUiCachesNaming(IReadOnlyCollection<string> movedDirectories)
    {
        var moved = movedDirectories.Select(ReportStorageCaches.NormalizeRelative).Where(item => item.Length > 0)
            .ToHashSet(StringComparer.OrdinalIgnoreCase);
        var cacheRoot = Path.Combine(Settings.ArchiveRoot, "ui-analysis-cache");
        if (moved.Count == 0 || !Directory.Exists(cacheRoot)) return 0;
        var dropped = 0;
        foreach (var file in Directory.EnumerateFiles(cacheRoot, "*.json", SearchOption.TopDirectoryOnly))
        {
            try
            {
                if (!ReportStorageCaches.NamesMovedCopy(File.ReadAllBytes(file), moved)) continue;
                EvictDeadCacheEntry(file);
                dropped++;
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
        }
        return dropped;
    }
}

/// <summary>Finds report paths inside an app cache entry, matched as the backend matches them.</summary>
public static class ReportStorageCaches
{
    private static readonly string[] ReportFiles = ["analysis.json", "report.md", "race-card.md"];

    /// <summary>
    /// Whether any string in the JSON names a file of a moved report directory. A path is
    /// compared by its part below the last "reports" folder, so a Coach folder copied to
    /// another location still matches. Invalid JSON names nothing.
    /// </summary>
    public static bool NamesMovedCopy(ReadOnlySpan<byte> json, IReadOnlySet<string> movedRelative)
    {
        if (movedRelative.Count == 0) return false;
        var reader = new Utf8JsonReader(json, new JsonReaderOptions { CommentHandling = JsonCommentHandling.Skip });
        try
        {
            while (reader.Read())
            {
                if (reader.TokenType != JsonTokenType.String) continue;
                var value = reader.GetString();
                if (string.IsNullOrWhiteSpace(value) || !ReportFiles.Any(name => value.EndsWith(name, StringComparison.OrdinalIgnoreCase))) continue;
                var relative = RelativeBelowReports(value);
                if (relative is not null && movedRelative.Contains(relative)) return true;
            }
        }
        catch (JsonException) { return false; }
        return false;
    }

    /// <summary>
    /// Every analysis.json path named anywhere in the JSON records of one app-owned folder
    /// (top level only). Unreadable or oversized records name nothing.
    /// </summary>
    public static IReadOnlyList<string> AnalysisPathsIn(string directory)
    {
        var found = new List<string>();
        if (!Directory.Exists(directory)) return found;
        try
        {
            foreach (var file in Directory.EnumerateFiles(directory, "*.json", SearchOption.TopDirectoryOnly).Take(2_000))
            {
                try
                {
                    if (new FileInfo(file).Length > 8 * 1024 * 1024) continue;
                    var reader = new Utf8JsonReader(File.ReadAllBytes(file));
                    while (reader.Read())
                    {
                        if (reader.TokenType == JsonTokenType.String && reader.GetString() is { } value
                            && value.EndsWith("analysis.json", StringComparison.OrdinalIgnoreCase))
                            found.Add(value);
                    }
                }
                catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException) { }
            }
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
        return found;
    }

    /// <summary>The report directory of a report file path, relative to reports; null when it is not below one.</summary>
    public static string? RelativeBelowReports(string reportFilePath)
    {
        var parts = reportFilePath.Split(['\\', '/'], StringSplitOptions.RemoveEmptyEntries);
        if (parts.Length < 3) return null;
        var reports = Array.FindLastIndex(parts, part => string.Equals(part, "reports", StringComparison.OrdinalIgnoreCase));
        if (reports < 0 || reports >= parts.Length - 2) return null;
        return string.Join('\\', parts[(reports + 1)..^1]);
    }

    public static string NormalizeRelative(string directory) =>
        string.Join('\\', directory.Split(['\\', '/'], StringSplitOptions.RemoveEmptyEntries));
}
