using System.Globalization;
using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

public sealed record LiveTelemetryMetricDefinition(
    string Id,
    string Name,
    string Description,
    LiveMonitorMetricSource Source,
    string DefaultUnit,
    IReadOnlyList<string> Units,
    IReadOnlyList<LiveMonitorDisplayStyle> Styles,
    LiveMonitorDisplayStyle DefaultStyle,
    int DefaultPrecision,
    double? SemanticMinimum = null,
    double? SemanticMaximum = null);

public sealed record LiveTelemetryMetricReading(
    bool Available,
    string DisplayValue,
    string SecondaryValue,
    string Unit,
    double? NumericValue,
    double? Minimum,
    double? Maximum,
    string AvailabilityMessage,
    IReadOnlyList<double> TrendValues);

public static class LiveTelemetryCatalog
{
    private static readonly IReadOnlyDictionary<string, LiveTelemetryMetricDefinition> ById = Build()
        .ToDictionary(item => item.Id, StringComparer.Ordinal);

    public static IReadOnlyList<LiveTelemetryMetricDefinition> All { get; } = ById.Values.OrderBy(item => item.Name, StringComparer.CurrentCultureIgnoreCase).ToArray();

    public static LiveTelemetryMetricDefinition Get(string id) => ById[id];
    public static bool TryGet(string id, out LiveTelemetryMetricDefinition definition) => ById.TryGetValue(id, out definition!);

    public static LiveTelemetryMetricReading Read(string metricId, LiveMonitorState state, string? requestedUnit = null, int? requestedPrecision = null, LiveMonitorTrendDuration duration = LiveMonitorTrendDuration.Seconds30)
    {
        var definition = Get(metricId);
        var snapshot = state.Snapshot;
        var unit = definition.Units.Contains(requestedUnit ?? string.Empty, StringComparer.Ordinal) ? requestedUnit! : definition.DefaultUnit;
        var precision = Math.Clamp(requestedPrecision ?? definition.DefaultPrecision, 0, 3);
        if (!snapshot.Connected)
            return Missing(unit, "Waiting for iRacing");

        var trend = Trend(metricId, state.History ?? [], duration, unit);
        return metricId switch
        {
            "air-temperature" => Number(snapshot.AirTemperatureC, unit, precision, trend, value => Temperature(value, unit)),
            "ahead-gap" => Seconds(snapshot.AheadGap.Seconds, precision, snapshot.AheadGap.UnavailableReason),
            "behind-gap" => Seconds(snapshot.BehindGap.Seconds, precision, snapshot.BehindGap.UnavailableReason),
            "brake" => Percent(snapshot.Brake, precision, trend),
            "brake-bias" => Number(snapshot.BrakeBiasPercent, "%", precision, trend),
            "class-position" => Position(snapshot.ClassPosition),
            "coach-cue" => Text(snapshot.PrimaryCue.Message, snapshot.PrimaryCue.Basis),
            "flag" => Text(snapshot.Flag, snapshot.Flag == "RACING" ? "Recorded session state" : "iRacing race status"),
            "fuel" => Fuel(snapshot, unit, precision),
            "fuel-laps" => Number(snapshot.FuelLapsRemaining, "laps", precision, trend, value => value),
            "gear" => Number(snapshot.Gear, string.Empty, 0, trend),
            "lap" => Number(snapshot.Lap, string.Empty, 0, trend, value => value, "Lap not measured yet."),
            "laps-remaining" => Number(snapshot.LapsRemaining, "laps", 0, trend),
            "last-lap" => LastLap(snapshot),
            "lateral-acceleration" => Acceleration(snapshot.LateralAccelerationG, unit, precision, trend),
            "leader-gap" => snapshot.OverallPosition == 1 ? new(true, "Leader", string.Empty, string.Empty, 0, null, null, string.Empty, []) : Seconds(snapshot.LeaderGap.Seconds, precision, snapshot.LeaderGap.UnavailableReason),
            "leader-last-lap" => LapTime(snapshot.LeaderLastLapSeconds),
            "longitudinal-acceleration" => Acceleration(snapshot.LongitudinalAccelerationG, unit, precision, trend),
            "mandatory-repair" => Seconds(snapshot.MandatoryRepairSeconds, 0, "No mandatory repair workload is currently recorded."),
            "on-pit-road" => Text(snapshot.OnPitRoad ? "On pit road" : "On track", "Recorded OnPitRoad state"),
            "optional-repair" => Seconds(snapshot.OptionalRepairSeconds, 0, "No optional repair workload is currently recorded."),
            "pace-range" => Pace(snapshot),
            "pit-window" => Pit(snapshot),
            "position" => Position(snapshot.OverallPosition),
            "rpm" => Number(snapshot.Rpm, "rpm", 0, trend),
            "speed" => Number(snapshot.SpeedMph, unit, precision, trend, value => Speed(value, unit)),
            "steering" => Number(snapshot.SteeringWheelAngleRadians, unit, precision, trend, value => Steering(value, unit)),
            "throttle" => Percent(snapshot.Throttle, precision, trend),
            "tire-phase" => Text(Useful(snapshot.TirePhase) ? snapshot.TirePhase : "Insufficient evidence", "Calculated from current clean-run evidence"),
            "track-temperature" => Number(snapshot.TrackTemperatureC, unit, precision, trend, value => Temperature(value, unit)),
            "yaw-rate" => Number(snapshot.YawRateDegreesPerSecond, unit, precision, trend, value => Yaw(value, unit)),
            _ => Missing(unit, "Not measured in this session")
        };
    }

