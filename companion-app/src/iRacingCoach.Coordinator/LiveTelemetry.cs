using System.Diagnostics;
using System.Runtime.InteropServices;
using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

public interface ILiveTelemetrySource : IDisposable
{
    bool TryRead(out LiveTelemetrySample sample);
}

public sealed record LiveReplayChannelCoverage(string Channel, bool Recorded, string? UnavailableReason);

public sealed record LiveReplayParticipant(
    int CarIndex,
    string? CarNumber,
    int? ClassId,
    string? ClassName,
    string? CarName,
    string? DriverName,
    string? TeamName,
    bool? IsSpectator);

public sealed record LiveReplayCarSample(
    int CarIndex,
    double? LapDistancePercent,
    int? Lap,
    int? CompletedLaps,
    int? OverallPosition,
    int? ClassPosition,
    bool? OnPitRoad,
    int? TrackSurface,
    int? PaceFlags,
    double? LastLapSeconds,
    double? BestLapSeconds);

public sealed record LiveReplayCaptureFrame(
    string SessionKey,
    DateTimeOffset CapturedAt,
    double? SessionTimeSeconds,
    int? SessionState,
    long? SessionFlags,
    long? SessionUniqueId,
    long? SubsessionId,
    int? SessionNumber,
    string? SessionType,
    int? PlayerCarIndex,
    IReadOnlyList<LiveReplayChannelCoverage> Coverage,
    IReadOnlyList<LiveReplayParticipant> Participants,
    IReadOnlyList<LiveReplayCarSample> Cars);

public sealed record LiveTelemetrySample
{
    public bool Connected { get; init; }
    public DateTimeOffset Timestamp { get; init; } = DateTimeOffset.UtcNow;
    public int Tick { get; init; }
    public int TickRate { get; init; }
    public string Flag { get; init; } = "Waiting";
    public bool UnderCaution { get; init; }
    public bool BlackFlag { get; init; }
    public bool RepairFlag { get; init; }
    public bool Towing { get; init; }
    public int? Lap { get; init; }
    public int? LapsRemaining { get; init; }
    public int? OverallPosition { get; init; }
    public int? ClassPosition { get; init; }
    public double? GapToLeaderSeconds { get; init; }
    public double? GapToClassLeaderSeconds { get; init; }
    public double? GapToAheadSeconds { get; init; }
    public double? GapToBehindSeconds { get; init; }
    public string LeaderGapUnavailableReason { get; init; } = "Live scoring interval unavailable.";
    public string AheadGapUnavailableReason { get; init; } = "No same-lap class car ahead.";
    public string BehindGapUnavailableReason { get; init; } = "No same-lap class car behind.";
    public double? LastLapSeconds { get; init; }
    public double? LeaderLastLapSeconds { get; init; }
    public double? FuelLiters { get; init; }
    public double? FuelLevelPercent { get; init; }
    public double? TrackTemperatureC { get; init; }
    public double? AirTemperatureC { get; init; }
    public double? BrakeBiasPercent { get; init; }
    public bool OnPitRoad { get; init; }
    public double? MandatoryRepairSeconds { get; init; }
    public double? OptionalRepairSeconds { get; init; }
    public double? SteeringWheelAngleRadians { get; init; }
    public double? Throttle { get; init; }
    public double? Brake { get; init; }
    public bool? BrakeAbsActive { get; init; }
    public double? BrakeAbsCutPercent { get; init; }
    public int? Gear { get; init; }
    public double? Rpm { get; init; }
    public double? YawRateRadiansPerSecond { get; init; }
    public double? LateralAccelerationG { get; init; }
    public double? LongitudinalAccelerationG { get; init; }
    public double? SpeedMetersPerSecond { get; init; }
    public double? LapDistancePercent { get; init; }
    public double? Latitude { get; init; }
    public double? Longitude { get; init; }
    public double? SessionTimeSeconds { get; init; }
    public int? SessionState { get; init; }
    public long? SessionFlags { get; init; }
    public long? SessionUniqueId { get; init; }
    public long? SubsessionId { get; init; }
    public int? SessionNumber { get; init; }
    public string? SessionType { get; init; }
    public int? PlayerCarIndex { get; init; }
    public IReadOnlyList<LiveReplayChannelCoverage> ReplayCoverage { get; init; } = [];
    public IReadOnlyList<LiveReplayParticipant> ReplayParticipants { get; init; } = [];
    public IReadOnlyList<LiveReplayCarSample> ReplayCars { get; init; } = [];
    public string Source { get; init; } = "Local iRacing SDK shared memory";
}

