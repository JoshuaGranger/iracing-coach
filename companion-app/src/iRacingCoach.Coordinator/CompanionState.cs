using System.Diagnostics;
using System.Collections.Concurrent;
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
    bool Priority);

public sealed class CompanionState : IDisposable
{
    private const int UiAnalysisCacheSchemaVersion = 4;
    private const string AppVersion = "0.12.0";
    private readonly IBackendClient _backend;
    private readonly ISettingsStore? _settingsStore;
    private readonly IGarage61CredentialStore _garage61Credentials;
    private readonly ICoachEngineSupervisor _coachEngine;
    private readonly LiveTelemetryService _liveTelemetry;
    private readonly IDurableArchiveService _archive;
    private readonly Dictionary<string, CancellationTokenSource> _jobTokens = new(StringComparer.OrdinalIgnoreCase);
    private readonly ConcurrentDictionary<string, Lazy<Task<JsonElement>>> _inflightBackendCalls = new(StringComparer.Ordinal);
    private CancellationTokenSource? _coachRequest;
    private readonly SemaphoreSlim _refreshGate = new(1, 1);
    private Timer? _fileRefresh;
    private readonly List<FileSystemWatcher> _watchers = [];
    private bool _initialized;
    private bool _disposed;
    private DateTimeOffset _lastPrimaryUiLiveUpdate = DateTimeOffset.MinValue;
    private int _serviceRequestsInFlight;
    private BackendHealthResult _lastBackendHealth = new(false, "unknown", "unknown", "unknown", 0, TimeSpan.Zero, "Not checked");

    public CompanionState() : this(new McpBackendClient(), new JsonSettingsStore(), new IRacingSdkTelemetrySource(), new CodexAppServerSupervisor(), new PowerShellGarage61CredentialStore(), new DurableArchiveService()) { }
    public CompanionState(ILiveTelemetrySource liveTelemetrySource) : this(new McpBackendClient(), new JsonSettingsStore(), liveTelemetrySource, new CodexAppServerSupervisor(), new PowerShellGarage61CredentialStore(), new DurableArchiveService()) { }
    public CompanionState(IBackendClient backend) : this(backend, null, new DisconnectedLiveTelemetrySource(), new DisabledCoachEngineSupervisor(), new PowerShellGarage61CredentialStore()) { }

    public CompanionState(IBackendClient backend, ISettingsStore? settingsStore) : this(backend, settingsStore, new DisconnectedLiveTelemetrySource(), new DisabledCoachEngineSupervisor(), new PowerShellGarage61CredentialStore()) { }

