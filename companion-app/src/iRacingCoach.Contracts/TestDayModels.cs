using System.Text.Json;

namespace iRacingCoach.Contracts;

/// <summary>
/// The backend's read-only test-day review (contract test-day-review-v1). Every
/// measurement, verdict and suggestion is computed by the backend; the app only
/// renders it. <see cref="Source"/> keeps the complete response so unknown
/// optional fields survive.
/// </summary>
public sealed record TestDayReview(
    int SchemaVersion,
    string? TestDayId,
    int RunCount,
    TestDayCar Car,
    TestDayTrack Track,
    TestDayWindows Windows,
    TestDayScreening Screening,
    IReadOnlyList<TestDayRun> Runs,
    TestDayNextChange NextChange,
    IReadOnlyList<string> Limitations,
    JsonElement Source)
{
    public const string Contract = "test-day-review-v1";
    public const int CurrentSchemaVersion = 1;
}

public sealed record TestDayCar(string? CarPath, string? CarName, string? CarId);
public sealed record TestDayTrack(string? TrackName, string? TrackConfig, string? ConfigurationKey);

public sealed record TestDayWindows(TestDaySpan Early, TestDayLateWindow Late, TestDaySpan FallOff, int MinimumCleanLaps);
public sealed record TestDaySpan(int? FirstRunLap, int? LastRunLap, string? Label);
public sealed record TestDayLateWindow(string Status, int? FirstRunLap, int? LastRunLap, string? Label, string? Reason, string? Basis)
{
    public bool Available => string.Equals(Status, "available", StringComparison.Ordinal);
}

public sealed record TestDayScreening(string Rule, IReadOnlyList<int> OfflineFlagRuleRuns, string? OfflineFlagRule);

/// <summary>One stint of the day: its measured run, pace windows, setup change, verdict and tires.</summary>
public sealed record TestDayRun(
    int Run,
    string? AnalysisId,
    string? AnalysisPath,
    string? RecordedAt,
    string? SessionType,
    string? TestDayId,
    int? DiscoveryRunNumber,
    bool OfflineFlagRuleApplied,
    TestDaySetup Setup,
    TestDayMeasuredRun? MeasuredRun,
    TestDayConditions Conditions,
    TestDayWindow Early,
    TestDayWindow Late,
    TestDayFallOff FallOff,
    TestDaySetupChange SetupChange,
    TestDayComparison? Comparison,
    TestDayTires Tires,
    IReadOnlyList<TestDayLap> Laps,
    RaceRunView? Trend);

public sealed record TestDaySetup(string? Name, bool? Fixed, bool Embedded, string? GarageFingerprint);
public sealed record TestDayMeasuredRun(int? AnalysisRun, int FirstLap, int LastLap, int RunLaps, int OtherRunCount);
public sealed record TestDayConditions(double? TrackTemperatureF, double? StartFuelGallons, string? TireCompound, TestDayTireStart TireStart);
public sealed record TestDayTireStart(string Status, IReadOnlyDictionary<string, double> InitialOdometerMeters, string? Detail);

/// <summary>An equal-age pace window. Missing is "not_measured" with a reason, never a zero.</summary>
public sealed record TestDayWindow(string Status, int? FirstRunLap, int? LastRunLap, string? Label, int CleanLapCount,
    IReadOnlyList<int> Laps, double? MedianSeconds, string? Reason)
{
    public bool Measured => string.Equals(Status, "measured", StringComparison.Ordinal) && MedianSeconds.HasValue;
}

public sealed record TestDayFallOff(string Status, string Method, int? FirstRunLap, int? LastRunLap, int FitLapCount,
    IReadOnlyList<int> Laps, bool EqualAge, double? SlopeSecondsPerLap, double? ScatterSeconds, string? Reason)
{
    public bool Measured => string.Equals(Status, "measured", StringComparison.Ordinal) && SlopeSecondsPerLap.HasValue;
}

public sealed record TestDaySetupChange(string Status, int? ComparedWithRun, int ChangeCount, IReadOnlyList<TestDaySettingChange> Changes,
    int UnknownCount, IReadOnlyList<string> UnknownFields, bool Complete, string? Reason);
public sealed record TestDaySettingChange(string Field, string Label, string? Previous, string? Current, double? Delta, string? Unit);

public sealed record TestDayComparison(int ComparedWithRun, string Verdict, string? BlockedBy, string? Basis, string? WindowLabel,
    int? FirstRunLap, int? LastRunLap, double? PreviousMedianSeconds, double? CurrentMedianSeconds, double? DeltaSeconds,
    double? ThresholdSeconds, double? StandardErrorSeconds, IReadOnlyList<string> Reasons, IReadOnlyList<string> Flags, string? Summary);

public sealed record TestDayTires(TestDayStopReading StopReading, TestDaySheetReading SheetReading,
    IReadOnlyDictionary<string, TestDayTireCorner> Corners);
public sealed record TestDayStopReading(string Status, string Source, string? MeasurementStatus, string? Reason)
{
    public bool Measured => string.Equals(Status, "measured", StringComparison.Ordinal);
}
public sealed record TestDaySheetReading(string Status, string Source, string Timing, string TimingNote)
{
    public bool Recorded => string.Equals(Status, "recorded", StringComparison.Ordinal);
}

public sealed record TestDayTireCorner(TestDayStopCorner? Stop, TestDaySheetCorner? Sheet, TestDayAgreement SheetVsStop,
    TestDayAgreement SheetVsPreviousStop, TestDayCornerChange? ChangeFromPreviousStop);
/// <summary>Inside/middle/outside of one tire, already oriented by the backend.</summary>
public sealed record TestDayBands(double? Inside, double? Middle, double? Outside);
public sealed record TestDayStopCorner(TestDayBands? TemperatureF, TestDayBands? TreadPercent, double? PressurePsi, string? PressureKind,
    double? SpreadF, double? MinimumTreadPercent);
public sealed record TestDaySheetCorner(TestDayBands? TemperatureF, TestDayBands? TreadPercent, double? HotPressurePsi, double? ColdPressurePsi,
    double? SpreadF, double? MinimumTreadPercent);
public sealed record TestDayAgreement(string Status, double? MaxTemperatureDeltaF, double? MaxTreadDeltaPercent);
public sealed record TestDayCornerChange(TestDayBands? TemperatureF, TestDayBands? TreadPercent);

public sealed record TestDayLap(int Lap, int? RunLap, double? LapTimeSeconds, bool Eligible, bool Plotted,
    IReadOnlyList<string> ExclusionReasons, string Label);

/// <summary>The one suggested next step, with the rule a later run must meet.</summary>
public sealed record TestDayNextChange(string Kind, string Title, string Instruction, IReadOnlyList<int> BasedOnRuns,
    IReadOnlyList<TestDayNextChangeSetting> Changes, TestDayRulesetStep? RulesetStep, TestDaySuccessRule SuccessRule,
    IReadOnlyList<string> Evidence, string? Note);
public sealed record TestDayNextChangeSetting(string Field, string Label, string? From, string? To);
public sealed record TestDayRulesetStep(string? RulesetId, string? SettingId, string? Direction, string? Instruction,
    string? ExpectedEffect, string? Risk, bool StepVerified);
public sealed record TestDaySuccessRule(string Direction, int? ReferenceRun, double? ReferenceMedianSeconds, string? WindowLabel,
    int? FirstRunLap, int? LastRunLap, double? ThresholdSeconds, int? MinimumCleanLaps, string Text);