public sealed class LiveTelemetryService : IDisposable
{
    // The Windows thread-pool timer is typically quantized too coarsely to
    // observe a 240 Hz SDK stream reliably. A dedicated worker blocks on a
    // high-resolution waitable timer while connected and drops to a cheap
    // discovery cadence while iRacing is not running.
    private const int ConnectedPollIntervalMilliseconds = 2;
    private const int DisconnectedPollIntervalMilliseconds = 40;
    private static readonly TimeSpan HistorySnapshotInterval = TimeSpan.FromMilliseconds(100);
    private const int MaximumHistoryPoints = 36_000;
    private readonly ILiveTelemetrySource _source;
    private readonly LiveTelemetryEngine _engine = new();
    private readonly object _gate = new();
    private readonly object _lifecycleGate = new();
    private Thread? _pollThread;
    private bool _disposed;
    private long _framesRead;
    private long _droppedFrames;
    private readonly Queue<LiveTracePoint> _history = new();
    private IReadOnlyList<LiveTracePoint> _historySnapshot = [];
    private int _busy;
    private DateTimeOffset _lastPublish = DateTimeOffset.MinValue;
    private DateTimeOffset _lastHistorySnapshot = DateTimeOffset.MinValue;
    private long _sessionEpoch;
    private int? _lastConnectedLap;
    private DateTimeOffset? _lastConnectedTimestamp;
    private int? _lastSourceTick;
    private DateTimeOffset _replaySessionStartedAt = DateTimeOffset.MinValue;
    private DateTimeOffset _lastReplayCaptureAt = DateTimeOffset.MinValue;

    public LiveTelemetryService(ILiveTelemetrySource source, LiveMonitorLayout layout)
    {
        _source = source;
        Current = new LiveMonitorState(LiveTelemetryEngine.Disconnected(), layout, false, 0, 0, 0, DateTimeOffset.UtcNow);
    }

    public event Action<LiveMonitorState>? Updated;
    public event Action<LiveTracePoint>? FrameCaptured;
    public event Action<LiveReplayCaptureFrame>? ReplayFrameCaptured;
    public event Action<string>? ReplaySessionEnded;
    public LiveMonitorState Current { get; private set; }
    public bool CoachingPaused { get; private set; }

    public void Start()
    {
        lock (_lifecycleGate)
        {
            if (_disposed || _pollThread is not null) return;
            _pollThread = new Thread(PollLoop)
            {
                IsBackground = true,
                Name = "iRacing Coach telemetry reader"
            };
            _pollThread.Start();
        }
    }

    public void SetCoachingPaused(bool paused)
    {
        CoachingPaused = paused;
        lock (_gate)
        {
            Current = Current with { CoachingPaused = paused, UpdatedAt = DateTimeOffset.UtcNow };
        }
        Updated?.Invoke(Current);
    }

    private void PollLoop()
    {
        var timer = NativeWaitableTimer.TryCreate();
        try
        {
            var interval = DisconnectedPollIntervalMilliseconds;
            while (!Volatile.Read(ref _disposed))
            {
                if (timer != IntPtr.Zero)
                {
                    if (!NativeWaitableTimer.Wait(timer, interval))
                    {
                        NativeWaitableTimer.Close(timer);
                        timer = IntPtr.Zero;
                        continue;
                    }
                }
                else
                {
                    Thread.Sleep(interval);
                }

                if (Volatile.Read(ref _disposed)) break;
                Poll();
                interval = Current.Snapshot.Connected
                    ? ConnectedPollIntervalMilliseconds
                    : DisconnectedPollIntervalMilliseconds;
            }
        }
        finally
        {
            if (timer != IntPtr.Zero) NativeWaitableTimer.Close(timer);
        }
    }

