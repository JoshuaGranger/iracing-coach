using System.Text.Json.Serialization;

namespace iRacingCoach.Contracts;

public enum EvidenceKind
{
    Measured,
    Derived,
    Inferred,
    Proxy,
    Unavailable
}

public static class EvidenceKindExtensions
{
    public static string Tag(this EvidenceKind kind) => kind switch
    {
        EvidenceKind.Measured => "[M]",
        EvidenceKind.Derived => "[D]",
        EvidenceKind.Inferred => "[I]",
        EvidenceKind.Proxy => "[P]",
        _ => "[U]"
    };

    public static string Css(this EvidenceKind kind) => kind.ToString().ToLowerInvariant();
}

public sealed record HealthItem(string Id, string Label, string State, string Detail, bool IsPrimary = false);

public sealed record RecoverableAppError(
    string Id,
    string Scope,
    string Message,
    DateTimeOffset OccurredUtc)
{
    public string SupportDetails => $"Error {Id}\nArea: {Scope}\nTime: {OccurredUtc:O}\nApp: iRacing Coach";
}

public sealed record RecentRace(
    string Id,
    string Track,
    string Layout,
    string Car,
    string Date,
    string SetupType,
    string Status,
    string Summary,
    bool Interrupted,
    bool Analyzed,
    int StartPosition,
    int FinishPosition,
    string CarPath = "",
    string AnalysisPath = "",
    string SourcePath = "",
    string StartTimeUtc = "",
    string EventKey = "",
    string SessionType = "Race",
    string EventScope = "",
    int FileCount = 1,
    string Series = "",
    string Season = "",
    string Selector = "",
    RaceOverview? Overview = null)
{
    public bool IsRace => string.Equals(SessionType, "Race", StringComparison.OrdinalIgnoreCase);
    public bool IsQualifying => SessionType?.Contains("Qual", StringComparison.OrdinalIgnoreCase) == true;
    public bool Reconnected => FileCount > 1;
    public string EffectiveSelector => string.IsNullOrWhiteSpace(Selector) ? Id ?? string.Empty : Selector;
}

public sealed record RaceOverview(
    int RecordedLaps = 0,
    int GreenLaps = 0,
    int CautionLaps = 0,
    int PitStops = 0,
    int Runs = 0,
    int LongestGreenRun = 0,
    double? PaceSlopeSecondsPerLap = null,
    double? PaceConsistencyPercent = null,
    double? LowestTireRemainingPercent = null,
    string LowestTireName = "",
    double? ControlLoadChangePercent = null,
    double? FuelUsedGallons = null,
    double? BestCleanLapSeconds = null,
    int ScheduledLaps = 0,
    double ScheduledMinutes = 0,
    int DeclaredLapLimit = 0,
    double DeclaredTimeLimitMinutes = 0);

public sealed record RaceEventGroup(
    string Id,
    string Track,
    string Layout,
    string Car,
    string Date,
    string SetupType,
    string EventScope,
    IReadOnlyList<RecentRace> Sessions)
{
    public bool Reconnected => Sessions?.OfType<RecentRace>().Any(session => session.Reconnected) == true;
    public bool Analyzed => Sessions?.OfType<RecentRace>().Any(session => session.Analyzed) == true;
}

public enum RaceBrowserFilter
{
    All,
    Official,
    HostedLeague,
    Ai,
    Fixed,
    Open,
    Analyzed,
    NeedsAnalysis
}

public sealed record InstalledCar(string Id, string Name, string Path, string Source);
public sealed record InstalledTrack(string Id, string Name, string Path, string Source);
public sealed record SetupField(string Key, string Label, string Group, string Value);

public sealed record LocalSetup(
    string Id,
    string Name,
    string Car,
    string Track,
    string Role,
    string StoPath,
    string Fingerprint,
    string PairStatus,
    string Note,
    IReadOnlyList<SetupField> Fields);

public sealed record EvidenceText(EvidenceKind Kind, string Text)
{
    public string Tag => Kind.Tag();
}

public sealed record RaceAction(string Label, EvidenceText Claim);

public sealed record CornerCoachingRow(
    string Zone,
    EvidenceText Early,
    EvidenceText Middle,
    EvidenceText Late,
    EvidenceText Groove);

public sealed record RaceTrigger(string Label, EvidenceText Claim);

public sealed record RaceCard(
    string Title,
    EvidenceText BottomLine,
    IReadOnlyList<RaceAction> Actions,
    IReadOnlyList<CornerCoachingRow> Corners,
    IReadOnlyList<RaceTrigger> Triggers,
    IReadOnlyList<EvidenceText> Appendix);

public sealed record AnalysisTracePoint(
    double LapPercent,
    double? SessionTimeSeconds,
    double? SpeedMph,
    double? SpeedMinimumMph,
    double? SpeedMaximumMph,
    double? Throttle,
    double? ThrottleMinimum,
    double? Brake,
    double? BrakeMean,
    double? SteeringRadians,
    double? SteeringPeakRadians,
    double? SlipAngleDegrees,
    int? Gear,
    double? Rpm,
    double? YawRateDegreesPerSecond,
    double? LateralG,
    double? LongitudinalG,
    double? Latitude,
    double? Longitude,
    double? TireStressProxy,
    IReadOnlyDictionary<string, double>? AdditionalSignals = null);

