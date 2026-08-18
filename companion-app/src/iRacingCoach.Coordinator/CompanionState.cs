using System.Diagnostics;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

public sealed record TuningFeedbackDraft(
    string Corner,
    string RunPhase,
    string CornerPhase,
    string Balance,
    string Severity,
    string Confidence,
    bool Priority,
    string Note = "",
    string FeedbackId = "");

public sealed record LocalInventorySectionStatus(string Name, bool Current, string Message);

public sealed class CompanionState : IDisposable
{
    // The cached backend response is part of the UI contract. Bump this when
    // mapped analysis fields change so an older response cannot silently hide
    // newly available maps, replay coverage, tire learning, or technical data.
    private const int UiAnalysisCacheSchemaVersion = 12;
    private const string AppVersion = "0.16.0";

    // Portable artifacts are machine-read. Indenting them inflated the analysis cache by
    // roughly 1.8x - one 25 MB entry spanned 615,614 lines - and allocating fresh options
    // per write defeats System.Text.Json's per-options metadata cache.
    private static readonly JsonSerializerOptions PortableArtifactJson = new(JsonSerializerDefaults.Web)
    {
        WriteIndented = false
    };
    private readonly IBackendClient _backend;
    private readonly ISettingsStore? _settingsStore;
    private readonly IGarage61CredentialStore _garage61Credentials;
    private readonly ICoachEngineSupervisor _coachEngine;
    private readonly LiveTelemetryService _liveTelemetry;
    private readonly LiveReplayCaptureStore _liveReplayCapture;
    private readonly IDurableArchiveService _archive;
    private readonly ICompanionPathProvider _pathProvider;
    private readonly bool _allowExternalHostActions;
    private readonly Dictionary<string, CancellationTokenSource> _jobTokens = new(StringComparer.OrdinalIgnoreCase);
    private readonly BackendOperationCoordinator _backendOperations = new();
    private readonly object _homeAnalysisSync = new();
    private readonly object _liveMonitorVisibilityGate = new();
    private readonly object _settingsPersistenceGate = new();
    private readonly object _inventoryGate = new();
    private readonly object _refreshSync = new();
    private readonly object _watcherSync = new();
    private readonly Queue<RecentRace> _homeAnalysisQueue = [];
    private readonly HashSet<string> _homeAnalysisActiveKeys = new(StringComparer.OrdinalIgnoreCase);
    private readonly CancellationTokenSource _homeAnalysisCancellation = new();
    private Task? _homeAnalysisWorker;
    private static readonly TimeSpan HomeAnalysisRetryDelay = TimeSpan.FromMilliseconds(250);
    private CancellationTokenSource? _coachRequest;
    private readonly SemaphoreSlim _refreshGate = new(1, 1);
    private readonly CancellationTokenSource _refreshLifetime = new();
    private Task? _refreshTask;
    private Task? _garageRefreshTask;
    private bool _refreshDirty;
    private bool _refreshShowToast;
    private int _refreshLoopsActive;
    private Timer? _fileRefresh;
    private readonly List<FileSystemWatcher> _watchers = [];
    private string _watcherRoot = string.Empty;
    private LocalInventorySnapshot? _localInventory;
    private bool _initialized;
    private bool _disposed;
    private bool _liveMonitorAutoReopenSuppressed;
    private bool _liveMonitorWasConnected;
    private DateTimeOffset _lastPrimaryUiLiveUpdate = DateTimeOffset.MinValue;
    private DateTimeOffset? _lastReplayFailureNotifiedAt;
    private int _serviceRequestsInFlight;
    private int _garage61ReferenceSyncActive;
    private long _tuningRaceSelectionEpoch;
    private long _tuningTargetSelectionEpoch;
    private long _startingTuneSelectionEpoch;
    private long _analysisSelectionEpoch;
    private long _planRequestEpoch;
    private long _experimentRequestEpoch;
    private string _selectedPlanRaceId = string.Empty;
    private string _selectedPlanCarId = string.Empty;
    private string _selectedPlanTrack = string.Empty;
    private string _planDistanceMode = "Laps";
    private double _planDistanceValue;
    private string _planSetupType = "Fixed";
    private string _symptomText = string.Empty;
    private string _tuningRunPhase = "Late run";
    private string _tuningCornerPhase = "Center";
    private string _tuningBalance = string.Empty;
    private string _tuningSeverity = "Moderate";
    private string _tuningConfidence = "Medium";
    private bool _tuningPriority;
    private string _tuningCorner = "Whole lap";
    private string _tuningNotes = string.Empty;
    private string _startingTuneSeason = CurrentIRacingSeason();
    private string _startingTuneCar = string.Empty;
    private string _startingTuneTrack = string.Empty;
    private string _startingTunePurpose = "Race";
    private string _selectedTuningRaceId = string.Empty;
    private readonly SemaphoreSlim _tuningDraftMutationGate = new(1, 1);
    private BackendHealthResult _lastBackendHealth = new(false, "unknown", "unknown", "unknown", 0, TimeSpan.Zero, "Not checked");

    public CompanionState() : this(new McpBackendClient(), new JsonSettingsStore(), new IRacingSdkTelemetrySource(), new CodexAppServerSupervisor(), new PowerShellGarage61CredentialStore(), new DurableArchiveService(), WindowsCompanionPathProvider.Instance) { }
    public CompanionState(ILiveTelemetrySource liveTelemetrySource) : this(new McpBackendClient(), new JsonSettingsStore(), liveTelemetrySource, new CodexAppServerSupervisor(), new PowerShellGarage61CredentialStore(), new DurableArchiveService(), WindowsCompanionPathProvider.Instance) { }
    public CompanionState(IBackendClient backend) : this(backend, null, new DisconnectedLiveTelemetrySource(), new DisabledCoachEngineSupervisor(), new PowerShellGarage61CredentialStore()) { }

    public CompanionState(IBackendClient backend, ISettingsStore? settingsStore) : this(backend, settingsStore, new DisconnectedLiveTelemetrySource(), new DisabledCoachEngineSupervisor(), new PowerShellGarage61CredentialStore()) { }

    public CompanionState(
        IBackendClient backend,
        ISettingsStore? settingsStore,
        ILiveTelemetrySource liveTelemetrySource,
        ICoachEngineSupervisor? coachEngine = null,
        IGarage61CredentialStore? garage61Credentials = null,
        IDurableArchiveService? archive = null,
        ICompanionPathProvider? pathProvider = null,
        bool allowExternalHostActions = true)
    {
        _backend = backend;
        _settingsStore = settingsStore;
        _garage61Credentials = garage61Credentials ?? new PowerShellGarage61CredentialStore();
        _coachEngine = coachEngine ?? new DisabledCoachEngineSupervisor();
        _archive = archive ?? new DurableArchiveService();
        _pathProvider = pathProvider ?? WindowsCompanionPathProvider.Instance;
        _allowExternalHostActions = allowExternalHostActions;
        Settings = settingsStore?.Load() ?? new CompanionSettings(_pathProvider);
        Settings.LiveMonitor ??= new LiveMonitorLayout();
        if (!Settings.Compatibility.Writable) SettingsMessage = Settings.Compatibility.Message;
        CurrentPage = Settings.FirstRunComplete ? "home" : "first-run";
        _liveTelemetry = new LiveTelemetryService(liveTelemetrySource, Settings.LiveMonitor);
        _liveReplayCapture = new LiveReplayCaptureStore(() => Settings.ArchiveRoot);
        _liveReplayCapture.StatusChanged += OnReplayCaptureStatusChanged;
        _liveTelemetry.Updated += OnLiveTelemetryUpdated;
        _liveTelemetry.FrameCaptured += OnLiveTelemetryFrameCaptured;
        _liveTelemetry.ReplayFrameCaptured += OnLiveReplayFrameCaptured;
        _liveTelemetry.ReplaySessionEnded += OnLiveReplaySessionEnded;
        _coachEngine.Changed += OnCoachEngineChanged;
        _coachEngine.CoachMessageDelta += OnCoachMessageDelta;
        CoachEngine = _coachEngine.Current;
        Health =
        [
            new("coachengine", "Coach Engine", "checking", "Starting…"),
            new("backend", "Race analysis", "checking", "Starting…", true),
            new("garage61", "Garage61", "checking", "Checking connection…"),
            new("repository", "Coach data", "checking", "Checking folders…")
        ];
    }

    public event Action? Changed;
    public event Action? LiveTelemetryChanged;
    public event Action<LiveTracePoint>? LiveTelemetryFrame;
    public event Action<CompanionSettings>? SettingsSaved;
    public event Action<bool, bool>? LiveMonitorVisibilityRequested;
    public event Action? RawTelemetryLocateRequested;

    public string CurrentPage { get; private set; } = "home";
    public bool RailCollapsed { get; private set; }
    public bool JobTrayOpen { get; private set; }
    public bool IsRefreshing { get; private set; }
    public bool HomeDataReady { get; private set; }
    public long LocalInventoryGeneration { get; private set; }
    public IReadOnlyList<LocalInventorySectionStatus> LocalInventorySections { get; private set; } = [];
    public bool PlanGenerated { get; private set; }
    public bool ExperimentGenerated { get; private set; }
    public bool DiagnosticsExpanded { get; private set; }
    public string? Toast { get; private set; }
    public string SettingsMessage { get; private set; } = "Preferences and racing history live in the Coach folder. Account connections remain protected on this PC.";
    public bool SettingsWritable => Settings.Compatibility.Writable;
    public string DataMessage { get; private set; } = "Looking for finalized iRacing recordings…";
    public string PlanMessage { get; private set; } = "Choose one of your recorded races to use its exact car, track, and setup context.";
    public string SetupMessage { get; private set; } = "Only setup files found on this PC are shown.";
    public string TuningMessage { get; private set; } = "Choose an analyzed open-setup race and describe what the car did.";
    // UI-only launch-session preference. This intentionally does not live in
    // CompanionSettings, so every app launch starts on Early while navigation
    // within the running app preserves the driver's selected phase.
    public string TuningActiveRunPhase { get; set; } = "early";
    public string SymptomText { get => _symptomText; set => SetExperimentInput(ref _symptomText, value); }
    public string TuningRunPhase { get => _tuningRunPhase; set => SetExperimentInput(ref _tuningRunPhase, value); }
    public string TuningCornerPhase { get => _tuningCornerPhase; set => SetExperimentInput(ref _tuningCornerPhase, value); }
    public string TuningBalance { get => _tuningBalance; set => SetExperimentInput(ref _tuningBalance, value); }
    public string TuningSeverity { get => _tuningSeverity; set => SetExperimentInput(ref _tuningSeverity, value); }
    public string TuningConfidence { get => _tuningConfidence; set => SetExperimentInput(ref _tuningConfidence, value); }
    public bool TuningPriority { get => _tuningPriority; set => SetExperimentInput(ref _tuningPriority, value); }
    public string TuningCorner { get => _tuningCorner; set => SetExperimentInput(ref _tuningCorner, value); }
    public string TuningNotes { get => _tuningNotes; set => SetExperimentInput(ref _tuningNotes, value); }
    public string FeedbackNotes { get; set; } = string.Empty;
    public string Garage61KeyInput { get; set; } = string.Empty;
    public string CoachQuestion { get; set; } = "What should I work on first based on this race?";
    public string CoachAnswer { get; private set; } = string.Empty;
    public string CoachProgress { get; private set; } = string.Empty;
    public bool IsCoaching { get; private set; }
    public int SetupStep { get; private set; } = 1;
    public string? PendingChatGptLoginId { get; private set; }
    public string SelectedPlanRaceId { get => _selectedPlanRaceId; set => SetPlanInput(ref _selectedPlanRaceId, value); }
    public string SelectedPlanCarId { get => _selectedPlanCarId; set => SetPlanInput(ref _selectedPlanCarId, value); }
    public string SelectedPlanTrack { get => _selectedPlanTrack; set => SetPlanInput(ref _selectedPlanTrack, value); }
    public string PlanDistanceMode { get => _planDistanceMode; set => SetPlanInput(ref _planDistanceMode, value); }
    public double PlanDistanceValue { get => _planDistanceValue; set => SetPlanInput(ref _planDistanceValue, value); }
    public string PlanSetupType { get => _planSetupType; set => SetPlanInput(ref _planSetupType, value); }
    public string SelectedTuningRaceId { get => _selectedTuningRaceId; set => SetExperimentInput(ref _selectedTuningRaceId, value); }
    public string SelectedTuningTargetRaceId { get; private set; } = string.Empty;
    public string SelectedTuningResultRaceId { get; set; } = string.Empty;
    public string SelectedTuningTurnId { get; private set; } = string.Empty;
    public string SelectedSetupId { get; set; } = string.Empty;
    public string CompareSetupId { get; set; } = string.Empty;
    public int StartingTuneStep { get; private set; } = 1;
    public string StartingTuneSeason { get => _startingTuneSeason; set => SetStartingTuneInput(ref _startingTuneSeason, value); }
    public string StartingTuneCar { get => _startingTuneCar; set => SetStartingTuneInput(ref _startingTuneCar, value); }
    public string StartingTuneTrack { get => _startingTuneTrack; set => SetStartingTuneInput(ref _startingTuneTrack, value); }
    public string StartingTunePurpose { get => _startingTunePurpose; set => SetStartingTuneInput(ref _startingTunePurpose, value); }
    public bool StartingTuneBusy { get; private set; }
    public string SelectedRaceSessionId { get; private set; } = string.Empty;
    public string RaceSearchText { get; set; } = string.Empty;
    public RaceBrowserFilter RaceFilter { get; set; }
    public CompanionSettings Settings { get; }
    public List<HealthItem> Health { get; }
    public List<RecentRace> Races { get; } = [];
    public List<RecentRace> EventSessions { get; } = [];
    public List<RaceEventGroup> EventGroups { get; } = [];
    public List<InstalledCar> Cars { get; } = [];
    public List<InstalledTrack> Tracks { get; } = [];
    public List<LocalSetup> Setups { get; } = [];
    public List<StrategyScenario> StrategyScenarios { get; } = [];
    public List<TuningFeedbackDraft> TuningFeedback { get; } = [];
    public RacePlanBriefing? PlanBriefing { get; private set; }
    public TuningExperimentView? TuningExperiment { get; private set; }
    public ProgressiveTuningDraft TuningDraft { get; private set; } = new();
    public TuningMapView? SelectedTuningMap { get; private set; }
    public TuningSetupTarget? SelectedTuningTarget { get; private set; }
    public StructuredTuningResultView? StructuredTuningResult { get; private set; }
    public SetupPackageView? StartingTunePackage { get; private set; }
    public RaceCard? CurrentRaceCard { get; private set; }
    public AnalysisWorkspace? CurrentAnalysis { get; private set; }
    public bool AnalysisWorkspaceOpen { get; private set; }
    public bool AnalysisLoading { get; private set; }
    public string AnalysisMessage { get; private set; } = string.Empty;
    public string Garage61ReferenceMessage { get; private set; } = string.Empty;
    public bool IsGarage61ReferenceSyncing => Volatile.Read(ref _garage61ReferenceSyncActive) != 0;
    public Garage61Connection Garage61 { get; private set; } = Garage61StatusReducer.Unprobed(false);
    public string Garage61StatusLabel => Garage61StatusReducer.Label(Garage61);
    public CoachEngineConnection CoachEngine { get; private set; }
    public IReadOnlyList<DiagnosticFact> Diagnostics { get; private set; } = [];
    public ArchiveStatus? Archive { get; private set; }
    public BackupPreparationResult? BackupPreparation { get; private set; }
    public List<JobItem> Jobs { get; } = [];
    public List<PortableCoachingRecord> CoachingHistory { get; } = [];
    public DateTimeOffset? LastUpdated { get; private set; }
    public int LocalRequestCount { get; private set; }
    public int Garage61RequestCount { get; private set; }
    public int ApiCacheHitCount { get; private set; }
    public int AiRequestCount { get; private set; }
    public DateTimeOffset? LastServiceRequest { get; private set; }
    public TimeSpan? LastAiDuration { get; private set; }
    public int ServiceFailureCount { get; private set; }
    public RecoverableAppError? LastRecoverableError { get; private set; }
    public bool ServiceRequestInFlight => Volatile.Read(ref _serviceRequestsInFlight) > 0;
    public LiveMonitorState LiveState => _liveTelemetry.Current;
    public LiveReplayCaptureStatus ReplayCaptureStatus => _liveReplayCapture.Status;
    public bool LiveCoachingPaused => _liveTelemetry.CoachingPaused;
    public bool PrimaryUiVisible { get; private set; } = true;
    public bool IRacingDetected => Directory.Exists(Settings.IRacingRoot);
    public bool Garage61ReferenceActionVisible => Garage61.Available && TryGetAnalysisPath(SelectedRaceSession, out _);

    public void ReportUnhandledException(string scope, Exception exception)
    {
        LastRecoverableError = StructuredAppLog.Record(scope, exception, AppVersion, Settings.LogsRoot, _pathProvider.UserProfile);
        ServiceFailureCount++;
        Toast = "Something went wrong, but the app is still running.";
        RaiseChanged();
    }

    public void ClearRecoverableError()
    {
        LastRecoverableError = null;
        RaiseChanged();
    }

    public RecentRace? SelectedPlanRace => Races.OfType<RecentRace>().FirstOrDefault(race => string.Equals(race.Id, SelectedPlanRaceId, StringComparison.Ordinal));
    public RecentRace? SelectedTuningRace => Races.OfType<RecentRace>().FirstOrDefault(race => string.Equals(race.Id, SelectedTuningRaceId, StringComparison.Ordinal));
    public RecentRace? SelectedTuningTargetRace => Races.OfType<RecentRace>().FirstOrDefault(race => string.Equals(race.Id, SelectedTuningTargetRaceId, StringComparison.Ordinal));
    public RecentRace? SelectedTuningResultRace => Races.OfType<RecentRace>().FirstOrDefault(race => string.Equals(race.Id, SelectedTuningResultRaceId, StringComparison.Ordinal));
    public RecentRace? SelectedRaceSession => EventSessions.OfType<RecentRace>().FirstOrDefault(session => string.Equals(session.Id, SelectedRaceSessionId, StringComparison.Ordinal));
    public LocalSetup? SelectedSetup => Setups.OfType<LocalSetup>().FirstOrDefault(setup => string.Equals(setup.Id, SelectedSetupId, StringComparison.Ordinal));
    public LocalSetup? CompareSetup => Setups.OfType<LocalSetup>().FirstOrDefault(setup => string.Equals(setup.Id, CompareSetupId, StringComparison.Ordinal));
    public IEnumerable<RecentRace> TuningRaces => Races.OfType<RecentRace>().Where(race => race.Analyzed && string.Equals(race.SetupType, "Open", StringComparison.OrdinalIgnoreCase) && !string.IsNullOrWhiteSpace(race.AnalysisPath));
    public IEnumerable<RecentRace> TuningEvidenceRaces => Races.OfType<RecentRace>().Where(race => race.Analyzed && !string.IsNullOrWhiteSpace(race.AnalysisPath));
    public IEnumerable<TuningRaceCandidate> TuningCandidates => TuningEvidenceRaces.Select(race =>
        ProgressiveTuningCoordinator.Candidate(race,
            CurrentAnalysis is not null && ProgressiveTuningCoordinator.Matches(race, CurrentAnalysis.TuningIdentity)
                ? CurrentAnalysis
                : null));
    public IEnumerable<RecentRace> TuningResultRaces => Races.OfType<RecentRace>().Where(race =>
        race.Analyzed && !string.IsNullOrWhiteSpace(race.AnalysisPath)
        && TuningDraft.RepresentativeSession is { } identity
        && string.Equals(race.CarPath, identity.CarPath, StringComparison.OrdinalIgnoreCase)
        && string.Equals(race.Track, identity.Track, StringComparison.OrdinalIgnoreCase)
        && string.Equals(race.Layout, identity.Layout, StringComparison.OrdinalIgnoreCase));
    public IEnumerable<RaceEventGroup> FilteredEventGroups => EventGroups.OfType<RaceEventGroup>()
        .Where(group => group.Sessions is { Count: > 0 })
        .Where(MatchesRaceBrowser);
    public CapabilityContext CapabilityContext
    {
        get
        {
            var live = LiveState.Snapshot;
            var raceSessions = EventSessions.OfType<RecentRace>().Where(session => session.IsRace).ToArray();
            return new CapabilityContext
            {
                HasRaceRecordings = Races.Count > 0,
                // Progressive tuning can start from any analyzed race as evidence. The
                // open-setup target is chosen inside the workflow when the evidence race
                // itself was fixed, so the navigation gate must not hide that path.
                HasOpenAnalyzedRace = TuningEvidenceRaces.Any(),
                HasSetupFiles = Setups.Count > 0,
                HasComparableSetups = CapabilityRegistry.HasSetupComparison(Setups, SelectedSetupId),
                HasTrackView = CapabilityRegistry.HasTrackView(CurrentAnalysis),
                HasMissingRawTelemetry = Archive?.Restored.UnresolvedSources > 0,
                LiveConnected = live.Connected,
                HasLeaderGap = live.LeaderGap.Seconds.HasValue,
                HasAheadGap = live.AheadGap.Seconds.HasValue,
                HasBehindGap = live.BehindGap.Seconds.HasValue,
                HasPaceRange = live.PaceTarget.MinimumSeconds.HasValue && live.PaceTarget.MaximumSeconds.HasValue,
                HasPitWindow = live.Pit.WindowOpensInLaps.HasValue && live.Pit.WindowClosesInLaps.HasValue,
                HasFuelLimit = live.Pit.FuelHardLimitLaps.HasValue,
                HasLastLap = live.LastLapSeconds.HasValue,
                HasLeaderLastLap = live.LeaderLastLapSeconds.HasValue,
                HasTirePhase = HasUsefulText(live.TirePhase),
                HasWeather = live.TrackTemperatureC.HasValue || live.AirTemperatureC.HasValue,
                HasBrakeBias = live.BrakeBiasPercent.HasValue,
                HasRepair = live.MandatoryRepairSeconds > 0 || live.OptionalRepairSeconds > 0,
                HasOfficialEvents = raceSessions.Any(session => string.Equals(session.EventScope, "Official", StringComparison.OrdinalIgnoreCase)),
                HasHostedLeagueEvents = raceSessions.Any(session => string.Equals(session.EventScope, "Hosted / League", StringComparison.OrdinalIgnoreCase)),
                HasAiEvents = raceSessions.Any(session => string.Equals(session.EventScope, "AI", StringComparison.OrdinalIgnoreCase)),
                HasFixedEvents = raceSessions.Any(session => string.Equals(session.SetupType, "Fixed", StringComparison.OrdinalIgnoreCase)),
                HasOpenEvents = raceSessions.Any(session => string.Equals(session.SetupType, "Open", StringComparison.OrdinalIgnoreCase)),
                HasAnalyzedEvents = raceSessions.Any(session => session.Analyzed),
                HasUnanalyzedEvents = raceSessions.Any(session => !session.Analyzed),
                CoachEngineInstalled = CoachEngine.Installed,
                CoachEngineRunning = CoachEngine.Running,
                ChatGptConnected = CoachEngine.ChatGptConnected,
                Garage61Configured = Garage61.Configured,
                Garage61Available = Garage61.Available
            };
        }
    }
    public CapabilityDecision Capability(ProductCapability capability) => CapabilityRegistry.Evaluate(capability, CapabilityContext);
    public bool IsCapabilityVisible(ProductCapability capability) => Capability(capability).Visible;
    public IReadOnlyList<RaceBrowserFilter> AvailableRaceFilters => Enum.GetValues<RaceBrowserFilter>()
        .Where(filter => filter == RaceBrowserFilter.All || IsCapabilityVisible(FilterCapability(filter)))
        .ToArray();
    public string Greeting => DateTime.Now.Hour switch
    {
        < 12 => "Good morning.",
        < 17 => "Good afternoon.",
        _ => "Good evening."
    };
    public string LastUpdatedLabel => IsRefreshing ? "Updating…" : LastUpdated is null ? "Starting…" : $"Updated {RelativeTime(LastUpdated.Value)}";
    public string? HeaderAlert
    {
        get
        {
            var requiredService = Health.FirstOrDefault(item =>
                item.State == "unavailable" && (item.IsPrimary || item.Id == "repository"));
            if (requiredService is not null)
            {
                return $"{requiredService.Label} needs attention";
            }

            var garage61 = Health.FirstOrDefault(item => item.Id == "garage61" && item.State == "warning");
            return garage61 is null ? null : "Garage61 connection needs attention";
        }
    }

    public async Task InitializeAsync(CancellationToken cancellationToken = default)
    {
        if (_initialized) return;
        _initialized = true;
        if (!Directory.Exists(Settings.IRacingInstallRoot))
        {
            var detectedInstall = new CompanionSettings().IRacingInstallRoot;
            if (Directory.Exists(detectedInstall)) Settings.IRacingInstallRoot = detectedInstall;
        }
        try
        {
            EnsureRepository();
        }
        catch (ArchiveCompatibilityException ex)
        {
            UpdateHealth("repository", "Coach data", "unavailable", ex.Message, true);
            SettingsMessage = ex.Message;
            Diagnostics =
            [
                new("App", $"v{AppVersion} / Windows x64", "ready"),
                new("Coach data", ex.Message, "warning"),
                new("Coach folder", Settings.CoachHome, "warning")
            ];
            HomeDataReady = true;
            RaiseChanged();
            return;
        }
        await RefreshDataAsync(showToast: false, cancellationToken);
        _liveTelemetry.Start();
        _ = InitializeCoachEngineAsync();
        if (Settings.LiveMonitor.Visible) LiveMonitorVisibilityRequested?.Invoke(true, false);
    }