    private void Poll()
    {
        if (Interlocked.Exchange(ref _busy, 1) != 0)
        {
            return;
        }

        try
        {
            var started = Stopwatch.GetTimestamp();
            if (_source.TryRead(out var sample))
            {
                var sessionChanged = ObserveSessionBoundary(sample);
                if (sessionChanged)
                {
                    if (Current.Snapshot.Connected) ReplaySessionEnded?.Invoke("session_changed");
                    _engine.ResetSession();
                    _history.Clear();
                    _historySnapshot = [];
                    _lastHistorySnapshot = sample.Timestamp;
                    _replaySessionStartedAt = sample.Timestamp;
                    _lastReplayCaptureAt = DateTimeOffset.MinValue;
                }
                ObserveSourceTicks(sample, sessionChanged);
                var snapshot = _engine.Update(sample, Current.Layout.SafeGlanceEnabled, CoachingPaused);
                var latency = Stopwatch.GetElapsedTime(started).TotalMilliseconds;
                LiveTracePoint? tracePoint = null;
                if (sample.Connected)
                {
                    double? paceMidpoint = snapshot.PaceTarget.MinimumSeconds.HasValue && snapshot.PaceTarget.MaximumSeconds.HasValue
                        ? (snapshot.PaceTarget.MinimumSeconds.Value + snapshot.PaceTarget.MaximumSeconds.Value) / 2
                        : null;
                    var historyMetrics = new LiveMetricHistoryFrame(
                        snapshot.AirTemperatureC,
                        snapshot.AheadGap.Seconds,
                        snapshot.BehindGap.Seconds,
                        snapshot.BrakeBiasPercent,
                        snapshot.ClassPosition,
                        snapshot.PrimaryCue.Priority,
                        LiveTelemetryMetricEncoding.Flag(snapshot.Flag),
                        snapshot.FuelLiters,
                        snapshot.FuelLapsRemaining,
                        snapshot.LapsRemaining,
                        snapshot.OverallPosition == 1 ? 0 : snapshot.LeaderGap.Seconds,
                        snapshot.LeaderLastLapSeconds,
                        snapshot.MandatoryRepairSeconds,
                        snapshot.OnPitRoad,
                        snapshot.OptionalRepairSeconds,
                        paceMidpoint,
                        snapshot.Pit.WindowOpensInLaps ?? snapshot.Pit.FuelHardLimitLaps,
                        snapshot.OverallPosition,
                        LiveTelemetryMetricEncoding.TirePhase(snapshot.TirePhase),
                        snapshot.TrackTemperatureC,
                        sample.BrakeAbsActive,
                        sample.BrakeAbsCutPercent);
                    tracePoint = new LiveTracePoint(
                        sample.Timestamp, sample.Lap, sample.LapDistancePercent,
                        sample.SpeedMetersPerSecond * 2.2369362920544, sample.Throttle, sample.Brake,
                        sample.SteeringWheelAngleRadians, sample.Gear, sample.Rpm,
                        sample.YawRateRadiansPerSecond * 57.29577951308232, sample.LateralAccelerationG,
                        sample.LongitudinalAccelerationG, sample.Latitude, sample.Longitude, sample.LastLapSeconds,
                        historyMetrics);
                    _history.Enqueue(tracePoint);
                    while (_history.Count > MaximumHistoryPoints ||
                           (_history.Count > 1 && tracePoint.At - _history.Peek().At > TimeSpan.FromMinutes(10)))
                        _history.Dequeue();
                }
                else if (Current.Snapshot.Connected)
                {
                    _history.Clear();
                    _historySnapshot = [];
                    _lastHistorySnapshot = sample.Timestamp;
                }
                if (sample.Timestamp - _lastHistorySnapshot >= HistorySnapshotInterval || _historySnapshot.Count == 0)
                {
                    _historySnapshot = _history.ToArray();
                    _lastHistorySnapshot = sample.Timestamp;
                }
                lock (_gate)
                {
                    Current = new LiveMonitorState(snapshot, Current.Layout, CoachingPaused, ++_framesRead, _droppedFrames, latency, DateTimeOffset.UtcNow, _historySnapshot, sample.TickRate, _sessionEpoch);
                }
                _lastPublish = DateTimeOffset.UtcNow;
                if (tracePoint is not null) FrameCaptured?.Invoke(tracePoint);
                CaptureReplayFrame(sample, sessionChanged);
                Updated?.Invoke(Current);
            }
            else if (DateTimeOffset.UtcNow - _lastPublish > TimeSpan.FromSeconds(1))
            {
                var snapshot = Current.Snapshot;
                if (snapshot.Connected && DateTimeOffset.UtcNow - snapshot.SourceTimestamp > TimeSpan.FromSeconds(2))
                {
                    MarkDisconnectedBoundary();
                    snapshot = LiveTelemetryEngine.Disconnected("Live telemetry became stale.");
                }
                lock (_gate)
                {
                    Current = Current with { Snapshot = snapshot, DroppedFrames = _droppedFrames, UpdatedAt = DateTimeOffset.UtcNow, SessionEpoch = _sessionEpoch };
                }
                _lastPublish = DateTimeOffset.UtcNow;
                Updated?.Invoke(Current);
            }
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or InvalidDataException or ArgumentException)
        {
            MarkDisconnectedBoundary();
            lock (_gate)
            {
                Current = Current with { Snapshot = LiveTelemetryEngine.Disconnected("The local iRacing telemetry stream could not be read."), UpdatedAt = DateTimeOffset.UtcNow, SessionEpoch = _sessionEpoch };
            }
            Updated?.Invoke(Current);
        }
        finally
        {
            Volatile.Write(ref _busy, 0);
        }
    }

    private bool ObserveSessionBoundary(LiveTelemetrySample sample)
    {
        if (!sample.Connected)
        {
            MarkDisconnectedBoundary();
            return false;
        }

        var sessionChanged = !Current.Snapshot.Connected ||
            (_lastConnectedTimestamp.HasValue && sample.Timestamp < _lastConnectedTimestamp.Value) ||
            (_lastConnectedLap.HasValue && sample.Lap.HasValue && sample.Lap.Value < _lastConnectedLap.Value);
        if (sessionChanged) _sessionEpoch++;
        _lastConnectedTimestamp = sample.Timestamp;
        if (sample.Lap.HasValue) _lastConnectedLap = sample.Lap;
        return sessionChanged;
    }

    private void ObserveSourceTicks(LiveTelemetrySample sample, bool sessionChanged)
    {
        if (!sample.Connected)
        {
            _lastSourceTick = null;
            return;
        }
        if (sessionChanged || !_lastSourceTick.HasValue)
        {
            _lastSourceTick = sample.Tick;
            return;
        }

        // Tick is a signed SDK counter that can eventually wrap. Interpreting
        // the delta as unsigned handles a normal wrap while rejecting a large
        // backwards reset as a new baseline rather than billions of losses.
        var delta = unchecked((uint)(sample.Tick - _lastSourceTick.Value));
        if (delta is > 1 and < 0x80000000u)
            _droppedFrames += delta - 1;
        _lastSourceTick = sample.Tick;
    }

    private void MarkDisconnectedBoundary()
    {
        if (Current.Snapshot.Connected)
        {
            _sessionEpoch++;
            ReplaySessionEnded?.Invoke("disconnected");
        }
        _lastConnectedTimestamp = null;
        _lastConnectedLap = null;
        _lastSourceTick = null;
    }

