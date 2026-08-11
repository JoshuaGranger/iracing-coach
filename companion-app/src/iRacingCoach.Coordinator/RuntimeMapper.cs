using System.Globalization;
using System.Text.Json;
using System.Text.RegularExpressions;
using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

public static class RuntimeMapper
{
    public static RecentRace ArchivedRace(JsonElement report, string analysisPath)
    {
        var identity = Object(report, "identity");
        var selection = Object(Object(report, "source"), "selection");
        var summary = Object(report, "race_summary");
        var eventKey = Text(selection, "subsession_id") ?? Text(identity, "subsession_id") ?? Text(report, "analysis_id") ?? analysisPath;
        var groupId = SelectionGroup(selection, identity);
        var id = groupId ?? Text(report, "analysis_id") ?? Path.GetFileName(Path.GetDirectoryName(analysisPath)) ?? analysisPath;
        var sessionType = Text(selection, "sim_session_type") ?? Text(identity, "event_type") ?? "Race";
        var start = Text(identity, "session_start") ?? Text(report, "analyzed_at") ?? string.Empty;
        var date = DateTimeOffset.TryParse(start, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out var parsed)
            ? parsed.ToLocalTime().ToString("MMM d · h:mm tt", CultureInfo.CurrentCulture) : "Archived race";
        var recorded = Integer(summary, "recorded_laps");
        var scheduled = Integer(summary, "scheduled_laps");
        return new RecentRace(id, DisplayTrack(Text(identity, "track_name") ?? Text(identity, "track_path")) ?? "Recorded track", DisplayLayout(Text(identity, "track_config")),
            DisplayCar(Text(identity, "car_name") ?? Text(identity, "car_path")) ?? "Recorded car", date,
            Boolean(identity, "is_fixed_setup") == true ? "Fixed" : "Open", "Analyzed",
            scheduled > 0 ? $"{recorded} of {scheduled} laps" : $"{recorded} recorded laps", false, true,
            Integer(summary, "starting_position"), Integer(summary, "final_recorded_position"), Text(identity, "car_path") ?? string.Empty,
            analysisPath, string.Empty, start, eventKey, sessionType, "Archived", 1,
            Text(identity, "series_name") ?? string.Empty, Text(identity, "season_name") ?? string.Empty, groupId ?? analysisPath,
            Overview(report));
    }

    public static RaceOverview Overview(JsonElement report)
    {
        var view = Object(report, "analysis_view");
        var analysis = view.ValueKind == JsonValueKind.Object ? view : report;
        var summary = Object(analysis, "race_summary");
        if (summary.ValueKind != JsonValueKind.Object) summary = Object(report, "race_summary");
        var runs = Array(analysis, "runs");
        var laps = Array(analysis, "laps");

        var incidentLaps = Array(Object(Object(analysis, "damage_repair"), "incident_points"), "events")
            .Select(item => NullableInteger(item, "candidate_lap"))
            .Where(value => value is > 0)
            .Select(value => value!.Value)
            .ToHashSet();
        var cleanTimeValues = new List<double>();
        string? previousLapFlag = null;
        foreach (var lap in laps.OrderBy(lap => Integer(lap, "lap")))
        {
            var lapTime = Number(lap, "lap_time_s");
            if (ComparableLap(lap, previousLapFlag) &&
                !incidentLaps.Contains(Integer(lap, "lap")) &&
                lapTime is > 0)
                cleanTimeValues.Add(lapTime.Value);
            previousLapFlag = Text(lap, "flag_state")?.Trim().ToLowerInvariant();
        }
        var cleanTimes = cleanTimeValues.ToArray();
        double? consistency = null;
        if (cleanTimes.Length >= 2)
        {
            var mean = cleanTimes.Average();
            if (mean > 0) consistency = Math.Sqrt(cleanTimes.Sum(value => Math.Pow(value - mean, 2)) / cleanTimes.Length) / mean * 100;
        }

        var runDetails = runs.Select(run =>
        {
            var pace = Object(run, "pace");
            var tire = Object(run, "tire_observation");
            var load = Object(run, "driving_load");
            var comparisonContext = Object(run, "damage_repair_context");
            return new
            {
                Green = (int)Math.Round(Number(run, "green_laps") ?? 0),
                Slope = Number(pace, "green_lap_time_slope_s_per_lap"),
                Tire = Number(tire, "lowest_remaining_percent"),
                TireName = Text(tire, "lowest_remaining_tire") ?? string.Empty,
                BrakeChange = Number(load, "early_brake_vs_late_percent"),
                SteerChange = Number(load, "early_steer_vs_late_percent"),
                ComparisonEligible = ComparisonContextEligible(comparisonContext)
            };
        }).ToArray();
        var longestComparable = runDetails.Where(run => run.ComparisonEligible).OrderByDescending(run => run.Green).FirstOrDefault();
        var tireReading = runDetails.Where(run => run.Tire.HasValue).OrderBy(run => run.Tire).FirstOrDefault();
        var controlChanges = runDetails.Where(run => run.ComparisonEligible).SelectMany(run => new[] { run.BrakeChange, run.SteerChange })
            .Where(value => value.HasValue).Select(value => Math.Abs(value!.Value)).ToArray();
        var controlChange = controlChanges.Length > 0 ? controlChanges.Max() : (double?)null;

        return new RaceOverview(
            Integer(summary, "recorded_laps"), Integer(summary, "green_laps_estimated"), Integer(summary, "caution_laps_estimated"),
            Integer(summary, "pit_stops_detected"), runs.Count(), runDetails.Select(run => run.Green).DefaultIfEmpty().Max(),
            longestComparable?.Slope, consistency, tireReading?.Tire, tireReading?.TireName ?? string.Empty,
            controlChange, Number(summary, "fuel_used_gal"), cleanTimes.Length > 0 ? cleanTimes.Min() : null);
    }

    public static AnalysisWorkspace ArchivedAnalysis(JsonElement report)
    {
        using var wrapped = JsonDocument.Parse(JsonSerializer.Serialize(new Dictionary<string, object?>
        {
            ["analysis_id"] = Text(report, "analysis_id") ?? string.Empty,
            ["analysis_view"] = report.Clone()
        }));
        return Analysis(wrapped.RootElement);
    }

    public static RaceCard ArchivedRaceCard(JsonElement report)
    {
        var signals = Array(report, "coaching_signals");
        var actions = signals.Take(3).Select((signal, index) => new RaceAction(index == 0 ? "First priority" : $"Priority {index + 1}",
            new EvidenceText(EvidenceKind.Derived, Text(signal, "coaching") ?? Text(signal, "finding") ?? "Review the recorded evidence."))).ToArray();
        var bottom = signals.FirstOrDefault();
        var bottomText = bottom.ValueKind == JsonValueKind.Object ? Text(bottom, "finding") : null;
        return new RaceCard("Recorded race", new EvidenceText(EvidenceKind.Measured, bottomText ?? "The saved analysis is available."), actions, [], [], []);
    }

    public static LocalSetup? ArchivedSetup(JsonElement report, string analysisPath)
    {
        var identity = Object(report, "identity");
        var setup = Object(identity, "setup");
        if (setup.ValueKind != JsonValueKind.Object) return null;
        var fields = new List<SetupField>();
        FlattenSetup(setup, string.Empty, fields);
        if (fields.Count == 0) return null;
        var fingerprint = Text(identity, "setup_fingerprint") ?? Text(report, "analysis_id") ?? analysisPath;
        return new LocalSetup(fingerprint, Text(identity, "setup_name") ?? "Recorded setup",
            DisplayCar(Text(identity, "car_name") ?? Text(identity, "car_path")) ?? "Recorded car",
            DisplayTrack(Text(identity, "track_name") ?? Text(identity, "track_path")) ?? string.Empty, "Recorded race", analysisPath, fingerprint,
            "Linked to archived race", "Setup parameters recorded by iRacing; the archived report remains read only.", fields);
    }

    private static void FlattenSetup(JsonElement element, string prefix, List<SetupField> fields)
    {
        foreach (var property in element.EnumerateObject())
        {
            var key = string.IsNullOrWhiteSpace(prefix) ? property.Name : $"{prefix}.{property.Name}";
            if (property.Value.ValueKind == JsonValueKind.Object) FlattenSetup(property.Value, key, fields);
            else if (property.Value.ValueKind is JsonValueKind.String or JsonValueKind.Number or JsonValueKind.True or JsonValueKind.False)
            {
                var parts = key.Split('.');
                fields.Add(new SetupField(key, Humanize(parts[^1]) ?? parts[^1], parts.Length > 1 ? string.Join(" · ", parts[..^1].Select(part => Humanize(part) ?? part)) : "Setup", DisplayJson(property.Value)));
            }
        }
    }

    public static IReadOnlyList<LocalSetup> Setups(JsonElement response)
    {
        if (!response.TryGetProperty("entries", out var entries) || entries.ValueKind != JsonValueKind.Array)
        {
            return [];
        }

        var result = new List<LocalSetup>();
        foreach (var entry in entries.EnumerateArray())
        {
            var sources = Object(entry, "sources");
            var sto = Array(sources, "sto").FirstOrDefault();
            var identity = Object(entry, "filename_identity");
            var parsed = Object(entry, "parsed_html");
            var fieldsObject = Object(parsed, "fields");
            var path = Text(sto, "path");
            if (string.IsNullOrWhiteSpace(path))
            {
                continue;
            }

            var stem = Text(entry, "stem") ?? Path.GetFileNameWithoutExtension(path);
            var warning = Object(Object(Object(entry, "parsed_html"), "identity"), "mismatches");
            result.Add(new LocalSetup(
                Text(sto, "sha256") ?? path,
                stem,
                DisplayCar(Text(entry, "car_folder")) ?? string.Empty,
                DisplayTrack(Text(identity, "track_hint")) ?? string.Empty,
                DisplaySetupRole(Text(identity, "role")),
                path,
                Text(sto, "sha256") ?? string.Empty,
                Text(entry, "pair_status") ?? string.Empty,
                Boolean(warning, "has_mismatch") == true
                    ? "The filename and exported setup details disagree. Verify it in iRacing before use."
                    : "Local setup file; the original remains unchanged.",
                fieldsObject.ValueKind == JsonValueKind.Object
                    ? fieldsObject.EnumerateObject().Select(property =>
                    {
                        var field = property.Value;
                        var raw = field.TryGetProperty("raw", out var rawValue) ? DisplayJson(rawValue) : string.Empty;
                        var unit = Text(field, "unit");
                        return new SetupField(property.Name, Text(field, "label") ?? Humanize(property.Name.Split('.').Last()) ?? property.Name,
                            Text(field, "section") ?? Humanize(property.Name.Split('.').First()) ?? "Setup", string.IsNullOrWhiteSpace(unit) || raw.Contains(unit, StringComparison.OrdinalIgnoreCase) ? raw : $"{raw} {unit}");
                    }).ToArray() : []));
        }
        return result;
    }

    public static Garage61Connection Garage61(JsonElement response)
    {
        var configured = Boolean(response, "configured") == true;
        var available = Boolean(response, "ok") == true && configured;
        var status = Text(response, "status") ?? (configured ? "configured" : "not_configured");
        var message = available
            ? "Connected and ready."
            : configured ? "Your token is saved. Connection retries automatically." : "Add your Garage61 token here when you want to connect.";
        return new Garage61Connection(configured, available, status, message);
    }

    public static AnalysisGarage61References? Garage61References(JsonElement response) =>
        MapGarage61References(Object(response, "garage61_representative_laps"), response);

    public static RaceCard RaceCard(JsonElement response)
    {
        var card = Object(response, "race_card");
        if (card.ValueKind != JsonValueKind.Object)
        {
            throw new InvalidDataException("The analysis response did not include its deterministic Race Card.");
        }

        var playbook = Object(card, "corner_playbook");
        var corners = Array(playbook, "rows").Select(row => new CornerCoachingRow(
            Text(row, "corner_phase") ?? Text(row, "zone_id") ?? "Recorded load zone",
            Claim(Object(row, "phase_1")),
            Claim(Object(row, "phase_2")),
            Claim(Object(row, "phase_3")),
            Claim(Object(row, "groove")))).ToArray();

        return new RaceCard(
            Text(card, "title") ?? "Race Card",
            Claim(Object(card, "bottom_line")),
            Array(card, "actions").Select(item => new RaceAction(Text(item, "label") ?? "Action", Claim(item))).ToArray(),
            corners,
            Array(card, "race_triggers").Select(item => new RaceTrigger(Text(item, "label") ?? "Trigger", Claim(item))).ToArray(),
            Array(card, "evidence_appendix").Select(Claim).ToArray());
    }

