using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

public static class LiveTelemetryFrameCompaction
{
    public static IReadOnlyList<LiveTracePoint> Compact(
        IReadOnlyList<LiveTracePoint> frames,
        int maximumFrames,
        IReadOnlyList<Func<LiveTracePoint, double?>> selectors)
    {
        if (frames.Count <= maximumFrames) return frames;
        maximumFrames = Math.Max(8, maximumFrames);
        var selected = new SortedSet<int>();
        var newestCount = Math.Max(1, maximumFrames / 2);
        var olderCount = Math.Max(0, frames.Count - newestCount);
        for (var index = olderCount; index < frames.Count; index++) selected.Add(index);

        var summaries = selectors.Select(selector => Summarize(frames, olderCount, selector)).ToArray();

        // Missing samples are semantic chart breaks, not disposable empty data.
        // Add gap starts round-robin so one noisy channel cannot consume the
        // whole budget before another channel gets a truthful discontinuity.
        for (var gap = 0; selected.Count < maximumFrames; gap++)
        {
            var added = false;
            foreach (var summary in summaries)
            {
                if (gap >= summary.Gaps.Count) continue;
                added |= selected.Add(summary.Gaps[gap]);
                if (selected.Count >= maximumFrames) break;
            }
            if (!added) break;
        }

        foreach (var summary in summaries)
        {
            if (selected.Count >= maximumFrames) break;
            if (summary.MinimumIndex >= 0) selected.Add(summary.MinimumIndex);
            if (selected.Count >= maximumFrames) break;
            if (summary.MaximumIndex >= 0) selected.Add(summary.MaximumIndex);
        }

        for (var slot = 0; selected.Count < maximumFrames && slot < maximumFrames * 2 && olderCount > 0; slot++)
            selected.Add((int)((long)Math.Max(0, olderCount - 1) * slot / Math.Max(1, maximumFrames * 2 - 1)));
        return selected.Select(index => frames[index]).ToArray();
    }

    private static SelectorSummary Summarize(
        IReadOnlyList<LiveTracePoint> frames,
        int count,
        Func<LiveTracePoint, double?> selector)
    {
        var minimumIndex = -1;
        var maximumIndex = -1;
        var minimum = 0d;
        var maximum = 0d;
        var gaps = new List<int>();
        var insideGap = false;
        for (var index = 0; index < count; index++)
        {
            var value = selector(frames[index]);
            if (!value.HasValue || !double.IsFinite(value.Value))
            {
                if (!insideGap) gaps.Add(index);
                insideGap = true;
                continue;
            }
            insideGap = false;
            if (minimumIndex < 0 || value.Value < minimum) { minimum = value.Value; minimumIndex = index; }
            if (maximumIndex < 0 || value.Value > maximum) { maximum = value.Value; maximumIndex = index; }
        }
        return new SelectorSummary(minimumIndex, maximumIndex, gaps);
    }

    private sealed record SelectorSummary(int MinimumIndex, int MaximumIndex, IReadOnlyList<int> Gaps);
}