    public static IReadOnlyList<LiveMonitorTrendDuration> TrendDurations(string metricId) => metricId switch
    {
        "speed" or "throttle" or "brake" or "steering" or "gear" or "rpm" or "yaw-rate" or "lateral-acceleration" or "longitudinal-acceleration" =>
            [LiveMonitorTrendDuration.Seconds15, LiveMonitorTrendDuration.Seconds30, LiveMonitorTrendDuration.Seconds60, LiveMonitorTrendDuration.OneLap, LiveMonitorTrendDuration.ThreeLaps],
        _ => []
    };

    private static IReadOnlyList<LiveTelemetryMetricDefinition> Build()
    {
        var number = new[] { LiveMonitorDisplayStyle.Number };
        var numericTrend = new[] { LiveMonitorDisplayStyle.Number, LiveMonitorDisplayStyle.Trend };
        var status = new[] { LiveMonitorDisplayStyle.Status, LiveMonitorDisplayStyle.Number };
        var percent = new[] { LiveMonitorDisplayStyle.Number, LiveMonitorDisplayStyle.Gauge, LiveMonitorDisplayStyle.Bar, LiveMonitorDisplayStyle.Trend };
        return
        [
            D("air-temperature", "Air temperature", "Recorded ambient temperature.", LiveMonitorMetricSource.Recorded, "°C", ["°C", "°F"], number, 1),
            D("ahead-gap", "Gap ahead", "Physical same-lap scoring interval to the car ahead.", LiveMonitorMetricSource.Calculated, "s", ["s"], number, 2),
            D("behind-gap", "Gap behind", "Physical same-lap scoring interval to the car behind.", LiveMonitorMetricSource.Calculated, "s", ["s"], number, 2),
            D("brake", "Brake", "Recorded brake-pedal input.", LiveMonitorMetricSource.Recorded, "%", ["%"], percent, 0, 0, 100),
            D("brake-bias", "Brake bias", "Recorded in-car brake-bias setting when the car exposes it.", LiveMonitorMetricSource.Recorded, "%", ["%"], [LiveMonitorDisplayStyle.Number, LiveMonitorDisplayStyle.Bar], 1, 40, 70),
            D("class-position", "Class position", "Recorded class scoring position.", LiveMonitorMetricSource.Recorded, string.Empty, [string.Empty], number, 0),
            D("coach-cue", "Coach cue", "Highest-priority local safe-glance instruction.", LiveMonitorMetricSource.Coach, string.Empty, [string.Empty], status, 0),
            D("flag", "Flag state", "Recorded iRacing race-control state.", LiveMonitorMetricSource.Recorded, string.Empty, [string.Empty], status, 0),
            D("fuel", "Fuel remaining", "Recorded fuel with locally calculated lap estimate.", LiveMonitorMetricSource.Calculated, "US gal", ["US gal", "L"], [LiveMonitorDisplayStyle.Number, LiveMonitorDisplayStyle.Gauge, LiveMonitorDisplayStyle.Bar], LiveMonitorDisplayStyle.Bar, 1, 0, 100),
            D("fuel-laps", "Fuel laps remaining", "Estimated laps from measured fuel-use history.", LiveMonitorMetricSource.Calculated, "laps", ["laps"], number, 1),
            D("gear", "Gear", "Recorded selected gear.", LiveMonitorMetricSource.Recorded, string.Empty, [string.Empty], numericTrend, LiveMonitorDisplayStyle.Number, 0),
            D("lap", "Lap", "Current recorded player lap.", LiveMonitorMetricSource.Recorded, string.Empty, [string.Empty], number, 0),
            D("laps-remaining", "Laps remaining", "Recorded session laps remaining.", LiveMonitorMetricSource.Recorded, "laps", ["laps"], number, 0),
            D("last-lap", "Last lap", "Last completed lap with leader-lap delta when available.", LiveMonitorMetricSource.Calculated, "time", ["time"], number, 3),
            D("lateral-acceleration", "Lateral acceleration", "Recorded lateral acceleration.", LiveMonitorMetricSource.Recorded, "g", ["g", "m/s²"], numericTrend, LiveMonitorDisplayStyle.Trend, 2),
            D("leader-gap", "Physical gap to leader", "Physical same-lap scoring interval to the overall leader.", LiveMonitorMetricSource.Calculated, "s", ["s"], number, 2),
            D("leader-last-lap", "Leader last lap", "Last recorded lap time for the overall leader.", LiveMonitorMetricSource.Recorded, "time", ["time"], number, 3),
            D("longitudinal-acceleration", "Longitudinal acceleration", "Recorded longitudinal acceleration.", LiveMonitorMetricSource.Recorded, "g", ["g", "m/s²"], numericTrend, LiveMonitorDisplayStyle.Trend, 2),
            D("mandatory-repair", "Mandatory repair", "Recorded mandatory repair countdown.", LiveMonitorMetricSource.Recorded, "s", ["s"], number, 0),
            D("on-pit-road", "Pit-road state", "Recorded pit-road state.", LiveMonitorMetricSource.Recorded, string.Empty, [string.Empty], status, 0),
            D("optional-repair", "Optional repair", "Recorded optional repair countdown.", LiveMonitorMetricSource.Recorded, "s", ["s"], number, 0),
            D("pace-range", "Personal pace range", "Clean in-session pace band when enough evidence exists.", LiveMonitorMetricSource.Coach, "time", ["time"], number, 3),
            D("pit-window", "Laps until recommended pit", "Strategic window when supported, otherwise the fuel hard limit.", LiveMonitorMetricSource.Coach, "laps", ["laps"], number, 0),
            D("position", "Position", "Recorded overall scoring position.", LiveMonitorMetricSource.Recorded, string.Empty, [string.Empty], number, 0),
            D("rpm", "RPM", "Recorded engine speed.", LiveMonitorMetricSource.Recorded, "rpm", ["rpm"], numericTrend, LiveMonitorDisplayStyle.Trend, 0),
            D("speed", "Speed", "Recorded vehicle speed.", LiveMonitorMetricSource.Recorded, "mph", ["mph", "km/h"], numericTrend, LiveMonitorDisplayStyle.Trend, 1),
            D("steering", "Steering", "Recorded steering-wheel angle.", LiveMonitorMetricSource.Recorded, "deg", ["deg", "rad"], numericTrend, LiveMonitorDisplayStyle.Trend, 1),
            D("throttle", "Throttle", "Recorded throttle-pedal input.", LiveMonitorMetricSource.Recorded, "%", ["%"], percent, LiveMonitorDisplayStyle.Gauge, 0, 0, 100),
            D("tire-phase", "Tire / run phase", "Local phase label from current clean-run evidence.", LiveMonitorMetricSource.Coach, string.Empty, [string.Empty], status, 0),
            D("track-temperature", "Track temperature", "Recorded track temperature.", LiveMonitorMetricSource.Recorded, "°C", ["°C", "°F"], number, 1),
            D("yaw-rate", "Yaw rate", "Recorded yaw rate.", LiveMonitorMetricSource.Recorded, "deg/s", ["deg/s", "rad/s"], numericTrend, LiveMonitorDisplayStyle.Trend, 1)
        ];
    }