    public static AnalysisWorkspace Analysis(JsonElement response)
    {
        var view = Object(response, "analysis_view");
        if (view.ValueKind != JsonValueKind.Object)
        {
            throw new InvalidDataException("The analysis response did not include the telemetry workspace.");
        }

        var selection = AnalysisSelection(response, view);
        var identity = Object(view, "identity");
        var summary = Object(view, "race_summary");
        var traceRoot = Object(view, "lap_traces");
        var track = Object(view, "track_profile");
        var strategy = Object(view, "strategy");
        var forecast = Object(strategy, "forecast");
        var damage = Object(view, "damage_repair");
        var setup = Object(view, "setup_telemetry");
        var quality = Object(view, "data_quality");
        var gradesRoot = Object(view, "race_grades");
        var timing = Object(response, "timing");
        var vectorGeometry = MapTrackGeometry(Object(view, "track_geometry"));
        var replay = MapRaceReplay(Object(view, "race_replay"));
        var tirePrediction = MapTirePrediction(Object(view, "tire_learning"));
        var garage61References = MapGarage61References(Object(view, "garage61_representative_laps"), view);
        var technicalInsights = MapTechnicalInsights(view);
        var tuningIdentity = MapTuningIdentity(response, view, selection, identity, vectorGeometry);

        string? previousMappedLapFlag = null;
        var laps = Array(view, "laps").OrderBy(lap => Integer(lap, "lap")).Select(lap =>
        {
            var position = Object(lap, "position");
            var reasons = LapComparisonExclusionReasons(lap, previousMappedLapFlag);
            previousMappedLapFlag = Text(lap, "flag_state")?.Trim().ToLowerInvariant();
            return new AnalysisLap(
                Integer(lap, "lap"), Number(lap, "lap_time_s"), Boolean(lap, "complete") == true,
                Text(lap, "flag_state") ?? "unknown", Number(lap, "green_fraction"), Number(lap, "caution_fraction"),
                Number(lap, "pit_time_s"), NullableInteger(position, "start"), NullableInteger(position, "end"),
                reasons.Length > 0,
                string.Join(", ", reasons.Select(Humanize).Where(value => value is not null)));
        }).ToArray();

        var lapsByNumber = laps.GroupBy(lap => lap.Lap).ToDictionary(group => group.Key, group => group.First());
        var traces = Array(traceRoot, "traces").Select(trace =>
        {
            var lapNumber = Integer(trace, "lap");
            var conditions = Object(trace, "conditions");
            var hasLapEligibility = lapsByNumber.TryGetValue(lapNumber, out var lapEligibility);
            return new AnalysisLapTrace(
                lapNumber, Number(trace, "lap_time_s"), Boolean(trace, "complete") == true,
                Text(trace, "flag_state") ?? "unknown", Number(trace, "green_fraction"), Number(trace, "caution_fraction"),
                Number(trace, "pit_time_s"),
                Array(trace, "points").Select(point => new AnalysisTracePoint(
                    Number(point, "lap_pct") ?? 0, Number(point, "session_time_s"), Number(point, "speed_mph"), Number(point, "speed_min_mph"), Number(point, "speed_max_mph"),
                    Number(point, "throttle"), Number(point, "throttle_min"), Number(point, "brake"), Number(point, "brake_mean"),
                    Number(point, "steering_rad"), Number(point, "steering_abs_peak_rad") ?? Number(point, "steering_peak_rad"), Number(point, "slip_angle_deg"), NullableInteger(point, "gear"), Number(point, "rpm"),
                    Number(point, "yaw_rate_deg_s"), Number(point, "lateral_g"), Number(point, "longitudinal_g"),
                    Number(point, "latitude") ?? Number(point, "lat"), Number(point, "longitude") ?? Number(point, "lon"), Number(point, "tire_stress_proxy"),
                    NumberDictionary(point, "additional_signals"))).ToArray(),
                Array(trace, "flag_states").Select(Value).Where(value => value.Length > 0).ToArray(),
                Boolean(trace, "pit_entry") == true, Boolean(trace, "pit_exit") == true,
                Number(trace, "fuel_used_gal"),
                conditions.ValueKind == JsonValueKind.Object ? new AnalysisLapConditions(
                    Text(conditions, "sky"), Number(conditions, "track_temperature_f"), Number(conditions, "air_temperature_f"),
                    Number(conditions, "wind_speed_mph"), Number(conditions, "wind_direction_degrees"),
                    Number(conditions, "relative_humidity_percent"), Number(conditions, "fog_percent"),
                    Number(conditions, "air_pressure_inhg"), Number(conditions, "air_density_lb_ft3"),
                    Number(conditions, "precipitation_percent"), Number(conditions, "track_wetness_state"),
                    Number(conditions, "track_usage_percent"), Text(conditions, "track_usage"),
                    Boolean(conditions, "weather_declared_wet")) : null,
                hasLapEligibility && !lapEligibility!.Confounded,
                hasLapEligibility ? lapEligibility!.ExclusionReason : "Lap comparison eligibility was not recorded");
        }).ToArray();

        var additionalTraceSignals = Array(traceRoot, "additional_signal_catalog")
            .Select(signal => new AnalysisTraceSignal(
                Text(signal, "id") ?? string.Empty,
                Text(signal, "name") ?? Humanize(Text(signal, "id")) ?? "Recorded signal",
                Text(signal, "unit") ?? string.Empty,
                Text(signal, "category") ?? "Other",
                Evidence(Text(signal, "evidence_type")),
                Text(signal, "description") ?? string.Empty,
                Array(signal, "source_channels").Select(Value).Where(value => value.Length > 0).ToArray()))
            .Where(signal => signal.Id.Length > 0)
            .DistinctBy(signal => signal.Id, StringComparer.Ordinal)
            .ToArray();

        var runs = Array(view, "runs").Select(run =>
        {
            var fuel = Object(run, "fuel");
            var pace = Object(run, "pace");
            var tire = Object(run, "tire_observation");
            var tires = Object(tire, "tires");
            var pitService = Object(run, "pit_service");
            var drivingLoad = Object(run, "driving_load");
            var damageContext = Object(run, "damage_repair_context");
            var reasons = Array(damageContext, "reason_codes").Select(Value).Where(value => value.Length > 0).ToArray();
            var runNumber = Integer(run, "run_number");
            var pitAssessment = Array(strategy, "pit_assessments").FirstOrDefault(item => Integer(item, "run_number") == runNumber);
            var serviceStart = Number(pitService, "start_time");
            var serviceEnd = Number(pitService, "end_time");
            var hasPitStop = Boolean(run, "ended_with_pit_stop") == true || pitService.ValueKind == JsonValueKind.Object;
            var repairCompletedSeconds = Array(damage, "episodes")
                .Where(episode => Array(Object(episode, "run_context"), "overlapping_run_numbers")
                    .Select(NullableIntegerValue).Any(number => number == runNumber))
                .Select(episode => Number(Object(episode, "timing"), "repair_work_completed_s") ?? 0)
                .Sum();
            var tireConditions = new[] { "LF", "RF", "LR", "RR" }
                .Select(corner => TireCondition(tires, corner))
                .Where(condition => condition is not null)
                .Cast<AnalysisTireCondition>()
                .ToDictionary(condition => condition.Corner, StringComparer.OrdinalIgnoreCase);
            var pitStop = hasPitStop
                ? new AnalysisPitStop(
                    serviceStart.HasValue && serviceEnd.HasValue ? Math.Max(0, serviceEnd.Value - serviceStart.Value) : null,
                    Number(pitService, "fuel_added_l") is { } fuelAddedLiters ? fuelAddedLiters / 3.785411784 : null,
                    Number(pitAssessment, "fuel_laps_remaining_at_end"),
                    Array(pitService, "tires_changed_observed").Select(Value).Where(value => value.Length > 0).ToArray(),
                    TireWearPercent(tires, "LF"), TireWearPercent(tires, "RF"), TireWearPercent(tires, "LR"), TireWearPercent(tires, "RR"),
                    repairCompletedSeconds > .005 ? repairCompletedSeconds : null,
                    Number(pitService, "penalty_served_s"),
                    tireConditions,
                    Number(pitAssessment, "pit_cycle_position_change"),
                    Number(pitAssessment, "scheduled_race_laps_remaining_after_stop"))
                : null;
            return new AnalysisRun(
                runNumber, Array(run, "lap_numbers").Select(NullableIntegerValue).Where(value => value.HasValue && value.Value > 0).Select(value => value!.Value).ToArray(),
                (int)Math.Round(Number(run, "green_laps") ?? 0), (int)Math.Round(Number(run, "caution_laps") ?? 0), Number(fuel, "used_gal"),
                Number(pace, "green_lap_time_slope_s_per_lap"), Humanize(Text(run, "tire_measurement_status")) ?? "Tire reading unavailable",
                ComparisonContextEligible(damageContext),
                reasons.Length == 0 ? "Recorded run" : string.Join(", ", reasons.Select(Humanize).Where(value => value is not null)),
                Number(pace, "early_average_lap_s"), Number(pace, "late_average_lap_s"), Number(pace, "early_to_late_delta_s"),
                Number(tire, "lowest_remaining_percent"), Text(tire, "lowest_remaining_tire") ?? string.Empty,
                Number(drivingLoad, "early_brake_vs_late_percent"), Number(drivingLoad, "early_steer_vs_late_percent"), pitStop,
                Array(run, "coaching_reference_lap_numbers")
                    .Select(NullableIntegerValue)
                    .Count(value => value.HasValue && value.Value >= 0),
                Boolean(run, "ended_under_caution"));
        }).ToArray();

        var shape = Array(track, "shape").Select(point => new TrackShapePoint(
            Number(point, "lap_pct") ?? 0, Number(point, "x") ?? 0, Number(point, "y") ?? 0)).ToArray();
        var segments = Array(track, "detected_corner_segments").Select(segment => new TrackSegment(
            Integer(segment, "segment"), Number(segment, "start_pct") ?? 0, Number(segment, "end_pct") ?? 0,
            Boolean(segment, "wraps_start_finish") == true, Text(segment, "label") ?? $"Load zone {Integer(segment, "segment")}" )).ToArray();
        var suppliedGrades = Array(gradesRoot, "categories").ToDictionary(item => Text(item, "key") ?? string.Empty, StringComparer.OrdinalIgnoreCase);
        var unavailableGrades = Array(gradesRoot, "unavailable_categories").ToDictionary(item => Text(item, "key") ?? string.Empty, StringComparer.OrdinalIgnoreCase);
        var rubricVersion = Text(gradesRoot, "rubric_version") ?? Text(Object(gradesRoot, "rubric"), "version") ?? "race-execution-v1";
        var gradeDefinitions = new[]
        {
            ("pace", "Pace execution", "Clean completed green laps; fastest and median lap time"),
            ("consistency", "Consistency and execution", "Usable-lap variation after supported exclusions"),
            ("tire_management", "Tire management", "Clean-run pace trend, control load, and discrete service readings"),
            ("racecraft", "Racecraft and adaptability", "Recorded start/finish position and race timeline"),
            ("strategy", "Pit and strategy execution", "Recorded pit, tow, repair, and fuel-window evidence")
        };
        var grades = gradeDefinitions.Select(definition =>
        {
            if (suppliedGrades.TryGetValue(definition.Item1, out var item))
            {
                var configuredWeight = Number(item, "weight_percent");
                var effectiveWeight = Number(item, "effective_weight");
                var configuredWeightText = ConfiguredWeightText(configuredWeight);
                var calibration = effectiveWeight.HasValue
                    ? $"{rubricVersion} · {configuredWeightText}; {effectiveWeight.Value * 100:0.#}% of available evidence"
                    : $"{rubricVersion} · {configuredWeightText}";
                return new RaceGrade(
                    definition.Item1, Text(item, "label") ?? definition.Item2, Text(item, "grade") ?? "Not graded",
                    Number(item, "score"), Evidence(Text(item, "evidence_type")), Text(item, "explanation") ?? "The available recorded evidence was graded.",
                    Text(item, "improvement") ?? "Review the supporting telemetry before choosing the next action.",
                    Text(item, "limitations") ?? "This local grade is bounded by the recorded channels.", true, [definition.Item3],
                    calibration, $"Deterministic local analysis · {rubricVersion}");
            }

            unavailableGrades.TryGetValue(definition.Item1, out var unavailable);
            var unavailableWeight = unavailable.ValueKind == JsonValueKind.Object ? Number(unavailable, "weight_percent") : null;
            var reason = unavailable.ValueKind == JsonValueKind.Object ? Text(unavailable, "reason") : null;
            return new RaceGrade(
                definition.Item1, unavailable.ValueKind == JsonValueKind.Object ? Text(unavailable, "label") ?? definition.Item2 : definition.Item2,
                "Not graded", null, EvidenceKind.Unavailable,
                reason ?? "There is not enough supported recorded evidence for this category.",
                "Record the specific comparable evidence required for this category.",
                $"{reason ?? "Required evidence is unavailable"} Missing evidence is excluded rather than converted to a neutral score.", false, [definition.Item3],
                $"{rubricVersion} · {ConfiguredWeightText(unavailableWeight)}; excluded from the overall grade",
                $"Deterministic local analysis · {rubricVersion}");
        }).ToArray();

        var damageSummary = Object(damage, "summary");
        var incidentPoints = Object(damage, "incident_points");
        var incidents = Array(incidentPoints, "events")
            .Select(item =>
            {
                var lap = NullableInteger(item, "candidate_lap");
                var points = Number(item, "points_added");
                return lap is >= 0 && points is >= 0
                    ? new AnalysisIncident(
                        lap.Value,
                        (int)Math.Round(points.Value),
                        Number(item, "session_time_s"),
                        Number(item, "count_before"),
                        Number(item, "count_after"),
                        Text(item, "source_channel"),
                        Text(item, "event_type"),
                        Text(item, "contact_target"),
                        Text(item, "track_location"),
                        Boolean(item, "on_pit_road"),
                        Number(item, "speed_mph"),
                        Number(item, "yaw_rate_deg_s"),
                        Number(item, "slip_angle_deg"))
                    : null;
            })
            .Where(item => item is not null)
            .Cast<AnalysisIncident>()
            .ToArray();
        var strategyDetails = new AnalysisStrategy(
            Number(strategy, "measured_green_fuel_gal_per_lap"), Number(strategy, "measured_caution_fuel_gal_per_lap"),
            NumberOrFirst(forecast, "all_green_range_laps"), NullableInteger(forecast, "minimum_stops_all_green"),
            Array(forecast, "equal_stint_pit_targets_all_green").Select(NullableIntegerValue).Where(value => value is > 0).Select(value => value!.Value).ToArray(),
            Number(forecast, "operational_reserve_fuel_l") is { } reserveLiters ? reserveLiters / 3.785411784 : null,
            Number(forecast, "operational_reserve_green_laps"), Humanize(Text(forecast, "classification")) ?? "Recorded fuel feasibility",
            Array(forecast, "assumptions").Select(Value).Where(value => value.Length > 0).ToArray(),
            Array(strategy, "limitations").Select(Value).Where(value => value.Length > 0).ToArray());
        var damageDetails = new AnalysisDamage(
            Integer(damageSummary, "pit_road_episodes"), Integer(damageSummary, "tow_episodes"),
            Integer(damageSummary, "recorded_repair_episodes"), Integer(damageSummary, "confirmed_fast_repair_uses"),
            Number(damageSummary, "total_pit_road_time_s"), Number(damageSummary, "total_repair_work_completed_s"),
            Array(damage, "limitations").Select(Value).Where(value => value.Length > 0).ToArray(), incidents);
        var tuningMap = MapTuningMap(view, vectorGeometry, shape, segments, tuningIdentity);

        return new AnalysisWorkspace(
            Integer(view, "schema_version"), Text(response, "analysis_id") ?? string.Empty,
            DisplayTrack(Text(identity, "track_name") ?? Text(identity, "track_path")) ?? "Recorded track", DisplayLayout(Text(identity, "track_config")),
            DisplayCar(Text(identity, "car_name") ?? Text(identity, "car_path")) ?? "Recorded car",
            Boolean(identity, "is_fixed_setup") == true ? "Fixed" : "Open", Text(selection, "sim_session_type") ?? Text(identity, "event_type") ?? "Race",
            Integer(summary, "recorded_laps"), Integer(summary, "scheduled_laps"), Integer(summary, "pit_stops_detected"),
            runs, laps, traces, shape, segments, shape.Length >= 3 ? "track_shape" : "normalized_distance_strip",
            Text(traceRoot, "tire_stress_definition") ?? "A relative controls-and-load proxy; not measured tire wear.",
            Humanize(Text(forecast, "status")) ?? Humanize(Text(strategy, "confidence")) ?? "Insufficient recorded strategy evidence",
            Humanize(Text(damage, "status")) ?? "Unavailable", strategyDetails, damageDetails, Text(identity, "setup_fingerprint") ?? string.Empty,
            Humanize(Text(quality, "confidence")) ?? "Unknown", Number(timing, "total_ms") ?? 0,
            Text(gradesRoot, "overall_grade") ?? "Not graded", grades,
            Array(traceRoot, "sector_start_pcts").Select(NumberValue).Where(value => value.HasValue).Select(value => value!.Value).Order().ToArray(),
            additionalTraceSignals,
            vectorGeometry,
            replay,
            tirePrediction,
            garage61References,
            technicalInsights,
            tuningIdentity,
            tuningMap);
    }