public sealed record AnalysisTraceSignal(
    string Id,
    string Name,
    string Unit,
    string Category,
    EvidenceKind Evidence,
    string Description,
    IReadOnlyList<string> SourceChannels);

public sealed record AnalysisLapConditions(
    string? Sky,
    double? TrackTemperatureF,
    double? AirTemperatureF,
    double? WindSpeedMph,
    double? WindDirectionDegrees,
    double? RelativeHumidityPercent,
    double? FogPercent,
    double? AirPressureInHg,
    double? AirDensityPoundsPerCubicFoot,
    double? PrecipitationPercent,
    double? TrackWetnessState,
    double? TrackUsagePercent,
    string? TrackUsage,
    bool? WeatherDeclaredWet);

public sealed record AnalysisLapTrace(
    int Lap,
    double? LapTimeSeconds,
    bool Complete,
    string FlagState,
    double? GreenFraction,
    double? CautionFraction,
    double? PitTimeSeconds,
    IReadOnlyList<AnalysisTracePoint> Points,
    IReadOnlyList<string>? FlagStates = null,
    bool PitEntry = false,
    bool PitExit = false,
    double? FuelUsedGallons = null,
    AnalysisLapConditions? Conditions = null,
    bool ComparisonEligible = true,
    string ExclusionReason = "");

public sealed record AnalysisLap(
    int Lap,
    double? LapTimeSeconds,
    bool Complete,
    string FlagState,
    double? GreenFraction,
    double? CautionFraction,
    double? PitTimeSeconds,
    int? StartPosition,
    int? EndPosition,
    bool Confounded,
    string ExclusionReason);

public static class AnalysisEligibility
{
    public static bool IsComparable(this AnalysisLapTrace trace) =>
        trace.ComparisonEligible &&
        trace.Complete &&
        trace.PitTimeSeconds.GetValueOrDefault() <= 0 &&
        trace.CautionFraction.GetValueOrDefault() <= .001 &&
        !IsCaution(trace.FlagState) &&
        !(trace.FlagStates ?? []).Any(IsCaution);

    public static bool IsComparable(this AnalysisLap lap) =>
        !lap.Confounded &&
        lap.Complete &&
        lap.PitTimeSeconds.GetValueOrDefault() <= 0 &&
        lap.CautionFraction.GetValueOrDefault() <= .001 &&
        !IsCaution(lap.FlagState);

    public static AnalysisRun? PitServiceFor(this AnalysisLapTrace trace, IReadOnlyList<AnalysisRun> runs, string direction)
    {
        var exact = runs.FirstOrDefault(run => run.Laps.Contains(trace.Lap));
        if (direction.Equals("in", StringComparison.OrdinalIgnoreCase) && exact?.PitStop is not null) return exact;
        if (direction.Equals("out", StringComparison.OrdinalIgnoreCase) && trace.PitEntry && trace.PitExit && exact?.PitStop is not null) return exact;
        return runs
            .Where(run => run.PitStop is not null && run.Laps.Count > 0 && trace.Lap >= run.Laps.Max() && trace.Lap - run.Laps.Max() <= 2)
            .OrderByDescending(run => run.Laps.Max())
            .FirstOrDefault();
    }

    private static bool IsCaution(string? flagState) =>
        flagState?.Contains("yellow", StringComparison.OrdinalIgnoreCase) == true ||
        flagState?.Contains("caution", StringComparison.OrdinalIgnoreCase) == true;
}

public sealed record AnalysisTireBands(
    double? Outer,
    double? Middle,
    double? Inner);

public sealed record AnalysisTireCondition(
    string Corner,
    double? AverageWearPercent,
    AnalysisTireBands WearPercent,
    AnalysisTireBands CarcassTemperatureF,
    AnalysisTireBands SurfaceTemperatureF,
    double? PressurePsi,
    string PressureKind);

public sealed record AnalysisPitStop(
    double? ServiceSeconds,
    double? FuelAddedGallons,
    double? EstimatedFuelLapsRemaining,
    IReadOnlyList<string> TiresChanged,
    double? LeftFrontTireWearPercent,
    double? RightFrontTireWearPercent,
    double? LeftRearTireWearPercent,
    double? RightRearTireWearPercent,
    double? DamageRepairedSeconds = null,
    double? PenaltyServedSeconds = null,
    IReadOnlyDictionary<string, AnalysisTireCondition>? TireConditions = null,
    double? PitCyclePositionChange = null,
    double? RaceLapsRemainingAfterStop = null);

public sealed record AnalysisRun(
    int Number,
    IReadOnlyList<int> Laps,
    int GreenLaps,
    int CautionLaps,
    double? FuelUsedGallons,
    double? PaceSlopeSecondsPerLap,
    string TireEndpoint,
    bool ComparisonEligible,
    string Status,
    double? EarlyAverageLapSeconds,
    double? LateAverageLapSeconds,
    double? EarlyToLateDeltaSeconds,
    double? TireRemainingPercent,
    string TireName,
    double? EarlyBrakeVsLatePercent,
    double? EarlySteerVsLatePercent,
    AnalysisPitStop? PitStop = null,
    int CoachingReferenceLapCount = 0,
    bool? EndedUnderCaution = null);

