using System.Text.Json;
using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

/// <summary>Projects the backend investigation; never derives findings or repairs missing evidence.</summary>
public static class RaceInvestigationMapper
{
    public static RaceInvestigation? Map(JsonElement source)
    {
        if (source.ValueKind is JsonValueKind.Undefined or JsonValueKind.Null) return null;
        var retained = source.Clone();
        try
        {
            if (RequiredInt(source, "schema_version") != 1)
                throw new JsonException("This pace investigation uses an unsupported version. Reanalyze the recording with the current app.");
            return new(1, RequiredText(source, "status"), RequiredText(source, "summary"), Text(source, "default_comparison_id"),
                Array(source, "comparisons").Select(Comparison).ToArray(),
                Array(source, "laps").Select(lap => new RaceInvestigationLap(RequiredInt(lap, "lap"), Number(lap, "lap_time_s"),
                    Integer(lap, "position"), RequiredText(lap, "flag_state"), RequiredBoolean(lap, "eligible"),
                    Strings(lap, "exclusion_reasons"), Integer(lap, "run_number"), RequiredBoolean(lap, "plot_pace"))).ToArray(),
                Array(source, "timeline").Select(item => new RaceInvestigationEvent(RequiredInt(item, "lap"), RequiredText(item, "kind"),
                    RequiredText(item, "label"), Text(item, "detail") ?? "", Number(item, "value"), Text(item, "unit"))).ToArray(),
                Track(Property(source, "track_evolution")), Strings(source, "limitations"), retained);
        }
        catch (JsonException exception)
        {
            return new(1, "unavailable", "Pace investigation could not be read", null, [], [], [], null, [], retained, exception.Message);
        }
    }

    private static RaceInvestigationComparison Comparison(JsonElement item) => new(
        RequiredText(item, "id"), RequiredText(item, "kind"), RequiredText(item, "title"), RequiredText(item, "summary"),
        Cohort(Property(item, "baseline")), Cohort(Property(item, "compared")), Number(item, "delta_s"), RequiredText(item, "eligibility"),
        Array(item, "metrics").Select(Metric).ToArray(),
        Array(item, "corners").Select(corner => new RaceInvestigationCorner(RequiredText(corner, "id"), RequiredText(corner, "label"),
            Number(corner, "start_pct"), Number(corner, "end_pct"), Number(corner, "delta_s"), Array(corner, "metrics").Select(Metric).ToArray())).ToArray(),
        Array(item, "hypotheses").Select(hypothesis => new RaceInvestigationHypothesis(RequiredText(hypothesis, "key"),
            RequiredText(hypothesis, "label"), RequiredText(hypothesis, "status"), Strings(hypothesis, "supporting"),
            Strings(hypothesis, "contradicting"), Strings(hypothesis, "unknown"), Text(hypothesis, "general_context"),
            Property(hypothesis, "references").ValueKind == JsonValueKind.Array ? Array(hypothesis, "references")
                .Select(reference => new RaceInvestigationReference(RequiredText(reference, "title"), RequiredText(reference, "url"))).ToArray() : [])).ToArray(),
        Array(item, "actions").Select(action => new RaceInvestigationAction(RequiredInt(action, "priority"), RequiredText(action, "title"),
            RequiredText(action, "instruction"), Strings(action, "evidence"), Text(action, "limitation"), Integers(action, "lap_numbers"),
            Number(action, "start_pct"), Number(action, "end_pct"), Strings(action, "signal_keys"))).ToArray());

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

    private static JsonElement Property(JsonElement item, string name) =>
        item.ValueKind == JsonValueKind.Object && item.TryGetProperty(name, out var value) ? value : default;
    private static string? Text(JsonElement item, string name)
    {
        var value = Property(item, name);
        if (value.ValueKind is JsonValueKind.Null or JsonValueKind.Undefined) return null;
        return value.ValueKind == JsonValueKind.String ? value.GetString() : throw new JsonException($"Invalid investigation field: {name}.");
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
    private static int? Integer(JsonElement item, string name)
    {
        var value = Number(item, name);
        if (value is null) return null;
        return value >= int.MinValue && value <= int.MaxValue && value == Math.Truncate(value.Value)
            ? (int)value.Value : throw new JsonException($"Invalid investigation integer: {name}.");
    }
    private static int RequiredInt(JsonElement item, string name) => Integer(item, name)
        ?? throw new JsonException($"Missing investigation field: {name}. Reanalyze the recording.");
    private static bool RequiredBoolean(JsonElement item, string name) => Property(item, name).ValueKind switch
    {
        JsonValueKind.True => true,
        JsonValueKind.False => false,
        _ => throw new JsonException($"Missing or invalid investigation flag: {name}.")
    };
    private static IReadOnlyList<JsonElement> Array(JsonElement item, string name)
    {
        var value = Property(item, name);
        return value.ValueKind == JsonValueKind.Array ? value.EnumerateArray().ToArray()
            : throw new JsonException($"Missing or invalid investigation list: {name}.");
    }
    private static IReadOnlyList<string> Strings(JsonElement item, string name) => Array(item, name).Select(value =>
        value.ValueKind == JsonValueKind.String ? value.GetString()! : throw new JsonException($"Invalid investigation text in {name}.")).ToArray();
    private static IReadOnlyList<int> Integers(JsonElement item, string name) => Array(item, name).Select(value =>
        value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var number) && number > 0
            ? number : throw new JsonException($"Invalid investigation lap in {name}.")).ToArray();
}