    private void CaptureReplayFrame(LiveTelemetrySample sample, bool sessionChanged)
    {
        if (!sample.Connected) return;
        if (!sessionChanged && sample.Timestamp - _lastReplayCaptureAt < TimeSpan.FromMilliseconds(500)) return;
        if (_replaySessionStartedAt == DateTimeOffset.MinValue) _replaySessionStartedAt = sample.Timestamp;
        _lastReplayCaptureAt = sample.Timestamp;
        var normalizedSessionType = string.Concat((sample.SessionType ?? "unknown").Where(char.IsLetterOrDigit)).ToLowerInvariant();
        var identity = $"sub-{sample.SubsessionId?.ToString() ?? "unknown"}-sid-{sample.SessionUniqueId?.ToString() ?? "unknown"}-num-{sample.SessionNumber?.ToString() ?? "unknown"}-type-{normalizedSessionType}";
        var sessionKey = $"{identity}-epoch-{_sessionEpoch}-{_replaySessionStartedAt:yyyyMMddHHmmssfff}";
        ReplayFrameCaptured?.Invoke(new LiveReplayCaptureFrame(
            sessionKey,
            sample.Timestamp,
            sample.SessionTimeSeconds,
            sample.SessionState,
            sample.SessionFlags,
            sample.SessionUniqueId,
            sample.SubsessionId,
            sample.SessionNumber,
            sample.SessionType,
            sample.PlayerCarIndex,
            sample.ReplayCoverage,
            sample.ReplayParticipants,
            sample.ReplayCars));
    }

    public void Dispose()
    {
        Thread? pollThread;
        lock (_lifecycleGate)
        {
            if (_disposed) return;
            Volatile.Write(ref _disposed, true);
            pollThread = _pollThread;
            _pollThread = null;
        }
        if (pollThread is not null && pollThread != Thread.CurrentThread)
            pollThread.Join(TimeSpan.FromMilliseconds(250));
        ReplaySessionEnded?.Invoke("disposed");
        _source.Dispose();
    }

    private static class NativeWaitableTimer
    {
        private const uint CreateWaitableTimerHighResolution = 0x00000002;
        private const uint TimerModifyState = 0x0002;
        private const uint Synchronize = 0x00100000;
        private const uint WaitObject0 = 0;

        public static IntPtr TryCreate()
        {
            if (!OperatingSystem.IsWindows()) return IntPtr.Zero;
            var timer = CreateWaitableTimerEx(IntPtr.Zero, null, CreateWaitableTimerHighResolution, TimerModifyState | Synchronize);
            return timer != IntPtr.Zero
                ? timer
                : CreateWaitableTimerEx(IntPtr.Zero, null, 0, TimerModifyState | Synchronize);
        }

        public static bool Wait(IntPtr timer, int milliseconds)
        {
            var dueTime = -Math.Max(1, milliseconds) * 10_000L;
            return SetWaitableTimer(timer, ref dueTime, 0, IntPtr.Zero, IntPtr.Zero, false) &&
                   WaitForSingleObject(timer, (uint)(milliseconds + 100)) == WaitObject0;
        }

        public static void Close(IntPtr timer) => CloseHandle(timer);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern IntPtr CreateWaitableTimerEx(
            IntPtr timerAttributes,
            string? timerName,
            uint flags,
            uint desiredAccess);

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool SetWaitableTimer(
            IntPtr timer,
            ref long dueTime,
            int period,
            IntPtr completionRoutine,
            IntPtr argument,
            [MarshalAs(UnmanagedType.Bool)] bool resume);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);

        [DllImport("kernel32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool CloseHandle(IntPtr handle);
    }
}

public sealed class LiveTelemetryEngine
{
    private readonly Dictionary<string, Queue<(DateTimeOffset At, double Value)>> _gapHistory = new(StringComparer.Ordinal);
    private readonly Queue<double> _cleanLapTimes = new();
    private readonly Queue<double> _fuelPerLap = new();
    private int? _lastLap;
    private double? _fuelAtLapStart;
    private int _observedRunGreenLaps;
    private int _observedRunCautionLaps;
    private double? _initialTrackTemperature;
    private LiveDriverCue? _displayedCue;
    private readonly Dictionary<int, Queue<double>> _brakePeakBaseline = [];
    private readonly Dictionary<int, double> _currentLapBrakePeaks = [];
    private readonly Dictionary<int, int> _brakePeakStreaks = [];
    private bool _currentLapFullyObserved;
    private bool _currentLapSessionConfounded;
    private bool _currentLapInputConfounded;
    private bool _currentLapUnderCaution;
    private bool _currentLapWasOnPitRoad;
    private bool _wasOnPitRoad;
    private LiveDriverCue? _persistentDriverCue;
    private bool _wasConnected;