public sealed record AnalysisStrategy(
    double? GreenFuelGallonsPerLap,
    double? CautionFuelGallonsPerLap,
    double? AllGreenRangeLaps,
    int? MinimumStopsAllGreen,
    IReadOnlyList<int> EqualStintPitTargets,
    double? ReserveFuelGallons,
    double? ReserveGreenLaps,
    string Classification,
    IReadOnlyList<string> Assumptions,
    IReadOnlyList<string> Limitations,
    RacePlanDecisionView? FuelPlan = null);

public sealed record AnalysisIncident(
    int Lap,
    int Points,
    double? SessionTimeSeconds = null,
    double? CountBefore = null,
    double? CountAfter = null,
    string? SourceChannel = null,
    string? EventType = null,
    string? ContactTarget = null,
    string? TrackLocation = null,
    bool? OnPitRoad = null,
    double? SpeedMph = null,
    double? YawRateDegreesPerSecond = null,
    double? SlipAngleDegrees = null);

public sealed record AnalysisDamage(
    int PitRoadEpisodes,
    int TowEpisodes,
    int RepairEpisodes,
    int FastRepairUses,
    double? PitRoadTimeSeconds,
    double? RepairWorkSeconds,
    IReadOnlyList<string> Limitations,
    IReadOnlyList<AnalysisIncident>? Incidents = null);

public sealed record TrackShapePoint(double LapPercent, double X, double Y);
public sealed record TrackSegment(int Number, double StartPercent, double EndPercent, bool WrapsStartFinish, string Label);
public sealed record RaceGrade(
    string Key,
    string Label,
    string Grade,
    double? Score,
    EvidenceKind Evidence,
    string Explanation,
    string Improvement,
    string Limitation,
    bool Available = true,
    IReadOnlyList<string>? Inputs = null,
    string Calibration = "Strict local execution scale",
    string Provenance = "Deterministic local analysis");

public sealed record AnalysisVectorPoint(
    double X,
    double Y,
    double? LapPercent = null,
    int Observations = 0);

public sealed record AnalysisVectorLine(
    AnalysisVectorPoint A,
    AnalysisVectorPoint B);

public sealed record AnalysisGeometrySourceBounds(
    double MinimumX,
    double MaximumX,
    double MinimumY,
    double MaximumY);

public sealed record AnalysisGeometryTransform(
    AnalysisGeometrySourceBounds? SourceBounds,
    double? NormalizationScale)
{
    public bool IsUsable => SourceBounds is not null
        && NormalizationScale is > 0
        && double.IsFinite(NormalizationScale.Value)
        && double.IsFinite(SourceBounds.MinimumX)
        && double.IsFinite(SourceBounds.MaximumX)
        && double.IsFinite(SourceBounds.MinimumY)
        && double.IsFinite(SourceBounds.MaximumY)
        && SourceBounds.MaximumX >= SourceBounds.MinimumX
        && SourceBounds.MaximumY >= SourceBounds.MinimumY;

    public bool TryNormalize(double sourceX, double sourceY, out double normalizedX, out double normalizedY)
    {
        normalizedX = 0;
        normalizedY = 0;
        if (!IsUsable || !double.IsFinite(sourceX) || !double.IsFinite(sourceY)) return false;
        var bounds = SourceBounds!;
        var scale = NormalizationScale!.Value;
        normalizedX = (sourceX - bounds.MinimumX) / scale;
        normalizedY = (bounds.MaximumY - sourceY) / scale;
        return double.IsFinite(normalizedX) && double.IsFinite(normalizedY);
    }
}

public sealed record AnalysisGeometryObservation(
    string ObservationId,
    IReadOnlyList<string> SourceSha256,
    AnalysisGeometryTransform? Transform,
    string? GeometryFingerprint,
    DateTimeOffset? ObservedAt,
    IReadOnlyDictionary<string, double> Quality);

public sealed record AnalysisGeometryProvenance(
    string? SelectedObservationId,
    AnalysisGeometryTransform? NormalizationTransform,
    IReadOnlyList<AnalysisGeometryObservation> Observations);

public sealed record AnalysisTrackGeometryQuality(
    bool? MainLoopComplete,
    double? LapPercentCoverage,
    double? MaximumLapPercentGap,
    double? ClosureDistance);

public sealed record AnalysisTrackGeometry(
    string Status,
    string TrackConfigurationKey,
    string CoordinateSystem,
    IReadOnlyList<AnalysisVectorPoint> MainPath,
    IReadOnlyList<AnalysisVectorPoint> PitLane,
    IReadOnlyList<AnalysisVectorPoint> PitEntryPath,
    IReadOnlyList<AnalysisVectorPoint> PitExitPath,
    AnalysisVectorLine? StartFinishLine,
    AnalysisVectorLine? PitCommitmentLine,
    AnalysisVectorLine? PitMergeLine,
    IReadOnlyList<string> UnavailableReasons,
    IReadOnlyList<string> SourceSha256,
    IReadOnlyList<string>? ObservedSourceSha256 = null,
    AnalysisGeometryTransform? Transform = null,
    AnalysisGeometryProvenance? GeometryProvenance = null,
    AnalysisTrackGeometryQuality? Quality = null,
    string? GeometryHash = null);

