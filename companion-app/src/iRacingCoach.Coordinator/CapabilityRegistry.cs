using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

public enum CapabilityClass
{
    SupportedNow,
    TemporarilyUnavailable,
    ConditionallyApplicable,
    PermanentlyUnsupported,
    NotImplemented
}

public enum ProductCapability
{
    Home,
    LiveTelemetry,
    LiveMonitor,
    RaceAnalysis,
    RacePlanning,
    SetupLibrary,
    ProgressiveTuning,
    Connections,
    Settings,
    BackupAndMigration,
    RawTelemetryRelocation,
    ChatGptCoaching,
    Garage61Connection,
    Garage61GlobalComparison,
    QualifyingAnalysis,
    SetupComparison,
    SetupPackageBuilder,
    SetupExperimentTab,
    TrackMap,
    ExactTargetTrace,
    PushToPass,
    WeightJacker,
    WetWeatherAnalysis,
    MulticlassAnalysis,
    LiveLeaderGap,
    LiveAheadGap,
    LiveBehindGap,
    LivePaceRange,
    LivePitWindow,
    LiveFuelLimit,
    LiveLastLap,
    LiveLeaderLastLap,
    LiveTirePhase,
    LiveWeather,
    LiveBrakeBias,
    LiveRepair,
    OfficialEventFilter,
    HostedLeagueEventFilter,
    AiEventFilter,
    FixedSetupFilter,
    OpenSetupFilter,
    AnalyzedFilter,
    NeedsAnalysisFilter
}

public sealed record CapabilityDefinition(
    ProductCapability Id,
    string Name,
    string UserValue,
    string DataSource,
    CapabilityClass Classification,
    string AppliesWhen,
    string Validation,
    string TemporaryFailureStates,
    string SupportedFallback,
    string ProductionVisibilityDecision,
    bool ProductionVisible);

public sealed record CapabilityDecision(
    CapabilityDefinition Definition,
    CapabilityClass Classification,
    bool Visible,
    string? StateMessage = null,
    string? RecoveryAction = null)
{
    public bool Active => Visible && Classification is CapabilityClass.SupportedNow or CapabilityClass.ConditionallyApplicable;
}

public sealed class CapabilityContext
{
    public bool HasRaceRecordings { get; init; }
    public bool HasOpenAnalyzedRace { get; init; }
    public bool HasSetupFiles { get; init; }
    public bool HasMissingRawTelemetry { get; init; }
    public bool LiveConnected { get; init; }
    public bool HasLeaderGap { get; init; }
    public bool HasAheadGap { get; init; }
    public bool HasBehindGap { get; init; }
    public bool HasPaceRange { get; init; }
    public bool HasPitWindow { get; init; }
    public bool HasFuelLimit { get; init; }
    public bool HasLastLap { get; init; }
    public bool HasLeaderLastLap { get; init; }
    public bool HasTirePhase { get; init; }
    public bool HasWeather { get; init; }
    public bool HasBrakeBias { get; init; }
    public bool HasRepair { get; init; }
    public bool HasOfficialEvents { get; init; }
    public bool HasHostedLeagueEvents { get; init; }
    public bool HasAiEvents { get; init; }
    public bool HasFixedEvents { get; init; }
    public bool HasOpenEvents { get; init; }
    public bool HasAnalyzedEvents { get; init; }
    public bool HasUnanalyzedEvents { get; init; }
    public bool CoachEngineInstalled { get; init; }
    public bool CoachEngineRunning { get; init; }
    public bool ChatGptConnected { get; init; }
    public bool Garage61Configured { get; init; }
    public bool Garage61Available { get; init; }
}

/// <summary>
/// The single production capability inventory. Backend evidence may retain
/// unavailable values for truthfulness; this registry decides whether a
/// user-facing surface is useful in the current context.
/// </summary>
public static class CapabilityRegistry
{
    private static readonly IReadOnlyDictionary<ProductCapability, CapabilityDefinition> Definitions = BuildDefinitions();

    public static IReadOnlyCollection<CapabilityDefinition> Inventory => Definitions.Values.ToArray();