    public static SetupPackageView SetupPackage(JsonElement response, string requestedCar, string requestedTrack, string requestedSeason, string purpose)
    {
        if (Boolean(response, "ok") != true) throw new InvalidDataException(Text(response, "message") ?? "A starting tune package could not be built from the selected context.");
        var raceBaseline = Object(response, "baseline");
        var qualifyingBaseline = Object(response, "qualifying");
        var wantsQualifying = purpose.Equals("Qualifying", StringComparison.OrdinalIgnoreCase);
        if (wantsQualifying && qualifyingBaseline.ValueKind != JsonValueKind.Object)
            throw new InvalidDataException("No separate qualifying baseline was found for this exact context. A race setup will not be relabeled as qualifying.");
        var baseline = wantsQualifying ? qualifyingBaseline : raceBaseline;
        var confirmation = Object(response, "baseline_confirmation");
        var donor = Object(response, "donor");
        var warnings = Array(baseline, "identity_warnings").Select(Value).Where(value => value.Length > 0).ToList();
        if (Text(donor, "warning") is { Length: > 0 } warning) warnings.Add(warning);
        if (warnings.Count == 0) warnings.Add("Confirm the selected setup passes iRacing tech for the target event before changing it.");
        var exact = !wantsQualifying && Boolean(confirmation, "confirmed") == true;
        var source = exact ? "Exact recorded baseline" : "Related baseline for validation";
        var donorName = Text(donor, "donor") ?? Text(baseline, "stem") ?? "Selected local baseline";
        return new SetupPackageView(
            Text(response, "package_id") ?? "Saved starting tune",
            Humanize(Text(response, "status")) ?? "Package ready",
            source,
            Text(baseline, "fingerprint") ?? "Fingerprint unavailable",
            donorName,
            exact ? "The source matches the requested recorded context." : wantsQualifying
                ? "A separate qualifying candidate was found. Verify it in the exact qualifying session before use."
                : (Text(confirmation, "reason") ?? "The source must be validated at the target track."),
            warnings,
            [
                "Load the source setup in iRacing without overwriting it.",
                "Save a working copy under a new name.",
                "Pass iRacing tech inspection for the exact car, track, and session.",
                "Run a clean baseline before making one controlled change.",
                "Keep this package fingerprint as the rollback reference."
            ],
            Text(response, "package_path") ?? string.Empty, requestedCar, requestedTrack, requestedSeason, purpose,
            Text(donor, "reason") ?? string.Empty,
            Boolean(response, "simulator_loadable_setup_produced") == true,
            Boolean(response, "source_setup_files_modified") == true);
    }

    public static TuningExperimentView Tuning(JsonElement response)
    {
        if (Boolean(response, "ok") != true)
        {
            var recommendation = Object(response, "recommendation");
            var blockers = Array(recommendation, "blockers").Select(Value).Where(value => value.Length > 0);
            throw new InvalidDataException(string.Join(" ", blockers.DefaultIfEmpty("The selected race is not suitable for a tuning recommendation.")));
        }

        var primary = Object(response, "primary_recommendation");
        var recommendationBody = Object(response, "recommendation");
        var setup = Object(recommendationBody, "setup");
        return new TuningExperimentView(
            Text(response, "experiment_id") ?? "Saved experiment",
            Humanize(RequireText(primary, "system", "tuning system"))!,
            RequireText(primary, "change", "recommended change"),
            RequireText(primary, "predicted_effect", "predicted effect"),
            RequireText(primary, "risk", "risk"),
            RequireText(setup, "fingerprint", "setup fingerprint"),
            Array(primary, "verify").Select(Value).Where(value => value.Length > 0).ToArray(),
            "Waiting for a comparison run");
    }