    public LiveRaceSnapshot Update(LiveTelemetrySample sample, bool safeGlanceEnabled, bool coachingPaused)
    {
        if (!sample.Connected)
        {
            if (_wasConnected) ResetSessionState();
            _wasConnected = false;
            return Disconnected();
        }
        _wasConnected = true;

        var lapAdvanced = _lastLap.HasValue && sample.Lap.HasValue && sample.Lap.Value > _lastLap.Value;
        var sequentialLap = lapAdvanced && sample.Lap!.Value == _lastLap!.Value + 1;
        if (lapAdvanced)
        {
            CompleteObservedLap(sample, sequentialLap);
            BeginObservedLap(sample, sequentialLap);
        }
        else if (!_lastLap.HasValue)
        {
            BeginObservedLap(sample, fullyObserved: false);
        }
        else if (sample.Lap.HasValue && sample.Lap.Value < _lastLap.Value)
        {
            ResetSessionState();
            _wasConnected = true;
            BeginObservedLap(sample, fullyObserved: false);
        }
        ObserveCurrentLap(sample);
        if (sample.Lap.HasValue) _lastLap = sample.Lap;
        _initialTrackTemperature ??= sample.TrackTemperatureC;

        var leader = BuildGap("leader", "Leader", sample.GapToLeaderSeconds, sample, sample.LeaderGapUnavailableReason);
        var classLeader = BuildGap("classLeader", "Class leader", sample.GapToClassLeaderSeconds, sample, sample.LeaderGapUnavailableReason);
        var ahead = BuildGap("ahead", "Ahead", sample.GapToAheadSeconds, sample, sample.AheadGapUnavailableReason);
        var behind = BuildGap("behind", "Behind", sample.GapToBehindSeconds, sample, sample.BehindGapUnavailableReason);
        var runPhase = _observedRunGreenLaps < 5 ? "Early run" : _observedRunGreenLaps < 20 ? "Middle run" : "Late run";
        var pace = BuildPaceTarget(sample, runPhase);
        var pit = BuildPit(sample);
        var measuredStraight = sample.Brake is { } brake && double.IsFinite(brake) && brake < 0.05 &&
            sample.SteeringWheelAngleRadians is { } steering && double.IsFinite(steering) && Math.Abs(steering) < 0.12 &&
            sample.LateralAccelerationG is { } lateralAcceleration && double.IsFinite(lateralAcceleration) && Math.Abs(lateralAcceleration) < 0.35;
        var glanceOpportunity = sample.UnderCaution || sample.OnPitRoad || lapAdvanced || measuredStraight;
        var candidate = BuildCue(sample, ahead, pit, pace, _persistentDriverCue, coachingPaused);
        var urgent = candidate.Priority >= LiveCuePriority.Strategy;
        var suppression = LiveCueSuppressionReason.None;
        if (coachingPaused)
        {
            suppression = LiveCueSuppressionReason.Paused;
            candidate = new LiveDriverCue("Live coaching paused", LiveCuePriority.Information, EvidenceKind.Measured, 1, sample.Timestamp, suppression, "User control");
        }
        else if (safeGlanceEnabled && !glanceOpportunity && !urgent && _displayedCue is not null)
        {
            suppression = LiveCueSuppressionReason.SafeGlanceDelay;
            candidate = _displayedCue with { SuppressionReason = suppression };
        }
        else
        {
            _displayedCue = candidate;
        }

        double? paceDelta = sample.LastLapSeconds.HasValue && sample.LeaderLastLapSeconds.HasValue
            ? sample.LastLapSeconds.Value - sample.LeaderLastLapSeconds.Value
            : null;
        var avgFuel = _fuelPerLap.Count >= 2 ? Median(_fuelPerLap) : null;
        var currentFuel = sample.FuelLiters is { } fuel && double.IsFinite(fuel) && fuel >= 0 ? fuel : (double?)null;
        double? fuelLaps = avgFuel.HasValue && currentFuel.HasValue ? Math.Max(0, currentFuel.Value / avgFuel.Value) : null;

        return new LiveRaceSnapshot(
            true, "Connected", sample.Flag, sample.Lap, sample.LapsRemaining, sample.OverallPosition, sample.ClassPosition,
            leader, classLeader, ahead, behind, sample.LastLapSeconds, sample.LeaderLastLapSeconds, paceDelta, pace, pit,
            _observedRunGreenLaps, _observedRunGreenLaps + _observedRunCautionLaps, _observedRunCautionLaps, runPhase, fuelLaps,
            sample.TrackTemperatureC,
            sample.TrackTemperatureC.HasValue && _initialTrackTemperature.HasValue ? sample.TrackTemperatureC.Value - _initialTrackTemperature.Value : null,
            sample.AirTemperatureC, sample.BrakeBiasPercent, sample.OnPitRoad, sample.MandatoryRepairSeconds, sample.OptionalRepairSeconds,
            sample.BlackFlag ? "Black flag" : "None recorded", candidate, null,
            new SafeGlanceState(safeGlanceEnabled, glanceOpportunity, urgent, suppression, sample.Timestamp),
            sample.Timestamp, DateTimeOffset.UtcNow - sample.Timestamp, sample.Source, 0.9, string.Empty,
            sample.SpeedMetersPerSecond * 2.2369362920544, sample.Throttle, sample.Brake, sample.SteeringWheelAngleRadians,
            sample.Gear, sample.Rpm, sample.YawRateRadiansPerSecond * 57.29577951308232, sample.LateralAccelerationG,
            sample.LongitudinalAccelerationG, sample.LapDistancePercent, sample.Latitude, sample.Longitude,
            sample.FuelLiters, sample.FuelLevelPercent);
    }

