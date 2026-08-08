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
    double? SemanticMaximum = null,
    LiveMonitorTrendShape TrendShape = LiveMonitorTrendShape.Continuous);

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

public sealed record LiveTelemetryTrendProjection(
    DateTimeOffset At,
    int? Lap,
    double? LapDistancePercent,
    double? Value);

public static class LiveTelemetryCatalog
{
    private static readonly IReadOnlyList<LiveMonitorTrendDuration> AllTrendDurations =
    [
        LiveMonitorTrendDuration.Seconds15,
        LiveMonitorTrendDuration.Seconds30,
        LiveMonitorTrendDuration.Seconds60,
        LiveMonitorTrendDuration.OneLap,
        LiveMonitorTrendDuration.ThreeLaps
    ];

    private static readonly IReadOnlyDictionary<string, LiveTelemetryMetricDefinition> ById = Build()
        .ToDictionary(item => item.Id, StringComparer.Ordinal);

    public static IReadOnlyList<LiveTelemetryMetricDefinition> All { get; } = ById.Values.OrderBy(item => item.Name, StringComparer.CurrentCultureIgnoreCase).ToArray();

    public static LiveTelemetryMetricDefinition Get(string id) => ById[id];
    public static bool TryGet(string id, out LiveTelemetryMetricDefinition definition) => ById.TryGetValue(id, out definition!);

    public static LiveTelemetryMetricReading Read(
        string metricId,
        LiveMonitorState state,
        string? requestedUnit = null,
        int? requestedPrecision = null,
        LiveMonitorTrendDuration duration = LiveMonitorTrendDuration.Seconds30,
        bool includeTrend = true)
    {
        var definition = Get(metricId);
        var snapshot = state.Snapshot;
        var unit = definition.Units.Contains(requestedUnit ?? string.Empty, StringComparer.Ordinal) ? requestedUnit! : definition.DefaultUnit;
        var precision = Math.Clamp(requestedPrecision ?? definition.DefaultPrecision, 0, 3);
        if (!snapshot.Connected)
            return Missing(unit, "Waiting for iRacing");

        var trend = includeTrend ? Trend(metricId, state.History ?? [], duration, unit) : [];
        var reading = metricId switch
        {
            "air-temperature" => Number(snapshot.AirTemperatureC, unit, precision, trend, value => Temperature(value, unit)),
            "ahead-gap" => Seconds(snapshot.AheadGap.Seconds, precision, snapshot.AheadGap.UnavailableReason, trend),
            "behind-gap" => Seconds(snapshot.BehindGap.Seconds, precision, snapshot.BehindGap.UnavailableReason, trend),
            "brake" => Percent(snapshot.Brake, precision, trend),
            "brake-bias" => Number(snapshot.BrakeBiasPercent, "%", precision, trend),
            "class-position" => Position(snapshot.ClassPosition, trend),
            "coach-cue" => Text(snapshot.PrimaryCue.Message, snapshot.PrimaryCue.Basis, (double)snapshot.PrimaryCue.Priority, 0, 100, trend),
            "flag" => Flag(snapshot, trend),
            "fuel" => Fuel(snapshot, unit, precision, trend),
            "fuel-laps" => Number(snapshot.FuelLapsRemaining, "laps", precision, trend, value => value),
            "gear" => Number(snapshot.Gear, string.Empty, 0, trend),
            "lap" => Number(snapshot.Lap, string.Empty, 0, trend, value => value, "Lap not measured yet."),
            "laps-remaining" => Number(snapshot.LapsRemaining, "laps", 0, trend),
            "last-lap" => LastLap(snapshot, trend),
            "lateral-acceleration" => Acceleration(snapshot.LateralAccelerationG, unit, precision, trend),
            "leader-gap" => snapshot.OverallPosition == 1 ? new(true, "Leader", string.Empty, string.Empty, 0, 0, null, string.Empty, trend) : Seconds(snapshot.LeaderGap.Seconds, precision, snapshot.LeaderGap.UnavailableReason, trend),
            "leader-last-lap" => LapTime(snapshot.LeaderLastLapSeconds, trend),
            "longitudinal-acceleration" => Acceleration(snapshot.LongitudinalAccelerationG, unit, precision, trend),
            "mandatory-repair" => Seconds(snapshot.MandatoryRepairSeconds, 0, "No mandatory repair workload is currently recorded.", trend),
            "on-pit-road" => Text(snapshot.OnPitRoad ? "On pit road" : "On track", "Recorded OnPitRoad state", snapshot.OnPitRoad ? 1 : 0, 0, 1, trend),
            "optional-repair" => Seconds(snapshot.OptionalRepairSeconds, 0, "No optional repair workload is currently recorded.", trend),
            "pace-range" => Pace(snapshot, trend),
            "pit-window" => Pit(snapshot, trend),
            "position" => Position(snapshot.OverallPosition, trend),
            "rpm" => Number(snapshot.Rpm, "rpm", 0, trend),
            "speed" => Number(snapshot.SpeedMph, unit, precision, trend, value => Speed(value, unit)),
            "steering" => Number(snapshot.SteeringWheelAngleRadians, unit, precision, trend, value => Steering(value, unit)),
            "throttle" => Percent(snapshot.Throttle, precision, trend),
            "tire-phase" => TirePhase(snapshot, trend),
            "track-temperature" => Number(snapshot.TrackTemperatureC, unit, precision, trend, value => Temperature(value, unit)),
            "yaw-rate" => Number(snapshot.YawRateDegreesPerSecond, unit, precision, trend, value => Yaw(value, unit)),
            _ => Missing(unit, "Not measured in this session")
        };

        return ApplyRange(definition, reading);
    }

