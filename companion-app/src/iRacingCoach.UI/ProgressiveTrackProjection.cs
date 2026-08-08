using System.Globalization;
using iRacingCoach.Contracts;

namespace iRacingCoach.UI;

public sealed record ProgressiveMapPoint(double Percent, double X, double Y);
public sealed record ProgressiveMapLine(double X1, double Y1, double X2, double Y2);

public sealed record ProgressiveProjectedTrack(
    bool Available,
    string GeometryStatus,
    string ViewBox,
    IReadOnlyList<ProgressiveMapPoint> MainPath,
    IReadOnlyList<ProgressiveMapPoint> PitLane,
    IReadOnlyList<ProgressiveMapPoint> PitEntry,
    IReadOnlyList<ProgressiveMapPoint> PitExit,
    ProgressiveMapLine? StartFinish)
{
    public string MainPathData => ProgressiveTrackProjection.ToPath(MainPath, true);
    public string PitLaneData => ProgressiveTrackProjection.ToPath(PitLane, false);
    public string PitEntryData => ProgressiveTrackProjection.ToPath(PitEntry, false);
    public string PitExitData => ProgressiveTrackProjection.ToPath(PitExit, false);
}

/// <summary>
/// Uses the same canonical-vector orientation and auto-fit behavior as Race Analysis.
/// It intentionally does not infer physical track edges from a single racing line.
/// </summary>
public static class ProgressiveTrackProjection
{
    private const double Width = 720;
    private const double Height = 420;
    private const double Padding = 34;

    public static ProgressiveProjectedTrack Create(AnalysisWorkspace workspace)
    {
        var geometry = workspace.VectorGeometry;
        if (geometry is { MainPath.Count: >= 3, Quality.MainLoopComplete: true } &&
            !geometry.Status.Equals("unavailable", StringComparison.OrdinalIgnoreCase))
        {
            var boundsPoints = geometry.MainPath
                .Concat(geometry.PitLane)
                .Concat(geometry.PitEntryPath)
                .Concat(geometry.PitExitPath)
                .Concat(LinePoints(geometry.StartFinishLine))
                .Where(IsFinite)
                .ToArray();
            var projection = CreateProjection(boundsPoints, invertY: false);
            return new ProgressiveProjectedTrack(
                true,
                "Canonical recorded geometry",
                $"0 0 {F(Width)} {F(Height)}",
                Project(geometry.MainPath, projection, true),
                Project(geometry.PitLane, projection, false),
                Project(geometry.PitEntryPath, projection, false),
                Project(geometry.PitExitPath, projection, false),
                geometry.StartFinishLine is { } line ? Project(line, projection) : BuildStartFinish(Project(geometry.MainPath, projection, true)));
        }

        var shape = workspace.TrackShape
            .Where(point => double.IsFinite(point.LapPercent) && double.IsFinite(point.X) && double.IsFinite(point.Y))
            .OrderBy(point => Wrap(point.LapPercent))
            .ToArray();
        if (!IsCompleteTrackShape(shape))
            return new ProgressiveProjectedTrack(false, "Geometry unavailable", $"0 0 {F(Width)} {F(Height)}", [], [], [], [], null);

        var source = shape.Select(point => new AnalysisVectorPoint(point.X, point.Y, Wrap(point.LapPercent))).ToArray();
        var shapeProjection = CreateProjection(source, invertY: true);
        var main = Project(source, shapeProjection, true);
        return new ProgressiveProjectedTrack(
            true,
            "Recorded racing line",
            $"0 0 {F(Width)} {F(Height)}",
            main,
            [], [], [],
            BuildStartFinish(main));
    }

    public static string PathForRange(IReadOnlyList<ProgressiveMapPoint> points, double startPct, double endPct)
    {
        if (points.Count < 2) return string.Empty;
        var ordered = points
            .Where(point => double.IsFinite(point.Percent) && double.IsFinite(point.X) && double.IsFinite(point.Y))
            .OrderBy(point => Wrap(point.Percent))
            .ToArray();
        if (ordered.Length < 2) return string.Empty;

        var start = Wrap(startPct);
        var end = Wrap(endPct);
        if (end <= start) end += 1;
        var ranged = new List<ProgressiveMapPoint> { PointAt(ordered, start) };
        ranged.AddRange(ordered
            .Select(point => point.Percent < start ? point with { Percent = point.Percent + 1 } : point)
            .Where(point => point.Percent > start && point.Percent < end)
            .OrderBy(point => point.Percent));
        ranged.Add(PointAt(ordered, end));
        return ToPath(ranged, false);
    }

    public static ProgressiveMapPoint PointAt(IReadOnlyList<ProgressiveMapPoint> points, double percent)
    {
        if (points.Count == 0) return new ProgressiveMapPoint(Wrap(percent), Width / 2, Height / 2);
        var ordered = points.OrderBy(point => Wrap(point.Percent)).ToArray();
        var target = Wrap(percent);
        for (var index = 0; index < ordered.Length; index++)
        {
            var first = ordered[index];
            var second = ordered[(index + 1) % ordered.Length];
            var a = Wrap(first.Percent);
            var b = Wrap(second.Percent);
            if (index == ordered.Length - 1) b += 1;
            var adjusted = target < a ? target + 1 : target;
            if (adjusted < a || adjusted > b) continue;
            var span = Math.Max(.0000001, b - a);
            var ratio = Math.Clamp((adjusted - a) / span, 0, 1);
            return new ProgressiveMapPoint(target,
                first.X + (second.X - first.X) * ratio,
                first.Y + (second.Y - first.Y) * ratio);
        }
        return ordered.MinBy(point => CircularDistance(point.Percent, target))! with { Percent = target };
    }