    public static StructuredTuningResultView StructuredTuning(JsonElement response)
    {
        var wrapped = Object(response, "result");
        var root = wrapped.ValueKind == JsonValueKind.Object ? wrapped : response;
        var eligibilityRoot = Object(root, "eligibility");
        var evidenceContract = Object(root, "tuning_evidence_v2");
        var evidenceItems = Array(root, "evidence").ToArray();
        if (evidenceItems.Length == 0) evidenceItems = Array(evidenceContract, "observations")
            .Concat(Array(evidenceContract, "evidence"))
            .Concat(Array(evidenceContract, "items")).ToArray();
        var evidence = evidenceItems.Select((item, index) => new TuningEvidenceView(
                Text(item, "evidence_id") ?? Text(item, "id") ?? $"evidence-{index + 1}",
                TuningEvidenceKind(item),
                Text(item, "label") ?? TuningObservationLabel(item, index),
                Text(item, "value") ?? Text(item, "text") ?? Text(item, "summary") ?? TuningObservationValue(item),
                Text(item, "unit") ?? string.Empty,
                Text(item, "source") ?? Text(item, "provenance") ?? "Local race evidence",
                Text(item, "limitation") ?? string.Empty))
            .ToArray();

        var candidateItems = Array(root, "candidate_whitelist").ToArray();
        if (candidateItems.Length == 0) candidateItems = Array(Object(root, "recommendation"), "candidate_whitelist").ToArray();
        var candidates = candidateItems.Select((item, index) => new TuningCandidateChangeView(
                Text(item, "candidate_id") ?? Text(item, "id") ?? $"candidate-{index + 1}",
                Humanize(Text(item, "system")) ?? "Setup",
                Text(item, "change") ?? string.Empty,
                Text(item, "predicted_effect") ?? string.Empty,
                Text(item, "risk") ?? string.Empty,
                Array(item, "verify").Concat(Array(item, "verification")).Select(Value).Where(value => value.Length > 0).Distinct(StringComparer.Ordinal).ToArray(),
                Text(item, "source") ?? "Installed versioned tuning rule",
                TuningCandidateConfidence(item),
                Array(item, "evidence_ids").Select(Value).Where(value => value.Length > 0).ToArray(),
                Array(item, "conflicts").Select(Value).Where(value => value.Length > 0).ToArray()))
            .ToArray();

        var recommendationRoot = Object(root, "recommendation");
        var recommendation = new StructuredTuningRecommendationView(
            Text(recommendationRoot, "status") ?? Text(root, "status") ?? "unavailable",
            Text(recommendationRoot, "selected_candidate_id") ?? string.Empty,
            Text(recommendationRoot, "summary") ?? Text(recommendationRoot, "explanation") ?? string.Empty,
            Array(recommendationRoot, "evidence_ids").Select(Value).Where(value => value.Length > 0).ToArray(),
            Array(recommendationRoot, "conflicts").Select(Value).Where(value => value.Length > 0).ToArray(),
            Array(recommendationRoot, "confidence_reasons").Select(Value).Where(value => value.Length > 0).ToArray());

        var missing = Array(root, "missing_required").Concat(Array(eligibilityRoot, "missing_required"))
            .Select(Value).Where(value => value.Length > 0).Distinct(StringComparer.Ordinal).ToArray();
        var blockers = Array(eligibilityRoot, "blockers").Select(Value).Where(value => value.Length > 0).ToArray();
        var canEvidence = Boolean(eligibilityRoot, "can_use_as_driving_evidence")
            ?? Boolean(eligibilityRoot, "can_use_as_evidence")
            ?? evidence.Length > 0;
        var canRecommend = Boolean(eligibilityRoot, "can_receive_garage_recommendation")
            ?? string.Equals(recommendation.Status, "ready", StringComparison.OrdinalIgnoreCase);
        var eligibility = new TuningEligibilityView
        {
            CanUseAsEvidence = canEvidence,
            CanReceiveGarageRecommendation = canRecommend,
            IsFinalized = Boolean(eligibilityRoot, "is_finalized") ?? canEvidence,
            IsOpenSetup = Boolean(eligibilityRoot, "is_open_setup") ?? canRecommend,
            EmbeddedSetupAvailable = Boolean(eligibilityRoot, "embedded_setup_available") ?? canRecommend,
            HasSetupFingerprint = Boolean(eligibilityRoot, "has_setup_fingerprint") ?? canRecommend,
            ExactIdentityAvailable = Boolean(eligibilityRoot, "exact_identity_available")
                ?? (Boolean(eligibilityRoot, "exact_map_identity") == true && Boolean(eligibilityRoot, "exact_open_setup_identity") == true),
            MissingRequired = missing,
            Blockers = blockers
        };

        var history = Array(root, "history").Concat(Array(root, "prior_successes_considered"))
            .Select(item => new TuningHistoryView(
                Text(item, "experiment_id") ?? Text(item, "id") ?? string.Empty,
                Text(item, "outcome") ?? string.Empty,
                Text(item, "setup_fingerprint") ?? Text(Object(item, "setup"), "fingerprint") ?? string.Empty,
                ParseTimestamp(Text(item, "recorded_utc") ?? Text(item, "created_utc")),
                Array(item, "evidence_ids").Select(Value).Where(value => value.Length > 0).ToArray()))
            .Where(item => item.ExperimentId.Length > 0)
            .DistinctBy(item => item.ExperimentId, StringComparer.Ordinal)
            .ToArray();
        var protocolRoot = Object(root, "test_protocol");
        var protocol = protocolRoot.ValueKind == JsonValueKind.Object
            ? new TuningTestProtocolView(
                Text(protocolRoot, "control") ?? string.Empty,
                Array(protocolRoot, "sequence").Select(Value).Where(value => value.Length > 0).ToArray(),
                Boolean(protocolRoot, "one_change_rule") == true,
                Array(protocolRoot, "comparison_requirements").Select(Value).Where(value => value.Length > 0).ToArray())
            : null;
        var limitations = Array(root, "limitations").Concat(Array(evidenceContract, "limitations"))
            .Select(Value).Where(value => value.Length > 0).Distinct(StringComparer.Ordinal).ToArray();

        return new StructuredTuningResultView(
            Text(root, "experiment_id") ?? string.Empty,
            Text(root, "experiment_path") ?? string.Empty,
            eligibility,
            evidence,
            candidates,
            recommendation,
            limitations,
            missing,
            history,
            protocol);
    }

    public static IReadOnlyList<StrategyScenario> Strategy(JsonElement response)
    {
        if (response.ValueKind != JsonValueKind.Array)
        {
            return [];
        }

        return response.EnumerateArray().Select((run, index) =>
        {
            var green = Integer(run, "green_laps");
            var caution = Integer(run, "caution_laps");
            var fuel = Number(run, "fuel_used_l");
            var fuelText = fuel is null ? string.Empty : $"{fuel.Value / 3.785411784:0.00} gal used";
            var runNumber = Integer(run, "run_number");
            return new StrategyScenario(
                runNumber > 0 ? $"Recorded run {runNumber}" : $"Recorded run {index + 1}",
                $"{green} green · {caution} caution laps",
                Text(run, "session_start") ?? Text(run, "analyzed_at") ?? "Recorded session",
                fuelText,
                "A comparable real run from your local race history.",
                EvidenceKind.Measured);
        }).ToArray();
    }

    public static RacePlanBriefing Plan(JsonElement response, int requestedDistanceValue = 0, string? requestedDistanceMode = null)
    {
        var view = Object(response, "analysis_view");
        var identity = Object(view, "identity");
        var summary = Object(view, "race_summary");
        var strategy = Object(view, "strategy");
        var forecast = Object(strategy, "forecast");
        var card = Object(response, "race_card");
        var playbook = Object(card, "corner_playbook");
        var range = Numbers(forecast, "all_green_range_laps").ToArray();
        var rangeText = range.Length switch
        {
            >= 2 => $"{range.Min():0.0}–{range.Max():0.0} green laps",
            1 => $"About {range[0]:0.0} green laps",
            _ => "Build two clean fuel samples to calculate range"
        };
        var scheduledLaps = Integer(summary, "scheduled_laps");
        var plannedLaps = scheduledLaps;
        var distanceLabel = string.Empty;
        if (requestedDistanceValue > 0 && requestedDistanceMode?.Equals("Laps", StringComparison.OrdinalIgnoreCase) == true)
        {
            plannedLaps = requestedDistanceValue;
            distanceLabel = $" for {plannedLaps} all-green laps";
        }
        else if (requestedDistanceValue > 0 && requestedDistanceMode?.Equals("Minutes", StringComparison.OrdinalIgnoreCase) == true)
        {
            var representativeLapSeconds = RepresentativeLapSeconds(view);
            plannedLaps = representativeLapSeconds.HasValue ? (int)Math.Ceiling(requestedDistanceValue * 60d / representativeLapSeconds.Value) : 0;
            distanceLabel = plannedLaps > 0 ? $" in a {requestedDistanceValue}-minute all-green race (about {plannedLaps} laps)" : string.Empty;
        }

        int? calculatedStops = null;
        if (requestedDistanceValue > 0 && plannedLaps > 0 && range.Length > 0 && range.Min() > 0)
            calculatedStops = Math.Max(0, (int)Math.Ceiling(plannedLaps / range.Min()) - 1);
        else
            calculatedStops = NullableInteger(forecast, "minimum_stops_all_green");
        var stopCount = calculatedStops is { } stops
            ? $"{stops} stop{(stops == 1 ? string.Empty : "s")}{distanceLabel}"
            : "Stop count needs recorded fuel range and race distance";

        var sourcePitTargets = Array(forecast, "equal_stint_pit_targets_all_green").Select(NullableIntegerValue).Where(value => value.HasValue && value.Value > 0).Select(value => value!.Value).ToArray();
        var pitTargets = requestedDistanceValue > 0 && plannedLaps > 0 && calculatedStops is > 0
            ? Enumerable.Range(1, calculatedStops.Value).Select(stop => (int)Math.Round(plannedLaps * stop / (calculatedStops.Value + 1d), MidpointRounding.AwayFromZero)).ToArray()
            : requestedDistanceValue > 0 ? [] : sourcePitTargets;
        var sourceActions = Array(card, "actions").Select(item => new RaceAction(Text(item, "label") ?? "Priority", Claim(item))).ToArray();
        var sourceStart = sourceActions.FirstOrDefault(item => item.Label.Equals("Start", StringComparison.OrdinalIgnoreCase));
        var sourcePace = sourceActions.FirstOrDefault(item =>
            item.Label.Contains("long", StringComparison.OrdinalIgnoreCase) ||
            item.Label.Contains("pace", StringComparison.OrdinalIgnoreCase) ||
            item.Label.Contains("tire", StringComparison.OrdinalIgnoreCase));
        var tire = PlanningTire(sourcePace?.Claim.Text);
        var shortRace = plannedLaps is > 0 and <= 25;
        var startClaim = PlanningStartClaim(sourceStart?.Claim, tire);
        var paceClaim = PlanningPaceClaim(sourcePace?.Claim, tire, plannedLaps, shortRace);
        var strategyClaim = PlanningStrategyClaim(
            sourceActions.FirstOrDefault(item => item.Label.Equals("Strategy", StringComparison.OrdinalIgnoreCase))?.Claim,
            plannedLaps,
            range,
            calculatedStops,
            pitTargets);
        var actions = new[]
        {
            new RaceAction("Start", startClaim),
            new RaceAction(shortRace ? "Race pace" : "Long run", paceClaim),
            new RaceAction("Strategy", strategyClaim)
        };
        var triggers = PlanningTriggers(
            Array(card, "race_triggers").Select(item => new RaceTrigger(Text(item, "label") ?? "Race trigger", Claim(item))).ToArray(),
            plannedLaps,
            range,
            calculatedStops,
            pitTargets);
        return new RacePlanBriefing(
            DisplayTrack(Text(identity, "track_name") ?? Text(identity, "track_path")) ?? "Recorded track", DisplayCar(Text(identity, "car_name") ?? Text(identity, "car_path")) ?? "Recorded car",
            Boolean(identity, "is_fixed_setup") == true ? "Fixed" : "Open", plannedLaps > 0 ? plannedLaps : scheduledLaps,
            Number(strategy, "measured_green_fuel_gal_per_lap"), Number(strategy, "measured_caution_fuel_gal_per_lap"),
            rangeText, stopCount, pitTargets,
            paceClaim.Text, actions,
            Array(playbook, "rows").Select(row => new CornerCoachingRow(
                Text(row, "corner_phase") ?? Text(row, "zone_id") ?? "Recorded load zone",
                Claim(Object(row, "phase_1")), Claim(Object(row, "phase_2")), Claim(Object(row, "phase_3")), Claim(Object(row, "groove")))).ToArray(),
            triggers,
            Array(forecast, "assumptions").Select(Value).Where(value => value.Length > 0).ToArray(),
            Humanize(Text(strategy, "confidence")) ?? "Low");
    }