    public static IReadOnlyList<LiveMonitorTrendDuration> TrendDurations(string metricId) => ById.ContainsKey(metricId) ? AllTrendDurations : [];

    public static IReadOnlyList<LiveTelemetryTrendProjection> ProjectTrend(
        string metricId,
        IReadOnlyList<LiveTracePoint> history,
        LiveMonitorTrendDuration duration,
        string? requestedUnit = null,
        int maximumPoints = 4096)
    {
        if (!ById.TryGetValue(metricId, out var definition) || history.Count == 0) return [];
        maximumPoints = Math.Clamp(maximumPoints, 5, 36_000);
        var unit = definition.Units.Contains(requestedUnit ?? string.Empty, StringComparer.Ordinal)
            ? requestedUnit!
            : definition.DefaultUnit;
        var start = TrendWindowStart(history, duration);
        var count = history.Count - start;
        if (count <= 0) return [];

        LiveTelemetryTrendProjection Project(int index)
        {
            var point = history[index];
            return new(point.At, point.Lap, point.LapDistancePercent, TrendValue(metricId, point, unit));
        }

        if (count <= maximumPoints)
        {
            var projected = new LiveTelemetryTrendProjection[count];
            for (var offset = 0; offset < count; offset++) projected[offset] = Project(start + offset);
            return projected;
        }

        // Chart seeds cross the WebView boundary only on mount or reconfigure.
        // Preserve first/last, extrema and a missing sample per time bucket so
        // brief peaks and truthful gaps survive in a strictly bounded payload.
        var bucketCount = Math.Max(1, maximumPoints / 5);
        var selectedIndices = new SortedSet<int>();
        for (var bucket = 0; bucket < bucketCount; bucket++)
        {
            var bucketStart = start + (int)((long)count * bucket / bucketCount);
            var bucketEnd = start + (int)((long)count * (bucket + 1) / bucketCount);
            if (bucketEnd <= bucketStart) continue;
            var minimumIndex = -1;
            var maximumIndex = -1;
            var missingIndex = -1;
            double minimum = 0;
            double maximum = 0;
            for (var index = bucketStart; index < bucketEnd; index++)
            {
                var value = TrendValue(metricId, history[index], unit);
                if (!value.HasValue || !double.IsFinite(value.Value))
                {
                    if (missingIndex < 0) missingIndex = index;
                    continue;
                }
                if (minimumIndex < 0 || value.Value < minimum) { minimum = value.Value; minimumIndex = index; }
                if (maximumIndex < 0 || value.Value > maximum) { maximum = value.Value; maximumIndex = index; }
            }
            selectedIndices.Add(bucketStart);
            selectedIndices.Add(bucketEnd - 1);
            if (minimumIndex >= 0) selectedIndices.Add(minimumIndex);
            if (maximumIndex >= 0) selectedIndices.Add(maximumIndex);
            if (missingIndex >= 0) selectedIndices.Add(missingIndex);
        }
        return selectedIndices.Take(maximumPoints).Select(Project).ToArray();
    }

