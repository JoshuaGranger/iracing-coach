using System.Text.Json;
using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

/// <summary>Projects the backend investigation; never derives findings or repairs missing evidence.</summary>
public static class RaceInvestigationMapper
{
    public const int CurrentSchemaVersion = 2;
    /// <summary>Must equal race_investigation.PROJECTION_VERSION in the backend.</summary>
    public const string CurrentProjectionVersion = "sections-trials-v6";

    public static RaceInvestigation? Map(JsonElement source)
    {
        if (source.ValueKind is JsonValueKind.Undefined or JsonValueKind.Null) return null;
        var retained = source.Clone();
        try
        {
            var schema = RequiredInt(source, "schema_version");
            if (schema is not (1 or CurrentSchemaVersion))
                throw new JsonException("This pace investigation uses an unsupported version. Reanalyze the recording with the current app.");
            var current = schema == CurrentSchemaVersion;
            return new(schema, RequiredText(source, "status"), RequiredText(source, "summary"), Text(source, "default_comparison_id"),
                Array(source, "comparisons").Select(item => Comparison(item, current)).ToArray(),
                Array(source, "laps").Select(lap => new RaceInvestigationLap(RequiredInt(lap, "lap"), Number(lap, "lap_time_s"),
                    Integer(lap, "position"), RequiredText(lap, "flag_state"), RequiredBoolean(lap, "eligible"),
                    Strings(lap, "exclusion_reasons"), Integer(lap, "run_number"), RequiredBoolean(lap, "plot_pace"),
                    current ? Number(lap, "green_exposure") : null)).ToArray(),
                Array(source, "timeline").Select(item => new RaceInvestigationEvent(RequiredInt(item, "lap"), RequiredText(item, "kind"),
                    RequiredText(item, "label"), Text(item, "detail") ?? "", Number(item, "value"), Text(item, "unit"))).ToArray(),
                Track(Property(source, "track_evolution")), Strings(source, "limitations"), retained,
                Runs: current ? Array(source, "runs").Select(RunView).ToArray() : null,
                Sections: current ? Sections(Property(source, "sections")) : null,
                ProjectionVersion: current ? RequiredText(source, "projection_version") : null);
        }
        catch (JsonException exception)
        {
            return new(1, "unavailable", "Pace investigation could not be read", null, [], [], [], null, [], retained, exception.Message);
        }
    }

    private static RaceInvestigationComparison Comparison(JsonElement item, bool current) => new(
        RequiredText(item, "id"), RequiredText(item, "kind"), RequiredText(item, "title"), RequiredText(item, "summary"),
        Cohort(Property(item, "baseline")), Cohort(Property(item, "compared")), Number(item, "delta_s"), RequiredText(item, "eligibility"),
        Array(item, "metrics").Select(Metric).ToArray(),
        Array(item, "corners").Select(corner => new RaceInvestigationCorner(RequiredText(corner, "id"), RequiredText(corner, "label"),
            Number(corner, "start_pct"), Number(corner, "end_pct"), Number(corner, "delta_s"), Array(corner, "metrics").Select(Metric).ToArray(),
            Text(corner, "short_label"), Text(corner, "boundary_status"), Integer(corner, "baseline_count") ?? 0, Integer(corner, "compared_count") ?? 0)).ToArray(),
        Array(item, "hypotheses").Select(hypothesis => new RaceInvestigationHypothesis(RequiredText(hypothesis, "key"),
            RequiredText(hypothesis, "label"), RequiredText(hypothesis, "status"), Strings(hypothesis, "supporting"),
            Strings(hypothesis, "contradicting"), Strings(hypothesis, "unknown"), Text(hypothesis, "general_context"),
            Property(hypothesis, "references").ValueKind == JsonValueKind.Array ? Array(hypothesis, "references")
                .Select(reference => new RaceInvestigationReference(RequiredText(reference, "title"), RequiredText(reference, "url"))).ToArray() : [])).ToArray(),
        Array(item, "actions").Select(action => new RaceInvestigationAction(RequiredInt(action, "priority"), RequiredText(action, "title"),
            RequiredText(action, "instruction"), Strings(action, "evidence"), Text(action, "limitation"), Integers(action, "lap_numbers"),
            Number(action, "start_pct"), Number(action, "end_pct"), Strings(action, "signal_keys"),
            current ? Text(action, "section_id") : null, current ? TrialSpec(Property(action, "trial")) : null,
            current ? TrialReference(Property(action, "early_run_version")) : null)).ToArray(),
        current ? Inspection(Property(item, "inspection")) : null,
        // Absent before sections-trials-v2; present, it must be a boolean.
        Boolean(item, "pace_comparable") ?? true,
        Property(item, "pace_unusable_reasons").ValueKind is JsonValueKind.Null or JsonValueKind.Undefined ? [] : Strings(item, "pace_unusable_reasons"));