    public static CapabilityDecision Evaluate(ProductCapability capability, CapabilityContext context)
    {
        var definition = Definitions[capability];
        if (definition.Classification is CapabilityClass.PermanentlyUnsupported or CapabilityClass.NotImplemented)
            return new(definition, definition.Classification, false);

        return capability switch
        {
            ProductCapability.LiveTelemetry when !context.LiveConnected => Temporary(definition, "Waiting for iRacing", "Start or join an iRacing session; connection retries automatically."),
            ProductCapability.RacePlanning => Conditional(definition, context.HasRaceRecordings),
            ProductCapability.ProgressiveTuning => Conditional(definition, context.HasOpenAnalyzedRace),
            ProductCapability.RawTelemetryRelocation => Conditional(definition, context.HasMissingRawTelemetry),
            ProductCapability.ChatGptCoaching when !context.CoachEngineInstalled => new(definition, CapabilityClass.TemporarilyUnavailable, false, "Coach Engine needs repair", "Run Repair installation from Connections."),
            ProductCapability.ChatGptCoaching when !context.CoachEngineRunning => Temporary(definition, "Coach Engine is restarting", "Wait for the private Coach Engine to restart."),
            ProductCapability.ChatGptCoaching when !context.ChatGptConnected => Temporary(definition, "ChatGPT signed out", "Reconnect ChatGPT."),
            ProductCapability.Garage61Connection when context.Garage61Configured && !context.Garage61Available => Temporary(definition, "Garage61 connection lost · retrying", "Check the network or replace the saved token."),
            ProductCapability.LiveLeaderGap => Conditional(definition, context.LiveConnected && context.HasLeaderGap),
            ProductCapability.LiveAheadGap => Conditional(definition, context.LiveConnected && context.HasAheadGap),
            ProductCapability.LiveBehindGap => Conditional(definition, context.LiveConnected && context.HasBehindGap),
            ProductCapability.LivePaceRange => Conditional(definition, context.LiveConnected && context.HasPaceRange),
            ProductCapability.LivePitWindow => Conditional(definition, context.LiveConnected && context.HasPitWindow),
            ProductCapability.LiveFuelLimit => Conditional(definition, context.LiveConnected && context.HasFuelLimit),
            ProductCapability.LiveLastLap => Conditional(definition, context.LiveConnected && context.HasLastLap),
            ProductCapability.LiveLeaderLastLap => Conditional(definition, context.LiveConnected && context.HasLeaderLastLap),
            ProductCapability.LiveTirePhase => Conditional(definition, context.LiveConnected && context.HasTirePhase),
            ProductCapability.LiveWeather => Conditional(definition, context.LiveConnected && context.HasWeather),
            ProductCapability.LiveBrakeBias => Conditional(definition, context.LiveConnected && context.HasBrakeBias),
            ProductCapability.LiveRepair => Conditional(definition, context.LiveConnected && context.HasRepair),
            ProductCapability.OfficialEventFilter => Conditional(definition, context.HasOfficialEvents),
            ProductCapability.HostedLeagueEventFilter => Conditional(definition, context.HasHostedLeagueEvents),
            ProductCapability.AiEventFilter => Conditional(definition, context.HasAiEvents),
            ProductCapability.FixedSetupFilter => Conditional(definition, context.HasFixedEvents),
            ProductCapability.OpenSetupFilter => Conditional(definition, context.HasOpenEvents),
            ProductCapability.AnalyzedFilter => Conditional(definition, context.HasAnalyzedEvents),
            ProductCapability.NeedsAnalysisFilter => Conditional(definition, context.HasUnanalyzedEvents),
            _ => new(definition, CapabilityClass.SupportedNow, definition.ProductionVisible)
        };
    }

    public static IReadOnlyList<CapabilityDecision> ActiveForAi(CapabilityContext context) =>
        Definitions.Keys.Select(capability => Evaluate(capability, context)).Where(decision => decision.Active).ToArray();

    public static bool IsSupported(EvidenceText? claim) =>
        claim is not null && claim.Kind != EvidenceKind.Unavailable && !string.IsNullOrWhiteSpace(claim.Text);

    private static CapabilityDecision Conditional(CapabilityDefinition definition, bool applies) =>
        new(definition, CapabilityClass.ConditionallyApplicable, applies);

    private static CapabilityDecision Temporary(CapabilityDefinition definition, string state, string action) =>
        new(definition, CapabilityClass.TemporarilyUnavailable, true, state, action);

