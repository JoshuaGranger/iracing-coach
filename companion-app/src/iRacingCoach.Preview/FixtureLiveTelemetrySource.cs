using iRacingCoach.Coordinator;

namespace iRacingCoach.Preview;

internal sealed class FixtureLiveTelemetrySource : ILiveTelemetrySource
{
    private int _tick;
    private readonly string _state = Environment.GetEnvironmentVariable("IRACING_COACH_PREVIEW_LIVE_STATE")?.Trim().ToLowerInvariant() ?? "green";

    public bool TryRead(out LiveTelemetrySample sample)
    {
        _tick++;
        if (_state == "disconnected")
        {
            sample = new LiveTelemetrySample { Connected = false };
            return _tick % 10 == 1;
        }

        var lap = Math.Min(47, 43 + _tick / 10);
        var caution = _state == "caution";
        var repair = _state == "repair";
        var criticalFuel = _state == "fuel";
        sample = new LiveTelemetrySample
        {
            Connected = true,
            Timestamp = DateTimeOffset.UtcNow,
            Tick = _tick,
            TickRate = 60,
            Flag = caution ? "CAUTION" : "GREEN",
            UnderCaution = caution,
            RepairFlag = repair,
            Lap = lap,
            LapsRemaining = Math.Max(0, 80 - lap),
            OverallPosition = 12,
            ClassPosition = 12,
            GapToLeaderSeconds = 3.21,
            GapToClassLeaderSeconds = 3.21,
            GapToAheadSeconds = Math.Max(0.42, 0.94 - _tick * 0.006),
            GapToBehindSeconds = 1.08 + _tick * 0.002,
            LastLapSeconds = 30.824,
            LeaderLastLapSeconds = 30.410,
            FuelLiters = criticalFuel ? Math.Max(0.4, 2.1 - _tick * 0.08) : Math.Max(12, 44 - _tick * 0.08),
            TrackTemperatureC = 43.2 + Math.Min(1.2, _tick * 0.005),
            AirTemperatureC = 27.4,
            BrakeBiasPercent = 58.1,
            OnPitRoad = false,
            MandatoryRepairSeconds = repair ? 34 : 0,
            OptionalRepairSeconds = repair ? 92 : 0,
            SteeringWheelAngleRadians = 0.02,
            Brake = 0,
            LateralAccelerationG = 0.04,
            SpeedMetersPerSecond = 72,
            LapDistancePercent = (_tick % 10) / 10d
        };
        return true;
    }

    public void Dispose() { }
}
