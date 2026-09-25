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

    internal static RaceInvestigation CurrentFixture()
    {
        using var document = JsonDocument.Parse(File.ReadAllText(Path.Combine(TestRepositoryPaths.RepositoryRoot, "test-data", "race-investigation.json")));
        return RaceInvestigationMapper.Map(document.RootElement)!;
    }

    [TestMethod]
    public void CurrentProjectionMapsRunsSectionsPassPairsAndSaveableTests()
    {
        var investigation = CurrentFixture();
        Assert.IsNull(investigation.UnavailableReason, investigation.UnavailableReason);
        Assert.AreEqual(2, investigation.SchemaVersion);
        Assert.AreEqual(RaceInvestigationMapper.CurrentProjectionVersion, investigation.ProjectionVersion);
        Assert.IsTrue(investigation.HasSectionInspection);
        Assert.IsTrue(investigation.Laps.Any(lap => lap.GreenExposure is > 0));
        var run = investigation.Runs!.Single();
        Assert.IsTrue(run.Trend.IncludesReferenceExclusions);
        Assert.IsTrue(run.Trend.Segments.All(segment => segment.Count >= 3));
        Assert.IsNotNull(run.Fit);
        Assert.IsLessThanOrEqualTo(run.Fit.DomainEnd, run.Fit.Points.Max(point => point.X));
        Assert.AreEqual("detected_in_recording", investigation.Sections!.Source);
        var clean = investigation.Comparisons.Single(item => item.Id == "run-1-clean");
        var pair = clean.Inspection!.Sections["A"];
        Assert.AreNotEqual(pair.A.Lap, pair.B.Lap);
        Assert.IsTrue(pair.Rows.Any(row => Math.Abs(row.X - pair.StartPct) < 1e-9), "The section entry must be an inspected row.");
        var trial = clean.Actions.Single().Trial!;
        Assert.AreEqual(3, trial.MinimumLaps);
        Assert.IsGreaterThan(trial.ReferenceMedianSeconds, trial.BaselineMedianSeconds);
        var finish = investigation.Comparisons.Single(item => item.Id == "run-1-finish");
        Assert.AreEqual("mixed", finish.Eligibility);
        Assert.IsTrue(finish.Actions.All(action => action.Trial is null), "Traffic-affected windows must never become a test baseline.");
    }

    /// <summary>
    /// IRP 9/23: the race's test needed three clear laps at 77.5–81.5 green laps into a run.
    /// The backend now states how long a run each test needs and may point a late-run action
    /// at an early-run test of the same section; the fixture's finish window points at the
    /// clean window's test here to exercise that path.
    /// </summary>
    private static string FixtureWithEarlyRunVersion()
    {
        var text = File.ReadAllText(Path.Combine(TestRepositoryPaths.RepositoryRoot, "test-data", "race-investigation.json"));
        var marker = "\"early_run_version\": null";
        var second = text.IndexOf(marker, text.IndexOf(marker, StringComparison.Ordinal) + 1, StringComparison.Ordinal);
        Assert.IsGreaterThan(0, second, "The finish window's action carries early_run_version.");
        return string.Concat(text.AsSpan(0, second), "\"early_run_version\": {\"comparison_id\": \"run-1-clean\", \"action_priority\": 1}", text.AsSpan(second + marker.Length));
    }

    [TestMethod]
    public void CurrentProjectionMapsHowLongARunEachTestNeedsAndItsEarlyRunVersion()
    {
        var investigation = CurrentFixture();
        var clean = investigation.Comparisons.Single(item => item.Id == "run-1-clean");
        Assert.AreEqual(new RaceTrialReach(7, 20, true), clean.Actions.Single().Trial!.Reach);
        Assert.IsTrue(investigation.Comparisons.SelectMany(item => item.Actions).All(action => action.EarlyRunVersion is null));
        Assert.IsNull(investigation.EarlyRunVersion(clean.Actions.Single()));

        using var document = JsonDocument.Parse(FixtureWithEarlyRunVersion());
        var linked = RaceInvestigationMapper.Map(document.RootElement)!;
        Assert.IsNull(linked.UnavailableReason, linked.UnavailableReason);
        var finish = linked.Comparisons.Single(item => item.Id == "run-1-finish").Actions.First();
        Assert.AreEqual(new RaceTrialReference("run-1-clean", 1), finish.EarlyRunVersion);
        var offered = linked.EarlyRunVersion(finish);
        Assert.IsNotNull(offered);
        Assert.AreEqual("run-1-clean", offered.Value.Comparison.Id);
        Assert.IsNotNull(offered.Value.Action.Trial);

        // A reference to a comparison the investigation does not hold offers nothing.
        using var dangling = JsonDocument.Parse(FixtureWithEarlyRunVersion().Replace("\"comparison_id\": \"run-1-clean\"", "\"comparison_id\": \"run-9-early\"", StringComparison.Ordinal));
        var missing = RaceInvestigationMapper.Map(dangling.RootElement)!;
        Assert.IsNull(missing.EarlyRunVersion(missing.Comparisons.Single(item => item.Id == "run-1-finish").Actions.First()));
    }

    [TestMethod]
    public void MalformedReachabilityOrEarlyRunVersionFailsClearly()
    {
        var text = File.ReadAllText(Path.Combine(TestRepositoryPaths.RepositoryRoot, "test-data", "race-investigation.json"));
        foreach (var input in new[]
        {
            text.Replace("\"test_day_reachable\": true", "\"test_day_reachable\": \"yes\"", StringComparison.Ordinal),
            text.Replace("\"run_laps_needed\": 7", "\"run_laps_needed\": 0", StringComparison.Ordinal),
            text.Replace("\"reachability\": {", "\"reachability\": \"short\", \"unused\": {", StringComparison.Ordinal),
            FixtureWithEarlyRunVersion().Replace("\"action_priority\": 1}", "\"action_priority\": \"first\"}", StringComparison.Ordinal)
        })
        {
            Assert.AreNotEqual(text, input, "The fixture edit must apply.");
            using var document = JsonDocument.Parse(input);
            var investigation = RaceInvestigationMapper.Map(document.RootElement)!;
            Assert.AreEqual("unavailable", investigation.Status);
            Assert.IsFalse(string.IsNullOrWhiteSpace(investigation.UnavailableReason));
        }
    }

    [TestMethod]
    public async Task RaceCardOffersTheEarlyRunVersionLabelledAsSuch()
    {
        using var document = JsonDocument.Parse(FixtureWithEarlyRunVersion());
        var investigation = RaceInvestigationMapper.Map(document.RootElement)!;
        var html = await RaceInvestigationPerformanceTests.Render<RaceInvestigationView>(new()
        {
            [nameof(RaceInvestigationView.Investigation)] = investigation,
            [nameof(RaceInvestigationView.ComparisonId)] = "run-1-finish"
        });
        StringAssert.Contains(html, "data-test-comparison=\"run-1-clean\"", "Saving uses the comparison that owns the offered test.");
        StringAssert.Contains(html, "data-test-version=\"early-run\"");
        StringAssert.Contains(html, "Early-run test");
        StringAssert.Contains(html, "Early-run version: the same section, laps 7, 8, 9 against laps 1, 2, 3.");
        StringAssert.Contains(html, "Can be checked in a 20-lap test run (needs about 7 green laps without a pit stop).");
        Assert.DoesNotContain("cannot be saved as a test", html, "A saveable early-run test replaces the unsaveable late one.");

        var sections = await RaceInvestigationPerformanceTests.Render<RaceInvestigationView>(new()
        {
            [nameof(RaceInvestigationView.Investigation)] = investigation,
            [nameof(RaceInvestigationView.View)] = "sections",
            [nameof(RaceInvestigationView.ComparisonId)] = "run-1-finish",
            [nameof(RaceInvestigationView.SectionId)] = "A"
        });
        StringAssert.Contains(sections, "data-test-comparison=\"run-1-clean\"");
        StringAssert.Contains(sections, "Early-run test");

        var plain = await RaceInvestigationPerformanceTests.Render<RaceInvestigationView>(new()
        {
            [nameof(RaceInvestigationView.Investigation)] = CurrentFixture(),
            [nameof(RaceInvestigationView.ComparisonId)] = "run-1-finish"
        });
        Assert.DoesNotContain("data-test-version", plain, "Without a backend reference the card keeps its own action.");
        StringAssert.Contains(plain, "cannot be saved as a test");
    }

    [TestMethod]
    public void LegacyProjectionStillMapsButDoesNotClaimSectionInspection()
    {
        using var document = JsonDocument.Parse(InvestigationJson);
        var investigation = RaceInvestigationMapper.Map(document.RootElement)!;
        Assert.AreEqual(1, investigation.SchemaVersion);
        Assert.IsNull(investigation.Runs);
        Assert.IsFalse(investigation.HasSectionInspection);
        Assert.IsNull(investigation.Comparisons.Single().Inspection);
    }

    [TestMethod]
    public void IncompatibleOrIncompleteCurrentProjectionFailsClearly()
    {
        var text = File.ReadAllText(Path.Combine(TestRepositoryPaths.RepositoryRoot, "test-data", "race-investigation.json"));
        foreach (var input in new[]
        {
            text.Replace("\"schema_version\": 2", "\"schema_version\": 3", StringComparison.Ordinal),
            text.Replace("\"runs\":", "\"runs_removed\":", StringComparison.Ordinal),
            text.Replace("\"projection_version\":", "\"projection_removed\":", StringComparison.Ordinal)
        })
        {
            Assert.AreNotEqual(text, input, "The fixture edit must apply.");
            using var document = JsonDocument.Parse(input);
            var investigation = RaceInvestigationMapper.Map(document.RootElement)!;
            Assert.AreEqual("unavailable", investigation.Status);
            Assert.IsFalse(string.IsNullOrWhiteSpace(investigation.UnavailableReason));
        }
    }

    [TestMethod]
    public void CachedResponsesNeedTheCurrentInvestigationProjection()
    {
        var current = File.ReadAllText(Path.Combine(TestRepositoryPaths.RepositoryRoot, "test-data", "race-investigation.json"));
        JsonElement Response(string investigation) => JsonDocument.Parse($"{{\"analysis_view\":{{\"race_investigation\":{investigation}}}}}").RootElement;
        Assert.IsTrue(RuntimeMapper.HasCurrentInvestigation(Response(current)));
        Assert.IsFalse(RuntimeMapper.HasCurrentInvestigation(Response(InvestigationJson)));
        Assert.IsFalse(RuntimeMapper.HasCurrentInvestigation(Response(current.Replace(RaceInvestigationMapper.CurrentProjectionVersion, "older", StringComparison.Ordinal))));
        Assert.IsTrue(RuntimeMapper.HasCurrentInvestigation(Response("null")), "Without an investigation the analysis profile alone decides.");
    }

    [TestMethod]
    public async Task RaceViewKeepsExcludedLapsVisibleWithExactIdentityAndTest()
    {
        using var document = JsonDocument.Parse(InvestigationJson);
        var investigation = RaceInvestigationMapper.Map(document.RootElement)!;
        var html = await RaceInvestigationPerformanceTests.Render<RaceInvestigationView>(new() { [nameof(RaceInvestigationView.Investigation)] = investigation });
        StringAssert.Contains(html, "data-lap=\"33\"");
        StringAssert.Contains(html, "close traffic");
        StringAssert.Contains(html, "Run 2 · 3 laps · 31, 32, 33");
        StringAssert.Contains(html, "27.0–30.0 green");
        StringAssert.Contains(html, "Compare corner entry");
        StringAssert.Contains(html, "cannot be saved as a test");
        Assert.IsFalse(html.Contains("javascript:", StringComparison.OrdinalIgnoreCase));
    }

    [TestMethod]
    public async Task OfflineTestingRunsViewCountsCleanNotGreenExposure()
    {
        // IRP 9/25 Offline Testing: the Runs view's window chips read "1.0–5.0 green" where no lap is green.
        using var document = JsonDocument.Parse(InvestigationJson);
        var investigation = RaceInvestigationMapper.Map(document.RootElement)!;
        var html = await RaceInvestigationPerformanceTests.Render<RaceInvestigationView>(new()
        {
            [nameof(RaceInvestigationView.Investigation)] = investigation,
            [nameof(RaceInvestigationView.OfflineTesting)] = true
        });
        StringAssert.Contains(html, "27.0–30.0 clean");
        Assert.DoesNotContain("27.0–30.0 green", html);
        Assert.DoesNotContain(" green</", html, "No exposure reads green outside a race.");
        Assert.AreEqual("3.0 clean", InvestigationPresentation.Exposure(3.0, "clean"));
        Assert.AreEqual("3.0 green", InvestigationPresentation.Exposure(3.0), "A race keeps green exposure.");
    }

    [TestMethod]
    public async Task SectionsViewShowsSixChannelsPassIdentityAndSplitTiming()
    {
        var investigation = CurrentFixture();
        var html = await RaceInvestigationPerformanceTests.Render<RaceInvestigationView>(new()
        {
            [nameof(RaceInvestigationView.Investigation)] = investigation,
            [nameof(RaceInvestigationView.View)] = "sections",
            [nameof(RaceInvestigationView.ComparisonId)] = "run-1-clean"
        });
        foreach (var channel in new[] { "speed", "gap", "throttle", "brake", "steering", "slip" })
            StringAssert.Contains(html, $"data-channel=\"{channel}\"");
        Assert.AreEqual(6, CountOf(html, "data-ri-chart"), "All six channels are charts; none hides behind a disclosure.");
        StringAssert.Contains(html, "Entry<b>");
        StringAssert.Contains(html, "Inside<b>");
        StringAssert.Contains(html, "Body slip");
        StringAssert.Contains(html, "Median pass of each group");
        StringAssert.Contains(html, "data-section=\"A\"");
        StringAssert.Contains(html, "Repeat section A at the same run age");
        Assert.IsFalse(html.Contains("<details", StringComparison.Ordinal), "Critical inputs are never inside an accordion.");
    }

    [TestMethod]
    public async Task RunsViewOverlaysObservedTrendAndKeepsFitOptional()
    {
        var investigation = CurrentFixture();
        var html = await RaceInvestigationPerformanceTests.Render<RaceInvestigationView>(new()
        {
            [nameof(RaceInvestigationView.Investigation)] = investigation,
            [nameof(RaceInvestigationView.View)] = "runs"
        });
        StringAssert.Contains(html, "class=\"ri-trend\"");
        StringAssert.Contains(html, "Screened fit");
        Assert.IsFalse(html.Contains("class=\"ri-fit\"", StringComparison.Ordinal), "The screened fit is shown only on request.");
        StringAssert.Contains(html, "Green laps into run");
        StringAssert.Contains(html, "five-lap median of recorded laps, including traffic");
    }

    [TestMethod]
    public async Task LegacyInvestigationOffersRefreshInsteadOfEmptyRunsOrSections()
    {
        using var document = JsonDocument.Parse(InvestigationJson);
        var investigation = RaceInvestigationMapper.Map(document.RootElement)!;
        foreach (var view in new[] { "runs", "sections" })
        {
            var html = await RaceInvestigationPerformanceTests.Render<RaceInvestigationView>(new()
            {
                [nameof(RaceInvestigationView.Investigation)] = investigation,
                [nameof(RaceInvestigationView.View)] = view
            });
            StringAssert.Contains(html, "data-legacy-investigation");
        }
    }

    [TestMethod]
    public void TrialRecordsMapEveryCheckAndKeepVerdictsDistinct()
    {
        var spec = """{"metric":{"key":"section_s","label":"Section time","unit":"s","direction":"decrease","start_pct":0.625,"end_pct":0.77},"section":{"id":"D","label":"D","start_pct":0.625,"end_pct":0.77,"boundary_status":"detected_section"},"annotation":{"configuration_key":"365-single-pit-road","source":"configuration_catalog","version":"v1"},"baseline":{"laps":[130,131,132],"median_s":2.5,"count":3},"reference":{"laps":[35,36,37],"median_s":2.4,"count":3},"exposure_window":[21.8,23.8],"conditions":{"fuel_start_gal":5.7,"track_temp_f":78},"success":{"threshold_s":0.05,"minimum_laps":3,"rule":"rule"}}""";
        var recording = """{"analysis_id":"later","recorded_at":"2026-09-24T18:00:00+00:00","track":"Bristol Motor Speedway","layout":"Single Pit Road","configuration_key":"365-single-pit-road","car_id":116,"car":"Toyota Supra Class B","session_type":"Practice","subsession_id":0}""";
        var json = $$$"""{"schema_version":1,"trial_id":"trial-0123456789abcdef0123","created_at":"2026-09-23T00:00:00Z","title":"Carry more speed into section D","instruction":"Test one change.","evidence":["e"],"source":{"analysis_id":"source","recorded_at":"2026-09-20T20:09:09+00:00","track":"Bristol Motor Speedway","layout":"Single Pit Road","car":"Toyota Supra Class B","car_id":116},"comparison":{"id":"runs-1-2-age","title":"Runs 1 and 2"},{{{spec[1..^1]}}},"evaluation_count":2,"future_field":{"kept":true}}""";
        var evaluations = $$"""[{"evaluated_at":"a","analysis_id":"first","recording":{{recording}},"verdict":"not_comparable","reasons":["No screened laps between 19.8 and 25.8 green laps into a run."],"confounds":[]},{"evaluated_at":"b","analysis_id":"later","recording":{{recording}},"verdict":"not_established","reasons":["Moved +0.010 s"],"confounds":["Starting fuel 5.7 → 12.0 gal."],"window":[19.8,25.8],"laps":[{"lap":22,"seconds":2.49,"green_exposure":21.5,"fuel_start_gal":12.0}],"median_s":2.49,"count":1,"improvement_s":0.01}]""";
        using var trialDocument = JsonDocument.Parse(json);
        using var evaluationDocument = JsonDocument.Parse(evaluations);
        var trial = RaceInvestigationMapper.Trial(trialDocument.RootElement, evaluationDocument.RootElement);
        Assert.AreEqual("runs-1-2-age", trial.ComparisonId);
        Assert.AreEqual(0.05, trial.Spec.ThresholdSeconds);
        Assert.AreEqual(21.8, trial.Spec.ExposureStart);
        Assert.HasCount(2, trial.Evaluations);
        CollectionAssert.AreEqual(new[] { "not_comparable", "not_established" }, trial.Evaluations.Select(item => item.Verdict).ToArray());
        Assert.AreEqual("116", trial.Source.CarId);
        Assert.IsTrue(trial.IsOpen, "Neither check established a result, so the test stays open.");
        Assert.AreEqual("Not comparable", InvestigationPresentation.Verdict("not_comparable"));
        Assert.AreEqual("Not established", InvestigationPresentation.Verdict("not_established"));
        using var future = JsonDocument.Parse(json.Replace("\"schema_version\":1", "\"schema_version\":2", StringComparison.Ordinal));
        Assert.ThrowsExactly<JsonException>(() => RaceInvestigationMapper.Trial(future.RootElement));
    }

    private static int CountOf(string text, string value)
    {
        var count = 0;
        for (var index = text.IndexOf(value, StringComparison.Ordinal); index >= 0; index = text.IndexOf(value, index + value.Length, StringComparison.Ordinal)) count++;
        return count;
    }
}