    private static LiveTelemetryMetricDefinition D(string id, string name, string description, LiveMonitorMetricSource source, string defaultUnit, IReadOnlyList<string> units, IReadOnlyList<LiveMonitorDisplayStyle> styles, int precision, double? min = null, double? max = null) =>
        new(id, name, description, source, defaultUnit, units, styles, styles[0], precision, min, max);

    private static LiveTelemetryMetricDefinition D(string id, string name, string description, LiveMonitorMetricSource source, string defaultUnit, IReadOnlyList<string> units, IReadOnlyList<LiveMonitorDisplayStyle> styles, LiveMonitorDisplayStyle defaultStyle, int precision, double? min = null, double? max = null) =>
        new(id, name, description, source, defaultUnit, units, styles, defaultStyle, precision, min, max);

    private static LiveTelemetryMetricReading Fuel(LiveRaceSnapshot snapshot, string unit, int precision)
    {
        if (!snapshot.FuelLiters.HasValue) return Missing(unit, "Fuel level not measured in this session");
        var value = unit == "US gal" ? snapshot.FuelLiters.Value * 0.2641720524 : snapshot.FuelLiters.Value;
        var secondary = snapshot.FuelLapsRemaining.HasValue ? $"{snapshot.FuelLapsRemaining.Value:0.0} estimated laps" : "Lap estimate needs clean fuel-use evidence";
        return new(true, value.ToString($"F{precision}", CultureInfo.CurrentCulture), secondary, unit,
            snapshot.FuelLevelPercent.HasValue ? snapshot.FuelLevelPercent.Value * 100 : null, 0, 100,
            snapshot.FuelLevelPercent.HasValue ? string.Empty : "Fuel percentage not measured; showing amount only.", []);
    }

