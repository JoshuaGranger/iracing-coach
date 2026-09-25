using System.Text.Json;
using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

/// <summary>
/// Reads a review_test_day response (test-day-review-v1). It never derives a
/// verdict, window or suggestion: a required field that is missing, or present
/// with the wrong type, fails with the field's path. Fields the schema allows to
/// be null stay null ("Not measured"), never zero. The whole response is kept
/// in <see cref="TestDayReview.Source"/> so unknown optional fields survive.
/// </summary>
public static class TestDayMapper
{
    private static readonly string[] Corners = ["LF", "RF", "LR", "RR"];

    public static TestDayReview Map(JsonElement response)
    {
        if (response.ValueKind != JsonValueKind.Object)
            throw new JsonException("The test-day review is not an object.");
        var contract = RequiredText(response, "contract", "$");
        if (!string.Equals(contract, TestDayReview.Contract, StringComparison.Ordinal))
            throw new JsonException($"The test-day review uses contract {contract}; this app reads {TestDayReview.Contract}. Update the app or the backend so they match.");
        var schema = RequiredInt(response, "schema_version", "$");
        if (schema != TestDayReview.CurrentSchemaVersion)
            throw new JsonException($"The test-day review uses schema version {schema}; this app reads version {TestDayReview.CurrentSchemaVersion}.");
        if (!RequiredBoolean(response, "ok", "$"))
            throw new JsonException("The backend did not confirm the test-day review.");
        if (!RequiredBoolean(response, "read_only", "$"))
            throw new JsonException("The test-day review must be read-only.");

        var runs = Array(response, "runs", "$").Select((run, index) => Run(run, $"runs[{index}]")).ToArray();
        var runCount = RequiredInt(response, "run_count", "$");
        if (runCount != runs.Length)
            throw new JsonException($"The test-day review lists {runs.Length} runs but reports run_count {runCount}.");
        var car = RequiredObject(response, "car", "$");
        var track = RequiredObject(response, "track", "$");
        return new TestDayReview(
            schema,
            OptionalText(response, "test_day_id", "$"),
            runCount,
            new TestDayCar(OptionalText(car, "car_path", "car"), OptionalText(car, "car_name", "car"), OptionalScalar(car, "car_id", "car")),
            new TestDayTrack(OptionalText(track, "track_name", "track"), OptionalText(track, "track_config", "track"),
                OptionalText(track, "configuration_key", "track")),
            Windows(RequiredObject(response, "windows", "$")),
            Screening(RequiredObject(response, "screening", "$")),
            runs,
            NextChange(RequiredObject(response, "next_change", "$")),
            Strings(response, "limitations", "$"),
            response.Clone())
        {
            UntimedRecordings = ArrayIfPresent(response, "untimed_recordings", "$").Select((item, index) =>
            {
                var at = $"untimed_recordings[{index}]";
                return new TestDayUntimedRecording(RequiredInt(item, "recording", at), OptionalText(item, "analysis_id", at),
                    OptionalText(item, "analysis_path", at), OptionalText(item, "recorded_at", at), OptionalText(item, "session_type", at),
                    OptionalText(item, "setup_name", at), RequiredText(item, "reason", at));
            }).ToArray(),
            ShortRuns = ArrayIfPresent(response, "short_runs", "$").Select((item, index) =>
            {
                var at = $"short_runs[{index}]";
                return new TestDayShortRun(RequiredInt(item, "recording", at), OptionalText(item, "analysis_id", at),
                    OptionalText(item, "analysis_path", at), OptionalText(item, "recorded_at", at), OptionalText(item, "session_type", at),
                    OptionalText(item, "setup_name", at), OptionalInt(item, "run_in_recording", at), RequiredInt(item, "runs_in_recording", at),
                    RequiredInt(item, "first_lap", at), RequiredInt(item, "last_lap", at), RequiredInt(item, "run_laps", at),
                    RequiredInt(item, "clean_lap_count", at), RequiredText(item, "reason", at));
            }).ToArray()
        };
    }