    public static double? TrendValue(string metricId, LiveTracePoint point, string? requestedUnit = null)
    {
        if (!ById.TryGetValue(metricId, out var definition)) return null;
        var unit = definition.Units.Contains(requestedUnit ?? string.Empty, StringComparer.Ordinal)
            ? requestedUnit!
            : definition.DefaultUnit;
        return metricId switch
        {
            "air-temperature" => ConvertValue(point.Metrics.AirTemperatureC, value => Temperature(value, unit)),
            "ahead-gap" => point.Metrics.AheadGapSeconds,
            "behind-gap" => point.Metrics.BehindGapSeconds,
            "speed" => point.SpeedMph.HasValue ? Speed(point.SpeedMph.Value, unit) : null,
            "throttle" => point.Throttle * 100,
            "brake" => point.Brake * 100,
            "brake-bias" => point.Metrics.BrakeBiasPercent,
            "class-position" => point.Metrics.ClassPosition,
            "coach-cue" => Ordinal(point.Metrics.CoachCuePriority),
            "flag" => Ordinal(point.Metrics.FlagState),
            "fuel" => ConvertValue(point.Metrics.FuelLiters, value => unit == "US gal" ? value * 0.2641720524 : value),
            "fuel-laps" => point.Metrics.FuelLapsRemaining,
            "steering" => point.SteeringWheelAngleRadians.HasValue ? Steering(point.SteeringWheelAngleRadians.Value, unit) : null,
            "gear" => point.Gear,
            "lap" => point.Lap,
            "laps-remaining" => point.Metrics.LapsRemaining,
            "last-lap" => point.LastLapSeconds,
            "leader-gap" => point.Metrics.LeaderGapSeconds,
            "leader-last-lap" => point.Metrics.LeaderLastLapSeconds,
            "mandatory-repair" => point.Metrics.MandatoryRepairSeconds,
            "on-pit-road" => point.Metrics.OnPitRoad.HasValue ? point.Metrics.OnPitRoad.Value ? 1d : 0d : null,
            "optional-repair" => point.Metrics.OptionalRepairSeconds,
            "pace-range" => point.Metrics.PaceMidpointSeconds,
            "pit-window" => point.Metrics.PitWindowLaps,
            "position" => point.Metrics.OverallPosition,
            "rpm" => point.Rpm,
            "yaw-rate" => point.YawRateDegreesPerSecond.HasValue ? Yaw(point.YawRateDegreesPerSecond.Value, unit) : null,
            "lateral-acceleration" => point.LateralAccelerationG.HasValue ? unit.StartsWith("m/s", StringComparison.Ordinal) ? point.LateralAccelerationG * 9.80665 : point.LateralAccelerationG : null,
            "longitudinal-acceleration" => point.LongitudinalAccelerationG.HasValue ? unit.StartsWith("m/s", StringComparison.Ordinal) ? point.LongitudinalAccelerationG * 9.80665 : point.LongitudinalAccelerationG : null,
            "tire-phase" => Ordinal(point.Metrics.TirePhase),
            "track-temperature" => ConvertValue(point.Metrics.TrackTemperatureC, value => Temperature(value, unit)),
            _ => null
        };
    }