    public Task RefreshDashboardAsync(CancellationToken cancellationToken = default) => RefreshDataAsync(true, cancellationToken);

    private Task RefreshDataAsync(bool showToast, CancellationToken cancellationToken)
    {
        Task refresh;
        lock (_refreshSync)
        {
            if (_disposed) return Task.CompletedTask;
            _refreshShowToast |= showToast;
            if (_refreshTask is { IsCompleted: false })
            {
                _refreshDirty = true;
                refresh = _refreshTask;
            }
            else
            {
                _refreshDirty = false;
                _refreshTask = Task.Run(RefreshLoopAsync);
                refresh = _refreshTask;
            }
        }
        return WaitForRefreshAsync(refresh, cancellationToken);
    }

    private static async Task WaitForRefreshAsync(Task refresh, CancellationToken cancellationToken)
    {
        if (cancellationToken.CanBeCanceled) await refresh.WaitAsync(cancellationToken);
        else await refresh;
    }

    private async Task RefreshLoopAsync()
    {
        Interlocked.Increment(ref _refreshLoopsActive);
        IsRefreshing = true;
        RaiseChanged();
        try
        {
            while (!_refreshLifetime.IsCancellationRequested)
            {
                bool showToast;
                lock (_refreshSync)
                {
                    _refreshDirty = false;
                    showToast = _refreshShowToast;
                    _refreshShowToast = false;
                }

                await RefreshInventoryPassAsync(showToast, _refreshLifetime.Token);

                lock (_refreshSync)
                {
                    if (_refreshDirty) continue;
                    break;
                }
            }
        }
        catch (OperationCanceledException) when (_refreshLifetime.IsCancellationRequested) { }
        catch (Exception ex)
        {
            RecordRefreshFailure("catalog refresh", ex);
            UpdateHealth("backend", "Race analysis", "unavailable", "Could not read local racing data", true);
            DataMessage = "The local inventory could not be updated. The last complete inventory is still shown.";
        }
        finally
        {
            IsRefreshing = Interlocked.Decrement(ref _refreshLoopsActive) > 0;
            HomeDataReady = true;
            RaiseChanged();
        }
    }

    private async Task RefreshInventoryPassAsync(bool showToast, CancellationToken cancellationToken)
    {
        await _refreshGate.WaitAsync(cancellationToken);
        try
        {
            var roots = new RefreshRoots(Settings.IRacingRoot, Settings.ArchiveRoot, Settings.IRacingInstallRoot);
            var configuration = CreateBackendConfiguration();
            var previous = CaptureLocalInventory(roots);
            var healthTask = ReadRefreshSectionAsync("Race analysis", () => _backend.CheckHealthAsync(configuration, cancellationToken), cancellationToken);
            var dashboardTask = ReadRefreshSectionAsync("Race recordings", async () =>
            {
                var response = await CallBackendAsync("iracing_companion_dashboard", new
                {
                    root = roots.IRacingRoot,
                    archive_root = roots.ArchiveRoot,
                    limit = 50
                }, cancellationToken);
                var races = DashboardMapper.Map(response)
                    .Select((race, index) => index < 6 ? EnrichRaceOverview(race) : race)
                    .ToList();
                foreach (var archived in LoadArchivedRaces())
                    if (races.All(race => !string.Equals(race.Id, archived.Id, StringComparison.OrdinalIgnoreCase))) races.Add(archived);
                return new DashboardRefresh(response, races.ToArray());
            }, cancellationToken);
            var discoveryTask = ReadRefreshSectionAsync("Race sessions", async () =>
            {
                var response = await CallBackendAsync("discover_iracing_sessions", new
                {
                    root = roots.IRacingRoot,
                    races_only = false,
                    limit = 200
                }, cancellationToken);
                if (response.ValueKind != JsonValueKind.Object
                    || !response.TryGetProperty("sessions", out var sessions)
                    || sessions.ValueKind != JsonValueKind.Array)
                    throw new InvalidDataException("The race-session response did not include a sessions array.");
                return response;
            }, cancellationToken);
            var setupTask = ReadRefreshSectionAsync("Local setups", async () =>
            {
                var response = await CallBackendAsync("catalog_iracing_setups", new
                {
                    root = roots.IRacingRoot,
                    archive_root = roots.ArchiveRoot,
                    maximum_entries = 500
                }, cancellationToken);
                if (response.ValueKind != JsonValueKind.Object
                    || !response.TryGetProperty("entries", out var entries)
                    || entries.ValueKind != JsonValueKind.Array)
                    throw new InvalidDataException("The setup response did not include an entries array.");
                var setups = RuntimeMapper.Setups(response).ToList();
                foreach (var archived in LoadArchivedSetups())
                    if (setups.All(setup => !string.Equals(setup.Id, archived.Id, StringComparison.OrdinalIgnoreCase))) setups.Add(archived);
                return (IReadOnlyList<LocalSetup>)setups.ToArray();
            }, cancellationToken);
            var garageBeforeProbe = Garage61;
            var garageTask = ReadRefreshSectionAsync("Garage61", async () =>
            {
                var response = await CallBackendAsync("garage61_auth_status", new { archive_root = roots.ArchiveRoot }, cancellationToken);
                if (response.ValueKind != JsonValueKind.Object)
                    throw new InvalidDataException("The Garage61 response was not an object.");
                return RuntimeMapper.Garage61(response, garageBeforeProbe);
            }, cancellationToken);

            await Task.WhenAll(healthTask, dashboardTask, discoveryTask, setupTask);
            var health = await healthTask;
            var dashboard = await dashboardTask;
            var discovery = await discoveryTask;
            var setups = await setupTask;
            var events = BuildEventSection(dashboard, discovery);
            var stagedRaces = dashboard.Success ? dashboard.Value!.Races : previous.Races;
            var stagedSetups = setups.Success ? setups.Value! : previous.Setups;
            var stagedEvents = events.Success ? events.Value! : previous.EventSessions;
            var groups = events.Success ? DashboardMapper.GroupEvents(stagedEvents) : previous.EventGroups;
            var cars = ReadRefreshSection("Installed cars", () => DiscoverCars(stagedRaces, stagedSetups));
            var tracks = ReadRefreshSection("Installed tracks", () => DiscoverTracks(stagedRaces));
            var next = new LocalInventorySnapshot(
                previous.Generation + 1,
                roots,
                stagedRaces,
                stagedEvents,
                groups,
                stagedSetups,
                cars.Success ? cars.Value! : previous.Cars,
                tracks.Success ? tracks.Value! : previous.Tracks,
                [
                    SectionStatus("Race recordings", dashboard),
                    SectionStatus("Race sessions", events),
                    SectionStatus("Local setups", setups),
                    SectionStatus("Installed cars", cars),
                    SectionStatus("Installed tracks", tracks)
                ]);

            RecordSectionFailure(dashboard);
            RecordSectionFailure(discovery);
            if (dashboard.Success && discovery.Success) RecordSectionFailure(events);
            RecordSectionFailure(setups);
            RecordSectionFailure(cars);
            RecordSectionFailure(tracks);
            if (!RefreshRootsAreCurrent(roots))
            {
                MarkRefreshDirty();
                return;
            }

            PublishLocalInventory(next, dashboard.Success);
            ConfigureWatchers(roots.IRacingRoot);
            if (health.Success)
            {
                _lastBackendHealth = health.Value!;
                UpdateHealth("backend", "Race analysis", health.Value!.Ok ? "ready" : "unavailable",
                    health.Value.Ok ? "Ready" : "Needs attention", true);
            }
            else
            {
                RecordSectionFailure(health);
                UpdateHealth("backend", "Race analysis", "unavailable", "Health check failed; local inventory retained", true);
            }
            UpdateHealth("repository", "Coach data", "ready", "Coach folder ready");
            Diagnostics = BuildDiagnostics(_lastBackendHealth);
            LastUpdated = DateTimeOffset.Now;
            HomeDataReady = true;
            if (showToast) Toast = next.Sections.All(section => section.Current)
                ? "Your local racing data is up to date."
                : "Some local data could not be updated. The last complete values are still shown.";
            RaiseChanged();
            QueueMissingHomeRaceAnalysis();

            var observer = ObserveGarageRefreshAsync(garageTask, roots);
            lock (_refreshSync) _garageRefreshTask = observer;
        }
        finally
        {
            _refreshGate.Release();
        }
    }

    private LocalInventorySnapshot CaptureLocalInventory(RefreshRoots roots)
    {
        lock (_inventoryGate)
        {
            var retainCurrentRoots = _localInventory is null || Equals(_localInventory.Roots, roots);
            return new LocalInventorySnapshot(
                _localInventory?.Generation ?? LocalInventoryGeneration,
                roots,
                retainCurrentRoots ? Races.ToArray() : [],
                retainCurrentRoots ? EventSessions.ToArray() : [],
                retainCurrentRoots ? EventGroups.ToArray() : [],
                retainCurrentRoots ? Setups.ToArray() : [],
                retainCurrentRoots ? Cars.ToArray() : [],
                retainCurrentRoots ? Tracks.ToArray() : [],
                retainCurrentRoots ? LocalInventorySections.ToArray() : []);
        }
    }

    private RefreshSection<IReadOnlyList<RecentRace>> BuildEventSection(
        RefreshSection<DashboardRefresh> dashboard,
        RefreshSection<JsonElement> discovery)
    {
        if (!dashboard.Success)
            return RefreshSection<IReadOnlyList<RecentRace>>.Failed(dashboard.Error!);
        if (!discovery.Success)
            return RefreshSection<IReadOnlyList<RecentRace>>.Failed(discovery.Error!);
        return ReadRefreshSection("Race sessions", () =>
        {
            var homeRaces = dashboard.Value!.Races;
            var sessions = DashboardMapper.MapEvents(dashboard.Value.Response, discovery.Value).Select(session =>
            {
                var homeRace = homeRaces.FirstOrDefault(candidate => SameRace(candidate, session));
                return homeRace?.Overview is { } overview
                    ? MergeRaceAnalysisState(session, overview, homeRace.Analyzed, homeRace.AnalysisPath)
                    : session;
            }).ToList();
            foreach (var archived in homeRaces)
                if (sessions.All(race => !string.Equals(race.Id, archived.Id, StringComparison.OrdinalIgnoreCase))) sessions.Add(archived);
            return (IReadOnlyList<RecentRace>)sessions.ToArray();
        });
    }

    private void PublishLocalInventory(LocalInventorySnapshot snapshot, bool dashboardCurrent)
    {
        lock (_inventoryGate)
        {
            if (_localInventory is null || Equals(_localInventory.Roots, snapshot.Roots))
            {
                var current = Races.Concat(EventSessions).OfType<RecentRace>()
                    .Where(race => race.Overview is not null)
                    .ToArray();
                RecentRace PreserveAnalysis(RecentRace candidate)
                {
                    var existing = current.FirstOrDefault(race => SameRace(race, candidate));
                    return existing?.Overview is { } overview
                        ? MergeRaceAnalysisState(candidate, overview, existing.Analyzed, existing.AnalysisPath)
                        : candidate;
                }
                var races = snapshot.Races.Select(PreserveAnalysis).ToArray();
                var sessions = snapshot.EventSessions.Select(PreserveAnalysis).ToArray();
                snapshot = snapshot with
                {
                    Races = races,
                    EventSessions = sessions,
                    EventGroups = DashboardMapper.GroupEvents(sessions)
                };
            }
            Races.Clear();
            Races.AddRange(snapshot.Races);
            EventSessions.Clear();
            EventSessions.AddRange(snapshot.EventSessions);
            EventGroups.Clear();
            EventGroups.AddRange(snapshot.EventGroups);
            Setups.Clear();
            Setups.AddRange(snapshot.Setups);
            Cars.Clear();
            Cars.AddRange(snapshot.Cars);
            Tracks.Clear();
            Tracks.AddRange(snapshot.Tracks);
            _localInventory = snapshot;
            LocalInventoryGeneration = snapshot.Generation;
            LocalInventorySections = snapshot.Sections;

            if (!AvailableRaceFilters.Contains(RaceFilter)) RaceFilter = RaceBrowserFilter.All;
            DataMessage = dashboardCurrent
                ? Races.Count == 0
                    ? "No finalized race recordings were found. iRacing recordings will appear here automatically."
                    : $"{Races.Count} race recording{(Races.Count == 1 ? string.Empty : "s")} found."
                : "Some race recordings could not be updated. The last complete inventory is still shown.";
            ApplyRaceDefaults();
            ApplyBrowserDefault();
            if (SelectedSetupId.Length == 0 || Setups.All(setup => setup.Id != SelectedSetupId))
                SelectedSetupId = Setups.FirstOrDefault()?.Id ?? string.Empty;
            if (CompareSetupId.Length == 0 || Setups.All(setup => setup.Id != CompareSetupId))
                CompareSetupId = Setups.FirstOrDefault(setup => setup.Id != SelectedSetupId)?.Id ?? string.Empty;
        }
    }

    private async Task ObserveGarageRefreshAsync(Task<RefreshSection<Garage61Connection>> garageTask, RefreshRoots roots)
    {
        try
        {
            var garage = await garageTask;
            if (_disposed) return;
            if (!RefreshRootsAreCurrent(roots)) return;
            if (garage.Success) Garage61 = garage.Value!;
            else
            {
                Garage61 = Garage61StatusReducer.ApplyFailure(Garage61, garage.Error!.Exception);
                RecordSectionFailure(garage);
            }
            UpdateHealth("garage61", "Garage61", Garage61.Available ? "ready" : Garage61.Configured ? "warning" : "neutral",
                Garage61StatusReducer.Label(Garage61));
            Diagnostics = BuildDiagnostics(_lastBackendHealth);
            RaiseChanged();
        }
        catch (OperationCanceledException) when (_refreshLifetime.IsCancellationRequested) { }
    }

    private static async Task<RefreshSection<T>> ReadRefreshSectionAsync<T>(
        string name,
        Func<Task<T>> read,
        CancellationToken cancellationToken)
    {
        try
        {
            return RefreshSection<T>.Succeeded(await read());
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception ex)
        {
            return RefreshSection<T>.Failed(new RefreshSectionError(name, ex));
        }
    }

    private static RefreshSection<T> ReadRefreshSection<T>(string name, Func<T> read)
    {
        try
        {
            return RefreshSection<T>.Succeeded(read());
        }
        catch (Exception ex)
        {
            return RefreshSection<T>.Failed(new RefreshSectionError(name, ex));
        }
    }

    private static LocalInventorySectionStatus SectionStatus<T>(string name, RefreshSection<T> section) =>
        section.Success
            ? new LocalInventorySectionStatus(name, true, "Ready")
            : new LocalInventorySectionStatus(name, false, "Update failed; showing the last complete values.");

    private void RecordSectionFailure<T>(RefreshSection<T> section)
    {
        if (section.Error is { } error) RecordRefreshFailure(error.Name, error.Exception);
    }

    private void RecordRefreshFailure(string scope, Exception exception)
    {
        LastRecoverableError = StructuredAppLog.Record(scope, exception, AppVersion, Settings.LogsRoot, _pathProvider.UserProfile);
        ServiceFailureCount++;
    }

    private bool RefreshRootsAreCurrent(RefreshRoots roots) =>
        string.Equals(Settings.IRacingRoot, roots.IRacingRoot, StringComparison.Ordinal)
        && string.Equals(Settings.ArchiveRoot, roots.ArchiveRoot, StringComparison.Ordinal)
        && string.Equals(Settings.IRacingInstallRoot, roots.IRacingInstallRoot, StringComparison.Ordinal);

    private void MarkRefreshDirty()
    {
        lock (_refreshSync)
        {
            if (_disposed) return;
            if (_refreshTask is { IsCompleted: false })
            {
                _refreshDirty = true;
                return;
            }
            _refreshDirty = false;
            _refreshTask = Task.Run(RefreshLoopAsync);
        }
    }

    private sealed record RefreshRoots(string IRacingRoot, string ArchiveRoot, string IRacingInstallRoot);
    private sealed record DashboardRefresh(JsonElement Response, IReadOnlyList<RecentRace> Races);
    private sealed record RefreshSectionError(string Name, Exception Exception);
    private sealed record RefreshSection<T>(bool Success, T? Value, RefreshSectionError? Error)
    {
        public static RefreshSection<T> Succeeded(T value) => new(true, value, null);
        public static RefreshSection<T> Failed(RefreshSectionError error) => new(false, default, error);
    }
    private sealed record LocalInventorySnapshot(
        long Generation,
        RefreshRoots Roots,
        IReadOnlyList<RecentRace> Races,
        IReadOnlyList<RecentRace> EventSessions,
        IReadOnlyList<RaceEventGroup> EventGroups,
        IReadOnlyList<LocalSetup> Setups,
        IReadOnlyList<InstalledCar> Cars,
        IReadOnlyList<InstalledTrack> Tracks,
        IReadOnlyList<LocalInventorySectionStatus> Sections);

    private async Task<JsonElement> SafeToolAsync(string name, object arguments, CancellationToken cancellationToken)
    {
        try
        {
            return await CallBackendAsync(name, arguments, cancellationToken);
        }
        catch (Exception ex)
        {
            ReportUnhandledException($"backend tool {name}", ex);
            return JsonSerializer.SerializeToElement(new { ok = false, status = "unavailable", message = Bound(ex.Message) });
        }
    }

    private Task<JsonElement> CallBackendAsync(
        string name,
        object arguments,
        CancellationToken cancellationToken,
        DetachedBackendOperationPolicy detachedPolicy = DetachedBackendOperationPolicy.CancelWhenNoSubscribers)
    {
        var requestKey = $"{name}|{JsonSerializer.Serialize(arguments)}";
        return _backendOperations.SubscribeAsync(
            requestKey,
            operationCancellation => ExecuteBackendCallAsync(name, arguments, operationCancellation),
            cancellationToken,
            detachedPolicy);
    }

    private async Task<JsonElement> ExecuteBackendCallAsync(string name, object arguments, CancellationToken cancellationToken)
    {
        var garage61Call = name.Contains("garage61", StringComparison.OrdinalIgnoreCase);
        if (garage61Call) Garage61RequestCount++; else LocalRequestCount++;
        LastServiceRequest = DateTimeOffset.Now;
        Interlocked.Increment(ref _serviceRequestsInFlight);
        RaiseChanged();
        try
        {
            var result = await _backend.CallToolAsync(CreateBackendConfiguration(), name, arguments, cancellationToken);
            if (CacheHit(result, "analysis_cache") || CacheHit(result, "knowledge_cache") ||
                (result.ValueKind == JsonValueKind.Object && result.TryGetProperty("cache_hit", out var direct) && direct.ValueKind == JsonValueKind.True))
            {
                ApiCacheHitCount++;
            }
            return result;
        }
        catch
        {
            ServiceFailureCount++;
            throw;
        }
        finally
        {
            Interlocked.Decrement(ref _serviceRequestsInFlight);
            RaiseChanged();
        }
    }

    private static bool CacheHit(JsonElement result, string property) =>
        result.ValueKind == JsonValueKind.Object && result.TryGetProperty(property, out var cache) && cache.ValueKind == JsonValueKind.Object &&
        cache.TryGetProperty("cache_hit", out var hit) && hit.ValueKind == JsonValueKind.True;

    public async Task AnalyzeRaceAsync(RecentRace race, CancellationToken cancellationToken = default, bool force = false)
    {
        if (AnalysisLoading && string.Equals(SelectedRaceSessionId, race.Id, StringComparison.Ordinal))
        {
            Notify("That race is already being analyzed.");
            return;
        }
        SelectRaceSession(race);

        // Progressive Tuning and Race Analysis share one analysis workspace. When the
        // requested race is already the loaded one, opening it is a pointer check - not a
        // reason to discard the evidence and re-read the recording. SelectTuningRaceAsync
        // has always short-circuited this way; the analysis path did not, which is why
        // Progressive Tuning -> Race Analysis paid a full re-analysis.
        if (!force && CurrentAnalysis is not null && ProgressiveTuningCoordinator.Matches(race, CurrentAnalysis.TuningIdentity))
        {
            AnalysisWorkspaceOpen = true;
            AnalysisLoading = false;
            AnalysisMessage = string.Empty;
            RaiseChanged();
            return;
        }

        var request = new AnalysisPublicationRequest(
            Volatile.Read(ref _analysisSelectionEpoch),
            race.Id,
            race.EffectiveSelector,
            Settings.IRacingRoot,
            Settings.ArchiveRoot);
        AnalysisWorkspaceOpen = true;
        AnalysisLoading = true;
        AnalysisMessage = race.Analyzed ? "Opening telemetry…" : "Reading telemetry…";
        CurrentRaceCard = null;
        CurrentAnalysis = null;
        Garage61ReferenceMessage = string.Empty;
        RaiseChanged();
        if (!force && TryLoadUiAnalysisCache(race))
        {
            RaiseChanged();
            return;
        }
        await RunJobAsync($"Analyze {race.Track}", $"session:{race.Id}", "Reading the recorded race", async token =>
        {
            var result = await CallBackendAsync("analyze_iracing_race", new
            {
                selector = request.Selector,
                iracing_root = request.IRacingRoot,
                archive_root = request.ArchiveRoot,
                target_hz = 20
            }, token);
            EnsureResponseMatchesSession(race, result);
            var mappedCard = RuntimeMapper.HasCurrentAnalysisProfile(result)
                ? RuntimeMapper.RaceCard(result)
                : null;
            var mappedAnalysis = RuntimeMapper.Analysis(result);
            EnsureAnalysisMatchesSession(race, mappedAnalysis);
            SaveUiAnalysisCache(race, result);
            if (!AnalysisRequestStillCurrent(request)) return;
            CurrentRaceCard = mappedCard;
            CurrentAnalysis = mappedAnalysis;
            ApplySuccessfulRaceAnalysis(race, mappedAnalysis, AnalysisPathFromResponse(result));
        }, cancellationToken);
        if (Volatile.Read(ref _analysisSelectionEpoch) != request.Epoch) return;
        AnalysisLoading = false;
        if (!AnalysisRequestStillCurrent(request))
        {
            AnalysisMessage = "The race selection changed before this analysis finished. The older result was not shown.";
            RaiseChanged();
            return;
        }
        AnalysisMessage = CurrentAnalysis is null ? "This recording could not be opened. Retry or copy the support details." : string.Empty;
        RaiseChanged();
    }

    public Task OpenRaceAsync(RecentRace race, CancellationToken cancellationToken = default) => AnalyzeRaceAsync(race, cancellationToken);
    public Task ReanalyzeRaceAsync(CancellationToken cancellationToken = default) => SelectedRaceSession is { } race
        ? AnalyzeRaceAsync(race, cancellationToken, force: true)
        : Task.CompletedTask;

    public void CloseAnalysisWorkspace()
    {
        Interlocked.Increment(ref _analysisSelectionEpoch);
        AnalysisWorkspaceOpen = false;
        AnalysisLoading = false;
        AnalysisMessage = string.Empty;
        RaiseChanged();
    }

    public async Task OpenRaceFromHomeAsync(RecentRace race, CancellationToken cancellationToken = default)
    {
        var session = EventSessions.FirstOrDefault(candidate =>
            SameSessionPhase(candidate, race) &&
            (string.Equals(candidate.Id, race.Id, StringComparison.OrdinalIgnoreCase) ||
             (race.EventKey.Length > 0 && string.Equals(candidate.EventKey, race.EventKey, StringComparison.OrdinalIgnoreCase)) ||
             (race.EffectiveSelector.Length > 0 && string.Equals(candidate.EffectiveSelector, race.EffectiveSelector, StringComparison.OrdinalIgnoreCase)))) ?? race;

        Navigate("analysis");
        SelectRaceSession(session);
        await AnalyzeRaceAsync(session, cancellationToken);
    }

    public void SelectRaceSession(RecentRace session)
    {
        Interlocked.Increment(ref _analysisSelectionEpoch);
        SelectedRaceSessionId = session.Id;
        RaiseChanged();
    }

    private bool AnalysisRequestStillCurrent(AnalysisPublicationRequest request) =>
        Volatile.Read(ref _analysisSelectionEpoch) == request.Epoch
        && string.Equals(SelectedRaceSessionId, request.RaceId, StringComparison.Ordinal)
        && string.Equals(Settings.IRacingRoot, request.IRacingRoot, StringComparison.Ordinal)
        && string.Equals(Settings.ArchiveRoot, request.ArchiveRoot, StringComparison.Ordinal);

    private sealed record AnalysisPublicationRequest(
        long Epoch,
        string RaceId,
        string Selector,
        string IRacingRoot,
        string ArchiveRoot);

    private void SetPlanInput<T>(ref T field, T value)
    {
        if (EqualityComparer<T>.Default.Equals(field, value)) return;
        field = value;
        Interlocked.Increment(ref _planRequestEpoch);
    }

    private void SetExperimentInput<T>(ref T field, T value)
    {
        if (EqualityComparer<T>.Default.Equals(field, value)) return;
        field = value;
        Interlocked.Increment(ref _experimentRequestEpoch);
    }

    private void SetStartingTuneInput(ref string field, string value)
    {
        if (string.Equals(field, value, StringComparison.Ordinal)) return;
        field = value;
        Interlocked.Increment(ref _startingTuneSelectionEpoch);
    }