public sealed record AnalysisReplayCoverage(
    string Channel,
    string Status,
    string? Reason,
    int? RecordedSegmentCount = null,
    int? SegmentCount = null,
    double? RecordedFraction = null,
    bool? AllSegmentsRecorded = null,
    int? TemporalGapCount = null);

public sealed record AnalysisReplayTemporalCoverage(
    string Status,
    int? RecordedFrameCount,
    int? ExpectedFrameCount,
    double? RecordedFraction,
    int? GapCount,
    double? LargestGapSeconds,
    double? StartSessionTimeSeconds,
    double? EndSessionTimeSeconds);

public sealed record AnalysisReplayParticipantCoverage(
    int CarIndex,
    string Status,
    int? RecordedFrameCount,
    int? TotalFrameCount,
    double? RecordedFraction,
    int? RecordedSegmentCount,
    int? SegmentCount,
    double? FirstSessionTimeSeconds,
    double? LastSessionTimeSeconds);

public sealed record AnalysisReplayParticipant(
    int CarIndex,
    string? CarNumber,
    int? ClassId,
    string? ClassName,
    string? CarName,
    string? DriverName,
    string? TeamName,
    bool IsPlayer,
    bool IsSpectator);

public sealed record AnalysisReplayCarState(
    int CarIndex,
    double? LapPercent,
    int? Lap,
    int? CompletedLaps,
    int? OverallPosition,
    int? ClassPosition,
    bool? OnPitRoad,
    int? TrackSurface,
    string? TrackSurfaceLabel,
    int? PaceFlags,
    double? LastLapSeconds,
    double? BestLapSeconds);

public sealed record AnalysisReplayPlayerTelemetry(
    int? IncidentPoints,
    int? DriverIncidentPoints,
    int? TeamIncidentPoints,
    int? TrackSurface,
    bool? OnPitRoad,
    bool? Towing,
    bool? RepairRequired,
    double? MandatoryRepairSeconds,
    double? OptionalRepairSeconds,
    double? SpeedMetersPerSecond,
    double? Throttle,
    double? Brake,
    double? SteeringWheelAngleRadians,
    int? Gear,
    double? Rpm,
    double? YawRateRadiansPerSecond,
    double? LateralAccelerationG,
    double? LongitudinalAccelerationG);

public sealed record AnalysisReplayObservedEvent(
    string Kind,
    string Label,
    string? SourceChannel,
    double? Delta);

public sealed record AnalysisReplayRepresentation(
    int? SourceFrameCount,
    int? DisplayFrameCount,
    double? SourceSampleRateHz,
    double? DisplaySampleRateHz,
    int? FrameBudget,
    bool? Decimated,
    double? RoutineIntervalSeconds,
    bool? KeyframesPreserved,
    int? DroppedKeyframeCount);

public sealed record AnalysisReplayFrame(
    double SessionTimeSeconds,
    string SessionState,
    long GlobalFlags,
    IReadOnlyList<string> GlobalFlagLabels,
    IReadOnlyList<AnalysisReplayCarState> Cars,
    AnalysisReplayPlayerTelemetry? PlayerTelemetry = null,
    IReadOnlyList<AnalysisReplayObservedEvent>? Events = null,
    bool GapBefore = false);

public sealed record AnalysisRaceReplay(
    string Status,
    IReadOnlyList<string> UnavailableReasons,
    IReadOnlyList<string> Limitations,
    IReadOnlyList<AnalysisReplayCoverage> Coverage,
    IReadOnlyList<AnalysisReplayParticipant> Participants,
    IReadOnlyList<AnalysisReplayFrame> Frames,
    double? SampleRateHz,
    int? PlayerCarIndex,
    string Interpolation,
    AnalysisReplayTemporalCoverage? TemporalCoverage = null,
    IReadOnlyList<AnalysisReplayParticipantCoverage>? ParticipantCoverage = null,
    AnalysisReplayRepresentation? Representation = null);

public sealed record AnalysisTireBandPrediction(
    double? RemainingPercent,
    double? LowPercent,
    double? HighPercent,
    double? WearRatePercentPerGreenLap,
    double? LapsRemainingToZero);

public sealed record AnalysisTireCornerPrediction(
    string Corner,
    IReadOnlyDictionary<string, AnalysisTireBandPrediction> Bands);

public sealed record AnalysisTireLearningPrediction(
    string Status,
    string? Reason,
    string? EvidenceClass,
    string? Confidence,
    int EligibleObservations,
    int MatchingSessions,
    double? LapsRemaining,
    double? PaceCostSeconds,
    double? PaceCostLowSeconds,
    double? PaceCostHighSeconds,
    double? PaceSlopeSecondsPerGreenLap,
    double? CapabilityPaceSeconds,
    double? CapabilityPaceLowSeconds,
    double? CapabilityPaceHighSeconds,
    IReadOnlyList<AnalysisTireCornerPrediction> Tires,
    string? ModelPath,
    int PersistentObservationCount,
    string? ModelVersion,
    string? ObservationSetFingerprint,
    int TotalObservations,
    int ExcludedObservations,
    double? EffectiveMatchedObservations,
    double? MedianFeatureDistance,
    int ComparableFeatureCount,
    IReadOnlyList<string> MatchedFeatures,
    IReadOnlyList<string> ExclusionReasons,
    string? MatchingScope,
    IReadOnlyDictionary<string, string> MatchingContext);

