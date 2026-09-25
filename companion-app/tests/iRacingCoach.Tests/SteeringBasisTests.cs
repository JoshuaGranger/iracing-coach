using System.Globalization;
using System.Text.Json;
using System.Text.RegularExpressions;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;
using iRacingCoach.UI;

namespace iRacingCoach.Tests;

/// <summary>
/// Joshua (2026-09-25): every "steering" reference is the amount of steering applied,
/// irrespective of the steering ratio. The backend declares road-wheel steering (wheel
/// angle ÷ the recording's ratio) or the labelled steering-wheel angle; the app names and
/// scales steering from that declaration and never mixes the two.
/// </summary>
[TestClass]
public sealed class SteeringBasisTests
{
    private const string RoadWheel12 = """
        {"schema_version":1,"basis":"road_wheel","status":"recorded","label":"Steering (road wheel)","short_label":"Steering (road wheel)",
         "unit":"deg","value_unit":"rad","degree_digits":2,"steering_ratio":12.0,"steering_ratio_text":"12:1",
         "ratio_source":"CarSetup.Chassis.Front.SteeringRatio","setup_steering_offset":"+6 deg","steering_offset_applied":false,
         "raw_channel":"SteeringWheelAngle","evidence_class":"derived","definition":"Recorded SteeringWheelAngle divided by the recording's 12:1 steering ratio.","reason":null}
        """;

    private const string NotRecorded = """
        {"schema_version":1,"basis":"steering_wheel","status":"not_recorded","label":"Steering wheel angle — ratio not recorded",
         "short_label":"Steering wheel angle","steering_ratio":null,"reason":"The garage setup does not state a steering ratio."}
        """;

    private static AnalysisWorkspace Workspace(string? steering)
    {
        var traces = steering is null ? "{\"traces\":[]}" : "{\"traces\":[],\"steering\":" + steering + "}";
        using var response = JsonDocument.Parse(
            "{\"analysis_view\":{\"analysis_profile_version\":\"post-race-foundations-v23\",\"identity\":{\"event_type\":\"Race\"}," +
            "\"race_summary\":{\"recorded_laps\":1},\"runs\":[],\"laps\":[],\"lap_traces\":" + traces + "," +
            "\"track_profile\":{\"shape\":[],\"detected_corner_segments\":[]},\"strategy\":{},\"damage_repair\":{},\"setup_telemetry\":{},\"data_quality\":{}}}");
        return RuntimeMapper.Analysis(response.RootElement);
    }

    [TestMethod]
    public void MapperReadsRoadWheelSteeringFromTheTraces()
    {
        var steering = Workspace(RoadWheel12).SteeringBasis;
        Assert.IsTrue(steering.IsRoadWheel);
        Assert.AreEqual(12d, steering.SteeringRatio);
        Assert.AreEqual("12:1", steering.SteeringRatioText);
        Assert.AreEqual("CarSetup.Chassis.Front.SteeringRatio", steering.RatioSource);
        Assert.AreEqual("Steering (road wheel)", steering.Label);
        Assert.AreEqual(2, steering.DegreeDigits);
        Assert.AreEqual(2d / 12, steering.Threshold(2), 1e-12, "Wheel-angle thresholds convert as the backend converts them.");
    }

    [TestMethod]
    public void AnUnknownRatioStaysLabelledSteeringWheelAngle()
    {
        var steering = Workspace(NotRecorded).SteeringBasis;
        Assert.IsFalse(steering.IsRoadWheel);
        Assert.AreEqual("Steering wheel angle — ratio not recorded", steering.Label);
        Assert.AreEqual("The garage setup does not state a steering ratio.", steering.Reason);
        Assert.AreEqual(1, steering.DegreeDigits);
        Assert.AreEqual(2d, steering.Threshold(2));
    }