    private static string? PlanningTire(string? text)
    {
        if (string.IsNullOrWhiteSpace(text)) return null;
        var match = Regex.Match(text, @"\b(LF|RF|LR|RR)\b", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
        return match.Success ? match.Value.ToUpperInvariant() : null;
    }

    private static EvidenceText PlanningStartClaim(EvidenceText? source, string? tire)
    {
        if (PlanningInstructionIsActionable(source)) return source!;
        return new EvidenceText(
            EvidenceKind.Inferred,
            tire is not null
                ? $"Protect the {tire} from the start: finish brake release before adding steering."
                : "Open conservatively: finish brake release before adding steering, then build throttle.");
    }

    private static EvidenceText PlanningPaceClaim(EvidenceText? source, string? tire, int plannedLaps, bool shortRace)
    {
        if (source?.Text.Contains("repair", StringComparison.OrdinalIgnoreCase) == true)
        {
            return new EvidenceText(
                EvidenceKind.Inferred,
                "Reset the baseline after repairs; judge pace only on a clean repaired-car run.");
        }
        if (shortRace && plannedLaps > 0)
        {
            return new EvidenceText(
                EvidenceKind.Inferred,
                tire is not null
                    ? $"For all {plannedLaps} laps, protect the {tire} with repeatable entries and progressive brake release."
                    : $"For all {plannedLaps} laps, keep brake release, throttle pickup, and steering corrections repeatable.");
        }
        if (PlanningInstructionIsActionable(source)) return source!;
        return new EvidenceText(
            EvidenceKind.Inferred,
            tire is not null
                ? $"As the run ages, protect the {tire} with repeatable entries and progressive brake release."
                : "As the run ages, keep entries, throttle pickup, and steering corrections repeatable.");
    }

    private static EvidenceText PlanningStrategyClaim(
        EvidenceText? source,
        int plannedLaps,
        IReadOnlyList<double> range,
        int? stops,
        IReadOnlyList<int> pitTargets)
    {
        var conservativeRange = range.Count > 0 ? range.Min() : (double?)null;
        if (plannedLaps > 0 && conservativeRange is > 0 && stops == 0)
        {
            var margin = Math.Max(0, conservativeRange.Value - plannedLaps);
            return new EvidenceText(
                EvidenceKind.Derived,
                $"No fuel stop for {plannedLaps} laps; protect the {margin:0.0}-lap conservative finish margin.");
        }
        if (plannedLaps > 0 && stops is > 0)
        {
            var target = pitTargets.Count > 0
                ? $"; target {string.Join(" and ", pitTargets.Select(lap => $"Lap {lap}"))}"
                : string.Empty;
            return new EvidenceText(
                EvidenceKind.Derived,
                $"Plan {stops} fuel stop{(stops == 1 ? string.Empty : "s")} for {plannedLaps} laps{target}.");
        }
        if (source is { Kind: not EvidenceKind.Unavailable } && !string.IsNullOrWhiteSpace(source.Text)) return source;
        return new EvidenceText(EvidenceKind.Unavailable, "A finish-range decision needs another clean fuel sample.");
    }

    private static IReadOnlyList<RaceTrigger> PlanningTriggers(
        IReadOnlyList<RaceTrigger> source,
        int plannedLaps,
        IReadOnlyList<double> range,
        int? stops,
        IReadOnlyList<int> pitTargets)
    {
        var result = new List<RaceTrigger>();
        var phase = source.FirstOrDefault(item =>
            item.Label.Contains("checkpoint", StringComparison.OrdinalIgnoreCase) ||
            item.Label.Contains("evolution", StringComparison.OrdinalIgnoreCase));
        if (phase is { Claim.Kind: not EvidenceKind.Unavailable } && !string.IsNullOrWhiteSpace(phase.Claim.Text))
        {
            var text = Regex.Replace(phase.Claim.Text, @"^Set-age\s+", "Recheck balance at ", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant)
                .Replace("->", "-");
            result.Add(new RaceTrigger("Balance checkpoint", new EvidenceText(phase.Claim.Kind, text)));
        }

        var conservativeRange = range.Count > 0 ? range.Min() : (double?)null;
        if (plannedLaps > 0 && conservativeRange is > 0 && stops == 0)
        {
            result.Add(new RaceTrigger(
                "Fuel margin",
                new EvidenceText(EvidenceKind.Derived,
                    $"Stay out while projected range clears the {plannedLaps}-lap finish; reconsider only if the margin disappears.")));
        }
        else if (plannedLaps > 0 && stops is > 0)
        {
            var window = pitTargets.Count > 0
                ? string.Join(" or ", pitTargets.Select(lap => $"Lap {lap}"))
                : "the balanced stint window";
            result.Add(new RaceTrigger(
                "Pit window",
                new EvidenceText(EvidenceKind.Derived,
                    $"Target {window}; move the stop only when live burn no longer supports the next stint.")));
        }
        else
        {
            result.Add(new RaceTrigger(
                "Fuel check",
                new EvidenceText(EvidenceKind.Inferred,
                    "Update the fuel call only after live burn establishes a finish margin.")));
        }

        var repair = source.FirstOrDefault(item => item.Claim.Text.Contains("repaired-car", StringComparison.OrdinalIgnoreCase));
        result.Add(repair is not null
            ? new RaceTrigger("Repair baseline", repair.Claim)
            : new RaceTrigger(
                "Balance response",
                new EvidenceText(EvidenceKind.Inferred,
                    "If balance changes, alter one driving input at a time; undo it if pace or stability worsens.")));
        return result;
    }

    private static bool PlanningInstructionIsActionable(EvidenceText? claim)
    {
        if (claim is null || claim.Kind == EvidenceKind.Unavailable || string.IsNullOrWhiteSpace(claim.Text)) return false;
        if (Regex.IsMatch(
            claim.Text,
            @"\b(?:positive|negative) (?:value )?(?:is|means)|\b(?:higher|lower) (?:means|indicates)|\bthis (?:value|metric|measurement) (?:means|describes)|\bcompare the same run(?:'s)? tire condition\b",
            RegexOptions.IgnoreCase | RegexOptions.CultureInvariant))
            return false;
        var opening = claim.Text.Split(' ', StringSplitOptions.RemoveEmptyEntries).FirstOrDefault()?.TrimEnd(':').ToLowerInvariant();
        return opening is "avoid" or "brake" or "build" or "carry" or "enter" or "exit" or "feed" or "finish" or "hold" or "keep" or "open" or "protect" or "reduce" or "release" or "reset" or "roll" or "stabilize" or "turn" or "unwind" or "validate";
    }

    private static double? RepresentativeLapSeconds(JsonElement view)
    {
        var traceTimes = Array(Object(view, "lap_traces"), "traces").Select(trace => Number(trace, "lap_time_s"));
        var lapTimes = Array(Object(view, "lap_table"), "laps").Select(lap => Number(lap, "lap_time_s"));
        var runTimes = Array(view, "runs").SelectMany(run =>
        {
            var pace = Object(run, "pace");
            return new[] { Number(pace, "early_average_lap_s"), Number(pace, "late_average_lap_s") };
        });
        var values = traceTimes.Concat(lapTimes).Concat(runTimes).Where(value => value is > 0).Select(value => value!.Value).Order().ToArray();
        if (values.Length == 0) return null;
        var middle = values.Length / 2;
        return values.Length % 2 == 0 ? (values[middle - 1] + values[middle]) / 2d : values[middle];
    }

    private static TuningSessionIdentity MapTuningIdentity(
        JsonElement response,
        JsonElement view,
        JsonElement selection,
        JsonElement identity,
        AnalysisTrackGeometry? geometry)
    {
        var source = Object(view, "source");
        var artifacts = Object(response, "artifacts");
        var embeddedSetup = Object(identity, "setup");
        var sourceHashes = (geometry?.SourceSha256 ?? [])
            .Concat(geometry?.ObservedSourceSha256 ?? [])
            .Concat(Array(source, "fingerprints").Select(item => Text(item, "sha256") ?? string.Empty))
            .Where(value => value.Length > 0)
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .OrderBy(value => value, StringComparer.OrdinalIgnoreCase)
            .ToArray();
        var fixedSetup = Boolean(identity, "is_fixed_setup");
        var setupType = fixedSetup switch
        {
            true => "Fixed",
            false => "Open",
            _ => Humanize(Text(identity, "setup_type")) ?? "Unknown"
        };
        var embeddedAvailable = Boolean(identity, "embedded_setup_available") == true
            || Integer(identity, "setup_parameter_count") > 0
            || embeddedSetup.ValueKind == JsonValueKind.Object && embeddedSetup.EnumerateObject().Any();
        return new TuningSessionIdentity
        {
            Selector = Text(selection, "selector") ?? Text(selection, "group_id") ?? string.Empty,
            AnalysisId = Text(response, "analysis_id") ?? Text(view, "analysis_id") ?? string.Empty,
            AnalysisPath = Text(response, "analysis_path") ?? Text(artifacts, "analysis") ?? Text(view, "analysis_path") ?? string.Empty,
            SessionId = Text(identity, "session_id") ?? Text(selection, "sim_session_num") ?? string.Empty,
            SessionUniqueId = Text(identity, "session_unique_id") ?? string.Empty,
            SubsessionId = Text(identity, "subsession_id") ?? Text(selection, "subsession_id") ?? string.Empty,
            SessionType = Text(selection, "sim_session_type") ?? Text(identity, "event_type") ?? string.Empty,
            TrackConfigurationKey = geometry?.TrackConfigurationKey ?? Text(identity, "track_configuration_key") ?? string.Empty,
            TrackId = Text(identity, "track_id") ?? string.Empty,
            Track = DisplayTrack(Text(identity, "track_name") ?? Text(identity, "track_path")) ?? string.Empty,
            Layout = DisplayLayout(Text(identity, "track_config")),
            CarId = Text(identity, "car_id") ?? string.Empty,
            CarPath = Text(identity, "car_path") ?? string.Empty,
            Car = DisplayCar(Text(identity, "car_name") ?? Text(identity, "car_path")) ?? string.Empty,
            SetupType = setupType,
            SetupFingerprint = Text(identity, "setup_fingerprint") ?? string.Empty,
            EmbeddedSetupAvailable = embeddedAvailable,
            SourceSha256 = sourceHashes
        };
    }

    private static TuningMapView MapTuningMap(
        JsonElement view,
        AnalysisTrackGeometry? geometry,
        IReadOnlyList<TrackShapePoint> shape,
        IReadOnlyList<TrackSegment> segments,
        TuningSessionIdentity identity)
    {
        var supplied = Object(view, "tuning_map");
        var suppliedPath = Array(supplied, "path").Concat(Array(supplied, "main_path"))
            .Select(TuningPoint)
            .Where(point => double.IsFinite(point.X) && double.IsFinite(point.Y))
            .ToArray();
        var path = suppliedPath.Length > 1
            ? suppliedPath
            : geometry?.MainPath is { Count: > 1 } vector
                ? vector.Select((point, index) => new TuningMapPoint(
                    point.LapPercent ?? (vector.Count <= 1 ? 0 : index / (double)(vector.Count - 1)),
                    point.X,
                    point.Y)).ToArray()
                : shape.Select(point => new TuningMapPoint(point.LapPercent, point.X, point.Y)).ToArray();

        var suppliedTurns = Array(supplied, "turns").Concat(Array(supplied, "corners"))
            .Select((turn, index) => new TuningTurn(
                Text(turn, "corner_id") ?? Text(turn, "id") ?? $"turn-{index + 1}",
                Text(turn, "label") ?? $"Turn {index + 1}",
                Number(turn, "start_pct") ?? 0,
                Number(turn, "apex_pct") ?? MidpointPercent(Number(turn, "start_pct") ?? 0, Number(turn, "end_pct") ?? 0),
                Number(turn, "end_pct") ?? 0,
                Boolean(turn, "is_official") == true,
                Text(turn, "confidence") ?? "Unknown",
                Text(turn, "correction_hint")))
            .ToArray();
        var turns = suppliedTurns.Length > 0
            ? suppliedTurns
            : segments.Select(segment => new TuningTurn(
                $"detected-{segment.Number}",
                segment.Label,
                segment.StartPercent,
                MidpointPercent(segment.StartPercent, segment.EndPercent),
                segment.EndPercent,
                false,
                "Detected",
                "Verify the label and three bounds against the official track map before using this as a named corner."))
                .ToArray();

        var mapIdentity = Text(supplied, "map_identity") ?? Text(supplied, "map_id")
            ?? identity.TrackConfigurationKey
            ?? string.Empty;
        if (string.IsNullOrWhiteSpace(mapIdentity)) mapIdentity = $"analysis:{identity.AnalysisId}";
        var hasSuppliedMap = supplied.ValueKind == JsonValueKind.Object && suppliedPath.Length > 1;
        return new TuningMapView(
            mapIdentity,
            Text(supplied, "source_type") ?? "telemetry-derived",
            Text(supplied, "source_label") ?? (hasSuppliedMap ? "Verified track map" : "Recorded racing geometry"),
            Text(supplied, "source_url"),
            Text(supplied, "confidence") ?? (path.Count() > 1 ? "Recorded" : "Unavailable"),
            Text(supplied, "verification_message") ?? (hasSuppliedMap ? null : "Detected turn labels and bounds are not official until the driver verifies them."),
            Boolean(supplied, "is_verified") == true,
            path,
            turns,
            Text(supplied, "geometry_hash") ?? geometry?.GeometryHash);
    }

    private static TuningMapPoint TuningPoint(JsonElement point) => new(
        Number(point, "lap_pct") ?? Number(point, "lap_percent") ?? 0,
        Number(point, "x") ?? 0,
        Number(point, "y") ?? 0);

    private static double MidpointPercent(double start, double end)
    {
        var normalizedStart = ((start % 1) + 1) % 1;
        var normalizedEnd = ((end % 1) + 1) % 1;
        var distance = normalizedEnd >= normalizedStart
            ? normalizedEnd - normalizedStart
            : 1 - normalizedStart + normalizedEnd;
        return (normalizedStart + distance / 2) % 1;
    }

    private static AnalysisTrackGeometry? MapTrackGeometry(JsonElement geometry)
    {
        if (geometry.ValueKind != JsonValueKind.Object) return null;
        var points = (string property) => Array(geometry, property).Select(VectorPoint).ToArray();
        var legacySources = Hashes(geometry, "source_sha256");
        var contributingSources = Hashes(geometry, "contributing_source_sha256");
        if (contributingSources.Count == 0) contributingSources = legacySources;
        var observedSources = Hashes(geometry, "observed_source_sha256");
        if (observedSources.Count == 0) observedSources = legacySources;
        var provenance = MapGeometryProvenance(Object(geometry, "geometry_provenance"));
        var transform = MapGeometryTransform(Object(geometry, "transform")) ?? provenance?.NormalizationTransform;
        var quality = MapTrackGeometryQuality(Object(geometry, "quality"));
        return new AnalysisTrackGeometry(
            Text(geometry, "status") ?? "unavailable",
            Text(geometry, "track_configuration_key") ?? string.Empty,
            Text(geometry, "coordinate_system") ?? string.Empty,
            points("main_path"),
            points("pit_lane"),
            points("pit_entry_path"),
            points("pit_exit_path"),
            VectorLine(Object(geometry, "start_finish_line")),
            VectorLine(Object(geometry, "pit_commitment_line")),
            VectorLine(Object(geometry, "pit_merge_line")),
            Array(geometry, "unavailable_reasons").Select(Value).Where(value => value.Length > 0).ToArray(),
            contributingSources,
            observedSources,
            transform,
            provenance,
            quality,
            Text(geometry, "geometry_hash"));
    }

    private static AnalysisTrackGeometryQuality? MapTrackGeometryQuality(JsonElement quality)
    {
        if (quality.ValueKind != JsonValueKind.Object) return null;
        var mapped = new AnalysisTrackGeometryQuality(
            Boolean(quality, "main_loop_complete"),
            Number(quality, "lap_percent_coverage"),
            Number(quality, "maximum_lap_percent_gap"),
            Number(quality, "closure_distance"));
        return mapped.MainLoopComplete.HasValue
            || mapped.LapPercentCoverage.HasValue
            || mapped.MaximumLapPercentGap.HasValue
            || mapped.ClosureDistance.HasValue
                ? mapped
                : null;
    }

    private static AnalysisGeometryTransform? MapGeometryTransform(JsonElement transform)
    {
        if (transform.ValueKind != JsonValueKind.Object) return null;
        var bounds = Object(transform, "source_bounds");
        var sourceBounds = bounds.ValueKind == JsonValueKind.Object
            && Number(bounds, "minimum_x") is { } minimumX
            && Number(bounds, "maximum_x") is { } maximumX
            && Number(bounds, "minimum_y") is { } minimumY
            && Number(bounds, "maximum_y") is { } maximumY
                ? new AnalysisGeometrySourceBounds(minimumX, maximumX, minimumY, maximumY)
                : null;
        var mapped = new AnalysisGeometryTransform(sourceBounds, Number(transform, "normalization_scale"));
        return mapped.IsUsable ? mapped : null;
    }

    private static AnalysisGeometryProvenance? MapGeometryProvenance(JsonElement provenance)
    {
        if (provenance.ValueKind != JsonValueKind.Object) return null;
        var observations = Array(provenance, "observations").Select(item => new AnalysisGeometryObservation(
            Text(item, "observation_id") ?? string.Empty,
            Hashes(item, "source_sha256"),
            MapGeometryTransform(Object(item, "transform")),
            Text(item, "geometry_fingerprint"),
            ParseTimestamp(Text(item, "observed_at")),
            NumberObject(Object(item, "quality"))))
            .Where(item => item.ObservationId.Length > 0)
            .ToArray();
        return new AnalysisGeometryProvenance(
            Text(provenance, "selected_observation_id"),
            MapGeometryTransform(Object(provenance, "normalization_transform")),
            observations);
    }

    private static AnalysisVectorPoint VectorPoint(JsonElement point) => new(
        Number(point, "x") ?? 0,
        Number(point, "y") ?? 0,
        Number(point, "lap_pct"),
        Integer(point, "observations"));

    private static AnalysisVectorLine? VectorLine(JsonElement line)
    {
        if (line.ValueKind != JsonValueKind.Object) return null;
        var a = Object(line, "a");
        var b = Object(line, "b");
        return a.ValueKind == JsonValueKind.Object && b.ValueKind == JsonValueKind.Object
            ? new AnalysisVectorLine(VectorPoint(a), VectorPoint(b))
            : null;
    }

    private static AnalysisRaceReplay? MapRaceReplay(JsonElement replay)
    {
        if (replay.ValueKind != JsonValueKind.Object) return null;
        var coverage = Array(replay, "coverage").Select(item => new AnalysisReplayCoverage(
            Text(item, "channel") ?? string.Empty,
            Text(item, "status") ?? "unavailable",
            Text(item, "reason"),
            NullableInteger(item, "recorded_segment_count"),
            NullableInteger(item, "segment_count"),
            Number(item, "recorded_fraction"),
            Boolean(item, "all_segments_recorded"),
            NullableInteger(item, "temporal_gap_count"))).ToArray();
        var temporalRoot = Object(replay, "temporal_coverage");
        var temporalCoverage = temporalRoot.ValueKind == JsonValueKind.Object
            ? new AnalysisReplayTemporalCoverage(
                Text(temporalRoot, "status") ?? "unavailable",
                NullableInteger(temporalRoot, "recorded_frame_count"),
                NullableInteger(temporalRoot, "expected_frame_count"),
                Number(temporalRoot, "recorded_fraction"),
                NullableInteger(temporalRoot, "gap_count"),
                Number(temporalRoot, "largest_gap_s"),
                Number(temporalRoot, "start_session_time_s"),
                Number(temporalRoot, "end_session_time_s"))
            : null;
        var participantCoverage = Array(replay, "participant_coverage").Select(item => new AnalysisReplayParticipantCoverage(
            Integer(item, "car_index"),
            Text(item, "status") ?? "unavailable",
            NullableInteger(item, "recorded_frame_count"),
            NullableInteger(item, "total_frame_count"),
            Number(item, "recorded_fraction"),
            NullableInteger(item, "recorded_segment_count"),
            NullableInteger(item, "segment_count"),
            Number(item, "first_session_time_s"),
            Number(item, "last_session_time_s"))).ToArray();
        var participants = Array(replay, "participants").Select(item => new AnalysisReplayParticipant(
            Integer(item, "car_index"),
            Text(item, "car_number"),
            NullableInteger(item, "class_id"),
            Text(item, "class_name"),
            Text(item, "car_name"),
            Text(item, "driver_name"),
            Text(item, "team_name"),
            Boolean(item, "is_player") == true,
            Boolean(item, "is_spectator") == true)).ToArray();
        var frames = Array(replay, "frames").Select(frame =>
        {
            var player = Object(frame, "player_telemetry");
            var playerTelemetry = player.ValueKind == JsonValueKind.Object
                ? new AnalysisReplayPlayerTelemetry(
                    NullableInteger(player, "incidentPoints"),
                    NullableInteger(player, "driverIncidentPoints"),
                    NullableInteger(player, "teamIncidentPoints"),
                    NullableInteger(player, "trackSurface"),
                    Boolean(player, "onPitRoad"),
                    Boolean(player, "towing"),
                    Boolean(player, "repairRequired"),
                    Number(player, "mandatoryRepairSeconds"),
                    Number(player, "optionalRepairSeconds"),
                    Number(player, "speedMetersPerSecond"),
                    Number(player, "throttle"),
                    Number(player, "brake"),
                    Number(player, "steeringWheelAngleRadians"),
                    NullableInteger(player, "gear"),
                    Number(player, "rpm"),
                    Number(player, "yawRateRadiansPerSecond"),
                    Number(player, "lateralAccelerationG"),
                    Number(player, "longitudinalAccelerationG"))
                : null;
            var events = Array(frame, "events").Select(item => new AnalysisReplayObservedEvent(
                Text(item, "kind") ?? "event",
                Text(item, "label") ?? "Recorded event",
                Text(item, "sourceChannel") ?? Text(item, "source_channel"),
                Number(item, "delta"))).ToArray();
            var cars = Array(frame, "cars").Select(MapReplayCarObject).ToArray();
            if (cars.Length == 0)
                cars = Array(frame, "car_rows")
                    .Where(row => row.ValueKind == JsonValueKind.Array)
                    .Select(MapReplayCarRow)
                    .ToArray();
            return new AnalysisReplayFrame(
                Number(frame, "session_time_s") ?? 0,
                Text(frame, "session_state") ?? "unknown",
                Long(frame, "global_flags") ?? 0,
                Array(frame, "global_flag_labels").Select(Value).Where(value => value.Length > 0).ToArray(),
                cars,
                playerTelemetry,
                events,
                Boolean(frame, "gap_before") == true);
        }).ToArray();
        var representationRoot = Object(replay, "representation");
        var representation = representationRoot.ValueKind == JsonValueKind.Object
            ? new AnalysisReplayRepresentation(
                NullableInteger(representationRoot, "source_frame_count"),
                NullableInteger(representationRoot, "display_frame_count"),
                Number(representationRoot, "source_sample_rate_hz"),
                Number(representationRoot, "display_sample_rate_hz"),
                NullableInteger(representationRoot, "frame_budget"),
                Boolean(representationRoot, "decimated"),
                Number(representationRoot, "routine_interval_s"),
                Boolean(representationRoot, "keyframes_preserved"),
                NullableInteger(representationRoot, "dropped_keyframe_count"))
            : null;
        return new AnalysisRaceReplay(
            Text(replay, "status") ?? "unavailable",
            Array(replay, "unavailable_reasons").Select(Value).Where(value => value.Length > 0).ToArray(),
            Array(replay, "limitations").Select(Value).Where(value => value.Length > 0).ToArray(),
            coverage,
            participants,
            frames,
            Number(replay, "sample_rate_hz"),
            NullableInteger(replay, "player_car_index"),
            Text(replay, "interpolation") ?? string.Empty,
            temporalCoverage,
            participantCoverage,
            representation);
    }

    private static AnalysisReplayCarState MapReplayCarObject(JsonElement car) => new(
        Integer(car, "car_index"),
        Number(car, "lap_pct"),
        NullableInteger(car, "lap"),
        NullableInteger(car, "completed_laps"),
        NullableInteger(car, "overall_position"),
        NullableInteger(car, "class_position"),
        Boolean(car, "on_pit_road"),
        NullableInteger(car, "track_surface"),
        Text(car, "track_surface_label"),
        NullableInteger(car, "pace_flags"),
        Number(car, "last_lap_time_s"),
        Number(car, "best_lap_time_s"));

    private static AnalysisReplayCarState MapReplayCarRow(JsonElement row) => new(
        ReplayRowInteger(row, 0) ?? 0,
        ReplayRowNumber(row, 1),
        ReplayRowInteger(row, 2),
        ReplayRowInteger(row, 3),
        ReplayRowInteger(row, 4),
        ReplayRowInteger(row, 5),
        ReplayRowBoolean(row, 6),
        ReplayRowInteger(row, 7),
        null,
        ReplayRowInteger(row, 8),
        ReplayRowNumber(row, 9),
        ReplayRowNumber(row, 10));

    private static JsonElement ReplayRowValue(JsonElement row, int index) =>
        row.ValueKind == JsonValueKind.Array && index >= 0 && index < row.GetArrayLength()
            ? row[index]
            : default;

    private static double? ReplayRowNumber(JsonElement row, int index)
    {
        var value = ReplayRowValue(row, index);
        return value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out var parsed) && double.IsFinite(parsed)
            ? parsed
            : null;
    }

