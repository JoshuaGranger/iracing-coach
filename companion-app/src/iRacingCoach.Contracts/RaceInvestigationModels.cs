using System.Text.Json;

namespace iRacingCoach.Contracts;

public sealed record RaceInvestigation(
    int SchemaVersion,
    string Status,
    string Summary,
    string? DefaultComparisonId,
    IReadOnlyList<RaceInvestigationComparison> Comparisons,
    IReadOnlyList<RaceInvestigationLap> Laps,
    IReadOnlyList<RaceInvestigationEvent> Timeline,
    RaceTrackEvolution? TrackEvolution,
    IReadOnlyList<string> Limitations,
    JsonElement Source,
    string? UnavailableReason = null,
    IReadOnlyList<RaceRunView>? Runs = null,
    RaceReviewSections? Sections = null,
    string? ProjectionVersion = null)
{
    /// <summary>Schema 1 predates run views, review sections and pass inspection.</summary>
    public bool HasSectionInspection => SchemaVersion >= 2 && Sections is { Sections.Count: > 0 };
}

/// <summary>
/// One backend window comparison. <see cref="PaceComparable"/> false means the backend
/// withheld the pace result (for example a caution between or inside the windows, or no
/// screened lap); <see cref="Summary"/> then states why. Projections before
/// sections-trials-v2 carry no flag and read as comparable.
/// </summary>
public sealed record RaceInvestigationComparison(
    string Id, string Kind, string Title, string Summary,
    RaceInvestigationCohort Baseline, RaceInvestigationCohort Compared,
    double? DeltaSeconds, string Eligibility,
    IReadOnlyList<RaceInvestigationMetric> Metrics,
    IReadOnlyList<RaceInvestigationCorner> Corners,
    IReadOnlyList<RaceInvestigationHypothesis> Hypotheses,
    IReadOnlyList<RaceInvestigationAction> Actions,
    RaceInspection? Inspection = null,
    bool PaceComparable = true,
    IReadOnlyList<string>? PaceUnusableReasons = null);

public sealed record RaceInvestigationCohort(
    string Label, int? RunNumber, IReadOnlyList<int> LapNumbers, int LapCount,
    IReadOnlyList<int> EligibleLapNumbers, IReadOnlyList<RaceInvestigationExclusion> ExcludedLaps,
    double? MedianLapSeconds, double? GreenAgeStart, double? GreenAgeEnd,
    double? FuelStartGallons, double? TrackTemperatureF, int? RepresentativeLap);

public sealed record RaceInvestigationExclusion(int Lap, IReadOnlyList<string> Reasons);
public sealed record RaceInvestigationMetric(string Key, string Label, string Unit,
    double? Baseline, double? Compared, double? Delta, string Evidence, string Detail);
public sealed record RaceInvestigationCorner(string Id, string Label, double? StartPct, double? EndPct,
    double? DeltaSeconds, IReadOnlyList<RaceInvestigationMetric> Metrics,
    string? ShortLabel = null, string? BoundaryStatus = null, int BaselineCount = 0, int ComparedCount = 0);
public sealed record RaceInvestigationHypothesis(string Key, string Label, string Status,
    IReadOnlyList<string> Supporting, IReadOnlyList<string> Contradicting, IReadOnlyList<string> Unknown,
    string? GeneralContext = null, IReadOnlyList<RaceInvestigationReference>? References = null);
public sealed record RaceInvestigationReference(string Title, string Url);
public sealed record RaceInvestigationAction(int Priority, string Title, string Instruction,
    IReadOnlyList<string> Evidence, string? Limitation, IReadOnlyList<int> LapNumbers,
    double? StartPct, double? EndPct, IReadOnlyList<string> SignalKeys,
    string? SectionId = null, RaceTrialSpec? Trial = null);
public sealed record RaceInvestigationLap(int Lap, double? LapTimeSeconds, int? Position,
    string FlagState, bool Eligible, IReadOnlyList<string> ExclusionReasons, int? RunNumber, bool PlotPace = false,
    double? GreenExposure = null);
public sealed record RaceInvestigationEvent(int Lap, string Kind, string Label, string Detail,
    double? Value, string? Unit);
public sealed record RaceTrackEvolution(string Status, string? Provenance, string? StartingUsage,
    string Summary, IReadOnlyList<RaceTrackEvolutionPoint> Points, string? UsageUnit,
    string? UsageSource, IReadOnlyList<string> Limitations);
public sealed record RaceTrackEvolutionPoint(int Lap, double? TrackTemperatureF, double? UsageValue);

/// <summary>Observed trend and screened fit for one run on the green-exposure axis.</summary>
public sealed record RaceRunView(int RunNumber, int PlottedLapCount, int? FirstLap, int? LastLap,
    RaceRunTrend Trend, RaceRunFit? Fit, string? FitUnavailableReason);
