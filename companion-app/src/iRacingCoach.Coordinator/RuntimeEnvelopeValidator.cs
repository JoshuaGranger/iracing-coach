using System.Text.Json;

namespace iRacingCoach.Coordinator;

internal static class RuntimeEnvelopeValidator
{
    private static readonly (string Name, JsonValueKind Kind)[] AnalyzeResultFields =
    [
        ("selection", JsonValueKind.Object),
        ("context", JsonValueKind.Object),
        ("analysis_cache", JsonValueKind.Object),
        ("knowledge_cache", JsonValueKind.Object),
        ("race_summary", JsonValueKind.Object),
        ("race_timeline", JsonValueKind.Object),
        ("damage_repair", JsonValueKind.Object),
        ("strategy_forecast", JsonValueKind.Object),
        ("data_quality", JsonValueKind.Object),
        ("source_files", JsonValueKind.Array),
        ("source_channel_coverage", JsonValueKind.Object),
        ("race_card", JsonValueKind.Object),
        ("analysis_view", JsonValueKind.Object),
        ("timing", JsonValueKind.Object),
        ("artifacts", JsonValueKind.Object)
    ];

    private static readonly (string Name, JsonValueKind Kind)[] AnalysisViewFields =
    [
        ("identity", JsonValueKind.Object),
        ("race_summary", JsonValueKind.Object),
        ("race_grades", JsonValueKind.Object),
        ("runs", JsonValueKind.Array),
        ("laps", JsonValueKind.Array),
        ("lap_traces", JsonValueKind.Object),
        ("track_profile", JsonValueKind.Object),
        ("track_geometry", JsonValueKind.Object),
        ("race_replay", JsonValueKind.Object),
        ("tire_learning", JsonValueKind.Object),
        ("garage61_representative_laps", JsonValueKind.Object),
        ("technical_insights", JsonValueKind.Array),
        ("corner_tire_age", JsonValueKind.Object),
        ("groove_evolution", JsonValueKind.Object),
        ("strategy", JsonValueKind.Object),
        ("damage_repair", JsonValueKind.Object),
        ("setup_telemetry", JsonValueKind.Object),
        ("conditions", JsonValueKind.Object),
        ("data_quality", JsonValueKind.Object)
    ];

    public static JsonElement RequireAnalyzeResultV1(JsonElement response)
    {
        if (response.ValueKind != JsonValueKind.Object)
            throw Invalid("analyze result", "must be an object");
        // Responses cached before the named analyze-result envelope existed did
        // not carry either discriminator. Keep that one bounded read window,
        // but never classify a partly present current envelope as legacy.
        if (!response.TryGetProperty("ok", out _) && !response.TryGetProperty("timing", out _))
        {
            if (!response.TryGetProperty("analysis_view", out var legacyView) || legacyView.ValueKind != JsonValueKind.Object)
                throw Invalid("legacy analyze result", "did not include an analysis view object");
            if (legacyView.TryGetProperty("schema_version", out var legacyVersion))
            {
                if (legacyVersion.ValueKind != JsonValueKind.Number || !legacyVersion.TryGetInt32(out var version))
                    throw Invalid("legacy analysis view", "field 'schema_version' must be an integer");
                if (version > 1) throw Invalid("legacy analysis view", $"version {version} is not supported");
            }
            return legacyView;
        }
        RequireBoolean(response, "ok", expected: true, "analyze result");
        RequireStringOrNull(response, "analysis_id", "analyze result");
        RequireString(response, "selector", allowEmpty: true, "analyze result");
        RequireInteger(response, "historical_runs_considered", minimum: 0, "analyze result");
        foreach (var (name, kind) in AnalyzeResultFields) RequireKind(response, name, kind, "analyze result");
        foreach (var name in new[] { "analysis_path", "report_path", "race_card_path" }) RequireString(response, name, allowEmpty: true, "analyze result");
        foreach (var item in response.GetProperty("source_files").EnumerateArray())
            if (item.ValueKind != JsonValueKind.String) throw Invalid("analyze result", "field 'source_files' must contain only text");

        var timing = response.GetProperty("timing");
        RequireInteger(timing, "contract_version", minimum: 1, "analyze result timing", expected: 1);
        RequireFiniteNumber(timing, "total_ms", minimum: 0, "analyze result timing");
        RequireBoolean(timing, "analysis_cache_hit", expected: null, "analyze result timing");
        return RequireAnalysisViewV1(response.GetProperty("analysis_view"));
    }