    private static IReadOnlyList<LiveTelemetryMetricDefinition> Build()
    {
        var all = new[] { LiveMonitorDisplayStyle.Number, LiveMonitorDisplayStyle.Gauge, LiveMonitorDisplayStyle.Bar, LiveMonitorDisplayStyle.Trend };
        var status = new[] { LiveMonitorDisplayStyle.Status, LiveMonitorDisplayStyle.Number, LiveMonitorDisplayStyle.Gauge, LiveMonitorDisplayStyle.Bar, LiveMonitorDisplayStyle.Trend };
        return
        [
            D("air-temperature", "Air temperature", "Recorded ambient temperature.", LiveMonitorMetricSource.Recorded, "°C", ["°C", "°F"], all, 1),
            D("ahead-gap", "Gap ahead", "Physical same-lap scoring interval to the car ahead.", LiveMonitorMetricSource.Calculated, "s", ["s"], all, 2, 0),
            D("behind-gap", "Gap behind", "Physical same-lap scoring interval to the car behind.", LiveMonitorMetricSource.Calculated, "s", ["s"], all, 2, 0),
            D("brake", "Brake", "Recorded brake-pedal input.", LiveMonitorMetricSource.Recorded, "%", ["%"], all, 0, 0, 100),
            D("brake-bias", "Brake bias", "Recorded in-car brake-bias setting when the car exposes it.", LiveMonitorMetricSource.Recorded, "%", ["%"], all, 1, 40, 70),
            D("class-position", "Class position", "Recorded class scoring position.", LiveMonitorMetricSource.Recorded, string.Empty, [string.Empty], all, 0, 1),
            D("coach-cue", "Coach cue", "Highest-priority local safe-glance instruction.", LiveMonitorMetricSource.Coach, string.Empty, [string.Empty], status, 0),
            D("flag", "Flag state", "Recorded iRacing race-control state.", LiveMonitorMetricSource.Recorded, string.Empty, [string.Empty], status, 0),
            DWithDefault("fuel", "Fuel remaining", "Recorded fuel with locally calculated lap estimate.", LiveMonitorMetricSource.Calculated, "US gal", ["US gal", "L"], all, LiveMonitorDisplayStyle.Bar, 1, 0),
            D("fuel-laps", "Fuel laps remaining", "Estimated laps from measured fuel-use history.", LiveMonitorMetricSource.Calculated, "laps", ["laps"], all, 1, 0),
            DWithDefault("gear", "Gear", "Recorded selected gear.", LiveMonitorMetricSource.Recorded, string.Empty, [string.Empty], all, LiveMonitorDisplayStyle.Number, 0, -1, 12),
            D("lap", "Lap", "Current recorded player lap.", LiveMonitorMetricSource.Recorded, string.Empty, [string.Empty], all, 0, 0),
            D("laps-remaining", "Laps remaining", "Recorded session laps remaining.", LiveMonitorMetricSource.Recorded, "laps", ["laps"], all, 0, 0),
            D("last-lap", "Last lap", "Last completed lap with leader-lap delta when available.", LiveMonitorMetricSource.Calculated, "time", ["time"], all, 3, 0),
            DWithDefault("lateral-acceleration", "Lateral acceleration", "Recorded lateral acceleration.", LiveMonitorMetricSource.Recorded, "g", ["g", "m/s²"], all, LiveMonitorDisplayStyle.Trend, 2),
            D("leader-gap", "Physical gap to leader", "Physical same-lap scoring interval to the overall leader.", LiveMonitorMetricSource.Calculated, "s", ["s"], all, 2, 0),
            D("leader-last-lap", "Leader last lap", "Last recorded lap time for the overall leader.", LiveMonitorMetricSource.Recorded, "time", ["time"], all, 3, 0),
            DWithDefault("longitudinal-acceleration", "Longitudinal acceleration", "Recorded longitudinal acceleration.", LiveMonitorMetricSource.Recorded, "g", ["g", "m/s²"], all, LiveMonitorDisplayStyle.Trend, 2),
            D("mandatory-repair", "Mandatory repair", "Recorded mandatory repair countdown.", LiveMonitorMetricSource.Recorded, "s", ["s"], all, 0, 0),
            D("on-pit-road", "Pit-road state", "Recorded pit-road state.", LiveMonitorMetricSource.Recorded, string.Empty, [string.Empty], status, 0),
            D("optional-repair", "Optional repair", "Recorded optional repair countdown.", LiveMonitorMetricSource.Recorded, "s", ["s"], all, 0, 0),
            D("pace-range", "Personal pace range", "Clean in-session pace band when enough evidence exists.", LiveMonitorMetricSource.Coach, "time", ["time"], all, 3, 0),
            D("pit-window", "Laps to pit", "Strategic window when supported, otherwise the fuel hard limit.", LiveMonitorMetricSource.Coach, "laps", ["laps"], all, 0, 0),
            D("position", "Position", "Recorded overall scoring position.", LiveMonitorMetricSource.Recorded, string.Empty, [string.Empty], all, 0, 1),
            DWithDefault("rpm", "RPM", "Recorded engine speed.", LiveMonitorMetricSource.Recorded, "rpm", ["rpm"], all, LiveMonitorDisplayStyle.Trend, 0, 0),
            DWithDefault("speed", "Speed", "Recorded vehicle speed.", LiveMonitorMetricSource.Recorded, "mph", ["mph", "km/h"], all, LiveMonitorDisplayStyle.Trend, 1, 0),
            DWithDefault("steering", "Steering", "Recorded steering-wheel angle.", LiveMonitorMetricSource.Recorded, "deg", ["deg", "rad"], all, LiveMonitorDisplayStyle.Trend, 1),
            DWithDefault("throttle", "Throttle", "Recorded throttle-pedal input.", LiveMonitorMetricSource.Recorded, "%", ["%"], all, LiveMonitorDisplayStyle.Gauge, 0, 0, 100),
            D("tire-phase", "Run phase", "Early, middle, or late segment derived from observed clean laps since pit-road entry; tire service is not confirmed.", LiveMonitorMetricSource.Coach, string.Empty, [string.Empty], status, 0),
            D("track-temperature", "Track temperature", "Recorded track temperature.", LiveMonitorMetricSource.Recorded, "°C", ["°C", "°F"], all, 1),
            DWithDefault("yaw-rate", "Yaw rate", "Recorded yaw rate.", LiveMonitorMetricSource.Recorded, "deg/s", ["deg/s", "rad/s"], all, LiveMonitorDisplayStyle.Trend, 1)
        ];
    }