    public static string ToPath(IReadOnlyList<ProgressiveMapPoint> points, bool close)
    {
        if (points.Count == 0) return string.Empty;
        var path = $"M {F(points[0].X)} {F(points[0].Y)}";
        for (var index = 1; index < points.Count; index++)
            path += $" L {F(points[index].X)} {F(points[index].Y)}";
        return close ? path + " Z" : path;
    }

    public static bool IsCompleteTrackShape(IReadOnlyList<TrackShapePoint> points)
    {
        if (points.Count < 3) return false;
        var ordered = points
            .Where(point => double.IsFinite(point.LapPercent) && double.IsFinite(point.X) && double.IsFinite(point.Y))
            .OrderBy(point => Wrap(point.LapPercent))
            .ToArray();
        if (ordered.Length < 3) return false;
        var maximumGap = Wrap(ordered[0].LapPercent) + 1 - Wrap(ordered[^1].LapPercent);
        for (var index = 1; index < ordered.Length; index++)
            maximumGap = Math.Max(maximumGap, Wrap(ordered[index].LapPercent) - Wrap(ordered[index - 1].LapPercent));
        var width = ordered.Max(point => point.X) - ordered.Min(point => point.X);
        var height = ordered.Max(point => point.Y) - ordered.Min(point => point.Y);
        var span = Math.Max(width, height);
        var closure = Math.Sqrt(Math.Pow(ordered[0].X - ordered[^1].X, 2) + Math.Pow(ordered[0].Y - ordered[^1].Y, 2));
        return maximumGap <= .05 && span > 0 && closure <= span * .15;
    }

    private static Projection CreateProjection(IReadOnlyList<AnalysisVectorPoint> points, bool invertY)
    {
        var minimumX = points.Min(point => point.X);
        var maximumX = points.Max(point => point.X);
        var minimumY = points.Min(point => point.Y);
        var maximumY = points.Max(point => point.Y);
        var scale = Math.Min(
            (Width - Padding * 2) / Math.Max(.0001, maximumX - minimumX),
            (Height - Padding * 2) / Math.Max(.0001, maximumY - minimumY));
        return new Projection((minimumX + maximumX) / 2, (minimumY + maximumY) / 2, scale, invertY);
    }

    private static IReadOnlyList<ProgressiveMapPoint> Project(IReadOnlyList<AnalysisVectorPoint> points, Projection projection, bool ensurePercent)
    {
        if (points.Count == 0) return [];
        return points
            .Where(IsFinite)
            .Select((point, index) => Project(point with
            {
                LapPercent = point.LapPercent ?? (ensurePercent ? index / (double)points.Count : 0)
            }, projection))
            .ToArray();
    }

    private static ProgressiveMapPoint Project(AnalysisVectorPoint point, Projection projection)
    {
        var y = (point.Y - projection.CenterY) * projection.Scale * (projection.InvertY ? -1 : 1);
        return new ProgressiveMapPoint(
            Wrap(point.LapPercent ?? 0),
            Width / 2 + (point.X - projection.CenterX) * projection.Scale,
            Height / 2 + y);
    }

    private static ProgressiveMapLine Project(AnalysisVectorLine line, Projection projection)
    {
        var first = Project(line.A, projection);
        var second = Project(line.B, projection);
        return new ProgressiveMapLine(first.X, first.Y, second.X, second.Y);
    }

    private static ProgressiveMapLine? BuildStartFinish(IReadOnlyList<ProgressiveMapPoint> points)
    {
        if (points.Count < 2) return null;
        var center = PointAt(points, 0);
        var before = PointAt(points, .995);
        var after = PointAt(points, .005);
        var tangentX = after.X - before.X;
        var tangentY = after.Y - before.Y;
        var length = Math.Sqrt(tangentX * tangentX + tangentY * tangentY);
        if (length < .0001) return new ProgressiveMapLine(center.X, center.Y - 16, center.X, center.Y + 16);
        const double halfLength = 16;
        var normalX = -tangentY / length * halfLength;
        var normalY = tangentX / length * halfLength;
        return new ProgressiveMapLine(center.X - normalX, center.Y - normalY, center.X + normalX, center.Y + normalY);
    }

    private static IEnumerable<AnalysisVectorPoint> LinePoints(AnalysisVectorLine? line) => line is null ? [] : [line.A, line.B];
    private static bool IsFinite(AnalysisVectorPoint point) => double.IsFinite(point.X) && double.IsFinite(point.Y);
    private static double CircularDistance(double a, double b)
    {
        var difference = Math.Abs(Wrap(a) - Wrap(b));
        return Math.Min(difference, 1 - difference);
    }
    private static double Wrap(double value) => ((value % 1) + 1) % 1;
    private static string F(double value) => value.ToString("0.###", CultureInfo.InvariantCulture);
    private sealed record Projection(double CenterX, double CenterY, double Scale, bool InvertY);
}
