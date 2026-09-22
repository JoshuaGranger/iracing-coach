using System.Collections;
using System.Reflection;
using System.Text.Json;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;
using iRacingCoach.UI;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class PitFuelPresentationTests
{
    [TestMethod]
    public void BristolStop_UsesPostServiceRangeAgainstPostServiceDistance()
    {
        using var document = JsonDocument.Parse(Response("\"post_stop_all_green_range_laps\":68.19,"));

        var workspace = RuntimeMapper.Analysis(document.RootElement);
        var stop = workspace.Runs.Single().PitStop!;

        // The pre-service range was 55.42. Using it with the next run's 56 laps
        // falsely reports a shortfall even though the service added fuel.
        Assert.AreEqual(68.19, stop.EstimatedFuelLapsRemaining);
        Assert.AreEqual(56.0, stop.RaceLapsRemainingAfterStop);
        Assert.AreEqual(12.19, stop.EstimatedFuelLapsRemaining!.Value - stop.RaceLapsRemainingAfterStop!.Value, .00001);
        Assert.AreEqual("+12.2 laps", workspace.TechnicalInsights!.Single().Metrics.Single().Value);
    }

    [TestMethod]
    [DataRow("")]
    [DataRow("\"post_stop_all_green_range_laps\":null,")]
    public void MissingPostServiceRange_DoesNotSubstitutePreServiceFuel(string rangeProperty)
    {
        using var document = JsonDocument.Parse(Response(rangeProperty));

        var stop = RuntimeMapper.Analysis(document.RootElement).Runs.Single().PitStop!;

        Assert.IsNull(stop.EstimatedFuelLapsRemaining);
        Assert.AreEqual(56.0, stop.RaceLapsRemainingAfterStop);
    }

    [TestMethod]
    public void ZeroPostServiceRange_RemainsZero()
    {
        using var document = JsonDocument.Parse(Response("\"post_stop_all_green_range_laps\":0,"));

        Assert.AreEqual(0d, RuntimeMapper.Analysis(document.RootElement).Runs.Single().PitStop!.EstimatedFuelLapsRemaining);
    }

    [TestMethod]
    public void RacecraftOverview_ShowsOutcomesAndPaceWhileDrilldownRetainsEveryMeasurement()
    {
        using var document = JsonDocument.Parse(Response(""));
        var measurements = new[]
        {
            Metric("Throttle commitment", "driver execution"), Metric("Brake / steer overlap", "driver execution"),
            Metric("Net positions", "result"), Metric("Early", "phases"), Metric("Middle", "phases"), Metric("Late", "phases"),
            Metric("Fastest clean", "pace"), Metric("Median clean", "pace"), Metric("Clean-lap variation", "consistency"),
            Metric("Steering corrections", "driver execution"), Metric("Speed envelope", "driver execution"),
            Metric("Long-run change", "pace"), Metric("Incident points", "execution")
        };
        var workspace = RuntimeMapper.Analysis(document.RootElement) with
        {
            TechnicalInsights = [new("racecraft", "Racecraft", "available", "pace-only", "Review the later run.", measurements, [], [])]
        };
        var component = new RaceTechnicalData();
        typeof(RaceTechnicalData).GetProperty(nameof(RaceTechnicalData.Workspace))!.SetValue(component, workspace);
        var overview = (IEnumerable)typeof(RaceTechnicalData).GetProperty("RacecraftCardMetrics", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(component)!;
        var labels = overview.Cast<object>().Select(item => (string)item.GetType().GetProperty("Label")!.GetValue(item)!).ToArray();

        CollectionAssert.AreEqual(new[] { "Net positions", "Long-run change", "Fastest clean", "Median clean", "Clean-lap variation", "Incident points" }, labels);
        typeof(RaceTechnicalData).GetProperty("ActiveInvestigation", BindingFlags.Instance | BindingFlags.NonPublic)!.SetValue(component, "racecraft");
        var drilldown = (IReadOnlyList<AnalysisTechnicalMetric>)typeof(RaceTechnicalData).GetProperty("ActiveMetrics", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(component)!;
        CollectionAssert.AreEqual(measurements, drilldown.ToArray());
    }

    private static AnalysisTechnicalMetric Metric(string label, string group) =>
        new(label, "1.0", EvidenceKind.Derived, "Recorded measurement.", "Compare the relevant laps.", "neutral", group);

    private static string Response(string rangeProperty) => $$$"""
    {
      "analysis_view":{
        "schema_version":1,"analysis_profile_version":"post-race-foundations-v19",
        "identity":{"track_name":"Bristol Motor Speedway","car_name":"Toyota Supra"},
        "race_summary":{"scheduled_laps":160,"recorded_laps":159},
        "runs":[{"run_number":1,"lap_numbers":[101,102],"green_laps":54,"ended_with_pit_stop":true,
          "pit_service":{"start_time":100,"end_time":115,"fuel_added_l":6.4},"fuel":{"end_l":27.79}}],
        "lap_traces":{"traces":[]},
        "strategy":{"forecast":{"scheduled_laps":160},"pit_assessments":[{"run_number":1,"fuel_laps_remaining_at_end":55.42,
          {{{rangeProperty}}}"post_stop_fuel_l":34.196,"scheduled_race_laps_remaining":58,
          "scheduled_race_laps_remaining_after_stop":56,"post_stop_all_green_surplus_laps":12.19}]},
        "technical_insights":[{"key":"fuel","label":"Fuel","status":"available","rating":"safe",
          "metrics":[{"label":"Tightest post-stop margin","value":"+12.2 laps","evidence_type":"derived"}]}]
      }
    }
    """;
}