    private static JsonElement RequireAnalysisViewV1(JsonElement view)
    {
        RequireInteger(view, "schema_version", minimum: 1, "analysis view", expected: 1);
        RequirePresent(view, "analysis_profile_version", "analysis view");
        var profile = view.GetProperty("analysis_profile_version");
        if (profile.ValueKind is not (JsonValueKind.String or JsonValueKind.Null))
            throw Invalid("analysis view", "field 'analysis_profile_version' must be text or null");
        foreach (var (name, kind) in AnalysisViewFields) RequireKind(view, name, kind, "analysis view");
        return view;
    }

    private static void RequirePresent(JsonElement element, string property, string envelope)
    {
        if (!element.TryGetProperty(property, out _)) throw Invalid(envelope, $"is missing required field '{property}'");
    }

    private static void RequireKind(JsonElement element, string property, JsonValueKind kind, string envelope)
    {
        if (!element.TryGetProperty(property, out var value)) throw Invalid(envelope, $"is missing required field '{property}'");
        if (value.ValueKind != kind) throw Invalid(envelope, $"field '{property}' must be {kind.ToString().ToLowerInvariant()}");
    }

    private static void RequireString(JsonElement element, string property, bool allowEmpty, string envelope)
    {
        if (!element.TryGetProperty(property, out var value)) throw Invalid(envelope, $"is missing required field '{property}'");
        if (value.ValueKind != JsonValueKind.String || !allowEmpty && string.IsNullOrWhiteSpace(value.GetString()))
            throw Invalid(envelope, $"field '{property}' must be text{(allowEmpty ? string.Empty : " and cannot be empty")}");
    }

    private static void RequireStringOrNull(JsonElement element, string property, string envelope)
    {
        if (!element.TryGetProperty(property, out var value)) throw Invalid(envelope, $"is missing required field '{property}'");
        if (value.ValueKind is not (JsonValueKind.String or JsonValueKind.Null))
            throw Invalid(envelope, $"field '{property}' must be text or null");
    }

    private static void RequireBoolean(JsonElement element, string property, bool? expected, string envelope)
    {
        if (!element.TryGetProperty(property, out var value)) throw Invalid(envelope, $"is missing required field '{property}'");
        if (value.ValueKind is not (JsonValueKind.True or JsonValueKind.False)) throw Invalid(envelope, $"field '{property}' must be true or false");
        if (expected.HasValue && value.GetBoolean() != expected.Value) throw Invalid(envelope, $"field '{property}' must be {expected.Value.ToString().ToLowerInvariant()}");
    }

    private static void RequireInteger(JsonElement element, string property, int minimum, string envelope, int? expected = null)
    {
        if (!element.TryGetProperty(property, out var value)) throw Invalid(envelope, $"is missing required field '{property}'");
        if (value.ValueKind != JsonValueKind.Number || !value.TryGetInt32(out var number) || number < minimum)
            throw Invalid(envelope, $"field '{property}' must be an integer at least {minimum}");
        if (expected.HasValue && number != expected.Value) throw Invalid(envelope, $"version {number} is not supported");
    }

    private static void RequireFiniteNumber(JsonElement element, string property, double minimum, string envelope)
    {
        if (!element.TryGetProperty(property, out var value)) throw Invalid(envelope, $"is missing required field '{property}'");
        if (value.ValueKind != JsonValueKind.Number || !value.TryGetDouble(out var number) || !double.IsFinite(number) || number < minimum)
            throw Invalid(envelope, $"field '{property}' must be a finite number at least {minimum}");
    }

    private static InvalidDataException Invalid(string envelope, string detail) =>
        new($"The {envelope} {detail}.");
}
