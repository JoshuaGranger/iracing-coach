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
    string? UnavailableReason = null);

public sealed record RaceInvestigationComparison(
    string Id, string Kind, string Title, string Summary,
    RaceInvestigationCohort Baseline, RaceInvestigationCohort Compared,
    double? DeltaSeconds, string Eligibility,
    IReadOnlyList<RaceInvestigationMetric> Metrics,
    IReadOnlyList<RaceInvestigationCorner> Corners,
    IReadOnlyList<RaceInvestigationHypothesis> Hypotheses,
    IReadOnlyList<RaceInvestigationAction> Actions);

public sealed record RaceInvestigationCohort(
    string Label, int? RunNumber, IReadOnlyList<int> LapNumbers, int LapCount,
    IReadOnlyList<int> EligibleLapNumbers, IReadOnlyList<RaceInvestigationExclusion> ExcludedLaps,
    double? MedianLapSeconds, double? GreenAgeStart, double? GreenAgeEnd,
    double? FuelStartGallons, double? TrackTemperatureF, int? RepresentativeLap);

public sealed record RaceInvestigationExclusion(int Lap, IReadOnlyList<string> Reasons);
public sealed record RaceInvestigationMetric(string Key, string Label, string Unit,
    double? Baseline, double? Compared, double? Delta, string Evidence, string Detail);
public sealed record RaceInvestigationCorner(string Id, string Label, double? StartPct, double? EndPct,
    double? DeltaSeconds, IReadOnlyList<RaceInvestigationMetric> Metrics);
public sealed record RaceInvestigationHypothesis(string Key, string Label, string Status,
    IReadOnlyList<string> Supporting, IReadOnlyList<string> Contradicting, IReadOnlyList<string> Unknown,
    string? GeneralContext = null, IReadOnlyList<RaceInvestigationReference>? References = null);
public sealed record RaceInvestigationReference(string Title, string Url);
public sealed record RaceInvestigationAction(int Priority, string Title, string Instruction,
    IReadOnlyList<string> Evidence, string? Limitation, IReadOnlyList<int> LapNumbers,
    double? StartPct, double? EndPct, IReadOnlyList<string> SignalKeys);
public sealed record RaceInvestigationLap(int Lap, double? LapTimeSeconds, int? Position,
    string FlagState, bool Eligible, IReadOnlyList<string> ExclusionReasons, int? RunNumber, bool PlotPace = false);
public sealed record RaceInvestigationEvent(int Lap, string Kind, string Label, string Detail,
    double? Value, string? Unit);
public sealed record RaceTrackEvolution(string Status, string? Provenance, string? StartingUsage,
    string Summary, IReadOnlyList<RaceTrackEvolutionPoint> Points, string? UsageUnit,
    string? UsageSource, IReadOnlyList<string> Limitations);
public sealed record RaceTrackEvolutionPoint(int Lap, double? TrackTemperatureF, double? UsageValue);

public sealed record RaceInvestigationInspection(IReadOnlyList<int> LapNumbers,
    double? StartPct = null, double? EndPct = null, IReadOnlyList<string>? SignalKeys = null);
