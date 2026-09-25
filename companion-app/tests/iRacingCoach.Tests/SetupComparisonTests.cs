using System.Collections.Concurrent;
using System.Text.Json;
using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;
using iRacingCoach.UI;

namespace iRacingCoach.Tests;

/// <summary>
/// The Setups page on the 9/24 IRP Supra test day: Compare showed "3 changed" for the
/// steering-ratio test (two corner weights a newton apart were counted) and "13 changed"
/// for the camber test, including "Front Arb · Link Slack −0 mm vs 0 mm"; the setups had
/// no link to their recordings, "supra IRP" found nothing, and the Compare list held the
/// same setup five times.
/// </summary>
[TestClass]
public sealed class SetupComparisonTests
{
    private const string Irp = "Lucas Oil Indianapolis Raceway Park · IRP";

    // The steering-ratio test against the Maconi baseline, trimmed from the recordings.
    private static readonly (string Key, string Value)[] Baseline =
    [
        ("Chassis.Front.CrossWeight", "50.9%"), ("Chassis.Front.SteeringRatio", "12:1"), ("Chassis.FrontArb.LinkSlack", "0 mm"),
        ("Chassis.LeftRear.CornerWeight", "4126 N"), ("Chassis.RightRear.CornerWeight", "3844 N"), ("Tires.LeftFront.ColdPressure", "96 kPa")
    ];
    private static readonly (string Key, string Value)[] Steer10 =
    [
        ("Chassis.Front.CrossWeight", "50.9%"), ("Chassis.Front.SteeringRatio", "10:1"), ("Chassis.FrontArb.LinkSlack", "-0 mm"),
        ("Chassis.LeftRear.CornerWeight", "4127 N"), ("Chassis.RightRear.CornerWeight", "3843 N"), ("Tires.LeftFront.ColdPressure", "96 kPa")
    ];

    // What compare_iracing_setups (setup_catalog.compare_displayed_setups) returns for them.
    private const string BackendResponse = """
        {"schema_version":1,"scheme":"recording","counts":{"changed":1,"unknown":0,"identical":3,"readouts_changed":2,"readouts_unknown":0,"readouts_identical":1,"readings_excluded":0},
         "rows":[{"key":"Chassis.Front.SteeringRatio","field":"chassis.front.steering_ratio","readout":false,"status":"changed","left":"10:1","right":"12:1"},
                 {"key":"Chassis.LeftRear.CornerWeight","field":"chassis.left_rear.corner_weight","readout":true,"status":"changed","left":"4127 N","right":"4126 N"},
                 {"key":"Chassis.RightRear.CornerWeight","field":"chassis.right_rear.corner_weight","readout":true,"status":"changed","left":"3843 N","right":"3844 N"},
                 {"key":"Chassis.Front.CrossWeight","field":"chassis.front.cross_weight","readout":true,"status":"identical","left":"50.9%","right":"50.9%"},
                 {"key":"Chassis.FrontArb.LinkSlack","field":"chassis.front_arb.link_slack","readout":false,"status":"identical","left":"-0 mm","right":"0 mm"},
                 {"key":"Tires.LeftFront.ColdPressure","field":"tires.left_front.cold_pressure","readout":false,"status":"identical","left":"96 kPa","right":"96 kPa"}],
         "readout_rule":"Computed garage readouts are listed apart.","equality_rule":"-0 equals 0."}
        """;

    private static LocalSetup Setup(string id, (string Key, string Value)[] values, string track = Irp, string recorded = "", string garage = "",
        string stoPath = "", string role = "Offline testing") =>
        new(id, id, "Toyota Supra Class B", track, role, stoPath.Length > 0 ? stoPath : $"{id}.json", id, "", "",
            values.Select(item => new SetupField(item.Key, item.Key.Split('.')[^1], string.Join(" · ", item.Key.Split('.')[..^1]), item.Value)).ToArray(),
            CarKey: "stockcars2 supra2019", ValuesSource: "recording", GarageFingerprint: garage, RecordedUtc: recorded);

