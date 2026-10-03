using System.Text.Json.Nodes;
using System.Text.RegularExpressions;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace Oracle;

public static class Json
{
    public static JsonNode? Num(double value) =>
        double.IsNaN(value) ? JsonValue.Create("NaN")
        : double.IsPositiveInfinity(value) ? JsonValue.Create("Infinity")
        : double.IsNegativeInfinity(value) ? JsonValue.Create("-Infinity")
        : JsonNode.Parse(value.ToString("R", System.Globalization.CultureInfo.InvariantCulture));

    public static JsonNode? From(object? value) => value switch
    {
        null => null,
        float f => Num(f),
        double d => Num(d),
        int i => JsonValue.Create(i),
        uint u => JsonValue.Create(u),
        long l => JsonValue.Create(l),
        bool b => JsonValue.Create(b),
        string s => JsonValue.Create(s),
        int[] a => new JsonArray(a.Select(x => (JsonNode?)JsonValue.Create(x)).ToArray()),
        float[] a => new JsonArray(a.Select(x => Num(x)).ToArray()),
        _ => throw new InvalidOperationException(value.GetType().Name),
    };
}

public static class Emit
{
    private static readonly string[] ExpectKeys =
    [
        "connected", "first_of_epoch", "session_epoch", "flag_state", "lap", "laps_remaining", "position", "class_position",
        "ahead_gap", "ahead_gap_trend", "behind_gap", "behind_gap_trend", "leader_gap", "last_lap", "leader_last_lap",
        "pace_delta_to_leader", "pace_band_min", "pace_band_max", "run_phase", "green_run_laps", "fuel_laps_remaining",
        "fuel_hard_limit_laps", "fuel", "fuel_pct", "mandatory_repair", "optional_repair", "on_pit_road", "brake_bias",
        "track_temperature", "air_temperature", "steering_road_wheel", "steering_ratio", "lap_dist_pct", "throttle", "brake",
        "gear", "rpm", "cue",
    ];

    public static Dictionary<string, JsonNode?> Sample(Scn scn, StepSpec step)
    {
        var d = new Dictionary<string, JsonNode?>();
        foreach (var def in Vars.All)
        {
            if (scn.Omit.Contains(def.Name)) continue;
            step.Values.TryGetValue(def.Name, out var value);
            d[def.Rust] = Json.From(value);
        }
        return d;
    }

    private static JsonNode? Opt(double? value) => value.HasValue ? Json.Num(value.Value) : null;
    private static JsonNode? Opt(int? value) => value.HasValue ? JsonValue.Create(value.Value) : null;

    public static Dictionary<string, JsonNode?> Expect(LiveMonitorState st, bool changed, uint rustEpoch)
    {
        var s = st.Snapshot;
        var d = ExpectKeys.ToDictionary(k => k, _ => (JsonNode?)null);
        d["connected"] = s.Connected;
        d["first_of_epoch"] = changed;
        d["session_epoch"] = rustEpoch;
        if (!s.Connected)
        {
            d["cue"] = Cue(s.PrimaryCue);
            return d;
        }
        d["flag_state"] = FlagName(s.Flag);
        d["lap"] = Opt(s.Lap);
        d["laps_remaining"] = Opt(s.LapsRemaining);
        d["position"] = Opt(s.OverallPosition);
        d["class_position"] = Opt(s.ClassPosition);
        d["ahead_gap"] = Opt(s.AheadGap.Seconds);
        d["ahead_gap_trend"] = s.AheadGap.Trend.ToString().ToLowerInvariant();
        d["behind_gap"] = Opt(s.BehindGap.Seconds);
        d["behind_gap_trend"] = s.BehindGap.Trend.ToString().ToLowerInvariant();
        d["leader_gap"] = s.OverallPosition == 1 ? Json.Num(0) : Opt(s.LeaderGap.Seconds);
        d["last_lap"] = Opt(s.LastLapSeconds);
        d["leader_last_lap"] = Opt(s.LeaderLastLapSeconds);
        d["pace_delta_to_leader"] = Opt(s.LastLapPaceDifferenceSeconds);
        d["pace_band_min"] = Opt(s.PaceTarget.MinimumSeconds);
        d["pace_band_max"] = Opt(s.PaceTarget.MaximumSeconds);
        d["run_phase"] = s.TirePhase switch { "Early run" => "early", "Middle run" => "middle", "Late run" => "late", _ => "unknown" };
        d["green_run_laps"] = Opt(s.GreenLapsOnTires);
        d["fuel_laps_remaining"] = Opt(s.FuelLapsRemaining);
        d["fuel_hard_limit_laps"] = Opt(s.Pit.FuelHardLimitLaps);
        d["fuel"] = Opt(s.FuelLiters);
        d["fuel_pct"] = Opt(s.FuelLevelPercent);
        d["mandatory_repair"] = Opt(s.MandatoryRepairSeconds);
        d["optional_repair"] = Opt(s.OptionalRepairSeconds);
        d["on_pit_road"] = s.OnPitRoad;
        d["brake_bias"] = Opt(s.BrakeBiasPercent);
        d["track_temperature"] = Opt(s.TrackTemperatureC);
        d["air_temperature"] = Opt(s.AirTemperatureC);
        d["steering_road_wheel"] = Opt(s.SteeringRoadWheelRadians);
        d["steering_ratio"] = Opt(s.SteeringRatio);
        d["lap_dist_pct"] = Opt(s.LapDistancePercent);
        d["throttle"] = Opt(s.Throttle);
        d["brake"] = Opt(s.Brake);
        d["gear"] = Opt(s.Gear);
        d["rpm"] = Opt(s.Rpm);
        d["cue"] = Cue(s.PrimaryCue);
        return d;
    }

