using iRacingCoach.Coordinator;

namespace iRacingCoach.App;

internal sealed class DebugLiveTelemetrySource : ILiveTelemetrySource
{
    private int _tick;
    private readonly string _state;

    public DebugLiveTelemetrySource(string state) => _state = state;

    public bool TryRead(out LiveTelemetrySample sample)
    {
        _tick++;
        if (_state == "disconnected")
        {
            sample = new LiveTelemetrySample { Connected = false };
            return _tick % 10 == 1;
        }

        var driverFeedback = _state == "brake";
        var unavailableBaseline = _state == "baseline";
        var calmFixture = driverFeedback || unavailableBaseline;
        var lap = unavailableBaseline ? 43 : driverFeedback ? Math.Min(49, 43 + _tick / 5) : Math.Min(47, 43 + _tick / 10);
        var caution = _state == "caution";
        var repair = _state == "repair";
        var criticalFuel = _state == "fuel";
        sample = new LiveTelemetrySample
        {
            Connected = true, Timestamp = DateTimeOffset.UtcNow, Tick = _tick, TickRate = 60, Flag = caution ? "CAUTION" : "GREEN", UnderCaution = caution, RepairFlag = repair, Lap = lap, LapsRemaining = Math.Max(0, 80 - lap), OverallPosition = 12, ClassPosition = 12,
            GapToLeaderSeconds = 3.21, GapToClassLeaderSeconds = 3.21, GapToAheadSeconds = calmFixture ? 3 : Math.Max(0.42, 0.94 - _tick * .006), GapToBehindSeconds = calmFixture ? 3 : 1.08 + _tick * .002,
            LastLapSeconds = 30.824, LeaderLastLapSeconds = 30.410, FuelLiters = criticalFuel ? Math.Max(.4, 2.1 - _tick * .08) : Math.Max(12, 44 - _tick * .08), TrackTemperatureC = 43.2, AirTemperatureC = 27.4, BrakeBiasPercent = 58.1,
            MandatoryRepairSeconds = repair ? 34 : 0, OptionalRepairSeconds = repair ? 92 : 0,
            SteeringWheelAngleRadians = .14 * Math.Sin(_tick / 7d), Throttle = .55 + .4 * Math.Cos(_tick / 9d),
            Brake = driverFeedback ? (_tick > 15 ? .8 : .5) : Math.Max(0, .35 * Math.Sin(_tick / 8d)), Gear = 4, Rpm = 6100 + 900 * Math.Sin(_tick / 8d),
            YawRateRadiansPerSecond = .08 * Math.Sin(_tick / 7d), LateralAccelerationG = .8 * Math.Sin(_tick / 7d), LongitudinalAccelerationG = .25 * Math.Cos(_tick / 9d),
            SpeedMetersPerSecond = 60 + 12 * Math.Cos(_tick / 9d), LapDistancePercent = driverFeedback ? .25 : (_tick % 100) / 100d,
            Latitude = 39.5 + .002 * Math.Cos(_tick / 16d), Longitude = -86.2 + .003 * Math.Sin(_tick / 16d)
        };
        return true;
    }
    public void Dispose() { }
}
