using System.Globalization;
using System.Text.Json;
using iRacingCoach.Contracts;

namespace iRacingCoach.UI;

/// <summary>
/// Formatting and pixel scaling for the race investigation. Every measured value
/// comes from the backend projection; nothing here derives evidence.
/// </summary>
public static class InvestigationPresentation
{
    public static readonly string[] RunColors = ["var(--accent)", "var(--warning)", "var(--info)", "var(--telemetry-steering)", "var(--telemetry-slower)", "var(--telemetry-speed)"];
    public static readonly string[] RunDashes = ["", "7 4", "2 3", "10 3 2 3", "4 4", "1 2"];

    public static string RunColor(int? run) => run is > 0 ? RunColors[(run.Value - 1) % RunColors.Length] : "var(--text-muted)";
    public static string RunDash(int? run) => run is > 0 ? RunDashes[(run.Value - 1) % RunDashes.Length] : "";

    public static string N(double value) => value.ToString("0.##", CultureInfo.InvariantCulture);
    public static string Seconds(double? value, int digits = 3) => value is { } number && double.IsFinite(number)
        ? number.ToString("F" + digits, CultureInfo.CurrentCulture) : "—";
    public static string Signed(double? value, int digits = 3) => value is { } number && double.IsFinite(number)
        ? (number > 0 ? "+" : number < 0 ? "−" : "") + Math.Abs(number).ToString("F" + digits, CultureInfo.CurrentCulture) : "—";
    public static string Number(double? value, int digits = 1) => value is { } number && double.IsFinite(number)
        ? (number < 0 ? "−" : "") + Math.Abs(number).ToString("F" + digits, CultureInfo.CurrentCulture) : "—";
    public static string Percent(double fraction) => (fraction * 100).ToString("0.0", CultureInfo.CurrentCulture);
    public static string Exposure(double? value) => value is { } number ? $"{number.ToString("0.0", CultureInfo.CurrentCulture)} green" : "exposure unknown";

    /// <summary>True only when the backend reports a usable, finite median pace change.</summary>
    public static bool HasPaceResult(RaceInvestigationComparison comparison) =>
        comparison.PaceComparable && comparison.DeltaSeconds is { } delta && double.IsFinite(delta);

    /// <summary>Slower is a loss, faster a gain; a withheld or missing result is neutral, never a gain.</summary>
    public static string PaceTone(RaceInvestigationComparison comparison) =>
        !HasPaceResult(comparison) ? "neutral" : comparison.DeltaSeconds > 0 ? "loss" : "gain";

    public static string PaceResult(RaceInvestigationComparison comparison) =>
        HasPaceResult(comparison) ? $"{Signed(comparison.DeltaSeconds)} s/lap" : "No pace result";

    public static string LapTime(double? seconds) => seconds is not { } value || !double.IsFinite(value) ? "—"
        : value >= 60 ? TimeSpan.FromSeconds(value).ToString(@"m\:ss\.fff", CultureInfo.InvariantCulture) : value.ToString("0.000", CultureInfo.CurrentCulture);

    /// <summary>"35, 36, 37" for short windows and "116–120" for contiguous long ones; never ambiguous.</summary>
    public static string Laps(IReadOnlyList<int> laps)
    {
        if (laps.Count == 0) return "no laps";
        var ordered = laps.Order().ToArray();
        var contiguous = ordered.Zip(ordered.Skip(1), (a, b) => b == a + 1).All(step => step);
        return contiguous && ordered.Length > 3 ? $"{ordered[0]}–{ordered[^1]}" : string.Join(", ", ordered);
    }

    public static string CohortIdentity(RaceInvestigationCohort cohort)
    {
        var run = cohort.RunNumber is { } number ? $"Run {number}" : "Laps";
        return $"{run} · {cohort.LapCount} lap{(cohort.LapCount == 1 ? "" : "s")} · {Laps(cohort.LapNumbers)}";
    }

    public static string CohortExposure(RaceInvestigationCohort cohort) => cohort.GreenAgeStart is { } start && cohort.GreenAgeEnd is { } end
        ? $"{start.ToString("0.0", CultureInfo.CurrentCulture)}–{end.ToString("0.0", CultureInfo.CurrentCulture)} green"
        : "exposure unknown";

    public static string PassIdentity(RacePass pass) =>
        $"{(pass.RunNumber is { } run ? $"R{run} · " : "")}Lap {pass.Lap} · {Exposure(pass.GreenExposure)}";