    [TestMethod]
    public async Task TheBackendDecidesChangesAndReadoutsAreListedApartNeverCounted()
    {
        var backend = new CompareBackend(BackendResponse);
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(Path.GetTempPath(), "iracing-setup-compare", Guid.NewGuid().ToString("N"), "settings.json")));
        var steer = Setup("steer10", Steer10);
        var baseline = Setup("baseline", [.. Baseline, ("Tires.LeftFront.LastHotPressure", "115 kPa")]);

        await state.CompareSetupsAsync(steer, baseline);

        var comparison = state.SetupComparisonFor(steer, baseline);
        Assert.IsNotNull(comparison, state.SetupComparisonError);
        Assert.AreEqual((1, 2), (comparison.Changed, comparison.ReadoutsChanged), "One setting changed; two readouts moved a newton.");
        var request = backend.Requests.Single();
        Assert.AreEqual("recording", request.GetProperty("scheme").GetString());
        Assert.IsFalse(request.GetProperty("right").TryGetProperty("Tires.LeftFront.LastHotPressure", out _), "Run readings are never sent.");
        Assert.AreEqual("-0 mm", request.GetProperty("left").GetProperty("Chassis.FrontArb.LinkSlack").GetString(), "Values go as displayed; the backend reads -0 as 0.");

        var rows = SetupPage.Differences(steer, baseline, comparison).ToArray();
        Assert.AreEqual("Chassis · Front · SteeringRatio", rows.Single(row => row.Kind == SetupPage.RowKind.Changed).Label);
        CollectionAssert.AreEquivalent(new[] { "Chassis · LeftRear · CornerWeight", "Chassis · RightRear · CornerWeight" },
            rows.Where(row => row.Kind == SetupPage.RowKind.Readout).Select(row => row.Label).ToArray());
        Assert.AreEqual(SetupPage.RowKind.Identical, rows.Single(row => row.Label.EndsWith("LinkSlack", StringComparison.Ordinal)).Kind);

        // The same pair is not read again; changed values are.
        await state.CompareSetupsAsync(steer, baseline);
        Assert.HasCount(1, backend.Requests);
        var edited = steer with { Fields = [.. steer.Fields.Where(field => field.Key != "Chassis.Front.SteeringRatio"), new("Chassis.Front.SteeringRatio", "SteeringRatio", "Chassis · Front", "11:1")] };
        Assert.IsNull(state.SetupComparisonFor(edited, baseline));

        var page = File.ReadAllText(Path.Combine(TestRepositoryPaths.UiRoot, "SetupPage.razor"));
        StringAssert.Contains(page, "data-row=\"readout\"");
        StringAssert.Contains(page, "<optgroup label=\"@group.Label\">");
        Assert.DoesNotContain("Normalized(", page, "The page no longer compares strings itself.");
    }

    [TestMethod]
    public async Task AFailedComparisonIsStatedAndNotRetriedForTheSameValues()
    {
        var backend = new CompareBackend(null);
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(Path.GetTempPath(), "iracing-setup-compare", Guid.NewGuid().ToString("N"), "settings.json")));
        var left = Setup("a", Steer10);
        var right = Setup("b", Baseline);
        await state.CompareSetupsAsync(left, right);
        Assert.IsNull(state.SetupComparisonFor(left, right));
        Assert.IsTrue(state.SetupComparisonFailed(left, right));
        Assert.IsFalse(state.IsSetupComparisonPending(left, right));
        StringAssert.StartsWith(state.SetupComparisonError, "The local analysis service could not compare");
    }

    [TestMethod]
    public void CompareListHasOneEntryPerSetOfAdjustments_SameTrackFirstThenNewest()
    {
        var selected = Setup("claude IRP test v2 LFcamber", Steer10, recorded: "2026-09-25T05:32:47Z", garage: "g-v2", stoPath: "v2.sto");
        var library = new[]
        {
            selected,
            Setup("claude IRP test v2 LFcamber.sto", Steer10, recorded: "2026-09-25T05:32:47Z", garage: "g-v2"),
            Setup("NOAPS_MaconiSetupShop IRP 26S4 R", Baseline, recorded: "2026-09-25T05:19:25Z", garage: "g-base", stoPath: "maconi.sto"),
            Setup("NOAPS_MaconiSetupShop IRP 26S4 R.sto", Baseline, recorded: "2026-09-25T05:19:25Z", garage: "g-base"),
            Setup("NOAPS_MaconiSetupShop IRP 26S4 R.sto ", Baseline, recorded: "2026-09-25T03:19:28Z", garage: "g-base"),
            Setup("claude IRP test v3 steer10", Steer10, recorded: "2026-09-25T05:45:58Z", garage: "g-v3", stoPath: "v3.sto"),
            Setup("claude IRP test v3 steer10.sto", Steer10, recorded: "2026-09-25T05:45:58Z", garage: "g-v3"),
            Setup("NOAPS_MaconiSetupShop IRP 26S4 Q", Baseline, track: "IRP", garage: "", stoPath: "q.sto"),
            Setup("Bristol race", Baseline, track: "Bristol Motor Speedway", recorded: "2026-09-21T02:47:26Z", garage: "g-bristol")
        };

        var groups = SetupComparability.Groups(library, selected);
        CollectionAssert.AreEqual(new[] { SetupComparability.SameTrackGroup, SetupComparability.OtherTracksGroup }, groups.Select(group => group.Label).ToArray());
        CollectionAssert.AreEqual(new[] { "claude IRP test v3 steer10", "NOAPS_MaconiSetupShop IRP 26S4 R", "NOAPS_MaconiSetupShop IRP 26S4 Q" },
            groups[0].Setups.Select(setup => setup.Name).ToArray(),
            "A shop file's short track name is the same track; the named file stands for its recordings; newest first.");
        CollectionAssert.AreEqual(new[] { "Bristol race" }, groups[1].Setups.Select(setup => setup.Name).ToArray());
        CollectionAssert.AreEqual(groups.SelectMany(group => group.Setups).ToArray(), SetupPage.CompareCandidates(library, selected).ToArray());

        var excluded = SetupPage.CompareExclusions(library, selected).ToDictionary(item => item.Setup.Name.Trim() + "|" + item.Setup.RecordedUtc, item => item.Reason);
        Assert.AreEqual(SetupComparability.SameAdjustmentsAsSelectedReason, excluded["claude IRP test v2 LFcamber.sto|2026-09-25T05:32:47Z"]);
        Assert.AreEqual("Same garage adjustments as NOAPS_MaconiSetupShop IRP 26S4 R", excluded["NOAPS_MaconiSetupShop IRP 26S4 R.sto|2026-09-25T03:19:28Z"]);
        Assert.AreEqual("Same garage adjustments as claude IRP test v3 steer10", excluded["claude IRP test v3 steer10.sto|2026-09-25T05:45:58Z"]);
    }

    [TestMethod]
    [DataRow("supra IRP", true)]
    [DataRow("IRP supra", true)]
    [DataRow("irp SUPRA v2", true)]
    [DataRow("offline testing", true)]
    [DataRow("stockcars2", true)]
    [DataRow("supra bristol", false)]
    public void SearchMatchesEveryWordInAnyOrder(string query, bool matches)
    {
        var setup = Setup("claude IRP test v2 LFcamber", Steer10, stoPath: @"C:\setups\stockcars2 supra2019\claude IRP test v2 LFcamber.sto");
        Assert.AreEqual(matches, CompanionState.MatchesSetupSearch(setup, query));
        Assert.IsTrue(CompanionState.MatchesSetupSearch(setup, "  "));
    }

    [TestMethod]
    public void RecordedSetupsNameTheirSessionAndASetupFileTakesItsRecordingsTrack()
    {
        var archived = RuntimeMapper.ArchivedSetup(JsonSerializer.SerializeToElement(new
        {
            source = new { selection = new { sim_session_type = "Offline Testing" } },
            identity = new
            {
                setup_fingerprint = "c02d0f61d9698571", garage_setup_fingerprint = "47b8926d", setup_name = "claude IRP test v2 LFcamber.sto",
                car_name = "Toyota Supra Class B", car_path = "stockcars2 supra2019", track_name = "Lucas Oil Indianapolis Raceway Park",
                track_config = "IRP", event_type = "Test", session_start = "2026-09-25T05:32:47+00:00",
                setup = new { Chassis = new { LeftFront = new { Camber = "+5.2 deg" } } }
            }
        }), "v2.json")!;
        Assert.AreEqual("Offline testing", archived.Role, "The badge said 'Recorded race' for an Offline Testing recording.");
        Assert.AreEqual(Irp, archived.Track);
        Assert.AreEqual(("47b8926d", "2026-09-25T05:32:47+00:00"), (archived.GarageFingerprint, archived.RecordedUtc));
        Assert.AreEqual("Practice", RuntimeMapper.ArchivedSetup(JsonSerializer.SerializeToElement(new
        {
            identity = new { event_type = "Race", setup = new { Chassis = new { Front = new { CrossWeight = "50.9%" } } } }
        }), "p.json", "Practice")!.Role, "A caller's recorded session type wins over the weekend event type.");

        var catalog = JsonSerializer.SerializeToElement(new
        {
            entries = new object[]
            {
                new
                {
                    stem = "claude IRP test v2 LFcamber", car_folder = "stockcars2 supra2019", pair_status = "sto_only",
                    filename_identity = new { track_hint = (string?)null, season_key = (string?)null },
                    sources = new { sto = new[] { new { path = @"C:\setups\stockcars2 supra2019\claude IRP test v2 LFcamber.sto", sha256 = "x" } } },
                    recorded_setup = new
                    {
                        schema_version = 1, status = "linked",
                        uses = new[] { new { analysis_path = "v2.json", session_start = "2026-09-25T05:32:47+00:00", session_type = "Offline Testing", track_name = "Lucas Oil Indianapolis Raceway Park", track_config = "IRP" } },
                        values = new
                        {
                            source = "recording", analysis_path = "v2.json", session_start = "2026-09-25T05:32:47+00:00", session_type = "Offline Testing",
                            track_name = "Lucas Oil Indianapolis Raceway Park", track_config = "IRP", garage_fingerprint = "47b8926d",
                            setup = new { Chassis = new { LeftFront = new { Camber = "+5.2 deg" } } }, recordings_agree = true
                        }
                    },
                    values_status = new { available = true, source = "recording" }
                },
                new
                {
                    stem = "josh test", car_folder = "stockcars2 supra2019", pair_status = "sto_only",
                    filename_identity = new { track_hint = (string?)null },
                    sources = new { sto = new[] { new { path = @"C:\setups\stockcars2 supra2019\josh test.sto", sha256 = "y" } } },
                    recorded_setup = new { schema_version = 1, status = "none" },
                    values_status = new { available = false, reason = "Values unknown." }
                }
            }
        });
        var files = RuntimeMapper.Setups(catalog);
        var linked = files.Single(setup => setup.Name == "claude IRP test v2 LFcamber");
        Assert.AreEqual(Irp, linked.Track, "The track came from the file name: 'Claude IRP Test V2 Lfcamber'.");
        Assert.AreEqual(("47b8926d", "2026-09-25T05:32:47+00:00"), (linked.GarageFingerprint, linked.RecordedUtc));
        Assert.AreEqual(string.Empty, files.Single(setup => setup.Name == "josh test").Track, "No recording and no shop-style name: the track is unspecified.");
        Assert.IsTrue(SetupComparability.SameTrack(linked, archived));
    }

    [TestMethod]
    public void EvidenceIncludesSessionsAnalysedSinceTheRefreshOncePerRecording()
    {
        using var state = new CompanionState(new CompareBackend(null), new JsonSettingsStore(Path.Combine(Path.GetTempPath(), "iracing-setup-compare", Guid.NewGuid().ToString("N"), "settings.json")));
        var setup = Setup("claude IRP test v2 LFcamber.sto", Steer10) with { AnalysisPaths = ["cli-v2.json", "app-v2.json"], ValuesAnalysisPath = "app-v2.json" };
        // An offline test stint the app just analysed is a session, not a dashboard race.
        state.EventSessions.Add(new("local:v2", "Lucas Oil Indianapolis Raceway Park", "IRP", "Toyota Supra Class B", "Sep 25 · 12:32 AM", "Open", "Analyzed",
            "Offline Testing", false, true, 0, 0, AnalysisPath: "app-v2.json", StartTimeUtc: "2026-09-25T05:32:47Z", SessionType: "Offline Testing"));
        // The same recording analysed once more elsewhere, archived under another report.
        state.Races.Add(new("cli", "Lucas Oil Indianapolis Raceway Park", "IRP", "Toyota Supra Class B", "Sep 25 · 12:32 AM", "Open", "Analyzed",
            "Offline Testing", false, true, 0, 0, AnalysisPath: "cli-v2.json", StartTimeUtc: "2026-09-25T05:32:47+00:00", SessionType: "Offline Testing"));

        var evidence = state.SetupEvidenceRaces(setup);
        Assert.HasCount(1, evidence, "One recording, however many analyses of it exist.");
        Assert.AreEqual("local:v2", evidence[0].Id, "The analysis the setup's values came from represents it.");
    }

    private sealed class CompareBackend(string? response) : IBackendClient
    {
        private readonly ConcurrentQueue<JsonElement> _requests = new();
        public IReadOnlyList<JsonElement> Requests => _requests.ToArray();
        public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default) =>
            Task.FromResult(new BackendHealthResult(true, "test", "1", "test", 1, TimeSpan.Zero));
        public Task<JsonElement> CallToolAsync(BackendConfiguration configuration, string toolName, object arguments, CancellationToken cancellationToken = default)
        {
            if (toolName != "compare_iracing_setups") throw new InvalidOperationException($"Unexpected tool: {toolName}");
            _requests.Enqueue(JsonSerializer.SerializeToElement(arguments));
            if (response is null) throw new BackendDomainException("{\"error\":\"RuntimeError\"}");
            using var document = JsonDocument.Parse(response);
            return Task.FromResult(document.RootElement.Clone());
        }
    }
}
