using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

internal sealed record CoachEvidenceWindow(
    [property: JsonPropertyName("kind")] string Kind,
    [property: JsonPropertyName("start")] double Start,
    [property: JsonPropertyName("end")] double End);

internal sealed record CoachNumericSeries(
    [property: JsonPropertyName("name")] string Name,
    [property: JsonPropertyName("unit")] string Unit,
    [property: JsonPropertyName("values")] IReadOnlyList<double> Values,
    [property: JsonPropertyName("window")] CoachEvidenceWindow Window);

internal sealed record CoachEvidenceClaim(
    [property: JsonPropertyName("kind")] string Kind,
    [property: JsonPropertyName("text")] string Text,
    [property: JsonPropertyName("evidence_id")] string EvidenceId,
    [property: JsonPropertyName("evidence_class")] string EvidenceClass);

internal sealed record CoachPacketSection(
    [property: JsonPropertyName("subject")] string Subject,
    [property: JsonPropertyName("available")] bool Available,
    [property: JsonPropertyName("claim")] CoachEvidenceClaim Claim,
    [property: JsonPropertyName("series")] IReadOnlyList<CoachNumericSeries> Series);

internal sealed record RaceCoachPacket(
    [property: JsonPropertyName("version")] int Version,
    [property: JsonPropertyName("packet_id")] string PacketId,
    [property: JsonPropertyName("supported_subjects")] IReadOnlyList<string> SupportedSubjects,
    [property: JsonPropertyName("sections")] IReadOnlyList<CoachPacketSection> Sections)
{
    [JsonIgnore]
    public bool HasNumericEvidence => SupportedSubjects.Count > 0;
}

internal static class RaceCoachPacketBuilder
{
    private static readonly JsonSerializerOptions CanonicalJson = new(JsonSerializerDefaults.Web);
    private static readonly string[] Subjects =
    [
        "lap_series", "run_series", "corner_window", "plan", "tire", "setup", "progress"
    ];

    public static RaceCoachPacket Build(AnalysisWorkspace analysis)
    {
        ArgumentNullException.ThrowIfNull(analysis);
        var sections = new Dictionary<string, CoachPacketSection>(StringComparer.Ordinal)
        {
            ["lap_series"] = LapSection(analysis),
            ["run_series"] = RunSection(analysis),
            ["corner_window"] = Unavailable(analysis, "corner_window", "No bounded corner-window numeric series was selected."),
            ["plan"] = PlanSection(analysis),
            ["tire"] = TireSection(analysis),
            ["setup"] = Unavailable(analysis, "setup", "No numeric setup values were included in this analysis."),
            ["progress"] = ProgressSection(analysis)
        };
        var ordered = Subjects.Select(subject => sections[subject]).ToArray();
        var supported = ordered.Where(section => section.Available).Select(section => section.Subject).ToArray();
        var identityMaterial = ordered.Select(section => new
        {
            subject = section.Subject,
            available = section.Available,
            evidence_id = section.Claim.EvidenceId,
            series = section.Series
        }).ToArray();
        var digest = SHA256.HashData(Encoding.UTF8.GetBytes(JsonSerializer.Serialize(
            new { version = 1, sections = identityMaterial }, CanonicalJson)));
        return new RaceCoachPacket(1, $"pk-{Convert.ToHexString(digest)[..24].ToLowerInvariant()}", supported, ordered);
    }

    private static CoachPacketSection LapSection(AnalysisWorkspace analysis)
    {
        var laps = analysis.Laps
            .Where(lap => lap.IsComparable() && Finite(lap.LapTimeSeconds))
            .OrderBy(lap => lap.Lap)
            .ToArray();
        if (laps.Length == 0)
            return Unavailable(analysis, "lap_series", "No complete, unconfounded lap times were recorded.");
        var series = new[]
        {
            Series("lap_time", "s", laps.Select(lap => lap.LapTimeSeconds!.Value), "lap", laps[0].Lap, laps[^1].Lap)
        };
        return Supported(analysis, "lap_series", "Complete unconfounded lap times from deterministic analysis.", "measured", series);
    }