    private static RaceInvestigationCohort Cohort(JsonElement item) => new(
        RequiredText(item, "label"), Integer(item, "run_number"), Integers(item, "lap_numbers"), RequiredInt(item, "lap_count"),
        Integers(item, "eligible_lap_numbers"), Array(item, "excluded_laps").Select(exclusion => new RaceInvestigationExclusion(
            RequiredInt(exclusion, "lap"), Strings(exclusion, "reasons"))).ToArray(), Number(item, "median_lap_s"),
        Number(item, "green_age_start"), Number(item, "green_age_end"), Number(item, "fuel_start_gal"),
        Number(item, "track_temp_f"), Integer(item, "representative_lap"));

    private static RaceInvestigationMetric Metric(JsonElement item) => new(
        RequiredText(item, "key"), RequiredText(item, "label"), Text(item, "unit") ?? "", Number(item, "baseline"),
        Number(item, "compared"), Number(item, "delta"), Text(item, "evidence") ?? "", Text(item, "detail") ?? "");

    private static RaceTrackEvolution? Track(JsonElement item)
    {
        if (item.ValueKind is JsonValueKind.Undefined or JsonValueKind.Null) return null;
        return new(RequiredText(item, "status"), Text(item, "provenance"), Text(item, "starting_usage"), RequiredText(item, "summary"),
            Array(item, "points").Select(point => new RaceTrackEvolutionPoint(RequiredInt(point, "lap"),
                Number(point, "track_temp_f"), Number(point, "usage_value"))).ToArray(), Text(item, "usage_unit"),
            Text(item, "usage_source"), Strings(item, "limitations"));
    }

    /// <summary>Maps one run's observed trend and screened fit; also read by the test-day review.</summary>
    public static RaceRunView RunView(JsonElement item)
    {
        var trend = Property(item, "trend");
        var fit = Property(item, "fit");
        return new(RequiredInt(item, "run_number"), RequiredInt(item, "plotted_lap_count"), Integer(item, "first_lap"), Integer(item, "last_lap"),
            new RaceRunTrend(RequiredText(trend, "method"), RequiredBoolean(trend, "includes_reference_exclusions"),
                RequiredInt(trend, "covered_lap_count"),
                Array(trend, "segments").Select(segment => (IReadOnlyList<RaceTrendPoint>)Elements(segment, "segments").Select(point =>
                    new RaceTrendPoint(RequiredInt(point, "lap"), RequiredNumber(point, "x"), RequiredNumber(point, "y"),
                        Integers(point, "sample_laps"))).ToArray()).ToArray(),
                Array(trend, "gaps").Select(gap => new RaceTrendGap(RequiredInt(gap, "after_lap"), RequiredInt(gap, "before_lap"),
                    RequiredText(gap, "reason"))).ToArray()),
            fit.ValueKind is JsonValueKind.Null or JsonValueKind.Undefined ? null : new RaceRunFit(RequiredText(fit, "method"),
                RequiredInt(fit, "eligible_count"), Integers(fit, "laps"), RequiredNumber(fit, "slope_s_per_green_lap"),
                Pair(fit, "domain").Start ?? throw new JsonException("Invalid investigation range: domain."), Pair(fit, "domain").End ?? throw new JsonException("Invalid investigation range: domain."),
                Array(fit, "points").Select(point => new RaceFitPoint(RequiredNumber(point, "x"), RequiredNumber(point, "y"))).ToArray()),
            Text(item, "fit_unavailable_reason"));
    }