    public async Task GeneratePlanAsync(CancellationToken cancellationToken = default)
    {
        var race = SelectedPlanRace;
        if (race is null)
        {
            Notify("Choose a recorded race first.");
            return;
        }
        if (SelectedPlanCarId.Length > 0 &&
            !string.Equals(SelectedPlanCarId, race.CarPath.Length > 0 ? race.CarPath : race.Car, StringComparison.OrdinalIgnoreCase))
        {
            PlanMessage = "Choose a reference race recorded with the selected car so the plan uses matching evidence.";
            Notify(PlanMessage);
            return;
        }
        var raceTrackKey = $"{race.Track}|{race.Layout}";
        if (SelectedPlanTrack.Length > 0 && !string.Equals(SelectedPlanTrack, raceTrackKey, StringComparison.OrdinalIgnoreCase))
        {
            PlanMessage = "Choose a comparable recorded race for this track. The app will not invent fuel or setup guidance for an unmatched event.";
            Notify(PlanMessage);
            RaiseChanged();
            return;
        }
        if (!race.Analyzed || race.AnalysisPath.Length == 0)
        {
            PlanMessage = "Analyze this race first so planning can use its exact recorded context.";
            Notify(PlanMessage);
            return;
        }

        var request = new PlanPublicationRequest(
            Interlocked.Increment(ref _planRequestEpoch),
            race.Id,
            race.EffectiveSelector,
            race.AnalysisPath,
            SelectedPlanCarId,
            SelectedPlanTrack,
            PlanSetupType,
            PlanDistanceMode,
            PlanDistanceValue,
            Settings.IRacingRoot,
            Settings.ArchiveRoot);

        try
        {
            var historyTask = CallBackendAsync("iracing_strategy_history", new
            {
                analysis_path = request.AnalysisPath,
                archive_root = request.ArchiveRoot,
                include_other_seasons = false,
                limit = 200
            }, cancellationToken);
            var analysisTask = CallBackendAsync("analyze_iracing_race", new
            {
                selector = request.Selector,
                iracing_root = request.IRacingRoot,
                archive_root = request.ArchiveRoot,
                target_hz = 20
            }, cancellationToken);
            await Task.WhenAll(historyTask, analysisTask);
            var result = await historyTask;
            var stagedScenarios = RuntimeMapper.Strategy(result).ToArray();
            var stagedBriefing = RuntimeMapper.Plan(await analysisTask, request.DistanceValue, request.DistanceMode);
            if (!PlanRequestStillCurrent(request)) return;
            StrategyScenarios.Clear();
            StrategyScenarios.AddRange(stagedScenarios);
            PlanBriefing = stagedBriefing;
            PersistPortableArtifact("strategy-history", $"strategy-{race.Id}", new
            {
                schemaVersion = 1,
                raceId = race.Id,
                createdUtc = DateTimeOffset.UtcNow,
                result
            });
            PlanGenerated = true;
            PlanMessage = StrategyScenarios.Count == 0
                ? "No directly comparable historical runs exist yet. More recorded races will improve this view."
                : $"Found {StrategyScenarios.Count} directly comparable recorded run{(StrategyScenarios.Count == 1 ? string.Empty : "s")}.";
            Notify("Race history checked.");
        }
        catch (Exception ex) when (ex is BackendProtocolException or BackendDomainException or InvalidDataException)
        {
            if (!PlanRequestStillCurrent(request)) return;
            PlanMessage = Bound(ex.Message);
            Notify("The plan could not be built from the available history.");
        }
        RaiseChanged();
    }

    private bool PlanRequestStillCurrent(PlanPublicationRequest request) =>
        Volatile.Read(ref _planRequestEpoch) == request.Epoch
        && string.Equals(SelectedPlanRaceId, request.RaceId, StringComparison.Ordinal)
        && SelectedPlanRace is { } currentRace
        && string.Equals(currentRace.EffectiveSelector, request.Selector, StringComparison.Ordinal)
        && string.Equals(currentRace.AnalysisPath, request.AnalysisPath, StringComparison.Ordinal)
        && string.Equals(SelectedPlanCarId, request.CarId, StringComparison.Ordinal)
        && string.Equals(SelectedPlanTrack, request.Track, StringComparison.Ordinal)
        && string.Equals(PlanSetupType, request.SetupType, StringComparison.Ordinal)
        && string.Equals(PlanDistanceMode, request.DistanceMode, StringComparison.Ordinal)
        && PlanDistanceValue.Equals(request.DistanceValue)
        && string.Equals(Settings.IRacingRoot, request.IRacingRoot, StringComparison.Ordinal)
        && string.Equals(Settings.ArchiveRoot, request.ArchiveRoot, StringComparison.Ordinal);

    private sealed record PlanPublicationRequest(
        long Epoch,
        string RaceId,
        string Selector,
        string AnalysisPath,
        string CarId,
        string Track,
        string SetupType,
        string DistanceMode,
        double DistanceValue,
        string IRacingRoot,
        string ArchiveRoot);

    public async Task GenerateExperimentAsync(CancellationToken cancellationToken = default)
    {
        var race = SelectedTuningRace;
        if (race is null)
        {
            Notify("Choose an analyzed open-setup race first.");
            return;
        }
        var symptom = BuildTuningSymptom();
        if (string.IsNullOrWhiteSpace(symptom))
        {
            Notify("Choose the handling balance before asking for a change.");
            return;
        }
        SymptomText = symptom;
        var request = new ExperimentPublicationRequest(
            Interlocked.Increment(ref _experimentRequestEpoch),
            Volatile.Read(ref _tuningRaceSelectionEpoch),
            race.Id,
            race.AnalysisPath,
            symptom,
            Settings.ArchiveRoot);

        try
        {
            var result = await CallBackendAsync("recommend_open_setup_tuning", new
            {
                analysis_path = request.AnalysisPath,
                archive_root = request.ArchiveRoot,
                symptoms = request.Symptom,
                maximum_changes = 1
            }, cancellationToken);
            var staged = RuntimeMapper.Tuning(result);
            if (!ExperimentRequestStillCurrent(request)) return;
            TuningExperiment = staged;
            ExperimentGenerated = true;
            TuningMessage = "The recommendation is tied to this race's embedded setup and telemetry.";
            Notify("One controlled setup test is ready.");
        }
        catch (Exception ex) when (ex is BackendProtocolException or BackendDomainException or InvalidDataException)
        {
            if (!ExperimentRequestStillCurrent(request)) return;
            TuningMessage = Bound(ex.Message);
            Notify("A safe tuning change could not be recommended from this race.");
        }
        RaiseChanged();
    }

    private bool ExperimentRequestStillCurrent(ExperimentPublicationRequest request) =>
        Volatile.Read(ref _experimentRequestEpoch) == request.Epoch
        && Volatile.Read(ref _tuningRaceSelectionEpoch) == request.RaceSelectionEpoch
        && SelectedTuningRace is { } currentRace
        && string.Equals(currentRace.Id, request.RaceId, StringComparison.Ordinal)
        && string.Equals(currentRace.AnalysisPath, request.AnalysisPath, StringComparison.Ordinal)
        && string.Equals(BuildTuningSymptom(), request.Symptom, StringComparison.Ordinal)
        && string.Equals(Settings.ArchiveRoot, request.ArchiveRoot, StringComparison.Ordinal);

    private sealed record ExperimentPublicationRequest(
        long Epoch,
        long RaceSelectionEpoch,
        string RaceId,
        string AnalysisPath,
        string Symptom,
        string ArchiveRoot);

    public void AddTuningFeedback()
    {
        if (string.IsNullOrWhiteSpace(TuningBalance) && string.IsNullOrWhiteSpace(SymptomText)) return;
        var draft = new TuningFeedbackDraft(
            string.IsNullOrWhiteSpace(TuningCorner) ? "Whole lap" : TuningCorner.Trim(),
            TuningRunPhase,
            TuningCornerPhase,
            string.IsNullOrWhiteSpace(TuningBalance) ? SymptomText.Trim() : TuningBalance.Trim(),
            TuningSeverity,
            TuningConfidence,
            TuningPriority,
            TuningNotes.Trim(),
            Guid.NewGuid().ToString("N"));
        if (!TuningFeedback.Contains(draft))
        {
            TuningFeedback.Add(draft);
            Interlocked.Increment(ref _experimentRequestEpoch);
        }
        RaiseChanged();
    }

    public void RemoveTuningFeedback(TuningFeedbackDraft draft)
    {
        if (TuningFeedback.Remove(draft)) Interlocked.Increment(ref _experimentRequestEpoch);
        RaiseChanged();
    }

    public async Task SelectTuningRaceAsync(string? raceId, CancellationToken cancellationToken = default)
    {
        var selectionEpoch = Interlocked.Increment(ref _tuningRaceSelectionEpoch);
        Interlocked.Increment(ref _tuningTargetSelectionEpoch);
        SelectedTuningRaceId = raceId?.Trim() ?? string.Empty;
        TuningCorner = "Whole lap";
        var race = SelectedTuningRace;
        if (race is null)
        {
            TuningFeedback.Clear();
            TuningDraft = new ProgressiveTuningDraft();
            SelectedTuningMap = null;
            SelectedTuningTarget = null;
            SelectedTuningTargetRaceId = string.Empty;
            SelectedTuningTurnId = string.Empty;
            StructuredTuningResult = null;
            TuningExperiment = null;
            ExperimentGenerated = false;
            CurrentRaceCard = null;
            CurrentAnalysis = null;
            RaiseChanged();
            return;
        }
        if (CurrentAnalysis is not null && ProgressiveTuningCoordinator.Matches(race, CurrentAnalysis.TuningIdentity))
        {
            ActivateTuningAnalysis(race);
            RaiseChanged();
            return;
        }

        // Do not expose the previous race's evidence while the new exact
        // analysis is still loading.
        TuningFeedback.Clear();
        TuningDraft = new ProgressiveTuningDraft();
        SelectedTuningMap = null;
        SelectedTuningTarget = null;
        SelectedTuningTargetRaceId = string.Empty;
        SelectedTuningTurnId = string.Empty;
        StructuredTuningResult = null;
        TuningExperiment = null;
        ExperimentGenerated = false;
        CurrentRaceCard = null;
        CurrentAnalysis = null;

        TuningMessage = "Loading this race's track and telemetry…";
        RaiseChanged();
        if (TryLoadUiAnalysisCache(race))
        {
            if (!IsCurrentTuningRaceSelection(selectionEpoch, race.Id)) return;
            ActivateTuningAnalysis(race);
            RaiseChanged();
            return;
        }

        try
        {
            var result = await CallBackendAsync("analyze_iracing_race", new
            {
                selector = race.EffectiveSelector,
                iracing_root = Settings.IRacingRoot,
                archive_root = Settings.ArchiveRoot,
                target_hz = 20
            }, cancellationToken);
            EnsureResponseMatchesSession(race, result);
            var raceCard = RuntimeMapper.HasCurrentAnalysisProfile(result)
                ? RuntimeMapper.RaceCard(result)
                : null;
            var analysis = RuntimeMapper.Analysis(result);
            EnsureAnalysisMatchesSession(race, analysis);
            SaveUiAnalysisCache(race, result);
            if (!IsCurrentTuningRaceSelection(selectionEpoch, race.Id)) return;
            CurrentRaceCard = raceCard;
            CurrentAnalysis = analysis;
            ActivateTuningAnalysis(race);
        }
        catch (Exception ex) when (ex is BackendProtocolException or BackendDomainException or InvalidDataException)
        {
            if (!IsCurrentTuningRaceSelection(selectionEpoch, race.Id)) return;
            TuningMessage = Bound(ex.Message);
            RaiseChanged();
            throw;
        }
        RaiseChanged();
    }

    public Task SelectTuningTurnAsync(string? cornerId, CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();
        SelectedTuningTurnId = cornerId?.Trim() ?? string.Empty;
        RaiseChanged();
        return Task.CompletedTask;
    }

    public async Task UpsertTuningFeedbackAsync(ProgressiveTuningFeedback feedback, CancellationToken cancellationToken = default)
    {
        using var mutation = await EnterTuningDraftMutationAsync(cancellationToken);
        if (string.IsNullOrWhiteSpace(feedback.FeedbackId)) feedback = feedback with { FeedbackId = Guid.NewGuid().ToString("N") };
        feedback = ValidateTuningFeedback(feedback);
        var draft = TuningDraft;
        var items = draft.Feedback.ToList();
        var index = items.FindIndex(item => string.Equals(item.FeedbackId, feedback.FeedbackId, StringComparison.Ordinal));
        if (index >= 0) items[index] = feedback;
        else items.Add(feedback);
        var updated = draft with { Feedback = items, UpdatedUtc = DateTimeOffset.UtcNow };
        await SaveTuningDraftSnapshotAsync(updated, cancellationToken);
        if (!IsCurrentTuningDraft(draft)) return;
        TuningDraft = updated;
        SyncLegacyTuningFeedback();
        RaiseChanged();
    }

    public async Task ReplaceTuningFeedbackBatchAsync(
        string cornerId,
        string runPhase,
        IEnumerable<ProgressiveTuningFeedback> items,
        CancellationToken cancellationToken = default)
    {
        using var mutation = await EnterTuningDraftMutationAsync(cancellationToken);
        var normalizedCorner = cornerId?.Trim() ?? string.Empty;
        var normalizedPhase = RequireTuningRunPhase(runPhase ?? string.Empty);
        _ = RequireCurrentTuningTurn(normalizedCorner);
        var draft = TuningDraft;
        var replacement = items.Select(item => ValidateTuningFeedback(item with
            {
                FeedbackId = string.IsNullOrWhiteSpace(item.FeedbackId) ? Guid.NewGuid().ToString("N") : item.FeedbackId,
                CornerId = normalizedCorner,
                RunPhase = normalizedPhase
            }))
            .GroupBy(item => item.FeedbackId, StringComparer.Ordinal)
            .Select(group => group.Last())
            .ToArray();
        var retained = draft.Feedback.Where(item =>
            !string.Equals(item.CornerId, normalizedCorner, StringComparison.Ordinal)
            || !string.Equals(NormalizeTuningRunPhase(item.RunPhase), normalizedPhase, StringComparison.Ordinal));
        var updated = draft with { Feedback = retained.Concat(replacement).ToArray(), UpdatedUtc = DateTimeOffset.UtcNow };
        await SaveTuningDraftSnapshotAsync(updated, cancellationToken);
        if (!IsCurrentTuningDraft(draft)) return;
        TuningDraft = updated;
        SyncLegacyTuningFeedback();
        RaiseChanged();
    }

    public Task SaveTuningFeedbackAsync(ProgressiveTuningFeedback feedback, CancellationToken cancellationToken = default) =>
        UpsertTuningFeedbackAsync(feedback, cancellationToken);

    public async Task RemoveTuningFeedbackAsync(string feedbackId, CancellationToken cancellationToken = default)
    {
        using var mutation = await EnterTuningDraftMutationAsync(cancellationToken);
        var draft = TuningDraft;
        var items = draft.Feedback.Where(item => !string.Equals(item.FeedbackId, feedbackId, StringComparison.Ordinal)).ToArray();
        if (items.Length == draft.Feedback.Count) return;
        var updated = draft with { Feedback = items, UpdatedUtc = DateTimeOffset.UtcNow };
        await SaveTuningDraftSnapshotAsync(updated, cancellationToken);
        if (!IsCurrentTuningDraft(draft)) return;
        TuningDraft = updated;
        SyncLegacyTuningFeedback();
        RaiseChanged();
    }

    public async Task SaveTuningGeneralNoteAsync(string note, CancellationToken cancellationToken = default)
    {
        using var mutation = await EnterTuningDraftMutationAsync(cancellationToken);
        var value = note?.Trim() ?? string.Empty;
        if (value.Length > 8_000) throw new InvalidOperationException("General feedback must be 8,000 characters or fewer.");
        var draft = TuningDraft;
        var updated = draft with { GeneralNote = value, UpdatedUtc = DateTimeOffset.UtcNow };
        await SaveTuningDraftSnapshotAsync(updated, cancellationToken);
        if (!IsCurrentTuningDraft(draft)) return;
        TuningDraft = updated;
        TuningNotes = value;
        RaiseChanged();
    }

    public async Task SaveTuningGoalAsync(string goal, CancellationToken cancellationToken = default)
    {
        using var mutation = await EnterTuningDraftMutationAsync(cancellationToken);
        var normalized = goal?.Trim().ToLowerInvariant() ?? string.Empty;
        if (normalized is not ("long-run-pace" or "tire-life" or "restart-pace" or "stability"))
            throw new InvalidOperationException("Choose long-run pace, tire life, restart pace, or stability.");
        var draft = TuningDraft;
        var updated = draft with { Goal = normalized, UpdatedUtc = DateTimeOffset.UtcNow };
        await SaveTuningDraftSnapshotAsync(updated, cancellationToken);
        if (!IsCurrentTuningDraft(draft)) return;
        TuningDraft = updated;
        RaiseChanged();
    }

    public async Task SaveTuningRepresentativeRunsAsync(IEnumerable<string> runIds, CancellationToken cancellationToken = default)
    {
        using var mutation = await EnterTuningDraftMutationAsync(cancellationToken);
        var draft = TuningDraft;
        if (SelectedTuningRace is null
            || !ProgressiveTuningCoordinator.Matches(SelectedTuningRace, draft.RepresentativeSession)
            || CurrentAnalysis is null
            || !ProgressiveTuningCoordinator.Matches(SelectedTuningRace, CurrentAnalysis.TuningIdentity))
            throw new InvalidOperationException("Reload the representative race before choosing its runs.");
        var available = CurrentAnalysis.Runs.Where(run => run.ComparisonEligible && run.CoachingReferenceLapCount >= 6)
            .Select(run => run.Number.ToString(CultureInfo.InvariantCulture)).ToHashSet(StringComparer.Ordinal);
        var selected = runIds.Select(value => value?.Trim() ?? string.Empty)
            .Where(value => value.Length > 0)
            .Distinct(StringComparer.Ordinal)
            .ToArray();
        if (selected.Length == 0 || selected.Length > 3 || selected.Any(value => !available.Contains(value)))
            throw new InvalidOperationException("Choose one to three comparison-eligible runs with at least six clean green laps.");
        var updated = draft with { RepresentativeRunIds = selected, UpdatedUtc = DateTimeOffset.UtcNow };
        await SaveTuningDraftSnapshotAsync(updated, cancellationToken);
        if (!IsCurrentTuningDraft(draft)) return;
        TuningDraft = updated;
        RaiseChanged();
    }

    public Task SaveRepresentativeRunIdsAsync(IEnumerable<string> runIds, CancellationToken cancellationToken = default) =>
        SaveTuningRepresentativeRunsAsync(runIds, cancellationToken);

    public async Task ApplyTuningMapAsync(TuningMapView map, string trackConfigurationKey, CancellationToken cancellationToken = default)
    {
        using var mutation = await EnterTuningDraftMutationAsync(cancellationToken);
        var draft = TuningDraft;
        var identity = draft.RepresentativeSession;
        if (SelectedTuningRace is null
            || CurrentAnalysis is null
            || !ProgressiveTuningCoordinator.Matches(SelectedTuningRace, identity)
            || !ProgressiveTuningCoordinator.Matches(SelectedTuningRace, CurrentAnalysis.TuningIdentity))
            throw new InvalidOperationException("Reload the representative race before applying its track map.");
        if (string.IsNullOrWhiteSpace(identity.TrackConfigurationKey)
            || !string.Equals(identity.TrackConfigurationKey, trackConfigurationKey?.Trim(), StringComparison.Ordinal))
            throw new InvalidOperationException("The selected track map does not match this recording's exact track configuration.");
        if (string.IsNullOrWhiteSpace(map.MapIdentity) || map.Path.Count < 2)
            throw new InvalidOperationException("The selected track map has no stable identity or usable path.");
        var authoritativeGeometryHash = CurrentAnalysis?.VectorGeometry?.GeometryHash
            ?? CurrentAnalysis?.TuningMap?.GeometryHash;
        if (string.IsNullOrWhiteSpace(authoritativeGeometryHash))
            throw new InvalidOperationException("The authoritative track-geometry hash is unavailable. Reanalyze this recording first.");
        map = map with { GeometryHash = authoritativeGeometryHash.Trim().ToLowerInvariant() };
        if (map.Path.Any(point => !double.IsFinite(point.LapPercent) || point.LapPercent is < 0 or >= 1
                || !double.IsFinite(point.X) || !double.IsFinite(point.Y))
            || map.Turns.Any(turn => string.IsNullOrWhiteSpace(turn.CornerId)
                || !double.IsFinite(turn.StartPct) || turn.StartPct is < 0 or >= 1
                || !double.IsFinite(turn.ApexPct) || turn.ApexPct is < 0 or >= 1
                || !double.IsFinite(turn.EndPct) || turn.EndPct is < 0 or >= 1
                || !IsForwardTuningTurn(turn.StartPct, turn.ApexPct, turn.EndPct))
            || map.Turns.Select(turn => turn.CornerId).Distinct(StringComparer.Ordinal).Count() != map.Turns.Count)
            throw new InvalidOperationException("The selected track map contains invalid or duplicate turn bounds.");

        var store = new PortableTuningTurnAnnotationStore(Settings.CoachHome);
        TuningTurnAnnotationSet? annotations = null;
        try { annotations = store.Load(identity.TrackConfigurationKey, map.MapIdentity); }
        catch (InvalidDataException ex) { _ = StructuredAppLog.Record("tuning turn annotations", ex, AppVersion, Settings.LogsRoot, _pathProvider.UserProfile); }
        var merged = PortableTuningTurnAnnotationStore.Merge(map, annotations);
        var updated = draft with
        {
            MapIdentity = map.MapIdentity,
            TurnCorrections = annotations?.Corrections ?? [],
            UpdatedUtc = DateTimeOffset.UtcNow
        };
        await SaveTuningDraftSnapshotAsync(updated, cancellationToken);
        if (!IsCurrentTuningDraft(draft)) return;
        SelectedTuningMap = merged;
        if (CurrentAnalysis is { } current) CurrentAnalysis = current with { TuningMap = merged };
        TuningDraft = updated;
        RaiseChanged();
    }

    public Task SaveTuningTurnCorrectionAsync(string cornerId, string label, CancellationToken cancellationToken = default)
    {
        var turn = SelectedTuningMap?.Turns.FirstOrDefault(item => string.Equals(item.CornerId, cornerId, StringComparison.Ordinal));
        if (turn is null) throw new InvalidOperationException("Choose a recorded turn before correcting its label.");
        return SaveTuningTurnCorrectionAsync(new TuningTurnCorrectionDraft
        {
            CornerId = turn.CornerId,
            Label = label?.Trim() ?? string.Empty,
            StartPct = turn.StartPct,
            ApexPct = turn.ApexPct,
            EndPct = turn.EndPct,
            Note = turn.CorrectionHint ?? string.Empty,
            MapIdentity = SelectedTuningMap!.MapIdentity,
            GeometryHash = ProgressiveTuningCoordinator.GeometryHash(SelectedTuningMap),
            VerifiedAt = DateTimeOffset.UtcNow
        }, cancellationToken);
    }

    public async Task SaveTuningTurnCorrectionAsync(TuningTurnCorrectionDraft correction, CancellationToken cancellationToken = default)
    {
        using var mutation = await EnterTuningDraftMutationAsync(cancellationToken);
        var draft = TuningDraft;
        var identity = draft.RepresentativeSession;
        var map = SelectedTuningMap ?? throw new InvalidOperationException("A verified track map is required before correcting a turn.");
        if (SelectedTuningRace is null || !ProgressiveTuningCoordinator.Matches(SelectedTuningRace, identity))
            throw new InvalidOperationException("Reload the representative race before correcting its turn map.");
        if (string.IsNullOrWhiteSpace(identity.TrackConfigurationKey))
            throw new InvalidOperationException("The exact track configuration is unavailable for this recording.");
        if (!string.Equals(correction.MapIdentity, map.MapIdentity, StringComparison.Ordinal))
            throw new InvalidOperationException("The turn correction belongs to a different track map.");
        if (ProgressiveTuningCoordinator.GeometryHash(map).Length == 0)
            throw new InvalidOperationException("The authoritative track-geometry hash is unavailable. Reanalyze this recording first.");
        if (!double.IsFinite(correction.StartPct) || correction.StartPct is < 0 or >= 1
            || !double.IsFinite(correction.ApexPct) || correction.ApexPct is < 0 or >= 1
            || !double.IsFinite(correction.EndPct) || correction.EndPct is < 0 or >= 1
            || !IsForwardTuningTurn(correction.StartPct, correction.ApexPct, correction.EndPct))
            throw new InvalidOperationException("Turn bounds must form a forward entry-to-apex-to-exit arc within one lap.");
        correction = correction with
        {
            CornerId = correction.CornerId.Trim(),
            Label = correction.Label.Trim(),
            Note = correction.Note.Trim(),
            GeometryHash = ProgressiveTuningCoordinator.GeometryHash(map),
            VerifiedAt = DateTimeOffset.UtcNow
        };
        if (correction.CornerId.Length == 0 || correction.Label.Length == 0)
            throw new InvalidOperationException("A turn correction requires a turn ID and label.");
        if (!map.Turns.Any(turn => string.Equals(turn.CornerId, correction.CornerId, StringComparison.Ordinal)))
            throw new InvalidOperationException("The corrected turn is not part of the current track map.");

        var store = new PortableTuningTurnAnnotationStore(Settings.CoachHome);
        var set = store.Load(identity.TrackConfigurationKey, map.MapIdentity) ?? new TuningTurnAnnotationSet
        {
            TrackConfigurationKey = identity.TrackConfigurationKey,
            MapIdentity = map.MapIdentity
        };
        var corrections = set.Corrections.ToList();
        // A single exact-configuration map file can serve recordings whose
        // authoritative geometry changes across iRacing builds or captures.
        // Replace only this geometry's corner correction; preserve the same
        // corner ID for every other geometry hash so switching races is durable.
        corrections.RemoveAll(item =>
            string.Equals(item.GeometryHash, correction.GeometryHash, StringComparison.Ordinal)
            && string.Equals(item.CornerId, correction.CornerId, StringComparison.Ordinal));
        corrections.Add(correction);
        set = set with { Corrections = corrections, UpdatedUtc = DateTimeOffset.UtcNow };
        _archive.MarkActive(Settings.CoachHome);
        await store.SaveAsync(set, cancellationToken);
        var updated = draft with { TurnCorrections = corrections, UpdatedUtc = DateTimeOffset.UtcNow };
        await SaveTuningDraftSnapshotAsync(updated, cancellationToken);
        if (!IsCurrentTuningDraft(draft)
            || SelectedTuningMap is null
            || !string.Equals(SelectedTuningMap.MapIdentity, map.MapIdentity, StringComparison.Ordinal)) return;
        TuningDraft = updated;
        SelectedTuningMap = PortableTuningTurnAnnotationStore.Merge(map, set);
        if (CurrentAnalysis is not null
            && SelectedTuningRace is not null
            && ProgressiveTuningCoordinator.Matches(SelectedTuningRace, CurrentAnalysis.TuningIdentity))
            CurrentAnalysis = CurrentAnalysis with { TuningMap = SelectedTuningMap };
        RaiseChanged();
    }