    public static LiveRaceSnapshot Disconnected(string reason = "Start iRacing to connect the local telemetry stream.")
    {
        var now = DateTimeOffset.UtcNow;
        var gap = new LiveGapState("Unavailable", null, LiveGapTrend.Unavailable, now, TimeSpan.Zero, EvidenceKind.Unavailable, 0, "Local iRacing SDK", reason);
        var pace = new LivePaceTarget(null, null, "Unavailable", "Unavailable", EvidenceKind.Unavailable, 0, now, "No clean live baseline is available.");
        var pit = new LivePitRecommendation(null, null, null, "Unavailable", EvidenceKind.Unavailable, 0, now, "Fuel and strategy context are unavailable.");
        var cue = new LiveDriverCue("Waiting for iRacing", LiveCuePriority.Information, EvidenceKind.Unavailable, 0, now, LiveCueSuppressionReason.StaleTelemetry, reason);
        return new LiveRaceSnapshot(false, "Waiting for iRacing", "WAITING", null, null, null, null, gap, gap with { Label = "Class leader" }, gap with { Label = "Ahead" }, gap with { Label = "Behind" }, null, null, null, pace, pit, null, null, null, "Unavailable", null, null, null, null, null, false, null, null, "Unavailable", cue, null, new SafeGlanceState(true, true, false, LiveCueSuppressionReason.StaleTelemetry, now), now, TimeSpan.Zero, "Local iRacing SDK shared memory", 0, reason);
    }

    private LiveGapState BuildGap(string key, string label, double? value, LiveTelemetrySample sample, string unavailableReason)
    {
        if (!value.HasValue || value.Value < 0 || sample.UnderCaution || sample.OnPitRoad)
        {
            var reason = sample.UnderCaution ? "Competitive gap trends are suppressed under caution." : sample.OnPitRoad ? "Competitive gap trends are suppressed during pit cycles." : unavailableReason;
            return new LiveGapState(label, value, value.HasValue ? LiveGapTrend.Stale : LiveGapTrend.Unavailable, sample.Timestamp, DateTimeOffset.UtcNow - sample.Timestamp, value.HasValue ? EvidenceKind.Measured : EvidenceKind.Unavailable, value.HasValue ? 0.7 : 0, sample.Source, reason);
        }

        if (!_gapHistory.TryGetValue(key, out var history)) _gapHistory[key] = history = new Queue<(DateTimeOffset, double)>();
        history.Enqueue((sample.Timestamp, value.Value));
        while (history.Count > 0 && sample.Timestamp - history.Peek().At > TimeSpan.FromSeconds(8)) history.Dequeue();
        var trend = LiveGapTrend.Stable;
        if (history.Count >= 2 && sample.Timestamp - history.Peek().At >= TimeSpan.FromSeconds(3))
        {
            var delta = value.Value - history.Peek().Value;
            trend = delta < -0.15 ? LiveGapTrend.Closing : delta > 0.15 ? LiveGapTrend.Growing : LiveGapTrend.Stable;
        }
        return new LiveGapState(label, value, trend, sample.Timestamp, DateTimeOffset.UtcNow - sample.Timestamp, EvidenceKind.Measured, 0.9, sample.Source);
    }

    private LivePaceTarget BuildPaceTarget(LiveTelemetrySample sample, string runPhase)
    {
        if (_cleanLapTimes.Count < 3)
            return new LivePaceTarget(null, null, "Unavailable", runPhase, EvidenceKind.Unavailable, 0, sample.Timestamp, "Three clean in-session laps are required for a defensible baseline.");
        var baseline = Median(_cleanLapTimes)!.Value;
        var halfBand = Math.Max(0.15, baseline * 0.005);
        return new LivePaceTarget(baseline - halfBand, baseline + halfBand, "Clean in-session baseline", runPhase, EvidenceKind.Derived, 0.72, sample.Timestamp);
    }

    private LivePitRecommendation BuildPit(LiveTelemetrySample sample)
    {
        var averageUse = _fuelPerLap.Count >= 2 ? Median(_fuelPerLap) : null;
        var currentFuel = sample.FuelLiters is { } fuel && double.IsFinite(fuel) && fuel >= 0 ? fuel : (double?)null;
        var hardLimit = averageUse.HasValue && currentFuel.HasValue
            ? Math.Max(0, (int)Math.Floor((currentFuel.Value - Math.Min(0.5, averageUse.Value * 0.5)) / averageUse.Value))
            : (int?)null;
        var recommendation = hardLimit switch
        {
            <= 1 => "Pit now if pit road and event rules permit",
            <= 3 => "Prepare to pit; fuel margin is critical",
            _ when hardLimit.HasValue => "Fuel hard limit from the current measured burn",
            _ => "Unavailable"
        };
        return new LivePitRecommendation(null, null, hardLimit, recommendation,
            hardLimit.HasValue ? EvidenceKind.Derived : EvidenceKind.Unavailable,
            hardLimit.HasValue ? 0.7 : 0, sample.Timestamp,
            hardLimit.HasValue ? "No validated strategic pit-window model is loaded." : "Two clean lap-to-lap fuel samples are required.");
    }