public sealed record RaceRunTrend(string Method, bool IncludesReferenceExclusions, int CoveredLapCount,
    IReadOnlyList<IReadOnlyList<RaceTrendPoint>> Segments, IReadOnlyList<RaceTrendGap> Gaps);
public sealed record RaceTrendPoint(int Lap, double X, double Y, IReadOnlyList<int> SampleLaps);
public sealed record RaceTrendGap(int AfterLap, int BeforeLap, string Reason);
public sealed record RaceRunFit(string Method, int EligibleCount, IReadOnlyList<int> Laps,
    double SlopeSecondsPerGreenLap, double DomainStart, double DomainEnd, IReadOnlyList<RaceFitPoint> Points);
public sealed record RaceFitPoint(double X, double Y);

/// <summary>Section bounds with their source; letters are analysis labels, not turn numbers.</summary>
public sealed record RaceReviewSections(string? ConfigurationKey, string Source, string Version, string Provenance,
    IReadOnlyList<RaceReviewSection> Sections, string? UnavailableReason);
public sealed record RaceReviewSection(string Id, string Label, double StartPct, double EndPct,
    string BoundaryStatus, string Provenance);

/// <summary>Two actual passes per section with their approach, computed by the backend.</summary>
public sealed record RaceInspection(string Mode, IReadOnlyList<int> PoolLaps, double? PoolExposureStart,
    double? PoolExposureEnd, IReadOnlyDictionary<string, RacePassPair> Sections);
public sealed record RacePassPair(string SectionId, double StartPct, double EndPct, double ContextStartPct,
    string ContextLabel, bool TimingComplete, int CandidateCount, RacePass A, RacePass B,
    double? SectionDeltaSeconds, double? EntryGapSeconds, double? ExitGapSeconds, double? EntrySpeedDifferenceMph,
    RaceSlipSummary Slip, IReadOnlyList<RacePairRow> Rows);
public sealed record RacePass(int Lap, string Role, double? SectionSeconds, int? RunNumber, double? GreenExposure,
    bool Eligible, IReadOnlyList<string> ExclusionReasons, double? FuelStartGallons, double? TrackTemperatureF);
public sealed record RaceSlipSummary(RaceSlipValue A, RaceSlipValue B);
public sealed record RaceSlipValue(double Coverage, double? MedianAbs, double? PeakAbs);
public sealed record RacePairRow(double X, RaceChannelValues A, RaceChannelValues B, double? Gap);
public sealed record RaceChannelValues(double? Speed, double? Throttle, double? Brake, double? Steering, double? Slip);

/// <summary>A saveable one-change test: exact bounds, the slow condition to beat and the rule.</summary>
public sealed record RaceTrialSpec(string MetricKey, string MetricLabel, double MetricStartPct, double MetricEndPct,
    string SectionId, string SectionLabel, double SectionStartPct, double SectionEndPct,
    double BaselineMedianSeconds, IReadOnlyList<int> BaselineLaps, double ReferenceMedianSeconds, IReadOnlyList<int> ReferenceLaps,
    double? ExposureStart, double? ExposureEnd, double ThresholdSeconds, int MinimumLaps, string Rule,
    double? FuelStartGallons, double? TrackTemperatureF, string? AnnotationVersion);

public sealed record RaceInvestigationInspection(IReadOnlyList<int> LapNumbers,
    double? StartPct = null, double? EndPct = null, IReadOnlyList<string>? SignalKeys = null);

/// <summary>A saved trial and every later-recording check, as the backend stores them.</summary>
public sealed record DrivingTrial(string TrialId, string CreatedAt, string Title, string Instruction,
    IReadOnlyList<string> Evidence, DrivingTrialRecording Source, string ComparisonId, string ComparisonTitle,
    RaceTrialSpec Spec, int EvaluationCount, DrivingTrialEvaluation? LatestEvaluation,
    IReadOnlyList<DrivingTrialEvaluation> Evaluations)
{
    public bool IsOpen => LatestEvaluation is null || LatestEvaluation.Verdict is "not_comparable" or "not_established";
}
public sealed record DrivingTrialRecording(string? AnalysisId, string? AnalysisPath, string? RecordedAt, string? Track,
    string? Layout, string? ConfigurationKey, string? CarId, string? Car, string? CarPath, string? SetupName,
    string? SetupFingerprint, string? SessionType, string? SubsessionId);
public sealed record DrivingTrialEvaluation(string EvaluatedAt, string? AnalysisId, string? AnalysisPath,
    DrivingTrialRecording Recording, string Verdict, IReadOnlyList<string> Reasons, IReadOnlyList<string> Confounds,
    double? WindowStart, double? WindowEnd, IReadOnlyList<DrivingTrialLap> Laps, double? MedianSeconds, int Count,
    double? ImprovementSeconds, RacePassPair? Pair);
public sealed record DrivingTrialLap(int Lap, double Seconds, double? GreenExposure, double? FuelStartGallons);
