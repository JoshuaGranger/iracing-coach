using System.Text.Json;
using System.Text.Json.Nodes;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;
using iRacingCoach.UI;

namespace iRacingCoach.Tests;

/// <summary>
/// A comparison the backend marks pace_comparable=false (a caution between or inside the
/// windows, or no screened lap) has no pace result: the app shows why in neutral styling,
/// offers no explanation statuses, and never sends its numbers to the coach.
/// </summary>
[TestClass]
public sealed class UnusablePaceComparisonTests
{
    private const string Reason = "Not a usable pace comparison: caution laps 5–6 separate laps 1–4 from laps 9–12.";

    /// <summary>The current fixture with run-1-finish withheld exactly as the backend emits it.</summary>
    internal static string UnusableFinishJson()
    {
        var node = JsonNode.Parse(File.ReadAllText(Path.Combine(TestRepositoryPaths.RepositoryRoot, "test-data", "race-investigation.json")))!;
        var finish = node["comparisons"]!.AsArray().Single(item => (string?)item!["id"] == "run-1-finish")!;
        finish["pace_comparable"] = false;
        finish["pace_unusable_reasons"] = new JsonArray("separated_by_caution");
        finish["delta_s"] = null;
        finish["summary"] = Reason;
        finish["hypotheses"] = new JsonArray();
        node["default_comparison_id"] = "run-1-finish";
        node["summary"] = Reason;
        return node.ToJsonString();
    }

    private static RaceInvestigation Unusable()
    {
        using var document = JsonDocument.Parse(UnusableFinishJson());
        return RaceInvestigationMapper.Map(document.RootElement)!;
    }

    [TestMethod]
    public void MapperCarriesPaceComparabilityAndReasons()
    {
        var investigation = Unusable();
        Assert.IsNull(investigation.UnavailableReason, investigation.UnavailableReason);
        var finish = investigation.Comparisons.Single(item => item.Id == "run-1-finish");
        Assert.IsFalse(finish.PaceComparable);
        CollectionAssert.AreEqual(new[] { "separated_by_caution" }, finish.PaceUnusableReasons!.ToArray());
        Assert.IsNull(finish.DeltaSeconds);
        var clean = investigation.Comparisons.Single(item => item.Id == "run-1-clean");
        Assert.IsTrue(clean.PaceComparable);
        Assert.IsEmpty(clean.PaceUnusableReasons!);

        // Before sections-trials-v2 there was no flag: those comparisons stay comparable.
        using var legacy = JsonDocument.Parse(RaceInvestigationMapperTests.InvestigationJson);
        Assert.IsTrue(RaceInvestigationMapper.Map(legacy.RootElement)!.Comparisons.Single().PaceComparable);

        // A present flag of the wrong type fails clearly instead of reading as comparable.
        using var wrong = JsonDocument.Parse(UnusableFinishJson().Replace("\"pace_comparable\":false", "\"pace_comparable\":\"no\"", StringComparison.Ordinal));
        Assert.AreEqual("unavailable", RaceInvestigationMapper.Map(wrong.RootElement)!.Status);
    }

    [TestMethod]
    public void UnusableComparisonsStayOutOfTheCoachPacket()
    {
        using var baseDocument = JsonDocument.Parse("""
            {"analysis_id":"unusable-packet-test","analysis_view":{"schema_version":1,"identity":{"track_name":"Track","car_name":"Car","event_type":"Race"},"race_summary":{},"runs":[],"laps":[],"lap_traces":{"traces":[]},"track_profile":{"detected_corner_segments":[]},"strategy":{},"damage_repair":{},"setup_telemetry":{},"data_quality":{}}}
            """);
        using var investigationDocument = JsonDocument.Parse(UnusableFinishJson());
        var workspace = RuntimeMapper.Analysis(baseDocument.RootElement) with { Investigation = RaceInvestigationMapper.Map(investigationDocument.RootElement) };

        var packet = RaceCoachPacketBuilder.Build(workspace);
        var names = packet.Sections.SelectMany(section => section.Series).Select(series => series.Name).ToArray();
        Assert.IsFalse(names.Any(name => name.StartsWith("run-1-finish/", StringComparison.Ordinal)),
            "Corner-area deltas and cohort medians of a withheld comparison must not reach the coach: " + string.Join(", ", names.Where(name => name.StartsWith("run-1-finish/", StringComparison.Ordinal))));
        Assert.IsTrue(names.Any(name => name.StartsWith("run-1-clean/", StringComparison.Ordinal)), "Usable comparisons still reach the coach.");

        var context = JsonSerializer.Serialize(RaceCoachPacketBuilder.InvestigationContext(workspace));
        using var parsed = JsonDocument.Parse(context);
        var ids = parsed.RootElement.GetProperty("comparisons").EnumerateArray().Select(item => item.GetProperty("Id").GetString()).ToArray();
        CollectionAssert.AreEqual(new[] { "run-1-clean" }, ids);
        Assert.AreEqual(1, parsed.RootElement.GetProperty("coverage").GetProperty("withheld_comparison_count").GetInt32());
    }

    [TestMethod]
    public async Task CardStatesWhyInNeutralStylingWithoutExplanations()
    {
        var html = await RaceInvestigationPerformanceTests.Render<RaceInvestigationView>(new()
        {
            [nameof(RaceInvestigationView.Investigation)] = Unusable(),
            [nameof(RaceInvestigationView.ComparisonId)] = "run-1-finish"
        });
        var card = html[html.IndexOf("data-selected-comparison=\"run-1-finish\"", StringComparison.Ordinal)..];
        StringAssert.Contains(card, "data-pace-unusable");
        StringAssert.Contains(card, "caution laps 5–6 separate laps 1–4 from laps 9–12");
        StringAssert.Contains(card, "<strong class=\"neutral\">No pace result</strong>");
        Assert.IsFalse(card.Contains("class=\"gain\"", StringComparison.Ordinal), "A withheld result is never shown as a gain.");
        Assert.IsFalse(card.Contains("Explanations", StringComparison.Ordinal), "No explanation statuses for a comparison without a pace result.");
        Assert.IsFalse(html.Contains("— s/lap", StringComparison.Ordinal), "No chart or card reads a missing delta as a pace.");
        var button = html[html.IndexOf("data-comparison=\"run-1-finish\"", StringComparison.Ordinal)..];
        button = button[..button.IndexOf("</button>", StringComparison.Ordinal)];
        StringAssert.Contains(button, "class=\"neutral\"");
        StringAssert.Contains(button, "no pace result");
    }

    [TestMethod]
    public async Task RunsViewReadsAWithheldResultNeutrally()
    {
        var html = await RaceInvestigationPerformanceTests.Render<RaceInvestigationView>(new()
        {
            [nameof(RaceInvestigationView.Investigation)] = Unusable(),
            [nameof(RaceInvestigationView.View)] = "runs",
            [nameof(RaceInvestigationView.ComparisonId)] = "run-1-finish"
        });
        StringAssert.Contains(html, "<b class=\"neutral\">No pace result</b>");
        Assert.IsFalse(html.Contains("— s/lap", StringComparison.Ordinal));
    }
}