    private static LiveTelemetryMetricDefinition D(string id, string name, string description, LiveMonitorMetricSource source, string defaultUnit, IReadOnlyList<string> units, IReadOnlyList<LiveMonitorDisplayStyle> styles, int precision, double? min = null, double? max = null) =>
        new(id, name, description, source, defaultUnit, units, styles, styles[0], precision, min, max, TrendShape(id));

    private static LiveTelemetryMetricDefinition DWithDefault(string id, string name, string description, LiveMonitorMetricSource source, string defaultUnit, IReadOnlyList<string> units, IReadOnlyList<LiveMonitorDisplayStyle> styles, LiveMonitorDisplayStyle defaultStyle, int precision, double? min = null, double? max = null) =>
        new(id, name, description, source, defaultUnit, units, styles, defaultStyle, precision, min, max, TrendShape(id));

    private static LiveTelemetryMetricReading Fuel(LiveRaceSnapshot snapshot, string unit, int precision, IReadOnlyList<double> trend)
    {
        if (!snapshot.FuelLiters.HasValue) return Missing(unit, "Fuel level not measured in this session");
        var value = unit == "US gal" ? snapshot.FuelLiters.Value * 0.2641720524 : snapshot.FuelLiters.Value;
        var secondary = snapshot.FuelLapsRemaining.HasValue ? $"{snapshot.FuelLapsRemaining.Value:0.0} estimated laps" : "Lap estimate needs clean fuel-use evidence";
        double? capacity = snapshot.FuelLevelPercent is > .0001
            ? value / snapshot.FuelLevelPercent.Value
            : null;
        return new(true, value.ToString($"F{precision}", CultureInfo.CurrentCulture), secondary, unit,
            value, 0, capacity,
            snapshot.FuelLevelPercent.HasValue ? string.Empty : "Fuel percentage not measured; range follows recorded amount.", trend);
    }

    private static LiveTelemetryMetricReading Flag(LiveRaceSnapshot snapshot, IReadOnlyList<double> trend)
    {
        var state = LiveTelemetryMetricEncoding.Flag(snapshot.Flag);
        return state.HasValue
            ? Text(snapshot.Flag, snapshot.Flag == "RACING" ? "Recorded session state" : "iRacing race status", Ordinal(state), 0, 7, trend)
            : Missing(string.Empty, "Flag state not measured in this session");
    }

    private static LiveTelemetryMetricReading TirePhase(LiveRaceSnapshot snapshot, IReadOnlyList<double> trend)
    {
        var phase = LiveTelemetryMetricEncoding.TirePhase(snapshot.TirePhase);
        return phase.HasValue
            ? Text(snapshot.TirePhase, "Observed clean laps since pit road", Ordinal(phase), 0, 2, trend)
            : Missing(string.Empty, "Insufficient evidence for tire phase");
    }