    private static TestDayWindows Windows(JsonElement item)
    {
        var late = RequiredObject(item, "late", "windows");
        return new(Span(RequiredObject(item, "early", "windows"), "windows.early"),
            new TestDayLateWindow(RequiredText(late, "status", "windows.late"), OptionalInt(late, "first_run_lap", "windows.late"),
                OptionalInt(late, "last_run_lap", "windows.late"), OptionalText(late, "label", "windows.late"),
                OptionalText(late, "reason", "windows.late"), OptionalText(late, "basis", "windows.late")),
            Span(RequiredObject(item, "fall_off", "windows"), "windows.fall_off"),
            RequiredInt(item, "minimum_clean_laps", "windows"));
    }

    private static TestDaySpan Span(JsonElement item, string path) =>
        new(OptionalInt(item, "first_run_lap", path), OptionalInt(item, "last_run_lap", path), OptionalText(item, "label", path));

    private static TestDayScreening Screening(JsonElement item) =>
        new(RequiredText(item, "rule", "screening"), Ints(item, "offline_flag_rule_runs", "screening"),
            OptionalText(item, "offline_flag_rule", "screening"));

    private static TestDayRun Run(JsonElement item, string path)
    {
        if (item.ValueKind != JsonValueKind.Object) throw new JsonException($"Invalid test-day run: {path}.");
        var setup = RequiredObject(item, "setup", path);
        var measured = NullableObject(item, "measured_run", path);
        var conditions = RequiredObject(item, "conditions", path);
        var tireStart = RequiredObject(conditions, "tire_start", $"{path}.conditions");
        var trend = NullableObject(item, "trend", path);
        var recording = item.TryGetProperty("recording", out _) ? RequiredObject(item, "recording", path) : default;
        var noise = item.TryGetProperty("noise", out _) ? NullableObject(item, "noise", path) : default;
        RaceRunView? trendView;
        try { trendView = trend.ValueKind == JsonValueKind.Object ? RaceInvestigationMapper.RunView(trend) : null; }
        catch (JsonException exception) { throw new JsonException($"Invalid test-day trend at {path}.trend: {exception.Message}", exception); }
        return new TestDayRun(
            RequiredInt(item, "run", path),
            OptionalText(item, "analysis_id", path),
            OptionalText(item, "analysis_path", path),
            OptionalText(item, "recorded_at", path),
            OptionalText(item, "session_type", path),
            OptionalText(item, "test_day_id", path),
            OptionalInt(item, "test_day_run", path),
            RequiredBoolean(item, "offline_flag_rule_applied", path),
            new TestDaySetup(OptionalText(setup, "name", $"{path}.setup"), OptionalBoolean(setup, "fixed", $"{path}.setup"),
                RequiredBoolean(setup, "embedded", $"{path}.setup"), OptionalText(setup, "garage_fingerprint", $"{path}.setup")),
            measured.ValueKind == JsonValueKind.Object
                ? new TestDayMeasuredRun(OptionalInt(measured, "analysis_run", $"{path}.measured_run"), RequiredInt(measured, "first_lap", $"{path}.measured_run"),
                    RequiredInt(measured, "last_lap", $"{path}.measured_run"), RequiredInt(measured, "run_laps", $"{path}.measured_run"),
                    RequiredInt(measured, "other_run_count", $"{path}.measured_run"))
                {
                    CleanRunLaps = measured.TryGetProperty("clean_run_laps", out _) ? OptionalInt(measured, "clean_run_laps", $"{path}.measured_run") : null
                }
                : null,
            new TestDayConditions(OptionalNumber(conditions, "track_temp_f", $"{path}.conditions"),
                OptionalNumber(conditions, "start_fuel_gal", $"{path}.conditions"),
                OptionalText(conditions, "tire_compound", $"{path}.conditions"),
                new TestDayTireStart(RequiredText(tireStart, "status", $"{path}.conditions.tire_start"),
                    NumberMap(NullableObject(tireStart, "initial_odometer_m", $"{path}.conditions.tire_start"), $"{path}.conditions.tire_start.initial_odometer_m"),
                    OptionalText(tireStart, "detail", $"{path}.conditions.tire_start"))),
            Window(RequiredObject(item, "early", path), $"{path}.early"),
            Window(RequiredObject(item, "late", path), $"{path}.late"),
            FallOff(RequiredObject(item, "fall_off", path), $"{path}.fall_off"),
            SetupChange(RequiredObject(item, "setup_change", path), $"{path}.setup_change"),
            Comparison(NullableObject(item, "comparison", path), $"{path}.comparison"),
            Tires(RequiredObject(item, "tires", path), $"{path}.tires"),
            Array(item, "laps", path).Select((lap, index) => Lap(lap, $"{path}.laps[{index}]")).ToArray(),
            trendView)
        {
            Recording = recording.ValueKind != JsonValueKind.Object ? null : new TestDayRecording(
                RequiredInt(recording, "number", $"{path}.recording"), OptionalText(recording, "analysis_id", $"{path}.recording"),
                OptionalText(recording, "analysis_path", $"{path}.recording"), OptionalText(recording, "recorded_at", $"{path}.recording"),
                OptionalInt(recording, "run_in_recording", $"{path}.recording"), RequiredInt(recording, "runs_in_recording", $"{path}.recording")),
            Noise = Noise(noise, $"{path}.noise")
        };
    }

