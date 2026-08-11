using iRacingCoach.Contracts;

namespace iRacingCoach.UI;

/// <summary>
/// Guards the Race Analysis projection anchor. A pit branch can have many
/// recorded points and nearly full lap-percent labels, so point count alone
/// cannot establish that a path is the canonical closed main loop.
/// </summary>
public static class AnalysisTrackMapGeometry
{
    private const double MinimumNormalizedMainSpan = .20;
    private const double MaximumRelativeMainSegment = .35;
    private const double MaximumOverlayMargin = .45;
    private const double MaximumRelativeOverlaySegment = .55;

    public static bool IsCanonicalMainLoop(IReadOnlyList<AnalysisVectorPoint> points)
    {
        var finite = points
            .Where(point => double.IsFinite(point.X) && double.IsFinite(point.Y))
            .ToArray();
        if (finite.Length < 3) return false;

        var width = finite.Max(point => point.X) - finite.Min(point => point.X);
        var height = finite.Max(point => point.Y) - finite.Min(point => point.Y);
        var span = Math.Max(width, height);
        if (!double.IsFinite(span) || span < MinimumNormalizedMainSpan) return false;

        var maximumSegment = finite
            .Zip(finite.Skip(1), (before, after) => Distance(before, after))
            .DefaultIfEmpty(0)
            .Max();
        if (!double.IsFinite(maximumSegment) || maximumSegment > span * MaximumRelativeMainSegment) return false;

        var closure = Math.Sqrt(
            Math.Pow(finite[0].X - finite[^1].X, 2)
            + Math.Pow(finite[0].Y - finite[^1].Y, 2));
        if (!double.IsFinite(closure) || closure > span * .15) return false;

        var percentages = finite
            .Where(point => point.LapPercent is { } percent && double.IsFinite(percent))
            .Select(point => Wrap(point.LapPercent!.Value))
            .Order()
            .ToArray();
        if (percentages.Length < 24) return true;

        var maximumGap = percentages[0] + 1 - percentages[^1];
        for (var index = 1; index < percentages.Length; index++)
            maximumGap = Math.Max(maximumGap, percentages[index] - percentages[index - 1]);
        return maximumGap <= .05;
    }

    public static bool IsPlausibleOverlayPath(
        IReadOnlyList<AnalysisVectorPoint> mainPath,
        IReadOnlyList<AnalysisVectorPoint> overlay)
    {
        if (!IsCanonicalMainLoop(mainPath) || overlay.Count < 2) return false;
        var main = Extents(mainPath);
        var candidate = Extents(overlay);
        if (main is null || candidate is null || candidate.Count != overlay.Count) return false;
        var span = Math.Max(main.MaximumX - main.MinimumX, main.MaximumY - main.MinimumY);
        var margin = span * MaximumOverlayMargin;
        if (candidate.MinimumX < main.MinimumX - margin
            || candidate.MaximumX > main.MaximumX + margin
            || candidate.MinimumY < main.MinimumY - margin
            || candidate.MaximumY > main.MaximumY + margin)
            return false;
        var maximumSegment = overlay
            .Zip(overlay.Skip(1), Distance)
            .DefaultIfEmpty(0)
            .Max();
        return double.IsFinite(maximumSegment) && maximumSegment <= span * MaximumRelativeOverlaySegment;
    }

    public static bool IsPlausibleOverlayLine(
        IReadOnlyList<AnalysisVectorPoint> mainPath,
        AnalysisVectorLine? line)
    {
        if (line is null || !IsPlausibleOverlayPath(mainPath, [line.A, line.B])) return false;
        var main = Extents(mainPath);
        if (main is null) return false;
        var span = Math.Max(main.MaximumX - main.MinimumX, main.MaximumY - main.MinimumY);
        return Distance(line.A, line.B) <= span * .20;
    }

    public static bool IsPlausibleNormalizedPoint(
        IReadOnlyList<AnalysisVectorPoint> mainPath,
        double x,
        double y)
    {
        if (!double.IsFinite(x) || !double.IsFinite(y) || !IsCanonicalMainLoop(mainPath)) return false;
        var main = Extents(mainPath);
        if (main is null) return false;
        var span = Math.Max(main.MaximumX - main.MinimumX, main.MaximumY - main.MinimumY);
        var margin = span * MaximumOverlayMargin;
        return x >= main.MinimumX - margin && x <= main.MaximumX + margin
            && y >= main.MinimumY - margin && y <= main.MaximumY + margin;
    }

    private static GeometryExtents? Extents(IReadOnlyList<AnalysisVectorPoint> points)
    {
        var finite = points.Where(point => double.IsFinite(point.X) && double.IsFinite(point.Y)).ToArray();
        return finite.Length == 0
            ? null
            : new GeometryExtents(
                finite.Min(point => point.X), finite.Max(point => point.X),
                finite.Min(point => point.Y), finite.Max(point => point.Y), finite.Length);
    }

    private static double Distance(AnalysisVectorPoint before, AnalysisVectorPoint after) => Math.Sqrt(
        Math.Pow(after.X - before.X, 2) + Math.Pow(after.Y - before.Y, 2));

    private static double Wrap(double value)
    {
        var wrapped = value % 1;
        return wrapped < 0 ? wrapped + 1 : wrapped;
    }

    private sealed record GeometryExtents(
        double MinimumX,
        double MaximumX,
        double MinimumY,
        double MaximumY,
        int Count);
}

/// <summary>
/// Stable screen-space work limits for race-analysis rendering. Logical lap
/// selection is never truncated; this class only limits visual vertices and
/// detailed cursor comparisons whose density can exceed the display itself.
/// </summary>
public static class AnalysisRenderBudget
{
    public const int MaximumColorGroups = 20;
    public const int CursorComparisonBudget = 24;
    public const int CursorSampleBins = 160;
    public const int TraceVertexBudgetPerSignal = 48_000;
    public const int MinimumPointsPerTrace = 48;

    public static int PointBudgetPerTrace(int logicalTraceCount, double screenWidth, bool focused = false)
    {
        var screenBudget = Math.Clamp((int)Math.Ceiling(Math.Max(1, screenWidth) * 1.25), 64, 1_200);
        if (focused) return screenBudget;
        var sharedBudget = Math.Max(MinimumPointsPerTrace, TraceVertexBudgetPerSignal / Math.Max(1, logicalTraceCount));
        return Math.Min(screenBudget, sharedBudget);
    }

    public static IReadOnlyList<int> RepresentativeIndices(int itemCount, int budget)
    {
        if (itemCount <= 0 || budget <= 0) return [];
        if (itemCount <= budget) return Enumerable.Range(0, itemCount).ToArray();
        if (budget == 1) return [0];

        var result = new int[budget];
        for (var slot = 0; slot < budget; slot++)
            result[slot] = (int)Math.Round(slot * (itemCount - 1d) / (budget - 1d));
        return result.Distinct().ToArray();
    }

    public static int ColorGroupCount(int logicalTraceCount) =>
        Math.Min(Math.Max(0, logicalTraceCount), MaximumColorGroups);
}