    private static string? FlagName(string flag) => flag switch
    {
        "GREEN" => "green", "RACING" => "racing", "WHITE" => "white", "CHECKERED" => "checkered", "CAUTION" => "caution",
        "RED" => "red", "BLACK FLAG" => "black", "DISQUALIFIED" => "disqualified", "UNKNOWN" => "unknown", _ => null,
    };

    private static readonly (int Id, string Pattern)[] CuePatterns =
    [
        (1, "^Black flag: follow the recorded penalty instruction$"),
        (2, "^Tow active$"),
        (3, @"^Mandatory repair: (\d+) s remaining$"),
        (4, "^Fuel hard limit: pit now if rules permit$"),
        (5, @"^Fuel hard limit: (\d+) laps$"),
        (6, "^Race flag unavailable: flag-dependent coaching paused$"),
        (7, "^Caution: competitive gap trends paused$"),
        (8, "^Pit road: confirm service and speed$"),
        (9, @"^Gap ahead closing: (\d\.\d\d) s$"),
        (10, "^Last lap outside the clean in-session target band$"),
        (11, @"^Braking zone near (\d+)% lap: peak ([\d,]+)% above your clean baseline on (\d+) comparable laps$"),
        (12, "^Building your pace range · complete three clean laps$"),
        (13, "^No action: pace and race state within the supported band$"),
        (14, "^Waiting for iRacing$"),
        (15, "^Live coaching paused$"),
    ];

    private static JsonNode Cue(LiveDriverCue cue)
    {
        foreach (var (id, pattern) in CuePatterns)
        {
            if (!Regex.IsMatch(cue.Message, pattern)) continue;
            return new JsonObject
            {
                ["id"] = id,
                ["priority"] = (int)cue.Priority,
                ["evidence"] = cue.Evidence.ToString().ToLowerInvariant(),
                ["text"] = cue.Message,
            };
        }
        throw new InvalidOperationException("cue message matches no template: " + cue.Message);
    }

    private static string? WireUnit(string unit) => unit switch
    {
        "" or "time" => null,
        "°C" => "C",
        "°F" => "F",
        "m/s²" => "m/s2",
        "laps" => "lap",
        _ => unit,
    };

    /// <summary>The tile catalog as C# defines it, for the byte check of <c>live-channels.json</c> tiles.</summary>
    public static JsonArray Catalog()
    {
        var catalog = new JsonArray();
        foreach (var def in LiveTelemetryCatalog.All.OrderBy(d => d.Id, StringComparer.Ordinal))
        {
            catalog.Add(new JsonObject
            {
                ["id"] = def.Id,
                ["label"] = def.Name,
                ["units"] = new JsonArray(def.Units.Select(WireUnit).Where(u => u is not null).Select(u => (JsonNode?)u).ToArray()),
                ["default_style"] = def.DefaultStyle.ToString().ToLowerInvariant(),
                ["precision"] = def.DefaultPrecision,
                ["min"] = Opt(def.SemanticMinimum),
                ["max"] = Opt(def.SemanticMaximum),
                ["step"] = def.TrendShape == LiveMonitorTrendShape.Step,
            });
        }
        return catalog;
    }

    public static JsonArray Tiles(LiveMonitorState st)
    {
        var tiles = new JsonArray();
        foreach (var def in LiveTelemetryCatalog.All)
        {
            foreach (var unit in def.Units)
            {
                var reading = LiveTelemetryCatalog.Read(def.Id, st, unit.Length == 0 ? null : unit, null, LiveMonitorTrendDuration.Seconds30, true);
                var showUnit = !string.IsNullOrWhiteSpace(reading.Unit) && !string.Equals(reading.Unit, "time", StringComparison.OrdinalIgnoreCase);
                var primary = reading.Available && showUnit ? $"{reading.DisplayValue} {reading.Unit}" : reading.DisplayValue;
                var state = reading.Available ? "value"
                    : reading.DisplayValue == "Waiting" ? "waiting"
                    : reading.DisplayValue == "Insufficient evidence" ? "insufficient"
                    : "not_measured";
                var finite = reading.Available && reading.NumericValue.HasValue && double.IsFinite(reading.NumericValue.Value);
                tiles.Add(new JsonObject
                {
                    ["id"] = def.Id,
                    ["unit"] = WireUnit(unit),
                    ["primary"] = primary,
                    ["secondary"] = string.IsNullOrEmpty(reading.SecondaryValue) ? null : reading.SecondaryValue,
                    ["state"] = state,
                    ["value"] = reading.Available ? Opt(reading.NumericValue) : null,
                    ["range"] = finite ? new JsonArray(Json.Num(reading.Minimum!.Value), Json.Num(reading.Maximum!.Value)) : null,
                    ["trend"] = new JsonArray(reading.TrendValues.Select(v => Json.Num(v)).ToArray()),
                });
            }
        }
        return tiles;
    }
}