    private static TestDayNoise? Noise(JsonElement item, string path) => item.ValueKind != JsonValueKind.Object ? null : new(
        RequiredText(item, "status", path), OptionalNumber(item, "noise_s", path),
        item.TryGetProperty("difference_count", out _) ? RequiredInt(item, "difference_count", path) : 0,
        item.TryGetProperty("basis", out _) ? OptionalText(item, "basis", path) : null,
        item.TryGetProperty("reason", out _) ? OptionalText(item, "reason", path) : null)
    {
        DifferenceNoiseSeconds = NumberIfPresent(item, "difference_noise_s", path),
        ResidualNoiseSeconds = NumberIfPresent(item, "residual_noise_s", path),
        ResidualBasis = item.TryGetProperty("residual_basis", out _) ? OptionalText(item, "residual_basis", path) : null,
        Autocorrelation = NumberIfPresent(item, "autocorrelation", path)
    };

    private static TestDayPairWindow? PairWindow(JsonElement item, string path) => item.ValueKind != JsonValueKind.Object ? null : new(
        RequiredText(item, "key", path), RequiredText(item, "status", path), OptionalText(item, "label", path),
        OptionalInt(item, "first_run_lap", path), OptionalInt(item, "last_run_lap", path), OptionalText(item, "verdict", path),
        Ints(item, "run_laps", path), OptionalNumber(item, "previous_median_s", path), OptionalNumber(item, "current_median_s", path),
        OptionalNumber(item, "delta_s", path), OptionalNumber(item, "threshold_s", path), OptionalText(item, "reason", path))
    {
        StatisticalThresholdSeconds = NumberIfPresent(item, "statistical_threshold_s", path),
        EffectiveLapCount = NumberIfPresent(item, "effective_lap_count", path),
        SameSetupSpread = SameSetupSpread(ObjectIfPresent(item, "same_setup_spread", path), $"{path}.same_setup_spread")
    };

    private static TestDayPairFallOff? PairFallOff(JsonElement item, string path) => item.ValueKind != JsonValueKind.Object ? null : new(
        RequiredText(item, "status", path), OptionalText(item, "label", path), OptionalNumber(item, "previous_slope_s_per_lap", path),
        OptionalNumber(item, "current_slope_s_per_lap", path), OptionalNumber(item, "delta_s_per_lap", path),
        OptionalNumber(item, "threshold_s_per_lap", path), OptionalText(item, "verdict", path), OptionalText(item, "reason", path))
    {
        StatisticalThresholdSecondsPerLap = NumberIfPresent(item, "statistical_threshold_s_per_lap", path),
        SameSetupSpread = SameSetupSpread(ObjectIfPresent(item, "same_setup_spread", path), $"{path}.same_setup_spread")
    };

    private static TestDaySameSetupSpread? SameSetupSpread(JsonElement item, string path) => item.ValueKind != JsonValueKind.Object ? null : new(
        NumberIfPresent(item, "spread_s", path), NumberIfPresent(item, "spread_s_per_lap", path), IntsIfPresent(item, "runs", path),
        item.TryGetProperty("pair_count", out _) ? RequiredInt(item, "pair_count", path) : 0);

    /// <summary>An optional number added after v1 shipped: absent is null; present must be a finite number or null.</summary>
    private static double? NumberIfPresent(JsonElement item, string name, string path) =>
        item.ValueKind == JsonValueKind.Object && item.TryGetProperty(name, out _) ? OptionalNumber(item, name, path) : null;

