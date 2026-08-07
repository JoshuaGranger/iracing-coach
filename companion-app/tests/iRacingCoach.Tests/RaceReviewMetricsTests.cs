using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class RaceReviewMetricsTests
{
    [TestMethod]
    public void CornerSummaries_UseComparableLapsPreserveMissingSignalsAndIncludeEveryArea()
    {
        var segments = Enumerable.Range(1, 5)
            .Select(number => new TrackSegment(number, .4, .6, false, $"Load zone {number}"))
            .ToArray();
        var traces = new[]
        {
            Trace(1, 100, null, comparisonEligible: true),
            Trace(2, 500, 1, comparisonEligible: false),
            Trace(3, 120, .2, comparisonEligible: true)
        };
        var workspace = Workspace(traces, segments, [new AnalysisIncident(2, 4)]);

        var summaries = RaceReviewMetrics.BuildCornerSummaries(workspace);

        Assert.HasCount(5, summaries, "Race Review must not silently discard track areas after the fourth one.");
        Assert.AreEqual("Corner area 1", summaries[0].Label);
        Assert.AreEqual("100 mph", summaries[0].Early, "Missing brake must stay absent instead of becoming 0%.");
        Assert.AreEqual("120 mph · 20% brake", summaries[0].Late);
        Assert.IsFalse(summaries.Any(summary => summary.Early.Contains("500", StringComparison.Ordinal)), "An excluded lap must not contaminate the comparison.");
    }

    [TestMethod]
    public void CornerSummaries_RequireTwoComparableLaps()
    {
        var workspace = Workspace(
            [Trace(1, 100, .1, comparisonEligible: true), Trace(2, 110, .2, comparisonEligible: false)],
            [new TrackSegment(1, .4, .6, false, "Turn 1")],
            []);

        Assert.IsEmpty(RaceReviewMetrics.BuildCornerSummaries(workspace));
    }

    private static AnalysisLapTrace Trace(int lap, double? speed, double? brake, bool comparisonEligible) => new(
        lap, 30 + lap, true, "green", 1, 0, 0,
        [new AnalysisTracePoint(.5, null, speed, null, null, null, null, brake, null, null, null, null, null, null, null, null, null, null, null, null)],
        ComparisonEligible: comparisonEligible,
        ExclusionReason: comparisonEligible ? string.Empty : "repair-confounded");

    private static AnalysisWorkspace Workspace(
        IReadOnlyList<AnalysisLapTrace> traces,
        IReadOnlyList<TrackSegment> segments,
        IReadOnlyList<AnalysisIncident> incidents) => new(
        1,
        "race-review-test",
        "Test Track",
        "Oval",
        "Test Car",
        "Fixed",
        "Race",
        traces.Count,
        traces.Count,
        0,
        [],
        [],
        traces,
        [],
        segments,
        "track_shape",
        "Relative estimate",
        "Unavailable",
        "Recorded",
        new AnalysisStrategy(null, null, null, null, [], null, null, "unavailable", [], []),
        new AnalysisDamage(0, 0, 0, 0, null, null, [], incidents),
        string.Empty,
        "medium",
        0,
        "Not graded",
        []);
}
