using System.Reflection;
using System.Text.Json;
using System.Text.Json.Serialization;
using iRacingCoach.Contracts;

namespace iRacingCoach.UI;

public sealed record ProgressiveTurnCatalogDocument(
    int SchemaVersion,
    IReadOnlyList<ProgressiveTurnMapRecord> Records);

public sealed record ProgressiveTurnMapRecord(
    string MapIdentity,
    string Track,
    string Layout,
    string SourceType,
    string SourceLabel,
    string? SourceUrl,
    string Confidence,
    bool Verified,
    IReadOnlyList<ProgressiveTurnAnnotation> Turns);

public sealed record ProgressiveTurnAnnotation(
    string CornerId,
    string Label,
    double StartPct,
    double ApexPct,
    double EndPct,
    string SourceType,
    string Confidence,
    bool IsOfficial,
    string? CorrectionNote = null,
    bool UserVerified = false)
{
    public bool WrapsStartFinish => EndPct < StartPct;
}

public sealed record ProgressiveTurnMapResolution(
    string MapIdentity,
    string SourceType,
    string SourceLabel,
    string? SourceUrl,
    string Confidence,
    bool Verified,
    bool UsesRecordedLoadZones,
    string StatusMessage,
    IReadOnlyList<ProgressiveTurnAnnotation> Turns)
{
    public bool HasTurns => Turns.Count > 0;
}

public sealed record ProgressiveTurnCorrectionRequest(
    string MapIdentity,
    string CornerId,
    string Label,
    double StartPct,
    double ApexPct,
    double EndPct);

public sealed record ProgressiveTuningFeedbackBatch(
    string CornerId,
    string RunPhase,
    IReadOnlyList<ProgressiveTuningFeedback> Feedback);

public static class ProgressiveTurnBounds
{
    private const double Epsilon = .000001;

    public static bool TryValidate(double entry, double apex, double exit, out string error)
    {
        if (!ProgressiveTurnCatalog.IsNormalizedPercent(entry)
            || !ProgressiveTurnCatalog.IsNormalizedPercent(apex)
            || !ProgressiveTurnCatalog.IsNormalizedPercent(exit))
        {
            error = "Entry, apex, and exit must stay within one lap.";
            return false;
        }

        var total = ForwardDistance(entry, exit);
        var toApex = ForwardDistance(entry, apex);
        if (total <= Epsilon)
        {
            error = "Entry and exit must be different points.";
            return false;
        }
        if (toApex <= Epsilon || toApex >= total - Epsilon)
        {
            error = "Set the points in driving order: entry, apex, then exit.";
            return false;
        }
        error = string.Empty;
        return true;
    }

    private static double ForwardDistance(double from, double to) => ((to - from) % 1 + 1) % 1;
}

