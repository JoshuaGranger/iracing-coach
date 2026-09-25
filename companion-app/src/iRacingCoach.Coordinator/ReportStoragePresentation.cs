using System.Globalization;
using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

/// <summary>
/// Words for the saved-analysis storage line and its review. Every number comes from the
/// backend's plan or result; nothing here decides what may be removed.
/// </summary>
public static class ReportStoragePresentation
{
    private const double Gibibyte = 1024d * 1024 * 1024;
    private const double Mebibyte = 1024d * 1024;

    /// <summary>Sizes as Windows Explorer shows them (1 GB = 1024³ bytes).</summary>
    public static string Size(long bytes) => bytes switch
    {
        >= (long)Gibibyte => string.Create(CultureInfo.InvariantCulture, $"{bytes / Gibibyte:0.0} GB"),
        >= (long)Mebibyte => string.Create(CultureInfo.InvariantCulture, $"{Math.Max(1, Math.Round(bytes / Mebibyte)):0} MB"),
        > 0 => "under 1 MB",
        _ => "0 MB"
    };

    public static string Copies(int count) => count == 1 ? "1 copy" : string.Create(CultureInfo.InvariantCulture, $"{count:N0} copies");

    /// <summary>"Older copies of saved analyses" value, e.g. "2.3 GB can be removed".</summary>
    public static string Reclaimable(ReportStoragePlan plan) => $"{Size(plan.Remove.Bytes)} can be removed";

    public static string RemoveHeadline(ReportStoragePlan plan) => $"{Copies(plan.Remove.Count)} · {Size(plan.Remove.Bytes)}";

    public static string KeepHeadline(ReportStoragePlan plan) => $"{Copies(plan.Kept.Count)} · {Size(plan.Kept.Bytes)}";

    /// <summary>When the listed copies were saved and why each can go.</summary>
    public static string RemoveDetail(ReportStoragePlan plan)
    {
        var parts = new List<string>();
        if (DateRange(plan.Remove.OldestUtc, plan.Remove.NewestUtc) is { } saved) parts.Add($"Saved {saved}");
        if (plan.Remove.SupersededCount > 0)
            parts.Add($"{plan.Remove.SupersededCount.ToString("N0", CultureInfo.InvariantCulture)} from an older analyzer version");
        if (plan.Remove.DuplicateCount > 0)
            parts.Add(plan.Remove.DuplicateCount == 1 ? "1 repeat of a kept analysis" : $"{plan.Remove.DuplicateCount.ToString("N0", CultureInfo.InvariantCulture)} repeats of a kept analysis");
        return string.Join(" · ", parts);
    }

    public const string KeepDetail =
        "Each recording's latest analysis, and every copy your race history, a saved test, a tuning record or an open test day uses.";

    public const string Untouched =
        "Recordings (IBT files), setups and everything outside saved analyses stay untouched. The copies move to removed-reports in the Coach data folder; nothing is deleted until you delete that folder.";

    public static string? DateRange(DateTimeOffset? oldest, DateTimeOffset? newest)
    {
        if (oldest is null || newest is null) return null;
        var first = oldest.Value.ToLocalTime();
        var last = newest.Value.ToLocalTime();
        var culture = CultureInfo.InvariantCulture;
        if (first.Date == last.Date) return first.ToString("d MMM yyyy", culture);
        return first.Year == last.Year
            ? $"{first.ToString("d MMM", culture)} – {last.ToString("d MMM yyyy", culture)}"
            : $"{first.ToString("d MMM yyyy", culture)} – {last.ToString("d MMM yyyy", culture)}";
    }

    public static string ResultHeadline(ReportStorageResult result) =>
        result.MovedCount == 0 ? "No copies were moved" : $"Moved {Copies(result.MovedCount)} ({Size(result.MovedBytes)})";

    public static string ResultDetail(ReportStorageResult result)
    {
        var parts = new List<string>();
        if (result.MovedCount > 0) parts.Add("They are in removed-reports. Delete that folder to free the space.");
        if (result.Skipped.Count > 0)
        {
            // "In use": a file inside is open, or the history, a saved test or the app now names it.
            var inUse = result.Skipped.Count(skip => skip.Reason is "in-use" or "referenced");
            var one = result.Skipped.Count == 1;
            var reason = inUse == result.Skipped.Count ? (one ? "it is in use" : "they are in use")
                : inUse == 0 ? (one ? "it changed since this review" : "they changed since this review")
                : "they are in use or changed since this review";
            parts.Add($"{Copies(result.Skipped.Count)} stayed because {reason}.");
        }
        return string.Join(" ", parts);
    }

    /// <summary>The quiet Settings line for copies already moved and not yet deleted.</summary>
    public static string? RecoveryLine(ReportStorageRecovery? recovery) =>
        recovery is { Bytes: > 0 } ? $"Removed copies: {Size(recovery.Bytes)} in removed-reports. Delete that folder to free the space." : null;
}