    /// <summary>An optional object added after v1 shipped: absent or null is default; present must be an object.</summary>
    private static JsonElement ObjectIfPresent(JsonElement item, string name, string path) =>
        item.ValueKind == JsonValueKind.Object && item.TryGetProperty(name, out _) ? NullableObject(item, name, path) : default;

    private static TestDayWindow Window(JsonElement item, string path) => new(
        RequiredText(item, "status", path), OptionalInt(item, "first_run_lap", path), OptionalInt(item, "last_run_lap", path),
        OptionalText(item, "label", path), RequiredInt(item, "clean_lap_count", path), Ints(item, "laps", path),
        OptionalNumber(item, "median_s", path), OptionalText(item, "reason", path));

    private static TestDayFallOff FallOff(JsonElement item, string path) => new(
        RequiredText(item, "status", path), RequiredText(item, "method", path), OptionalInt(item, "first_run_lap", path),
        OptionalInt(item, "last_run_lap", path), RequiredInt(item, "fit_lap_count", path), Ints(item, "laps", path),
        RequiredBoolean(item, "equal_age", path), OptionalNumber(item, "slope_s_per_lap", path), OptionalNumber(item, "scatter_s", path),
        OptionalText(item, "reason", path));

    private static TestDaySetupChange SetupChange(JsonElement item, string path) => new(
        RequiredText(item, "status", path), OptionalInt(item, "compared_with_run", path), RequiredInt(item, "change_count", path),
        Settings(Array(item, "changes", path), $"{path}.changes"),
        RequiredInt(item, "unknown_count", path), Strings(item, "unknown_fields", path), RequiredBoolean(item, "complete", path),
        OptionalText(item, "reason", path))
    {
        Readouts = Settings(ArrayIfPresent(item, "readouts", path), $"{path}.readouts")
    };

    private static TestDaySettingChange[] Settings(IReadOnlyList<JsonElement> items, string path) => items.Select((change, index) =>
    {
        var at = $"{path}[{index}]";
        return new TestDaySettingChange(RequiredText(change, "field", at), RequiredText(change, "label", at),
            OptionalScalar(change, "previous", at), OptionalScalar(change, "current", at), OptionalNumber(change, "delta", at),
            OptionalText(change, "unit", at));
    }).ToArray();

    private static TestDayComparison? Comparison(JsonElement item, string path)
    {
        if (item.ValueKind != JsonValueKind.Object) return null;
        var windows = ObjectIfPresent(item, "windows", path);
        return new(
            RequiredInt(item, "compared_with_run", path), RequiredText(item, "verdict", path), OptionalText(item, "blocked_by", path),
            OptionalText(item, "basis", path), OptionalText(item, "window_label", path), OptionalInt(item, "first_run_lap", path),
            OptionalInt(item, "last_run_lap", path), OptionalNumber(item, "previous_median_s", path), OptionalNumber(item, "current_median_s", path),
            OptionalNumber(item, "delta_s", path), OptionalNumber(item, "threshold_s", path), OptionalNumber(item, "standard_error_s", path),
            Strings(item, "reasons", path), Strings(item, "flags", path), OptionalText(item, "summary", path))
        {
            RunLaps = IntsIfPresent(item, "run_laps", path),
            PreviousLaps = IntsIfPresent(item, "previous_laps", path),
            CurrentLaps = IntsIfPresent(item, "current_laps", path),
            CommonRunLaps = item.TryGetProperty("common_run_laps", out _) ? OptionalInt(item, "common_run_laps", path) : null,
            Noise = Noise(ObjectIfPresent(item, "noise", path), $"{path}.noise"),
            EarlyWindow = PairWindow(ObjectIfPresent(windows, "early", $"{path}.windows"), $"{path}.windows.early"),
            LateWindow = PairWindow(ObjectIfPresent(windows, "late", $"{path}.windows"), $"{path}.windows.late"),
            FallOff = PairFallOff(ObjectIfPresent(item, "fall_off", path), $"{path}.fall_off")
        };
    }

