using System.Text.Json;
using System.Text.Json.Nodes;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

/// <summary>
/// test-day-review-v1 in the app: the mapper reads the backend fixture as recorded,
/// fails with the field's path when a required field is missing, and never fills a gap.
/// </summary>
[TestClass]
public sealed class TestDayMapperTests
{
    internal static JsonObject Fixture() =>
        JsonNode.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "fixtures", "test-day-review.json")))!.AsObject();

    internal static TestDayReview Map(JsonNode node) => TestDayMapper.Map(JsonSerializer.SerializeToElement(node));

    [TestMethod]
    public void MapperReadsTheBackendFixtureAsRecorded()
    {
        var review = Map(Fixture());

        Assert.AreEqual(3, review.RunCount);
        Assert.AreEqual("test-day-0123456789abcdef", review.TestDayId);
        Assert.IsTrue(review.Windows.Late.Available);
        Assert.AreEqual("Laps 12–16", review.Windows.Late.Label);
        Assert.IsNull(review.Runs[0].Comparison, "Run 1 has nothing earlier to compare with.");
        Assert.AreEqual("within_scatter", review.Runs[1].Comparison!.Verdict);
        Assert.AreEqual("faster", review.Runs[2].Comparison!.Verdict);
        Assert.AreEqual(-0.11, review.Runs[2].Comparison!.DeltaSeconds!.Value, 1e-9);
        Assert.AreEqual("changed", review.Runs[2].SetupChange.Status);
        Assert.AreEqual("Front brake bias", review.Runs[2].SetupChange.Changes.Single().Label);
        Assert.AreEqual("keep_and_step", review.NextChange.Kind);
        Assert.IsFalse(review.NextChange.RulesetStep!.StepVerified);
        StringAssert.StartsWith(review.NextChange.SuccessRule.Text, "Beat 24.068 s median on laps 12–16");
        Assert.AreEqual(3, review.Runs[2].Trend!.RunNumber, "The chart trend belongs to the day's run, not the recording's run.");
        Assert.AreEqual("Run 3 · lap 14", review.Runs[2].Laps.Single(lap => lap.Lap == 14).Label);
        Assert.AreEqual(new TestDayRecording(3, "fixture-stint-3", review.Runs[2].AnalysisPath, "2026-08-04T19:25:00+00:00", 1, 1), review.Runs[2].Recording);

        var lastStop = review.Runs[2].Tires;
        Assert.IsFalse(lastStop.StopReading.Measured);
        Assert.IsNull(lastStop.Corners["LF"].Stop, "A run that ended on track has no stall reading; nothing stands in for it.");
        Assert.AreEqual("unverified", lastStop.SheetReading.Timing);
        Assert.AreEqual(176.0, review.Runs[1].Tires.Corners["LF"].Sheet!.TemperatureF!.Outside);
        Assert.AreEqual("disagree", review.Runs[0].Tires.Corners["LF"].SheetVsStop.Status);
    }

    [TestMethod]
    [DataRow("runs[1].late", "runs[1].late")]
    [DataRow("runs[0].tires.corners.RF", "runs[0].tires.corners.RF")]
    [DataRow("next_change.success_rule.text", "next_change.success_rule.text")]
    [DataRow("windows", "$.windows")]
    [DataRow("runs[2].comparison.verdict", "runs[2].comparison.verdict")]
    public void MapperFailsClearlyOnAMissingRequiredField(string path, string reported)
    {
        var fixture = Fixture();
        Remove(fixture, path);

        var error = Assert.ThrowsExactly<JsonException>(() => Map(fixture));
        StringAssert.Contains(error.Message, reported);
    }

    [TestMethod]
    public void MapperRejectsAnotherContractOrSchemaVersion()
    {
        var other = Fixture();
        other["contract"] = "test-day-review-v2";
        StringAssert.Contains(Assert.ThrowsExactly<JsonException>(() => Map(other)).Message, "test-day-review-v1");

        var newer = Fixture();
        newer["schema_version"] = 2;
        StringAssert.Contains(Assert.ThrowsExactly<JsonException>(() => Map(newer)).Message, "schema version 2");
    }

    [TestMethod]
    public void MapperPreservesUnknownOptionalFields()
    {
        var fixture = Fixture();
        fixture["future_summary"] = new JsonObject { ["keep"] = true };
        fixture["runs"]![0]!["future_run_note"] = "kept";
        fixture["runs"]![0]!["tires"]!["corners"]!["LF"]!["future_band"] = 1.5;

        var review = Map(fixture);

        Assert.IsTrue(review.Source.GetProperty("future_summary").GetProperty("keep").GetBoolean());
        Assert.AreEqual("kept", review.Source.GetProperty("runs")[0].GetProperty("future_run_note").GetString());
        Assert.AreEqual(1.5, review.Source.GetProperty("runs")[0].GetProperty("tires").GetProperty("corners").GetProperty("LF").GetProperty("future_band").GetDouble());
    }

    [TestMethod]
    public void MapperReadsTheFieldsAddedForDisplayAndToleratesTheirAbsence()
    {
        var fixture = Fixture();
        var comparison = fixture["runs"]![2]!["comparison"]!.AsObject();
        comparison["previous_laps"] = new JsonArray(13, 14, 15);
        comparison["current_laps"] = new JsonArray(12, 14, 16);
        fixture["runs"]![2]!["setup_change"]!["readouts"] = new JsonArray(new JsonObject
        {
            ["field"] = "chassis.front.cross_weight", ["label"] = "Front cross weight", ["previous"] = "52.1%", ["current"] = "52.4%",
            ["delta"] = 0.3, ["unit"] = "%"
        });

        var review = Map(fixture);

        CollectionAssert.AreEqual(new[] { 12, 13, 14, 15, 16 }, review.Runs[2].Comparison!.RunLaps.ToArray());
        CollectionAssert.AreEqual(new[] { 13, 14, 15 }, review.Runs[2].Comparison!.PreviousLaps.ToArray());
        Assert.AreEqual("Front cross weight", review.Runs[2].SetupChange.Readouts.Single().Label);
        Assert.AreEqual("carcass", review.Runs[0].Tires.StopReading.Temperature);
        Assert.IsFalse(review.Runs[0].Tires.SheetReading.Uniform);
        CollectionAssert.AreEqual(new[] { "temperature" }, review.Runs[0].Tires.Corners["LF"].SheetVsStop.DisagreesOn.ToArray());

        // A v1 backend from before these fields: absent reads as empty, never as a failure.
        var older = Fixture();
        foreach (var run in older["runs"]!.AsArray())
        {
            run!["setup_change"]!.AsObject().Remove("readouts");
            if (run["comparison"] is JsonObject compared) { compared.Remove("run_laps"); compared.Remove("previous_laps"); compared.Remove("current_laps"); }
            run["tires"]!["stop_reading"]!.AsObject().Remove("temperature");
            run["tires"]!["sheet_reading"]!.AsObject().Remove("uniform");
            foreach (var corner in run["tires"]!["corners"]!.AsObject()) corner.Value!["sheet_vs_stop"]!.AsObject().Remove("disagrees_on");
        }
        var mapped = Map(older);
        Assert.IsEmpty(mapped.Runs[2].Comparison!.RunLaps);
        Assert.IsEmpty(mapped.Runs[2].SetupChange.Readouts);
        Assert.IsNull(mapped.Runs[0].Tires.StopReading.Temperature);
        Assert.IsEmpty(mapped.Runs[0].Tires.Corners["LF"].SheetVsStop.DisagreesOn);

        var wrong = Fixture();
        wrong["runs"]![2]!["comparison"]!["run_laps"] = "12-16";
        StringAssert.Contains(Assert.ThrowsExactly<JsonException>(() => Map(wrong)).Message, "runs[2].comparison.run_laps");
    }

    private static void Remove(JsonObject root, string path)
    {
        JsonNode node = root;
        var parts = path.Split('.');
        foreach (var part in parts[..^1]) node = Step(node, part);
        var last = parts[^1];
        if (last.Contains('['))
        {
            var name = last[..last.IndexOf('[')];
            var index = int.Parse(last[(last.IndexOf('[') + 1)..last.IndexOf(']')], System.Globalization.CultureInfo.InvariantCulture);
            node[name]!.AsArray().RemoveAt(index);
        }
        else node.AsObject().Remove(last);
    }

    private static JsonNode Step(JsonNode node, string part)
    {
        if (!part.Contains('[')) return node[part]!;
        var name = part[..part.IndexOf('[')];
        var index = int.Parse(part[(part.IndexOf('[') + 1)..part.IndexOf(']')], System.Globalization.CultureInfo.InvariantCulture);
        return node[name]![index]!;
    }
}
