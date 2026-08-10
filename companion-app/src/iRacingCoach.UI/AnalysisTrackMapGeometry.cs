using iRacingCoach.Contracts;

namespace iRacingCoach.UI;

/// <summary>
/// Guards the Race Analysis projection anchor. A pit branch can have many
/// recorded points and nearly full lap-percent labels, so point count alone
/// cannot establish that a path is the canonical closed main loop.
/// </summary>
public static class AnalysisTrackMapGeometry
{
    public static bool IsCanonicalMainLoop(IReadOnlyList<AnalysisVectorPoint> points)
    {
        var finite = points
            .Where(point => double.IsFinite(point.X) && double.IsFinite(point.Y))
            .ToArray();
        if (finite.Length < 3) return false;

        var width = finite.Max(point => point.X) - finite.Min(point => point.X);
        var height = finite.Max(point => point.Y) - finite.Min(point => point.Y);
        var span = Math.Max(width, height);
        if (!double.IsFinite(span) || span <= .000001) return false;

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

    private static double Wrap(double value)
    {
        var wrapped = value % 1;
        return wrapped < 0 ? wrapped + 1 : wrapped;
    }
}