    private static LiveTelemetryMetricReading LastLap(LiveRaceSnapshot snapshot)
    {
        if (!snapshot.LastLapSeconds.HasValue) return Missing("time", "No completed lap measured yet");
        var secondary = snapshot.LastLapPaceDifferenceSeconds.HasValue
            ? $"{snapshot.LastLapPaceDifferenceSeconds.Value:+0.000;-0.000;0.000} s vs leader last lap"
            : "Leader comparison not measured";
        return new(true, FormatLap(snapshot.LastLapSeconds.Value), secondary, "", snapshot.LastLapSeconds, null, null, string.Empty, []);
    }

    private static LiveTelemetryMetricReading Pace(LiveRaceSnapshot snapshot)
    {
        if (!snapshot.PaceTarget.MinimumSeconds.HasValue || !snapshot.PaceTarget.MaximumSeconds.HasValue)
            return Missing("time", "Insufficient evidence for a clean personal pace range");
        return new(true, $"{FormatLap(snapshot.PaceTarget.MinimumSeconds.Value)}–{FormatLap(snapshot.PaceTarget.MaximumSeconds.Value)}", snapshot.PaceTarget.TirePhase, string.Empty, null, null, null, string.Empty, []);
    }

    private static LiveTelemetryMetricReading Pit(LiveRaceSnapshot snapshot)
    {
        if (snapshot.Pit.WindowOpensInLaps.HasValue && snapshot.Pit.WindowClosesInLaps.HasValue)
            return new(true, $"{snapshot.Pit.WindowOpensInLaps}–{snapshot.Pit.WindowClosesInLaps}", "Strategic pit window", "laps", snapshot.Pit.WindowOpensInLaps, null, null, string.Empty, []);
        if (snapshot.Pit.FuelHardLimitLaps.HasValue)
            return new(true, snapshot.Pit.FuelHardLimitLaps.Value.ToString(CultureInfo.CurrentCulture), "Fuel hard limit; strategic window not established", "laps", snapshot.Pit.FuelHardLimitLaps, null, null, snapshot.Pit.UnavailableReason, []);
        return Missing("laps", "Insufficient evidence for pit guidance");
    }

    private static LiveTelemetryMetricReading Position(int? value) => value is > 0
        ? new(true, $"P{value}", string.Empty, string.Empty, value, null, null, string.Empty, [])
        : Missing(string.Empty, "Position not measured in this session");

    private static LiveTelemetryMetricReading LapTime(double? value) => value is > 0
        ? new(true, FormatLap(value.Value), string.Empty, string.Empty, value, null, null, string.Empty, [])
        : Missing("time", "No completed lap measured yet");

    private static LiveTelemetryMetricReading Seconds(double? value, int precision, string missing) => value.HasValue
        ? new(true, value.Value.ToString($"F{precision}", CultureInfo.CurrentCulture), string.Empty, "s", value, null, null, string.Empty, [])
        : Missing("s", missing);

    private static LiveTelemetryMetricReading Text(string value, string secondary) => Useful(value)
        ? new(true, value, secondary, string.Empty, null, null, null, string.Empty, [])
        : Missing(string.Empty, "Not measured in this session");