    private static LiveTelemetryMetricReading LastLap(LiveRaceSnapshot snapshot, IReadOnlyList<double> trend)
    {
        if (!snapshot.LastLapSeconds.HasValue) return Missing("time", "No completed lap measured yet");
        var secondary = snapshot.LastLapPaceDifferenceSeconds.HasValue
            ? $"{snapshot.LastLapPaceDifferenceSeconds.Value:+0.000;-0.000;0.000} s vs leader last lap"
            : "Leader comparison not measured";
        return new(true, FormatLap(snapshot.LastLapSeconds.Value), secondary, "time", snapshot.LastLapSeconds, null, null, string.Empty, trend);
    }

    private static LiveTelemetryMetricReading Pace(LiveRaceSnapshot snapshot, IReadOnlyList<double> trend)
    {
        if (!snapshot.PaceTarget.MinimumSeconds.HasValue || !snapshot.PaceTarget.MaximumSeconds.HasValue)
            return Missing("time", "Insufficient evidence for a clean personal pace range");
        var midpoint = (snapshot.PaceTarget.MinimumSeconds.Value + snapshot.PaceTarget.MaximumSeconds.Value) / 2;
        return new(true, $"{FormatLap(snapshot.PaceTarget.MinimumSeconds.Value)}–{FormatLap(snapshot.PaceTarget.MaximumSeconds.Value)}", snapshot.PaceTarget.TirePhase, "time", midpoint, null, null, string.Empty, trend);
    }

    private static LiveTelemetryMetricReading Pit(LiveRaceSnapshot snapshot, IReadOnlyList<double> trend)
    {
        if (snapshot.Pit.WindowOpensInLaps.HasValue && snapshot.Pit.WindowClosesInLaps.HasValue)
            return new(true, $"{snapshot.Pit.WindowOpensInLaps}–{snapshot.Pit.WindowClosesInLaps}", "Strategic pit window", "laps", snapshot.Pit.WindowOpensInLaps, null, null, string.Empty, trend);
        if (snapshot.Pit.FuelHardLimitLaps.HasValue)
            return new(true, snapshot.Pit.FuelHardLimitLaps.Value.ToString(CultureInfo.CurrentCulture), "Fuel hard limit; strategic window not established", "laps", snapshot.Pit.FuelHardLimitLaps, null, null, snapshot.Pit.UnavailableReason, trend);
        return Missing("laps", "Insufficient evidence for pit guidance");
    }

    private static LiveTelemetryMetricReading Position(int? value, IReadOnlyList<double> trend) => value is > 0
        ? new(true, $"P{value}", string.Empty, string.Empty, value, null, null, string.Empty, trend)
        : Missing(string.Empty, "Position not measured in this session");

    private static LiveTelemetryMetricReading LapTime(double? value, IReadOnlyList<double> trend) => value is > 0
        ? new(true, FormatLap(value.Value), string.Empty, "time", value, null, null, string.Empty, trend)
        : Missing("time", "No completed lap measured yet");

    private static LiveTelemetryMetricReading Seconds(double? value, int precision, string missing, IReadOnlyList<double> trend) => value.HasValue
        ? new(true, value.Value.ToString($"F{precision}", CultureInfo.CurrentCulture), string.Empty, "s", value, null, null, string.Empty, trend)
        : Missing("s", missing);

    private static LiveTelemetryMetricReading Text(string value, string secondary, double? numeric, double? minimum, double? maximum, IReadOnlyList<double> trend) => Useful(value)
        ? new(true, value, secondary, string.Empty, numeric, minimum, maximum, string.Empty, trend)
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

