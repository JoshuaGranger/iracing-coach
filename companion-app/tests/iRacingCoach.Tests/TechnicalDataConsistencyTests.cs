using System.Collections;
using System.Reflection;
using System.Text.Json;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;
using iRacingCoach.UI;

namespace iRacingCoach.Tests;

/// <summary>
/// Kansas 9/21 Details: the Racecraft &amp; pace card showed a GAIN badge over "No clean run
/// comparison", and the tire card mixed "9.8 %" wear with "90.2 %" remaining.
/// </summary>
[TestClass]
public sealed class TechnicalDataConsistencyTests
{
    [TestMethod]
    public void RacecraftBadgeStatesThePositionChangeItRestsOnInsteadOfABareGain()
    {
        var component = Component(Workspace(RacecraftInsight(withNetPositions: true)));

        Assert.AreEqual("No clean run comparison", Read<string>(component, "PaceSummary"));
        Assert.AreEqual("+4 positions", Read<string>(component, "PaceState"));
        Assert.AreEqual("positive", Read<string>(component, "PaceStateClass"));
    }

    [TestMethod]
    public void RacecraftBadgeWithoutAMeasuredPositionChangeIsNeverAGainOrLoss()
    {
        foreach (var rating in new[] { "gain", "loss" })
        {
            var component = Component(Workspace(RacecraftInsight(withNetPositions: false, rating)));
            var state = Read<string>(component, "PaceState");
            var tone = Read<string>(component, "PaceStateClass");

            Assert.AreEqual("No clean run comparison", Read<string>(component, "PaceSummary"));
            Assert.AreEqual("Unavailable", state, rating);
            Assert.AreEqual("unavailable", tone, rating);
        }
    }

    [TestMethod]
    public void TireCardAndBandTilesReadAsTreadRemaining()
    {
        var component = Component(Workspace());
        var metrics = ((IEnumerable)Read<object>(component, "TireCardMetrics")).Cast<object>()
            .Select(item => (Label: (string)item.GetType().GetProperty("Label")!.GetValue(item)!, Value: (string)item.GetType().GetProperty("Value")!.GetValue(item)!))
            .ToArray();

        // The lowest Kansas band was RF middle at 90.21% remaining, never "9.8%".
        CollectionAssert.Contains(metrics, ("Lowest remaining", "90.2%"));
        Assert.IsFalse(metrics.Any(metric => metric.Label.Contains("wear", StringComparison.OrdinalIgnoreCase)));

        var stop = ((AnalysisWorkspace)typeof(RaceTechnicalData).GetProperty(nameof(RaceTechnicalData.Workspace))!.GetValue(component)!).Runs[0].PitStop!;
        var rf = stop.TireConditions!["RF"];
        var title = (string)typeof(RaceTechnicalData).GetMethod("BandTitle", BindingFlags.Static | BindingFlags.NonPublic)!
            .Invoke(null, [rf, "M", rf.WearPercent.Middle])!;
        StringAssert.StartsWith(title, "Middle: 90.2% tread remaining;");

        var page = File.ReadAllText(Path.Combine(TestRepositoryPaths.CompanionAppRoot, "src", "iRacingCoach.UI", "RaceTechnicalData.razor"));
        StringAssert.Contains(page, "<b>@Percent(Remaining(band.Value))</b>", "Band tiles show tread remaining, like every other tire reading.");
        StringAssert.Contains(page, "Each band: tread remaining");
    }

    private static RaceTechnicalData Component(AnalysisWorkspace workspace)
    {
        var component = new RaceTechnicalData();
        typeof(RaceTechnicalData).GetProperty(nameof(RaceTechnicalData.Workspace))!.SetValue(component, workspace);
        return component;
    }

    private static T Read<T>(RaceTechnicalData component, string property) =>
        (T)typeof(RaceTechnicalData).GetProperty(property, BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(component)!;

    private static AnalysisWorkspace Workspace(AnalysisTechnicalInsight? racecraft = null)
    {
        using var document = JsonDocument.Parse(KansasResponse);
        var workspace = RuntimeMapper.Analysis(document.RootElement);
        return racecraft is null ? workspace : workspace with { TechnicalInsights = [racecraft] };
    }

    private static AnalysisTechnicalInsight RacecraftInsight(bool withNetPositions, string rating = "gain")
    {
        var metrics = new List<AnalysisTechnicalMetric>
        {
            new("Fastest race pace", "31.274 s", EvidenceKind.Measured, "", "", "neutral", "pace"),
            new("Median race pace", "31.852 s", EvidenceKind.Measured, "", "", "neutral", "pace"),
            new("Incident points", "16", EvidenceKind.Measured, "", "", "attention", "execution")
        };
        if (withNetPositions) metrics.Insert(0, new("Net positions", "+4 positions", EvidenceKind.Measured, "", "", "positive", "result"));
        return new("racecraft", "Racecraft & pace", "available", rating,
            "Restarts running lost 3 position(s); compare those laps with the strongest phase.", metrics, [], []);
    }

    // Kansas 9/21 runs as recorded: a confirmed four-tire stop after run 1 (lap 17)
    // measured RF middle at 90.21% remaining; neither run had a clean pace comparison.
    private const string KansasResponse = """
    {
      "analysis_view":{
        "schema_version":1,"analysis_profile_version":"post-race-foundations-v22",
        "identity":{"track_name":"Kansas Speedway","car_name":"Toyota Tundra TRD Pro","event_type":"Race"},
        "race_summary":{"scheduled_laps":42,"recorded_laps":42},
        "runs":[
          {"run_number":1,"lap_numbers":[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17],"green_laps":9,"caution_laps":8,
           "ended_with_pit_stop":true,"tire_measurement_status":"measured_at_stop",
           "pace":{"early_average_lap_s":null,"late_average_lap_s":null,"early_to_late_delta_s":null},
           "pit_service":{"start_time":1066.917,"end_time":1080.917,"fuel_added_l":0.0,"tires_changed_observed":["LF","LR","RF","RR"]},
           "tire_observation":{"lowest_remaining_percent":91.93,"lowest_remaining_tire":"RF","tires":{
             "LF":{"average_remaining_percent":95.63,"remaining_percent":{"L":94.87,"M":95.26,"R":96.76}},
             "LR":{"average_remaining_percent":95.88,"remaining_percent":{"L":95.64,"M":95.7,"R":96.3}},
             "RF":{"average_remaining_percent":91.93,"remaining_percent":{"L":90.24,"M":90.21,"R":95.33}},
             "RR":{"average_remaining_percent":93.49,"remaining_percent":{"L":93.22,"M":93.0,"R":94.24}}}}},
          {"run_number":2,"lap_numbers":[19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42],"green_laps":9,
           "ended_with_pit_stop":false,"tire_measurement_status":"unmeasured_final_run",
           "pace":{"early_average_lap_s":null,"late_average_lap_s":null,"early_to_late_delta_s":null}}],
        "lap_traces":{"traces":[]},
        "strategy":{"forecast":{"scheduled_laps":42}}
      }
    }
    """;
}