public sealed record AnalysisGarage61ReferenceLap(
    string Id,
    double? LapTimeSeconds,
    string SetupType,
    string ComparisonRole,
    bool TelemetryAvailable,
    string Driver,
    string Provider = "Garage61",
    DateTimeOffset? RetrievedAt = null,
    string? SourceSha256 = null,
    IReadOnlyList<string>? AvailableSignals = null,
    string? AlignmentStatus = null,
    bool? AlignmentUsable = null,
    int? AlignedBins = null,
    double? AlignmentCoverageFraction = null);

public sealed record AnalysisGarage61References(
    string Status,
    string? Reason,
    string? ComparisonScope,
    IReadOnlyList<AnalysisGarage61ReferenceLap> Laps,
    string Provider = "Garage61",
    DateTimeOffset? RetrievedAt = null,
    string? SourceSha256 = null,
    IReadOnlyList<string>? AvailableSignals = null,
    string? ComparisonStatus = null,
    string? ComparisonReason = null,
    string? SetupScope = null,
    int? UsableReferenceLaps = null,
    double? MedianCoverageFraction = null);

public sealed record AnalysisTechnicalMetric(
    string Label,
    string Value,
    EvidenceKind Evidence,
    string Detail = "",
    string Action = "",
    string Tone = "neutral",
    string Group = "");

public sealed record AnalysisTechnicalInsight(
    string Key,
    string Label,
    string Status,
    string Rating,
    string Takeaway,
    IReadOnlyList<AnalysisTechnicalMetric> Metrics,
    IReadOnlyList<string> Evidence,
    IReadOnlyList<string> UnavailableReasons);

public sealed record AnalysisWorkspace(
    int SchemaVersion,
    string AnalysisId,
    string Track,
    string Layout,
    string Car,
    string SetupType,
    string SessionType,
    int RecordedLaps,
    int ScheduledLaps,
    int PitStops,
    IReadOnlyList<AnalysisRun> Runs,
    IReadOnlyList<AnalysisLap> Laps,
    IReadOnlyList<AnalysisLapTrace> Traces,
    IReadOnlyList<TrackShapePoint> TrackShape,
    IReadOnlyList<TrackSegment> Segments,
    string GeometryMode,
    string TireStressDefinition,
    string StrategyStatus,
    string DamageStatus,
    AnalysisStrategy Strategy,
    AnalysisDamage Damage,
    string SetupFingerprint,
    string DataConfidence,
    double BackendElapsedMilliseconds,
    string OverallGrade,
    IReadOnlyList<RaceGrade> Grades,
    IReadOnlyList<double>? SectorStartPercents = null,
    IReadOnlyList<AnalysisTraceSignal>? AdditionalTraceSignals = null,
    AnalysisTrackGeometry? VectorGeometry = null,
    AnalysisRaceReplay? Replay = null,
    AnalysisTireLearningPrediction? TirePrediction = null,
    AnalysisGarage61References? Garage61References = null,
    IReadOnlyList<AnalysisTechnicalInsight>? TechnicalInsights = null,
    TuningSessionIdentity? TuningIdentity = null,
    TuningMapView? TuningMap = null,
    double ScheduledMinutes = 0,
    int DeclaredLapLimit = 0,
    double DeclaredTimeLimitMinutes = 0);

public sealed class JobItem
{
    public required string Id { get; init; }
    public required string Title { get; init; }
    public required string CanonicalKey { get; init; }
    public string Stage { get; set; } = "Queued";
    public string Status { get; set; } = "queued";
    public int Progress { get; set; }
    public TimeSpan Elapsed { get; set; }
    public bool Cancellable { get; set; } = true;
    public string? ArtifactLabel { get; set; }
}

public sealed class CompanionSettings
{
    public static string DefaultCoachHome => Path.Combine(
        WindowsCompanionPathProvider.Instance.Documents,
        "iRacing Coach");

    public CompanionSettings() : this(WindowsCompanionPathProvider.Instance) { }

    public CompanionSettings(ICompanionPathProvider pathProvider)
    {
        ArgumentNullException.ThrowIfNull(pathProvider);
        CoachHome = Path.Combine(pathProvider.Documents, "iRacing Coach");
        IRacingRoot = Path.Combine(pathProvider.Documents, "iRacing");
        IRacingInstallRoot = ResolveDefaultIRacingInstallRoot(pathProvider);
        LocalStateRootOverride = Path.Combine(pathProvider.LocalApplicationData, "iRacingCoach");
    }

