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
        StringAssert.Contains(priorities["Strategy"], "37.8-lap conservative finish margin");
        StringAssert.Contains(triggers["Fuel margin"], "55-lap finish");
        StringAssert.Contains(triggers["Balance response"], "undo it if pace or stability worsens");
        Assert.IsFalse(plan.Priorities.Any(item => item.Claim.Text.Contains("positive value is", StringComparison.OrdinalIgnoreCase)));
    }

    [TestMethod]
    public void ShortPortlandPlan_RewritesRecordedCardForRequestedDistance()
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
        StringAssert.Contains(priorities["Strategy"], "No fuel stop for 15 laps");
        StringAssert.Contains(priorities["Strategy"], "19.7-lap conservative finish margin");
        Assert.IsTrue(plan.Triggers.Any(item =>
            item.Label == "Fuel margin" && item.Claim.Text.Contains("15-lap finish", StringComparison.Ordinal)));
        Assert.IsFalse(plan.Priorities.Any(item => item.Claim.Text.Contains("100 laps", StringComparison.OrdinalIgnoreCase)));
    }

    private static string Response(string track, int scheduledLaps, double rangeLaps, string paceLabel, string paceText) => $$"""
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
            "all_green_range_laps": {{rangeLaps.ToString(System.Globalization.CultureInfo.InvariantCulture)}},
            "minimum_stops_all_green": 2,
            "equal_stint_pit_targets_all_green": [33, 67],
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