    private static CoachPacketSection RunSection(AnalysisWorkspace analysis)
    {
        var runs = analysis.Runs.Where(run => run.ComparisonEligible).OrderBy(run => run.Number).ToArray();
        if (runs.Length == 0)
            return Unavailable(analysis, "run_series", "No comparison-eligible run was recorded.");
        var start = runs[0].Number;
        var end = runs[^1].Number;
        var series = new List<CoachNumericSeries>();
        Add(series, "green_laps", "count", runs.Select(run => (double?)run.GreenLaps), "run", start, end);
        Add(series, "pace_slope", "s/lap", runs.Select(run => run.PaceSlopeSecondsPerLap), "run", start, end);
        Add(series, "early_average_lap", "s", runs.Select(run => run.EarlyAverageLapSeconds), "run", start, end);
        Add(series, "late_average_lap", "s", runs.Select(run => run.LateAverageLapSeconds), "run", start, end);
        Add(series, "early_to_late_delta", "s", runs.Select(run => run.EarlyToLateDeltaSeconds), "run", start, end);
        return series.Count == 0
            ? Unavailable(analysis, "run_series", "Eligible runs carried no finite numeric series.")
            : Supported(analysis, "run_series", "Comparison-eligible run measurements from deterministic analysis.", "derived", series);
    }

    private static CoachPacketSection PlanSection(AnalysisWorkspace analysis)
    {
        if (analysis.StrategyStatus.Contains("insufficient", StringComparison.OrdinalIgnoreCase) ||
            analysis.StrategyStatus.Contains("unavailable", StringComparison.OrdinalIgnoreCase))
        {
            return Unavailable(analysis, "plan", analysis.StrategyStatus);
        }
        var strategy = analysis.Strategy;
        var end = Math.Max(analysis.RecordedLaps, analysis.ScheduledLaps);
        var series = new List<CoachNumericSeries>();
        AddScalar(series, "green_fuel_rate", "gal/lap", strategy.GreenFuelGallonsPerLap, "session", 0, end);
        AddScalar(series, "caution_fuel_rate", "gal/lap", strategy.CautionFuelGallonsPerLap, "session", 0, end);
        AddScalar(series, "all_green_range", "lap", strategy.AllGreenRangeLaps, "session", 0, end);
        AddScalar(series, "minimum_stops", "count", strategy.MinimumStopsAllGreen, "session", 0, end);
        AddScalar(series, "reserve_fuel", "gal", strategy.ReserveFuelGallons, "session", 0, end);
        AddScalar(series, "reserve_green_laps", "lap", strategy.ReserveGreenLaps, "session", 0, end);
        if (strategy.EqualStintPitTargets.Count > 0)
            series.Add(Series("equal_stint_pit_targets", "lap", strategy.EqualStintPitTargets.Select(value => (double)value), "session", 0, end));
        return series.Count == 0
            ? Unavailable(analysis, "plan", "The deterministic strategy did not produce finite plan values.")
            : Supported(analysis, "plan", "Fuel and stop values from the deterministic strategy.", "derived", series);
    }

    private static CoachPacketSection TireSection(AnalysisWorkspace analysis)
    {
        var tire = analysis.TirePrediction;
        if (tire is null || !string.Equals(tire.Status, "available", StringComparison.OrdinalIgnoreCase))
            return Unavailable(analysis, "tire", tire?.Reason ?? "No calibrated tire prediction was available.");
        var end = Math.Max(analysis.RecordedLaps, analysis.ScheduledLaps);
        var series = new List<CoachNumericSeries>();
        AddScalar(series, "laps_remaining", "lap", tire.LapsRemaining, "session", 0, end);
        AddScalar(series, "pace_cost", "s", tire.PaceCostSeconds, "session", 0, end);
        AddScalar(series, "pace_cost_low", "s", tire.PaceCostLowSeconds, "session", 0, end);
        AddScalar(series, "pace_cost_high", "s", tire.PaceCostHighSeconds, "session", 0, end);
        AddScalar(series, "matched_observations", "count", tire.EffectiveMatchedObservations, "session", 0, end);
        return series.Count == 0
            ? Unavailable(analysis, "tire", "The calibrated tire result carried no finite numeric values.")
            : Supported(analysis, "tire", "Calibrated tire-model output gated by deterministic evidence.", tire.EvidenceClass ?? "inferred", series);
    }