    [TestMethod]
    public void AnUndeclaredOrContradictoryBasisIsNeverRoadWheel()
    {
        var legacy = Workspace(null);
        Assert.IsNull(legacy.Steering);
        Assert.AreSame(AnalysisSteeringBasis.Undeclared, legacy.SteeringBasis);
        Assert.AreEqual("Steering wheel angle — ratio not recorded", legacy.SteeringBasis.Label);

        // A road-wheel claim without a usable ratio cannot put a road-wheel label on wheel angles.
        foreach (var claim in new[] { """{"basis":"road_wheel","steering_ratio":null,"label":"Steering (road wheel)"}""",
                                      """{"basis":"road_wheel","steering_ratio":0,"label":"Steering (road wheel)"}""",
                                      """{"basis":"road_wheel","steering_ratio":"12:1"}""" })
        {
            var steering = Workspace(claim).SteeringBasis;
            Assert.IsFalse(steering.IsRoadWheel, claim);
            Assert.AreEqual("Steering wheel angle — ratio not recorded", steering.Label, claim);
        }
    }

    [TestMethod]
    public void TraceSignalsAreNamedForTheBasisAndNeverPairedWithTheRawWheel()
    {
        Assert.AreEqual("Steering (road wheel)", AnalysisTraceLayouts.Signal("steering", Workspace(RoadWheel12).Steering)!.Name);
        var fallback = AnalysisTraceLayouts.Signal("steering", Workspace(NotRecorded).Steering)!;
        Assert.AreEqual("Steering wheel angle — ratio not recorded", fallback.Name);
        Assert.AreEqual("Wheel", fallback.ShortLabel);
        Assert.AreEqual("Steering wheel angle", AnalysisTraceLayouts.Signal("steering-wheel-angle")!.Name);
        Assert.IsFalse(AnalysisTraceLayouts.SecondarySignalOptions("steering").Any(signal => signal.Id == "steering-wheel-angle"));
        Assert.IsFalse(AnalysisTraceLayouts.SecondarySignalOptions("steering-wheel-angle").Any(signal => signal.Id == "steering"));

        var layout = new AnalysisTraceLayout { Rows = [new AnalysisTraceRow { PrimarySignalId = "steering", SecondarySignalId = "steering-wheel-angle" }] };
        Assert.IsTrue(AnalysisTraceLayouts.ValidateAndRepair(layout));
        Assert.AreEqual(string.Empty, layout.Rows[0].SecondarySignalId, "A saved layout cannot draw road-wheel steering and wheel angle on one chart.");
    }

    [TestMethod]
    public void InvestigationAndPairsCarryTheirSteering()
    {
        var investigation = RaceInvestigationMapperTests.CurrentFixture();
        Assert.IsTrue(investigation.SteeringBasis.IsRoadWheel);
        Assert.AreEqual(12d, investigation.SteeringBasis.SteeringRatio);
        var pair = investigation.Comparisons.Select(item => item.Inspection).OfType<RaceInspection>()
            .SelectMany(item => item.Sections.Values).First();
        Assert.IsTrue(pair.SteeringDeclaration.IsRoadWheel);
        Assert.AreEqual("Steering (road wheel)", pair.SteeringDeclaration.Label);
        Assert.AreEqual(2, pair.SteeringDeclaration.DegreeDigits);
        var metric = investigation.Comparisons.SelectMany(item => item.Metrics).First(item => item.Key == "steering");
        Assert.AreEqual("Average steering (road wheel)", metric.Label);
    }

    [TestMethod]
    public void PairsWithoutComparableSteeringSayNotCompared()
    {
        using var withheld = JsonDocument.Parse("""{"basis":null,"label":"Steering (road wheel)","comparable":false,"reason":"Steering is not compared: the saved test's steering is steering-wheel angle without a recorded steering ratio."}""");
        var pair = SteeringBasisMapper.MapPair(withheld.RootElement)!;
        Assert.IsFalse(pair.Comparable);
        Assert.IsFalse(pair.IsRoadWheel);
        Assert.AreEqual(SteeringBasisMapper.NotComparedLabel, pair.Label);
        StringAssert.Contains(pair.Reason, "without a recorded steering ratio");

        using var wheel = JsonDocument.Parse("""{"basis":"steering_wheel","label":"Steering wheel angle — ratio not readable","comparable":true}""");
        Assert.AreEqual("Steering wheel angle — ratio not readable", SteeringBasisMapper.MapPair(wheel.RootElement)!.Label);
        Assert.AreSame(RaceSteeringPair.Undeclared, (SteeringBasisMapper.MapPair(default) ?? RaceSteeringPair.Undeclared));
    }

