using System.Diagnostics;
using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

public interface ILiveTelemetrySource : IDisposable
{
    bool TryRead(out LiveTelemetrySample sample);
}

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
    public int? Gear { get; init; }
    public double? Rpm { get; init; }
    public double? YawRateRadiansPerSecond { get; init; }
    public double? LateralAccelerationG { get; init; }
    public double? LongitudinalAccelerationG { get; init; }
    public double? SpeedMetersPerSecond { get; init; }
    public double? LapDistancePercent { get; init; }
    public double? Latitude { get; init; }
    public double? Longitude { get; init; }
    public string Source { get; init; } = "Local iRacing SDK shared memory";
}

public sealed class LiveTelemetryService : IDisposable
{
    private static readonly TimeSpan PollInterval = TimeSpan.FromMilliseconds(8);
    private static readonly TimeSpan HistorySnapshotInterval = TimeSpan.FromMilliseconds(100);
    private const int MaximumHistoryPoints = 36_000;
    private readonly ILiveTelemetrySource _source;
    private readonly LiveTelemetryEngine _engine = new();
    private readonly object _gate = new();
    private Timer? _timer;
    private bool _disposed;
    private long _framesRead;
    private long _droppedFrames;
    private readonly Queue<LiveTracePoint> _history = new();
    private IReadOnlyList<LiveTracePoint> _historySnapshot = [];
    private int _busy;
    private DateTimeOffset _lastPublish = DateTimeOffset.MinValue;
    private DateTimeOffset _lastHistorySnapshot = DateTimeOffset.MinValue;

    public LiveTelemetryService(ILiveTelemetrySource source, LiveMonitorLayout layout)
    {
        _source = source;
        Current = new LiveMonitorState(LiveTelemetryEngine.Disconnected(), layout, false, 0, 0, 0, DateTimeOffset.UtcNow);
    }

    public event Action<LiveMonitorState>? Updated;
    public event Action<LiveTracePoint>? FrameCaptured;
    public LiveMonitorState Current { get; private set; }
    public bool CoachingPaused { get; private set; }

    public void Start() => _timer ??= new Timer(Poll, null, TimeSpan.Zero, PollInterval);

    public void SetCoachingPaused(bool paused)
    {
        CoachingPaused = paused;
        lock (_gate)
        {
            Current = Current with { CoachingPaused = paused, UpdatedAt = DateTimeOffset.UtcNow };
        }
        Updated?.Invoke(Current);
    }

    private void Poll(object? state)
    {
        if (Interlocked.Exchange(ref _busy, 1) != 0)
        {
            Interlocked.Increment(ref _droppedFrames);
            return;
        }

        try
        {
            var started = Stopwatch.GetTimestamp();
            if (_source.TryRead(out var sample))
            {
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
                        snapshot.TrackTemperatureC);
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
                    Current = new LiveMonitorState(snapshot, Current.Layout, CoachingPaused, ++_framesRead, _droppedFrames, latency, DateTimeOffset.UtcNow, _historySnapshot, sample.TickRate);
                }
                _lastPublish = DateTimeOffset.UtcNow;
                if (tracePoint is not null) FrameCaptured?.Invoke(tracePoint);
                Updated?.Invoke(Current);
            }
            else if (DateTimeOffset.UtcNow - _lastPublish > TimeSpan.FromSeconds(1))
            {
                var snapshot = Current.Snapshot;
                if (snapshot.Connected && DateTimeOffset.UtcNow - snapshot.SourceTimestamp > TimeSpan.FromSeconds(2))
                {
                    snapshot = LiveTelemetryEngine.Disconnected("Live telemetry became stale.");
                }
                lock (_gate)
                {
                    Current = Current with { Snapshot = snapshot, DroppedFrames = _droppedFrames, UpdatedAt = DateTimeOffset.UtcNow };
                }
                _lastPublish = DateTimeOffset.UtcNow;
                Updated?.Invoke(Current);
            }
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or InvalidDataException or ArgumentException)
        {
            lock (_gate)
            {
                Current = Current with { Snapshot = LiveTelemetryEngine.Disconnected("The local iRacing telemetry stream could not be read."), UpdatedAt = DateTimeOffset.UtcNow };
            }
            Updated?.Invoke(Current);
        }
        finally
        {
            Volatile.Write(ref _busy, 0);
        }
    }

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        _timer?.Dispose();
        _source.Dispose();
    }
}

public sealed class LiveTelemetryEngine
{
    private readonly Dictionary<string, Queue<(DateTimeOffset At, double Value)>> _gapHistory = new(StringComparer.Ordinal);
    private readonly Queue<double> _cleanLapTimes = new();
    private readonly Queue<double> _fuelPerLap = new();
    private int? _lastLap;
    private double? _fuelAtLapStart;
    private int _greenLaps;
    private int _cautionLaps;
    private double? _initialTrackTemperature;
    private LiveDriverCue? _displayedCue;
    private readonly Dictionary<int, Queue<double>> _brakePeakBaseline = [];
    private readonly Dictionary<int, double> _currentLapBrakePeaks = [];
    private readonly Dictionary<int, int> _brakePeakStreaks = [];
    private bool _currentLapInputConfounded;
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