    private static int? ReplayRowInteger(JsonElement row, int index)
    {
        var value = ReplayRowValue(row, index);
        if (value.ValueKind != JsonValueKind.Number) return null;
        if (value.TryGetInt32(out var integer)) return integer;
        return value.TryGetDouble(out var parsed) && double.IsFinite(parsed)
            ? (int)Math.Round(parsed)
            : null;
    }

    private static bool? ReplayRowBoolean(JsonElement row, int index)
    {
        var value = ReplayRowValue(row, index);
        return value.ValueKind switch
        {
            JsonValueKind.True => true,
            JsonValueKind.False => false,
            _ => null
        };
    }

    private static AnalysisTireLearningPrediction? MapTirePrediction(JsonElement learning)
    {
        if (learning.ValueKind != JsonValueKind.Object) return null;
        var prediction = Object(learning, "prediction");
        if (prediction.ValueKind != JsonValueKind.Object) return null;
        var tireRoot = Object(prediction, "tires");
        AnalysisTireCornerPrediction[] tires = tireRoot.ValueKind != JsonValueKind.Object
            ? []
            : tireRoot.EnumerateObject().Select(corner =>
            {
                var bands = corner.Value.ValueKind != JsonValueKind.Object
                    ? new Dictionary<string, AnalysisTireBandPrediction>(StringComparer.OrdinalIgnoreCase)
                    : corner.Value.EnumerateObject().ToDictionary(
                        band => band.Name,
                        band => new AnalysisTireBandPrediction(
                            Number(band.Value, "remaining_percent"),
                            Number(band.Value, "low_percent"),
                            Number(band.Value, "high_percent"),
                            Number(band.Value, "wear_rate_percent_per_green_lap"),
                            Number(band.Value, "laps_remaining_to_zero")),
                        StringComparer.OrdinalIgnoreCase);
                return new AnalysisTireCornerPrediction(corner.Name, bands);
            }).ToArray();
        var persistent = Object(learning, "persistent_model");
        var context = Object(learning, "context");
        var matchingContext = context.ValueKind == JsonValueKind.Object
            ? context.EnumerateObject()
                .Select(property => (property.Name, Value: DisplayJson(property.Value)))
                .Where(item => item.Value.Length > 0)
                .ToDictionary(item => item.Name, item => item.Value, StringComparer.OrdinalIgnoreCase)
            : new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        return new AnalysisTireLearningPrediction(
            Text(prediction, "status") ?? "unavailable",
            Text(prediction, "reason"),
            Text(prediction, "evidence_class"),
            Text(prediction, "confidence"),
            Integer(prediction, "eligible_observations"),
            Integer(prediction, "matching_sessions"),
            Number(prediction, "laps_remaining"),
            Number(prediction, "pace_cost_s"),
            Number(prediction, "pace_cost_low_s"),
            Number(prediction, "pace_cost_high_s"),
            Number(prediction, "pace_slope_s_per_green_lap"),
            Number(prediction, "capability_pace_s"),
            Number(prediction, "capability_pace_low_s"),
            Number(prediction, "capability_pace_high_s"),
            tires,
            Text(persistent, "path"),
            Integer(persistent, "observation_count"),
            Text(prediction, "model_version") ?? Text(persistent, "model_version"),
            Text(prediction, "observation_set_fingerprint") ?? Text(persistent, "observation_set_fingerprint"),
            Integer(prediction, "total_observations"),
            Integer(prediction, "excluded_observations"),
            Number(prediction, "effective_matched_observations"),
            Number(prediction, "median_feature_distance"),
            Integer(prediction, "comparable_feature_count"),
            Array(prediction, "matched_features").Select(Value).Where(value => value.Length > 0).ToArray(),
            Array(prediction, "exclusion_reasons").Select(Value).Where(value => value.Length > 0).ToArray(),
            Text(prediction, "matching_scope"),
            matchingContext);
    }