    public async Task SelectTuningOpenTargetAsync(string? raceId, CancellationToken cancellationToken = default)
    {
        var selectionEpoch = Interlocked.Increment(ref _tuningTargetSelectionEpoch);
        var draft = TuningDraft;
        var representativeRaceId = SelectedTuningRaceId;
        var race = Races.FirstOrDefault(item => string.Equals(item.Id, raceId?.Trim(), StringComparison.Ordinal));
        if (race is null)
        {
            using var mutation = await EnterTuningDraftMutationAsync(cancellationToken);
            if (!IsCurrentTuningTargetRequest(selectionEpoch, draft, representativeRaceId)) return;
            var currentDraft = TuningDraft;
            var updated = currentDraft with { OpenSetupTarget = null, UpdatedUtc = DateTimeOffset.UtcNow };
            await SaveTuningDraftSnapshotAsync(updated, cancellationToken);
            if (!IsCurrentTuningTargetSelection(selectionEpoch, currentDraft, representativeRaceId)) return;
            SelectedTuningTargetRaceId = string.Empty;
            SelectedTuningTarget = null;
            TuningDraft = updated;
            RaiseChanged();
            return;
        }
        var workspace = await LoadTuningWorkspaceAsync(race, cancellationToken);
        if (!IsCurrentTuningTargetRequest(selectionEpoch, draft, representativeRaceId)) return;
        var candidate = ProgressiveTuningCoordinator.Candidate(race, workspace);
        var target = ProgressiveTuningCoordinator.OpenTarget(candidate)
            ?? throw new InvalidOperationException(string.Join(" ", candidate.Eligibility.MissingRequired.Concat(candidate.Eligibility.Blockers)));
        if (!ProgressiveTuningCoordinator.CompatibleOpenTarget(draft.RepresentativeSession, target.Baseline))
            throw new InvalidOperationException("The open setup target must match the representative race's exact car and track configuration.");
        using var targetMutation = await EnterTuningDraftMutationAsync(cancellationToken);
        if (!IsCurrentTuningTargetRequest(selectionEpoch, draft, representativeRaceId)) return;
        var currentTargetDraft = TuningDraft;
        var updatedTargetDraft = currentTargetDraft with { OpenSetupTarget = target, UpdatedUtc = DateTimeOffset.UtcNow };
        await SaveTuningDraftSnapshotAsync(updatedTargetDraft, cancellationToken);
        if (!IsCurrentTuningTargetSelection(selectionEpoch, currentTargetDraft, representativeRaceId)) return;
        SelectedTuningTargetRaceId = race.Id;
        SelectedTuningTarget = target;
        TuningDraft = updatedTargetDraft;
        RaiseChanged();
    }

    public async Task SubmitStructuredTuningAsync(CancellationToken cancellationToken = default)
    {
        var race = SelectedTuningRace ?? throw new InvalidOperationException("Choose a representative race first.");
        var draft = TuningDraft;
        var selectionEpoch = Volatile.Read(ref _tuningRaceSelectionEpoch);
        var representative = draft.RepresentativeSession;
        if (!ProgressiveTuningCoordinator.Matches(race, representative))
            throw new InvalidOperationException("Reload the representative race before requesting a recommendation.");
        if (draft.Feedback.Count == 0)
            throw new InvalidOperationException("Describe at least one handling issue before requesting a recommendation.");
        if (draft.RepresentativeRunIds.Count == 0)
            throw new InvalidOperationException("This race has no comparison-eligible run with enough clean green laps.");
        var evidenceCandidate = ProgressiveTuningCoordinator.Candidate(race, CurrentAnalysis);
        if (!evidenceCandidate.Eligibility.CanUseAsEvidence)
            throw new InvalidOperationException(string.Join(" ", evidenceCandidate.Eligibility.MissingRequired));
        var target = evidenceCandidate.Eligibility.CanReceiveGarageRecommendation
            ? ProgressiveTuningCoordinator.OpenTarget(evidenceCandidate)
            : SelectedTuningTarget;
        if (target is null || !ProgressiveTuningCoordinator.CompatibleOpenTarget(representative, target.Baseline))
            throw new InvalidOperationException("Choose a verified open-setup recording for the same exact car and track configuration.");
        var mapSubmission = ProgressiveTuningCoordinator.BuildMapSubmission(
            representative,
            SelectedTuningMap ?? throw new InvalidOperationException("A recorded track map is required for corner-specific tuning."),
            draft.TurnCorrections);

        TuningMessage = "Building one controlled setup test…";
        RaiseChanged();
        try
        {
            var deterministicPayload = BuildStructuredTuningPayload(draft, representative, target, mapSubmission);
            var result = await CallBackendAsync(
                "recommend_structured_open_setup_tuning",
                deterministicPayload,
                cancellationToken);
            var deterministicResult = RuntimeMapper.StructuredTuning(result);
            if (!IsCurrentTuningRaceSelection(selectionEpoch, race.Id) || !IsCurrentTuningDraft(draft)) return;
            StructuredTuningResult = deterministicResult;
            await TryApplyBoundedTuningCoachAsync(
                deterministicResult,
                target,
                deterministicPayload,
                draft,
                selectionEpoch,
                race.Id,
                cancellationToken);
            if (!IsCurrentTuningRaceSelection(selectionEpoch, race.Id) || !IsCurrentTuningDraft(draft)) return;
            var chosen = StructuredTuningResult.CandidateWhitelist.FirstOrDefault(item =>
                    string.Equals(item.CandidateId, StructuredTuningResult.Recommendation.SelectedCandidateId, StringComparison.Ordinal))
                ?? StructuredTuningResult.CandidateWhitelist.FirstOrDefault();
            if (chosen is not null)
            {
                TuningExperiment = new TuningExperimentView(
                    StructuredTuningResult.ExperimentId,
                    chosen.System,
                    chosen.Change,
                    chosen.PredictedEffect,
                    chosen.Risk,
                    target.Baseline.SetupFingerprint,
                    chosen.Verify,
                    "Waiting for a comparison run");
            }
            ExperimentGenerated = chosen is not null;
            TuningMessage = chosen is null
                ? string.Join(" ", StructuredTuningResult.MissingRequired.Concat(StructuredTuningResult.Limitations)).Trim()
                : "One controlled setup test is ready.";
        }
        catch (Exception ex) when (ex is BackendProtocolException or BackendDomainException or InvalidDataException)
        {
            if (!IsCurrentTuningRaceSelection(selectionEpoch, race.Id) || !IsCurrentTuningDraft(draft)) return;
            TuningMessage = Bound(ex.Message);
            Notify("A safe tuning change could not be recommended from this evidence.");
        }
        RaiseChanged();
    }

    private Dictionary<string, object?> BuildStructuredTuningPayload(
        ProgressiveTuningDraft draft,
        TuningSessionIdentity representative,
        TuningSetupTarget target,
        TuningMapSubmissionIdentity mapSubmission)
    {
        var payload = new Dictionary<string, object?>(StringComparer.Ordinal)
        {
            ["analysis_path"] = representative.AnalysisPath,
            ["open_target_analysis_path"] = target.Baseline.AnalysisPath,
            ["archive_root"] = Settings.ArchiveRoot,
            ["feedback"] = draft.Feedback.Select(item => new
            {
                feedback_id = item.FeedbackId,
                corner_id = item.CornerId,
                corner_label = item.CornerLabel,
                start_pct = item.StartPct,
                apex_pct = item.ApexPct,
                end_pct = item.EndPct,
                run_phase = item.RunPhase,
                corner_phases = item.CornerPhases,
                symptom_id = item.SymptomId,
                severity = item.Severity,
                driver_confidence = item.DriverConfidence,
                note = item.Note,
                priority = item.Priority
            }).ToArray(),
            ["representative_run_ids"] = draft.RepresentativeRunIds,
            ["map_identity"] = mapSubmission,
            ["ruleset_id"] = draft.RulesetId,
            ["goal"] = draft.Goal,
            ["generic_note"] = draft.GeneralNote,
            ["maximum_changes"] = 1
        };
        return payload;
    }

    private async Task TryApplyBoundedTuningCoachAsync(
        StructuredTuningResultView deterministicResult,
        TuningSetupTarget target,
        IReadOnlyDictionary<string, object?> deterministicPayload,
        ProgressiveTuningDraft draft,
        long selectionEpoch,
        string raceId,
        CancellationToken cancellationToken)
    {
        if (!CoachEngine.ChatGptConnected
            || !deterministicResult.Eligibility.CanReceiveGarageRecommendation
            || deterministicResult.CandidateWhitelist.Count == 0
            || deterministicResult.Evidence.Count == 0)
            return;

        try
        {
            var evidence = ProgressiveTuningCoordinator.BuildAiEvidence(deterministicResult, target.Baseline);
            var boundedRequest = ProgressiveTuningCoordinator.BuildBoundedAiRequest(deterministicResult, target.Baseline);
            if (boundedRequest is null) return;
            Settings.CoachThreadIds.TryGetValue(evidence.WorkflowKey, out var threadId);
            var instruction =
                "Select exactly one candidate from candidate_whitelist. Cite only supplied evidence IDs. " +
                "Do not invent setup values, telemetry, causes, legality, or additional changes.";
            var reply = await _coachEngine.AskStructuredCoachAsync(
                threadId,
                instruction,
                boundedRequest.Json,
                "ai-tuning-output.schema.json",
                cancellationToken);
            if (!IsCurrentTuningRaceSelection(selectionEpoch, raceId) || !IsCurrentTuningDraft(draft)) return;
            if (!string.IsNullOrWhiteSpace(reply.ThreadId))
            {
                Settings.CoachThreadIds[evidence.WorkflowKey] = reply.ThreadId;
                PersistSettingsQuietly();
            }

            var selection = JsonSerializer.Deserialize<TuningAiSelection>(reply.Text);
            if (selection is null) return;
            var validation = ProgressiveTuningCoordinator.ValidateAiSelection(
                deterministicResult,
                selection,
                boundedRequest.CandidateIds,
                boundedRequest.EvidenceIds);
            if (!validation.Valid || validation.Selection is null) return;

            var synthesizedPayload = new Dictionary<string, object?>(deterministicPayload, StringComparer.Ordinal)
            {
                ["ai_response"] = validation.Selection
            };
            var synthesizedResponse = await CallBackendAsync(
                "recommend_structured_open_setup_tuning",
                synthesizedPayload,
                cancellationToken);
            if (!IsCurrentTuningRaceSelection(selectionEpoch, raceId) || !IsCurrentTuningDraft(draft)) return;
            var synthesizedResult = RuntimeMapper.StructuredTuning(synthesizedResponse);
            var synthesizedSelection = new TuningAiSelection(
                synthesizedResult.Recommendation.SelectedCandidateId,
                synthesizedResult.Recommendation.Summary,
                synthesizedResult.Recommendation.EvidenceIds,
                synthesizedResult.Recommendation.Conflicts,
                synthesizedResult.Recommendation.ConfidenceReasons);
            var synthesizedValidation = ProgressiveTuningCoordinator.ValidateAiSelection(deterministicResult, synthesizedSelection);
            if (synthesizedValidation.Valid
                && string.Equals(synthesizedResult.ExperimentId, deterministicResult.ExperimentId, StringComparison.Ordinal)
                && string.Equals(synthesizedResult.Recommendation.SelectedCandidateId, validation.Selection.SelectedCandidateId, StringComparison.Ordinal)
                && synthesizedResult.CandidateWhitelist.Any(item =>
                    string.Equals(item.CandidateId, validation.Selection.SelectedCandidateId, StringComparison.Ordinal)))
                StructuredTuningResult = synthesizedResult;
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            // A Coach-side timeout must not discard the deterministic recommendation.
        }
        catch (Exception ex) when (ex is IOException
                                   or InvalidOperationException
                                   or JsonException
                                   or TimeoutException
                                   or BackendProtocolException
                                   or BackendDomainException
                                   or InvalidDataException)
        {
            // AI synthesis is optional. The deterministic result remains authoritative.
        }
    }

    public TuningAiEvidenceView? BuildTuningAiEvidence() =>
        StructuredTuningResult is null || SelectedTuningTarget is null
            ? null
            : ProgressiveTuningCoordinator.BuildAiEvidence(StructuredTuningResult, SelectedTuningTarget.Baseline);

    public TuningAiSelectionValidation ValidateTuningAiSelection(TuningAiSelection selection) =>
        StructuredTuningResult is null
            ? new TuningAiSelectionValidation(false, null, ["Run deterministic tuning analysis before requesting AI synthesis."])
            : ProgressiveTuningCoordinator.ValidateAiSelection(StructuredTuningResult, selection);

    private void ActivateTuningAnalysis(RecentRace race)
    {
        if (CurrentAnalysis is null) throw new InvalidOperationException("The representative race analysis is not loaded.");
        var identity = ProgressiveTuningCoordinator.Bind(
            CurrentAnalysis.TuningIdentity ?? ProgressiveTuningCoordinator.FromRace(race), race);
        CurrentAnalysis = CurrentAnalysis with { TuningIdentity = identity };
        var candidate = ProgressiveTuningCoordinator.Candidate(race, CurrentAnalysis);
        var map = CurrentAnalysis.TuningMap;
        TuningTurnAnnotationSet? annotations = null;
        if (map is not null && !string.IsNullOrWhiteSpace(identity.TrackConfigurationKey) && !string.IsNullOrWhiteSpace(map.MapIdentity))
        {
            try
            {
                annotations = new PortableTuningTurnAnnotationStore(Settings.CoachHome)
                    .Load(identity.TrackConfigurationKey, map.MapIdentity);
            }
            catch (InvalidDataException ex)
            {
                _ = StructuredAppLog.Record("tuning turn annotations", ex, AppVersion, Settings.LogsRoot, _pathProvider.UserProfile);
            }
        }
        SelectedTuningMap = map is null ? null : PortableTuningTurnAnnotationStore.Merge(map, annotations);
        CurrentAnalysis = CurrentAnalysis with { TuningMap = SelectedTuningMap };

        ProgressiveTuningDraft? saved = null;
        try { saved = new PortableTuningDraftStore(Settings.CoachHome).Load(identity); }
        catch (InvalidDataException ex) { _ = StructuredAppLog.Record("tuning draft", ex, AppVersion, Settings.LogsRoot, _pathProvider.UserProfile); }
        var selfTarget = ProgressiveTuningCoordinator.OpenTarget(candidate);
        var savedTarget = saved?.OpenSetupTarget;
        SelectedTuningTarget = selfTarget
            ?? (savedTarget is not null && ProgressiveTuningCoordinator.CompatibleOpenTarget(identity, savedTarget.Baseline)
                ? savedTarget
                : null);
        SelectedTuningTargetRaceId = SelectedTuningTarget?.Baseline.RaceId ?? string.Empty;
        var defaultRun = CurrentAnalysis.Runs
            .Where(run => run.ComparisonEligible && run.CoachingReferenceLapCount >= 6)
            .OrderByDescending(run => run.GreenLaps)
            .ThenBy(run => run.CautionLaps)
            .ThenByDescending(run => run.Laps.Count)
            .ThenBy(run => run.Number)
            .FirstOrDefault();
        var runIds = saved?.RepresentativeRunIds.Count > 0
            ? saved.RepresentativeRunIds
            : defaultRun is null ? [] : [defaultRun.Number.ToString(CultureInfo.InvariantCulture)];
        TuningDraft = (saved ?? new ProgressiveTuningDraft()) with
        {
            RepresentativeSession = identity,
            OpenSetupTarget = SelectedTuningTarget,
            RepresentativeRunIds = runIds,
            MapIdentity = SelectedTuningMap?.MapIdentity ?? string.Empty,
            TurnCorrections = annotations?.Corrections ?? saved?.TurnCorrections ?? [],
            UpdatedUtc = saved?.UpdatedUtc ?? DateTimeOffset.UtcNow
        };
        TuningNotes = TuningDraft.GeneralNote;
        SelectedTuningTurnId = string.Empty;
        StructuredTuningResult = null;
        SyncLegacyTuningFeedback();
        TuningMessage = candidate.Eligibility.CanReceiveGarageRecommendation
            ? "Choose a turn and describe what the car did."
            : candidate.Eligibility.CanUseAsEvidence
                ? "This race can supply driving evidence. Choose a matching open setup before requesting a garage change."
                : string.Join(" ", candidate.Eligibility.MissingRequired);
    }

    private async Task<AnalysisWorkspace> LoadTuningWorkspaceAsync(RecentRace race, CancellationToken cancellationToken)
    {
        if (CurrentAnalysis is not null
            && ProgressiveTuningCoordinator.Matches(race, CurrentAnalysis.TuningIdentity)
            && HasAuthoritativeTuningGeometry(CurrentAnalysis))
            return CurrentAnalysis;
        if (!string.IsNullOrWhiteSpace(race.AnalysisPath) && File.Exists(race.AnalysisPath))
        {
            using var document = JsonDocument.Parse(await File.ReadAllTextAsync(race.AnalysisPath, cancellationToken));
            var archived = RuntimeMapper.ArchivedAnalysis(document.RootElement);
            EnsureAnalysisMatchesSession(race, archived);
            archived = archived with
            {
                TuningIdentity = ProgressiveTuningCoordinator.Bind(
                    archived.TuningIdentity ?? ProgressiveTuningCoordinator.FromRace(race), race)
            };
            // Pre-progressive archives can carry an open setup but no stable
            // geometry identity. Reanalyze instead of treating a synthesized
            // track/layout label as exact target provenance.
            if (HasAuthoritativeTuningGeometry(archived)) return archived;
        }
        var response = await CallBackendAsync("analyze_iracing_race", new
        {
            selector = race.EffectiveSelector,
            iracing_root = Settings.IRacingRoot,
            archive_root = Settings.ArchiveRoot,
            target_hz = 20
        }, cancellationToken);
        EnsureResponseMatchesSession(race, response);
        var analysis = RuntimeMapper.Analysis(response);
        EnsureAnalysisMatchesSession(race, analysis);
        analysis = analysis with
        {
            TuningIdentity = ProgressiveTuningCoordinator.Bind(
                analysis.TuningIdentity ?? ProgressiveTuningCoordinator.FromRace(race), race)
        };
        if (!HasAuthoritativeTuningGeometry(analysis))
            throw new InvalidDataException("This recording could not provide an authoritative track-geometry identity after reanalysis.");
        return analysis;
    }

    private static bool HasAuthoritativeTuningGeometry(AnalysisWorkspace analysis)
    {
        var hash = analysis.VectorGeometry?.GeometryHash?.Trim() ?? string.Empty;
        return !string.IsNullOrWhiteSpace(analysis.TuningIdentity?.TrackConfigurationKey)
            && hash.Length == 64
            && hash.All(Uri.IsHexDigit);
    }

    private async Task SaveTuningDraftAsync(CancellationToken cancellationToken)
        => await SaveTuningDraftSnapshotAsync(TuningDraft, cancellationToken);

    private async Task SaveTuningDraftSnapshotAsync(ProgressiveTuningDraft draft, CancellationToken cancellationToken)
    {
        if (string.IsNullOrWhiteSpace(draft.RepresentativeSession.AnalysisId)
            || string.IsNullOrWhiteSpace(draft.RepresentativeSession.AnalysisPath))
            throw new InvalidOperationException("Load an exact analyzed recording before saving tuning feedback.");
        _archive.MarkActive(Settings.CoachHome);
        await new PortableTuningDraftStore(Settings.CoachHome).SaveAsync(draft, cancellationToken);
    }

    private async ValueTask<IDisposable> EnterTuningDraftMutationAsync(CancellationToken cancellationToken)
    {
        await _tuningDraftMutationGate.WaitAsync(cancellationToken);
        return new SemaphoreReleaser(_tuningDraftMutationGate);
    }

    private sealed class SemaphoreReleaser(SemaphoreSlim semaphore) : IDisposable
    {
        private SemaphoreSlim? _semaphore = semaphore;

        public void Dispose() => Interlocked.Exchange(ref _semaphore, null)?.Release();
    }

    private bool IsCurrentTuningRaceSelection(long epoch, string raceId) =>
        Volatile.Read(ref _tuningRaceSelectionEpoch) == epoch
        && string.Equals(SelectedTuningRaceId, raceId, StringComparison.Ordinal);

    private bool IsCurrentTuningTargetSelection(
        long epoch,
        ProgressiveTuningDraft draft,
        string representativeRaceId) =>
        Volatile.Read(ref _tuningTargetSelectionEpoch) == epoch
        && string.Equals(SelectedTuningRaceId, representativeRaceId, StringComparison.Ordinal)
        && IsCurrentTuningDraft(draft);

    private bool IsCurrentTuningTargetRequest(
        long epoch,
        ProgressiveTuningDraft draft,
        string representativeRaceId) =>
        Volatile.Read(ref _tuningTargetSelectionEpoch) == epoch
        && string.Equals(SelectedTuningRaceId, representativeRaceId, StringComparison.Ordinal)
        && IsCurrentTuningDraftIdentity(draft);

    private bool IsCurrentTuningDraft(ProgressiveTuningDraft draft) =>
        ReferenceEquals(TuningDraft, draft)
        && IsCurrentTuningDraftIdentity(draft);

    private bool IsCurrentTuningDraftIdentity(ProgressiveTuningDraft draft) =>
        string.Equals(TuningDraft.DraftId, draft.DraftId, StringComparison.Ordinal)
        && string.Equals(SelectedTuningRaceId, draft.RepresentativeSession.RaceId, StringComparison.Ordinal)
        && string.Equals(
            ProgressiveTuningCoordinator.TuningIdentityKey(TuningDraft.RepresentativeSession),
            ProgressiveTuningCoordinator.TuningIdentityKey(draft.RepresentativeSession),
            StringComparison.Ordinal);

    private void SyncLegacyTuningFeedback()
    {
        TuningFeedback.Clear();
        TuningFeedback.AddRange(TuningDraft.Feedback.Select(item => new TuningFeedbackDraft(
            string.IsNullOrWhiteSpace(item.CornerLabel) ? item.CornerId : item.CornerLabel,
            Humanize(item.RunPhase),
            item.CornerPhases.Count == 0 ? "Whole corner" : string.Join(", ", item.CornerPhases.Select(Humanize)),
            Humanize(item.SymptomId),
            TuningScale(item.Severity),
            TuningScale(item.DriverConfidence),
            item.Priority >= 4,
            item.Note,
            item.FeedbackId)));
    }

    private static string NormalizeTuningRunPhase(string value) => value.Trim().ToLowerInvariant() switch
    {
        "early" or "early run" => "early",
        "late" or "late run" => "late",
        _ => "middle"
    };

    private static string RequireTuningRunPhase(string value) => value.Trim().ToLowerInvariant() switch
    {
        "early" or "early run" => "early",
        "middle" or "mid" or "mid run" or "middle run" => "middle",
        "late" or "late run" => "late",
        _ => throw new InvalidOperationException("Run phase must be early, middle, or late.")
    };

    private TuningTurn RequireCurrentTuningTurn(string cornerId)
    {
        var race = SelectedTuningRace ?? throw new InvalidOperationException("Choose a representative race first.");
        if (!ProgressiveTuningCoordinator.Matches(race, TuningDraft.RepresentativeSession))
            throw new InvalidOperationException("Reload the representative race before saving corner feedback.");
        var map = SelectedTuningMap ?? throw new InvalidOperationException("Load this race's current track map before saving corner feedback.");
        if (!string.Equals(TuningDraft.MapIdentity, map.MapIdentity, StringComparison.Ordinal))
            throw new InvalidOperationException("The corner feedback belongs to an older track map.");
        return map.Turns.FirstOrDefault(turn => string.Equals(turn.CornerId, cornerId, StringComparison.Ordinal))
            ?? throw new InvalidOperationException("The selected turn is not part of the current track map.");
    }