    public static string PassRole(string role) => role switch
    {
        "fastest" => "Fastest",
        "slowest" => "Slowest",
        "baseline_median" => "Earlier group median",
        "compared_median" => "Later group median",
        "before" => "Before",
        "after" => "After",
        _ => role
    };

    public static string Reason(string code) => code switch
    {
        "close_traffic" => "close traffic",
        "incident_points" => "incident",
        "eligibility_unknown" => "screening unknown",
        "lap_timing_unavailable" => "timing unavailable",
        "not_racing_state" => "not racing",
        "recorded_repair_disruption" => "repair",
        _ => code.Replace('_', ' ')
    };

    public static string Reasons(IReadOnlyList<string> codes) => string.Join(", ", codes.Select(Reason).Distinct());

    /// <summary>
    /// How the backend says a test can be checked: the run length without a pit stop its
    /// window needs, against the test-day run length it assumed. Empty when not reported.
    /// </summary>
    public static string Reach(RaceTrialSpec spec) => spec.Reach switch
    {
        null => "",
        { TestDayReachable: true } reach => $"Can be checked in a {reach.TestDayRunLaps}-lap test run (needs about {reach.RunLapsNeeded} green laps without a pit stop).",
        { } reach => $"Needs a run of about {reach.RunLapsNeeded} green laps without a pit stop; a {reach.TestDayRunLaps}-lap test run cannot check it."
    };

    public static string Verdict(string verdict) => verdict switch
    {
        "improved" => "Improved",
        "worse" => "Worse",
        "not_established" => "Not established",
        "not_comparable" => "Not comparable",
        _ => verdict.Replace('_', ' ')
    };

    public static string VerdictTone(string verdict) => verdict switch
    {
        "improved" => "good",
        "worse" => "bad",
        _ => "neutral"
    };

    public static string HypothesisStatus(string status) => status switch
    {
        "supported" => "Observed change",
        "possible" => "Possible",
        "not_supported" => "Not supported",
        _ => "Unresolved"
    };

    public static string DateLabel(string? iso)
    {
        if (string.IsNullOrWhiteSpace(iso) || !DateTimeOffset.TryParse(iso, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out var value)) return "";
        return value.ToLocalTime().ToString("MMM d, h:mm tt", CultureInfo.CurrentCulture);
    }

    private static readonly JsonSerializerOptions RowOptions = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };

    /// <summary>Backend pass rows for the cursor module; values are passed through unchanged.</summary>
    public static string RowsAttribute(RacePassPair? pair) => pair is null ? "[]" : JsonSerializer.Serialize(pair.Rows, RowOptions);

    /// <summary>Recorded layout points as [x, y, lap fraction] for map inspection.</summary>
    public static string MapPointsAttribute(IEnumerable<AnalysisVectorPoint> points) =>
        JsonSerializer.Serialize(points.Where(point => point.LapPercent is not null)
            .Select(point => new[] { Math.Round(point.X, 5), Math.Round(point.Y, 5), Math.Round(point.LapPercent!.Value, 5) }));

    /// <summary>Evenly spaced round ticks inside [min, max]; axis origin is never implied to be data.</summary>
    public static IReadOnlyList<double> Ticks(double min, double max, int count)
    {
        if (!double.IsFinite(min) || !double.IsFinite(max) || max <= min) return [min];
        var raw = (max - min) / Math.Max(1, count);
        var magnitude = Math.Pow(10, Math.Floor(Math.Log10(raw)));
        var step = new[] { 1, 2, 2.5, 5, 10 }.Select(factor => factor * magnitude).First(candidate => candidate >= raw);
        var first = Math.Ceiling(min / step) * step;
        var result = new List<double>();
        for (var value = first; value <= max + step * 1e-6; value += step) result.Add(Math.Round(value, 6));
        return result;
    }
}

/// <summary>Linear pixel mapping for one chart plot area.</summary>
public readonly record struct PlotScale(double Left, double Top, double Width, double Height, double XMin, double XMax, double YMin, double YMax)
{
    public double X(double value) => Left + (value - XMin) / Math.Max(1e-9, XMax - XMin) * Width;
    public double Y(double value) => Top + (YMax - value) / Math.Max(1e-9, YMax - YMin) * Height;
    public double Right => Left + Width;
    public double Bottom => Top + Height;
}