    [TestMethod]
    public async Task SectionPanelLabelsRoadWheelSteeringAndWithholdsUncomparableSteering()
    {
        var investigation = RaceInvestigationMapperTests.CurrentFixture();
        var comparison = investigation.Comparisons.First(item => item.Inspection?.Sections.Count > 0);
        var section = comparison.Inspection!.Sections.Keys.First();
        async Task<string> Render(RaceInvestigation value) => await RaceInvestigationPerformanceTests.Render<RaceInvestigationView>(new()
        {
            [nameof(RaceInvestigationView.Investigation)] = value,
            [nameof(RaceInvestigationView.View)] = "sections",
            [nameof(RaceInvestigationView.ComparisonId)] = comparison.Id,
            [nameof(RaceInvestigationView.SectionId)] = section
        });

        var road = await Render(investigation);
        StringAssert.Contains(road, ">Steering (road wheel)<small>°</small>");
        StringAssert.Contains(road, "steering-wheel angle divided by the steering ratio");
        StringAssert.Contains(road, "data-ri-read=\"steering\" data-side=\"a\" data-digits=\"2\"");
        Assert.DoesNotContain("Steering wheel angle", road);

        var pair = comparison.Inspection.Sections[section];
        var withheldPair = pair with
        {
            Steering = new RaceSteeringPair(null, SteeringBasisMapper.NotComparedLabel, "Steering", 1, false, "Steering is not compared: reason."),
            Rows = pair.Rows.Select(row => row with { A = row.A with { Steering = null }, B = row.B with { Steering = null } }).ToArray()
        };
        var withheld = investigation with
        {
            Comparisons = investigation.Comparisons.Select(item => item.Id != comparison.Id ? item : item with
            {
                Inspection = item.Inspection! with { Sections = new Dictionary<string, RacePassPair> { [section] = withheldPair } }
            }).ToArray()
        };
        var html = await Render(withheld);
        StringAssert.Contains(html, "title=\"Steering is not compared: reason.\">Not compared</span>");
    }

    [TestMethod]
    public async Task WheelAngleFallbackIsLabelledInTheSectionPanel()
    {
        var investigation = RaceInvestigationMapperTests.CurrentFixture();
        var comparison = investigation.Comparisons.First(item => item.Inspection?.Sections.Count > 0);
        var section = comparison.Inspection!.Sections.Keys.First();
        var pair = comparison.Inspection.Sections[section] with { Steering = RaceSteeringPair.Undeclared };
        var fallback = investigation with
        {
            Steering = AnalysisSteeringBasis.Undeclared,
            Comparisons = investigation.Comparisons.Select(item => item.Id != comparison.Id ? item : item with
            {
                Inspection = item.Inspection! with { Sections = new Dictionary<string, RacePassPair> { [section] = pair } }
            }).ToArray()
        };
        var html = await RaceInvestigationPerformanceTests.Render<RaceInvestigationView>(new()
        {
            [nameof(RaceInvestigationView.Investigation)] = fallback,
            [nameof(RaceInvestigationView.View)] = "sections",
            [nameof(RaceInvestigationView.ComparisonId)] = comparison.Id,
            [nameof(RaceInvestigationView.SectionId)] = section
        });
        StringAssert.Contains(html, ">Steering wheel angle<small>°, ratio not recorded</small>");
        StringAssert.Contains(html, "data-ri-read=\"steering\" data-side=\"a\" data-digits=\"1\"");
        Assert.DoesNotContain("Steering (road wheel)<small>", html);
    }