    private static RaceReviewSections Sections(JsonElement item) => new(Text(item, "configuration_key"), RequiredText(item, "source"),
        RequiredText(item, "version"), Text(item, "provenance") ?? "",
        Array(item, "sections").Select(section => new RaceReviewSection(RequiredText(section, "id"), RequiredText(section, "label"),
            RequiredNumber(section, "start_pct"), RequiredNumber(section, "end_pct"), RequiredText(section, "boundary_status"),
            Text(section, "provenance") ?? "")).ToArray(),
        Text(item, "unavailable_reason"));

    private static RaceInspection Inspection(JsonElement item)
    {
        var pool = Property(item, "pool_exposure");
        var sections = Property(item, "sections");
        if (sections.ValueKind != JsonValueKind.Object) throw new JsonException("Missing or invalid investigation object: sections.");
        return new(RequiredText(item, "mode"), Integers(item, "pool_laps"),
            pool.ValueKind == JsonValueKind.Array ? Pair(item, "pool_exposure").Start : null,
            pool.ValueKind == JsonValueKind.Array ? Pair(item, "pool_exposure").End : null,
            sections.EnumerateObject().ToDictionary(property => property.Name, property => PassPair(property.Value), StringComparer.Ordinal));
    }

    /// <summary>Maps an inspection pair; also used for before/after pairs in trial checks.</summary>
    public static RacePassPair PassPair(JsonElement item)
    {
        var slip = Property(item, "slip");
        return new(RequiredText(item, "section_id"), RequiredNumber(item, "start_pct"), RequiredNumber(item, "end_pct"),
            RequiredNumber(item, "context_start_pct"), Text(item, "context_label") ?? "", RequiredBoolean(item, "timing_complete"),
            Integer(item, "candidate_count") ?? 0, Pass(Property(item, "a")), Pass(Property(item, "b")),
            Number(item, "section_delta_s"), Number(item, "entry_gap_s"), Number(item, "exit_gap_s"),
            Number(item, "entry_speed_difference_mph"),
            new RaceSlipSummary(SlipValue(Property(slip, "a")), SlipValue(Property(slip, "b"))),
            Array(item, "rows").Select(row => new RacePairRow(RequiredNumber(row, "x"), Channels(Property(row, "a")),
                Channels(Property(row, "b")), Number(row, "gap"))).ToArray());
    }

    private static RacePass Pass(JsonElement item) => new(RequiredInt(item, "lap"), RequiredText(item, "role"), Number(item, "section_s"),
        Integer(item, "run_number"), Number(item, "green_exposure"), Boolean(item, "eligible") ?? true,
        Property(item, "exclusion_reasons").ValueKind == JsonValueKind.Array ? Strings(item, "exclusion_reasons") : [],
        Number(item, "fuel_start_gal"), Number(item, "track_temp_f"));

    private static RaceSlipValue SlipValue(JsonElement item) => item.ValueKind == JsonValueKind.Object
        ? new(Number(item, "coverage") ?? 0, Number(item, "median_abs"), Number(item, "peak_abs"))
        : new(0, null, null);

    private static RaceChannelValues Channels(JsonElement item) => item.ValueKind == JsonValueKind.Object
        ? new(Number(item, "speed"), Number(item, "throttle"), Number(item, "brake"), Number(item, "steering"), Number(item, "slip"))
        : throw new JsonException("Missing investigation trace row values.");

    public static RaceTrialSpec? TrialSpec(JsonElement item)
    {
        if (item.ValueKind is JsonValueKind.Null or JsonValueKind.Undefined) return null;
        var metric = Property(item, "metric");
        var section = Property(item, "section");
        var baseline = Property(item, "baseline");
        var reference = Property(item, "reference");
        var success = Property(item, "success");
        var conditions = Property(item, "conditions");
        var window = Property(item, "exposure_window");
        return new(RequiredText(metric, "key"), RequiredText(metric, "label"), RequiredNumber(metric, "start_pct"), RequiredNumber(metric, "end_pct"),
            RequiredText(section, "id"), RequiredText(section, "label"), RequiredNumber(section, "start_pct"), RequiredNumber(section, "end_pct"),
            RequiredNumber(baseline, "median_s"), Integers(baseline, "laps"), RequiredNumber(reference, "median_s"), Integers(reference, "laps"),
            window.ValueKind == JsonValueKind.Array ? Pair(item, "exposure_window").Start : null,
            window.ValueKind == JsonValueKind.Array ? Pair(item, "exposure_window").End : null,
            RequiredNumber(success, "threshold_s"), RequiredInt(success, "minimum_laps"), RequiredText(success, "rule"),
            conditions.ValueKind == JsonValueKind.Object ? Number(conditions, "fuel_start_gal") : null,
            conditions.ValueKind == JsonValueKind.Object ? Number(conditions, "track_temp_f") : null,
            Property(item, "annotation").ValueKind == JsonValueKind.Object ? Text(Property(item, "annotation"), "version") : null,
            Reach(Property(item, "reachability")));
    }