    private static TestDayTires Tires(JsonElement item, string path)
    {
        var stop = RequiredObject(item, "stop_reading", path);
        var sheet = RequiredObject(item, "sheet_reading", path);
        var corners = RequiredObject(item, "corners", path);
        return new TestDayTires(
            new TestDayStopReading(RequiredText(stop, "status", $"{path}.stop_reading"), RequiredText(stop, "source", $"{path}.stop_reading"),
                OptionalText(stop, "measurement_status", $"{path}.stop_reading"), OptionalText(stop, "reason", $"{path}.stop_reading"))
            {
                Temperature = stop.TryGetProperty("temperature", out _) ? OptionalText(stop, "temperature", $"{path}.stop_reading") : null,
                HotPressure = ObjectIfPresent(stop, "hot_pressure", $"{path}.stop_reading") is { ValueKind: JsonValueKind.Object } hot
                    ? new TestDayHotPressure(RequiredText(hot, "status", $"{path}.stop_reading.hot_pressure"),
                        OptionalText(hot, "source", $"{path}.stop_reading.hot_pressure"), OptionalInt(hot, "matched_run", $"{path}.stop_reading.hot_pressure"),
                        OptionalText(hot, "reason", $"{path}.stop_reading.hot_pressure"))
                    : null
            },
            new TestDaySheetReading(RequiredText(sheet, "status", $"{path}.sheet_reading"), RequiredText(sheet, "source", $"{path}.sheet_reading"),
                RequiredText(sheet, "timing", $"{path}.sheet_reading"), RequiredText(sheet, "timing_note", $"{path}.sheet_reading"))
            {
                Uniform = sheet.TryGetProperty("uniform", out _) && RequiredBoolean(sheet, "uniform", $"{path}.sheet_reading")
            },
            Corners.ToDictionary(corner => corner, corner => Corner(RequiredObject(corners, corner, $"{path}.corners"), $"{path}.corners.{corner}"),
                StringComparer.Ordinal));
    }

    private static TestDayTireCorner Corner(JsonElement item, string path)
    {
        var stop = NullableObject(item, "stop", path);
        var sheet = NullableObject(item, "sheet", path);
        var change = NullableObject(item, "change_from_previous_stop", path);
        return new TestDayTireCorner(
            stop.ValueKind != JsonValueKind.Object ? null : new TestDayStopCorner(
                Bands(NullableObject(stop, "temperature_f", $"{path}.stop"), $"{path}.stop.temperature_f"),
                Bands(NullableObject(stop, "tread_percent", $"{path}.stop"), $"{path}.stop.tread_percent"),
                OptionalNumber(stop, "pressure_psi", $"{path}.stop"), OptionalText(stop, "pressure_kind", $"{path}.stop"),
                OptionalNumber(stop, "spread_f", $"{path}.stop"), OptionalNumber(stop, "minimum_tread_percent", $"{path}.stop"))
            {
                ColdPressurePsi = stop.TryGetProperty("cold_pressure_psi", out _) ? OptionalNumber(stop, "cold_pressure_psi", $"{path}.stop") : null,
                HotPressurePsi = stop.TryGetProperty("hot_pressure_psi", out _) ? OptionalNumber(stop, "hot_pressure_psi", $"{path}.stop") : null
            },
            sheet.ValueKind != JsonValueKind.Object ? null : new TestDaySheetCorner(
                Bands(NullableObject(sheet, "temperature_f", $"{path}.sheet"), $"{path}.sheet.temperature_f"),
                Bands(NullableObject(sheet, "tread_percent", $"{path}.sheet"), $"{path}.sheet.tread_percent"),
                OptionalNumber(sheet, "hot_pressure_psi", $"{path}.sheet"), OptionalNumber(sheet, "cold_pressure_psi", $"{path}.sheet"),
                OptionalNumber(sheet, "spread_f", $"{path}.sheet"), OptionalNumber(sheet, "minimum_tread_percent", $"{path}.sheet")),
            Agreement(RequiredObject(item, "sheet_vs_stop", path), $"{path}.sheet_vs_stop"),
            Agreement(RequiredObject(item, "sheet_vs_previous_stop", path), $"{path}.sheet_vs_previous_stop"),
            change.ValueKind != JsonValueKind.Object ? null : new TestDayCornerChange(
                Bands(NullableObject(change, "temperature_f", $"{path}.change_from_previous_stop"), $"{path}.change_from_previous_stop.temperature_f"),
                Bands(NullableObject(change, "tread_percent", $"{path}.change_from_previous_stop"), $"{path}.change_from_previous_stop.tread_percent")));
    }