    private static LiveTelemetryMetricReading Percent(double? value, int precision, IReadOnlyList<double> trend) => value.HasValue
        ? new(true, (value.Value * 100).ToString($"F{precision}", CultureInfo.CurrentCulture), string.Empty, "%", value.Value * 100, 0, 100, string.Empty, trend)
        : Missing("%", "Input not measured in this session");

    private static LiveTelemetryMetricReading Acceleration(double? value, string unit, int precision, IReadOnlyList<double> trend) =>
        Number(value, unit, precision, trend, input => unit == "m/s²" ? input * 9.80665 : input);

    private static LiveTelemetryMetricReading Number<T>(T? value, string unit, int precision, IReadOnlyList<double> trend, Func<double, double>? converter = null, string missing = "Not measured in this session") where T : struct, IConvertible
    {
        if (!value.HasValue) return Missing(unit, missing);
        var numeric = Convert.ToDouble(value.Value, CultureInfo.InvariantCulture);
        numeric = converter?.Invoke(numeric) ?? numeric;
        return new(true, numeric.ToString($"F{precision}", CultureInfo.CurrentCulture), string.Empty, unit, numeric, null, null, string.Empty, trend);
    }

    private static LiveTelemetryMetricReading Missing(string unit, string reason) => new(false, reason.StartsWith("Insufficient", StringComparison.Ordinal) ? "Insufficient evidence" : reason.StartsWith("Waiting", StringComparison.Ordinal) ? "Waiting" : "Not measured", string.Empty, unit, null, null, null, reason, []);

    private static IReadOnlyList<double> Trend(string metricId, IReadOnlyList<LiveTracePoint> history, LiveMonitorTrendDuration duration, string unit)
    {
        if (history.Count == 0 || TrendDurations(metricId).Count == 0) return [];
        var newest = history[^1];
        IEnumerable<LiveTracePoint> selected = duration switch
        {
            LiveMonitorTrendDuration.Seconds15 => history.Where(point => newest.At - point.At <= TimeSpan.FromSeconds(15)),
            LiveMonitorTrendDuration.Seconds30 => history.Where(point => newest.At - point.At <= TimeSpan.FromSeconds(30)),
            LiveMonitorTrendDuration.Seconds60 => history.Where(point => newest.At - point.At <= TimeSpan.FromSeconds(60)),
            LiveMonitorTrendDuration.OneLap when newest.Lap.HasValue => history.Where(point => point.Lap == newest.Lap),
            LiveMonitorTrendDuration.ThreeLaps when newest.Lap.HasValue => history.Where(point => point.Lap >= newest.Lap - 2),
            _ => history
        };
        return selected.Select(point => metricId switch
        {
            "speed" => point.SpeedMph.HasValue ? Speed(point.SpeedMph.Value, unit) : null,
            "throttle" => point.Throttle * 100,
            "brake" => point.Brake * 100,
            "steering" => point.SteeringWheelAngleRadians.HasValue ? Steering(point.SteeringWheelAngleRadians.Value, unit) : null,
            "gear" => point.Gear,
            "rpm" => point.Rpm,
            "yaw-rate" => point.YawRateDegreesPerSecond.HasValue ? Yaw(point.YawRateDegreesPerSecond.Value, unit) : null,
            "lateral-acceleration" => point.LateralAccelerationG.HasValue ? unit == "m/s²" ? point.LateralAccelerationG * 9.80665 : point.LateralAccelerationG : null,
            "longitudinal-acceleration" => point.LongitudinalAccelerationG.HasValue ? unit == "m/s²" ? point.LongitudinalAccelerationG * 9.80665 : point.LongitudinalAccelerationG : null,
            _ => null
        }).Where(value => value.HasValue && double.IsFinite(value.Value)).Select(value => value!.Value).ToArray();
    }

    private static double Speed(double mph, string unit) => unit == "km/h" ? mph * 1.609344 : mph;
    private static double Temperature(double c, string unit) => unit == "°F" ? c * 9 / 5 + 32 : c;
    private static double Steering(double radians, string unit) => unit == "rad" ? radians : radians * 57.29577951308232;
    private static double Yaw(double degrees, string unit) => unit == "rad/s" ? degrees / 57.29577951308232 : degrees;
    private static string FormatLap(double seconds) => TimeSpan.FromSeconds(seconds).ToString(seconds >= 60 ? "m\\:ss\\.fff" : "s\\.fff", CultureInfo.CurrentCulture);
    private static bool Useful(string? value) => !string.IsNullOrWhiteSpace(value) && !value.Equals("Unavailable", StringComparison.OrdinalIgnoreCase);
}