    /// <summary>Optional before sections-trials-v5; present, every field is required.</summary>
    private static RaceTrialReach? Reach(JsonElement item) => item.ValueKind switch
    {
        JsonValueKind.Null or JsonValueKind.Undefined => null,
        JsonValueKind.Object => new(PositiveInt(item, "run_laps_needed"), PositiveInt(item, "test_day_run_laps"),
            Boolean(item, "test_day_reachable") ?? throw new JsonException("Missing or invalid investigation flag: test_day_reachable.")),
        _ => throw new JsonException("Invalid investigation object: reachability.")
    };

    private static RaceTrialReference? TrialReference(JsonElement item) => item.ValueKind switch
    {
        JsonValueKind.Null or JsonValueKind.Undefined => null,
        JsonValueKind.Object => new(RequiredText(item, "comparison_id"), RequiredInt(item, "action_priority")),
        _ => throw new JsonException("Invalid investigation object: early_run_version.")
    };

    private static int PositiveInt(JsonElement item, string name) => RequiredInt(item, name) is var value and > 0
        ? value : throw new JsonException($"Invalid investigation count: {name}.");

    /// <summary>Maps a saved trial summary or full record from the trial tools.</summary>
    public static DrivingTrial Trial(JsonElement item, JsonElement? evaluations = null)
    {
        if (RequiredInt(item, "schema_version") != 1)
            throw new JsonException("This saved test uses an unsupported format.");
        var comparison = Property(item, "comparison");
        var list = evaluations is { ValueKind: JsonValueKind.Array } explicitList ? explicitList
            : Property(item, "evaluations").ValueKind == JsonValueKind.Array ? Property(item, "evaluations") : default;
        var mapped = list.ValueKind == JsonValueKind.Array ? list.EnumerateArray().Select(Evaluation).ToArray() : [];
        var latest = Property(item, "latest_evaluation");
        return new(RequiredText(item, "trial_id"), RequiredText(item, "created_at"), RequiredText(item, "title"),
            RequiredText(item, "instruction"), Strings(item, "evidence"), Recording(Property(item, "source")),
            RequiredText(comparison, "id"), Text(comparison, "title") ?? "", TrialSpec(item) ?? throw new JsonException("Saved test is missing its metric."),
            Integer(item, "evaluation_count") ?? mapped.Length,
            latest.ValueKind == JsonValueKind.Object ? Evaluation(latest) : mapped.LastOrDefault(), mapped);
    }

    public static DrivingTrialEvaluation Evaluation(JsonElement item)
    {
        var window = Property(item, "window");
        var pair = Property(item, "pair");
        return new(RequiredText(item, "evaluated_at"), Text(item, "analysis_id"), Text(item, "analysis_path"),
            Recording(Property(item, "recording")), RequiredText(item, "verdict"), Strings(item, "reasons"),
            Property(item, "confounds").ValueKind == JsonValueKind.Array ? Strings(item, "confounds") : [],
            window.ValueKind == JsonValueKind.Array ? Pair(item, "window").Start : null,
            window.ValueKind == JsonValueKind.Array ? Pair(item, "window").End : null,
            Property(item, "laps").ValueKind == JsonValueKind.Array ? Array(item, "laps").Select(lap => new DrivingTrialLap(
                RequiredInt(lap, "lap"), RequiredNumber(lap, "seconds"), Number(lap, "green_exposure"), Number(lap, "fuel_start_gal"))).ToArray() : [],
            Number(item, "median_s"), Integer(item, "count") ?? 0, Number(item, "improvement_s"),
            pair.ValueKind == JsonValueKind.Object ? PassPair(pair) : null);
    }