        var lapCompleted = _lastLap.HasValue && sample.Lap.HasValue && sample.Lap.Value > _lastLap.Value;
        if (lapCompleted) CompleteDriverInputLap(sample);
        CaptureDriverInput(sample);
        if (lapCompleted)
        {
            if (sample.UnderCaution) _cautionLaps++; else _greenLaps++;
            if (!sample.UnderCaution && !sample.OnPitRoad && !sample.RepairFlag && sample.LastLapSeconds is > 0)
            {
                EnqueueBounded(_cleanLapTimes, sample.LastLapSeconds.Value, 8);
            }
            if (_fuelAtLapStart.HasValue && sample.FuelLiters.HasValue)
            {
                var used = _fuelAtLapStart.Value - sample.FuelLiters.Value;
                if (used is > 0.05 and < 20) EnqueueBounded(_fuelPerLap, used, 8);
            }
            _fuelAtLapStart = sample.FuelLiters;
        }
        else if (!_lastLap.HasValue)
        {
            _fuelAtLapStart = sample.FuelLiters;
        }
        _lastLap = sample.Lap;
        _initialTrackTemperature ??= sample.TrackTemperatureC;

        var leader = BuildGap("leader", "Leader", sample.GapToLeaderSeconds, sample, sample.LeaderGapUnavailableReason);
        var classLeader = BuildGap("classLeader", "Class leader", sample.GapToClassLeaderSeconds, sample, sample.LeaderGapUnavailableReason);
        var ahead = BuildGap("ahead", "Ahead", sample.GapToAheadSeconds, sample, sample.AheadGapUnavailableReason);
        var behind = BuildGap("behind", "Behind", sample.GapToBehindSeconds, sample, sample.BehindGapUnavailableReason);
        var tirePhase = _greenLaps < 5 ? "Early run" : _greenLaps < 20 ? "Middle run" : "Late run";
        var pace = BuildPaceTarget(sample, tirePhase);
        var pit = BuildPit(sample);
        var glanceOpportunity = sample.UnderCaution || sample.OnPitRoad || lapCompleted ||
            ((sample.Brake ?? 0) < 0.05 && Math.Abs(sample.SteeringWheelAngleRadians ?? 0) < 0.12 && Math.Abs(sample.LateralAccelerationG ?? 0) < 0.35);
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
        var avgFuel = Median(_fuelPerLap);
        double? fuelLaps = avgFuel.HasValue && sample.FuelLiters.HasValue ? Math.Max(0, sample.FuelLiters.Value / avgFuel.Value) : null;

        return new LiveRaceSnapshot(
            true, "Connected", sample.Flag, sample.Lap, sample.LapsRemaining, sample.OverallPosition, sample.ClassPosition,
            leader, classLeader, ahead, behind, sample.LastLapSeconds, sample.LeaderLastLapSeconds, paceDelta, pace, pit,
            _greenLaps, _greenLaps + _cautionLaps, _cautionLaps, tirePhase, fuelLaps,
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

    private LivePaceTarget BuildPaceTarget(LiveTelemetrySample sample, string tirePhase)
    {
        if (_cleanLapTimes.Count < 3)
            return new LivePaceTarget(null, null, "Unavailable", tirePhase, EvidenceKind.Unavailable, 0, sample.Timestamp, "Three clean in-session laps are required for a defensible baseline.");
        var baseline = Median(_cleanLapTimes)!.Value;
        var halfBand = Math.Max(0.15, baseline * 0.005);
        return new LivePaceTarget(baseline - halfBand, baseline + halfBand, "Clean in-session baseline", tirePhase, EvidenceKind.Derived, 0.72, sample.Timestamp);
    }

    private LivePitRecommendation BuildPit(LiveTelemetrySample sample)
    {
        var averageUse = Median(_fuelPerLap);
        var hardLimit = averageUse.HasValue && sample.FuelLiters.HasValue
            ? Math.Max(0, (int)Math.Floor((sample.FuelLiters.Value - Math.Min(0.5, averageUse.Value * 0.5)) / averageUse.Value))
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

    private void CaptureDriverInput(LiveTelemetrySample sample)
    {
        _currentLapInputConfounded |= sample.UnderCaution || sample.OnPitRoad || sample.RepairFlag || sample.Towing || sample.BlackFlag ||
            sample.GapToAheadSeconds is < 1.5 || sample.GapToBehindSeconds is < 0.75;
        if (sample.LapDistancePercent is not >= 0 or >= 1 || sample.Brake is not >= 0) return;
        var zone = Math.Clamp((int)(sample.LapDistancePercent.Value * 20), 0, 19);
        if (!_currentLapBrakePeaks.TryGetValue(zone, out var peak) || sample.Brake.Value > peak)
            _currentLapBrakePeaks[zone] = sample.Brake.Value;
    }

    private void CompleteDriverInputLap(LiveTelemetrySample sample)
    {
        if (_currentLapInputConfounded || _currentLapBrakePeaks.Count == 0)
        {
            _currentLapBrakePeaks.Clear();
            _currentLapInputConfounded = false;
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
        _currentLapInputConfounded = false;
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
        _greenLaps = 0;
        _cautionLaps = 0;
        _initialTrackTemperature = null;
        _displayedCue = null;
        _persistentDriverCue = null;
        _currentLapInputConfounded = false;
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
