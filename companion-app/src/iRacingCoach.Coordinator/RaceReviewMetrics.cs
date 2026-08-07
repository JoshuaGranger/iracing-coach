using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

public sealed record RaceReviewCornerSummary(
    string Label,
    string Early,
    string Late,
    int EarlyLapCount,
    int LateLapCount);

public static class RaceReviewMetrics
{
    public static IReadOnlyList<RaceReviewCornerSummary> BuildCornerSummaries(AnalysisWorkspace workspace)
    {
        if (workspace.Segments.Count == 0 || workspace.Traces.Count == 0) return [];
        var incidentLaps = (workspace.Damage.Incidents ?? [])
            .Where(incident => incident.Points > 0)
            .Select(incident => incident.Lap)
            .ToHashSet();
        var comparable = workspace.Traces
            .Where(trace => trace.IsComparable() && !incidentLaps.Contains(trace.Lap) && trace.Points.Count > 0)
            .OrderBy(trace => trace.Lap)
            .ToArray();
        if (comparable.Length < 2) return [];

        var phaseSize = Math.Max(1, (int)Math.Ceiling(comparable.Length / 3d));
        var early = comparable.Take(phaseSize).ToArray();
        var late = comparable.Skip(Math.Max(0, comparable.Length - phaseSize)).ToArray();
        return workspace.Segments.Select(zone => new RaceReviewCornerSummary(
            FriendlyZone(zone),
            ZoneSummary(early, zone),
            ZoneSummary(late, zone),
            early.Length,
            late.Length)).ToArray();
    }

    private static string FriendlyZone(TrackSegment zone) =>
        string.IsNullOrWhiteSpace(zone.Label) || zone.Label.StartsWith("Load zone", StringComparison.OrdinalIgnoreCase)
            ? $"Corner area {zone.Number}"
            : zone.Label;

    private static string ZoneSummary(IReadOnlyList<AnalysisLapTrace> traces, TrackSegment zone)
    {
        var speed = PerLapAverage(traces, zone, point => point.SpeedMph);
        var brake = PerLapAverage(traces, zone, point => point.Brake);
        var parts = new List<string>(2);
        if (speed.HasValue) parts.Add($"{speed.Value:0} mph");
        if (brake.HasValue) parts.Add($"{brake.Value * 100:0}% brake");
        return parts.Count == 0 ? "No recorded values" : string.Join(" \u00B7 ", parts);
    }

    private static double? PerLapAverage(
        IReadOnlyList<AnalysisLapTrace> traces,
        TrackSegment zone,
        Func<AnalysisTracePoint, double?> selector)
    {
        var lapAverages = new List<double>(traces.Count);
        foreach (var trace in traces)
        {
            var values = trace.Points
                .Where(point => InZone(point.LapPercent, zone))
                .Select(selector)
                .Where(value => value.HasValue && double.IsFinite(value.Value))
                .Select(value => value!.Value)
                .ToArray();
            if (values.Length > 0) lapAverages.Add(values.Average());
        }
        return lapAverages.Count > 0 ? lapAverages.Average() : null;
    }

    private static bool InZone(double lapPercent, TrackSegment zone) => zone.WrapsStartFinish
        ? lapPercent >= zone.StartPercent || lapPercent <= zone.EndPercent
        : lapPercent >= zone.StartPercent && lapPercent <= zone.EndPercent;
}