    private static LiveTelemetryMetricReading ApplyRange(LiveTelemetryMetricDefinition definition, LiveTelemetryMetricReading reading)
    {
        if (!reading.Available || !reading.NumericValue.HasValue || !double.IsFinite(reading.NumericValue.Value)) return reading;

        var finite = reading.TrendValues.Where(double.IsFinite).Append(reading.NumericValue.Value).ToArray();
        var observedMinimum = finite.Min();
        var observedMaximum = finite.Max();
        var span = observedMaximum - observedMinimum;
        var padding = span > .0001 ? span * .08 : Math.Max(1, Math.Abs(observedMaximum) * .08);
        var minimum = reading.Minimum ?? definition.SemanticMinimum ?? observedMinimum - padding;
        var maximum = reading.Maximum ?? definition.SemanticMaximum ?? observedMaximum + padding;
        if (maximum <= minimum)
        {
            var fallback = Math.Max(1, Math.Abs(reading.NumericValue.Value) * .08);
            if (reading.Maximum is null && definition.SemanticMaximum is null) maximum = minimum + fallback;
            else if (reading.Minimum is null && definition.SemanticMinimum is null) minimum = maximum - fallback;
        }
        return reading with { Minimum = minimum, Maximum = maximum };
    }

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
        return selected.TakeLast(4096).Select(point => metricId switch
        {
            "air-temperature" => ConvertValue(point.Metrics.AirTemperatureC, value => Temperature(value, unit)),
            "ahead-gap" => point.Metrics.AheadGapSeconds,
            "behind-gap" => point.Metrics.BehindGapSeconds,
            "speed" => point.SpeedMph.HasValue ? Speed(point.SpeedMph.Value, unit) : null,
            "throttle" => point.Throttle * 100,
            "brake" => point.Brake * 100,
            "brake-bias" => point.Metrics.BrakeBiasPercent,
            "class-position" => point.Metrics.ClassPosition,
            "coach-cue" => Ordinal(point.Metrics.CoachCuePriority),
            "flag" => Ordinal(point.Metrics.FlagState),
            "fuel" => ConvertValue(point.Metrics.FuelLiters, value => unit == "US gal" ? value * 0.2641720524 : value),
            "fuel-laps" => point.Metrics.FuelLapsRemaining,
            "steering" => point.SteeringWheelAngleRadians.HasValue ? Steering(point.SteeringWheelAngleRadians.Value, unit) : null,
            "gear" => point.Gear,
            "lap" => point.Lap,
            "laps-remaining" => point.Metrics.LapsRemaining,
            "last-lap" => point.LastLapSeconds,
            "leader-gap" => point.Metrics.LeaderGapSeconds,
            "leader-last-lap" => point.Metrics.LeaderLastLapSeconds,
            "mandatory-repair" => point.Metrics.MandatoryRepairSeconds,
            "on-pit-road" => point.Metrics.OnPitRoad.HasValue ? point.Metrics.OnPitRoad.Value ? 1d : 0d : null,
            "optional-repair" => point.Metrics.OptionalRepairSeconds,
            "pace-range" => point.Metrics.PaceMidpointSeconds,
            "pit-window" => point.Metrics.PitWindowLaps,
            "position" => point.Metrics.OverallPosition,
            "rpm" => point.Rpm,
            "yaw-rate" => point.YawRateDegreesPerSecond.HasValue ? Yaw(point.YawRateDegreesPerSecond.Value, unit) : null,
            "lateral-acceleration" => point.LateralAccelerationG.HasValue ? unit == "m/s²" ? point.LateralAccelerationG * 9.80665 : point.LateralAccelerationG : null,
            "longitudinal-acceleration" => point.LongitudinalAccelerationG.HasValue ? unit == "m/s²" ? point.LongitudinalAccelerationG * 9.80665 : point.LongitudinalAccelerationG : null,
            "tire-phase" => Ordinal(point.Metrics.TirePhase),
            "track-temperature" => ConvertValue(point.Metrics.TrackTemperatureC, value => Temperature(value, unit)),
            _ => null
        }).Where(value => value.HasValue && double.IsFinite(value.Value)).Select(value => value!.Value).ToArray();
    }

    private static int TrendWindowStart(IReadOnlyList<LiveTracePoint> history, LiveMonitorTrendDuration duration)
    {
        var newest = history[^1];
        if (duration is LiveMonitorTrendDuration.Seconds15 or LiveMonitorTrendDuration.Seconds30 or LiveMonitorTrendDuration.Seconds60)
        {
            var seconds = duration == LiveMonitorTrendDuration.Seconds15 ? 15 : duration == LiveMonitorTrendDuration.Seconds30 ? 30 : 60;
            var cutoff = newest.At - TimeSpan.FromSeconds(seconds);
            var start = history.Count - 1;
            while (start > 0 && history[start - 1].At >= cutoff) start--;
            return start;
        }

        var newestProgress = LapProgress(newest);
        if (!newestProgress.HasValue) return 0;
        var lapWindow = duration == LiveMonitorTrendDuration.ThreeLaps ? 3 : 1;
        var progressCutoff = newestProgress.Value - lapWindow;
        var result = history.Count - 1;
        var laterProgress = newestProgress.Value;
        while (result > 0)
        {
            var priorProgress = LapProgress(history[result - 1]);
            if (!priorProgress.HasValue) { result--; continue; }
            if (priorProgress.Value > laterProgress + .25 || priorProgress.Value < progressCutoff) break;
            laterProgress = priorProgress.Value;
            result--;
        }
        return result;
    }

    private static double? LapProgress(LiveTracePoint point)
    {
        if (!point.Lap.HasValue) return null;
        var distance = point.LapDistancePercent;
        if (!distance.HasValue || !double.IsFinite(distance.Value)) return point.Lap.Value;
        var fraction = distance.Value > 1 ? distance.Value / 100d : distance.Value;
        return point.Lap.Value + Math.Clamp(fraction, 0, 1);
    }

    private static LiveMonitorTrendShape TrendShape(string metricId) => metricId switch
    {
        "class-position" or "coach-cue" or "flag" or "gear" or "lap" or "laps-remaining" or "last-lap" or
        "leader-last-lap" or "on-pit-road" or "pit-window" or "position" or "tire-phase" => LiveMonitorTrendShape.Step,
        _ => LiveMonitorTrendShape.Continuous
    };

    private static double? ConvertValue(double? value, Func<double, double> converter) => value.HasValue ? converter(value.Value) : null;
    private static double? Ordinal<T>(T? value) where T : struct, Enum => value.HasValue ? System.Convert.ToDouble(value.Value, CultureInfo.InvariantCulture) : null;

    private static double Speed(double mph, string unit) => unit == "km/h" ? mph * 1.609344 : mph;
    private static double Temperature(double c, string unit) => unit == "°F" ? c * 9 / 5 + 32 : c;
    private static double Steering(double radians, string unit) => unit == "rad" ? radians : radians * 57.29577951308232;
    private static double Yaw(double degrees, string unit) => unit == "rad/s" ? degrees / 57.29577951308232 : degrees;
    private static string FormatLap(double seconds) => TimeSpan.FromSeconds(seconds).ToString(seconds >= 60 ? "m\\:ss\\.fff" : "s\\.fff", CultureInfo.CurrentCulture);
    private static bool Useful(string? value) => !string.IsNullOrWhiteSpace(value) && !value.Equals("Unavailable", StringComparison.OrdinalIgnoreCase);
}

