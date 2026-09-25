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
        Assert.AreEqual("Last 5 laps", review.Windows.Late.Label, "Each run and each pair has its own late window.");
        Assert.IsNull(review.Runs[0].Comparison, "Run 1 has nothing earlier to compare with.");
        Assert.AreEqual("within_scatter", review.Runs[1].Comparison!.Verdict);
        Assert.AreEqual("faster", review.Runs[2].Comparison!.Verdict);
        Assert.AreEqual(-0.11, review.Runs[2].Comparison!.DeltaSeconds!.Value, 1e-9);
        Assert.AreEqual("changed", review.Runs[2].SetupChange.Status);
        Assert.AreEqual("Front brake bias", review.Runs[2].SetupChange.Changes.Single().Label);
        Assert.AreEqual("keep_and_step", review.NextChange.Kind);
        Assert.IsFalse(review.NextChange.RulesetStep!.StepVerified);
        StringAssert.StartsWith(review.NextChange.SuccessRule.Text, "Beat 24.068 s median on laps 12–16 by at least 0.033 s");
        Assert.AreEqual(3, review.Runs[2].Trend!.RunNumber, "The chart trend belongs to the day's run, not the recording's run.");
        Assert.AreEqual("Run 3 · lap 14", review.Runs[2].Laps.Single(lap => lap.Lap == 14).Label);
        Assert.AreEqual(new TestDayRecording(5, "fixture-stint-3", review.Runs[2].AnalysisPath, "2026-08-04T19:25:00+00:00", 1, 1), review.Runs[2].Recording,
            "Recording 1 of the day has no timed lap and recording 2 one clean lap; both are listed apart and never a run.");

        var lastStop = review.Runs[2].Tires;
        Assert.IsFalse(lastStop.StopReading.Measured);
        Assert.IsNull(lastStop.Corners["LF"].Stop, "A run that ended on track has no stall reading; nothing stands in for it.");
        Assert.AreEqual("unverified", lastStop.SheetReading.Timing);
        Assert.AreEqual(176.0, review.Runs[1].Tires.Corners["LF"].Sheet!.TemperatureF!.Outside);
        Assert.AreEqual("disagree", review.Runs[0].Tires.Corners["LF"].SheetVsStop.Status);
    }

    [TestMethod]
    public void MapperReadsTheCommonLengthWindowsNoiseHotPressuresAndGarageOnlyRecordings()
    {
        var review = Map(Fixture());

        var garage = review.UntimedRecordings.Single();
        Assert.AreEqual((1, "fixture-garage-only", "No timed lap was recorded."), (garage.Recording, garage.AnalysisId, garage.Reason));
        Assert.IsTrue(review.Runs[0].Noise!.Measured);
        Assert.AreEqual(0.021, review.Runs[0].Noise!.NoiseSeconds!.Value, 1e-9);
        var comparison = review.Runs[2].Comparison!;
        Assert.AreEqual(16, comparison.CommonRunLaps);
        Assert.AreEqual(("late", "Laps 12–16", "faster"), (comparison.LateWindow!.Key, comparison.LateWindow.Label, comparison.LateWindow.Verdict));
        Assert.AreEqual(("early", "faster"), (comparison.EarlyWindow!.Key, comparison.EarlyWindow.Verdict));
        Assert.IsTrue(comparison.FallOff!.Measured);
        Assert.AreEqual("these_runs", comparison.Noise!.Basis);
        var stop = review.Runs[0].Tires;
        Assert.IsTrue(stop.StopReading.HotPressure!.Measured);
        Assert.AreEqual(2, stop.StopReading.HotPressure.MatchedRun);
        var lf = stop.Corners["LF"].Stop!;
        Assert.AreEqual((20.16, 16.97), (lf.HotPressurePsi, lf.ColdPressurePsi));
        Assert.IsTrue(lf.PressureIsColdSetting, "The stall channel read the set cold pressure; it is never a hot reading.");

        // A v1 backend from before these fields: absent reads as missing, never as zero or a failure.
        var older = Fixture();
        older.Remove("untimed_recordings");
        foreach (var run in older["runs"]!.AsArray())
        {
            run!.AsObject().Remove("noise");
            if (run["comparison"] is JsonObject compared) { compared.Remove("windows"); compared.Remove("fall_off"); compared.Remove("noise"); compared.Remove("common_run_laps"); }
            run["tires"]!["stop_reading"]!.AsObject().Remove("hot_pressure");
            foreach (var corner in run["tires"]!["corners"]!.AsObject())
                if (corner.Value!["stop"] is JsonObject stopCorner) { stopCorner.Remove("hot_pressure_psi"); stopCorner.Remove("cold_pressure_psi"); }
        }
        var mapped = Map(older);
        Assert.IsEmpty(mapped.UntimedRecordings);
        Assert.IsNull(mapped.Runs[0].Noise);
        Assert.IsNull(mapped.Runs[2].Comparison!.LateWindow);
        Assert.IsNull(mapped.Runs[2].Comparison!.FallOff);
        Assert.IsNull(mapped.Runs[2].Comparison!.CommonRunLaps);
        Assert.IsNull(mapped.Runs[0].Tires.StopReading.HotPressure);
        Assert.IsNull(mapped.Runs[0].Tires.Corners["LF"].Stop!.HotPressurePsi);

        var wrong = Fixture();
        wrong["runs"]![2]!["comparison"]!["windows"]!["late"]!["run_laps"] = "12-16";
        StringAssert.Contains(Assert.ThrowsExactly<JsonException>(() => Map(wrong)).Message, "runs[2].comparison.windows.late.run_laps");
    }

    [TestMethod]
    public void MapperReadsShortRunsTheNoiseEstimatesAndTheSameSetupSpread()
    {
        // IRP 9/24: four stubs between resets were rows; the verdicts ignored pace that wandered for laps at a time.
        var review = Map(Fixture());

        var stub = review.ShortRuns.Single();
        Assert.AreEqual(new TestDayShortRun(2, "fixture-stub", stub.AnalysisPath, "2026-08-04T17:55:00+00:00", "Offline Testing", "fixture-stub.sto",
            1, 1, 1, 2, 2, 1, "1 clean lap after lap 1; 3 are needed to compare a run."), stub);
        var comparison = review.Runs[2].Comparison!;
        Assert.AreEqual(0.021, comparison.Noise!.DifferenceNoiseSeconds);
        Assert.AreEqual(0.0106, comparison.Noise.ResidualNoiseSeconds);
        Assert.AreEqual("these_runs", comparison.Noise.ResidualBasis);
        Assert.AreEqual(0.0, comparison.Noise.Autocorrelation);
        var late = comparison.LateWindow!;
        Assert.AreEqual(0.033, late.StatisticalThresholdSeconds);
        Assert.AreEqual(5.0, late.EffectiveLapCount);
        Assert.AreEqual(0.01, late.SameSetupSpread!.SpreadSeconds);
        CollectionAssert.AreEqual(new[] { 1, 2 }, late.SameSetupSpread.Runs.ToArray());
        Assert.AreEqual("Laps 6–16", comparison.FallOff!.Label, "Fall-off starts after laps 2–5, where the tires come in.");
        Assert.AreEqual(0.0057, comparison.FallOff.StatisticalThresholdSecondsPerLap);
        Assert.AreEqual(0.0, comparison.FallOff.SameSetupSpread!.SpreadSecondsPerLap);
        Assert.IsNull(review.Runs[1].Comparison!.LateWindow!.SameSetupSpread, "No other same-setup pair: nothing stands in for it.");

        // A v1 backend from before these fields: absent reads as empty or missing, never as zero or a failure.
        var older = Fixture();
        older.Remove("short_runs");
        foreach (var run in older["runs"]!.AsArray())
        {
            if (run!["comparison"] is not JsonObject compared) continue;
            foreach (var key in new[] { "difference_noise_s", "residual_noise_s", "residual_basis", "autocorrelation" }) compared["noise"]!.AsObject().Remove(key);
            foreach (var window in compared["windows"]!.AsObject())
                foreach (var key in new[] { "statistical_threshold_s", "effective_lap_count", "same_setup_spread" }) window.Value!.AsObject().Remove(key);
            compared["fall_off"]!.AsObject().Remove("same_setup_spread");
        }
        var mapped = Map(older);
        Assert.IsEmpty(mapped.ShortRuns);
        Assert.IsNull(mapped.Runs[2].Comparison!.Noise!.ResidualNoiseSeconds);
        Assert.IsNull(mapped.Runs[2].Comparison!.LateWindow!.EffectiveLapCount);
        Assert.IsNull(mapped.Runs[2].Comparison!.LateWindow!.SameSetupSpread);
        Assert.IsNull(mapped.Runs[2].Comparison!.FallOff!.SameSetupSpread);

        var wrong = Fixture();
        wrong["short_runs"]![0]!["clean_lap_count"] = "one";
        StringAssert.Contains(Assert.ThrowsExactly<JsonException>(() => Map(wrong)).Message, "short_runs[0].clean_lap_count");
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