    public CompanionState(IBackendClient backend, ISettingsStore? settingsStore, ILiveTelemetrySource liveTelemetrySource, ICoachEngineSupervisor? coachEngine = null, IGarage61CredentialStore? garage61Credentials = null, IDurableArchiveService? archive = null)
    {
        _backend = backend;
        _settingsStore = settingsStore;
        _garage61Credentials = garage61Credentials ?? new PowerShellGarage61CredentialStore();
        _coachEngine = coachEngine ?? new DisabledCoachEngineSupervisor();
        _archive = archive ?? new DurableArchiveService();
        Settings = settingsStore?.Load() ?? new CompanionSettings();
        Settings.LiveMonitor ??= new LiveMonitorLayout();
        CurrentPage = Settings.FirstRunComplete ? "home" : "first-run";
        _liveTelemetry = new LiveTelemetryService(liveTelemetrySource, Settings.LiveMonitor);
        _liveTelemetry.Updated += OnLiveTelemetryUpdated;
        _liveTelemetry.FrameCaptured += OnLiveTelemetryFrameCaptured;
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
    public event Action<bool>? LiveMonitorVisibilityRequested;
    public event Action? RawTelemetryLocateRequested;

    public string CurrentPage { get; private set; } = "home";
    public bool RailCollapsed { get; private set; }
    public bool JobTrayOpen { get; private set; }
    public bool IsRefreshing { get; private set; }
    public bool PlanGenerated { get; private set; }
    public bool ExperimentGenerated { get; private set; }
    public bool DiagnosticsExpanded { get; private set; }
    public string? Toast { get; private set; }
    public string SettingsMessage { get; private set; } = "Preferences and racing history live in the Coach folder. Account connections remain protected on this PC.";
    public string DataMessage { get; private set; } = "Looking for finalized iRacing recordings…";
    public string PlanMessage { get; private set; } = "Choose one of your recorded races to use its exact car, track, and setup context.";
    public string SetupMessage { get; private set; } = "Only setup files found on this PC are shown.";
    public string TuningMessage { get; private set; } = "Choose an analyzed open-setup race and describe what the car did.";
    public string SymptomText { get; set; } = string.Empty;
    public string TuningRunPhase { get; set; } = "Late run";
    public string TuningCornerPhase { get; set; } = "Center";
    public string TuningBalance { get; set; } = string.Empty;
    public string TuningSeverity { get; set; } = "Moderate";
    public string TuningConfidence { get; set; } = "Medium";
    public bool TuningPriority { get; set; }
    public string TuningCorner { get; set; } = "Whole lap";
    public string TuningNotes { get; set; } = string.Empty;
    public string FeedbackNotes { get; set; } = string.Empty;
    public string Garage61KeyInput { get; set; } = string.Empty;
    public string CoachQuestion { get; set; } = "What should I work on first based on this race?";
    public string CoachAnswer { get; private set; } = string.Empty;
    public string CoachProgress { get; private set; } = string.Empty;
    public bool IsCoaching { get; private set; }
    public int SetupStep { get; private set; } = 1;
    public string? PendingChatGptLoginId { get; private set; }
    public string SelectedPlanRaceId { get; set; } = string.Empty;
    public string SelectedPlanCarId { get; set; } = string.Empty;
    public string SelectedPlanTrack { get; set; } = string.Empty;
    public string PlanDistanceMode { get; set; } = "Laps";
    public int PlanDistanceValue { get; set; } = 50;
    public string PlanSetupType { get; set; } = "Fixed";
    public string SelectedTuningRaceId { get; set; } = string.Empty;
    public string SelectedSetupId { get; set; } = string.Empty;
    public string CompareSetupId { get; set; } = string.Empty;
    public int StartingTuneStep { get; private set; } = 1;
    public string StartingTuneSeason { get; set; } = string.Empty;
    public string StartingTuneCar { get; set; } = string.Empty;
    public string StartingTuneTrack { get; set; } = string.Empty;
    public string StartingTunePurpose { get; set; } = "Race";
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
    public List<LocalSetup> Setups { get; } = [];
    public List<StrategyScenario> StrategyScenarios { get; } = [];
    public List<TuningFeedbackDraft> TuningFeedback { get; } = [];
    public RacePlanBriefing? PlanBriefing { get; private set; }
    public TuningExperimentView? TuningExperiment { get; private set; }
    public SetupPackageView? StartingTunePackage { get; private set; }
    public RaceCard? CurrentRaceCard { get; private set; }
    public AnalysisWorkspace? CurrentAnalysis { get; private set; }
    public bool AnalysisWorkspaceOpen { get; private set; }
    public bool AnalysisLoading { get; private set; }
    public string AnalysisMessage { get; private set; } = string.Empty;
    public Garage61Connection Garage61 { get; private set; } = new(false, false, "checking", "Checking connection…");
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
    public bool LiveCoachingPaused => _liveTelemetry.CoachingPaused;
    public bool PrimaryUiVisible { get; private set; } = true;
    public bool IRacingDetected => Directory.Exists(Settings.IRacingRoot);

    public void ReportUnhandledException(string scope, Exception exception)
    {
        LastRecoverableError = StructuredAppLog.Record(scope, exception, AppVersion);
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
    public RecentRace? SelectedRaceSession => EventSessions.OfType<RecentRace>().FirstOrDefault(session => string.Equals(session.Id, SelectedRaceSessionId, StringComparison.Ordinal));
    public LocalSetup? SelectedSetup => Setups.OfType<LocalSetup>().FirstOrDefault(setup => string.Equals(setup.Id, SelectedSetupId, StringComparison.Ordinal));
    public LocalSetup? CompareSetup => Setups.OfType<LocalSetup>().FirstOrDefault(setup => string.Equals(setup.Id, CompareSetupId, StringComparison.Ordinal));
    public IEnumerable<RecentRace> TuningRaces => Races.OfType<RecentRace>().Where(race => race.Analyzed && string.Equals(race.SetupType, "Open", StringComparison.OrdinalIgnoreCase) && !string.IsNullOrWhiteSpace(race.AnalysisPath));
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
                HasOpenAnalyzedRace = TuningRaces.Any(),
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
                new("Portable archive", ex.Message, "warning"),
                new("Portable Coach folder", Settings.CoachHome, "warning")
            ];
            RaiseChanged();
            return;
        }
        await RefreshDataAsync(showToast: false, cancellationToken);
        ConfigureWatchers();
        _liveTelemetry.Start();
        _ = InitializeCoachEngineAsync();
        if (Settings.LiveMonitor.Visible) LiveMonitorVisibilityRequested?.Invoke(true);
    }

    public Task RefreshDashboardAsync(CancellationToken cancellationToken = default) => RefreshDataAsync(true, cancellationToken);

    private async Task RefreshDataAsync(bool showToast, CancellationToken cancellationToken)
    {
        if (!_refreshGate.Wait(0)) return;
        IsRefreshing = true;
        RaiseChanged();
        try
        {
            var configuration = CreateBackendConfiguration();
            var healthTask = _backend.CheckHealthAsync(configuration, cancellationToken);
            var dashboardTask = SafeToolAsync("iracing_companion_dashboard", new
            {
                root = Settings.IRacingRoot,
                archive_root = Settings.ArchiveRoot,
                limit = 50
            }, cancellationToken);
            var discoveryTask = SafeToolAsync("discover_iracing_sessions", new
            {
                root = Settings.IRacingRoot,
                races_only = false,
                limit = 200
            }, cancellationToken);
            var setupTask = SafeToolAsync("catalog_iracing_setups", new
            {
                root = Settings.IRacingRoot,
                archive_root = Settings.ArchiveRoot,
                maximum_entries = 500
            }, cancellationToken);
            var garageTask = SafeToolAsync("garage61_auth_status", new
            {
                archive_root = Settings.ArchiveRoot
            }, cancellationToken);

            await Task.WhenAll(healthTask, dashboardTask, discoveryTask, setupTask, garageTask);
            var health = await healthTask;
            _lastBackendHealth = health;
            UpdateHealth("backend", "Race analysis", health.Ok ? "ready" : "unavailable",
                health.Ok ? "Ready" : "Needs attention", true);

            var dashboard = await dashboardTask;
            if (dashboard.ValueKind == JsonValueKind.Object)
            {
                var mapped = DashboardMapper.Map(dashboard);
                Races.Clear();
                Races.AddRange(mapped.Select((race, index) => index < 6 ? EnrichRaceOverview(race) : race));
                var discovery = await discoveryTask;
                EventSessions.Clear();
                EventSessions.AddRange(DashboardMapper.MapEvents(dashboard, discovery));
                foreach (var archived in LoadArchivedRaces())
                {
                    if (Races.All(race => !string.Equals(race.Id, archived.Id, StringComparison.OrdinalIgnoreCase))) Races.Add(archived);
                    if (EventSessions.All(race => !string.Equals(race.Id, archived.Id, StringComparison.OrdinalIgnoreCase))) EventSessions.Add(archived);
                }
                EventGroups.Clear();
                EventGroups.AddRange(DashboardMapper.GroupEvents(EventSessions));
                if (!AvailableRaceFilters.Contains(RaceFilter)) RaceFilter = RaceBrowserFilter.All;
                DataMessage = Races.Count == 0
                    ? "No finalized race recordings were found. iRacing recordings will appear here automatically."
                    : $"{Races.Count} race recording{(Races.Count == 1 ? string.Empty : "s")} found.";
                ApplyRaceDefaults();
                ApplyBrowserDefault();
            }

            var setupResponse = await setupTask;
            if (setupResponse.ValueKind == JsonValueKind.Object)
            {
                Setups.Clear();
                Setups.AddRange(RuntimeMapper.Setups(setupResponse));
                foreach (var archived in LoadArchivedSetups())
                    if (Setups.All(setup => !string.Equals(setup.Id, archived.Id, StringComparison.OrdinalIgnoreCase))) Setups.Add(archived);
                if (SelectedSetupId.Length == 0 || Setups.All(setup => setup.Id != SelectedSetupId))
                {
                    SelectedSetupId = Setups.FirstOrDefault()?.Id ?? string.Empty;
                }
                if (CompareSetupId.Length == 0 || Setups.All(setup => setup.Id != CompareSetupId))
                {
                    CompareSetupId = Setups.FirstOrDefault(setup => setup.Id != SelectedSetupId)?.Id ?? string.Empty;
                }
            }

            var garageResponse = await garageTask;
            if (garageResponse.ValueKind == JsonValueKind.Object)
            {
                Garage61 = RuntimeMapper.Garage61(garageResponse);
            }
            UpdateHealth("garage61", "Garage61", Garage61.Available ? "ready" : Garage61.Configured ? "warning" : "neutral",
                Garage61.Available ? "Connected" : Garage61.Configured ? "Key saved · offline" : "Not configured");
            UpdateHealth("repository", "Coach data", "ready", "Portable folder ready");

            DiscoverCars();
            Diagnostics = BuildDiagnostics(health);
            LastUpdated = DateTimeOffset.Now;
            if (showToast) Toast = "Your local racing data is up to date.";
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            if (showToast) Toast = "Update cancelled.";
        }
        catch (Exception ex)
        {
            ReportUnhandledException("catalog refresh", ex);
            UpdateHealth("backend", "Race analysis", "unavailable", "Could not read local racing data", true);
            UpdateHealth("garage61", "Garage61", "neutral", "Not checked");
            UpdateHealth("repository", "Coach data", "ready", "Portable folder ready");
            DataMessage = $"The app could not update: {Bound(ex.Message)}";
            if (showToast) Toast = "The update needs attention. Open Diagnostics for details.";
        }
        finally
        {
            IsRefreshing = false;
            _refreshGate.Release();
            RaiseChanged();
        }
    }

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

    private async Task<JsonElement> CallBackendAsync(string name, object arguments, CancellationToken cancellationToken)
    {
        var requestKey = $"{name}|{JsonSerializer.Serialize(arguments)}";
        var pending = new Lazy<Task<JsonElement>>(
            () => ExecuteBackendCallAsync(name, arguments, cancellationToken),
            LazyThreadSafetyMode.ExecutionAndPublication);
        var shared = _inflightBackendCalls.GetOrAdd(requestKey, pending);
        try
        {
            return await shared.Value;
        }
        finally
        {
            _inflightBackendCalls.TryRemove(new KeyValuePair<string, Lazy<Task<JsonElement>>>(requestKey, shared));
        }
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
        SelectRaceSession(race);
        AnalysisWorkspaceOpen = true;
        AnalysisLoading = true;
        AnalysisMessage = race.Analyzed ? "Opening telemetry…" : "Reading telemetry…";
        CurrentRaceCard = null;
        CurrentAnalysis = null;
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
                selector = race.EffectiveSelector,
                iracing_root = Settings.IRacingRoot,
                archive_root = Settings.ArchiveRoot,
                target_hz = 20
            }, token);
            var mappedCard = RuntimeMapper.RaceCard(result);
            var mappedAnalysis = RuntimeMapper.Analysis(result);
            CurrentRaceCard = mappedCard;
            CurrentAnalysis = mappedAnalysis;
            UpdateRaceOverview(race, mappedAnalysis);
            SaveUiAnalysisCache(race, result);
        }, cancellationToken);
        AnalysisLoading = false;
        AnalysisMessage = CurrentAnalysis is null ? "This recording could not be opened. Retry or copy the support details." : string.Empty;
        RaiseChanged();
    }

    public Task OpenRaceAsync(RecentRace race, CancellationToken cancellationToken = default) => AnalyzeRaceAsync(race, cancellationToken);
    public Task ReanalyzeRaceAsync(CancellationToken cancellationToken = default) => SelectedRaceSession is { } race
        ? AnalyzeRaceAsync(race, cancellationToken, force: true)
        : Task.CompletedTask;

    public void CloseAnalysisWorkspace()
    {
        AnalysisWorkspaceOpen = false;
        AnalysisLoading = false;
        AnalysisMessage = string.Empty;
        RaiseChanged();
    }

    public async Task OpenRaceFromHomeAsync(RecentRace race, CancellationToken cancellationToken = default)
    {
        var session = EventSessions.FirstOrDefault(candidate =>
            string.Equals(candidate.Id, race.Id, StringComparison.OrdinalIgnoreCase) ||
            (race.EventKey.Length > 0 && string.Equals(candidate.EventKey, race.EventKey, StringComparison.OrdinalIgnoreCase)) ||
            (race.EffectiveSelector.Length > 0 && string.Equals(candidate.EffectiveSelector, race.EffectiveSelector, StringComparison.OrdinalIgnoreCase))) ?? race;

        Navigate("analysis");
        SelectRaceSession(session);
        await AnalyzeRaceAsync(session, cancellationToken);
    }

    public void SelectRaceSession(RecentRace session)
    {
        SelectedRaceSessionId = session.Id;
        RaiseChanged();
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

        try
        {
            var historyTask = CallBackendAsync("iracing_strategy_history", new
            {
                analysis_path = race.AnalysisPath,
                archive_root = Settings.ArchiveRoot,
                include_other_seasons = false,
                limit = 200
            }, cancellationToken);
            var analysisTask = CallBackendAsync("analyze_iracing_race", new
            {
                selector = race.EffectiveSelector,
                iracing_root = Settings.IRacingRoot,
                archive_root = Settings.ArchiveRoot,
                target_hz = 20
            }, cancellationToken);
            await Task.WhenAll(historyTask, analysisTask);
            var result = await historyTask;
            StrategyScenarios.Clear();
            StrategyScenarios.AddRange(RuntimeMapper.Strategy(result));
            PlanBriefing = RuntimeMapper.Plan(await analysisTask, PlanDistanceValue, PlanDistanceMode);
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
            PlanMessage = Bound(ex.Message);
            Notify("The plan could not be built from the available history.");
        }
        RaiseChanged();
    }

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

        try
        {
            var result = await CallBackendAsync("recommend_open_setup_tuning", new
            {
                analysis_path = race.AnalysisPath,
                archive_root = Settings.ArchiveRoot,
                symptoms = symptom,
                maximum_changes = 1
            }, cancellationToken);
            TuningExperiment = RuntimeMapper.Tuning(result);
            PersistPortableArtifact("tuning-experiments", $"experiment-{TuningExperiment.ExperimentId}", new
            {
                schemaVersion = 1,
                raceId = race.Id,
                createdUtc = DateTimeOffset.UtcNow,
                symptom,
                result
            });
            ExperimentGenerated = true;
            TuningMessage = "The recommendation is tied to this race's embedded setup and telemetry.";
            Notify("One controlled setup test is ready.");
        }
        catch (Exception ex) when (ex is BackendProtocolException or BackendDomainException or InvalidDataException)
        {
            TuningMessage = Bound(ex.Message);
            Notify("A safe tuning change could not be recommended from this race.");
        }
        RaiseChanged();
    }

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
            TuningPriority);
        if (!TuningFeedback.Contains(draft)) TuningFeedback.Add(draft);
        RaiseChanged();
    }

    public void RemoveTuningFeedback(TuningFeedbackDraft draft)
    {
        TuningFeedback.Remove(draft);
        RaiseChanged();
    }

    public async Task SelectTuningRaceAsync(string? raceId, CancellationToken cancellationToken = default)
    {
        SelectedTuningRaceId = raceId?.Trim() ?? string.Empty;
        TuningCorner = "Whole lap";
        TuningFeedback.Clear();
        var race = SelectedTuningRace;
        if (race is null)
        {
            RaiseChanged();
            return;
        }
        if (CurrentAnalysis is not null &&
            string.Equals(CurrentAnalysis.Track, race.Track, StringComparison.OrdinalIgnoreCase) &&
            string.Equals(CurrentAnalysis.Car, race.Car, StringComparison.OrdinalIgnoreCase))
        {
            RaiseChanged();
            return;
        }

        TuningMessage = "Loading this race's track and telemetry…";
        RaiseChanged();
        if (TryLoadUiAnalysisCache(race))
        {
            TuningMessage = "Click a recorded corner, then describe what the car did.";
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
            CurrentRaceCard = RuntimeMapper.RaceCard(result);
            CurrentAnalysis = RuntimeMapper.Analysis(result);
            SaveUiAnalysisCache(race, result);
            TuningMessage = "Click a recorded corner, then describe what the car did.";
        }
        catch (Exception ex) when (ex is BackendProtocolException or BackendDomainException or InvalidDataException)
        {
            TuningMessage = Bound(ex.Message);
        }
        RaiseChanged();
    }

    private string BuildTuningSymptom()
    {
        if (TuningFeedback.Count > 0)
        {
            return string.Join(" ", TuningFeedback.Select((item, index) =>
                $"Issue {index + 1}: {item.Severity} {item.Balance.ToLowerInvariant()} at {item.CornerPhase.ToLowerInvariant()} in {item.Corner} during the {item.RunPhase.ToLowerInvariant()}. Driver confidence: {item.Confidence.ToLowerInvariant()}." +
                (item.Priority ? " This is the driver's highest-priority issue." : string.Empty)));
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

    public async Task RecordOutcomeAsync(string outcome, CancellationToken cancellationToken = default)
    {
        if (TuningExperiment is null) return;
        try
        {
            _ = await CallBackendAsync("record_open_setup_feedback", new
            {
                experiment_id = TuningExperiment.ExperimentId,
                outcome,
                notes = FeedbackNotes.Trim(),
                archive_root = Settings.ArchiveRoot
            }, cancellationToken);
            TuningExperiment = TuningExperiment with { Outcome = Humanize(outcome) };
            Notify("Outcome saved with the experiment.");
        }
        catch (Exception ex) when (ex is BackendProtocolException or BackendDomainException)
        {
            Notify($"The outcome was not saved: {Bound(ex.Message)}");
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
                ? "A byte-identical copy and its source record were saved in your portable setups folder."
                : "That exact setup is already in your portable setups folder.";
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
        StartingTuneBusy = true;
        SetupMessage = "Reviewing your local setups and recorded context…";
        RaiseChanged();
        try
        {
            var response = await CallBackendAsync("build_open_setup_package", new
            {
                iracing_root = Settings.IRacingRoot,
                archive_root = Settings.ArchiveRoot,
                season = StartingTuneSeason.Trim(),
                car = StartingTuneCar.Trim(),
                track = StartingTuneTrack.Trim()
            }, cancellationToken);
            StartingTunePackage = RuntimeMapper.SetupPackage(response, StartingTuneCar.Trim(), StartingTuneTrack.Trim(), StartingTuneSeason.Trim(), StartingTunePurpose);
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
        StartingTunePackage = null;
        StartingTuneStep = 1;
        SetupMessage = "Enter an open-setup event context to build a new starting tune.";
        RaiseChanged();
    }

    public void SaveSettings()
    {
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
            _settingsStore?.Save(Settings);
            SettingsMessage = "Portable preferences were saved. Account connections stay protected on this Windows user.";
            Toast = "Settings saved.";
            SettingsSaved?.Invoke(Settings);
            _ = RefreshDataAsync(false, CancellationToken.None);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or InvalidOperationException or ArgumentException or TimeoutException)
        {
            SettingsMessage = "Windows could not save the settings file. Check access to the Coach repository.";
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
            SettingsMessage = "iRacing data was found and the Coach archive is ready.";
        }
        else
        {
            SettingsMessage = "iRacing data was not found automatically. Correct the iRacing Documents folder in Settings.";
        }
        RaiseChanged();
    }

    public async Task ConnectChatGptAsync(bool deviceCode = false, CancellationToken cancellationToken = default)
    {
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
        try
        {
            _garage61Credentials.Store(Garage61KeyInput);
            Garage61KeyInput = string.Empty;
            Settings.Garage61ApiKey = string.Empty;
            _settingsStore?.Save(Settings);
            await RefreshDataAsync(false, cancellationToken);
            SetupStep = Math.Max(SetupStep, 4);
            Toast = "Garage61 is connected for this Windows user.";
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or InvalidOperationException or ArgumentException or TimeoutException)
        {
            Toast = $"Garage61 could not be connected: {Bound(ex.Message)}";
        }
        RaiseChanged();
    }

    public async Task DisconnectGarage61Async(CancellationToken cancellationToken = default)
    {
        try
        {
            _garage61Credentials.Remove();
            await RefreshDataAsync(false, cancellationToken);
            Toast = "Garage61 was disconnected from this PC.";
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
        {
            Toast = $"Garage61 could not be disconnected: {Bound(ex.Message)}";
        }
        RaiseChanged();
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
            var evidence = BuildCoachEvidence();
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
        _settingsStore?.Save(Settings);
        CurrentPage = "home";
        Toast = "iRacing Coach is ready.";
        RaiseChanged();
    }

    public void RepairInstallation()
    {
        var setup = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "iRacingCoach",
            "Installer",
            "iRacingCoach-0.12.0-Setup.exe");
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
        Settings.LaunchAtSignIn = false;
        Settings.UseReducedMotion = false;
        Settings.DiagnosticIncludeConfounded = false;
        var liveDefaults = new LiveMonitorLayout();
        Settings.LiveMonitor.Visible = liveDefaults.Visible;
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
        LiveMonitorVisibilityRequested?.Invoke(false);
        SettingsMessage = "App preferences were restored. Protected account connections and racing history were kept.";
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
        if (visible) RaiseChanged();
    }
    public void ToggleLiveMonitor() => SetLiveMonitorVisible(!Settings.LiveMonitor.Visible);
    public void SetLiveMonitorVisible(bool visible, bool requestHost = true)
    {
        Settings.LiveMonitor.Visible = visible;
        PersistSettingsQuietly();
        if (requestHost) LiveMonitorVisibilityRequested?.Invoke(visible);
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
                Toast = "Your portable Coach folder is ready to copy.";
            }
            else
            {
                SettingsMessage = BackupPreparation.Message;
                Toast = "The portable folder is still active. Finish the listed work and try again.";
            }
            Diagnostics = BuildDiagnostics(_lastBackendHealth);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or InvalidDataException)
        {
            SettingsMessage = $"The backup check could not finish: {Bound(ex.Message)}";
            Toast = "The portable folder is not ready to copy.";
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
    public Task RetryJobAsync(JobItem job) { Notify("Choose the action again from its page."); return Task.CompletedTask; }
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
            Toast = "The telemetry source was identified. Prepare the archive again to refresh missing-source status.";
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
        SettingsMessage = Archive.Message + " Account connections stay protected on this PC.";
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
        if (SelectedTuningRaceId.Length == 0 || TuningRaces.All(race => race.Id != SelectedTuningRaceId))
            SelectedTuningRaceId = TuningRaces.FirstOrDefault()?.Id ?? string.Empty;
    }

    private void ApplyBrowserDefault()
    {
        if (SelectedRaceSessionId.Length > 0 && EventSessions.Any(session => session.Id == SelectedRaceSessionId)) return;
        SelectedRaceSessionId = EventGroups.SelectMany(group => group.Sessions).FirstOrDefault(session => session.IsRace)?.Id
            ?? EventSessions.FirstOrDefault()?.Id
            ?? string.Empty;
    }

    private void DiscoverCars()
    {
        var found = new Dictionary<string, InstalledCar>(StringComparer.OrdinalIgnoreCase);
        foreach (var race in Races)
        {
            var id = race.CarPath.Length > 0 ? race.CarPath : race.Car;
            found[id] = new InstalledCar(id, race.Car, race.CarPath, "Recorded race");
        }
        foreach (var setup in Setups)
        {
            if (!found.ContainsKey(setup.Car)) found[setup.Car] = new InstalledCar(setup.Car, setup.Car, setup.StoPath, "Local setup");
        }
        foreach (var root in InstalledCarRoots())
        {
            try
            {
                foreach (var directory in Directory.EnumerateDirectories(root))
                {
                    var id = Path.GetFileName(directory);
                    found.TryAdd(id, new InstalledCar(id, Humanize(id), directory, "Installed on this PC"));
                }
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
        }
        Cars.Clear();
        Cars.AddRange(found.Values.OrderBy(car => car.Name, StringComparer.CurrentCultureIgnoreCase));
    }

    private IEnumerable<string> InstalledCarRoots()
    {
        var programFiles = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
        var programFilesX86 = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86);
        var candidates = new[]
        {
            Path.Combine(Settings.IRacingInstallRoot, "cars"),
            Path.Combine(programFiles, "iRacing", "cars"),
            Path.Combine(programFilesX86, "iRacing", "cars"),
            Path.Combine(programFilesX86, "Steam", "steamapps", "common", "iRacing", "cars"),
            Path.Combine(programFiles, "Steam", "steamapps", "common", "iRacing", "cars")
        };
        return candidates.Where(Directory.Exists).Distinct(StringComparer.OrdinalIgnoreCase);
    }

    private void ConfigureWatchers()
    {
        foreach (var root in new[] { Settings.IRacingRoot }.Distinct(StringComparer.OrdinalIgnoreCase))
        {
            if (!Directory.Exists(root)) continue;
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
        _fileRefresh ??= new Timer(_ => _ = RefreshDataAsync(false, CancellationToken.None), null, Timeout.InfiniteTimeSpan, Timeout.InfiniteTimeSpan);
        _fileRefresh.Change(TimeSpan.FromSeconds(2), Timeout.InfiniteTimeSpan);
    }

    private IReadOnlyList<DiagnosticFact> BuildDiagnostics(BackendHealthResult health) =>
    [
        new("App", $"v{AppVersion} · Windows x64", "ready"),
        new("Race analysis service", health.Ok ? $"Ready · {BackendVersionLabel(health.ServerVersion)} · {health.ToolCount} tools" : health.Error ?? "Unavailable", health.Ok ? "ready" : "warning"),
        new("Contract compatibility", health.Ok && health.ToolCount == 16 ? "Compatible · MCP v1" : $"Expected 16 tools; found {health.ToolCount}", health.Ok && health.ToolCount == 16 ? "ready" : "warning"),
        new("Coach Engine", CoachEngine.Installed ? $"{CoachEngine.RuntimeVersion} · {(CoachEngine.Running ? "running" : "stopped")}" : CoachEngine.Message, CoachEngine.Installed ? "ready" : "warning"),
        new("ChatGPT", CoachEngine.ChatGptConnected ? "Connected" : "Not connected; deterministic features remain available", CoachEngine.ChatGptConnected ? "ready" : "neutral"),
        new("iRacing folder", Settings.IRacingRoot, Directory.Exists(Settings.IRacingRoot) ? "ready" : "warning"),
        new("iRacing installation", Settings.IRacingInstallRoot, Directory.Exists(Settings.IRacingInstallRoot) ? "ready" : "neutral"),
        new("Portable Coach repository", Settings.CoachHome, Directory.Exists(Settings.CoachHome) ? "ready" : "warning"),
        new("Archive schema", Archive is null ? "Not initialized" : $"v{Archive.SchemaVersion} · ID {Archive.ArchiveId[..Math.Min(8, Archive.ArchiveId.Length)]}", Archive?.Compatible == true ? "ready" : "warning"),
        new("Archive integrity", Archive?.LastIntegrityCheckUtc is null ? "Not checked yet; use Prepare Backup / Migration Copy" : Archive.IntegrityVerified == false ? "Portable files changed since the last prepared copy" : $"Verified · checked {Archive.LastIntegrityCheckUtc.Value.ToLocalTime():g}", Archive?.IntegrityVerified == false ? "warning" : Archive?.LastIntegrityCheckUtc is null ? "neutral" : "ready"),
        new("Restored portable data", Archive is null ? "Unavailable" : $"{Archive.Restored.TotalItems:N0} indexed items · {Archive.Restored.UnresolvedSources:N0} missing telemetry sources", Archive?.Restored.UnresolvedSources > 0 ? "neutral" : "ready"),
        new("Settings file", Settings.SettingsPath, File.Exists(Settings.SettingsPath) ? "ready" : "neutral"),
        new("Setup copies", Settings.SetupsRoot, "ready"),
        new("Garage61", Garage61.Available ? "Connected" : Garage61.Configured ? "Protected connection saved; retrying" : "Not connected", Garage61.Available ? "ready" : "neutral"),
        new("Live telemetry", LiveState.Snapshot.Connected ? $"Connected · {LiveState.Snapshot.Flag} · {LiveState.Snapshot.DataAge.TotalMilliseconds:0} ms old" : "Waiting for iRacing", LiveState.Snapshot.Connected ? "ready" : "neutral"),
        new("Live update pipeline", $"{LiveState.FramesRead:N0} frames · {LiveState.DroppedFrames:N0} dropped · {LiveState.RenderLatencyMs:0.00} ms compute", LiveState.DroppedFrames == 0 ? "ready" : "warning"),
        new("Live Monitor", Settings.LiveMonitor.Visible ? $"Visible · {LiveMonitorLayouts.Active(Settings.LiveMonitor).Layout.Name}" : "Hidden", "neutral"),
        new("Overlay compatibility", "Works above borderless-windowed iRacing and on another monitor; exclusive fullscreen may cover it", "neutral"),
        new("Automatic discovery", "Watching files · quiet 30-second safety check", "ready"),
        new("Finalized races found", Races.Count.ToString(CultureInfo.CurrentCulture)),
        new("Local setup files found", Setups.Count.ToString(CultureInfo.CurrentCulture)),
        new("Cars found on this PC", Cars.Count.ToString(CultureInfo.CurrentCulture))
    ];

    private static string BackendVersionLabel(string version) =>
        Version.TryParse(version.TrimStart('v', 'V'), out _) ? $"v{version.TrimStart('v', 'V')}" : version;

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
        return new BackendConfiguration("powershell.exe", launcher, Settings.PythonPath, Settings.IRacingRoot, Settings.ArchiveRoot, Settings.CoachHome, Settings.IRacingInstallRoot);
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

    private string BuildCoachEvidence()
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
            activeCapabilities = CapabilityRegistry.ActiveForAi(CapabilityContext)
                .Select(item => new { id = item.Definition.Id.ToString(), item.Definition.Name, item.Definition.UserValue }),
            setupChangesAllowed = selected?.SetupType.Equals("Open", StringComparison.OrdinalIgnoreCase) == true,
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

    private bool TryLoadUiAnalysisCache(RecentRace race)
    {
        var path = UiAnalysisCachePath(race);
        if (!File.Exists(path)) return TryLoadArchivedAnalysis(race);
        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(path));
            var root = document.RootElement;
            if (!root.TryGetProperty("schemaVersion", out var schema)
                || schema.ValueKind != JsonValueKind.Number
                || !schema.TryGetInt32(out var cacheSchema)
                || cacheSchema != UiAnalysisCacheSchemaVersion) return false;
            if (!root.TryGetProperty("response", out var response) || response.ValueKind != JsonValueKind.Object) return false;
            var cachedSourceWrite = root.TryGetProperty("sourceLastWriteUtc", out var stamp) && stamp.ValueKind == JsonValueKind.String
                ? stamp.GetString() : null;
            if (!string.IsNullOrWhiteSpace(race.SourcePath) && File.Exists(race.SourcePath))
            {
                var current = File.GetLastWriteTimeUtc(race.SourcePath).ToString("O", CultureInfo.InvariantCulture);
                if (!string.Equals(current, cachedSourceWrite, StringComparison.Ordinal)) return false;
            }
            CurrentRaceCard = RuntimeMapper.RaceCard(response);
            CurrentAnalysis = RuntimeMapper.Analysis(response);
            UpdateRaceOverview(race, CurrentAnalysis);
            AnalysisLoading = false;
            AnalysisMessage = string.Empty;
            ApiCacheHitCount++;
            return true;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException or InvalidDataException or InvalidOperationException)
        {
            ReportUnhandledException("saved analysis cache", ex);
            return false;
        }
    }

    private bool TryLoadArchivedAnalysis(RecentRace race)
    {
        if (string.IsNullOrWhiteSpace(race.AnalysisPath) || !File.Exists(race.AnalysisPath)) return false;
        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(race.AnalysisPath));
            CurrentAnalysis = RuntimeMapper.ArchivedAnalysis(document.RootElement);
            CurrentRaceCard = RuntimeMapper.ArchivedRaceCard(document.RootElement);
            UpdateRaceOverview(race, CurrentAnalysis);
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
        if (string.IsNullOrWhiteSpace(race.AnalysisPath) || !File.Exists(race.AnalysisPath)) return race;
        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(race.AnalysisPath));
            return race with { Overview = RuntimeMapper.Overview(document.RootElement) };
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException or InvalidDataException or InvalidOperationException)
        {
            ReportUnhandledException("race overview", ex);
            return race;
        }
    }

    private void UpdateRaceOverview(RecentRace race, AnalysisWorkspace analysis)
    {
        var cleanTimes = analysis.Traces.Where(trace => trace.Complete && trace.PitTimeSeconds.GetValueOrDefault() <= 0
                && !trace.FlagState.Contains("yellow", StringComparison.OrdinalIgnoreCase)
                && !trace.FlagState.Contains("caution", StringComparison.OrdinalIgnoreCase)
                && trace.LapTimeSeconds is > 0)
            .Select(trace => trace.LapTimeSeconds!.Value).ToArray();
        double? consistency = null;
        if (cleanTimes.Length >= 2)
        {
            var mean = cleanTimes.Average();
            if (mean > 0) consistency = Math.Sqrt(cleanTimes.Sum(value => Math.Pow(value - mean, 2)) / cleanTimes.Length) / mean * 100;
        }
        var longest = analysis.Runs.OrderByDescending(run => run.GreenLaps).FirstOrDefault();
        var tire = analysis.Runs.Where(run => run.TireRemainingPercent.HasValue).OrderBy(run => run.TireRemainingPercent).FirstOrDefault();
        var controlChange = analysis.Runs.SelectMany(run => new[] { run.EarlyBrakeVsLatePercent, run.EarlySteerVsLatePercent })
            .Where(value => value.HasValue).Select(value => Math.Abs(value!.Value)).DefaultIfEmpty().Max();
        var overview = new RaceOverview(
            analysis.RecordedLaps, analysis.Runs.Sum(run => run.GreenLaps), analysis.Runs.Sum(run => run.CautionLaps),
            analysis.PitStops, analysis.Runs.Count, longest?.GreenLaps ?? 0, longest?.PaceSlopeSecondsPerLap,
            consistency, tire?.TireRemainingPercent, tire?.TireName ?? string.Empty, controlChange > 0 ? controlChange : null,
            analysis.Runs.Where(run => run.FuelUsedGallons.HasValue).Sum(run => run.FuelUsedGallons), cleanTimes.Length > 0 ? cleanTimes.Min() : null);
        for (var index = 0; index < Races.Count; index++)
            if (SameRace(Races[index], race)) Races[index] = Races[index] with { Overview = overview };
        for (var index = 0; index < EventSessions.Count; index++)
            if (SameRace(EventSessions[index], race)) EventSessions[index] = EventSessions[index] with { Overview = overview };
    }

    private static bool SameRace(RecentRace candidate, RecentRace selected) =>
        string.Equals(candidate.Id, selected.Id, StringComparison.OrdinalIgnoreCase) ||
        (!string.IsNullOrWhiteSpace(candidate.EventKey) && string.Equals(candidate.EventKey, selected.EventKey, StringComparison.OrdinalIgnoreCase)) ||
        (!string.IsNullOrWhiteSpace(candidate.EffectiveSelector) && string.Equals(candidate.EffectiveSelector, selected.EffectiveSelector, StringComparison.OrdinalIgnoreCase));

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
            sourceLastWriteUtc = !string.IsNullOrWhiteSpace(race.SourcePath) && File.Exists(race.SourcePath)
                ? File.GetLastWriteTimeUtc(race.SourcePath).ToString("O", CultureInfo.InvariantCulture)
                : null,
            savedUtc = DateTimeOffset.UtcNow,
            response
        });
    }

    private string UiAnalysisCachePath(RecentRace race) =>
        Path.Combine(Settings.ArchiveRoot, "ui-analysis-cache", UiAnalysisCacheKey(race) + ".json");

    private static string UiAnalysisCacheKey(RecentRace race) =>
        Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(race.EffectiveSelector))).ToLowerInvariant();

    private void PersistPortableArtifact(string component, string name, object value)
    {
        try
        {
            _archive.MarkActive(Settings.CoachHome);
            var directory = Path.GetFullPath(Path.Combine(Settings.ArchiveRoot, component));
            var archiveRoot = Path.GetFullPath(Settings.ArchiveRoot).TrimEnd(Path.DirectorySeparatorChar);
            if (!directory.StartsWith(archiveRoot + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("The portable artifact path escaped the Coach archive.");
            Directory.CreateDirectory(directory);
            var safeName = string.Concat(name.Select(character => Path.GetInvalidFileNameChars().Contains(character) || character is ':' or '/' or '\\' ? '-' : character));
            var path = Path.Combine(directory, safeName + ".json");
            var temporary = path + $".{Guid.NewGuid():N}.tmp";
            File.WriteAllText(temporary, JsonSerializer.Serialize(value, new JsonSerializerOptions(JsonSerializerDefaults.Web) { WriteIndented = true }));
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
            }, new JsonSerializerOptions(JsonSerializerDefaults.Web) { WriteIndented = true }));
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
        if (state.Snapshot.Connected && Settings.LiveMonitor.ReopenOnConnect && !Settings.LiveMonitor.Visible)
        {
            Settings.LiveMonitor.Visible = true;
            LiveMonitorVisibilityRequested?.Invoke(true);
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

    private void PersistSettingsQuietly()
    {
        try
        {
            _archive.MarkActive(Settings.CoachHome);
            _settingsStore?.Save(Settings);
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
        _fileRefresh?.Dispose();
        foreach (var watcher in _watchers) watcher.Dispose();
        foreach (var token in _jobTokens.Values) { token.Cancel(); token.Dispose(); }
        _coachRequest?.Cancel();
        _coachRequest?.Dispose();
        _liveTelemetry.Updated -= OnLiveTelemetryUpdated;
        _liveTelemetry.FrameCaptured -= OnLiveTelemetryFrameCaptured;
        _liveTelemetry.Dispose();
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
        _refreshGate.Dispose();
    }
}
