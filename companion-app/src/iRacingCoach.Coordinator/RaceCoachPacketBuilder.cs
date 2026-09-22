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
    private const int MaximumLapSamples = 512;
    private const int MaximumComparisons = 8;
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
            ["corner_window"] = CornerSection(analysis),
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
        if (UsableInvestigation(analysis) is { } investigation)
        {
            var observed = BoundLaps(investigation.Laps.Where(lap => Finite(lap.LapTimeSeconds)).OrderBy(lap => lap.Lap).ToArray());
            if (observed.Count > 0)
            {
                var start = observed[0].Lap;
                var end = observed[^1].Lap;
                var numeric = new List<CoachNumericSeries>
                {
                    Series("observed_lap_number", "lap", observed.Select(lap => (double)lap.Lap), "lap", start, end),
                    Series("observed_lap_time", "s", observed.Select(lap => lap.LapTimeSeconds!.Value), "lap", start, end),
                    Series("reference_screening_passed", "boolean:1=passed;0=excluded", observed.Select(lap => lap.Eligible ? 1d : 0d), "lap", start, end),
                    Series("green_pace_plot_included", "boolean:1=included;0=excluded", observed.Select(lap => lap.PlotPace ? 1d : 0d), "lap", start, end)
                };
                foreach (var comparison in InvestigationComparisons(investigation))
                {
                    AddCohort(numeric, comparison.Id + "/baseline", comparison.Baseline);
                    AddCohort(numeric, comparison.Id + "/compared", comparison.Compared);
                    var window = ComparisonWindow(comparison);
                    if (window is null) continue;
                    AddScalar(numeric, comparison.Id + "/all_laps_passed_screening", "boolean:1=passed;0=not_all_passed",
                        comparison.Eligibility == "eligible" ? 1d : 0d, "lap", window.Value.Start, window.Value.End);
                    AddScalar(numeric, comparison.Id + "/median_lap_delta", "s", comparison.DeltaSeconds, "lap", window.Value.Start, window.Value.End);
                    foreach (var metric in comparison.Metrics.Take(24))
                        AddMetric(numeric, comparison.Id + "/" + metric.Key, metric, "lap", window.Value.Start, window.Value.End);
                }
                return Supported(analysis, "lap_series",
                    "Recorded lap times paired by index with exact lap numbers and screening flags. Excluded, caution and final observed laps remain observations, not clean coaching references. Cohort deltas are comparisons, not proven causes; initial/final sample bounds are in the attached investigation context.",
                    "derived", numeric);
            }
        }
        var laps = analysis.Laps
            .Where(lap => lap.IsComparable() && Finite(lap.LapTimeSeconds))
            .OrderBy(lap => lap.Lap)
            .ToArray();
        if (laps.Length == 0)
            return Unavailable(analysis, "lap_series", "No complete, unconfounded lap times were recorded.");
        var series = new[]
        {
            Series("lap_number", "lap", laps.Select(lap => (double)lap.Lap), "lap", laps[0].Lap, laps[^1].Lap),
            Series("lap_time", "s", laps.Select(lap => lap.LapTimeSeconds!.Value), "lap", laps[0].Lap, laps[^1].Lap)
        };
        return Supported(analysis, "lap_series", "Complete unconfounded lap times from deterministic analysis.", "measured", series);
    }

    private static CoachPacketSection CornerSection(AnalysisWorkspace analysis)
    {
        var investigation = UsableInvestigation(analysis);
        if (investigation is null)
            return Unavailable(analysis, "corner_window", "No bounded corner-window numeric series was selected.");
        var series = new List<CoachNumericSeries>();
        foreach (var comparison in InvestigationComparisons(investigation))
        {
            var addedCohorts = false;
            foreach (var corner in comparison.Corners.Take(12))
            {
                if (corner.StartPct is not >= 0 or > 1 || corner.EndPct is not >= 0 or > 1 || corner.EndPct <= corner.StartPct)
                    continue;
                var start = corner.StartPct!.Value;
                var end = corner.EndPct!.Value;
                var prefix = comparison.Id + "/" + corner.Id;
                var before = series.Count;
                AddScalar(series, prefix + "/area_time_delta", "s", corner.DeltaSeconds, "corner", start, end);
                foreach (var metric in corner.Metrics.Take(16))
                    AddMetric(series, prefix + "/" + metric.Key, metric, "corner", start, end);
                if (series.Count == before) continue;
                AddScalar(series, prefix + "/all_laps_passed_screening", "boolean:1=passed;0=not_all_passed",
                    comparison.Eligibility == "eligible" ? 1d : 0d, "corner", start, end);
                if (addedCohorts) continue;
                AddCohort(series, comparison.Id + "/baseline", comparison.Baseline);
                AddCohort(series, comparison.Id + "/compared", comparison.Compared);
                addedCohorts = true;
            }
        }
        return series.Count == 0
            ? Unavailable(analysis, "corner_window", "No supported bounded track area has finite comparison measurements.")
            : Supported(analysis, "corner_window",
                "Backend track-area comparisons with exact cohort lap identifiers. Corner window bounds are fractions of one lap, not named official turns or telemetry targets. Observed comparisons retain their screening limits; differences do not establish a cause or a target input.", "derived", series);
    }

    // This is the only investigation context intended for a Coach request. Never
    // serialize RaceInvestigation.Source: it deliberately retains unknown fields.
    public static object? InvestigationContext(AnalysisWorkspace analysis)
    {
        var investigation = UsableInvestigation(analysis);
        if (investigation is null) return null;
        var laps = BoundLaps(investigation.Laps.OrderBy(lap => lap.Lap).ToArray());
        var comparisons = InvestigationComparisons(investigation);
        return new
        {
            schema_version = investigation.SchemaVersion,
            investigation.Status,
            investigation.Summary,
            default_comparison_id = investigation.DefaultComparisonId,
            coverage = new { recorded_lap_count = investigation.Laps.Count, included_lap_count = laps.Count,
                complete_lap_series = laps.Count == investigation.Laps.Count,
                sample_policy = "All laps up to 512; longer recordings retain the first 256 and last 256. Every numeric sample has an exact lap identifier.",
                comparison_count = investigation.Comparisons.Count, included_comparison_count = comparisons.Count },
            comparisons,
            laps,
            timeline = investigation.Timeline.Take(128).ToArray(),
            track_evolution = investigation.TrackEvolution is { } track ? new
            {
                track.Status, track.Provenance, track.StartingUsage, track.Summary,
                track.UsageUnit, track.UsageSource, points = BoundTrackPoints(track.Points), track.Limitations,
                coverage = new { recorded_point_count = track.Points.Count, included_point_count = Math.Min(track.Points.Count, MaximumLapSamples) }
            } : null,
            investigation.Limitations,
            interpretation = "Starting track usage is metadata, not measured evolution. Temperature alone cannot establish or exclude changing rubber/grip. Screening eligibility is not causal proof. Driving trials are conditional experiments, never exact target telemetry."
        };
    }

    private static RaceInvestigation? UsableInvestigation(AnalysisWorkspace analysis) =>
        analysis.Investigation is { SchemaVersion: 1, UnavailableReason: null } investigation ? investigation : null;

    private static IReadOnlyList<RaceInvestigationComparison> InvestigationComparisons(RaceInvestigation investigation) =>
        investigation.Comparisons.OrderByDescending(comparison => comparison.Id == investigation.DefaultComparisonId).Take(MaximumComparisons).ToArray();

    private static IReadOnlyList<RaceInvestigationLap> BoundLaps(IReadOnlyList<RaceInvestigationLap> laps) =>
        laps.Count <= MaximumLapSamples ? laps : laps.Take(MaximumLapSamples / 2).Concat(laps.TakeLast(MaximumLapSamples / 2)).ToArray();

    private static IReadOnlyList<RaceTrackEvolutionPoint> BoundTrackPoints(IReadOnlyList<RaceTrackEvolutionPoint> points) =>
        points.Count <= MaximumLapSamples ? points : points.Take(MaximumLapSamples / 2).Concat(points.TakeLast(MaximumLapSamples / 2)).ToArray();

    private static (int Start, int End)? ComparisonWindow(RaceInvestigationComparison comparison)
    {
        var laps = comparison.Baseline.LapNumbers.Concat(comparison.Compared.LapNumbers).ToArray();
        return laps.Length == 0 ? null : (laps.Min(), laps.Max());
    }

    private static void AddCohort(ICollection<CoachNumericSeries> series, string prefix, RaceInvestigationCohort cohort)
    {
        if (cohort.LapNumbers.Count == 0) return;
        var start = cohort.LapNumbers.Min();
        var end = cohort.LapNumbers.Max();
        var laps = cohort.LapNumbers.Take(MaximumLapSamples).ToArray();
        series.Add(Series(prefix + "/lap_number", "lap", laps.Select(lap => (double)lap), "lap", start, end));
        series.Add(Series(prefix + "/reference_screening_passed", "boolean:1=passed;0=excluded", laps.Select(lap => cohort.EligibleLapNumbers.Contains(lap) ? 1d : 0d), "lap", start, end));
        AddScalar(series, prefix + "/median_lap_time", "s", cohort.MedianLapSeconds, "lap", start, end);
        AddScalar(series, prefix + "/green_age_start", "green lap equivalents", cohort.GreenAgeStart, "lap", start, end);
        AddScalar(series, prefix + "/green_age_end", "green lap equivalents", cohort.GreenAgeEnd, "lap", start, end);
    }

    private static void AddMetric(ICollection<CoachNumericSeries> series, string prefix, RaceInvestigationMetric metric,
        string kind, double start, double end)
    {
        if (string.IsNullOrWhiteSpace(metric.Unit)) return;
        AddScalar(series, prefix + "/baseline", metric.Unit, metric.Baseline, kind, start, end);
        AddScalar(series, prefix + "/compared", metric.Unit, metric.Compared, kind, start, end);
        AddScalar(series, prefix + "/delta", metric.Unit, metric.Delta, kind, start, end);
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