    public string CoachHome { get; set; }
    public string IRacingRoot { get; set; }
    public string IRacingInstallRoot { get; set; }
    // In-memory migration bridge only. JsonSettingsStore reads the legacy
    // property explicitly; this model can never serialize it again.
    [JsonIgnore]
    public string Garage61ApiKey { get; set; } = string.Empty;
    public bool FirstRunComplete { get; set; }
    public int SettingsSchemaVersion { get; set; } = 5;
    public Dictionary<string, string> CoachThreadIds { get; set; } = new(StringComparer.OrdinalIgnoreCase);
    [JsonIgnore]
    public string PythonPath => ResolvePackagedExecutable("python", "python.exe");
    public bool LaunchAtSignIn { get; set; }
    public bool UseReducedMotion { get; set; }
    public string ThemeColor { get; set; } = "mint";
    public string CustomThemeColor { get; set; } = "#5CE8C3";
    public bool DiagnosticIncludeConfounded { get; set; }
    public LiveMonitorLayout LiveMonitor { get; set; } = new();
    public AnalysisTraceLayout RaceAnalysisTraces { get; set; } = new();
    public AnalysisTraceLayoutSet RaceAnalysisTraceLayouts { get; set; } = new();

    [JsonIgnore] public string ArchiveRoot => Path.Combine(CoachHome, "data");
    [JsonIgnore] public string SetupsRoot => Path.Combine(CoachHome, "setups");
    [JsonIgnore] public string SettingsPath => Path.Combine(CoachHome, "settings.json");
    [JsonIgnore] public string? LocalStateRootOverride { get; set; }
    [JsonIgnore] public string LocalStateRoot => string.IsNullOrWhiteSpace(LocalStateRootOverride)
        ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "iRacingCoach")
        : Path.GetFullPath(LocalStateRootOverride);
    [JsonIgnore] public string LogsRoot => Path.Combine(LocalStateRoot, "logs");

    private static string ResolvePackagedExecutable(string directory, string executable)
    {
        var packaged = Path.Combine(AppContext.BaseDirectory, directory, executable);
        if (File.Exists(packaged)) return packaged;
        var configured = Environment.GetEnvironmentVariable("IRACING_COACH_PYTHON");
        return string.IsNullOrWhiteSpace(configured) ? Path.GetFileNameWithoutExtension(executable) : configured;
    }

    private static string ResolveDefaultIRacingInstallRoot(ICompanionPathProvider pathProvider)
    {
        var candidates = new List<string>
        {
            Path.Combine(pathProvider.ProgramFilesX86, "iRacing"),
            Path.Combine(pathProvider.ProgramFiles, "iRacing")
        };
        foreach (var driveRoot in pathProvider.FixedDriveRoots)
        {
            candidates.Add(Path.Combine(driveRoot, "Games", "iRacing"));
            candidates.Add(Path.Combine(driveRoot, "iRacing"));
            candidates.Add(Path.Combine(driveRoot, "SteamLibrary", "steamapps", "common", "iRacing"));
        }
        return candidates.FirstOrDefault(Directory.Exists) ?? candidates[0];
    }
}

public sealed class AnalysisTraceLayout
{
    public List<AnalysisTraceRow> Rows { get; set; } = [];
}

public sealed class AnalysisTraceRow
{
    public string Id { get; set; } = $"trace-row-{Guid.NewGuid():N}";
    public string PrimarySignalId { get; set; } = "speed";
    public string SecondarySignalId { get; set; } = string.Empty;
}

public enum LiveMonitorMetricSource { Recorded, Calculated, Coach }
public enum LiveMonitorDisplayStyle { Number, Gauge, Bar, Trend, Status }
public enum LiveMonitorTrendDuration { Seconds15, Seconds30, Seconds60, OneLap, ThreeLaps }
public enum LiveMonitorTrendShape { Continuous, Step }
public enum LiveGapTrend { Closing, Stable, Growing, Stale, Unavailable }
public enum LiveCuePriority { Information = 0, Environment = 10, Coaching = 20, Pace = 30, Traffic = 40, PitService = 50, Strategy = 60, Critical = 100 }
public enum LiveCueSuppressionReason { None, SafeGlanceDelay, Caution, PitCycle, DifferentLap, StaleTelemetry, NoBaseline, Paused }

// Stable visualization ordinals for categorical live telemetry. They are not
// measurements or severity scores; chart renderers should use a stepped path.
public enum LiveFlagTrendState { Green = 0, Blue = 1, White = 2, Checkered = 3, Yellow = 4, Black = 5, Red = 6, Other = 7 }
public enum LiveTirePhaseTrendState { Early = 0, Middle = 1, Late = 2 }

public sealed class LiveMonitorLayout
{
    public const string FactoryDefaultId = "factory-default";
    public bool Visible { get; set; }
    public string ActiveLayoutId { get; set; } = FactoryDefaultId;
    public bool IsLocked { get; set; } = true;
    public List<LiveMonitorNamedLayout> UserLayouts { get; set; } = [];
    public bool BuiltInDashboardsInitialized { get; set; }
    [JsonIgnore] public double? Left { get; set; }
    [JsonIgnore] public double? Top { get; set; }
    [JsonIgnore] public double OverallScale { get; set; } = 1;
    public bool SafeGlanceEnabled { get; set; } = true;
    public bool ReopenOnConnect { get; set; }
    public int HistoryLaps { get; set; } = 3;
    [JsonIgnore] public string MonitorDeviceName { get; set; } = string.Empty;
    [JsonIgnore] public DateTimeOffset? PlacementRecoveredAt { get; set; }
    public string GlobalHotkey { get; set; } = string.Empty;
}