    private static TestDayBands? Bands(JsonElement item, string path) => item.ValueKind != JsonValueKind.Object ? null
        : new TestDayBands(OptionalNumber(item, "inside", path), OptionalNumber(item, "middle", path), OptionalNumber(item, "outside", path));

    private static TestDayAgreement Agreement(JsonElement item, string path) => new(RequiredText(item, "status", path),
        OptionalNumber(item, "max_temperature_delta_f", path), OptionalNumber(item, "max_tread_delta_percent", path))
    {
        DisagreesOn = item.TryGetProperty("disagrees_on", out _) ? Strings(item, "disagrees_on", path) : []
    };

    private static TestDayLap Lap(JsonElement item, string path) => new(
        RequiredInt(item, "lap", path), OptionalInt(item, "run_lap", path), OptionalNumber(item, "lap_time_s", path),
        RequiredBoolean(item, "eligible", path), RequiredBoolean(item, "plotted", path), Strings(item, "exclusion_reasons", path),
        RequiredText(item, "label", path));

    private static TestDayNextChange NextChange(JsonElement item)
    {
        const string path = "next_change";
        var step = NullableObject(item, "ruleset_step", path);
        var success = RequiredObject(item, "success_rule", path);
        const string successPath = "next_change.success_rule";
        return new TestDayNextChange(
            RequiredText(item, "kind", path), RequiredText(item, "title", path), RequiredText(item, "instruction", path),
            Ints(item, "based_on_runs", path),
            Array(item, "changes", path).Select((change, index) =>
            {
                var at = $"{path}.changes[{index}]";
                return new TestDayNextChangeSetting(RequiredText(change, "field", at), RequiredText(change, "label", at),
                    OptionalScalar(change, "from", at), OptionalScalar(change, "to", at));
            }).ToArray(),
            step.ValueKind != JsonValueKind.Object ? null : new TestDayRulesetStep(
                OptionalText(step, "ruleset_id", $"{path}.ruleset_step"), OptionalText(step, "setting_id", $"{path}.ruleset_step"),
                OptionalText(step, "direction", $"{path}.ruleset_step"), OptionalText(step, "instruction", $"{path}.ruleset_step"),
                OptionalText(step, "expected_effect", $"{path}.ruleset_step"), OptionalText(step, "risk", $"{path}.ruleset_step"),
                RequiredBoolean(step, "step_verified", $"{path}.ruleset_step")),
            new TestDaySuccessRule(RequiredText(success, "direction", successPath), OptionalInt(success, "reference_run", successPath),
                OptionalNumber(success, "reference_median_s", successPath), OptionalText(success, "window_label", successPath),
                OptionalInt(success, "first_run_lap", successPath), OptionalInt(success, "last_run_lap", successPath),
                OptionalNumber(success, "threshold_s", successPath), OptionalInt(success, "minimum_clean_laps", successPath),
                RequiredText(success, "text", successPath)),
            Strings(item, "evidence", path),
            OptionalText(item, "note", path));
    }

    // ---- strict readers: a required key must be present; null only where the contract allows it ----

    private static JsonElement Required(JsonElement item, string name, string path)
    {
        if (item.ValueKind != JsonValueKind.Object || !item.TryGetProperty(name, out var value))
            throw new JsonException($"Missing test-day field: {path}.{name}.");
        return value;
    }

    private static JsonElement RequiredObject(JsonElement item, string name, string path)
    {
        var value = Required(item, name, path);
        return value.ValueKind == JsonValueKind.Object ? value : throw new JsonException($"Invalid test-day object: {path}.{name}.");
    }

    private static JsonElement NullableObject(JsonElement item, string name, string path)
    {
        var value = Required(item, name, path);
        return value.ValueKind switch
        {
            JsonValueKind.Object => value,
            JsonValueKind.Null => default,
            _ => throw new JsonException($"Invalid test-day object: {path}.{name}.")
        };
    }

    private static string? OptionalText(JsonElement item, string name, string path)
    {
        var value = Required(item, name, path);
        return value.ValueKind switch
        {
            JsonValueKind.String => value.GetString(),
            JsonValueKind.Null => null,
            _ => throw new JsonException($"Invalid test-day text: {path}.{name}.")
        };
    }