    [TestMethod]
    public void ReplaySteeringIsNamedForTheBasisAndPositiveIsLeft()
    {
        using var document = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "fixtures", "analyze-repair-heavy.json")));
        var archived = RuntimeMapper.ArchivedAnalysis(document.RootElement);
        const System.Reflection.BindingFlags flags = System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic;
        var type = typeof(RaceReplayWorkspace);
        (string Label, Func<double, string> Text) Replay(AnalysisSteeringBasis? steering)
        {
            var component = new RaceReplayWorkspace();
            type.GetProperty(nameof(RaceReplayWorkspace.Workspace))!.SetValue(component, archived with { Steering = steering });
            var chart = ((System.Collections.IEnumerable)type.GetProperty("ReplayCharts", flags)!.GetValue(component)!).Cast<object>().Last();
            var label = (string)chart.GetType().GetProperty("Label")!.GetValue(chart)!;
            return (label, radians => (string)type.GetMethod("SteeringText", flags)!.Invoke(component, [radians])!);
        }

        var road = Replay(new AnalysisSteeringBasis(AnalysisSteeringBasis.RoadWheelBasis, "recorded", "Steering (road wheel)", "Steering (road wheel)", 12, "12:1"));
        Assert.AreEqual("Steering (road wheel)", road.Label);
        // iRacing steering is positive to the left (IRP's left turns read positive).
        Assert.AreEqual("5.96° left", road.Text(5.96 * Math.PI / 180));
        Assert.AreEqual("0.50° right", road.Text(-.5 * Math.PI / 180));
        var wheel = Replay(null);
        Assert.AreEqual("Steering wheel angle — ratio not recorded", wheel.Label);
        Assert.AreEqual("71.5° left", wheel.Text(71.53 * Math.PI / 180));
    }

    [TestMethod]
    public void LiveRatioParsingFollowsTheBackendRules()
    {
        foreach (var (text, expected) in new (string?, double?)[]
                 {
                     ("12:1", 12), ("10:1", 10), ("16.5:1", 16.5), (" 12 : 1 ", 12), ("\"12:1\"", 12), ("14:1.0", 14), ("12", 12),
                     ("12:2", null), ("1:12", null), ("0:1", null), ("-12:1", null), ("twelve", null), ("", null), (null, null),
                     ("3:1", null), ("41:1", null), ("12:1 turns", null)
                 })
            Assert.AreEqual(expected, LiveSteering.ParseRatio(text), text ?? "null");
    }

    [TestMethod]
    public void LiveRatioComesFromTheCarSetupBlockOnly()
    {
        const string supra = "WeekendInfo:\n TrackName: irp\nDriverInfo:\n Drivers:\n - CarIdx: 0\n   SteeringRatio: 99:1\nCarSetup:\n UpdateCount: 10\n Chassis:\n  Front:\n   BallastForward: 914 mm\n   SteeringRatio: 12:1\n   SteeringOffset: +6 deg\n  Rear:\n   RearEndRatio: 5.5\n...\n";
        Assert.AreEqual(12d, LiveSteering.RatioFromSessionInfo(supra), "A SteeringRatio outside CarSetup is not the car's ratio.");
        Assert.AreEqual(10d, LiveSteering.RatioFromSessionInfo(supra.Replace("12:1", "10:1").Replace("\n", "\r\n")));
        Assert.IsNull(LiveSteering.RatioFromSessionInfo(supra.Replace("   SteeringRatio: 12:1\n", string.Empty)), "Absent: not guessed.");
        Assert.IsNull(LiveSteering.RatioFromSessionInfo(supra.Replace("12:1", "12:2")), "Unreadable: not guessed.");
        Assert.IsNull(LiveSteering.RatioFromSessionInfo(supra.Replace("  Rear:\n", "  Rear:\n   SteeringRatio: 10:1\n")), "Two ratios: not guessed.");
        Assert.IsNull(LiveSteering.RatioFromSessionInfo("WeekendInfo:\n TrackName: irp\n"));
        Assert.IsNull(LiveSteering.RatioFromSessionInfo(null));
    }

    [TestMethod]
    public void LiveRoadWheelSteeringIsTheWheelAngleOverTheRatio()
    {
        var wheel = 71.53 * Math.PI / 180;
        Assert.AreEqual(71.53 / 12, LiveSteering.RoadWheelRadians(wheel, 12)!.Value * 180 / Math.PI, 1e-9);
        Assert.IsNull(LiveSteering.RoadWheelRadians(wheel, null));
        Assert.IsNull(LiveSteering.RoadWheelRadians(null, 12));
        Assert.IsNull(LiveSteering.RoadWheelRadians(double.NaN, 12));
        // Near centre is 0.12 rad at the wheel, or 0.01 rad of road wheel when the ratio is known.
        Assert.IsTrue(LiveSteering.NearCenter(.11, null));
        Assert.IsFalse(LiveSteering.NearCenter(.13, null));
        Assert.IsTrue(LiveSteering.NearCenter(.11, 12));
        Assert.IsFalse(LiveSteering.NearCenter(.11, 10), "At 10:1 the same wheel angle is more steering applied.");
    }

    private static LiveMonitorState LiveState(double? wheel, double? ratio) => new(
        LiveTelemetryEngine.Disconnected() with
        {
            Connected = true,
            SteeringWheelAngleRadians = wheel,
            SteeringRoadWheelRadians = LiveSteering.RoadWheelRadians(wheel, ratio),
            SteeringRatio = ratio
        },
        new LiveMonitorLayout(), false, 0, 0, 0, DateTimeOffset.UtcNow);

    [TestMethod]
    public void LiveTilesShowRoadWheelSteeringOrSayTheRatioIsMissing()
    {
        Assert.AreEqual("Steering (road wheel)", LiveTelemetryCatalog.Get("steering").Name);
        Assert.AreEqual(LiveMonitorMetricSource.Calculated, LiveTelemetryCatalog.Get("steering").Source);
        Assert.AreEqual("Steering wheel angle", LiveTelemetryCatalog.Get("steering-wheel-angle").Name);

        var wheel = 71.53 * Math.PI / 180;
        var road = LiveTelemetryCatalog.Read("steering", LiveState(wheel, 12), includeTrend: false);
        Assert.IsTrue(road.Available);
        Assert.AreEqual(71.53 / 12, road.NumericValue!.Value, 1e-9);
        Assert.AreEqual("12:1 steering ratio", road.SecondaryValue);
        Assert.AreEqual(71.53, LiveTelemetryCatalog.Read("steering-wheel-angle", LiveState(wheel, 12), includeTrend: false).NumericValue!.Value, 1e-9);

        var missing = LiveTelemetryCatalog.Read("steering", LiveState(wheel, null), includeTrend: false);
        Assert.IsFalse(missing.Available);
        Assert.IsNull(missing.NumericValue, "Without a ratio the wheel angle is never shown as road-wheel steering.");
        Assert.AreEqual("Ratio not recorded", missing.DisplayValue);
        StringAssert.Contains(missing.AvailabilityMessage, "does not state a steering ratio");
        Assert.AreEqual(71.53, LiveTelemetryCatalog.Read("steering-wheel-angle", LiveState(wheel, null), includeTrend: false).NumericValue!.Value, 1e-9);
    }

    [TestMethod]
    public void LiveSteeringTraceIsScaledForRoadWheelDegreesAndLabelledInsideItsColumn()
    {
        var chart = File.ReadAllText(Path.Combine(TestRepositoryPaths.UiRoot, "wwwroot", "live-telemetry-chart.js"));
        var steering = Regex.Match(chart, "\\{ key: \"steering\"[^}]*\\}").Value;
        // The row steps by 30 deg of wheel at the reference ratio (2.5 deg of road wheel), as every
        // analysis steering floor converts; on the old +/-30 deg floor a 6 deg corner filled a fifth of it.
        var step = Regex.Match(steering, "rangeStep: ([0-9.]+)");
        Assert.IsTrue(step.Success, steering);
        Assert.AreEqual(30 / AnalysisSteeringBasis.ThresholdReferenceRatio,
            double.Parse(step.Groups[1].Value, CultureInfo.InvariantCulture), 1e-12);
        StringAssert.Contains(chart, "const step = panel.rangeStep || 30;");
        StringAssert.Contains(chart, "Math.max(step, Math.ceil(");
        Assert.DoesNotContain("Math.max(30,", chart, "No row keeps the steering-wheel range floor.");
        // The canvas label column is 74 px: "Steering" with "road wheel" on its own line, not one
        // 21-character label drawn across the start of the plot.
        StringAssert.Contains(steering, "label: \"Steering\"");
        StringAssert.Contains(steering, "detail: \"road wheel\"");
        StringAssert.Contains(chart, "if (panel.detail) context.fillText(panel.detail, 8, plot.top + 38);");
    }

    [TestMethod]
    public void SteeringQualifierIsNotCutOffInTraceRowsOrMeasurementCards()
    {
        // At 1280x720 "Steering (road wheel)" read "Steering (roa…" in the 88 px trace label and
        // "Average steering …" on the Details card, which looks like the old unqualified "Steering".
        var coach = File.ReadAllText(Path.Combine(TestRepositoryPaths.UiRoot, "wwwroot", "coach.css"));
        StringAssert.Contains(coach, ".trace-row-signal-name:only-child { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; white-space: normal; text-wrap: balance; }");
        var workspace = File.ReadAllText(Path.Combine(TestRepositoryPaths.UiRoot, "TelemetryWorkspace.razor"));
        StringAssert.Contains(workspace, "<strong>@foreach (var signal in panel.Signals) { <span class=\"trace-row-signal-name\">@signal.Name</span> }</strong>",
            "A one-signal row's name is the only child of its label, so it may take two lines.");

        var iteration = File.ReadAllText(Path.Combine(TestRepositoryPaths.UiRoot, "wwwroot", "iteration-ux.css"));
        var label = Regex.Match(iteration, @"\.race-technical-data \.technical-findings-list article > header > div span \{[^}]*\}").Value;
        StringAssert.Contains(label, "-webkit-line-clamp: 2;");
        StringAssert.Contains(label, "text-wrap: balance;");
        Assert.DoesNotContain("white-space: nowrap", label);
        var razor = File.ReadAllText(Path.Combine(TestRepositoryPaths.UiRoot, "RaceTechnicalData.razor"));
        StringAssert.Contains(razor, "<header><span class=\"technical-finding-group\">@MetricGroupLabel(metric)</span><div><span>@metric.Label</span><strong>@metric.Value</strong></div></header>");
    }

    [TestMethod]
    public void LiveTrendsUseRoadWheelSteering()
    {
        var point = new LiveTracePoint(DateTimeOffset.UtcNow, 1, .5, 100, 1, 0, .6, 4, 7000, null, null, null, null, null, null,
            SteeringRoadWheelRadians: .05);
        Assert.AreEqual(.05 * 180 / Math.PI, LiveTelemetryCatalog.TrendValue("steering", point)!.Value, 1e-9);
        Assert.AreEqual(.6 * 180 / Math.PI, LiveTelemetryCatalog.TrendValue("steering-wheel-angle", point)!.Value, 1e-9);
        Assert.IsNull(LiveTelemetryCatalog.TrendValue("steering", point with { SteeringRoadWheelRadians = null }));
    }

    [TestMethod]
    public void EngineCarriesTheLiveRatioIntoTheSnapshot()
    {
        var engine = new LiveTelemetryEngine();
        var snapshot = engine.Update(new LiveTelemetrySample
        {
            Connected = true, Timestamp = DateTimeOffset.UtcNow, Flag = "GREEN", Lap = 3,
            SteeringWheelAngleRadians = .6, SteeringRatio = 12
        }, true, false);
        Assert.AreEqual(.05, snapshot.SteeringRoadWheelRadians!.Value, 1e-12);
        Assert.AreEqual(12d, snapshot.SteeringRatio);
        var unknown = engine.Update(new LiveTelemetrySample
        {
            Connected = true, Timestamp = DateTimeOffset.UtcNow.AddSeconds(1), Flag = "GREEN", Lap = 3, SteeringWheelAngleRadians = .6
        }, true, false);
        Assert.IsNull(unknown.SteeringRoadWheelRadians);
        Assert.AreEqual(.6, unknown.SteeringWheelAngleRadians);
    }
}