public sealed class LiveMonitorNamedLayout
{
    public string Id { get; set; } = $"layout-{Guid.NewGuid():N}";
    public string Name { get; set; } = "Custom";
    public int Rows { get; set; } = 2;
    public int Columns { get; set; } = 3;
    public List<LiveMonitorTile> Tiles { get; set; } = [];
}

public sealed class LiveMonitorTile
{
    public string Id { get; set; } = $"tile-{Guid.NewGuid():N}";
    public string MetricId { get; set; } = "position";
    public int Row { get; set; }
    public int Column { get; set; }
    public int RowSpan { get; set; } = 1;
    public int ColumnSpan { get; set; } = 1;
    public LiveMonitorDisplayStyle DisplayStyle { get; set; } = LiveMonitorDisplayStyle.Number;
    public string Unit { get; set; } = string.Empty;
    public int Precision { get; set; } = 1;
    public LiveMonitorTrendDuration TrendDuration { get; set; } = LiveMonitorTrendDuration.Seconds30;
    public string Accent { get; set; } = "default";
    public bool HighlightAbsIntervention { get; set; }
}

public sealed record LiveGapState(
    string Label,
    double? Seconds,
    LiveGapTrend Trend,
    DateTimeOffset SourceTimestamp,
    TimeSpan DataAge,
    EvidenceKind Evidence,
    double Confidence,
    string Source,
    string UnavailableReason = "");

public sealed record LiveTracePoint(
    DateTimeOffset At,
    int? Lap,
    double? LapDistancePercent,
    double? SpeedMph,
    double? Throttle,
    double? Brake,
    double? SteeringWheelAngleRadians,
    int? Gear,
    double? Rpm,
    double? YawRateDegreesPerSecond,
    double? LateralAccelerationG,
    double? LongitudinalAccelerationG,
    double? Latitude,
    double? Longitude,
    double? LastLapSeconds,
    LiveMetricHistoryFrame Metrics = default);

// Retained beside the high-rate driving channels without a dictionary or
// boxed value per sample. Nullable fields preserve unavailable data as missing.
public readonly record struct LiveMetricHistoryFrame(
    double? AirTemperatureC,
    double? AheadGapSeconds,
    double? BehindGapSeconds,
    double? BrakeBiasPercent,
    int? ClassPosition,
    LiveCuePriority? CoachCuePriority,
    LiveFlagTrendState? FlagState,
    double? FuelLiters,
    double? FuelLapsRemaining,
    int? LapsRemaining,
    double? LeaderGapSeconds,
    double? LeaderLastLapSeconds,
    double? MandatoryRepairSeconds,
    bool? OnPitRoad,
    double? OptionalRepairSeconds,
    double? PaceMidpointSeconds,
    int? PitWindowLaps,
    int? OverallPosition,
    LiveTirePhaseTrendState? TirePhase,
    double? TrackTemperatureC,
    bool? BrakeAbsActive = null,
    double? BrakeAbsCutPercent = null);

public sealed record LivePaceTarget(
    double? MinimumSeconds,
    double? MaximumSeconds,
    string Source,
    string TirePhase,
    EvidenceKind Evidence,
    double Confidence,
    DateTimeOffset SourceTimestamp,
    string UnavailableReason = "");

public sealed record LivePitRecommendation(
    int? WindowOpensInLaps,
    int? WindowClosesInLaps,
    int? FuelHardLimitLaps,
    string Recommendation,
    EvidenceKind Evidence,
    double Confidence,
    DateTimeOffset SourceTimestamp,
    string UnavailableReason = "");

public sealed record LiveDriverCue(
    string Message,
    LiveCuePriority Priority,
    EvidenceKind Evidence,
    double Confidence,
    DateTimeOffset SourceTimestamp,
    LiveCueSuppressionReason SuppressionReason = LiveCueSuppressionReason.None,
    string Basis = "");

public sealed record SafeGlanceState(
    bool Enabled,
    bool IsGlanceOpportunity,
    bool UrgentOverride,
    LiveCueSuppressionReason SuppressionReason,
    DateTimeOffset EvaluatedAt);

public sealed record LiveRaceSnapshot(
    bool Connected,
    string ConnectionLabel,
    string Flag,
    int? Lap,
    int? LapsRemaining,
    int? OverallPosition,
    int? ClassPosition,
    LiveGapState LeaderGap,
    LiveGapState ClassLeaderGap,
    LiveGapState AheadGap,
    LiveGapState BehindGap,
    double? LastLapSeconds,
    double? LeaderLastLapSeconds,
    double? LastLapPaceDifferenceSeconds,
    LivePaceTarget PaceTarget,
    LivePitRecommendation Pit,
    int? GreenLapsOnTires,
    int? TotalLapsOnTires,
    int? CautionLapsOnTires,
    string TirePhase,
    double? FuelLapsRemaining,
    double? TrackTemperatureC,
    double? TrackTemperatureChangeC,
    double? AirTemperatureC,
    double? BrakeBiasPercent,
    bool OnPitRoad,
    double? MandatoryRepairSeconds,
    double? OptionalRepairSeconds,
    string PenaltyStatus,
    LiveDriverCue PrimaryCue,
    LiveDriverCue? SecondaryCue,
    SafeGlanceState SafeGlance,
    DateTimeOffset SourceTimestamp,
    TimeSpan DataAge,
    string Source,
    double Confidence,
    string UnavailableReason = "",
    double? SpeedMph = null,
    double? Throttle = null,
    double? Brake = null,
    double? SteeringWheelAngleRadians = null,
    int? Gear = null,
    double? Rpm = null,
    double? YawRateDegreesPerSecond = null,
    double? LateralAccelerationG = null,
    double? LongitudinalAccelerationG = null,
    double? LapDistancePercent = null,
    double? Latitude = null,
    double? Longitude = null,
    double? FuelLiters = null,
    double? FuelLevelPercent = null);