    private static LiveDriverCue BuildCue(LiveTelemetrySample sample, LiveGapState ahead, LivePitRecommendation pit, LivePaceTarget pace, LiveDriverCue? driverCue, bool paused)
    {
        if (sample.BlackFlag) return Cue("Black flag: follow the recorded penalty instruction", LiveCuePriority.Critical, EvidenceKind.Measured, sample, "iRacing race status");
        if (sample.Towing) return Cue("Tow active", LiveCuePriority.Critical, EvidenceKind.Measured, sample, "PlayerCarTowTime");
        if (sample.MandatoryRepairSeconds is > 0) return Cue($"Mandatory repair: {Math.Ceiling(sample.MandatoryRepairSeconds.Value)} s remaining", LiveCuePriority.Critical, EvidenceKind.Measured, sample, "iRacing repair timer");
        if (pit.FuelHardLimitLaps is <= 1) return Cue("Fuel hard limit: pit now if rules permit", LiveCuePriority.Critical, EvidenceKind.Derived, sample, "Fuel-use history");
        if (pit.FuelHardLimitLaps is <= 3) return Cue($"Fuel hard limit: {pit.FuelHardLimitLaps} laps", LiveCuePriority.Strategy, EvidenceKind.Derived, sample, "Fuel-use history");
        if (sample.UnderCaution) return Cue("Caution: competitive gap trends paused", LiveCuePriority.PitService, EvidenceKind.Measured, sample, "iRacing race status");
        if (sample.OnPitRoad) return Cue("Pit road: confirm service and speed", LiveCuePriority.PitService, EvidenceKind.Measured, sample, "OnPitRoad");
        if (ahead.Seconds is < 1.0 && ahead.Trend == LiveGapTrend.Closing) return Cue($"Gap ahead closing: {ahead.Seconds:0.00} s", LiveCuePriority.Traffic, EvidenceKind.Derived, sample, "8-second scoring trend");
        if (pace.MaximumSeconds.HasValue && sample.LastLapSeconds > pace.MaximumSeconds.Value + 0.3) return Cue("Last lap outside the clean in-session target band", LiveCuePriority.Pace, EvidenceKind.Derived, sample, pace.Source);
        if (driverCue is not null) return driverCue;
        if (!pace.MaximumSeconds.HasValue) return Cue("Building your pace range · complete three clean laps", LiveCuePriority.Information, EvidenceKind.Unavailable, sample, pace.UnavailableReason);
        return Cue("No action: pace and race state within the supported band", LiveCuePriority.Information, EvidenceKind.Derived, sample, pace.Source);
    }

    private void BeginObservedLap(LiveTelemetrySample sample, bool fullyObserved)
    {
        _currentLapFullyObserved = fullyObserved;
        _currentLapSessionConfounded = false;
        _currentLapInputConfounded = false;
        _currentLapUnderCaution = false;
        _currentLapWasOnPitRoad = false;
        _currentLapBrakePeaks.Clear();
        _fuelAtLapStart = fullyObserved ? sample.FuelLiters : null;
    }

    private void ObserveCurrentLap(LiveTelemetrySample sample)
    {
        if (sample.OnPitRoad && !_wasOnPitRoad)
        {
            _observedRunGreenLaps = 0;
            _observedRunCautionLaps = 0;
        }
        _wasOnPitRoad = sample.OnPitRoad;

        var sessionConfounded = sample.UnderCaution || sample.OnPitRoad || sample.RepairFlag || sample.Towing || sample.BlackFlag;
        _currentLapSessionConfounded |= sessionConfounded;
        _currentLapInputConfounded |= sessionConfounded || sample.GapToAheadSeconds is < 1.5 || sample.GapToBehindSeconds is < 0.75;
        _currentLapUnderCaution |= sample.UnderCaution;
        _currentLapWasOnPitRoad |= sample.OnPitRoad;

        if (sample.LapDistancePercent is not >= 0 or >= 1 || sample.Brake is not >= 0) return;
        var zone = Math.Clamp((int)(sample.LapDistancePercent.Value * 20), 0, 19);
        if (!_currentLapBrakePeaks.TryGetValue(zone, out var peak) || sample.Brake.Value > peak)
            _currentLapBrakePeaks[zone] = sample.Brake.Value;
    }

    private void CompleteObservedLap(LiveTelemetrySample sample, bool sequentialLap)
    {
        var fullyObserved = sequentialLap && _currentLapFullyObserved;
        var boundarySessionConfounded = sample.UnderCaution || sample.OnPitRoad || sample.RepairFlag || sample.Towing || sample.BlackFlag;
        var cleanSessionLap = fullyObserved && !_currentLapSessionConfounded && !boundarySessionConfounded;
        var completedUnderCaution = _currentLapUnderCaution || sample.UnderCaution;
        var completedOnPitRoad = _currentLapWasOnPitRoad || sample.OnPitRoad;
        if (cleanSessionLap && !completedOnPitRoad)
        {
            _observedRunGreenLaps++;
        }
        else if (fullyObserved && !completedOnPitRoad && completedUnderCaution)
        {
            _observedRunCautionLaps++;
        }

        if (cleanSessionLap && sample.LastLapSeconds is > 0 and < 3600 && double.IsFinite(sample.LastLapSeconds.Value))
            EnqueueBounded(_cleanLapTimes, sample.LastLapSeconds.Value, 8);

        if (cleanSessionLap && _fuelAtLapStart is { } startFuel && startFuel >= 0 && double.IsFinite(startFuel) &&
            sample.FuelLiters is { } endFuel && endFuel >= 0 && double.IsFinite(endFuel))
        {
            var used = startFuel - endFuel;
            if (used is > 0.05 and < 20) EnqueueBounded(_fuelPerLap, used, 8);
        }

        CompleteDriverInputLap(sample, fullyObserved && !_currentLapInputConfounded && !boundarySessionConfounded);
    }