    private ProgressiveTuningFeedback ValidateTuningFeedback(ProgressiveTuningFeedback feedback)
    {
        var turn = RequireCurrentTuningTurn(feedback.CornerId.Trim());
        if (feedback.StartPct is not { } start
            || feedback.ApexPct is not { } apex
            || feedback.EndPct is not { } end
            || !double.IsFinite(start) || !double.IsFinite(apex) || !double.IsFinite(end)
            || start is < 0 or >= 1 || apex is < 0 or >= 1 || end is < 0 or >= 1
            || Math.Abs(start - turn.StartPct) > 0.000001
            || Math.Abs(apex - turn.ApexPct) > 0.000001
            || Math.Abs(end - turn.EndPct) > 0.000001)
            throw new InvalidOperationException("The corner feedback belongs to different turn bounds. Reload the current map and try again.");
        var symptom = feedback.SymptomId.Trim().ToLowerInvariant().Replace('_', '-');
        if (symptom is not ("good" or "tight" or "loose" or "unstable-braking" or "wheel-hop-lock"
            or "wheelspin" or "cant-take-throttle" or "bottoming" or "harsh-skating" or "low-grip" or "other"))
            throw new InvalidOperationException("Choose a supported handling description.");
        var phases = feedback.CornerPhases.Count == 0
            ? ["whole"]
            : feedback.CornerPhases.Select(value => value.Trim().ToLowerInvariant()).Distinct(StringComparer.Ordinal).ToArray();
        if (phases.Any(phase => phase is not ("entry" or "center" or "exit" or "whole")))
            throw new InvalidOperationException("Corner phase must be entry, center, exit, or whole corner.");
        if (feedback.Note.Trim().Length > 2_000)
            throw new InvalidOperationException("Corner feedback notes must be 2,000 characters or fewer.");
        if (feedback.FeedbackId.Trim().Length is < 1 or > 160)
            throw new InvalidOperationException("Corner feedback identity is invalid.");
        return NormalizeTuningFeedback(feedback with
        {
            CornerLabel = turn.Label,
            StartPct = turn.StartPct,
            ApexPct = turn.ApexPct,
            EndPct = turn.EndPct,
            RunPhase = RequireTuningRunPhase(feedback.RunPhase),
            CornerPhases = phases,
            SymptomId = symptom
        });
    }

    private static ProgressiveTuningFeedback NormalizeTuningFeedback(ProgressiveTuningFeedback feedback) => feedback with
    {
        CornerId = feedback.CornerId.Trim(),
        CornerLabel = feedback.CornerLabel.Trim(),
        RunPhase = NormalizeTuningRunPhase(feedback.RunPhase),
        CornerPhases = feedback.CornerPhases.Select(value => value.Trim().ToLowerInvariant()).Where(value => value.Length > 0).Distinct(StringComparer.Ordinal).ToArray(),
        SymptomId = feedback.SymptomId.Trim().ToLowerInvariant(),
        Severity = Math.Clamp(feedback.Severity, 1, 5),
        DriverConfidence = Math.Clamp(feedback.DriverConfidence, 1, 5),
        Priority = Math.Clamp(feedback.Priority, 1, 5),
        Note = feedback.Note.Trim()
    };

    private static bool IsForwardTuningTurn(double start, double apex, double end)
    {
        const double epsilon = 0.000001;
        var arc = (end - start + 1) % 1;
        var apexDistance = (apex - start + 1) % 1;
        return arc > epsilon && apexDistance > epsilon && apexDistance < arc - epsilon;
    }

    private static string TuningScale(int value) => Math.Clamp(value, 1, 5) switch
    {
        1 => "Low",
        2 => "Mild",
        4 => "High",
        5 => "Very high",
        _ => "Moderate"
    };

    private string BuildTuningSymptom()
    {
        if (TuningFeedback.Count > 0)
        {
            return string.Join(" ", TuningFeedback.Select((item, index) =>
                $"Issue {index + 1}: {item.Severity} {item.Balance.ToLowerInvariant()} at {item.CornerPhase.ToLowerInvariant()} in {item.Corner} during the {item.RunPhase.ToLowerInvariant()}. Driver confidence: {item.Confidence.ToLowerInvariant()}." +
                (item.Priority ? " This is the driver's highest-priority issue." : string.Empty) +
                (string.IsNullOrWhiteSpace(item.Note) ? string.Empty : $" Driver note: {item.Note.Trim()}")));
        }
        if (!string.IsNullOrWhiteSpace(TuningBalance))
        {
            var corner = string.IsNullOrWhiteSpace(TuningCorner) ? "the selected zone" : TuningCorner.Trim();
            var notes = string.IsNullOrWhiteSpace(TuningNotes) ? string.Empty : $" {TuningNotes.Trim()}";
            var priority = TuningPriority ? " This is the driver's highest-priority issue." : string.Empty;
            return $"{TuningSeverity} {TuningBalance.Trim().ToLowerInvariant()} at {TuningCornerPhase.ToLowerInvariant()} in {corner} during the {TuningRunPhase.ToLowerInvariant()}. Driver confidence: {TuningConfidence.ToLowerInvariant()}.{priority}{notes}".Trim();
        }
        return SymptomText.Trim();
    }

    public Task RecordOutcomeAsync(string outcome, CancellationToken cancellationToken = default) =>
        RecordOutcomeAsync(outcome, null, cancellationToken);

    public async Task RecordOutcomeAsync(string outcome, string? resultAnalysisPath, CancellationToken cancellationToken = default)
    {
        var experiment = TuningExperiment;
        if (experiment is null) return;
        if (string.IsNullOrWhiteSpace(resultAnalysisPath) && SelectedTuningResultRaceId.Length > 0)
            resultAnalysisPath = SelectedTuningResultRace?.AnalysisPath;
        try
        {
            _ = await CallBackendAsync("record_open_setup_feedback", new
            {
                experiment_id = experiment.ExperimentId,
                outcome,
                notes = FeedbackNotes.Trim(),
                archive_root = Settings.ArchiveRoot,
                result_analysis_path = string.IsNullOrWhiteSpace(resultAnalysisPath) ? null : resultAnalysisPath
            }, cancellationToken);
            if (TuningExperiment is not null
                && string.Equals(TuningExperiment.ExperimentId, experiment.ExperimentId, StringComparison.Ordinal))
            {
                TuningExperiment = TuningExperiment with { Outcome = Humanize(outcome) };
                Notify("Outcome saved with the experiment.");
            }
        }
        catch (Exception ex) when (ex is BackendProtocolException or BackendDomainException)
        {
            Notify($"The outcome was not saved: {Bound(ex.Message)}");
            RaiseChanged();
            throw;
        }
        RaiseChanged();
    }

    public async Task CopySelectedSetupAsync(CancellationToken cancellationToken = default)
    {
        var setup = SelectedSetup;
        if (setup is null)
        {
            Notify("Choose a local setup file first.");
            return;
        }
        try
        {
            var response = await CallBackendAsync("copy_iracing_setup_to_coach", new
            {
                source_path = setup.StoPath,
                iracing_root = Settings.IRacingRoot,
                coach_home = Settings.CoachHome
            }, cancellationToken);
            var copied = response.TryGetProperty("copied", out var value) && value.ValueKind == JsonValueKind.True;
            SetupMessage = copied
                ? "An exact copy was saved in your Coach setup library."
                : "That exact setup is already in your Coach setup library.";
            Notify(SetupMessage);
        }
        catch (Exception ex) when (ex is BackendProtocolException or BackendDomainException or InvalidDataException)
        {
            SetupMessage = Bound(ex.Message);
            Notify("The setup was not copied.");
        }
        RaiseChanged();
    }

    public void OpenSelectedSetupFolder()
    {
        var setup = SelectedSetup;
        if (setup is null) return;
        if (!_allowExternalHostActions)
        {
            Notify("Opening folders is disabled in the isolated host profile.");
            return;
        }
        _ = Process.Start(new ProcessStartInfo("explorer.exe")
        {
            UseShellExecute = true,
            ArgumentList = { "/select,", setup.StoPath }
        });
    }

    public void OpenStartingTune()
    {
        if (string.IsNullOrWhiteSpace(StartingTuneCar)) StartingTuneCar = SelectedSetup?.Car ?? Races.FirstOrDefault(race => race.SetupType.Equals("Open", StringComparison.OrdinalIgnoreCase))?.Car ?? string.Empty;
        if (string.IsNullOrWhiteSpace(StartingTuneTrack)) StartingTuneTrack = SelectedSetup?.Track ?? Races.FirstOrDefault(race => race.SetupType.Equals("Open", StringComparison.OrdinalIgnoreCase))?.Track ?? string.Empty;
        RaiseChanged();
    }

    public async Task BuildStartingTuneAsync(CancellationToken cancellationToken = default)
    {
        if (StartingTuneBusy) return;
        if (string.IsNullOrWhiteSpace(StartingTuneSeason) || string.IsNullOrWhiteSpace(StartingTuneCar) || string.IsNullOrWhiteSpace(StartingTuneTrack))
        {
            SetupMessage = "Enter the iRacing season, car, and exact track layout before finding a baseline.";
            RaiseChanged();
            return;
        }
        var request = new StartingTuneRequest(
            StartingTuneSeason.Trim(),
            StartingTuneCar.Trim(),
            StartingTuneTrack.Trim(),
            StartingTunePurpose.Trim(),
            Interlocked.Increment(ref _startingTuneSelectionEpoch));
        StartingTuneBusy = true;
        SetupMessage = "Reviewing your local setups and recorded context…";
        RaiseChanged();
        try
        {
            var response = await CallBackendAsync("build_open_setup_package", new
            {
                iracing_root = Settings.IRacingRoot,
                archive_root = Settings.ArchiveRoot,
                season = request.Season,
                car = request.Car,
                track = request.Track
            }, cancellationToken);
            var staged = RuntimeMapper.SetupPackage(response, request.Car, request.Track, request.Season, request.Purpose);
            if (!StartingTuneRequestStillCurrent(request))
            {
                SetupMessage = "The event or session purpose changed while the source was being checked. The older result was not shown.";
                return;
            }
            StartingTunePackage = staged;
            StartingTuneStep = 2;
            SetupMessage = "A read-only source and rollback record are ready for review.";
        }
        catch (Exception ex) when (ex is BackendProtocolException or BackendDomainException or InvalidDataException)
        {
            SetupMessage = Bound(ex.Message);
            StartingTunePackage = null;
        }
        finally
        {
            StartingTuneBusy = false;
            RaiseChanged();
        }
    }

    public void SetStartingTuneStep(int step)
    {
        if (StartingTunePackage is null && step > 1) return;
        StartingTuneStep = Math.Clamp(step, 1, 4);
        RaiseChanged();
    }

    public void ResetStartingTune()
    {
        Interlocked.Increment(ref _startingTuneSelectionEpoch);
        StartingTunePackage = null;
        StartingTuneStep = 1;
        SetupMessage = "Enter an open-setup event context to build a new starting tune.";
        RaiseChanged();
    }

    private bool StartingTuneRequestStillCurrent(StartingTuneRequest request) =>
        Interlocked.Read(ref _startingTuneSelectionEpoch) == request.Epoch &&
        string.Equals(StartingTuneSeason.Trim(), request.Season, StringComparison.Ordinal) &&
        string.Equals(StartingTuneCar.Trim(), request.Car, StringComparison.Ordinal) &&
        string.Equals(StartingTuneTrack.Trim(), request.Track, StringComparison.Ordinal) &&
        string.Equals(StartingTunePurpose.Trim(), request.Purpose, StringComparison.OrdinalIgnoreCase);

    private sealed record StartingTuneRequest(string Season, string Car, string Track, string Purpose, long Epoch);

    public void SaveSettings()
    {
        if (!SettingsWritable)
        {
            SettingsMessage = Settings.Compatibility.Message;
            Toast = "Settings were not changed.";
            RaiseChanged();
            return;
        }
        var sourceValid = TryValidateLocalRoot(Settings.IRacingRoot, out var sourceError);
        var installValid = TryValidateLocalRoot(Settings.IRacingInstallRoot, out var installError);
        var homeValid = TryValidateLocalRoot(Settings.CoachHome, out var homeError);
        if (!sourceValid || !installValid || !homeValid)
        {
            SettingsMessage = sourceError ?? installError ?? homeError ?? "Choose valid local folders.";
            RaiseChanged();
            return;
        }
        if (Garage61KeyInput.IndexOfAny(['\r', '\n', '\0']) >= 0)
        {
            SettingsMessage = "The Garage61 API key contains an invalid line break or control character.";
            RaiseChanged();
            return;
        }

        try
        {
            if (!string.IsNullOrWhiteSpace(Garage61KeyInput))
            {
                _garage61Credentials.Store(Garage61KeyInput);
                Settings.Garage61ApiKey = string.Empty;
                Garage61KeyInput = string.Empty;
            }
            EnsureRepository();
            SaveSettingsToStore();
            SettingsMessage = "Settings saved. Account connections stay protected on this Windows user.";
            Toast = "Settings saved.";
            SettingsSaved?.Invoke(Settings);
            _ = RefreshDataAsync(false, CancellationToken.None);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or InvalidOperationException or ArgumentException or TimeoutException)
        {
            SettingsMessage = "Windows could not save the settings file. Check access to the Coach folder.";
            Toast = "Settings were not saved.";
        }
        RaiseChanged();
    }

    public void DetectIRacing()
    {
        EnsureRepository();
        if (Directory.Exists(Settings.IRacingRoot))
        {
            SetupStep = Math.Max(SetupStep, 2);
            SettingsMessage = "iRacing data was found and the Coach folder is ready.";
        }
        else
        {
            SettingsMessage = "iRacing data was not found automatically. Correct the iRacing Documents folder in Settings.";
        }
        RaiseChanged();
    }

    public async Task ConnectChatGptAsync(bool deviceCode = false, CancellationToken cancellationToken = default)
    {
        if (!_allowExternalHostActions)
        {
            Notify("ChatGPT sign-in is disabled in the isolated host profile.");
            return;
        }
        try
        {
            await _coachEngine.StartAsync(Settings, cancellationToken);
            if (!string.IsNullOrWhiteSpace(PendingChatGptLoginId))
            {
                await _coachEngine.CancelLoginAsync(PendingChatGptLoginId, cancellationToken);
                PendingChatGptLoginId = null;
            }
            var login = await _coachEngine.BeginChatGptLoginAsync(deviceCode, cancellationToken);
            PendingChatGptLoginId = login.LoginId;
            if (!string.IsNullOrWhiteSpace(login.Url))
            {
                _ = Process.Start(new ProcessStartInfo(login.Url) { UseShellExecute = true });
            }
            Toast = deviceCode && !string.IsNullOrWhiteSpace(login.VerificationCode)
                ? "Enter the verification code shown here in your browser."
                : "Finish signing in to ChatGPT in your browser.";
        }
        catch (Exception ex) when (ex is IOException or InvalidOperationException or JsonException or TimeoutException)
        {
            Toast = $"ChatGPT connection could not start: {Bound(ex.Message)}";
        }
        RaiseChanged();
    }

    public async Task ConnectGarage61Async(CancellationToken cancellationToken = default)
    {
        var prior = Garage61;
        try
        {
            using var replacement = _garage61Credentials.BeginReplacement(Garage61KeyInput);
            Garage61KeyInput = string.Empty;
            Settings.Garage61ApiKey = string.Empty;
            var response = await CallBackendAsync("garage61_auth_status", new { archive_root = Settings.ArchiveRoot }, cancellationToken);
            if (response.ValueKind != JsonValueKind.Object)
                throw new InvalidDataException("The Garage61 validation response was not an object.");
            var candidate = RuntimeMapper.Garage61(response, Garage61StatusReducer.Unprobed(true));
            if (!candidate.Connected)
            {
                replacement.Rollback();
                Garage61 = prior.Configured ? prior : Garage61StatusReducer.Unprobed(false);
                Toast = $"Garage61 connection was not replaced. {candidate.Message}";
                return;
            }

            Garage61 = candidate;
            SaveSettingsToStore();
            replacement.Commit();
            SetupStep = Math.Max(SetupStep, 4);
            Toast = "Garage61 is connected for this Windows user.";
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or InvalidOperationException or InvalidDataException or ArgumentException or JsonException or TimeoutException)
        {
            Garage61 = prior;
            Toast = $"Garage61 could not be connected: {Bound(ex.Message)}";
        }
        finally
        {
            RaiseChanged();
        }
    }

    public async Task DisconnectGarage61Async(CancellationToken cancellationToken = default)
    {
        try
        {
            _garage61Credentials.Remove();
            SaveSettingsToStore();
            await RefreshDataAsync(false, cancellationToken);
            Toast = "Garage61 was disconnected from this PC.";
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            Toast = $"Garage61 could not be disconnected: {Bound(ex.Message)}";
        }
        RaiseChanged();
    }

    public async Task SyncGarage61ReferencesAsync(CancellationToken cancellationToken = default)
    {
        var race = SelectedRaceSession;
        if (!Garage61.Available || !TryGetAnalysisPath(race, out var analysisPath)) return;
        if (Interlocked.CompareExchange(ref _garage61ReferenceSyncActive, 1, 0) != 0) return;

        Garage61ReferenceMessage = "Finding comparable laps…";
        RaiseChanged();
        try
        {
            var result = await CallBackendAsync("sync_garage61_references", new
            {
                analysis_path = analysisPath,
                archive_root = Settings.ArchiveRoot,
                maximum_laps = 6,
                download_telemetry = true
            }, cancellationToken);
            var references = RuntimeMapper.Garage61References(result)
                ?? throw new InvalidDataException("Garage61 returned no reference result.");
            if (IsCurrentRace(race) && CurrentAnalysis is not null)
                CurrentAnalysis = CurrentAnalysis with { Garage61References = references };
            InvalidateUiAnalysisCache(race!);

            if (IsCurrentRace(race)) Garage61ReferenceMessage = references.Laps.Count switch
            {
                0 => "No comparable Garage61 laps were found for this race.",
                1 => "One Garage61 reference lap is ready.",
                _ => $"{references.Laps.Count} Garage61 reference laps are ready."
            };
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            if (IsCurrentRace(race)) Garage61ReferenceMessage = "Garage61 search cancelled.";
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or InvalidOperationException or InvalidDataException or JsonException or TimeoutException)
        {
            LastRecoverableError = StructuredAppLog.Record("Garage61 reference search", ex, AppVersion, Settings.LogsRoot, _pathProvider.UserProfile);
            if (IsCurrentRace(race)) Garage61ReferenceMessage = Garage61ReferenceFailure(ex.Message);
        }
        finally
        {
            Interlocked.Exchange(ref _garage61ReferenceSyncActive, 0);
            RaiseChanged();
        }
    }

    public async Task VerifyInstallationAsync(CancellationToken cancellationToken = default)
    {
        await RefreshDataAsync(false, cancellationToken);
        await _coachEngine.StartAsync(Settings, cancellationToken);
        var localReady = Health.Any(item => item.Id == "backend" && item.State == "ready");
        if (localReady && CoachEngine.Installed)
        {
            SetupStep = 5;
            Toast = "Installation verified. Local features are ready even when services are offline.";
        }
        else
        {
            Toast = "Installation needs attention. Open Diagnostics or run Repair installation.";
        }
        RaiseChanged();
    }

    public async Task AskRaceCoachAsync(CancellationToken cancellationToken = default)
    {
        if (CurrentRaceCard is null)
        {
            Toast = "Analyze a recorded race before asking the Coach.";
            RaiseChanged();
            return;
        }
        if (!CoachEngine.ChatGptConnected)
        {
            Toast = "Connect ChatGPT from Connections before asking the Coach.";
            RaiseChanged();
            return;
        }
        if (CurrentAnalysis is null)
        {
            Toast = "The Coach needs numeric deterministic analysis before it can answer.";
            RaiseChanged();
            return;
        }

        var packet = RaceCoachPacketBuilder.Build(CurrentAnalysis);
        if (!packet.HasNumericEvidence)
        {
            Toast = "The Coach cannot answer from prose alone. This race has no supported numeric evidence.";
            RaiseChanged();
            return;
        }

        _coachRequest?.Cancel();
        _coachRequest?.Dispose();
        _coachRequest = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        IsCoaching = true;
        AiRequestCount++;
        var aiStarted = Stopwatch.GetTimestamp();
        CoachAnswer = string.Empty;
        CoachProgress = "Coach is reviewing the race evidence…";
        RaiseChanged();
        try
        {
            var workflowKey = $"race:{(SelectedRaceSessionId.Length > 0 ? SelectedRaceSessionId : CurrentRaceCard.Title)}";
            Settings.CoachThreadIds.TryGetValue(workflowKey, out var threadId);
            var evidence = BuildCoachEvidence(packet);
            var reply = await _coachEngine.AskCoachAsync(threadId, CoachQuestion, evidence, _coachRequest.Token);
            Settings.CoachThreadIds[workflowKey] = reply.ThreadId;
            PersistSettingsQuietly();
            PersistPortableArtifact("ai-coaching", $"coach-{Guid.NewGuid():N}", new
            {
                schemaVersion = 1,
                createdUtc = DateTimeOffset.UtcNow,
                workflowKey,
                threadId = reply.ThreadId,
                question = CoachQuestion,
                evidence = JsonSerializer.Deserialize<JsonElement>(evidence),
                structuredResponse = reply.Text
            });
            CoachAnswer = FormatCoachReply(reply.Text);
            CoachingHistory.Insert(0, new($"coach-{reply.TurnId}", DateTimeOffset.UtcNow, workflowKey, CoachQuestion, CoachAnswer));
            CoachProgress = "Coaching response complete";
        }
        catch (OperationCanceledException)
        {
            CoachProgress = "Coaching request cancelled";
        }
        catch (Exception ex) when (ex is IOException or InvalidOperationException or JsonException or TimeoutException)
        {
            CoachProgress = "Coach could not finish this request";
            CoachAnswer = $"Your race analysis is safe. Reconnect ChatGPT from Connections and try again. {Bound(ex.Message)}";
        }
        finally
        {
            LastAiDuration = Stopwatch.GetElapsedTime(aiStarted);
            IsCoaching = false;
            RaiseChanged();
        }
    }

    public void CancelCoachRequest() => _coachRequest?.Cancel();

    public void ContinueSetupWithoutConnection()
    {
        SetupStep = Math.Min(5, SetupStep + 1);
        RaiseChanged();
    }

    public void CompleteFirstRun()
    {
        Settings.FirstRunComplete = true;
        Settings.SettingsSchemaVersion = Math.Max(Settings.SettingsSchemaVersion, 2);
        SaveSettingsToStore();
        CurrentPage = "home";
        Toast = "iRacing Coach is ready.";
        RaiseChanged();
    }

    public void RepairInstallation()
    {
        if (!_allowExternalHostActions)
        {
            Toast = "Repair launch is disabled in the isolated host profile.";
            RaiseChanged();
            return;
        }
        var setup = Path.Combine(
            _pathProvider.LocalApplicationData,
            "iRacingCoach",
            "Installer",
            $"iRacingCoach-{AppVersion}-Setup.exe");
        if (!File.Exists(setup))
        {
            Toast = "The repair package could not be found. Run the latest iRacing Coach installer again.";
            RaiseChanged();
            return;
        }
        try
        {
            var start = new ProcessStartInfo(setup) { UseShellExecute = true, Verb = "runas" };
            start.ArgumentList.Add("--repair");
            _ = Process.Start(start);
            Toast = "Windows is opening iRacing Coach Repair.";
        }
        catch (Exception ex) when (ex is InvalidOperationException or System.ComponentModel.Win32Exception)
        {
            Toast = "Repair was cancelled or could not start.";
        }
        RaiseChanged();
    }

    public void ResetSettings()
    {
        var defaults = new CompanionSettings();
        Settings.IRacingRoot = defaults.IRacingRoot;
        Settings.IRacingInstallRoot = defaults.IRacingInstallRoot;
        Settings.ThemeColor = ThemeColors.DefaultId;
        Settings.CustomThemeColor = ThemeColors.DefaultCustomHex;
        Settings.LaunchAtSignIn = false;
        Settings.UseReducedMotion = false;
        Settings.DiagnosticIncludeConfounded = false;
        var liveDefaults = new LiveMonitorLayout();
        lock (_liveMonitorVisibilityGate)
        {
            Settings.LiveMonitor.Visible = liveDefaults.Visible;
            _liveMonitorAutoReopenSuppressed = false;
        }
        Settings.LiveMonitor.ActiveLayoutId = liveDefaults.ActiveLayoutId;
        Settings.LiveMonitor.IsLocked = liveDefaults.IsLocked;
        Settings.LiveMonitor.UserLayouts = [];
        Settings.LiveMonitor.Left = liveDefaults.Left;
        Settings.LiveMonitor.Top = liveDefaults.Top;
        Settings.LiveMonitor.OverallScale = liveDefaults.OverallScale;
        Settings.LiveMonitor.SafeGlanceEnabled = liveDefaults.SafeGlanceEnabled;
        Settings.LiveMonitor.ReopenOnConnect = liveDefaults.ReopenOnConnect;
        Settings.LiveMonitor.MonitorDeviceName = liveDefaults.MonitorDeviceName;
        Settings.LiveMonitor.PlacementRecoveredAt = null;
        Settings.LiveMonitor.GlobalHotkey = liveDefaults.GlobalHotkey;
        LiveMonitorVisibilityRequested?.Invoke(false, false);
        SettingsMessage = "App preferences were restored. Protected account connections and racing history were kept.";
        RaiseChanged();
    }

    public void SetThemeColor(string colorId)
    {
        var normalized = ThemeColors.Normalize(colorId);
        if (string.Equals(Settings.ThemeColor, normalized, StringComparison.Ordinal)) return;
        Settings.ThemeColor = normalized;
        RaiseChanged();
    }

    public void SetCustomThemeColor(string color)
    {
        var normalized = ThemeColors.NormalizeCustomHex(color);
        var colorChanged = !string.Equals(Settings.CustomThemeColor, normalized, StringComparison.OrdinalIgnoreCase);
        var modeChanged = !string.Equals(Settings.ThemeColor, ThemeColors.CustomId, StringComparison.Ordinal);
        if (!colorChanged && !modeChanged) return;
        Settings.CustomThemeColor = normalized;
        Settings.ThemeColor = ThemeColors.CustomId;
        RaiseChanged();
    }

