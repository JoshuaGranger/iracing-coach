using System.Text.Json;
using System.Text.Json.Nodes;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class RuntimeEnvelopeValidationTests
{
    private static readonly string[] RequiredTopLevel =
    [
        "ok", "analysis_id", "selector", "selection", "context", "analysis_cache", "knowledge_cache",
        "historical_runs_considered", "race_summary", "race_timeline", "damage_repair", "strategy_forecast",
        "data_quality", "source_files", "source_channel_coverage", "analysis_path", "report_path", "race_card_path",
        "race_card", "analysis_view", "timing", "artifacts"
    ];

    private static readonly string[] RequiredView =
    [
        "schema_version", "analysis_profile_version", "identity", "race_summary", "race_grades", "runs", "laps",
        "lap_traces", "track_profile", "track_geometry", "race_replay", "tire_learning", "garage61_representative_laps",
        "technical_insights", "corner_tire_age", "groove_evolution", "strategy", "damage_repair", "setup_telemetry",
        "conditions", "data_quality"
    ];

    [TestMethod]
    public void CurrentEnvelope_RequiresEveryDeclaredFieldAndAcceptsUnknownOptionalData()
    {
        var current = CurrentEnvelope();
        current["future_optional"] = new JsonObject { ["nested"] = 7 };
        current["analysis_view"]!["future_view_optional"] = new JsonArray("kept", 9);
        _ = Map(current);

        foreach (var field in RequiredTopLevel)
        {
            var mutant = CurrentEnvelope();
            mutant.Remove(field);
            var error = Assert.Throws<InvalidDataException>(() => Map(mutant), field);
            StringAssert.Contains(error.Message, field);
        }

        foreach (var field in RequiredView)
        {
            var mutant = CurrentEnvelope();
            mutant["analysis_view"]!.AsObject().Remove(field);
            var error = Assert.Throws<InvalidDataException>(() => Map(mutant), field);
            StringAssert.Contains(error.Message, field);
        }
    }

    [TestMethod]
    public void CurrentEnvelope_RefusesNullWrongTypeAndFutureVersions()
    {
        foreach (var mutate in new Action<JsonObject>[]
        {
            root => root["analysis_view"] = null,
            root => root["analysis_id"] = 7,
            root => root["timing"]!["analysis_cache_hit"] = "false",
            root => root["analysis_view"]!["runs"] = new JsonObject(),
            root => root["historical_runs_considered"] = -1,
            root => root["timing"]!["contract_version"] = 2,
            root => root["analysis_view"]!["schema_version"] = 2
        })
        {
            var mutant = CurrentEnvelope();
            mutate(mutant);
            Assert.Throws<InvalidDataException>(() => Map(mutant));
        }

        var nullableId = CurrentEnvelope();
        nullableId["analysis_id"] = null;
        nullableId["selector"] = string.Empty;
        _ = Map(nullableId);
    }

    [TestMethod]
    public void LegacyWindow_IsBoundedAndFutureViewStillFails()
    {
        var legacy = new JsonObject
        {
            ["analysis_id"] = "legacy",
            ["analysis_view"] = new JsonObject { ["identity"] = new JsonObject() }
        };
        _ = Map(legacy);

        legacy["analysis_view"]!["schema_version"] = 2;
        Assert.Throws<InvalidDataException>(() => Map(legacy));

        legacy.Remove("analysis_view");
        Assert.Throws<InvalidDataException>(() => Map(legacy));
    }

    private static object Map(JsonObject payload)
    {
        using var document = JsonDocument.Parse(payload.ToJsonString());
        return RuntimeMapper.Analysis(document.RootElement);
    }

    private static JsonObject CurrentEnvelope() => new()
    {
        ["ok"] = true,
        ["analysis_id"] = "analysis-contract",
        ["selector"] = "selector-contract",
        ["selection"] = new JsonObject(),
        ["context"] = new JsonObject(),
        ["analysis_cache"] = new JsonObject(),
        ["knowledge_cache"] = new JsonObject(),
        ["historical_runs_considered"] = 0,
        ["race_summary"] = new JsonObject(),
        ["race_timeline"] = new JsonObject(),
        ["damage_repair"] = new JsonObject(),
        ["strategy_forecast"] = new JsonObject(),
        ["data_quality"] = new JsonObject(),
        ["source_files"] = new JsonArray(),
        ["source_channel_coverage"] = new JsonObject(),
        ["analysis_path"] = string.Empty,
        ["report_path"] = string.Empty,
        ["race_card_path"] = string.Empty,
        ["race_card"] = new JsonObject(),
        ["analysis_view"] = new JsonObject
        {
            ["schema_version"] = 1,
            ["analysis_profile_version"] = null,
            ["identity"] = new JsonObject(),
            ["race_summary"] = new JsonObject(),
            ["race_grades"] = new JsonObject(),
            ["runs"] = new JsonArray(),
            ["laps"] = new JsonArray(),
            ["lap_traces"] = new JsonObject(),
            ["track_profile"] = new JsonObject(),
            ["track_geometry"] = new JsonObject(),
            ["race_replay"] = new JsonObject(),
            ["tire_learning"] = new JsonObject(),
            ["garage61_representative_laps"] = new JsonObject(),
            ["technical_insights"] = new JsonArray(),
            ["corner_tire_age"] = new JsonObject(),
            ["groove_evolution"] = new JsonObject(),
            ["strategy"] = new JsonObject(),
            ["damage_repair"] = new JsonObject(),
            ["setup_telemetry"] = new JsonObject(),
            ["conditions"] = new JsonObject(),
            ["data_quality"] = new JsonObject()
        },
        ["timing"] = new JsonObject { ["contract_version"] = 1, ["total_ms"] = 0, ["analysis_cache_hit"] = false },
        ["artifacts"] = new JsonObject()
    };
}