    private static AnalysisGarage61References? MapGarage61References(JsonElement references, JsonElement envelope)
    {
        if (references.ValueKind != JsonValueKind.Object) return null;
        var comparisons = Array(references, "reference_comparisons")
            .Concat(Array(envelope, "reference_comparisons"))
            .Where(item => item.ValueKind == JsonValueKind.Object)
            .GroupBy(item => Text(item, "lap_id") ?? string.Empty, StringComparer.OrdinalIgnoreCase)
            .Where(group => group.Key.Length > 0)
            .ToDictionary(group => group.Key, group => group.First(), StringComparer.OrdinalIgnoreCase);
        var comparisonQuality = Object(references, "comparison_quality");
        if (comparisonQuality.ValueKind != JsonValueKind.Object) comparisonQuality = Object(envelope, "comparison_quality");
        var manifest = Object(Object(envelope, "cache"), "manifest");
        var retrievedAt = ParseTimestamp(
            Text(references, "retrieved_at") ??
            Text(references, "synced_at") ??
            Text(manifest, "refreshed_at"));
        var laps = Array(references, "representative_laps").Select(item =>
        {
            var lap = Object(item, "lap");
            var telemetry = Object(item, "telemetry");
            var driver = Object(lap, "driver");
            var driverName = Text(driver, "name") ?? Text(driver, "displayName") ?? Text(lap, "driverName") ?? string.Empty;
            var id = Text(lap, "id") ?? Text(item, "id") ?? string.Empty;
            comparisons.TryGetValue(id, out var comparison);
            var quality = Object(comparison, "quality");
            return new AnalysisGarage61ReferenceLap(
                id,
                Number(lap, "lapTime"),
                Text(item, "setup_type") ?? Text(lap, "setupType") ?? string.Empty,
                Text(item, "comparison_role") ?? "representative",
                Text(telemetry, "status") is "downloaded" or "cached" || Boolean(lap, "canViewTelemetry") == true,
                driverName,
                "Garage61",
                retrievedAt,
                Text(telemetry, "sha256"),
                Array(quality, "signals").Select(Value).Where(value => value.Length > 0).Distinct(StringComparer.OrdinalIgnoreCase).ToArray(),
                Text(quality, "status"),
                Boolean(quality, "usable"),
                NullableInteger(quality, "aligned_bins"),
                Number(quality, "coverage_fraction"));
        }).Where(item => item.Id.Length > 0).ToArray();
        var availableSignals = laps.SelectMany(lap => lap.AvailableSignals ?? [])
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .OrderBy(value => value, StringComparer.OrdinalIgnoreCase)
            .ToArray();
        return new AnalysisGarage61References(
            Text(references, "status") ?? "unavailable",
            Text(references, "reason"),
            Text(references, "comparison_scope"),
            laps,
            "Garage61",
            retrievedAt,
            Text(manifest, "source_hash"),
            availableSignals,
            Text(comparisonQuality, "status"),
            Text(comparisonQuality, "reason"),
            Text(comparisonQuality, "setup_scope"),
            NullableInteger(comparisonQuality, "usable_reference_laps"),
            Number(comparisonQuality, "median_coverage_fraction"));
    }

    private static DateTimeOffset? ParseTimestamp(string? value) =>
        DateTimeOffset.TryParse(value, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out var parsed)
            ? parsed.ToUniversalTime()
            : null;

    private static IReadOnlyList<AnalysisTechnicalInsight> MapTechnicalInsights(JsonElement view) =>
        Array(view, "technical_insights").Select(item => new AnalysisTechnicalInsight(
            Text(item, "key") ?? string.Empty,
            Text(item, "label") ?? Humanize(Text(item, "key")) ?? "Technical insight",
            Text(item, "status") ?? "unavailable",
            Text(item, "rating") ?? string.Empty,
            Text(item, "takeaway") ?? string.Empty,
            Array(item, "metrics").Select(metric => new AnalysisTechnicalMetric(
                Text(metric, "label") ?? string.Empty,
                Text(metric, "value") ?? string.Empty,
                Evidence(Text(metric, "evidence_type")),
                Text(metric, "detail") ?? string.Empty,
                Text(metric, "action") ?? string.Empty,
                Text(metric, "tone") ?? "neutral",
                Text(metric, "group") ?? string.Empty)).ToArray(),
            Array(item, "evidence").Select(Value).Where(value => value.Length > 0).ToArray(),
            Array(item, "unavailable_reasons").Select(Value).Where(value => value.Length > 0).ToArray()))
        .Where(item => item.Key.Length > 0)
        .ToArray();

    private static double? TireWearPercent(JsonElement tires, string corner)
    {
        var remaining = Number(Object(tires, corner), "average_remaining_percent");
        return remaining.HasValue ? Math.Clamp(100d - remaining.Value, 0d, 100d) : null;
    }

    private static AnalysisTireCondition? TireCondition(JsonElement tires, string corner)
    {
        var tire = Object(tires, corner);
        if (tire.ValueKind != JsonValueKind.Object) return null;
        var pressure = Object(tire, "pressure");
        double? averageWear = Number(tire, "average_remaining_percent") is { } averageRemaining
            ? Math.Clamp(100d - averageRemaining, 0d, 100d)
            : null;
        var wear = TireBands(Object(tire, "remaining_percent"), corner, string.Empty, remaining =>
            remaining.HasValue ? Math.Clamp(100d - remaining.Value, 0d, 100d) : null);
        var carcass = TireBands(Object(tire, "carcass_temperature_f"), corner, "C", value => value);
        var surface = TireBands(Object(tire, "surface_temperature_f"), corner, string.Empty, value => value);
        var pressurePsi = Number(pressure, "psi");
        if (!averageWear.HasValue && !HasValue(wear) && !HasValue(carcass) && !HasValue(surface) && !pressurePsi.HasValue) return null;
        return new AnalysisTireCondition(
            corner,
            averageWear,
            wear,
            carcass,
            surface,
            pressurePsi,
            Humanize(Text(pressure, "kind")) ?? string.Empty);
    }

    private static AnalysisTireBands TireBands(JsonElement values, string corner, string prefix, Func<double?, double?> convert)
    {
        var leftSide = corner.StartsWith('L');
        var outerKey = prefix + (leftSide ? "L" : "R");
        var innerKey = prefix + (leftSide ? "R" : "L");
        return new AnalysisTireBands(
            convert(Number(values, outerKey)),
            convert(Number(values, prefix + "M")),
            convert(Number(values, innerKey)));
    }

    private static bool HasValue(AnalysisTireBands values) =>
        values.Outer.HasValue || values.Middle.HasValue || values.Inner.HasValue;

    private static JsonElement Object(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object && element.TryGetProperty(property, out var value) && value.ValueKind == JsonValueKind.Object
            ? value : default;

    private static IEnumerable<JsonElement> Array(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object && element.TryGetProperty(property, out var value) && value.ValueKind == JsonValueKind.Array
            ? value.EnumerateArray().ToArray() : [];

    private static IReadOnlyDictionary<string, double> NumberDictionary(JsonElement element, string property)
    {
        if (element.ValueKind != JsonValueKind.Object ||
            !element.TryGetProperty(property, out var value) ||
            value.ValueKind != JsonValueKind.Object)
            return new Dictionary<string, double>(StringComparer.Ordinal);

        var result = new Dictionary<string, double>(StringComparer.Ordinal);
        foreach (var item in value.EnumerateObject())
            if (NumberValue(item.Value) is { } number)
                result[item.Name] = number;
        return result;
    }

    private static IReadOnlyDictionary<string, double> NumberObject(JsonElement element)
    {
        if (element.ValueKind != JsonValueKind.Object)
            return new Dictionary<string, double>(StringComparer.Ordinal);
        var result = new Dictionary<string, double>(StringComparer.Ordinal);
        foreach (var item in element.EnumerateObject())
            if (NumberValue(item.Value) is { } number)
                result[item.Name] = number;
        return result;
    }

    private static IReadOnlyList<string> Hashes(JsonElement element, string property) =>
        Array(element, property)
            .Select(Value)
            .Where(value => value.Length == 64 && value.All(Uri.IsHexDigit))
            .Select(value => value.ToLowerInvariant())
            .Distinct(StringComparer.Ordinal)
            .OrderBy(value => value, StringComparer.Ordinal)
            .ToArray();

    private static IEnumerable<double> Numbers(JsonElement element, string property)
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(property, out var value)) yield break;
        if (NumberValue(value) is { } scalar) { yield return scalar; yield break; }
        if (value.ValueKind != JsonValueKind.Array) yield break;
        foreach (var item in value.EnumerateArray()) if (NumberValue(item) is { } number) yield return number;
    }

    private static double? NumberOrFirst(JsonElement element, string property) => Numbers(element, property).Select(number => (double?)number).FirstOrDefault();