    public Task RunHealthCheckAsync(CancellationToken cancellationToken = default) => VerifyInstallationAsync(cancellationToken);

    public void Navigate(string page)
    {
        if (string.Equals(page, "connections", StringComparison.OrdinalIgnoreCase)) page = "settings";
        var capability = page switch
        {
            "planning" => ProductCapability.RacePlanning,
            "tuning" => ProductCapability.ProgressiveTuning,
            _ => (ProductCapability?)null
        };
        CurrentPage = capability.HasValue && !IsCapabilityVisible(capability.Value) ? "home" : page;
        Toast = null;
        RaiseChanged();
    }

    public void SetPrimaryUiVisible(bool visible)
    {
        PrimaryUiVisible = visible;
        if (!visible) return;

        // Paint first. MainWindow.ShowFromTray calls this on the WPF dispatcher before
        // Show(), and the sweep below reads and parses every cached race - seconds of
        // blocking I/O during which the restored window cannot draw at all.
        // ProcessHomeAnalysisRaceAsync already applies the same state from the background
        // worker, so running the sweep off-thread keeps the existing thread contract.
        RaiseChanged();
        _ = Task.Run(() =>
        {
            try
            {
                QueueMissingHomeRaceAnalysis();
            }
            catch (Exception ex) when (ex is ObjectDisposedException or IOException or UnauthorizedAccessException)
            {
                // The app closed, or the archive became unreadable, while the sweep ran.
                // The next visibility change repeats it.
            }
        });
    }
    public bool LiveMonitorVisible
    {
        get { lock (_liveMonitorVisibilityGate) return Settings.LiveMonitor.Visible; }
    }
    public void ToggleLiveMonitor() => SetLiveMonitorVisible(!LiveMonitorVisible);
    public void SetLiveMonitorVisible(bool visible, bool requestHost = true, bool userInitiated = true)
    {
        lock (_liveMonitorVisibilityGate)
        {
            if (userInitiated)
                _liveMonitorAutoReopenSuppressed = !visible && LiveState.Snapshot.Connected;
            Settings.LiveMonitor.Visible = visible;
        }
        PersistSettingsQuietly();
        if (requestHost) LiveMonitorVisibilityRequested?.Invoke(visible, visible && userInitiated);
        RaiseChanged();
    }
    public void ToggleLiveCoaching()
    {
        _liveTelemetry.SetCoachingPaused(!_liveTelemetry.CoachingPaused);
        Notify(_liveTelemetry.CoachingPaused ? "Live coaching paused." : "Live coaching resumed.");
    }
    public void SaveLiveMonitorPreferences()
    {
        PersistSettingsQuietly();
        RaiseChanged();
    }
    public void SaveRaceAnalysisTracePreferences()
    {
        Settings.RaceAnalysisTraces ??= new AnalysisTraceLayout();
        Settings.RaceAnalysisTraceLayouts ??= new AnalysisTraceLayoutSet();
        _ = AnalysisTraceLayoutSets.ValidateAndRepair(Settings.RaceAnalysisTraceLayouts, Settings.RaceAnalysisTraces);
        Settings.RaceAnalysisTraces = AnalysisTraceLayoutSets.CloneLayout(
            AnalysisTraceLayoutSets.Active(Settings.RaceAnalysisTraceLayouts).Named.Layout);
        PersistSettingsQuietly();
        RaiseChanged();
    }
    public void ToggleRail() { RailCollapsed = !RailCollapsed; RaiseChanged(); }
    public void ToggleJobTray() { JobTrayOpen = !JobTrayOpen; RaiseChanged(); }
    public void ToggleDiagnostics() { DiagnosticsExpanded = !DiagnosticsExpanded; RaiseChanged(); }
    public void OpenTroubleshooting() { DiagnosticsExpanded = true; CurrentPage = "settings"; Toast = null; RaiseChanged(); }
    public void ResetPlan() { PlanGenerated = false; PlanBriefing = null; StrategyScenarios.Clear(); PlanMessage = "Choose one of your recorded races to use its exact context."; RaiseChanged(); }

    public void SelectPlanCar(string? carId)
    {
        SelectedPlanCarId = carId?.Trim() ?? string.Empty;
        var preferred = Races.OfType<RecentRace>().FirstOrDefault(race =>
                PlanCarMatches(race) && string.Equals(SelectedPlanTrack, $"{race.Track}|{race.Layout}", StringComparison.OrdinalIgnoreCase))
            ?? Races.OfType<RecentRace>().FirstOrDefault(PlanCarMatches);
        ApplyPlanReference(preferred, updateTrack: true);
        RaiseChanged();
    }

    public void SelectPlanTrack(string? trackKey)
    {
        SelectedPlanTrack = trackKey?.Trim() ?? string.Empty;
        ApplyPlanReference(Races.OfType<RecentRace>().FirstOrDefault(race =>
            PlanCarMatches(race) && string.Equals(SelectedPlanTrack, $"{race.Track}|{race.Layout}", StringComparison.OrdinalIgnoreCase)), updateTrack: false);
        RaiseChanged();
    }

    public void SelectPlanSetupType(string? setupType)
    {
        PlanSetupType = setupType?.Trim() ?? "Fixed";
        SelectedPlanRaceId = Races.OfType<RecentRace>().FirstOrDefault(race =>
            PlanCarMatches(race) &&
            string.Equals(SelectedPlanTrack, $"{race.Track}|{race.Layout}", StringComparison.OrdinalIgnoreCase) &&
            string.Equals(PlanSetupType, race.SetupType, StringComparison.OrdinalIgnoreCase))?.Id ?? string.Empty;
        RaiseChanged();
    }

    private bool PlanCarMatches(RecentRace race) => string.Equals(SelectedPlanCarId, race.CarPath.Length > 0 ? race.CarPath : race.Car, StringComparison.OrdinalIgnoreCase);

    private void ApplyPlanReference(RecentRace? race, bool updateTrack)
    {
        SelectedPlanRaceId = race?.Id ?? string.Empty;
        if (race is null) return;
        if (updateTrack) SelectedPlanTrack = $"{race.Track}|{race.Layout}";
        PlanSetupType = race.SetupType.Equals("Open", StringComparison.OrdinalIgnoreCase) ? "Open" : "Fixed";
    }
    public void DismissToast() { Toast = null; RaiseChanged(); }
    public void Notify(string message) { Toast = message; RaiseChanged(); }
    public void OpenLogs()
    {
        var logs = Settings.LogsRoot;
        Directory.CreateDirectory(logs);
        if (!_allowExternalHostActions)
        {
            Notify("Opening folders is disabled in the isolated host profile.");
            return;
        }
        _ = Process.Start(new ProcessStartInfo("explorer.exe") { UseShellExecute = true, ArgumentList = { logs } });
    }

    public async Task PrepareBackupAsync(CancellationToken cancellationToken = default)
    {
        await _refreshGate.WaitAsync(cancellationToken);
        try
        {
            var blockers = Jobs.Where(job => job.Status is "queued" or "running").Select(job => job.Title).ToList();
            if (IsCoaching) blockers.Add("an AI coaching response");
            if (blockers.Count == 0)
            {
                var databaseIssue = await CheckpointArchiveDatabaseAsync(cancellationToken);
                if (databaseIssue is not null) blockers.Add(databaseIssue);
            }
            BackupPreparation = _archive.PrepareForCopy(Settings.CoachHome, AppVersion, "MCP v1", blockers);
            if (BackupPreparation.SafeToCopy)
            {
                if (Archive is not null)
                    Archive = Archive with
                    {
                        IntegrityVerified = true,
                        LastIntegrityCheckUtc = BackupPreparation.CheckedUtc,
                        Message = BackupPreparation.Message,
                        Restored = Archive.Restored with { UnresolvedSources = BackupPreparation.UnresolvedSources }
                    };
                SettingsMessage = $"Safe to copy: {BackupPreparation.FileCount:N0} files checked. Copy {BackupPreparation.Root}.";
                Toast = "Your Coach folder is ready to copy.";
            }
            else
            {
                SettingsMessage = BackupPreparation.Message;
                Toast = "The Coach folder is still in use. Finish the listed work and try again.";
            }
            Diagnostics = BuildDiagnostics(_lastBackendHealth);
        }
        catch (Exception ex) when (ex is not OperationCanceledException)
        {
            LastRecoverableError = StructuredAppLog.Record("prepare portable copy", ex, AppVersion, Settings.LogsRoot, _pathProvider.UserProfile);
            SettingsMessage = $"The backup check could not finish: {Bound(ex.Message)}";
            Toast = "The Coach folder is not ready to copy.";
        }
        finally
        {
            _refreshGate.Release();
            RaiseChanged();
        }
    }
    public void CancelJob(string id) { if (_jobTokens.TryGetValue(id, out var token)) token.Cancel(); }
    public void CancelAllJobs()
    {
        foreach (var token in _jobTokens.Values.ToArray()) token.Cancel();
    }
    public async Task RetryJobAsync(JobItem job, CancellationToken cancellationToken = default)
    {
        ArgumentNullException.ThrowIfNull(job);
        if (job.Status is not ("cancelled" or "failed")) return;
        if (!job.CanonicalKey.StartsWith("session:", StringComparison.Ordinal))
        {
            Notify("Open the original page to retry this work.");
            return;
        }

        var raceId = job.CanonicalKey["session:".Length..];
        var race = Races.Concat(EventSessions).FirstOrDefault(candidate =>
            candidate is not null && string.Equals(candidate.Id, raceId, StringComparison.Ordinal));
        if (race is null)
        {
            Notify("That recording is no longer available. Refresh your races and try again.");
            return;
        }
        if (Jobs.Any(candidate =>
                string.Equals(candidate.CanonicalKey, job.CanonicalKey, StringComparison.Ordinal)
                && candidate.Status is "queued" or "running"))
        {
            Notify("That work is already in progress.");
            return;
        }

        await AnalyzeRaceAsync(race, cancellationToken, force: true);
    }
    public void ClearCompletedJobs() { Jobs.RemoveAll(job => job.Status is "complete" or "cancelled" or "failed"); RaiseChanged(); }
    public void OpenArtifact() { Navigate("analysis"); }
    public void RequestLocateRawTelemetry() => RawTelemetryLocateRequested?.Invoke();

    public async Task RegisterLocatedTelemetryAsync(string path, CancellationToken cancellationToken = default)
    {
        if (!File.Exists(path) || !string.Equals(Path.GetExtension(path), ".ibt", StringComparison.OrdinalIgnoreCase))
        {
            Notify("Choose an existing iRacing .ibt telemetry file.");
            return;
        }
        try
        {
            var identity = await Task.Run(() =>
            {
                cancellationToken.ThrowIfCancellationRequested();
                using var stream = File.OpenRead(path);
                var hash = Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
                var info = new FileInfo(path);
                return (hash, info.Length, info.LastWriteTimeUtc);
            }, cancellationToken);
            PersistPortableArtifact("race-index/source-locations", $"source-{identity.hash[..16]}", new
            {
                schemaVersion = 1,
                stableId = identity.hash,
                fileName = Path.GetFileName(path),
                sha256 = identity.hash,
                bytes = identity.Length,
                lastWriteUtc = identity.LastWriteTimeUtc,
                currentPath = Path.GetFullPath(path),
                mappedUtc = DateTimeOffset.UtcNow
            });
            Toast = "The telemetry file was found. Run the copy check again to refresh its status.";
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            Toast = "That telemetry file could not be read.";
        }
        RaiseChanged();
    }

    private async Task RunJobAsync(string title, string canonicalKey, string stage, Func<CancellationToken, Task> operation, CancellationToken cancellationToken)
    {
        if (Jobs.Any(job => job.CanonicalKey == canonicalKey && job.Status is "queued" or "running"))
        {
            Notify("That work is already in progress.");
            return;
        }
        var job = new JobItem { Id = $"job-{Guid.NewGuid():N}"[..12], Title = title, CanonicalKey = canonicalKey, Stage = stage, Status = "running", Progress = 20 };
        Jobs.Insert(0, job);
        JobTrayOpen = true;
        var linked = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
        _jobTokens[job.Id] = linked;
        var timer = Stopwatch.StartNew();
        RaiseChanged();
        try
        {
            _archive.MarkActive(Settings.CoachHome);
            await operation(linked.Token);
            job.Status = "complete"; job.Stage = "Complete"; job.Progress = 100; job.Cancellable = false; job.Elapsed = timer.Elapsed;
            JobTrayOpen = false;
            Toast = null;
        }
        catch (OperationCanceledException)
        {
            job.Status = "cancelled"; job.Stage = "Cancelled safely"; job.Cancellable = false; job.Elapsed = timer.Elapsed;
            JobTrayOpen = false;
            Toast = null;
        }
        catch (Exception ex)
        {
            ReportUnhandledException($"job {title}", ex);
            job.Status = "failed"; job.Stage = Bound(ex.Message); job.Cancellable = false; job.Elapsed = timer.Elapsed;
            Toast = $"{title} needs attention.";
        }
        finally
        {
            _jobTokens.Remove(job.Id); linked.Dispose(); RaiseChanged();
        }
    }

    private void EnsureRepository()
    {
        Archive = _archive.Initialize(Settings.CoachHome, AppVersion, "MCP v1");
        Directory.CreateDirectory(Settings.LogsRoot);
        LoadPortableCoachingHistory();
        SettingsMessage = SettingsWritable
            ? Archive.Message + " Account connections stay protected on this PC."
            : Settings.Compatibility.Message;
    }

    private void LoadPortableCoachingHistory()
    {
        CoachingHistory.Clear();
        var directory = Path.Combine(Settings.ArchiveRoot, "ai-coaching");
        if (!Directory.Exists(directory)) return;
        foreach (var path in Directory.EnumerateFiles(directory, "*.json", SearchOption.TopDirectoryOnly)
            .OrderByDescending(File.GetLastWriteTimeUtc).Take(50))
        {
            try
            {
                using var document = JsonDocument.Parse(File.ReadAllText(path));
                var root = document.RootElement;
                var created = root.TryGetProperty("createdUtc", out var createdValue) && createdValue.TryGetDateTimeOffset(out var parsed)
                    ? parsed : File.GetLastWriteTimeUtc(path);
                var workflow = root.TryGetProperty("workflowKey", out var workflowValue) ? workflowValue.GetString() ?? "Archived race" : "Archived race";
                var question = root.TryGetProperty("question", out var questionValue) ? questionValue.GetString() ?? "Coaching question" : "Coaching question";
                var response = root.TryGetProperty("structuredResponse", out var responseValue) ? responseValue.GetString() ?? string.Empty : string.Empty;
                CoachingHistory.Add(new(Path.GetFileNameWithoutExtension(path), created, workflow, question, FormatCoachReply(response)));
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException) { }
        }
    }

    private void ApplyRaceDefaults()
    {
        var latest = Races.FirstOrDefault();
        if (SelectedPlanRaceId.Length == 0 || Races.All(race => race.Id != SelectedPlanRaceId))
            SelectedPlanRaceId = latest?.Id ?? string.Empty;
        if (SelectedPlanCarId.Length == 0)
            SelectedPlanCarId = latest is null ? string.Empty : latest.CarPath.Length > 0 ? latest.CarPath : latest.Car;
        if (SelectedPlanTrack.Length == 0 && latest is not null)
            SelectedPlanTrack = $"{latest.Track}|{latest.Layout}";
        if (latest is not null)
        {
            PlanSetupType = latest.SetupType.Equals("Open", StringComparison.OrdinalIgnoreCase) ? "Open" : "Fixed";
        }
        if (SelectedTuningRaceId.Length == 0 || TuningEvidenceRaces.All(race => race.Id != SelectedTuningRaceId))
            SelectedTuningRaceId = TuningEvidenceRaces.FirstOrDefault()?.Id ?? string.Empty;
    }

    private void ApplyBrowserDefault()
    {
        if (SelectedRaceSessionId.Length > 0 && EventSessions.Any(session => session.Id == SelectedRaceSessionId)) return;
        SelectedRaceSessionId = EventGroups.SelectMany(group => group.Sessions).FirstOrDefault(session => session.IsRace)?.Id
            ?? EventSessions.FirstOrDefault()?.Id
            ?? string.Empty;
    }

