using System.Text.Json;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;
using iRacingCoach.UI;
using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Components.Web;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class RaceInvestigationMapperTests
{
    internal const string InvestigationJson = """
    {
      "schema_version":1,"status":"usable","summary":"Pace recovered after the stop, then fell again.",
      "default_comparison_id":"later-run","future_evidence":{"preserve":true},
      "comparisons":[{
        "id":"later-run","kind":"within_run","title":"Later in the run","summary":"The later laps were slower through the corners.",
        "baseline":{"label":"Earlier laps","run_number":2,"lap_numbers":[12,13,14],"lap_count":3,"eligible_lap_numbers":[12,13,14],"excluded_laps":[],"median_lap_s":15.5,"green_age_start":8,"green_age_end":11,"fuel_start_gal":8,"track_temp_f":78,"representative_lap":13},
        "compared":{"label":"Last recorded laps","run_number":2,"lap_numbers":[31,32,33],"lap_count":3,"eligible_lap_numbers":[],"excluded_laps":[{"lap":31,"reasons":["close_traffic"]},{"lap":32,"reasons":["close_traffic"]},{"lap":33,"reasons":["close_traffic"]}],"median_lap_s":16.9,"green_age_start":27,"green_age_end":30,"fuel_start_gal":3,"track_temp_f":78,"representative_lap":32},
        "delta_s":1.4,"eligibility":"observed",
        "metrics":[{"key":"track_temp_f","label":"Track temperature","unit":"°F","baseline":78,"compared":78,"delta":0,"evidence":"measured","detail":"Recorded within each lap."},{"key":"wear","label":"Final tire wear","unit":"%","baseline":null,"compared":null,"delta":null,"evidence":"unavailable","detail":"No final stop reading."}],
        "corners":[{"id":"turn-pair","label":"Turns 1–2","start_pct":0.1,"end_pct":0.4,"delta_s":0.4,"metrics":[]}],
        "hypotheses":[{"key":"track","label":"Track evolution","status":"unresolved","supporting":[],"contradicting":["Temperature stayed at 78°F."],"unknown":["No changing track-usage channel was recorded."],"general_context":"Track rubber can change while temperature stays similar.","references":[{"title":"Simulation reference","url":"https://example.org/track"},{"title":"Unsafe link","url":"javascript:alert(1)"}]}],
        "actions":[{"priority":1,"title":"Compare corner entry","instruction":"Try one small release change and compare at the same tire age.","evidence":["Lower corner speed appears in the selected laps."],"limitation":"Traffic remains a competing explanation.","lap_numbers":[13,32],"start_pct":0.1,"end_pct":0.4,"signal_keys":["speed","brake","steering"]}]
      }],
      "laps":[{"lap":12,"lap_time_s":15.5,"position":1,"flag_state":"green","eligible":true,"plot_pace":true,"exclusion_reasons":[],"run_number":2},{"lap":33,"lap_time_s":16.9,"position":3,"flag_state":"green","eligible":false,"plot_pace":true,"exclusion_reasons":["close_traffic"],"run_number":2}],
      "timeline":[{"lap":20,"kind":"incident","label":"Incident points +4","detail":"Points do not establish damage.","value":4,"unit":"points"}],
      "track_evolution":{"status":"starting_state_only","provenance":"Session metadata and recorded temperatures.","starting_usage":"moderately high usage","summary":"Starting rubber is recorded; its change during the race is not.","points":[{"lap":12,"track_temp_f":78,"usage_value":null},{"lap":33,"track_temp_f":78,"usage_value":null}],"usage_unit":"%","usage_source":null,"limitations":["The starting label is not a time series."]},
      "limitations":["Comparison timing is observed; causal claims need further evidence."]
    }
    """;

    [TestMethod]
    public void MapsBackendFindingsWithoutReplacingMissingMeasurementsWithZero()
    {
        using var document = JsonDocument.Parse(InvestigationJson);
        var investigation = RaceInvestigationMapper.Map(document.RootElement)!;
        Assert.IsNull(investigation.UnavailableReason);
        var comparison = investigation.Comparisons.Single();
        Assert.AreEqual(0d, comparison.Metrics[0].Delta);
        Assert.IsNull(comparison.Metrics[1].Baseline);
        Assert.IsNull(comparison.Metrics[1].Compared);
        Assert.IsNull(comparison.Metrics[1].Delta);
        Assert.AreEqual(1.4d, comparison.DeltaSeconds);
        Assert.AreEqual("observed", comparison.Eligibility);
        CollectionAssert.AreEqual(new[] { 31, 32, 33 }, comparison.Compared.LapNumbers.ToArray());
        Assert.IsEmpty(comparison.Compared.EligibleLapNumbers);
        Assert.AreEqual(33, investigation.Laps.Last().Lap);
        Assert.IsFalse(investigation.Laps.Last().Eligible);
        Assert.IsNull(investigation.TrackEvolution!.UsageSource);
        Assert.IsTrue(investigation.TrackEvolution.Points.All(point => point.UsageValue is null));
        Assert.AreEqual("moderately high usage", investigation.TrackEvolution.StartingUsage);
    }

    [TestMethod]
    public void PreservesUnknownEvidenceAfterInputDocumentIsDisposed()
    {
        RaceInvestigation investigation;
        using (var document = JsonDocument.Parse(InvestigationJson))
            investigation = RaceInvestigationMapper.Map(document.RootElement)!;
        Assert.IsTrue(investigation.Source.GetProperty("future_evidence").GetProperty("preserve").GetBoolean());
        CollectionAssert.AreEqual(new[] { 13, 32 }, investigation.Comparisons.Single().Actions.Single().LapNumbers.ToArray());
        Assert.AreEqual(.1d, investigation.Comparisons.Single().Actions.Single().StartPct);
        Assert.AreEqual(.4d, investigation.Comparisons.Single().Actions.Single().EndPct);
    }

    [TestMethod]
    public void UnsupportedVersionAndMissingRequiredEvidenceFailClearly()
    {
        foreach (var input in new[]
        {
            InvestigationJson.Replace("\"schema_version\":1", "\"schema_version\":99", StringComparison.Ordinal),
            InvestigationJson.Replace("\"eligible\":false,", "", StringComparison.Ordinal),
            InvestigationJson.Replace("\"delta_s\":1.4", "\"delta_s\":\"wrong type\"", StringComparison.Ordinal)
        })
        {
            using var document = JsonDocument.Parse(input);
            var investigation = RaceInvestigationMapper.Map(document.RootElement)!;
            Assert.AreEqual("unavailable", investigation.Status);
            Assert.IsFalse(string.IsNullOrWhiteSpace(investigation.UnavailableReason));
            Assert.IsEmpty(investigation.Comparisons);
        }
        Assert.IsNull(RaceInvestigationMapper.Map(default));
    }

    [TestMethod]
    public async Task RenderKeepsExcludedFinalLapsAndSeparatesStartingTrackStateFromEvolution()
    {
        using var document = JsonDocument.Parse(InvestigationJson);
        var investigation = RaceInvestigationMapper.Map(document.RootElement)!;
        using var services = new ServiceCollection().AddSingleton<ILoggerFactory>(Microsoft.Extensions.Logging.Abstractions.NullLoggerFactory.Instance).BuildServiceProvider();
        await using var renderer = new HtmlRenderer(services, services.GetRequiredService<ILoggerFactory>());
        var html = await renderer.Dispatcher.InvokeAsync(async () =>
        {
            var output = await renderer.RenderComponentAsync<RacePaceInvestigation>(ParameterView.FromDictionary(
                new Dictionary<string, object?> { [nameof(RacePaceInvestigation.Investigation)] = investigation }));
            return output.ToHtmlString();
        });
        StringAssert.Contains(html, "Last recorded laps");
        StringAssert.Contains(html, "Lap 33");
        StringAssert.Contains(html, "close traffic");
        StringAssert.Contains(html, "Starting track state");
        StringAssert.Contains(html, "moderately high usage");
        Assert.IsFalse(html.Contains("Recorded track usage", StringComparison.Ordinal));
        StringAssert.Contains(html, "Compare corner entry");
        StringAssert.Contains(html, "data-investigation-inspect=\"action\"");
        StringAssert.Contains(html, "https://example.org/track");
        Assert.IsFalse(html.Contains("javascript:", StringComparison.OrdinalIgnoreCase));
    }
}