    private static string? Text(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object && element.TryGetProperty(property, out var value)
            ? Value(value) : null;

    private static string Value(JsonElement element) => element.ValueKind switch
    {
        JsonValueKind.String => element.GetString() ?? string.Empty,
        JsonValueKind.Number => element.GetRawText(),
        _ => string.Empty
    };

    private static string DisplayJson(JsonElement element) => element.ValueKind switch
    {
        JsonValueKind.String => element.GetString() ?? string.Empty,
        JsonValueKind.Number => element.GetRawText(),
        JsonValueKind.True => "Yes",
        JsonValueKind.False => "No",
        JsonValueKind.Array => string.Join(", ", element.EnumerateArray().Select(DisplayJson)),
        _ => string.Empty
    };

    private static bool? Boolean(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object && element.TryGetProperty(property, out var value)
            ? value.ValueKind switch { JsonValueKind.True => true, JsonValueKind.False => false, _ => null }
            : null;

    private static int Integer(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object && element.TryGetProperty(property, out var value)
            ? NullableIntegerValue(value) ?? 0 : 0;

    private static int? NullableInteger(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object && element.TryGetProperty(property, out var value)
            ? NullableIntegerValue(value) : null;

    private static long? Long(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object && element.TryGetProperty(property, out var value) && value.ValueKind == JsonValueKind.Number
            ? value.TryGetInt64(out var integer) ? integer
            : value.TryGetDouble(out var number) && double.IsFinite(number) ? (long)Math.Round(number) : null
            : null;

    private static double? Number(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object && element.TryGetProperty(property, out var value)
            ? NumberValue(value) : null;

    private static int? NullableIntegerValue(JsonElement value)
    {
        if (value.ValueKind != JsonValueKind.Number) return null;
        if (value.TryGetInt32(out var integer)) return integer;
        if (!value.TryGetDouble(out var number) || !double.IsFinite(number)) return null;
        var rounded = Math.Round(number);
        return Math.Abs(number - rounded) <= 1e-9 && rounded is >= int.MinValue and <= int.MaxValue
            ? (int)rounded : null;
    }

    private static double? NumberValue(JsonElement value) =>
        value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out var number) && double.IsFinite(number) ? number : null;

    private static EvidenceText Claim(JsonElement element) => new(
        Evidence(Text(element, "evidence_type")),
        Text(element, "text") ?? string.Empty);

    private static string RequireText(JsonElement element, string property, string label)
    {
        var value = Text(element, property);
        if (string.IsNullOrWhiteSpace(value)) throw new InvalidDataException($"The tuning response did not include a {label}.");
        return value;
    }

    private static EvidenceKind TuningEvidenceKind(JsonElement item)
    {
        var declared = Text(item, "evidence_type") ?? Text(item, "kind");
        if (!string.IsNullOrWhiteSpace(declared)) return Evidence(declared);
        return Text(item, "source")?.ToLowerInvariant() switch
        {
            "derived-from-recorded-telemetry" => EvidenceKind.Derived,
            "driver-report" => EvidenceKind.Inferred,
            _ => EvidenceKind.Unavailable
        };
    }

    private static string TuningObservationLabel(JsonElement item, int index)
    {
        var corner = Text(item, "corner_label") ?? Text(item, "corner_id");
        var phase = Humanize(Text(item, "run_phase"));
        if (!string.IsNullOrWhiteSpace(corner) && !string.IsNullOrWhiteSpace(phase)) return $"{corner} · {phase}";
        return !string.IsNullOrWhiteSpace(corner) ? corner : $"Evidence {index + 1}";
    }

    private static string TuningObservationValue(JsonElement item)
    {
        var symptom = Humanize(Text(item, "symptom_id"));
        var severity = NullableInteger(item, "severity");
        if (!string.IsNullOrWhiteSpace(symptom)) return severity.HasValue ? $"{symptom} · severity {severity}/5" : symptom;
        var metrics = Object(item, "metrics");
        if (metrics.ValueKind != JsonValueKind.Object) return string.Empty;
        return string.Join(" · ", metrics.EnumerateObject().Take(5).Select(property =>
            $"{Humanize(property.Name) ?? property.Name} {DisplayJson(property.Value)}"));
    }

    private static string TuningCandidateConfidence(JsonElement item)
    {
        var direct = Text(item, "confidence");
        if (!string.IsNullOrWhiteSpace(direct)) return direct;
        var confidence = Object(item, "confidence");
        return Number(confidence, "overall") is { } overall
            ? $"{overall * 100:0}%"
            : "Unknown";
    }

    private static EvidenceKind Evidence(string? value) => value?.ToLowerInvariant() switch
    {
        "measured" => EvidenceKind.Measured,
        "derived" => EvidenceKind.Derived,
        "inferred" => EvidenceKind.Inferred,
        "proxy" => EvidenceKind.Proxy,
        _ => EvidenceKind.Unavailable
    };

    private static string? Humanize(string? value)
    {
        if (string.IsNullOrWhiteSpace(value)) return null;
        var spaced = Regex.Replace(value.Replace('_', ' ').Replace('-', ' '), "(?<=[a-z0-9])(?=[A-Z])", " ");
        return CultureInfo.CurrentCulture.TextInfo.ToTitleCase(spaced);
    }

    private static bool ComparableLap(JsonElement lap, string? previousFlag) =>
        LapComparisonExclusionReasons(lap, previousFlag).Length == 0;

    private static string[] LapComparisonExclusionReasons(JsonElement lap, string? previousFlag)
    {
        var reasons = new List<string>();
        var flag = Text(lap, "flag_state")?.Trim().ToLowerInvariant() ?? string.Empty;
        if (Boolean(lap, "complete") != true) reasons.Add("partial");
        if (!string.Equals(flag, "green", StringComparison.Ordinal)) reasons.Add("caution_or_mixed");
        if ((Number(lap, "pit_time_s") ?? 0) >= 1) reasons.Add("pit");
        if (Number(lap, "racing_state_fraction") is { } racingFraction && racingFraction < .98)
            reasons.Add("not_racing_state");
        var cleanContext = Object(lap, "clean_context");
        if (Number(cleanContext, "on_track_fraction") is { } onTrackFraction && onTrackFraction < .98)
            reasons.Add("off_track");
        if (Number(cleanContext, "traffic_proximity_fraction") is { } trafficFraction && trafficFraction >= .10)
            reasons.Add("close_traffic");
        if (string.Equals(previousFlag, "caution", StringComparison.Ordinal) && string.Equals(flag, "green", StringComparison.Ordinal))
            reasons.Add("restart");
        var comparisonContext = Object(lap, "damage_repair_context");
        var contextReasons = ReasonCodes(comparisonContext, "exclusion_reason_codes");
        if (Boolean(comparisonContext, "automatic_coaching_reference_eligible") == false || contextReasons.Length > 0)
            reasons.AddRange(contextReasons.Length > 0 ? contextReasons : ["damage_repair_context"]);
        return reasons.Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
    }

    private static bool ComparisonContextEligible(JsonElement context) =>
        Boolean(context, "automatic_coaching_reference_eligible") != false &&
        ReasonCodes(context, "exclusion_reason_codes").Length == 0 &&
        ReasonCodes(context, "reason_codes").Length == 0;

    private static string[] ReasonCodes(JsonElement context, string property)
    {
        if (context.ValueKind != JsonValueKind.Object || !context.TryGetProperty(property, out var value)) return [];
        if (value.ValueKind == JsonValueKind.String)
            return string.IsNullOrWhiteSpace(value.GetString()) ? [] : [value.GetString()!];
        if (value.ValueKind != JsonValueKind.Array) return value.ValueKind is JsonValueKind.Null or JsonValueKind.Undefined ? [] : [value.GetRawText()];
        return value.EnumerateArray()
            .Where(item => item.ValueKind is not JsonValueKind.Null and not JsonValueKind.Undefined)
            .Select(item => !string.IsNullOrWhiteSpace(Value(item)) ? Value(item) : item.GetRawText())
            .Where(item => !string.IsNullOrWhiteSpace(item))
            .ToArray();
    }

    private static string ConfiguredWeightText(double? weight) =>
        weight.HasValue ? $"{weight.Value:0.#}% configured" : "configured weight unavailable";

    private static JsonElement AnalysisSelection(JsonElement response, JsonElement view)
    {
        var transport = Object(response, "selection");
        var recorded = Object(Object(view, "source"), "selection");
        var transportPhase = Text(transport, "sim_session_type");
        var recordedPhase = Text(recorded, "sim_session_type");
        if (!string.IsNullOrWhiteSpace(transportPhase) && !string.IsNullOrWhiteSpace(recordedPhase) &&
            !string.Equals(SessionPhase(transportPhase), SessionPhase(recordedPhase), StringComparison.Ordinal))
            throw new InvalidDataException($"The analysis response identified conflicting session phases ({transportPhase} and {recordedPhase}).");

        var transportGroup = SelectionGroup(transport, default);
        var recordedGroup = SelectionGroup(recorded, default);
        if (!string.IsNullOrWhiteSpace(transportGroup) && !string.IsNullOrWhiteSpace(recordedGroup) &&
            !string.Equals(transportGroup, recordedGroup, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("The analysis response identified conflicting recorded session groups.");
        return !string.IsNullOrWhiteSpace(transportPhase) ? transport : recorded;
    }

    private static string? SelectionGroup(JsonElement selection, JsonElement identity)
    {
        if (Text(selection, "group_id") is { Length: > 0 } groupId) return groupId;
        var subsession = Text(selection, "subsession_id") ?? Text(identity, "subsession_id");
        var simSession = Text(selection, "sim_session_num");
        return string.IsNullOrWhiteSpace(subsession) || string.IsNullOrWhiteSpace(simSession)
            ? null
            : $"subsession:{subsession}:{simSession}";
    }

    private static string SessionPhase(string? value) =>
        value?.Contains("qual", StringComparison.OrdinalIgnoreCase) == true
            ? "qualifying"
            : string.Equals(value, "race", StringComparison.OrdinalIgnoreCase)
                ? "race"
                : value?.Trim().ToLowerInvariant() ?? string.Empty;

    internal static string? DisplayCar(string? value)
    {
        if (string.IsNullOrWhiteSpace(value)) return null;
        var normalized = Regex.Replace(value.ToLowerInvariant(), "[^a-z0-9]+", string.Empty);
        if (normalized.Contains("toyotatundra2022")) return "Toyota Tundra TRD Pro";
        if (normalized.Contains("supra2019")) return "Toyota Supra Class B";
        if (normalized.Contains("acuraarx06gtp")) return "Acura ARX-06 GTP";
        if (normalized.Contains("dallarap217")) return "Dallara P217";
        if (normalized.Contains("porsche992rgt3")) return "Porsche 911 GT3 R (992)";
        var leaf = value.Replace('\\', '/').Split('/', StringSplitOptions.RemoveEmptyEntries).LastOrDefault() ?? value;
        leaf = Regex.Replace(leaf, "^(trucks|stockcars2|stockcars|road|dirt)\\s+", string.Empty, RegexOptions.IgnoreCase);
        leaf = Regex.Replace(leaf, "(?<=[A-Za-z])(?=\\d{4}$)", " ");
        return Humanize(leaf);
    }

    internal static string? DisplayTrack(string? value)
    {
        if (string.IsNullOrWhiteSpace(value)) return null;
        var normalized = Regex.Replace(value.ToLowerInvariant(), "[^a-z0-9]+", string.Empty);
        if (normalized.Contains("kentucky")) return "Kentucky Speedway";
        if (normalized.Contains("newhampshire")) return "New Hampshire Motor Speedway";
        var friendly = Regex.Replace(value, "\\b(19|20)\\d{2}\\b", string.Empty).Trim();
        friendly = Regex.Replace(friendly, "\\b(oval|road|short|full)\\b$", string.Empty, RegexOptions.IgnoreCase).Trim();
        return Humanize(friendly);
    }

    internal static string DisplayLayout(string? value) => string.IsNullOrWhiteSpace(value) ? string.Empty : Humanize(Regex.Replace(value, "\\b(19|20)\\d{2}\\b", string.Empty).Trim()) ?? string.Empty;

    private static string DisplaySetupRole(string? value)
    {
        if (string.IsNullOrWhiteSpace(value) || value.Equals("sto_only", StringComparison.OrdinalIgnoreCase)) return "Saved setup";
        return Humanize(value) ?? "Saved setup";
    }
}
