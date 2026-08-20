using System.Globalization;
using System.Text.Json;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class RacePlanDecisionConsumerTests
{
    [TestMethod]
    public void ExactRangeJustBelowDisplayedDistance_KeepsTheBackendOneStopDecision()
    {
        using var response = JsonDocument.Parse(Response(Decision(50, 49.96, 1, [25], 49.92), legacyStops: 0));

        var plan = RuntimeMapper.Plan(response.RootElement, 50, "Laps");

        Assert.IsTrue(plan.FuelPlan!.IsUsable);
        Assert.AreEqual(1, plan.FuelPlan.MinimumStops);
        Assert.IsFalse(plan.FuelPlan.NoStopLanguagePermitted);
        Assert.AreEqual(49.96, plan.FuelPlan.AllGreenRangeLaps);
        Assert.AreEqual(49.92, plan.FuelPlan.FinalStintMarginLaps);
        StringAssert.Contains(plan.Priorities.Single(item => item.Label == "Strategy").Claim.Text, "Plan 1 fuel stop");
        Assert.IsFalse(AllPlanText(plan).Contains("no fuel stop", StringComparison.OrdinalIgnoreCase));
    }

    [TestMethod]
    public void RoundedSixtySixPointSevenProjection_CannotReplaceTheBackendThreeStopDecision()
    {
        using var response = JsonDocument.Parse(Response(
            Decision(200, 66.66, 3, [50, 100, 150], 66.64),
            legacyStops: 2));

        var plan = RuntimeMapper.Plan(response.RootElement, 200, "Laps");

        Assert.AreEqual(3, plan.FuelPlan!.MinimumStops);
        CollectionAssert.AreEqual(new[] { 50d, 100d, 150d }, plan.FuelPlan.EqualStintPitTargets.ToArray());
        StringAssert.Contains(plan.StopCount, "3 stops");
        Assert.IsFalse(AllPlanText(plan).Contains("no-stop", StringComparison.OrdinalIgnoreCase));
    }

    [TestMethod]
    public void TechnicalProjection_UsesTheSameExactDecisionAsPlanning()
    {
        using var response = JsonDocument.Parse(Response(Decision(50, 49.96, 1, [25], 49.92), legacyStops: 0));

        var workspace = RuntimeMapper.Analysis(response.RootElement);

        Assert.AreEqual(49.96, workspace.Strategy.AllGreenRangeLaps);
        Assert.AreEqual(1, workspace.Strategy.MinimumStopsAllGreen);
        Assert.AreEqual(1, workspace.Strategy.FuelPlan!.MinimumStops);
        Assert.IsFalse(workspace.Strategy.FuelPlan.NoStopLanguagePermitted);
    }

    [TestMethod]
    public void GenuineNoStopLanguage_RequiresTheTransportedPermission()
    {
        using var response = JsonDocument.Parse(Response(Decision(40, 55.25, 0, [], 15.25), legacyStops: 4));

        var plan = RuntimeMapper.Plan(response.RootElement, 40, "Laps");

        Assert.IsTrue(plan.FuelPlan!.NoStopLanguagePermitted);
        Assert.AreEqual(0, plan.FuelPlan.MinimumStops);
        StringAssert.Contains(plan.Priorities.Single(item => item.Label == "Strategy").Claim.Text, "No fuel stop");
    }

    [TestMethod]
    public void ExplicitNullAuthority_NeverFallsBackToAConfidentLegacyNoStopProjection()
    {
        using var response = JsonDocument.Parse(Response("null", legacyStops: 0));

        var plan = RuntimeMapper.Plan(response.RootElement, 50, "Laps");

        Assert.AreEqual("authoritative_decision_unreadable", plan.FuelPlan!.Status);
        Assert.IsFalse(plan.FuelPlan.IsUsable);
        Assert.IsFalse(plan.FuelPlan.NoStopLanguagePermitted);
        Assert.IsFalse(AllPlanText(plan).Contains("no fuel stop", StringComparison.OrdinalIgnoreCase));
        Assert.IsEmpty(plan.PitTargets);
    }

    [TestMethod]
    public void FutureDecisionVersion_IsRefusedWithoutLegacyFallback()
    {
        var future = Decision(50, 49.96, 1, [25], 49.92).Replace(
            "\"decision_version\":1",
            "\"decision_version\":2",
            StringComparison.Ordinal);
        using var response = JsonDocument.Parse(Response(future, legacyStops: 0));

        var plan = RuntimeMapper.Plan(response.RootElement, 50, "Laps");

        Assert.AreEqual("authoritative_decision_unreadable", plan.FuelPlan!.Status);
        StringAssert.Contains(plan.FuelPlan.UnavailableReason, "decision_version 2");
        Assert.IsFalse(AllPlanText(plan).Contains("no fuel stop", StringComparison.OrdinalIgnoreCase));
    }

    [TestMethod]
    public void ContradictoryFuelScalars_AreRefusedRatherThanNormalized()
    {
        var measured = Decision(50, 49.96, 1, [25], 49.92,
            greenBurn: 1, maximumFuel: 50.96, reserveFuel: 1, usableFuel: 49.96);
        var tampered = measured.Replace("\"usable_fuel_l\":49.96", "\"usable_fuel_l\":999", StringComparison.Ordinal);
        using var response = JsonDocument.Parse(Response(tampered, legacyStops: 0));

        var plan = RuntimeMapper.Plan(response.RootElement, 50, "Laps");

        Assert.AreEqual("authoritative_decision_unreadable", plan.FuelPlan!.Status);
        StringAssert.Contains(plan.FuelPlan.UnavailableReason, "usable_fuel_l");
        Assert.IsFalse(plan.FuelPlan.IsUsable);
    }

    [TestMethod]
    public void UnknownOptionalDecisionFields_AreRetainedByTheTypedProjection()
    {
        var decision = Decision(50, 49.96, 1, [25], 49.92).Replace(
            "\"limitations\":[]",
            "\"limitations\":[],\"future_hint\":{\"mode\":\"retain\"}",
            StringComparison.Ordinal);
        using var response = JsonDocument.Parse(Response(decision, legacyStops: 0));

        var plan = RuntimeMapper.Plan(response.RootElement, 50, "Laps");

        Assert.AreEqual("{\"mode\":\"retain\"}", plan.FuelPlan!.ExtensionData["future_hint"]);
    }

    [TestMethod]
    public void RequestedDistanceMismatch_LeavesTheSourceDecisionVisibleButInapplicable()
    {
        using var response = JsonDocument.Parse(Response(Decision(100, 34.7, 2, [33.333333333333336, 66.66666666666667], 4.1), legacyStops: 2));

        var plan = RuntimeMapper.Plan(response.RootElement, 15, "Laps");

        Assert.IsFalse(plan.FuelPlan!.IsUsable);
        Assert.IsFalse(plan.FuelPlan.AppliesToRequestedDistance);
        StringAssert.Contains(plan.FuelPlan.UnavailableReason, "differs from the backend-owned decision");
        Assert.IsFalse(AllPlanText(plan).Contains("no fuel stop", StringComparison.OrdinalIgnoreCase));
    }

    [TestMethod]
    public void LegacyOwnDistance_AdoptsTheStoredCountWithoutReparsingTheRoundedRange()
    {
        using var response = JsonDocument.Parse(ResponseWithoutDecision(50, 50.0, 1, [25]));

        var plan = RuntimeMapper.Plan(response.RootElement);

        Assert.IsTrue(plan.FuelPlan!.IsLegacy);
        Assert.AreEqual(1, plan.FuelPlan.MinimumStops);
        Assert.IsFalse(plan.FuelPlan.ReDecidable);
        Assert.IsFalse(plan.FuelPlan.NoStopLanguagePermitted);
    }

    private static string AllPlanText(RacePlanBriefing plan) => string.Join(" ",
        plan.Priorities.Select(item => item.Claim.Text)
            .Concat(plan.Triggers.Select(item => item.Claim.Text)));

    private static string Decision(
        double scheduled,
        double range,
        int stops,
        IReadOnlyList<double> targets,
        double margin,
        double? greenBurn = null,
        double? maximumFuel = null,
        double? reserveFuel = null,
        double? usableFuel = null)
    {
        var number = (double? value) => value?.ToString("R", CultureInfo.InvariantCulture) ?? "null";
        return $$"""
        {
          "decision_version":1,
          "status":"usable",
          "scheduled_laps":{{number(scheduled)}},
          "green_burn_l_per_lap":{{number(greenBurn)}},
          "maximum_start_fuel_l":{{number(maximumFuel)}},
          "reserve_green_laps":1.0,
          "reserve_fuel_l":{{number(reserveFuel)}},
          "usable_fuel_l":{{number(usableFuel)}},
          "all_green_range_laps":{{number(range)}},
          "minimum_stops":{{stops}},
          "stints":{{stops + 1}},
          "final_stint_margin_laps":{{number(margin)}},
          "equal_stint_pit_targets":[{{string.Join(",", targets.Select(value => number(value)))}}],
          "caution_scenario":null,
          "no_stop_language_permitted":{{(stops == 0 ? "true" : "false")}},
          "re_decidable":true,
          "classification":"fuel-feasibility decision",
          "assumptions":[],
          "limitations":[]
        }
        """;
    }

    private static string Response(string decision, int legacyStops) => $$"""
    {
      "analysis_view":{
        "analysis_profile_version":"post-race-foundations-v15",
        "identity":{"track_name":"Contract Speedway","car_name":"Contract Car"},
        "race_summary":{"scheduled_laps":50},
        "runs":[],"lap_traces":{"traces":[]},
        "strategy":{"confidence":"high","forecast":{
          "status":"usable","scheduled_laps":50,"all_green_range_laps":50.0,
          "minimum_stops_all_green":{{legacyStops}},
          "equal_stint_pit_targets_all_green":[],
          "race_plan_decision":{{decision}}
        }
        }
      },
      "race_card":{"actions":[],"corner_playbook":{"rows":[]},"race_triggers":[]}
    }
    """;

    private static string ResponseWithoutDecision(double scheduled, double range, int stops, IReadOnlyList<double> targets) => $$"""
    {
      "analysis_view":{
        "analysis_profile_version":"post-race-foundations-v15",
        "identity":{"track_name":"Legacy Speedway","car_name":"Legacy Car"},
        "race_summary":{"scheduled_laps":{{scheduled.ToString("R", CultureInfo.InvariantCulture)}},"recorded_laps":10},
        "runs":[],"lap_traces":{"traces":[]},
        "strategy":{"confidence":"medium","forecast":{
          "status":"usable",
          "scheduled_laps":{{scheduled.ToString("R", CultureInfo.InvariantCulture)}},
          "all_green_range_laps":{{range.ToString("R", CultureInfo.InvariantCulture)}},
          "minimum_stops_all_green":{{stops}},
          "equal_stint_pit_targets_all_green":[{{string.Join(",", targets.Select(value => value.ToString("R", CultureInfo.InvariantCulture)))}}]
        }
        }
      },
      "race_card":{"actions":[],"corner_playbook":{"rows":[]},"race_triggers":[]}
    }
    """;
}
