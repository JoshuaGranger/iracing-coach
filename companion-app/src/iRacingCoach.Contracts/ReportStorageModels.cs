using System.Text.Json;

namespace iRacingCoach.Contracts;

/// <summary>
/// The backend's plan for older copies of saved analyses (reclaim_report_storage,
/// contract report-storage-reclaim-v1, mode plan). The backend decides which copies
/// may move and which stay; the app only shows the plan and sends back the driver's
/// confirmation. <see cref="Source"/> keeps the whole response so unknown optional
/// fields survive.
/// </summary>
public sealed record ReportStoragePlan(
    string Status,
    string PlanId,
    string ReportsRoot,
    string RecoveryRoot,
    ReportStorageRemoval Remove,
    ReportStorageKept Kept,
    ReportStorageRecovery Recovery,
    JsonElement Source)
{
    public const string Contract = "report-storage-reclaim-v1";
    public const int CurrentSchemaVersion = 1;

    /// <summary>Only a ready plan listing at least one copy offers removal.</summary>
    public bool HasReclaimable =>
        string.Equals(Status, "ready", StringComparison.Ordinal) && Remove.Count > 0 && Remove.Directories.Count == Remove.Count;
}

/// <summary>What a confirmation would move: every copy, with totals and its date range.</summary>
public sealed record ReportStorageRemoval(
    int Count,
    long Bytes,
    int DuplicateCount,
    long DuplicateBytes,
    int SupersededCount,
    long SupersededBytes,
    DateTimeOffset? OldestUtc,
    DateTimeOffset? NewestUtc,
    IReadOnlyList<ReportStorageCopy> Directories);

/// <summary>One report directory, relative to the reports folder, and the copy that stays in its place.</summary>
public sealed record ReportStorageCopy(string Directory, long Bytes, string Kind, string AnalysisId, string? KeptCopy);

/// <summary>Report directories that stay, counted by the backend's reason.</summary>
public sealed record ReportStorageKept(int Count, long Bytes, IReadOnlyDictionary<string, int> ByReason);

/// <summary>The removed-reports folder: what earlier confirmations moved and the driver has not yet deleted.</summary>
public sealed record ReportStorageRecovery(string Path, long Bytes, int BatchCount);

/// <summary>The outcome of one confirmed removal (mode execute): each listed copy moved whole or was skipped.</summary>
public sealed record ReportStorageResult(
    string Status,
    string PlanId,
    string ConfirmedPlanId,
    string? BatchDirectory,
    int MovedCount,
    long MovedBytes,
    IReadOnlyList<ReportStorageCopy> Moved,
    IReadOnlyList<ReportStorageSkip> Skipped,
    ReportStorageRecovery Recovery,
    JsonElement Source);

/// <summary>A listed copy that stayed, and why (referenced, in use, changed, missing, ...).</summary>
public sealed record ReportStorageSkip(string Directory, string Reason);