    private static CoachPacketSection ProgressSection(AnalysisWorkspace analysis)
    {
        var scores = analysis.Grades.Where(grade => grade.Available && Finite(grade.Score)).Select(grade => grade.Score!.Value).ToArray();
        return scores.Length == 0
            ? Unavailable(analysis, "progress", "No numeric deterministic grades were available.")
            : Supported(analysis, "progress", "Deterministic execution grades for this race.", "derived",
                [Series("grade_score", "percent", scores, "session", 0, Math.Max(analysis.RecordedLaps, 1))]);
    }

    private static CoachPacketSection Supported(
        AnalysisWorkspace analysis,
        string subject,
        string text,
        string evidenceClass,
        IReadOnlyList<CoachNumericSeries> series)
    {
        if (series.Count == 0 || series.Any(item => item.Values.Count == 0))
            throw new InvalidDataException($"Coach section {subject} cannot claim support without numbers.");
        var evidenceId = EvidenceId(analysis.AnalysisId, subject, series);
        return new CoachPacketSection(subject, true, new("fact", text, evidenceId, evidenceClass), series);
    }

    private static CoachPacketSection Unavailable(AnalysisWorkspace analysis, string subject, string reason)
    {
        var evidenceId = EvidenceId(analysis.AnalysisId, subject, reason);
        return new CoachPacketSection(subject, false, new("unavailable", reason, evidenceId, "unavailable"), []);
    }

    private static CoachNumericSeries Series(
        string name,
        string unit,
        IEnumerable<double> values,
        string windowKind,
        double start,
        double end)
    {
        var finite = values.Where(double.IsFinite).ToArray();
        if (finite.Length == 0) throw new InvalidDataException($"Coach series {name} has no finite values.");
        if (string.IsNullOrWhiteSpace(unit)) throw new InvalidDataException($"Coach series {name} has no unit.");
        if (!double.IsFinite(start) || !double.IsFinite(end) || end < start)
            throw new InvalidDataException($"Coach series {name} has an invalid evidence window.");
        return new CoachNumericSeries(name, unit, finite, new(windowKind, start, end));
    }

    private static void Add(
        ICollection<CoachNumericSeries> destination,
        string name,
        string unit,
        IEnumerable<double?> values,
        string windowKind,
        double start,
        double end)
    {
        var finite = values.Where(Finite).Select(value => value!.Value).ToArray();
        if (finite.Length > 0) destination.Add(Series(name, unit, finite, windowKind, start, end));
    }

    private static void AddScalar(
        ICollection<CoachNumericSeries> destination,
        string name,
        string unit,
        double? value,
        string windowKind,
        double start,
        double end)
    {
        if (Finite(value)) destination.Add(Series(name, unit, [value!.Value], windowKind, start, end));
    }

    private static void AddScalar(
        ICollection<CoachNumericSeries> destination,
        string name,
        string unit,
        int? value,
        string windowKind,
        double start,
        double end) =>
        AddScalar(destination, name, unit, value.HasValue ? (double)value.Value : null, windowKind, start, end);

    private static bool Finite(double? value) => value.HasValue && double.IsFinite(value.Value);

    private static string EvidenceId(string analysisId, string subject, object material)
    {
        var json = JsonSerializer.Serialize(new { analysis_id = analysisId, subject, material }, CanonicalJson);
        var digest = SHA256.HashData(Encoding.UTF8.GetBytes(json));
        return $"ev-{Convert.ToHexString(digest)[..24].ToLowerInvariant()}";
    }
}