    private static IReadOnlyDictionary<ProductCapability, CapabilityDefinition> BuildDefinitions()
    {
        var items = new[]
        {
            Supported(ProductCapability.Home, "Home", "Choose a useful current workflow", "Local application state"),
            Supported(ProductCapability.LiveTelemetry, "Live telemetry", "See trustworthy current race data", "iRacing SDK shared memory", "Waiting for iRacing; automatic reconnect"),
            Supported(ProductCapability.LiveMonitor, "Live Monitor", "Keep glanceable race cues above iRacing", "Validated live telemetry snapshot"),
            Supported(ProductCapability.RaceAnalysis, "Race analysis", "Turn a finalized race recording into deterministic coaching", "Local IBT analysis and archived Race Cards", "Analysis in progress; background job status remains visible"),
            ConditionalDefinition(ProductCapability.RacePlanning, "Race planning", "Reuse comparable personal race history", "Analyzed local race history", "At least one recorded race"),
            Supported(ProductCapability.SetupLibrary, "Setup library", "Find and identify local setup files without modifying them", "Read-only .sto discovery"),
            ConditionalDefinition(ProductCapability.ProgressiveTuning, "Progressive tuning", "Run one controlled setup experiment", "Analyzed open-setup race plus embedded setup", "An analyzed open-setup race exists"),
            Supported(ProductCapability.Connections, "Connections", "Manage working optional services", "Private Coach Engine and Garage61 credential adapter"),
            Supported(ProductCapability.Settings, "Settings", "Control working application behavior", "Portable and machine-local settings"),
            Supported(ProductCapability.BackupAndMigration, "Backup and migration", "Create an integrity-checked portable copy", "Durable archive manifest and SQLite checkpoint"),
            ConditionalDefinition(ProductCapability.RawTelemetryRelocation, "Raw telemetry relocation", "Reconnect archived analysis to its original recording", "SHA-256 source mapping", "An archived raw source is missing"),
            Supported(ProductCapability.ChatGptCoaching, "ChatGPT coaching", "Explain deterministic evidence conversationally", "Private Coach Engine with bounded MCP tools", "Signed out, token refresh, or private runtime restart; reconnect or repair action"),
            Supported(ProductCapability.Garage61Connection, "Garage61 connection", "Authorize approved personal Garage61 access", "Machine-bound PAT and auth-status adapter", "Connection loss or rate limit; automatic retry and credential replacement"),
            Unsupported(ProductCapability.Garage61GlobalComparison, "Garage61 global-field comparison", "Compare against globally visible laps", "Garage61 API", "Global-visible scope is not approved"),
            NotImplemented(ProductCapability.QualifyingAnalysis, "Qualifying analysis", "Review qualifying performance", "Finalized qualifying recording"),
            NotImplemented(ProductCapability.SetupComparison, "Setup comparison", "Compare setup parameters", "Validated setup parser"),
            NotImplemented(ProductCapability.SetupPackageBuilder, "Setup package builder", "Assemble a coaching package", "Setup/package workflow"),
            NotImplemented(ProductCapability.SetupExperimentTab, "Setup experiments tab", "Browse setup experiments", "Tuning history"),
            NotImplemented(ProductCapability.TrackMap, "Track map", "Locate coaching on geographic track shape", "Calibrated track geometry"),
            NotImplemented(ProductCapability.ExactTargetTrace, "Exact target trace", "Compare against an aligned representative lap", "Validated aligned target telemetry"),
            NotImplemented(ProductCapability.PushToPass, "Push-to-pass guidance", "Use a supported in-car control", "Car capability and live channel"),
            NotImplemented(ProductCapability.WeightJacker, "Weight-jacker guidance", "Use a supported in-car control", "Car capability and live channel"),
            NotImplemented(ProductCapability.WetWeatherAnalysis, "Wet-weather analysis", "Adapt coaching to recorded wet conditions", "Validated rain and wetness evidence"),
            NotImplemented(ProductCapability.MulticlassAnalysis, "Multiclass analysis", "Separate overall and class race context", "Validated class scoring metadata"),
            ConditionalDefinition(ProductCapability.LiveLeaderGap, "Leader gap", "Track the measured overall-leader interval", "iRacing scoring interval", "A valid same-lap interval exists"),
            ConditionalDefinition(ProductCapability.LiveAheadGap, "Gap ahead", "Track the measured nearby interval", "iRacing scoring interval", "A valid same-lap car ahead exists"),
            ConditionalDefinition(ProductCapability.LiveBehindGap, "Gap behind", "Track the measured nearby interval", "iRacing scoring interval", "A valid same-lap car behind exists"),
            ConditionalDefinition(ProductCapability.LivePaceRange, "Personal pace range", "Use a clean personal session baseline", "Three or more clean completed laps", "A clean baseline exists"),
            ConditionalDefinition(ProductCapability.LivePitWindow, "Pit window", "Use a defensible strategic pit range", "Fuel model and strategy context", "Both window bounds are supported"),
            ConditionalDefinition(ProductCapability.LiveFuelLimit, "Fuel hard limit", "Know the measured fuel feasibility limit", "Live fuel burn", "A valid fuel burn exists"),
            ConditionalDefinition(ProductCapability.LiveLastLap, "Last lap", "Review the most recent completed lap", "iRacing lap timing", "A completed player lap exists"),
            ConditionalDefinition(ProductCapability.LiveLeaderLastLap, "Leader last lap", "Compare recent leader pace", "iRacing scoring timing", "A completed leader lap exists"),
            ConditionalDefinition(ProductCapability.LiveTirePhase, "Tire phase", "Understand the supported phase of the current run", "Clean-lap session baseline", "A phase other than unknown is supported"),
            ConditionalDefinition(ProductCapability.LiveWeather, "Track and air temperature", "See measured environmental context", "Live telemetry channels", "At least one temperature channel exists"),
            ConditionalDefinition(ProductCapability.LiveBrakeBias, "Brake bias", "See a supported in-car adjustment", "Live brake-bias channel", "The channel exists for the current car"),
            ConditionalDefinition(ProductCapability.LiveRepair, "Repair status", "See active recorded repair time", "Live repair channels", "A repair timer is positive"),
            ConditionalDefinition(ProductCapability.OfficialEventFilter, "Official-event filter", "Narrow recorded events", "Recorded event scope", "At least one official event is identified"),
            ConditionalDefinition(ProductCapability.HostedLeagueEventFilter, "Hosted/league filter", "Narrow recorded events", "Recorded event scope", "At least one hosted or league event is identified"),
            ConditionalDefinition(ProductCapability.AiEventFilter, "AI-event filter", "Narrow recorded events", "Recorded event scope", "At least one AI event is identified"),
            ConditionalDefinition(ProductCapability.FixedSetupFilter, "Fixed-setup filter", "Narrow recorded events", "Recorded setup type", "At least one fixed-setup event exists"),
            ConditionalDefinition(ProductCapability.OpenSetupFilter, "Open-setup filter", "Narrow recorded events", "Recorded setup type", "At least one open-setup event exists"),
            ConditionalDefinition(ProductCapability.AnalyzedFilter, "Analyzed filter", "Narrow recorded events", "Archive analysis status", "At least one analyzed event exists"),
            ConditionalDefinition(ProductCapability.NeedsAnalysisFilter, "Needs-analysis filter", "Narrow recorded events", "Archive analysis status", "At least one finalized race needs analysis")
        };
        return items.ToDictionary(item => item.Id);
    }

    private static CapabilityDefinition Supported(ProductCapability id, string name, string value, string source, string temporaryFailures = "None") =>
        new(id, name, value, source, CapabilityClass.SupportedNow, "Always", "Automated tests and native QA", temporaryFailures, "None required", "Render normally", true);

    private static CapabilityDefinition ConditionalDefinition(ProductCapability id, string name, string value, string source, string applies) =>
        new(id, name, value, source, CapabilityClass.ConditionallyApplicable, applies, "Capability-context tests", "None; absence outside the applicable context is expected", "Omit the element; retain evidence internally", "Render only when applicable", true);

    private static CapabilityDefinition Unsupported(ProductCapability id, string name, string value, string source, string reason) =>
        new(id, name, value, source, CapabilityClass.PermanentlyUnsupported, reason, "Production absence test", "Not recoverable", "Use personal local history when applicable", "Removed from production", false);

    private static CapabilityDefinition NotImplemented(ProductCapability id, string name, string value, string source) =>
        new(id, name, value, source, CapabilityClass.NotImplemented, "No complete validated production implementation", "Production absence test", "Not exposed as a temporary state", "Omit until implementation and validation are complete", "Development inventory only", false);
}
