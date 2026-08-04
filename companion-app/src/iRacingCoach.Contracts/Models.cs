using System.Text.Json.Serialization;
using System.Runtime.InteropServices;

namespace iRacingCoach.Contracts;

public static class WindowsKnownFolders
{
    private static readonly Guid DocumentsFolderId = new("FDD39AD0-238F-46AF-ADB4-6C85480369C7");

    public static string Documents
    {
        get
        {
            if (OperatingSystem.IsWindows() && SHGetKnownFolderPath(DocumentsFolderId, 0, IntPtr.Zero, out var pointer) == 0)
            {
                try
                {
                    var resolved = Marshal.PtrToStringUni(pointer);
                    if (!string.IsNullOrWhiteSpace(resolved)) return resolved;
                }
                finally
                {
                    Marshal.FreeCoTaskMem(pointer);
                }
            }
            return Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
        }
    }

    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    private static extern int SHGetKnownFolderPath([MarshalAs(UnmanagedType.LPStruct)] Guid folderId, uint flags, IntPtr token, out IntPtr path);
}

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
    double? BestCleanLapSeconds = null);

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
    double? TireStressProxy);

public sealed record AnalysisLapTrace(
    int Lap,
    double? LapTimeSeconds,
    bool Complete,
    string FlagState,
    double? GreenFraction,
    double? CautionFraction,
    double? PitTimeSeconds,
    IReadOnlyList<AnalysisTracePoint> Points);

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

public sealed record AnalysisPitStop(
    double? ServiceSeconds,
    double? FuelAddedGallons,
    double? EstimatedFuelLapsRemaining,
    IReadOnlyList<string> TiresChanged,
    double? LeftFrontTireWearPercent,
    double? RightFrontTireWearPercent,
    double? LeftRearTireWearPercent,
    double? RightRearTireWearPercent);

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
    AnalysisPitStop? PitStop = null);

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
    IReadOnlyList<string> Limitations);

public sealed record AnalysisDamage(
    int PitRoadEpisodes,
    int TowEpisodes,
    int RepairEpisodes,
    int FastRepairUses,
    double? PitRoadTimeSeconds,
    double? RepairWorkSeconds,
    IReadOnlyList<string> Limitations);

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
    IReadOnlyList<RaceGrade> Grades);

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
        WindowsKnownFolders.Documents,
        "iRacing Coach");

    public string CoachHome { get; set; } = DefaultCoachHome;
    public string IRacingRoot { get; set; } = Path.Combine(WindowsKnownFolders.Documents, "iRacing");
    public string IRacingInstallRoot { get; set; } = ResolveDefaultIRacingInstallRoot();
    // In-memory migration bridge only. JsonSettingsStore reads the legacy
    // property explicitly; this model can never serialize it again.
    [JsonIgnore]
    public string Garage61ApiKey { get; set; } = string.Empty;
    public bool FirstRunComplete { get; set; }
    public int SettingsSchemaVersion { get; set; } = 4;
    public Dictionary<string, string> CoachThreadIds { get; set; } = new(StringComparer.OrdinalIgnoreCase);
    [JsonIgnore]
    public string PythonPath => ResolvePackagedExecutable("python", "python.exe");
    public bool LaunchAtSignIn { get; set; }
    public bool UseReducedMotion { get; set; }
    public bool DiagnosticIncludeConfounded { get; set; }
    public LiveMonitorLayout LiveMonitor { get; set; } = new();

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

    private static string ResolveDefaultIRacingInstallRoot()
    {
        var candidates = new[]
        {
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), "iRacing"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "iRacing")
        };
        return candidates.FirstOrDefault(Directory.Exists) ?? candidates[0];
    }
}

public enum LiveMonitorMetricSource { Recorded, Calculated, Coach }
public enum LiveMonitorDisplayStyle { Number, Gauge, Bar, Trend, Status }
public enum LiveMonitorTrendDuration { Seconds15, Seconds30, Seconds60, OneLap, ThreeLaps }
public enum LiveGapTrend { Closing, Stable, Growing, Stale, Unavailable }
public enum LiveCuePriority { Information = 0, Environment = 10, Coaching = 20, Pace = 30, Traffic = 40, PitService = 50, Strategy = 60, Critical = 100 }
public enum LiveCueSuppressionReason { None, SafeGlanceDelay, Caution, PitCycle, DifferentLap, StaleTelemetry, NoBaseline, Paused }

public sealed class LiveMonitorLayout
{
    public const string FactoryDefaultId = "factory-default";
    public bool Visible { get; set; }
    public string ActiveLayoutId { get; set; } = FactoryDefaultId;
    public bool IsLocked { get; set; } = true;
    public List<LiveMonitorNamedLayout> UserLayouts { get; set; } = [];
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
    double? LastLapSeconds);

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
    int SourceTickRate = 0);

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
    string Confidence);

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
    string ClientVersion = "0.11.1");

public sealed record BackendHealthResult(
    bool Ok,
    string ServerName,
    string ServerVersion,
    string ProtocolVersion,
    int ToolCount,
    TimeSpan Elapsed,
    string? Error = null);
