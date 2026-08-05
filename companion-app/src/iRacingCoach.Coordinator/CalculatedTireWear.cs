using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

public static class CalculatedTireWear
{
    public static IReadOnlyDictionary<int, IReadOnlyList<double?>> Build(
        IReadOnlyList<AnalysisLapTrace> traces,
        IReadOnlyList<AnalysisRun> runs)
    {
        var cumulativeLoad = traces.ToDictionary(trace => trace.Lap, CumulativeLoad);
        var totalLoad = cumulativeLoad.ToDictionary(
            item => item.Key,
            item => item.Value.LastOrDefault(value => value.HasValue) ?? 0d);

        var factorsByRun = new Dictionary<int, double>();
        var measuredWearTotal = 0d;
        var measuredLoadTotal = 0d;
        foreach (var run in runs)
        {
            var observedWear = ObservedWorstWear(run);
            var runLoad = run.Laps.Where(totalLoad.ContainsKey).Sum(lap => totalLoad[lap]);
            if (observedWear is not > 0 || runLoad <= 0) continue;
            factorsByRun[run.Number] = observedWear.Value / runLoad;
            measuredWearTotal += observedWear.Value;
            measuredLoadTotal += runLoad;
        }

        if (measuredLoadTotal <= 0) return new Dictionary<int, IReadOnlyList<double?>>();
        var sessionFactor = measuredWearTotal / measuredLoadTotal;
        var runByLap = runs
            .SelectMany(run => run.Laps.Select(lap => new { lap, run }))
            .GroupBy(item => item.lap)
            .ToDictionary(group => group.Key, group => group.First().run);

        var result = new Dictionary<int, IReadOnlyList<double?>>();
        foreach (var trace in traces)
        {
            if (!cumulativeLoad.TryGetValue(trace.Lap, out var load) || totalLoad[trace.Lap] <= 0) continue;
            var factor = runByLap.TryGetValue(trace.Lap, out var run) && factorsByRun.TryGetValue(run.Number, out var directFactor)
                ? directFactor
                : sessionFactor;
            result[trace.Lap] = load.Select(value => value.HasValue ? value.Value * factor : (double?)null).ToArray();
        }
        return result;
    }

    private static double? ObservedWorstWear(AnalysisRun run)
    {
        var pit = run.PitStop;
        if (pit is null) return run.TireRemainingPercent is { } remaining ? Math.Clamp(100d - remaining, 0d, 100d) : null;
        var values = new[]
        {
            pit.LeftFrontTireWearPercent,
            pit.RightFrontTireWearPercent,
            pit.LeftRearTireWearPercent,
            pit.RightRearTireWearPercent
        }.Where(value => value.HasValue).Select(value => value!.Value).ToArray();
        return values.Length > 0
            ? values.Max()
            : run.TireRemainingPercent is { } fallbackRemaining ? Math.Clamp(100d - fallbackRemaining, 0d, 100d) : null;
    }

    private static IReadOnlyList<double?> CumulativeLoad(AnalysisLapTrace trace)
    {
        var result = new double?[trace.Points.Count];
        var cumulative = 0d;
        var hasLoad = false;
        for (var index = 0; index < trace.Points.Count; index++)
        {
            var point = trace.Points[index];
            var current = NonNegative(point.TireStressProxy);
            if (index == 0)
            {
                if (current.HasValue)
                {
                    cumulative = current.Value * Math.Clamp(point.LapPercent, 0d, 1d);
                    hasLoad = true;
                }
                result[index] = hasLoad ? cumulative : null;
                continue;
            }

            var previousPoint = trace.Points[index - 1];
            var previous = NonNegative(previousPoint.TireStressProxy);
            if (current.HasValue || previous.HasValue)
            {
                var segmentLoad = current.HasValue && previous.HasValue
                    ? (current.Value + previous.Value) / 2d
                    : current ?? previous ?? 0d;
                cumulative += segmentLoad * Math.Max(0d, point.LapPercent - previousPoint.LapPercent);
                hasLoad = true;
            }
            result[index] = hasLoad ? cumulative : null;
        }
        return result;
    }

    private static double? NonNegative(double? value) => value.HasValue ? Math.Max(0d, value.Value) : null;
}