    private static string RequiredText(JsonElement item, string name, string path) =>
        OptionalText(item, name, path) ?? throw new JsonException($"Missing test-day field: {path}.{name}.");

    /// <summary>Setting values may be text ("56.5%") or a bare number; either is shown as recorded.</summary>
    private static string? OptionalScalar(JsonElement item, string name, string path)
    {
        var value = Required(item, name, path);
        return value.ValueKind switch
        {
            JsonValueKind.String => value.GetString(),
            JsonValueKind.Number => value.GetRawText(),
            JsonValueKind.True => "true",
            JsonValueKind.False => "false",
            JsonValueKind.Null => null,
            _ => throw new JsonException($"Invalid test-day value: {path}.{name}.")
        };
    }

    private static double? OptionalNumber(JsonElement item, string name, string path)
    {
        var value = Required(item, name, path);
        if (value.ValueKind == JsonValueKind.Null) return null;
        return value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out var number) && double.IsFinite(number)
            ? number
            : throw new JsonException($"Invalid test-day number: {path}.{name}.");
    }

    private static int? OptionalInt(JsonElement item, string name, string path)
    {
        var value = OptionalNumber(item, name, path);
        if (value is null) return null;
        return value.Value == Math.Truncate(value.Value) && value.Value is >= int.MinValue and <= int.MaxValue
            ? (int)value.Value
            : throw new JsonException($"Invalid test-day integer: {path}.{name}.");
    }

    private static int RequiredInt(JsonElement item, string name, string path) =>
        OptionalInt(item, name, path) ?? throw new JsonException($"Missing test-day field: {path}.{name}.");

    private static bool? OptionalBoolean(JsonElement item, string name, string path) => Required(item, name, path).ValueKind switch
    {
        JsonValueKind.True => true,
        JsonValueKind.False => false,
        JsonValueKind.Null => null,
        _ => throw new JsonException($"Invalid test-day flag: {path}.{name}.")
    };

    private static bool RequiredBoolean(JsonElement item, string name, string path) =>
        OptionalBoolean(item, name, path) ?? throw new JsonException($"Missing test-day field: {path}.{name}.");

    private static IReadOnlyList<JsonElement> Array(JsonElement item, string name, string path)
    {
        var value = Required(item, name, path);
        return value.ValueKind == JsonValueKind.Array ? value.EnumerateArray().ToArray() : throw new JsonException($"Invalid test-day list: {path}.{name}.");
    }

    /// <summary>An optional list added after v1 shipped: absent is empty; present must be a list.</summary>
    private static IReadOnlyList<JsonElement> ArrayIfPresent(JsonElement item, string name, string path) =>
        item.ValueKind == JsonValueKind.Object && item.TryGetProperty(name, out _) ? Array(item, name, path) : [];

    private static IReadOnlyList<int> IntsIfPresent(JsonElement item, string name, string path) =>
        item.ValueKind == JsonValueKind.Object && item.TryGetProperty(name, out _) ? Ints(item, name, path) : [];

    private static IReadOnlyList<string> Strings(JsonElement item, string name, string path) => Array(item, name, path)
        .Select(value => value.ValueKind == JsonValueKind.String ? value.GetString()! : throw new JsonException($"Invalid test-day text in {path}.{name}."))
        .ToArray();

    private static IReadOnlyList<int> Ints(JsonElement item, string name, string path) => Array(item, name, path)
        .Select(value => value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var number)
            ? number
            : throw new JsonException($"Invalid test-day integer in {path}.{name}."))
        .ToArray();

    private static IReadOnlyDictionary<string, double> NumberMap(JsonElement item, string path)
    {
        if (item.ValueKind != JsonValueKind.Object) return new Dictionary<string, double>(StringComparer.Ordinal);
        var result = new Dictionary<string, double>(StringComparer.Ordinal);
        foreach (var property in item.EnumerateObject())
        {
            if (property.Value.ValueKind == JsonValueKind.Null) continue;
            result[property.Name] = property.Value.ValueKind == JsonValueKind.Number && property.Value.TryGetDouble(out var number) && double.IsFinite(number)
                ? number
                : throw new JsonException($"Invalid test-day number: {path}.{property.Name}.");
        }
        return result;
    }
}