    private void CompleteDriverInputLap(LiveTelemetrySample sample, bool eligible)
    {
        if (!eligible || _currentLapBrakePeaks.Count == 0)
        {
            _currentLapBrakePeaks.Clear();
            return;
        }

        var medians = _brakePeakBaseline
            .Where(pair => pair.Value.Count >= 3)
            .ToDictionary(pair => pair.Key, pair => Median(pair.Value)!.Value);
        var strongestBaseline = medians.Count == 0 ? 0 : medians.Values.Max();
        LiveDriverCue? newCue = null;
        var cueSeverity = 0d;
        foreach (var (zone, peak) in _currentLapBrakePeaks)
        {
            if (!_brakePeakBaseline.TryGetValue(zone, out var baseline))
                _brakePeakBaseline[zone] = baseline = new Queue<double>();

            if (baseline.Count >= 3 && medians.TryGetValue(zone, out var median) && strongestBaseline > 0 && median >= strongestBaseline * .35)
            {
                var deviations = baseline.Select(value => Math.Abs(value - median));
                var repeatabilityBand = Math.Max(median * .08, Median(deviations)!.Value * 3);
                var excess = peak - median;
                if (excess > repeatabilityBand)
                {
                    var streak = _brakePeakStreaks.TryGetValue(zone, out var previous) ? previous + 1 : 1;
                    _brakePeakStreaks[zone] = streak;
                    if (streak >= 3 && excess / median > cueSeverity)
                    {
                        cueSeverity = excess / median;
                        var zonePercent = zone * 5;
                        newCue = new LiveDriverCue(
                            $"Braking zone near {zonePercent}% lap: peak {excess / median:P0} above your clean baseline on {streak} comparable laps",
                            LiveCuePriority.Coaching,
                            EvidenceKind.Derived,
                            .72,
                            sample.Timestamp,
                            LiveCueSuppressionReason.None,
                            "Clean in-session brake-peak baseline by lap-distance zone");
                    }
                    continue;
                }
                _brakePeakStreaks[zone] = 0;
            }

            baseline.Enqueue(peak);
            while (baseline.Count > 8) baseline.Dequeue();
        }
        _persistentDriverCue = newCue;
        _currentLapBrakePeaks.Clear();
    }

    internal void ResetSession()
    {
        ResetSessionState();
        _wasConnected = false;
    }

    private void ResetSessionState()
    {
        _gapHistory.Clear();
        _cleanLapTimes.Clear();
        _fuelPerLap.Clear();
        _brakePeakBaseline.Clear();
        _currentLapBrakePeaks.Clear();
        _brakePeakStreaks.Clear();
        _lastLap = null;
        _fuelAtLapStart = null;
        _observedRunGreenLaps = 0;
        _observedRunCautionLaps = 0;
        _initialTrackTemperature = null;
        _displayedCue = null;
        _persistentDriverCue = null;
        _currentLapFullyObserved = false;
        _currentLapSessionConfounded = false;
        _currentLapInputConfounded = false;
        _currentLapUnderCaution = false;
        _currentLapWasOnPitRoad = false;
        _wasOnPitRoad = false;
    }

    private static LiveDriverCue Cue(string message, LiveCuePriority priority, EvidenceKind evidence, LiveTelemetrySample sample, string basis) =>
        new(message, priority, evidence, evidence == EvidenceKind.Unavailable ? 0 : 0.8, sample.Timestamp, LiveCueSuppressionReason.None, basis);

    private static void EnqueueBounded(Queue<double> queue, double value, int limit)
    {
        queue.Enqueue(value);
        while (queue.Count > limit) queue.Dequeue();
    }

    private static double? Median(IEnumerable<double> values)
    {
        var ordered = values.Order().ToArray();
        if (ordered.Length == 0) return null;
        return ordered.Length % 2 == 1 ? ordered[ordered.Length / 2] : (ordered[ordered.Length / 2 - 1] + ordered[ordered.Length / 2]) / 2;
    }
}

public sealed class DisconnectedLiveTelemetrySource : ILiveTelemetrySource
{
    private DateTimeOffset _last = DateTimeOffset.MinValue;
    public bool TryRead(out LiveTelemetrySample sample)
    {
        sample = new LiveTelemetrySample { Connected = false };
        if (DateTimeOffset.UtcNow - _last < TimeSpan.FromSeconds(1)) return false;
        _last = DateTimeOffset.UtcNow;
        return true;
    }
    public void Dispose() { }
}