    private static DrivingTrialRecording Recording(JsonElement item) => item.ValueKind == JsonValueKind.Object
        ? new(Text(item, "analysis_id"), Text(item, "analysis_path"), Text(item, "recorded_at"), Text(item, "track"), Text(item, "layout"),
            Text(item, "configuration_key"), Scalar(item, "car_id"), Text(item, "car"), Text(item, "car_path"), Text(item, "setup_name"),
            Text(item, "setup_fingerprint"), Text(item, "session_type"), Scalar(item, "subsession_id"))
        : throw new JsonException("Missing saved-test recording identity.");

    private static JsonElement Property(JsonElement item, string name) =>
        item.ValueKind == JsonValueKind.Object && item.TryGetProperty(name, out var value) ? value : default;
    private static string? Text(JsonElement item, string name)
    {
        var value = Property(item, name);
        if (value.ValueKind is JsonValueKind.Null or JsonValueKind.Undefined) return null;
        return value.ValueKind == JsonValueKind.String ? value.GetString() : throw new JsonException($"Invalid investigation field: {name}.");
    }
    private static string? Scalar(JsonElement item, string name)
    {
        var value = Property(item, name);
        return value.ValueKind switch
        {
            JsonValueKind.String => value.GetString(),
            JsonValueKind.Number => value.GetRawText(),
            _ => null
        };
    }
    private static string RequiredText(JsonElement item, string name) => Text(item, name)
        ?? throw new JsonException($"Missing investigation field: {name}. Reanalyze the recording.");
    private static double? Number(JsonElement item, string name)
    {
        var value = Property(item, name);
        if (value.ValueKind is JsonValueKind.Null or JsonValueKind.Undefined) return null;
        return value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out var number) && double.IsFinite(number)
            ? number : throw new JsonException($"Invalid investigation number: {name}.");
    }
    private static double RequiredNumber(JsonElement item, string name) => Number(item, name)
        ?? throw new JsonException($"Missing investigation field: {name}. Reanalyze the recording.");
    private static (double? Start, double? End) Pair(JsonElement item, string name)
    {
        var values = Array(item, name);
        if (values.Count != 2) throw new JsonException($"Invalid investigation range: {name}.");
        double? Read(JsonElement value) => value.ValueKind == JsonValueKind.Null ? null
            : value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out var number) && double.IsFinite(number)
                ? number : throw new JsonException($"Invalid investigation range: {name}.");
        return (Read(values[0]), Read(values[1]));
    }
    private static int? Integer(JsonElement item, string name)
    {
        var value = Number(item, name);
        if (value is null) return null;
        return value >= int.MinValue && value <= int.MaxValue && value == Math.Truncate(value.Value)
            ? (int)value.Value : throw new JsonException($"Invalid investigation integer: {name}.");
    }
    private static int RequiredInt(JsonElement item, string name) => Integer(item, name)
        ?? throw new JsonException($"Missing investigation field: {name}. Reanalyze the recording.");
    private static bool? Boolean(JsonElement item, string name) => Property(item, name).ValueKind switch
    {
        JsonValueKind.True => true,
        JsonValueKind.False => false,
        JsonValueKind.Null or JsonValueKind.Undefined => null,
        _ => throw new JsonException($"Invalid investigation flag: {name}.")
    };
    private static bool RequiredBoolean(JsonElement item, string name) => Boolean(item, name)
        ?? throw new JsonException($"Missing or invalid investigation flag: {name}.");
    private static IReadOnlyList<JsonElement> Array(JsonElement item, string name)
    {
        var value = Property(item, name);
        return value.ValueKind == JsonValueKind.Array ? value.EnumerateArray().ToArray()
            : throw new JsonException($"Missing or invalid investigation list: {name}.");
    }
    private static IReadOnlyList<JsonElement> Elements(JsonElement value, string name) => value.ValueKind == JsonValueKind.Array
        ? value.EnumerateArray().ToArray() : throw new JsonException($"Missing or invalid investigation list: {name}.");
    private static IReadOnlyList<string> Strings(JsonElement item, string name) => Array(item, name).Select(value =>
        value.ValueKind == JsonValueKind.String ? value.GetString()! : throw new JsonException($"Invalid investigation text in {name}.")).ToArray();
    private static IReadOnlyList<int> Integers(JsonElement item, string name) => Array(item, name).Select(value =>
        value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var number) && number > 0
            ? number : throw new JsonException($"Invalid investigation lap in {name}.")).ToArray();
}