public sealed record LiveMonitorState(
    LiveRaceSnapshot Snapshot,
    LiveMonitorLayout Layout,
    bool CoachingPaused,
    long FramesRead,
    long DroppedFrames,
    double RenderLatencyMs,
    DateTimeOffset UpdatedAt,
    IReadOnlyList<LiveTracePoint>? History = null,
    int SourceTickRate = 0,
    long SessionEpoch = 0);

public sealed record TrayApplicationState(
    bool MainWindowVisible,
    bool LiveMonitorVisible,
    bool TelemetryConnected,
    bool CoachingPaused,
    string Tooltip,
    LiveCuePriority AlertPriority);

public sealed record StrategyScenario(
    string Label,
    string Stops,
    string Window,
    string Fuel,
    string Note,
    EvidenceKind Evidence);

public sealed record RacePlanCautionScenario(
    double ObservedCautionFraction,
    double MixedBurnLitersPerLap,
    double RangeLaps,
    int MinimumStops,
    string EvidenceClass,
    string Limitation);

public sealed record RacePlanDecisionView(
    int DecisionVersion,
    string Status,
    double? ScheduledLaps,
    double? GreenBurnLitersPerLap,
    double? MaximumStartFuelLiters,
    double ReserveGreenLaps,
    double? ReserveFuelLiters,
    double? UsableFuelLiters,
    double? AllGreenRangeLaps,
    int? MinimumStops,
    int? Stints,
    double? FinalStintMarginLaps,
    IReadOnlyList<double> EqualStintPitTargets,
    RacePlanCautionScenario? CautionScenario,
    bool NoStopLanguagePermitted,
    bool ReDecidable,
    bool AppliesToRequestedDistance,
    string Classification,
    IReadOnlyList<string> Assumptions,
    IReadOnlyList<string> Limitations,
    IReadOnlyDictionary<string, string> ExtensionData,
    bool IsLegacy = false)
{
    public bool IsUsable =>
        Status.Equals("usable", StringComparison.Ordinal) &&
        AppliesToRequestedDistance;

    public string UnavailableReason => Limitations.FirstOrDefault()
        ?? "The authoritative fuel decision is unavailable.";
}

public sealed record RacePlanBriefing(
    string Track,
    string Car,
    string SetupType,
    int ScheduledLaps,
    double? GreenFuelGallonsPerLap,
    double? CautionFuelGallonsPerLap,
    string FuelRange,
    string StopCount,
    IReadOnlyList<int> PitTargets,
    string TireGuidance,
    IReadOnlyList<RaceAction> Priorities,
    IReadOnlyList<CornerCoachingRow> Corners,
    IReadOnlyList<RaceTrigger> Triggers,
    IReadOnlyList<string> Assumptions,
    string Confidence,
    string DistanceLabel = "Finish constraint unresolved",
    bool DistanceIsEstimated = false,
    RacePlanDecisionView? FuelPlan = null);

public sealed record SetupPackageView(
    string PackageId,
    string Status,
    string Source,
    string Fingerprint,
    string Donor,
    string Confirmation,
    IReadOnlyList<string> Risks,
    IReadOnlyList<string> BaselineChecks,
    string PackagePath = "",
    string Car = "",
    string Track = "",
    string Season = "",
    string Purpose = "Race",
    string DonorReason = "",
    bool SimulatorSetupProduced = false,
    bool SourceFilesModified = false);

public sealed record TuningExperimentView(
    string ExperimentId,
    string System,
    string Change,
    string PredictedEffect,
    string Risk,
    string RollbackFingerprint,
    IReadOnlyList<string> Verify,
    string Outcome);

public sealed record Garage61Connection(bool Configured, bool Available, string Status, string Message);

public sealed record CoachEngineConnection(
    bool Installed,
    bool Running,
    bool ChatGptConnected,
    string Status,
    string Message,
    string RuntimeVersion = "",
    string? AccountLabel = null,
    string? LoginUrl = null,
    string? VerificationCode = null);

public sealed record DiagnosticFact(string Label, string Value, string State = "neutral");

public sealed record BackendConfiguration(
    string PowerShellPath,
    string LauncherPath,
    string PythonPath,
    string IRacingRoot,
    string ArchiveRoot,
    string CoachHomeRoot,
    string IRacingInstallRoot = "",
    string ClientVersion = "0.16.0",
    string LocalStateRoot = "",
    string UserProfileRoot = "",
    string TemporaryRoot = "",
    bool NetworkAllowed = true);

public sealed record BackendHealthResult(
    bool Ok,
    string ServerName,
    string ServerVersion,
    string ProtocolVersion,
    int ToolCount,
    TimeSpan Elapsed,
    string? Error = null);