/// <summary>
/// Resolves only exact track/configuration records. Telemetry segments remain
/// selectable as low-confidence load zones but are never promoted to official turns.
/// </summary>
public sealed class ProgressiveTurnCatalog
{
    private const string EmbeddedCatalogSuffix = "Data.turn-map-catalog.v1.json";
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        UnmappedMemberHandling = JsonUnmappedMemberHandling.Skip
    };

    private static readonly IReadOnlyDictionary<string, int> SourcePriority =
        new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase)
        {
            ["iracing-official"] = 500,
            ["nascar-official"] = 400,
            ["venue-official"] = 350,
            ["iracing-hud-capture"] = 325,
            ["verified-manual"] = 300,
            ["licensed-community"] = 200
        };

    public static ProgressiveTurnCatalog Default { get; } = LoadEmbedded();

    public ProgressiveTurnCatalog(ProgressiveTurnCatalogDocument document)
    {
        if (document.SchemaVersion != 1)
            throw new InvalidDataException($"Unsupported turn-map catalog schema {document.SchemaVersion}.");
        Document = document;
    }

    public ProgressiveTurnCatalogDocument Document { get; }

    public static ProgressiveTurnCatalog LoadJson(string json)
    {
        var document = JsonSerializer.Deserialize<ProgressiveTurnCatalogDocument>(json, JsonOptions)
            ?? throw new InvalidDataException("The turn-map catalog is empty.");
        return new ProgressiveTurnCatalog(document);
    }

    public ProgressiveTurnMapResolution Resolve(AnalysisWorkspace workspace)
    {
        var exactConfigurationKey = workspace.VectorGeometry?.TrackConfigurationKey?.Trim();
        var hasExactConfigurationKey = !string.IsNullOrWhiteSpace(exactConfigurationKey);
        var mapIdentity = hasExactConfigurationKey
            ? exactConfigurationKey!
            : BuildMapIdentity(workspace.Track, workspace.Layout);

        var record = ResolveExact(mapIdentity, workspace.Track, workspace.Layout, hasExactConfigurationKey);
        if (record is not null)
        {
            var turns = record.Turns
                .Where(IsValidTurn)
                .DistinctBy(turn => turn.CornerId, StringComparer.OrdinalIgnoreCase)
                .OrderBy(turn => Wrap(turn.StartPct))
                .ToArray();
            if (turns.Length > 0)
            {
                var verified = hasExactConfigurationKey && record.Verified && turns.All(turn => turn.IsOfficial ||
                    string.Equals(turn.SourceType, "verified-manual", StringComparison.OrdinalIgnoreCase));
                return new ProgressiveTurnMapResolution(
                    mapIdentity,
                    record.SourceType,
                    record.SourceLabel,
                    record.SourceUrl,
                    record.Confidence,
                    verified,
                    false,
                    verified
                        ? "Corner labels verified for this exact configuration."
                        : hasExactConfigurationKey
                            ? "Corner labels need local verification."
                            : "Exact track configuration is unavailable; verify every label and boundary locally.",
                    turns);
            }
        }

        var loadZones = workspace.Segments
            .Where(segment => double.IsFinite(segment.StartPercent) && double.IsFinite(segment.EndPercent))
            .OrderBy(segment => Wrap(segment.StartPercent))
            .Select(segment => new ProgressiveTurnAnnotation(
                $"load-zone-{segment.Number}",
                $"Load zone {segment.Number}",
                Wrap(segment.StartPercent),
                Midpoint(segment.StartPercent, segment.EndPercent, segment.WrapsStartFinish),
                Wrap(segment.EndPercent),
                "telemetry-derived",
                "low",
                false,
                "Corner name and boundaries have not been verified against an exact-configuration map."))
            .ToArray();

        return new ProgressiveTurnMapResolution(
            mapIdentity,
            "telemetry-derived",
            "Recorded telemetry",
            null,
            "low",
            false,
            loadZones.Length > 0,
            loadZones.Length > 0
                ? "Official corner labels are unavailable. Recorded load zones are shown for feedback."
                : "No verified corners or recorded load zones are available for this configuration.",
            loadZones);
    }

    public ProgressiveTurnMapRecord? ResolveExact(string mapIdentity, string track, string layout, bool requireMapIdentity = false)
    {
        var normalizedIdentity = Normalize(mapIdentity);
        var normalizedTrack = Normalize(track);
        var normalizedLayout = Normalize(layout);
        return Document.Records
            .Where(record => requireMapIdentity
                ? !string.IsNullOrWhiteSpace(record.MapIdentity) && Normalize(record.MapIdentity) == normalizedIdentity
                : (!string.IsNullOrWhiteSpace(record.MapIdentity) && Normalize(record.MapIdentity) == normalizedIdentity) ||
                  (Normalize(record.Track) == normalizedTrack && Normalize(record.Layout) == normalizedLayout))
            .OrderByDescending(record => record.Verified)
            .ThenByDescending(record => SourcePriority.GetValueOrDefault(record.SourceType))
            .FirstOrDefault();
    }

    public static string BuildMapIdentity(string track, string layout) => $"{Normalize(track)}::{Normalize(layout)}";

    private static ProgressiveTurnCatalog LoadEmbedded()
    {
        var assembly = typeof(ProgressiveTurnCatalog).Assembly;
        var resource = assembly.GetManifestResourceNames()
            .SingleOrDefault(name => name.EndsWith(EmbeddedCatalogSuffix, StringComparison.OrdinalIgnoreCase))
            ?? throw new InvalidDataException("The built-in turn-map catalog is missing.");
        using var stream = assembly.GetManifestResourceStream(resource)
            ?? throw new InvalidDataException("The built-in turn-map catalog could not be opened.");
        using var reader = new StreamReader(stream);
        return LoadJson(reader.ReadToEnd());
    }

    private static bool IsValidTurn(ProgressiveTurnAnnotation turn) =>
        !string.IsNullOrWhiteSpace(turn.CornerId) &&
        !string.IsNullOrWhiteSpace(turn.Label) &&
        IsNormalizedPercent(turn.StartPct) && IsNormalizedPercent(turn.ApexPct) && IsNormalizedPercent(turn.EndPct);

    public static bool IsNormalizedPercent(double value) => double.IsFinite(value) && value is >= 0 and < 1;
    private static double Wrap(double value) => ((value % 1) + 1) % 1;
    private static double Midpoint(double start, double end, bool wraps)
    {
        start = Wrap(start);
        end = Wrap(end);
        if (wraps || end < start) end += 1;
        return Wrap((start + end) / 2);
    }
    private static string Normalize(string? value) => string.Concat((value ?? string.Empty)
        .Trim()
        .ToLowerInvariant()
        .Where(char.IsLetterOrDigit));
}