internal static class LiveTelemetryMetricEncoding
{
    public static LiveFlagTrendState? Flag(string? value)
    {
        if (string.IsNullOrWhiteSpace(value) || value.Contains("waiting", StringComparison.OrdinalIgnoreCase) || value.Contains("unavailable", StringComparison.OrdinalIgnoreCase)) return null;
        if (value.Contains("black", StringComparison.OrdinalIgnoreCase)) return LiveFlagTrendState.Black;
        if (value.Contains("check", StringComparison.OrdinalIgnoreCase)) return LiveFlagTrendState.Checkered;
        if (value.Contains("red", StringComparison.OrdinalIgnoreCase)) return LiveFlagTrendState.Red;
        if (value.Contains("yellow", StringComparison.OrdinalIgnoreCase) || value.Contains("caution", StringComparison.OrdinalIgnoreCase)) return LiveFlagTrendState.Yellow;
        if (value.Contains("white", StringComparison.OrdinalIgnoreCase)) return LiveFlagTrendState.White;
        if (value.Contains("blue", StringComparison.OrdinalIgnoreCase)) return LiveFlagTrendState.Blue;
        if (value.Contains("green", StringComparison.OrdinalIgnoreCase) || value.Contains("racing", StringComparison.OrdinalIgnoreCase)) return LiveFlagTrendState.Green;
        return LiveFlagTrendState.Other;
    }

    public static LiveTirePhaseTrendState? TirePhase(string? value)
    {
        if (string.IsNullOrWhiteSpace(value) || value.Contains("unavailable", StringComparison.OrdinalIgnoreCase) || value.Contains("insufficient", StringComparison.OrdinalIgnoreCase)) return null;
        if (value.Contains("early", StringComparison.OrdinalIgnoreCase)) return LiveTirePhaseTrendState.Early;
        if (value.Contains("middle", StringComparison.OrdinalIgnoreCase) || value.Contains("mid", StringComparison.OrdinalIgnoreCase)) return LiveTirePhaseTrendState.Middle;
        if (value.Contains("late", StringComparison.OrdinalIgnoreCase)) return LiveTirePhaseTrendState.Late;
        return null;
    }
}
