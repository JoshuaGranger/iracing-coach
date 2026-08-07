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
                    Number(point, "steering_rad"), Number(point, "steering_peak_rad"), Number(point, "slip_angle_deg"), NullableInteger(point, "gear"), Number(point, "rpm"),
                    Number(point, "yaw_rate_deg_s"), Number(point, "lateral_g"), Number(point, "longitudinal_g"),
                    Number(point, "lat"), Number(point, "lon"), Number(point, "tire_stress_proxy"))).ToArray(),
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
            var pitStop = hasPitStop
                ? new AnalysisPitStop(
                    serviceStart.HasValue && serviceEnd.HasValue ? Math.Max(0, serviceEnd.Value - serviceStart.Value) : null,
                    Number(pitService, "fuel_added_l") is { } fuelAddedLiters ? fuelAddedLiters / 3.785411784 : null,
                    Number(pitAssessment, "fuel_laps_remaining_at_end"),
                    Array(pitService, "tires_changed_observed").Select(Value).Where(value => value.Length > 0).ToArray(),
                    TireWearPercent(tires, "LF"), TireWearPercent(tires, "RF"), TireWearPercent(tires, "LR"), TireWearPercent(tires, "RR"),
                    repairCompletedSeconds > .005 ? repairCompletedSeconds : null,
                    Number(pitService, "penalty_served_s"))
                : null;
            return new AnalysisRun(
                runNumber, Array(run, "lap_numbers").Select(NullableIntegerValue).Where(value => value.HasValue && value.Value > 0).Select(value => value!.Value).ToArray(),
                (int)Math.Round(Number(run, "green_laps") ?? 0), (int)Math.Round(Number(run, "caution_laps") ?? 0), Number(fuel, "used_gal"),
                Number(pace, "green_lap_time_slope_s_per_lap"), Humanize(Text(run, "tire_measurement_status")) ?? "Tire reading unavailable",
                ComparisonContextEligible(damageContext),
                reasons.Length == 0 ? "Recorded run" : string.Join(", ", reasons.Select(Humanize).Where(value => value is not null)),
                Number(pace, "early_average_lap_s"), Number(pace, "late_average_lap_s"), Number(pace, "early_to_late_delta_s"),
                Number(tire, "lowest_remaining_percent"), Text(tire, "lowest_remaining_tire") ?? string.Empty,
                Number(drivingLoad, "early_brake_vs_late_percent"), Number(drivingLoad, "early_steer_vs_late_percent"), pitStop);
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
            .Select(item => new AnalysisIncident(Integer(item, "candidate_lap"), (int)Math.Round(Number(item, "points_added") ?? 0)))
            .Where(item => item.Lap > 0 && item.Points > 0)
            .GroupBy(item => item.Lap)
            .Select(group => new AnalysisIncident(group.Key, group.Sum(item => item.Points)))
            .OrderBy(item => item.Lap)
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
            Array(traceRoot, "sector_start_pcts").Select(NumberValue).Where(value => value.HasValue).Select(value => value!.Value).Order().ToArray());
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
        var actions = Array(card, "actions").Select(item => new RaceAction(Text(item, "label") ?? "Priority", Claim(item))).ToArray();
        var tire = actions.FirstOrDefault(item => item.Label.Contains("long", StringComparison.OrdinalIgnoreCase) || item.Label.Contains("tire", StringComparison.OrdinalIgnoreCase));
        return new RacePlanBriefing(
            DisplayTrack(Text(identity, "track_name") ?? Text(identity, "track_path")) ?? "Recorded track", DisplayCar(Text(identity, "car_name") ?? Text(identity, "car_path")) ?? "Recorded car",
            Boolean(identity, "is_fixed_setup") == true ? "Fixed" : "Open", plannedLaps > 0 ? plannedLaps : scheduledLaps,
            Number(strategy, "measured_green_fuel_gal_per_lap"), Number(strategy, "measured_caution_fuel_gal_per_lap"),
            rangeText, stopCount, pitTargets,
            tire?.Claim.Text ?? "Use the recorded early-, middle-, and late-run comparison to set tire-management targets.", actions,
            Array(playbook, "rows").Select(row => new CornerCoachingRow(
                Text(row, "corner_phase") ?? Text(row, "zone_id") ?? "Recorded load zone",
                Claim(Object(row, "phase_1")), Claim(Object(row, "phase_2")), Claim(Object(row, "phase_3")), Claim(Object(row, "groove")))).ToArray(),
            Array(card, "race_triggers").Select(item => new RaceTrigger(Text(item, "label") ?? "Race trigger", Claim(item))).ToArray(),
            Array(forecast, "assumptions").Select(Value).Where(value => value.Length > 0).ToArray(),
            Humanize(Text(strategy, "confidence")) ?? "Low");
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

    private static double? TireWearPercent(JsonElement tires, string corner)
    {
        var remaining = Number(Object(tires, corner), "average_remaining_percent");
        return remaining.HasValue ? Math.Clamp(100d - remaining.Value, 0d, 100d) : null;
    }

    private static JsonElement Object(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object && element.TryGetProperty(property, out var value) && value.ValueKind == JsonValueKind.Object
            ? value : default;

    private static IEnumerable<JsonElement> Array(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object && element.TryGetProperty(property, out var value) && value.ValueKind == JsonValueKind.Array
            ? value.EnumerateArray().ToArray() : [];

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
