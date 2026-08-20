using System.Text.Json;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class RacePlanningRecommendationTests
{
    [TestMethod]
    public void IowaLikeNoStopPlan_ReplacesMetricDefinitionWithCompleteRaceActions()
    {
        using var response = JsonDocument.Parse(Response(
            track: "Iowa Speedway",
            scheduledLaps: 55,
            rangeLaps: 92.8,
            paceLabel: "Long run",
            paceText: "As the run ages, protect the RF with repeatable entries and progressive brake release."));

        var plan = RuntimeMapper.Plan(response.RootElement, 55, "Laps");
        var priorities = plan.Priorities.ToDictionary(item => item.Label, item => item.Claim.Text);
        var triggers = plan.Triggers.ToDictionary(item => item.Label, item => item.Claim.Text);

        Assert.AreEqual(
            "Protect the RF from the start: finish brake release before adding steering.",
            priorities["Start"]);
        StringAssert.Contains(priorities["Long run"], "As the run ages");
        StringAssert.Contains(priorities["Strategy"], "55 laps");
        StringAssert.Contains(priorities["Strategy"], "37.8-lap finish margin");
        StringAssert.Contains(triggers["Fuel margin"], "37.8-lap finish margin");
        StringAssert.Contains(triggers["Balance response"], "undo it if pace or stability worsens");
        Assert.IsFalse(plan.Priorities.Any(item => item.Claim.Text.Contains("positive value is", StringComparison.OrdinalIgnoreCase)));
    }

    [TestMethod]
    public void ShortPortlandPlan_WithholdsFuelAdviceForARequestedDistanceTheBackendDidNotDecide()
    {
        using var response = JsonDocument.Parse(Response(
            track: "Portland International Raceway",
            scheduledLaps: 100,
            rangeLaps: 34.7,
            paceLabel: "Long run",
            paceText: "Keep entries, throttle pickup, and steering corrections repeatable as the run ages."));

        var plan = RuntimeMapper.Plan(response.RootElement, 15, "Laps");
        var priorities = plan.Priorities.ToDictionary(item => item.Label, item => item.Claim.Text);

        Assert.AreEqual(15, plan.ScheduledLaps);
        Assert.IsTrue(priorities.ContainsKey("Race pace"));
        StringAssert.Contains(priorities["Race pace"], "For all 15 laps");
        StringAssert.Contains(priorities["Strategy"], "differs from the backend-owned decision");
        Assert.IsFalse(plan.FuelPlan!.IsUsable);
        Assert.IsFalse(plan.FuelPlan.NoStopLanguagePermitted && plan.FuelPlan.AppliesToRequestedDistance);
        Assert.IsTrue(plan.Triggers.Any(item =>
            item.Label == "Fuel check" && item.Claim.Kind == EvidenceKind.Unavailable));
        Assert.IsFalse(plan.Priorities.Any(item => item.Claim.Text.Contains("100 laps", StringComparison.OrdinalIgnoreCase)));
    }

    [TestMethod]
    public void HybridLimits_WithholdStopPlanUntilFinishConstraintIsResolved()
    {
        using var response = JsonDocument.Parse("""
        {
          "analysis_view": {
            "analysis_profile_version":"post-race-foundations-v14",
            "identity": {"track_name":"Hybrid Speedway","car_name":"Test Car","is_fixed_setup":true},
            "race_summary": {"scheduled_laps":500,"scheduled_minutes":30.5,"recorded_laps":8},
            "runs":[],"lap_traces":{"traces":[]},
            "strategy": {
              "confidence":"medium",
              "forecast": {
                "status":"hybrid_finish_constraint_unresolved",
                "scheduled_laps":null,
                "all_green_range_laps":34.7,
                "minimum_stops_all_green":14,
                "equal_stint_pit_targets_all_green":[33,67]
              }
            }
          },
          "race_card": {
            "actions":[{"label":"Strategy","evidence_type":"derived","text":"Plan 14 fuel stops for 500 laps"}],
            "corner_playbook":{"rows":[]},"race_triggers":[]
          }
        }
        """);

        var plan = RuntimeMapper.Plan(response.RootElement);
        var strategy = plan.Priorities.Single(item => item.Label == "Strategy").Claim.Text;

        Assert.AreEqual(0, plan.ScheduledLaps);
        StringAssert.Contains(plan.DistanceLabel, "500 laps");
        StringAssert.Contains(plan.DistanceLabel, "30.5 minutes");
        Assert.AreEqual("Stop count needs a resolved race distance", plan.StopCount);
        Assert.IsEmpty(plan.PitTargets);
        StringAssert.Contains(strategy, "resolve the governing finish constraint");
        Assert.DoesNotContain("500", strategy);
        Assert.IsFalse(plan.Priorities.Concat(plan.Triggers.Select(item => new RaceAction(item.Label, item.Claim)))
            .Any(item => item.Claim.Text.Contains("14", StringComparison.Ordinal) ||
                         item.Claim.Text.Contains("33", StringComparison.Ordinal) ||
                         item.Claim.Text.Contains("500", StringComparison.Ordinal)));
    }

    [TestMethod]
    public void TimedPlan_LabelsLapConversionAsAnEstimate()
    {
        using var response = JsonDocument.Parse("""
        {
          "analysis_view": {
            "analysis_profile_version":"post-race-foundations-v14",
            "identity":{"track_name":"Timed Speedway","car_name":"Test Car"},
            "race_summary":{"scheduled_minutes":30.5},
            "runs":[{"pace":{"early_average_lap_s":75.0,"late_average_lap_s":75.0}}],
            "lap_traces":{"traces":[]},
            "strategy":{"confidence":"medium","forecast":{"all_green_range_laps":10.0,"assumptions":[]}}
          },
          "race_card":{"actions":[],"corner_playbook":{"rows":[]},"race_triggers":[]}
        }
        """);

        var plan = RuntimeMapper.Plan(response.RootElement, 30.5, "Minutes");
        var text = string.Join(" ", plan.Priorities.Select(item => item.Claim.Text)
            .Concat(plan.Triggers.Select(item => item.Claim.Text)));

        Assert.AreEqual(25, plan.ScheduledLaps);
        Assert.IsTrue(plan.DistanceIsEstimated);
        StringAssert.Contains(plan.DistanceLabel, "30.5 minutes");
        StringAssert.Contains(plan.DistanceLabel, "~25 laps");
        Assert.IsTrue(plan.Assumptions.Any(item => item.Contains("75.0-second", StringComparison.Ordinal) && item.Contains("can vary", StringComparison.Ordinal)));
        Assert.DoesNotContain("all 25 laps", text, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain("25-lap finish", text, StringComparison.OrdinalIgnoreCase);
        StringAssert.Contains(plan.StopCount, "needs a recorded fuel range");
    }

    [TestMethod]
    public void TimedPlan_WithoutCleanPaceLeavesLapEstimateUnavailable()
    {
        using var response = JsonDocument.Parse("""
        {"analysis_view":{"identity":{},"race_summary":{"scheduled_minutes":30},"runs":[],"laps":[],
          "lap_traces":{"traces":[]},"strategy":{"forecast":{"all_green_range_laps":10.0}}},
         "race_card":{"actions":[],"corner_playbook":{"rows":[]},"race_triggers":[]}}
        """);

        var plan = RuntimeMapper.Plan(response.RootElement, 30, "Minutes");

        Assert.AreEqual(0, plan.ScheduledLaps);
        Assert.IsFalse(plan.DistanceIsEstimated);
        StringAssert.Contains(plan.DistanceLabel, "lap estimate unavailable");
        Assert.IsEmpty(plan.PitTargets);
        StringAssert.Contains(plan.StopCount, "needs a comparable clean lap");
    }

    [TestMethod]
    public void TimedPlan_ExcludesRepairConfoundedPaceFromLapEstimate()
    {
        using var response = JsonDocument.Parse("""
        {"analysis_view":{"identity":{},"race_summary":{"scheduled_minutes":30},
          "runs":[
            {"pace":{"early_average_lap_s":60,"late_average_lap_s":60},"damage_repair_context":{"automatic_coaching_reference_eligible":false,"reason_codes":["recorded_repair_evidence"]}},
            {"pace":{"early_average_lap_s":75,"late_average_lap_s":75},"damage_repair_context":{"automatic_coaching_reference_eligible":true,"reason_codes":[]}}],
          "lap_traces":{"traces":[]},"strategy":{"forecast":{"all_green_range_laps":10.0}}},
         "race_card":{"actions":[],"corner_playbook":{"rows":[]},"race_triggers":[]}}
        """);

        var plan = RuntimeMapper.Plan(response.RootElement, 30, "Minutes");

        Assert.AreEqual(24, plan.ScheduledLaps);
        StringAssert.Contains(plan.DistanceLabel, "~24 laps");
        Assert.IsFalse(plan.DistanceLabel.Contains("~30 laps", StringComparison.Ordinal));
    }

    [TestMethod]
    public void ExactLapDistance_RemainsKnownWhenFuelForecastIsUnavailable()
    {
        using var response = JsonDocument.Parse("""
        {"analysis_view":{"analysis_profile_version":"post-race-foundations-v14","identity":{},
          "race_summary":{"scheduled_laps":80},"runs":[],"lap_traces":{"traces":[]},
          "strategy":{"forecast":{"status":"insufficient_evidence","scheduled_laps":80}}},
         "race_card":{"actions":[],"corner_playbook":{"rows":[]},"race_triggers":[]}}
        """);

        var plan = RuntimeMapper.Plan(response.RootElement);

        Assert.AreEqual(80, plan.ScheduledLaps);
        Assert.AreEqual("80 laps", plan.DistanceLabel);
        StringAssert.Contains(plan.StopCount, "needs a recorded fuel range");
        Assert.IsEmpty(plan.PitTargets);
    }

    private static string Response(string track, int scheduledLaps, double rangeLaps, string paceLabel, string paceText)
    {
        var stops = scheduledLaps == 55 ? 0 : 2;
        var stints = stops + 1;
        var margin = stints * rangeLaps - scheduledLaps;
        var targets = stops == 0 ? "[]" : "[33.333333333333336,66.66666666666667]";
        return $$"""
    {
      "analysis_view": {
        "identity": {
          "track_name": "{{track}}",
          "track_config": "Oval",
          "car_name": "Toyota Supra Class B",
          "is_fixed_setup": true
        },
        "race_summary": { "scheduled_laps": {{scheduledLaps}}, "recorded_laps": {{scheduledLaps}} },
        "runs": [],
        "lap_traces": { "traces": [] },
        "strategy": {
          "confidence": "medium",
          "measured_green_fuel_gal_per_lap": 0.196,
          "forecast": {
            "status": "usable",
            "scheduled_laps": {{scheduledLaps}},
            "all_green_range_laps": {{rangeLaps.ToString(System.Globalization.CultureInfo.InvariantCulture)}},
            "minimum_stops_all_green": {{stops}},
            "equal_stint_pit_targets_all_green": {{targets}},
            "race_plan_decision": {
              "decision_version": 1,
              "status": "usable",
              "scheduled_laps": {{scheduledLaps}},
              "green_burn_l_per_lap": null,
              "maximum_start_fuel_l": null,
              "reserve_green_laps": 1.0,
              "reserve_fuel_l": null,
              "usable_fuel_l": null,
              "all_green_range_laps": {{rangeLaps.ToString(System.Globalization.CultureInfo.InvariantCulture)}},
              "minimum_stops": {{stops}},
              "stints": {{stints}},
              "final_stint_margin_laps": {{margin.ToString(System.Globalization.CultureInfo.InvariantCulture)}},
              "equal_stint_pit_targets": {{targets}},
              "caution_scenario": null,
              "no_stop_language_permitted": {{(stops == 0 ? "true" : "false")}},
              "re_decidable": true,
              "classification": "fuel-feasibility decision",
              "assumptions": [],
              "limitations": []
            },
            "assumptions": []
          }
        }
      },
      "race_card": {
        "actions": [
          {
            "label": "Start",
            "evidence_type": "inferred",
            "text": "A positive value is falloff; compare the same run's tire condition and driving load."
          },
          {
            "label": "{{paceLabel}}",
            "evidence_type": "inferred",
            "text": "{{paceText}}"
          },
          {
            "label": "Strategy",
            "evidence_type": "derived",
            "text": "Plan 2 fuel stops for 100 laps; target Lap 33/67"
          }
        ],
        "corner_playbook": { "rows": [] },
        "race_triggers": [
          {
            "label": "Run-evolution checkpoint",
            "evidence_type": "unavailable",
            "text": "Phase green-lap bounds unavailable"
          },
          {
            "label": "Fuel decision",
            "evidence_type": "derived",
            "text": "Plan 2 fuel stops for 100 laps; target Lap 33/67"
          },
          {
            "label": "Validation rule",
            "evidence_type": "inferred",
            "text": "Fixed setup: change one driving input and compare the same corner in the same race phase"
          }
        ]
      }
    }
    """;
    }
}