    private IReadOnlyList<InstalledCar> DiscoverCars(
        IReadOnlyList<RecentRace> races,
        IReadOnlyList<LocalSetup> setups)
    {
        var found = new Dictionary<string, InstalledCar>(StringComparer.OrdinalIgnoreCase);
        foreach (var race in races)
        {
            var id = race.CarPath.Length > 0 ? race.CarPath : race.Car;
            found[id] = new InstalledCar(id, race.Car, race.CarPath, "Recorded race");
        }
        foreach (var setup in setups)
        {
            if (!found.ContainsKey(setup.Car)) found[setup.Car] = new InstalledCar(setup.Car, setup.Car, setup.StoPath, "Local setup");
        }
        foreach (var root in InstalledCarRoots())
        {
            try
            {
                foreach (var directory in LeafInstalledContent(root, 3))
                {
                    var relative = Path.GetRelativePath(root, directory).Replace('\\', '/');
                    var id = relative.Replace('/', ' ');
                    var name = Humanize(Path.GetFileName(directory));
                    found.TryAdd(id, new InstalledCar(id, name, directory, "Installed on this PC"));
                }
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
        }
        return found.Values.OrderBy(car => car.Name, StringComparer.CurrentCultureIgnoreCase).ToArray();
    }

    private IReadOnlyList<InstalledTrack> DiscoverTracks(IReadOnlyList<RecentRace> races)
    {
        var found = new Dictionary<string, InstalledTrack>(StringComparer.OrdinalIgnoreCase);
        foreach (var race in races.OfType<RecentRace>())
        {
            var id = string.IsNullOrWhiteSpace(race.Layout) ? race.Track : $"{race.Track}/{race.Layout}";
            found[id] = new InstalledTrack(id, string.IsNullOrWhiteSpace(race.Layout) ? race.Track : $"{race.Track} - {race.Layout}", race.SourcePath, "Recorded race");
        }

        foreach (var root in InstalledTrackRoots())
        {
            try
            {
                foreach (var trackDirectory in Directory.EnumerateDirectories(root))
                {
                    foreach (var layoutDirectory in LeafInstalledContent(trackDirectory, 3))
                    {
                        var relative = Path.GetRelativePath(root, layoutDirectory).Replace('\\', '/');
                        var pieces = relative.Split('/', StringSplitOptions.RemoveEmptyEntries).Select(Humanize).ToArray();
                        var name = string.Join(" - ", pieces);
                        found.TryAdd(relative, new InstalledTrack(relative, name, layoutDirectory, "Installed on this PC"));
                    }
                }
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
        }

        return found.Values.OrderBy(track => track.Name, StringComparer.CurrentCultureIgnoreCase).ToArray();
    }

    private static IEnumerable<string> LeafInstalledContent(string root, int remainingDepth)
    {
        string[] children;
        try { children = remainingDepth > 0 ? Directory.GetDirectories(root) : []; }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { yield break; }

        var yieldedChild = false;
        foreach (var child in children)
        {
            foreach (var layout in LeafInstalledContent(child, remainingDepth - 1))
            {
                yieldedChild = true;
                yield return layout;
            }
        }
        if (yieldedChild) yield break;

        var containsTrackData = false;
        try { containsTrackData = Directory.EnumerateFiles(root, "*.dat", SearchOption.TopDirectoryOnly).Any(); }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
        if (containsTrackData) yield return root;
    }

    private IEnumerable<string> InstalledCarRoots()
    {
        var programFiles = _pathProvider.ProgramFiles;
        var programFilesX86 = _pathProvider.ProgramFilesX86;
        var candidates = new List<string>
        {
            Path.Combine(Settings.IRacingInstallRoot, "cars"),
            Path.Combine(programFiles, "iRacing", "cars"),
            Path.Combine(programFilesX86, "iRacing", "cars"),
            Path.Combine(programFilesX86, "Steam", "steamapps", "common", "iRacing", "cars"),
            Path.Combine(programFiles, "Steam", "steamapps", "common", "iRacing", "cars")
        };
        foreach (var driveRoot in _pathProvider.FixedDriveRoots)
        {
            candidates.Add(Path.Combine(driveRoot, "Games", "iRacing", "cars"));
            candidates.Add(Path.Combine(driveRoot, "iRacing", "cars"));
            candidates.Add(Path.Combine(driveRoot, "SteamLibrary", "steamapps", "common", "iRacing", "cars"));
        }
        return candidates.Where(Directory.Exists).Distinct(StringComparer.OrdinalIgnoreCase);
    }

    private IEnumerable<string> InstalledTrackRoots() => InstalledCarRoots()
        .Select(root => Path.Combine(Directory.GetParent(root)?.FullName ?? string.Empty, "tracks"))
        .Where(Directory.Exists)
        .Distinct(StringComparer.OrdinalIgnoreCase);

    private static string CurrentIRacingSeason()
    {
        var now = DateTimeOffset.Now;
        var season = now.Month switch
        {
            12 or 1 or 2 => 1,
            3 or 4 or 5 => 2,
            6 or 7 or 8 => 3,
            _ => 4
        };
        var year = now.Month == 12 ? now.Year + 1 : now.Year;
        return $"{year}S{season}";
    }

    private void ConfigureWatchers(string root)
    {
        lock (_watcherSync)
        {
            if (_disposed) return;
            if (string.Equals(_watcherRoot, root, StringComparison.OrdinalIgnoreCase) && _watchers.Count > 0) return;
            foreach (var watcher in _watchers) watcher.Dispose();
            _watchers.Clear();
            _watcherRoot = root;
            if (!Directory.Exists(root)) return;
            try
            {
                var watcher = new FileSystemWatcher(root)
                {
                    IncludeSubdirectories = true,
                    NotifyFilter = NotifyFilters.FileName | NotifyFilters.LastWrite | NotifyFilters.DirectoryName,
                    EnableRaisingEvents = true
                };
                watcher.Created += OnFileChanged; watcher.Changed += OnFileChanged; watcher.Renamed += OnFileChanged; watcher.Deleted += OnFileChanged;
                _watchers.Add(watcher);
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
        }
    }

    private void OnFileChanged(object sender, FileSystemEventArgs args)
    {
        var extension = Path.GetExtension(args.FullPath);
        if (extension.Length > 0 && extension is not (".ibt" or ".sto" or ".htm" or ".html")) return;
        lock (_watcherSync)
        {
            if (_disposed) return;
            _fileRefresh ??= new Timer(_ => _ = RefreshDataAsync(false, CancellationToken.None), null, Timeout.InfiniteTimeSpan, Timeout.InfiniteTimeSpan);
            _fileRefresh.Change(TimeSpan.FromSeconds(2), Timeout.InfiniteTimeSpan);
        }
    }

    private IReadOnlyList<DiagnosticFact> BuildDiagnostics(BackendHealthResult health) =>
    [
        new("App", $"v{AppVersion} · Windows x64", "ready"),
        new("Race analysis service", health.Ok ? $"Ready · {BackendVersionLabel(health.ServerVersion)} · {health.ToolCount} tools" : health.Error ?? "Unavailable", health.Ok ? "ready" : "warning"),
        new("Contract compatibility", health.Ok && health.ToolCount == 17 ? "Compatible · MCP v1" : $"Expected 17 tools; found {health.ToolCount}", health.Ok && health.ToolCount == 17 ? "ready" : "warning"),
        new("Coach Engine", CoachEngine.Installed ? $"{CoachEngine.RuntimeVersion} · {(CoachEngine.Running ? "running" : "stopped")}" : CoachEngine.Message, CoachEngine.Installed ? "ready" : "warning"),
        new("ChatGPT", CoachEngine.ChatGptConnected ? "Connected" : "Not connected; deterministic features remain available", CoachEngine.ChatGptConnected ? "ready" : "neutral"),
        new("iRacing folder", Settings.IRacingRoot, Directory.Exists(Settings.IRacingRoot) ? "ready" : "warning"),
        new("iRacing installation", Settings.IRacingInstallRoot, Directory.Exists(Settings.IRacingInstallRoot) ? "ready" : "neutral"),
        new("Coach folder", Settings.CoachHome, Directory.Exists(Settings.CoachHome) ? "ready" : "warning"),
        new("Copy readiness", Archive?.LastIntegrityCheckUtc is null ? "Not checked yet; use Move or back up" : Archive.IntegrityVerified == false ? "Coach files changed since the last copy check" : $"Verified · checked {Archive.LastIntegrityCheckUtc.Value.ToLocalTime():g}", Archive?.IntegrityVerified == false ? "warning" : Archive?.LastIntegrityCheckUtc is null ? "neutral" : "ready"),
        new("Coach data", Archive is null ? "Unavailable" : $"{Archive.Restored.TotalItems:N0} saved items · {Archive.Restored.UnresolvedSources:N0} telemetry files need locating", Archive?.Restored.UnresolvedSources > 0 ? "neutral" : "ready"),
        new("Settings file", Settings.SettingsPath, File.Exists(Settings.SettingsPath) ? "ready" : "neutral"),
        new("Setup copies", Settings.SetupsRoot, "ready"),
        .. LocalInventorySections.Select(section => new DiagnosticFact(
            $"Local {section.Name.ToLowerInvariant()}",
            section.Message,
            section.Current ? "ready" : "warning")),
        new("Garage61", Garage61.Available ? "Connected" : Garage61.Configured ? "Protected connection saved; retrying" : "Not connected", Garage61.Available ? "ready" : "neutral"),
        new("Live telemetry", LiveState.Snapshot.Connected ? $"Connected · {LiveState.Snapshot.Flag} · {LiveState.Snapshot.DataAge.TotalMilliseconds:0} ms old" : "Waiting for iRacing", LiveState.Snapshot.Connected ? "ready" : "neutral"),
        new("Live update pipeline", $"{LiveState.FramesRead:N0} frames · {LiveState.DroppedFrames:N0} dropped · {LiveState.RenderLatencyMs:0.00} ms compute", LiveState.DroppedFrames == 0 ? "ready" : "warning"),
        new("Replay capture", ReplayCaptureDiagnostic(), ReplayCaptureStatus.HasFailure ? "warning" : "ready"),
        new("Telemetry popout", Settings.LiveMonitor.Visible ? $"Visible · {LiveMonitorLayouts.Active(Settings.LiveMonitor).Layout.Name}" : "Hidden", "neutral"),
        new("Overlay compatibility", "Works above borderless-windowed iRacing and on another monitor; exclusive fullscreen may cover it", "neutral"),
        new("Race updates", "Watching for completed recordings", "ready"),
        new("Finalized races found", Races.Count.ToString(CultureInfo.CurrentCulture)),
        new("Local setup files found", Setups.Count.ToString(CultureInfo.CurrentCulture)),
        new("Cars found on this PC", Cars.Count.ToString(CultureInfo.CurrentCulture))
    ];

    private static string BackendVersionLabel(string version) =>
        Version.TryParse(version.TrimStart('v', 'V'), out _) ? $"v{version.TrimStart('v', 'V')}" : version;

    private string ReplayCaptureDiagnostic()
    {
        var status = ReplayCaptureStatus;
        if (!status.HasFailure)
            return status.State == "recording" ? "Recording locally" : "Ready";
        return $"{status.Message} {status.DroppedAfterFailure:N0} frames were not committed; the capture remains incomplete and retryable.";
    }

    private void UpdateHealth(string id, string label, string state, string detail, bool primary = false)
    {
        var index = Health.FindIndex(item => item.Id == id);
        var item = new HealthItem(id, label, state, detail, primary);
        if (index >= 0) Health[index] = item; else Health.Add(item);
    }

    private BackendConfiguration CreateBackendConfiguration()
    {
        var launcher = FindWorkspaceFile(Path.Combine("iracing-coach", "skills", "analyze-iracing-race", "scripts", "start-mcp.ps1"))
            ?? throw new BackendProtocolException("The packaged race analysis service was not found.");
        var temporaryRoot = Path.Combine(Settings.LocalStateRoot, "temp");
        Directory.CreateDirectory(temporaryRoot);
        return new BackendConfiguration(
            "powershell.exe",
            launcher,
            Settings.PythonPath,
            Settings.IRacingRoot,
            Settings.ArchiveRoot,
            Settings.CoachHome,
            Settings.IRacingInstallRoot,
            LocalStateRoot: Settings.LocalStateRoot,
            UserProfileRoot: _pathProvider.UserProfile,
            TemporaryRoot: temporaryRoot,
            NetworkAllowed: _allowExternalHostActions);
    }

    private bool MatchesRaceBrowser(RaceEventGroup group)
    {
        var query = RaceSearchText.Trim();
        if (query.Length > 0 && !new[] { group.Track, group.Layout, group.Car }
            .Concat(group.Sessions.Select(session => session.Series))
            .Any(value => value.Contains(query, StringComparison.CurrentCultureIgnoreCase)))
        {
            return false;
        }

        return RaceFilter switch
        {
            RaceBrowserFilter.Official => string.Equals(group.EventScope, "Official", StringComparison.OrdinalIgnoreCase),
            RaceBrowserFilter.HostedLeague => string.Equals(group.EventScope, "Hosted / League", StringComparison.OrdinalIgnoreCase),
            RaceBrowserFilter.Ai => string.Equals(group.EventScope, "AI", StringComparison.OrdinalIgnoreCase),
            RaceBrowserFilter.Fixed => group.Sessions.Any(session => session.SetupType == "Fixed"),
            RaceBrowserFilter.Open => group.Sessions.Any(session => session.SetupType == "Open"),
            RaceBrowserFilter.Analyzed => group.Analyzed,
            RaceBrowserFilter.NeedsAnalysis => group.Sessions.Any(session => session.IsRace && !session.Analyzed),
            _ => true
        };
    }

    private static ProductCapability FilterCapability(RaceBrowserFilter filter) => filter switch
    {
        RaceBrowserFilter.Official => ProductCapability.OfficialEventFilter,
        RaceBrowserFilter.HostedLeague => ProductCapability.HostedLeagueEventFilter,
        RaceBrowserFilter.Ai => ProductCapability.AiEventFilter,
        RaceBrowserFilter.Fixed => ProductCapability.FixedSetupFilter,
        RaceBrowserFilter.Open => ProductCapability.OpenSetupFilter,
        RaceBrowserFilter.Analyzed => ProductCapability.AnalyzedFilter,
        RaceBrowserFilter.NeedsAnalysis => ProductCapability.NeedsAnalysisFilter,
        _ => ProductCapability.RaceAnalysis
    };

    private string BuildCoachEvidence(RaceCoachPacket packet)
    {
        var card = CurrentRaceCard!;
        var corners = card.Corners.Select(row => new
        {
            row.Zone,
            Claims = new[]
            {
                Claim("Early", row.Early), Claim("Middle", row.Middle),
                Claim("Late", row.Late), Claim("Groove", row.Groove)
            }.Where(claim => claim is not null).ToArray()
        }).Where(row => row.Claims.Length > 0).ToArray();
        var selected = SelectedRaceSession;
        return JsonSerializer.Serialize(new
        {
            active_capabilities = CapabilityRegistry.ActiveForAi(CapabilityContext)
                .Select(item => new { id = item.Definition.Id.ToString(), item.Definition.Name, item.Definition.UserValue }),
            setup_changes_allowed = selected?.SetupType.Equals("Open", StringComparison.OrdinalIgnoreCase) == true,
            numeric_packet = packet,
            race = new
            {
                card.Title,
                BottomLine = Claim(null, card.BottomLine),
                Actions = card.Actions.Where(action => CapabilityRegistry.IsSupported(action.Claim))
                    .Select(action => new { action.Label, Claim = Claim(null, action.Claim) }),
                Corners = corners,
                Triggers = card.Triggers.Where(trigger => CapabilityRegistry.IsSupported(trigger.Claim))
                    .Select(trigger => new { trigger.Label, Claim = Claim(null, trigger.Claim) })
            }
        });
    }

    private static object? Claim(string? phase, EvidenceText claim) => CapabilityRegistry.IsSupported(claim)
        ? new { Phase = phase, Evidence = claim.Kind.ToString(), claim.Text }
        : null;

    private static bool HasUsefulText(string value) => !string.IsNullOrWhiteSpace(value)
        && !value.Equals("Unknown", StringComparison.OrdinalIgnoreCase)
        && !value.Equals("Unavailable", StringComparison.OrdinalIgnoreCase)
        && !value.Equals("Waiting", StringComparison.OrdinalIgnoreCase);

    private static bool TryValidateLocalRoot(string value, out string? error)
    {
        error = null;
        if (string.IsNullOrWhiteSpace(value)) { error = "A local folder is required."; return false; }
        if (value.StartsWith("\\\\", StringComparison.Ordinal) || value.StartsWith("\\\\?\\", StringComparison.Ordinal)) { error = "Network and device paths are not supported."; return false; }
        try { _ = Path.GetFullPath(value); return Path.IsPathRooted(value); }
        catch (Exception ex) when (ex is ArgumentException or NotSupportedException or PathTooLongException) { error = "The selected folder path is not valid."; return false; }
    }

    private static string? FindWorkspaceFile(string relativePath)
    {
        foreach (var start in new[] { Directory.GetCurrentDirectory(), AppContext.BaseDirectory })
        {
            var current = new DirectoryInfo(start);
            while (current is not null)
            {
                var candidate = Path.Combine(current.FullName, relativePath);
                if (File.Exists(candidate)) return candidate;
                current = current.Parent;
            }
        }
        return null;
    }

    private static string Bound(string value) => value.Length <= 240 ? value : value[..240] + "…";

    private bool TryGetAnalysisPath(RecentRace? race, out string analysisPath)
    {
        analysisPath = string.Empty;
        var candidate = race?.AnalysisPath;
        if (string.IsNullOrWhiteSpace(candidate)) return false;
        try
        {
            var fullPath = Path.GetFullPath(candidate);
            var archiveRoot = Path.GetFullPath(Settings.ArchiveRoot).TrimEnd(Path.DirectorySeparatorChar);
            if (!fullPath.StartsWith(archiveRoot + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase) ||
                !File.Exists(fullPath)) return false;
            analysisPath = fullPath;
            return true;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or ArgumentException or NotSupportedException)
        {
            return false;
        }
    }

    private bool IsCurrentRace(RecentRace? race) => race is not null && SelectedRaceSession is { } current && SameRace(current, race);

    private void InvalidateUiAnalysisCache(RecentRace race)
    {
        try
        {
            var path = UiAnalysisCachePath(race);
            if (File.Exists(path)) File.Delete(path);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            _ = StructuredAppLog.Record("Garage61 UI cache refresh", ex, AppVersion, Settings.LogsRoot, _pathProvider.UserProfile);
        }
    }

    private static string Garage61ReferenceFailure(string message)
    {
        if (message.Contains("permission", StringComparison.OrdinalIgnoreCase) ||
            message.Contains("unauthorized", StringComparison.OrdinalIgnoreCase) ||
            message.Contains("token", StringComparison.OrdinalIgnoreCase) ||
            message.Contains("401", StringComparison.OrdinalIgnoreCase) ||
            message.Contains("403", StringComparison.OrdinalIgnoreCase))
            return "Garage61 needs to be reconnected in Settings.";
        if (message.Contains("mapping", StringComparison.OrdinalIgnoreCase) ||
            message.Contains("car", StringComparison.OrdinalIgnoreCase) && message.Contains("track", StringComparison.OrdinalIgnoreCase))
            return "Garage61 could not match this race's car, track, and season.";
        return "Garage61 could not finish the reference search. Try again.";
    }

    private bool TryLoadUiAnalysisCache(RecentRace race)
    {
        var path = UiAnalysisCachePath(race);
        if (!File.Exists(path)) return TryLoadArchiveOnlyAnalysis(race);
        if (!CachedSchemaMayBeCurrent(path)) return TryLoadArchiveOnlyAnalysis(race);
        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(path));
            var root = document.RootElement;
            if (!root.TryGetProperty("schemaVersion", out var schema)
                || schema.ValueKind != JsonValueKind.Number
                || !schema.TryGetInt32(out var cacheSchema)
                || cacheSchema != UiAnalysisCacheSchemaVersion) return TryLoadArchiveOnlyAnalysis(race);
            if (!CacheMatchesSession(root, race)) return TryLoadArchiveOnlyAnalysis(race);
            if (!root.TryGetProperty("response", out var response) || response.ValueKind != JsonValueKind.Object) return TryLoadArchiveOnlyAnalysis(race);
            if (!ResponseMatchesSession(response, race)) return TryLoadArchiveOnlyAnalysis(race);
            var cachedSourceWrite = root.TryGetProperty("sourceLastWriteUtc", out var stamp) && stamp.ValueKind == JsonValueKind.String
                ? stamp.GetString() : null;
            if (!string.IsNullOrWhiteSpace(race.SourcePath) && File.Exists(race.SourcePath))
            {
                var current = File.GetLastWriteTimeUtc(race.SourcePath).ToString("O", CultureInfo.InvariantCulture);
                if (!string.Equals(current, cachedSourceWrite, StringComparison.Ordinal)) return false;
            }
            var analysis = RuntimeMapper.Analysis(response);
            EnsureAnalysisMatchesSession(race, analysis);
            CurrentRaceCard = RuntimeMapper.HasCurrentAnalysisProfile(response)
                ? RuntimeMapper.RaceCard(response)
                : null;
            CurrentAnalysis = analysis;
            ApplySuccessfulRaceAnalysis(race, analysis, AnalysisPathFromResponse(response));
            AnalysisLoading = false;
            AnalysisMessage = string.Empty;
            ApiCacheHitCount++;
            return true;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException or InvalidDataException or InvalidOperationException)
        {
            ReportUnhandledException("saved analysis cache", ex);
            return TryLoadArchiveOnlyAnalysis(race);
        }
    }

    private bool TryLoadArchiveOnlyAnalysis(RecentRace race)
    {
        // A finalized recording can be reanalyzed under current semantics.
        // Preserve historical rows whose source no longer exists, but never
        // let an unversioned analysis.json act as the live cache for an
        // available source recording.
        var sourceAvailable = !string.IsNullOrWhiteSpace(race.SourcePath)
            && File.Exists(race.SourcePath);
        return !sourceAvailable && TryLoadArchivedAnalysis(race);
    }

    private bool TryLoadArchivedAnalysis(RecentRace race)
    {
        if (string.IsNullOrWhiteSpace(race.AnalysisPath) || !File.Exists(race.AnalysisPath)) return false;
        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(race.AnalysisPath));
            var analysis = RuntimeMapper.ArchivedAnalysis(document.RootElement);
            if (!AnalysisMatchesSession(race, analysis)) return false;
            CurrentAnalysis = analysis;
            CurrentRaceCard = RuntimeMapper.HasCurrentAnalysisProfile(document.RootElement)
                ? RuntimeMapper.ArchivedRaceCard(document.RootElement)
                : null;
            ApplySuccessfulRaceAnalysis(race, CurrentAnalysis, race.AnalysisPath);
            AnalysisLoading = false;
            AnalysisMessage = string.Empty;
            ApiCacheHitCount++;
            return true;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException or InvalidDataException or InvalidOperationException)
        {
            ReportUnhandledException("archived race analysis", ex);
            return false;
        }
    }

    private IReadOnlyList<RecentRace> LoadArchivedRaces()
    {
        var reports = Path.Combine(Settings.ArchiveRoot, "reports");
        if (!Directory.Exists(reports)) return [];
        var result = new List<RecentRace>();
        foreach (var path in Directory.EnumerateFiles(reports, "analysis.json", SearchOption.AllDirectories).OrderByDescending(File.GetLastWriteTimeUtc).Take(200))
        {
            try
            {
                using var document = JsonDocument.Parse(File.ReadAllText(path));
                result.Add(RuntimeMapper.ArchivedRace(document.RootElement, path));
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException or InvalidDataException or InvalidOperationException)
            {
                ReportUnhandledException("archived race catalog", ex);
            }
        }
        return result;
    }

    private RecentRace EnrichRaceOverview(RecentRace race)
    {
        if (TryReadUiAnalysisCache(race, out var cachedOverview, out var cachedAnalysisPath))
            return MergeRaceAnalysisState(race, cachedOverview, analyzed: true, cachedAnalysisPath);
        return TryReadCachedRaceOverview(race, out var overview) ? race with { Overview = overview } : race;
    }

    private bool TryReadCachedRaceOverview(RecentRace race, out RaceOverview overview)
    {
        if (TryReadUiAnalysisCache(race, out overview)) return true;
        return TryReadArchivedRaceOverview(race, out overview);
    }

    // Split out so callers that have already tried - and failed - the UI cache do not pay
    // for a second full read and parse of the same file. On a real archive that duplicate
    // cost hundreds of megabytes per tray restore.
    private bool TryReadArchivedRaceOverview(RecentRace race, out RaceOverview overview)
    {
        overview = race.Overview ?? new RaceOverview();
        if (string.IsNullOrWhiteSpace(race.AnalysisPath) || !File.Exists(race.AnalysisPath)) return false;
        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(race.AnalysisPath));
            var archived = RuntimeMapper.ArchivedAnalysis(document.RootElement);
            if (!AnalysisMatchesSession(race, archived)) return false;
            overview = RuntimeMapper.Overview(document.RootElement);
            return true;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException or InvalidDataException or InvalidOperationException)
        {
            return false;
        }
    }

    private bool TryReadUiAnalysisCache(RecentRace race, out RaceOverview overview)
    {
        return TryReadUiAnalysisCache(race, out overview, out _);
    }

    // SaveUiAnalysisCache writes schemaVersion first, so a dead cache generation can be
    // rejected from a small prefix instead of parsing the whole entry to reach the same
    // conclusion. Entries left behind by an older schema are otherwise read in full and
    // discarded on every catalog sweep and every tray restore.
    // Unknown is deliberately optimistic: if the stamp is not in the prefix, the caller's
    // full parse decides, so an unexpected property order can never reject a valid entry.
    private static bool CachedSchemaMayBeCurrent(string path)
    {
        try
        {
            using var stream = File.OpenRead(path);
            Span<byte> prefix = stackalloc byte[512];
            var read = stream.ReadAtLeast(prefix, prefix.Length, throwOnEndOfStream: false);
            if (read <= 0) return true;
            var reader = new Utf8JsonReader(prefix[..read], isFinalBlock: false, state: default);
            while (reader.Read())
            {
                if (reader.TokenType != JsonTokenType.PropertyName) continue;
                if (!reader.ValueTextEquals("schemaVersion"u8)) continue;
                if (!reader.Read()) return true;
                return reader.TokenType == JsonTokenType.Number
                    && reader.TryGetInt32(out var cacheSchema)
                    && cacheSchema == UiAnalysisCacheSchemaVersion;
            }
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException)
        {
            // Let the full read report the failure in one place.
        }
        return true;
    }

    private bool TryReadUiAnalysisCache(RecentRace race, out RaceOverview overview, out string analysisPath)
    {
        overview = race.Overview ?? new RaceOverview();
        analysisPath = race.AnalysisPath;
        if (string.IsNullOrWhiteSpace(race.EffectiveSelector)) return false;
        var cachePath = UiAnalysisCachePath(race);
        if (!File.Exists(cachePath)) return false;
        if (!CachedSchemaMayBeCurrent(cachePath)) return false;
        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(cachePath));
            var root = document.RootElement;
            var validSchema = root.TryGetProperty("schemaVersion", out var schema)
                && schema.ValueKind == JsonValueKind.Number
                && schema.TryGetInt32(out var cacheSchema)
                && cacheSchema == UiAnalysisCacheSchemaVersion;
            var hasResponse = root.TryGetProperty("response", out var response)
                && response.ValueKind == JsonValueKind.Object
                && response.TryGetProperty("analysis_view", out var view)
                && view.ValueKind == JsonValueKind.Object;
            var cachedSourceWrite = root.TryGetProperty("sourceLastWriteUtc", out var stamp) && stamp.ValueKind == JsonValueKind.String
                ? stamp.GetString() : null;
            var sourceMatches = string.IsNullOrWhiteSpace(race.SourcePath) || !File.Exists(race.SourcePath)
                || string.Equals(File.GetLastWriteTimeUtc(race.SourcePath).ToString("O", CultureInfo.InvariantCulture), cachedSourceWrite, StringComparison.Ordinal);
            if (!validSchema || !hasResponse || !sourceMatches || !CacheMatchesSession(root, race) || !ResponseMatchesSession(response, race)) return false;
            overview = RuntimeMapper.Overview(response);
            var cachedPath = AnalysisPathFromResponse(response);
            if (!string.IsNullOrWhiteSpace(cachedPath)) analysisPath = cachedPath;
            return true;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException or InvalidDataException or InvalidOperationException)
        {
            // A stale or damaged summary cache should be repaired quietly.
            return false;
        }
    }

    private void ApplySuccessfulRaceAnalysis(RecentRace race, AnalysisWorkspace analysis, string analysisPath)
    {
        ApplyRaceAnalysisState(race, BuildRaceOverview(analysis), analyzed: true, analysisPath);
    }

    private static RaceOverview BuildRaceOverview(AnalysisWorkspace analysis)
    {
        var incidentLaps = (analysis.Damage.Incidents ?? []).Where(incident => incident.Points > 0).Select(incident => incident.Lap).ToHashSet();
        var cleanTimes = analysis.Traces.Where(trace => trace.IsComparable()
                && !incidentLaps.Contains(trace.Lap)
                && trace.LapTimeSeconds is > 0)
            .Select(trace => trace.LapTimeSeconds!.Value).ToArray();
        double? consistency = null;
        if (cleanTimes.Length >= 2)
        {
            var mean = cleanTimes.Average();
            if (mean > 0) consistency = Math.Sqrt(cleanTimes.Sum(value => Math.Pow(value - mean, 2)) / cleanTimes.Length) / mean * 100;
        }
        var longest = analysis.Runs.Where(run => run.ComparisonEligible).OrderByDescending(run => run.GreenLaps).FirstOrDefault();
        var tire = analysis.Runs.Where(run => run.TireRemainingPercent.HasValue).OrderBy(run => run.TireRemainingPercent).FirstOrDefault();
        var controlChanges = analysis.Runs.Where(run => run.ComparisonEligible).SelectMany(run => new[] { run.EarlyBrakeVsLatePercent, run.EarlySteerVsLatePercent })
            .Where(value => value.HasValue).Select(value => Math.Abs(value!.Value)).ToArray();
        var controlChange = controlChanges.Length > 0 ? controlChanges.Max() : (double?)null;
        var recordedFuel = analysis.Runs.Where(run => run.FuelUsedGallons.HasValue).Select(run => run.FuelUsedGallons!.Value).ToArray();
        return new RaceOverview(
            analysis.RecordedLaps, analysis.Runs.Sum(run => run.GreenLaps), analysis.Runs.Sum(run => run.CautionLaps),
            analysis.PitStops, analysis.Runs.Count, analysis.Runs.Select(run => run.GreenLaps).DefaultIfEmpty().Max(), longest?.PaceSlopeSecondsPerLap,
            consistency, tire?.TireRemainingPercent, tire?.TireName ?? string.Empty, controlChange,
            recordedFuel.Length > 0 ? recordedFuel.Sum() : null, cleanTimes.Length > 0 ? cleanTimes.Min() : null,
            ScheduledLaps: analysis.ScheduledLaps,
            ScheduledMinutes: analysis.ScheduledMinutes,
            DeclaredLapLimit: analysis.DeclaredLapLimit,
            DeclaredTimeLimitMinutes: analysis.DeclaredTimeLimitMinutes);
    }

    private bool ApplyRaceOverview(RecentRace race, RaceOverview overview)
    {
        return ApplyRaceAnalysisState(race, overview, analyzed: false, analysisPath: string.Empty);
    }

    private bool ApplySuccessfulRaceAnalysis(RecentRace race, RaceOverview overview, string analysisPath)
    {
        return ApplyRaceAnalysisState(race, overview, analyzed: true, analysisPath);
    }

    private bool ApplyRaceAnalysisState(RecentRace race, RaceOverview overview, bool analyzed, string analysisPath)
    {
        lock (_inventoryGate)
        {
            var changed = false;
            var eventSessionsChanged = false;
            for (var index = 0; index < Races.Count; index++)
            {
                if (!SameRace(Races[index], race)) continue;
                var updated = MergeRaceAnalysisState(Races[index], overview, analyzed, analysisPath);
                if (Equals(Races[index], updated)) continue;
                Races[index] = updated;
                changed = true;
            }
            for (var index = 0; index < EventSessions.Count; index++)
            {
                if (!SameRace(EventSessions[index], race)) continue;
                var updated = MergeRaceAnalysisState(EventSessions[index], overview, analyzed, analysisPath);
                if (Equals(EventSessions[index], updated)) continue;
                EventSessions[index] = updated;
                changed = true;
                eventSessionsChanged = true;
            }
            if (eventSessionsChanged)
            {
                // RaceEventGroup is an immutable snapshot of its sessions. Rebuild it
                // whenever a background summary replaces an EventSessions record so
                // the Race Analysis catalog observes the new overview immediately.
                EventGroups.Clear();
                EventGroups.AddRange(DashboardMapper.GroupEvents(EventSessions));
            }
            return changed;
        }
    }

    private static RecentRace MergeRaceAnalysisState(RecentRace race, RaceOverview overview, bool analyzed, string analysisPath)
    {
        if (!analyzed) return race with { Overview = overview };
        return race with
        {
            Overview = overview,
            Analyzed = true,
            Status = "Analyzed",
            AnalysisPath = string.IsNullOrWhiteSpace(analysisPath) ? race.AnalysisPath : analysisPath
        };
    }

    private void QueueMissingHomeRaceAnalysis()
    {
        if (_disposed || !HomeDataReady) return;
        var missing = new List<RecentRace>();
        var changed = false;
        var candidates = EventSessions
            .Concat(Races)
            .Where(race => race.IsRace && !string.IsNullOrWhiteSpace(race.EffectiveSelector))
            .GroupBy(UiAnalysisCacheKey, StringComparer.OrdinalIgnoreCase)
            .Select(group => group.First())
            .ToArray();
        foreach (var race in candidates)
        {
            if (TryReadUiAnalysisCache(race, out var overview, out var analysisPath))
            {
                changed |= ApplySuccessfulRaceAnalysis(race, overview, analysisPath);
                continue;
            }
            var sourceAvailable = !string.IsNullOrWhiteSpace(race.SourcePath)
                && File.Exists(race.SourcePath);
            if (!sourceAvailable && TryReadArchivedRaceOverview(race, out overview))
            {
                changed |= ApplySuccessfulRaceAnalysis(race, overview, race.AnalysisPath);
                continue;
            }
            missing.Add(race);
        }
        if (changed) RaiseChanged();

        lock (_homeAnalysisSync)
        {
            foreach (var race in missing)
            {
                var key = UiAnalysisCacheKey(race);
                if (!_homeAnalysisActiveKeys.Add(key)) continue;
                _homeAnalysisQueue.Enqueue(race);
            }
            if (_homeAnalysisQueue.Count > 0 && (_homeAnalysisWorker is null || _homeAnalysisWorker.IsCompleted))
                _homeAnalysisWorker = ProcessHomeAnalysisQueueAsync();
        }
    }

    private async Task ProcessHomeAnalysisQueueAsync()
    {
        // This is deliberately low-priority maintenance. Give the first render
        // and live SDK connection a chance to settle before touching recordings.
        try
        {
            await Task.Delay(TimeSpan.FromMilliseconds(100), _homeAnalysisCancellation.Token);
        }
        catch (OperationCanceledException) when (_homeAnalysisCancellation.IsCancellationRequested)
        {
            return;
        }
        while (!_homeAnalysisCancellation.IsCancellationRequested)
        {
            await WaitForHomeAnalysisWindowAsync();
            if (_homeAnalysisCancellation.IsCancellationRequested) return;

            RecentRace race;
            lock (_homeAnalysisSync)
            {
                if (_homeAnalysisQueue.Count == 0)
                {
                    _homeAnalysisWorker = null;
                    return;
                }
                race = _homeAnalysisQueue.Dequeue();
            }

            try { await ProcessHomeAnalysisRaceAsync(race); }
            catch (OperationCanceledException) when (_homeAnalysisCancellation.IsCancellationRequested) { return; }
        }
    }

    private async Task ProcessHomeAnalysisRaceAsync(RecentRace race)
    {
        var key = UiAnalysisCacheKey(race);
        try
        {
            if (TryReadUiAnalysisCache(race, out var cachedOverview, out var cachedAnalysisPath))
            {
                if (ApplySuccessfulRaceAnalysis(race, cachedOverview, cachedAnalysisPath)) RaiseChanged();
                return;
            }

            for (var attempt = 0; attempt < 2; attempt++)
            {
                try
                {
                    // Interactive analysis and driving always win. Recheck before
                    // every attempt because either state can change during retry delay.
                    await WaitForHomeAnalysisWindowAsync();
                    _homeAnalysisCancellation.Token.ThrowIfCancellationRequested();
                    var result = await CallBackendAsync("analyze_iracing_race", new
                    {
                        selector = race.EffectiveSelector,
                        iracing_root = Settings.IRacingRoot,
                        archive_root = Settings.ArchiveRoot,
                        target_hz = 20
                    }, _homeAnalysisCancellation.Token, DetachedBackendOperationPolicy.CompleteForCache);
                    if (!result.TryGetProperty("analysis_view", out var view) || view.ValueKind != JsonValueKind.Object)
                        throw new InvalidDataException("Background race analysis did not return an analysis view.");
                    EnsureResponseMatchesSession(race, result);

                    var overview = RuntimeMapper.Overview(result);
                    SaveUiAnalysisCache(race, result);
                    if (ApplySuccessfulRaceAnalysis(race, overview, AnalysisPathFromResponse(result))) RaiseChanged();
                    return;
                }
                catch (OperationCanceledException) when (_homeAnalysisCancellation.IsCancellationRequested)
                {
                    throw;
                }
                catch (Exception) when (attempt == 0)
                {
                    // One short retry absorbs a transient backend/read failure while
                    // remaining invisible to the job tray and bounded during startup.
                    await Task.Delay(HomeAnalysisRetryDelay, _homeAnalysisCancellation.Token);
                }
                catch (Exception)
                {
                    // Release the active key below. A later refresh or file event may
                    // schedule another bounded attempt without creating an infinite loop.
                    return;
                }
            }
        }
        finally
        {
            lock (_homeAnalysisSync) _homeAnalysisActiveKeys.Remove(key);
        }
    }

    private async Task WaitForHomeAnalysisWindowAsync()
    {
        while (!_homeAnalysisCancellation.IsCancellationRequested &&
               (AnalysisLoading || LiveState.Snapshot.Connected))
        {
            try
            {
                await Task.Delay(TimeSpan.FromMilliseconds(250), _homeAnalysisCancellation.Token);
            }
            catch (OperationCanceledException) when (_homeAnalysisCancellation.IsCancellationRequested)
            {
                return;
            }
        }
    }

    private static bool SameRace(RecentRace candidate, RecentRace selected)
    {
        if (string.Equals(candidate.Id, selected.Id, StringComparison.OrdinalIgnoreCase)) return true;
        if (!SameSessionPhase(candidate, selected)) return false;
        return (!string.IsNullOrWhiteSpace(candidate.EventKey) && string.Equals(candidate.EventKey, selected.EventKey, StringComparison.OrdinalIgnoreCase)) ||
               (!string.IsNullOrWhiteSpace(candidate.EffectiveSelector) && string.Equals(candidate.EffectiveSelector, selected.EffectiveSelector, StringComparison.OrdinalIgnoreCase));
    }

    private static bool SameSessionPhase(RecentRace first, RecentRace second) =>
        string.Equals(SessionPhase(first.SessionType), SessionPhase(second.SessionType), StringComparison.Ordinal);

    private static string SessionPhase(string? sessionType) =>
        sessionType?.Contains("qual", StringComparison.OrdinalIgnoreCase) == true
            ? "qualifying"
            : string.Equals(sessionType, "race", StringComparison.OrdinalIgnoreCase)
                ? "race"
                : sessionType?.Trim().ToLowerInvariant() ?? string.Empty;

    private static void EnsureAnalysisMatchesSession(RecentRace race, AnalysisWorkspace analysis)
    {
        if (AnalysisMatchesSession(race, analysis)) return;
        throw new InvalidDataException($"The analysis returned {analysis.SessionType} telemetry instead of the requested {race.SessionType} session.");
    }

    private static bool AnalysisMatchesSession(RecentRace race, AnalysisWorkspace analysis) =>
        string.Equals(SessionPhase(race.SessionType), SessionPhase(analysis.SessionType), StringComparison.Ordinal);

    private static bool CacheMatchesSession(JsonElement cache, RecentRace race) =>
        cache.TryGetProperty("sessionPhase", out var phase) &&
        phase.ValueKind == JsonValueKind.String &&
        string.Equals(phase.GetString(), SessionPhase(race.SessionType), StringComparison.Ordinal) &&
        cache.TryGetProperty("selector", out var selector) &&
        selector.ValueKind == JsonValueKind.String &&
        string.Equals(NormalizeSessionSelector(selector.GetString()), NormalizeSessionSelector(race.EffectiveSelector), StringComparison.Ordinal);

    private static void EnsureResponseMatchesSession(RecentRace race, JsonElement response)
    {
        var types = ResponseSessionTypes(response);
        if (types.Count == 0 || types.Any(type =>
                !string.Equals(SessionPhase(type), SessionPhase(race.SessionType), StringComparison.Ordinal)))
            throw new InvalidDataException($"Analysis returned {ResponseSessionType(response) ?? "an unknown session"} instead of the requested {race.SessionType} session.");

        var requestedSelector = NormalizeSessionSelector(race.EffectiveSelector);
        var selectors = ResponseSessionSelectors(response);
        if (requestedSelector.Length == 0 || selectors.Count == 0 || selectors.Any(selector =>
                !string.Equals(NormalizeSessionSelector(selector), requestedSelector, StringComparison.Ordinal)))
            throw new InvalidDataException($"Analysis returned telemetry for {string.Join(" / ", selectors.DefaultIfEmpty("an unknown recording"))} instead of the requested recording {race.EffectiveSelector}.");
    }

    private static bool ResponseMatchesSession(JsonElement response, RecentRace race)
    {
        var types = ResponseSessionTypes(response);
        var requestedSelector = NormalizeSessionSelector(race.EffectiveSelector);
        var selectors = ResponseSessionSelectors(response);
        return types.Count > 0 && types.All(type =>
                string.Equals(SessionPhase(type), SessionPhase(race.SessionType), StringComparison.Ordinal)) &&
            requestedSelector.Length > 0 && selectors.Count > 0 && selectors.All(selector =>
                string.Equals(NormalizeSessionSelector(selector), requestedSelector, StringComparison.Ordinal));
    }

    private static string? ResponseSessionType(JsonElement response) =>
        ResponseSessionTypes(response) is { Count: > 0 } types ? string.Join(" / ", types) : null;

    private static IReadOnlyList<string> ResponseSessionTypes(JsonElement response)
    {
        if (response.ValueKind != JsonValueKind.Object) return [];
        var types = new List<string>(2);
        if (response.TryGetProperty("selection", out var selection) && selection.ValueKind == JsonValueKind.Object &&
            selection.TryGetProperty("sim_session_type", out var selectedType) && selectedType.ValueKind == JsonValueKind.String)
        {
            if (!string.IsNullOrWhiteSpace(selectedType.GetString())) types.Add(selectedType.GetString()!);
        }
        if (response.TryGetProperty("analysis_view", out var view) && view.ValueKind == JsonValueKind.Object &&
            view.TryGetProperty("source", out var source) && source.ValueKind == JsonValueKind.Object &&
            source.TryGetProperty("selection", out var recordedSelection) && recordedSelection.ValueKind == JsonValueKind.Object &&
            recordedSelection.TryGetProperty("sim_session_type", out var recordedType) && recordedType.ValueKind == JsonValueKind.String)
        {
            if (!string.IsNullOrWhiteSpace(recordedType.GetString())) types.Add(recordedType.GetString()!);
        }
        if (types.Count == 0 && response.TryGetProperty("analysis_view", out view) && view.ValueKind == JsonValueKind.Object &&
            view.TryGetProperty("identity", out var identity) && identity.ValueKind == JsonValueKind.Object &&
            identity.TryGetProperty("event_type", out var eventType) && eventType.ValueKind == JsonValueKind.String &&
            !string.IsNullOrWhiteSpace(eventType.GetString()))
            types.Add(eventType.GetString()!);
        return types.Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
    }

    private static IReadOnlyList<string> ResponseSessionSelectors(JsonElement response)
    {
        if (response.ValueKind != JsonValueKind.Object) return [];
        var selectors = new List<string>(2);
        if (response.TryGetProperty("selection", out var selection) && selection.ValueKind == JsonValueKind.Object &&
            SessionSelectionGroup(selection) is { Length: > 0 } selectedGroup)
            selectors.Add(selectedGroup);
        if (response.TryGetProperty("analysis_view", out var view) && view.ValueKind == JsonValueKind.Object &&
            view.TryGetProperty("source", out var source) && source.ValueKind == JsonValueKind.Object &&
            source.TryGetProperty("selection", out var recordedSelection) && recordedSelection.ValueKind == JsonValueKind.Object &&
            SessionSelectionGroup(recordedSelection) is { Length: > 0 } recordedGroup)
            selectors.Add(recordedGroup);
        return selectors.Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
    }

    private static string? SessionSelectionGroup(JsonElement selection)
    {
        if (selection.TryGetProperty("group_id", out var group) && JsonScalarText(group) is { Length: > 0 } groupId)
            return groupId;
        var subsession = selection.TryGetProperty("subsession_id", out var subsessionValue) ? JsonScalarText(subsessionValue) : null;
        var simSession = selection.TryGetProperty("sim_session_num", out var simSessionValue) ? JsonScalarText(simSessionValue) : null;
        return string.IsNullOrWhiteSpace(subsession) || string.IsNullOrWhiteSpace(simSession)
            ? null
            : $"subsession:{subsession}:{simSession}";
    }

    private static string? JsonScalarText(JsonElement value) => value.ValueKind switch
    {
        JsonValueKind.String => value.GetString()?.Trim(),
        JsonValueKind.Number => value.GetRawText(),
        _ => null
    };

    private static string NormalizeSessionSelector(string? value)
    {
        var normalized = value?.Trim().Replace('\\', '/').TrimEnd('/').ToLowerInvariant() ?? string.Empty;
        var parts = normalized.Split(':');
        return parts.Length == 3 && parts[0] == "subsession" &&
               long.TryParse(parts[1], NumberStyles.Integer, CultureInfo.InvariantCulture, out var subsession) &&
               int.TryParse(parts[2], NumberStyles.Integer, CultureInfo.InvariantCulture, out var simSession)
            ? $"subsession:{subsession}:{simSession}"
            : normalized;
    }

    private IReadOnlyList<LocalSetup> LoadArchivedSetups()
    {
        var reports = Path.Combine(Settings.ArchiveRoot, "reports");
        if (!Directory.Exists(reports)) return [];
        var result = new List<LocalSetup>();
        foreach (var path in Directory.EnumerateFiles(reports, "analysis.json", SearchOption.AllDirectories).OrderByDescending(File.GetLastWriteTimeUtc).Take(200))
        {
            try
            {
                using var document = JsonDocument.Parse(File.ReadAllText(path));
                if (RuntimeMapper.ArchivedSetup(document.RootElement, path) is { } setup && result.All(item => !string.Equals(item.Id, setup.Id, StringComparison.OrdinalIgnoreCase))) result.Add(setup);
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException or InvalidDataException or InvalidOperationException)
            {
                ReportUnhandledException("archived setup catalog", ex);
            }
        }
        return result;
    }

    private void SaveUiAnalysisCache(RecentRace race, JsonElement response)
    {
        PersistPortableArtifact("ui-analysis-cache", UiAnalysisCacheKey(race), new
        {
            schemaVersion = UiAnalysisCacheSchemaVersion,
            sessionPhase = SessionPhase(race.SessionType),
            selector = race.EffectiveSelector,
            sourceLastWriteUtc = !string.IsNullOrWhiteSpace(race.SourcePath) && File.Exists(race.SourcePath)
                ? File.GetLastWriteTimeUtc(race.SourcePath).ToString("O", CultureInfo.InvariantCulture)
                : null,
            savedUtc = DateTimeOffset.UtcNow,
            response
        });
    }

    private static string AnalysisPathFromResponse(JsonElement response)
    {
        if (response.ValueKind != JsonValueKind.Object) return string.Empty;
        if (response.TryGetProperty("analysis_path", out var path)
            && path.ValueKind == JsonValueKind.String
            && !string.IsNullOrWhiteSpace(path.GetString()))
        {
            return path.GetString()!;
        }
        if (response.TryGetProperty("artifacts", out var artifacts)
            && artifacts.ValueKind == JsonValueKind.Object
            && artifacts.TryGetProperty("analysis", out var artifactPath)
            && artifactPath.ValueKind == JsonValueKind.String
            && !string.IsNullOrWhiteSpace(artifactPath.GetString()))
        {
            return artifactPath.GetString()!;
        }
        return string.Empty;
    }

    private string UiAnalysisCachePath(RecentRace race) =>
        Path.Combine(Settings.ArchiveRoot, "ui-analysis-cache", UiAnalysisCacheKey(race) + ".json");

    private static string UiAnalysisCacheKey(RecentRace race) =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes($"{SessionPhase(race.SessionType)}|{race.EffectiveSelector}"))).ToLowerInvariant();

    private void PersistPortableArtifact(string component, string name, object value)
    {
        try
        {
            _archive.MarkActive(Settings.CoachHome);
            var directory = Path.GetFullPath(Path.Combine(Settings.ArchiveRoot, component));
            var archiveRoot = Path.GetFullPath(Settings.ArchiveRoot).TrimEnd(Path.DirectorySeparatorChar);
            if (!directory.StartsWith(archiveRoot + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("The saved-data path escaped the Coach data folder.");
            Directory.CreateDirectory(directory);
            var safeName = string.Concat(name.Select(character => Path.GetInvalidFileNameChars().Contains(character) || character is ':' or '/' or '\\' ? '-' : character));
            var path = Path.Combine(directory, safeName + ".json");
            var temporary = path + $".{Guid.NewGuid():N}.tmp";
            File.WriteAllText(temporary, JsonSerializer.Serialize(value, PortableArtifactJson));
            File.Move(temporary, path, overwrite: true);

            var activityDirectory = Path.Combine(Settings.ArchiveRoot, "activity-history");
            Directory.CreateDirectory(activityDirectory);
            var activityPath = Path.Combine(activityDirectory, $"{DateTimeOffset.UtcNow:yyyyMMdd-HHmmssfff}-{Guid.NewGuid():N}.json");
            var activityTemporary = activityPath + ".tmp";
            File.WriteAllText(activityTemporary, JsonSerializer.Serialize(new
            {
                schemaVersion = 1,
                createdUtc = DateTimeOffset.UtcNow,
                action = component,
                artifactId = safeName
            }, PortableArtifactJson));
            File.Move(activityTemporary, activityPath);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or ArgumentException or JsonException)
        {
            // The backend result remains authoritative; a portable history write
            // failure is surfaced by the next archive integrity check.
        }
    }
    private static string Humanize(string value) => CultureInfo.CurrentCulture.TextInfo.ToTitleCase(value.Replace('_', ' ').Replace('-', ' '));
    private static string RelativeTime(DateTimeOffset value)
    {
        var age = DateTimeOffset.Now - value;
        if (age < TimeSpan.FromSeconds(10)) return "just now";
        if (age < TimeSpan.FromMinutes(1)) return $"{(int)age.TotalSeconds}s ago";
        return $"{(int)age.TotalMinutes}m ago";
    }

    private void RaiseChanged() => Changed?.Invoke();

    private void OnLiveTelemetryUpdated(LiveMonitorState state)
    {
        var connected = state.Snapshot.Connected;
        var openMonitor = false;
        lock (_liveMonitorVisibilityGate)
        {
            if (!connected || !_liveMonitorWasConnected) _liveMonitorAutoReopenSuppressed = false;
            _liveMonitorWasConnected = connected;
            if (connected && Settings.LiveMonitor.ReopenOnConnect && !Settings.LiveMonitor.Visible && !_liveMonitorAutoReopenSuppressed)
            {
                Settings.LiveMonitor.Visible = true;
                openMonitor = true;
            }
        }
        if (openMonitor)
        {
            PersistSettingsQuietly();
            LiveMonitorVisibilityRequested?.Invoke(true, false);
        }
        LiveTelemetryChanged?.Invoke();
        var now = DateTimeOffset.UtcNow;
        if (PrimaryUiVisible && now - _lastPrimaryUiLiveUpdate >= TimeSpan.FromMilliseconds(250))
        {
            _lastPrimaryUiLiveUpdate = now;
            RaiseChanged();
        }
    }

    private void OnLiveTelemetryFrameCaptured(LiveTracePoint frame) => LiveTelemetryFrame?.Invoke(frame);

    private void OnLiveReplayFrameCaptured(LiveReplayCaptureFrame frame) => _liveReplayCapture.Capture(frame);

    private void OnLiveReplaySessionEnded(string reason) => _liveReplayCapture.EndSession(reason);

    private void OnReplayCaptureStatusChanged()
    {
        var status = ReplayCaptureStatus;
        if (status.HasFailure && status.FirstFailedAt != _lastReplayFailureNotifiedAt)
        {
            _lastReplayFailureNotifiedAt = status.FirstFailedAt;
            Toast = "Replay capture is incomplete; live telemetry continues. Open Diagnostics for details.";
        }
        Diagnostics = BuildDiagnostics(_lastBackendHealth);
        RaiseChanged();
    }

    private async Task InitializeCoachEngineAsync()
    {
        try
        {
            await _coachEngine.StartAsync(Settings);
        }
        catch (Exception ex) when (ex is IOException or InvalidOperationException or JsonException or TimeoutException)
        {
            CoachEngine = new(false, false, false, "unavailable", $"Coach Engine could not start: {Bound(ex.Message)}");
            UpdateHealth("coachengine", "Coach Engine", "unavailable", "Optional AI coaching is unavailable", false);
            RaiseChanged();
        }
    }

    private void OnCoachEngineChanged(CoachEngineConnection connection)
    {
        CoachEngine = connection;
        var state = !connection.Installed || connection.Status is "repair" or "unavailable"
            ? "unavailable"
            : connection.ChatGptConnected ? "ready" : connection.Running ? "neutral" : "warning";
        var detail = connection.ChatGptConnected ? "ChatGPT connected" : connection.Message;
        UpdateHealth("coachengine", "Coach Engine", state, detail, false);
        if (connection.ChatGptConnected)
        {
            PendingChatGptLoginId = null;
            SetupStep = Math.Max(SetupStep, 3);
        }
        RaiseChanged();
    }

    private void OnCoachMessageDelta(string delta)
    {
        if (!IsCoaching) return;
        CoachProgress = $"Coach is writing… {Math.Max(1, delta.Length)} new character{(delta.Length == 1 ? string.Empty : "s")}";
        RaiseChanged();
    }

    private static string FormatCoachReply(string value)
    {
        try
        {
            using var document = JsonDocument.Parse(value);
            var root = document.RootElement;
            var lines = new List<string>();
            if (root.TryGetProperty("summary", out var summary) && !string.IsNullOrWhiteSpace(summary.GetString())) lines.Add(summary.GetString()!);
            if (root.TryGetProperty("actions", out var actions) && actions.ValueKind == JsonValueKind.Array)
            {
                foreach (var action in actions.EnumerateArray())
                {
                    if (action.TryGetProperty("text", out var text) && !string.IsNullOrWhiteSpace(text.GetString())) lines.Add($"• {text.GetString()}");
                }
            }
            if (root.TryGetProperty("limitations", out var limitations) && limitations.ValueKind == JsonValueKind.Array)
            {
                var notes = limitations.EnumerateArray().Select(item => item.GetString()).Where(item => !string.IsNullOrWhiteSpace(item)).ToArray();
                if (notes.Length > 0) lines.Add($"Evidence limits: {string.Join("; ", notes)}");
            }
            return lines.Count > 0 ? string.Join(Environment.NewLine + Environment.NewLine, lines) : value;
        }
        catch (JsonException)
        {
            return value;
        }
    }

    private void SaveSettingsToStore()
    {
        lock (_settingsPersistenceGate)
            _settingsStore?.Save(Settings);
    }

    private void PersistSettingsQuietly()
    {
        try
        {
            lock (_settingsPersistenceGate)
            {
                _archive.MarkActive(Settings.CoachHome);
                _settingsStore?.Save(Settings);
            }
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or InvalidOperationException) { }
    }

    private async Task<string?> CheckpointArchiveDatabaseAsync(CancellationToken cancellationToken)
    {
        var database = Path.Combine(Settings.ArchiveRoot, "history.sqlite3");
        if (!File.Exists(database)) return null;
        try
        {
            var start = new ProcessStartInfo(Settings.PythonPath)
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardError = true,
                RedirectStandardOutput = true
            };
            start.ArgumentList.Add("-c");
            start.ArgumentList.Add("import sqlite3,sys; c=sqlite3.connect(sys.argv[1],timeout=15); c.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall(); ok=c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'; c.commit(); c.close(); raise SystemExit(0 if ok else 2)");
            start.ArgumentList.Add(database);
            using var process = Process.Start(start) ?? throw new IOException("The packaged database checkpoint process did not start.");
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeout.CancelAfter(TimeSpan.FromSeconds(20));
            await process.WaitForExitAsync(timeout.Token);
            if (process.ExitCode == 0 && !File.Exists(database + "-wal")) return null;
            var detail = await process.StandardError.ReadToEndAsync(cancellationToken);
            return string.IsNullOrWhiteSpace(detail) ? "the race-history database checkpoint" : "the race-history database checkpoint (see Diagnostics)";
        }
        catch (Exception ex) when (ex is IOException or InvalidOperationException or System.ComponentModel.Win32Exception or OperationCanceledException)
        {
            return "the race-history database checkpoint";
        }
    }

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        _homeAnalysisCancellation.Cancel();
        _refreshLifetime.Cancel();
        _backendOperations.Dispose();
        Task[] refreshTasks;
        lock (_refreshSync)
            refreshTasks = new[] { _refreshTask, _garageRefreshTask }.Where(task => task is not null).Cast<Task>().ToArray();
        var refreshesStopped = refreshTasks.Length == 0;
        if (!refreshesStopped)
        {
            try { refreshesStopped = Task.WhenAll(refreshTasks).Wait(TimeSpan.FromSeconds(5)); }
            catch (AggregateException)
            {
                refreshesStopped = refreshTasks.All(task => task.IsCompleted);
            }
        }
        lock (_watcherSync)
        {
            _fileRefresh?.Dispose();
            foreach (var watcher in _watchers) watcher.Dispose();
            _watchers.Clear();
        }
        foreach (var token in _jobTokens.Values) { token.Cancel(); token.Dispose(); }
        _coachRequest?.Cancel();
        _coachRequest?.Dispose();
        _liveTelemetry.Updated -= OnLiveTelemetryUpdated;
        _liveTelemetry.FrameCaptured -= OnLiveTelemetryFrameCaptured;
        _liveTelemetry.ReplayFrameCaptured -= OnLiveReplayFrameCaptured;
        _liveTelemetry.ReplaySessionEnded -= OnLiveReplaySessionEnded;
        _liveReplayCapture.StatusChanged -= OnReplayCaptureStatusChanged;
        _liveTelemetry.Dispose();
        _liveReplayCapture.Dispose();
        _coachEngine.Changed -= OnCoachEngineChanged;
        _coachEngine.CoachMessageDelta -= OnCoachMessageDelta;
        _coachEngine.DisposeAsync().AsTask().GetAwaiter().GetResult();
        try
        {
            if (_initialized && Archive is not null)
            {
                var blockers = Jobs.Where(job => job.Status is "queued" or "running").Select(job => job.Title).ToArray();
                if (blockers.Length == 0)
                    _ = CheckpointArchiveDatabaseAsync(CancellationToken.None).GetAwaiter().GetResult();
                _archive.PrepareForCopy(Settings.CoachHome, AppVersion, "MCP v1", blockers);
            }
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or InvalidDataException or InvalidOperationException) { }
        _homeAnalysisCancellation.Dispose();
        _refreshLifetime.Dispose();
        if (refreshesStopped) _refreshGate.Dispose();
        else _ = Task.WhenAll(refreshTasks).ContinueWith(completed =>
        {
            _ = completed.Exception;
            _refreshGate.Dispose();
        }, TaskScheduler.Default);
    }
}
