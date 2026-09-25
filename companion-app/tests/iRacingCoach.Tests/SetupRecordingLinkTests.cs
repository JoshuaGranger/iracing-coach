using System.Globalization;
using System.Text.Json;
using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;
using iRacingCoach.UI;

namespace iRacingCoach.Tests;

/// <summary>
/// The Setups page on Joshua's real library: every .sto showed "0 values", Compare offered
/// only "No comparison", and the IRP setup his 9/23 practice and race loaded said no recording
/// used it. Values and links now come from the backend's recording attribution.
/// </summary>
[TestClass]
public sealed class SetupRecordingLinkTests
{
    private const string RaceStart = "2026-09-24T02:09:03+00:00";
    private static readonly string Root = Path.Combine(Path.GetTempPath(), "iracing-setup-links");
    private static readonly string RacePath = Path.Combine(Root, "reports", "subsession-88866363-2", "analysis.json");
    private static readonly string PracticePath = Path.Combine(Root, "reports", "subsession-88864603-0", "analysis.json");

    // Trimmed from the real IRP race IBT CarSetup; readings are what the backend strips.
    private static object Garage(string crossWeight = "50.9%") => new
    {
        Tires = new { LeftFront = new { ColdPressure = "96 kPa" }, RightFront = new { ColdPressure = "193 kPa" } },
        Chassis = new
        {
            Front = new { CrossWeight = crossWeight, FrontBrakeBias = "64.0%" },
            LeftFront = new { RideHeight = "108 mm", SpringPerchOffset = "-95 mm" },
            Rear = new { RearEndRatio = 5.57 }
        }
    };

    private static object RecordedCarSetup() => new
    {
        UpdateCount = 13,
        Tires = new
        {
            LeftFront = new { ColdPressure = "96 kPa", LastHotPressure = "128 kPa", LastTempsOMI = "61C, 52C, 45C", TreadRemaining = "99%, 100%, 100%" },
            RightFront = new { ColdPressure = "193 kPa", LastHotPressure = "211 kPa", LastTempsIMO = "85C, 81C, 78C", TreadRemaining = "98%, 98%, 98%" }
        },
        Chassis = new
        {
            Front = new { CrossWeight = "50.9%", FrontBrakeBias = "64.0%" },
            LeftFront = new { RideHeight = "108 mm", SpringPerchOffset = "-95 mm" },
            Rear = new { RearEndRatio = 5.57 }
        }
    };

    private static object Entry(string car, string stem, object recordedSetup, object valuesStatus, object? parsedHtml = null) => new
    {
        stem,
        car_folder = car,
        pair_status = "sto_only",
        filename_identity = new { track_hint = "IRP", season_key = "2026S4", role = "race" },
        sources = new { sto = new[] { new { path = Path.Combine(Root, "setups", car, stem + ".sto"), sha256 = "sto-" + car } } },
        parsed_html = parsedHtml,
        recorded_setup = recordedSetup,
        values_status = valuesStatus
    };

    private static JsonElement Catalog() => JsonSerializer.SerializeToElement(new
    {
        entries = new[]
        {
            Entry("stockcars2 supra2019", "NOAPS_MaconiSetupShop IRP 26S4 R", new
            {
                schema_version = 1, status = "linked",
                uses = new[]
                {
                    new { analysis_path = RacePath, session_start = RaceStart, session_type = "Race" },
                    new { analysis_path = PracticePath, session_start = "2026-09-24T01:45:56+00:00", session_type = "Practice" }
                },
                values = new { source = "recording", analysis_path = RacePath, session_start = RaceStart, session_type = "Race", setup = Garage(), field_count = 7, file_saved_after_recording = false, recordings_agree = true }
            }, new { available = true, source = "recording" }),
            Entry("stockcars2 supra2019", "NOAPS_MaconiSetupShop Bristol 26S4 R", new
            {
                schema_version = 1, status = "linked",
                uses = new[] { new { analysis_path = Path.Combine(Root, "reports", "bristol", "analysis.json"), session_start = "2026-09-21T02:47:26+00:00", session_type = "Race" } },
                values = new { source = "recording", session_start = "2026-09-21T02:47:26+00:00", session_type = "Race", setup = Garage("51.4%"), file_saved_after_recording = false, recordings_agree = true }
            }, new { available = true, source = "recording" }),
            Entry("stockcars2 supra2019", "NOAPS_MaconiSetupShop IRP 26S4 Q", new { schema_version = 1, status = "none", uses = Array.Empty<object>() },
                new { available = false, reason_code = "sto_not_readable", reason = "Values unknown: .sto files are encrypted. Analyze a recording that loaded this file unchanged, or export its setup sheet from iRacing." }),
            Entry("stockcars2 supra2019", "NOAPS_MaconiSetupShop Iowa 26S3 R", new { schema_version = 1, status = "none", uses = Array.Empty<object>() },
                new { available = true, source = "setup_export" },
                new { fields = new Dictionary<string, object>
                {
                    ["tires.left_front.cold_pressure"] = new { raw = "15.0 psi", unit = "kPa", label = "Cold pressure", section = "LEFT FRONT" },
                    ["chassis.front.ballast_forward"] = new { raw = "28.0\"", unit = "mm", label = "Ballast forward", section = "FRONT" },
                    ["chassis.rear.rear_end_ratio"] = new { raw = "4.57", unit = (string?)null, label = "Rear end ratio", section = "REAR" },
                    ["chassis.front.nose_weight"] = new { raw = "50.3", unit = "%", label = "Nose weight", section = "FRONT" }
                } }),
            Entry("stockcars2 camaro2019", "NOAPS_MaconiSetupShop IRP 26S4 R", new { schema_version = 1, status = "none", uses = Array.Empty<object>() },
                new { available = false, reason_code = "sto_not_readable", reason = "Values unknown." })
        }
    });

    private static LocalSetup RecordedRaceSetup() => RuntimeMapper.ArchivedSetup(JsonSerializer.SerializeToElement(new
    {
        analysis_id = "afd7b4c927c390ac2d5fedc2",
        identity = new
        {
            setup = RecordedCarSetup(), setup_fingerprint = "560bc40e77bc5a53", setup_name = "NOAPS_MaconiSetupShop IRP 26S4 R.sto",
            car_name = "Toyota Supra Class B", car_path = "stockcars2 supra2019", track_name = "Lucas Oil Indianapolis Raceway Park", track_config = "IRP"
        }
    }), RacePath)!;

    [TestMethod]
    public void SetupFileLoadedByAnalysedRecordings_ShowsThoseValuesAndLinksBothRecordings()
    {
        var setups = RuntimeMapper.Setups(Catalog());
        var irp = setups.Single(setup => setup.Name == "NOAPS_MaconiSetupShop IRP 26S4 R" && setup.CarKey == "stockcars2 supra2019");

        Assert.AreEqual("recording", irp.ValuesSource);
        Assert.HasCount(7, irp.Fields, "Garage values only; the run's tire readings are not setup values.");
        var date = DateTimeOffset.Parse(RaceStart, CultureInfo.InvariantCulture).ToLocalTime().ToString("MMM d, yyyy", CultureInfo.CurrentCulture);
        Assert.AreEqual($"From recording {date} · Race.", irp.ValuesNote);
        CollectionAssert.AreEqual(new[] { RacePath, PracticePath }, irp.AnalysisPaths!.ToArray());

        using var state = new CompanionState(new NoBackend(), new JsonSettingsStore(Path.Combine(Root, Guid.NewGuid().ToString("N"), "settings.json")));
        foreach (var setup in setups) state.Setups.Add(setup);
        state.Races.Add(new("race", "Lucas Oil Indianapolis Raceway Park", "IRP", "Toyota Supra Class B", "Sep 23", "Open", "Ready", "Race", false, true, 0, 0, AnalysisPath: RacePath));
        state.Races.Add(new("practice", "Lucas Oil Indianapolis Raceway Park", "IRP", "Toyota Supra Class B", "Sep 23", "Open", "Ready", "Practice", false, true, 0, 0, AnalysisPath: PracticePath));
        CollectionAssert.AreEquivalent(new[] { "race", "practice" }, state.SetupEvidenceRaces(irp).Select(race => race.Id).ToArray());
        var otherCar = setups.Single(setup => setup.Name == "NOAPS_MaconiSetupShop IRP 26S4 R" && setup.CarKey == "stockcars2 camaro2019");
        Assert.IsEmpty(state.SetupEvidenceRaces(otherCar), "The same file name for another car is a different setup.");
    }

    [TestMethod]
    public void SetupFileWithoutKnownValues_SaysWhyInsteadOfZeroValues()
    {
        var unknown = RuntimeMapper.Setups(Catalog()).Single(setup => setup.Name == "NOAPS_MaconiSetupShop IRP 26S4 Q");
        Assert.IsEmpty(unknown.Fields);
        Assert.AreEqual(string.Empty, unknown.ValuesSource);
        StringAssert.StartsWith(unknown.ValuesNote, "Values unknown: .sto files are encrypted.");
        Assert.IsEmpty(SetupPage.CompareCandidates(RuntimeMapper.Setups(Catalog()), unknown), "A setup without values cannot be compared.");

        var legacy = RuntimeMapper.Setups(JsonSerializer.SerializeToElement(new
        {
            entries = new[] { new { stem = "Old", car_folder = "stockcars2 supra2019", sources = new { sto = new[] { new { path = Path.Combine(Root, "Old.sto") } } } } }
        })).Single();
        StringAssert.StartsWith(legacy.ValuesNote, "Values unknown", "An older backend without attribution still says so plainly.");

        var exported = RuntimeMapper.Setups(Catalog()).Single(setup => setup.Name == "NOAPS_MaconiSetupShop Iowa 26S3 R");
        Assert.AreEqual("export", exported.ValuesSource);
        CollectionAssert.AreEqual(new[] { "15.0 psi", "28.0\"", "4.57", "50.3 %" }, exported.Fields.Select(field => field.Value).ToArray(),
            "A sheet value keeps its own unit; the canonical unit is added only to a bare number.");

        var page = File.ReadAllText(Path.Combine(TestRepositoryPaths.UiRoot, "SetupPage.razor"));
        Assert.DoesNotContain("@setup.Fields.Count values", page);
        Assert.DoesNotContain("This .sto has no readable export yet", page);
    }

    [TestMethod]
    public void CompareOffersEverySameCarSetupWithComparableValues_AcrossTracksAndRecordings()
    {
        var setups = RuntimeMapper.Setups(Catalog()).Append(RecordedRaceSetup()).ToArray();
        var irp = setups.Single(setup => setup.Name == "NOAPS_MaconiSetupShop IRP 26S4 R" && setup.CarKey == "stockcars2 supra2019");

        var candidates = SetupPage.CompareCandidates(setups, irp).Select(setup => setup.Name).ToArray();
        CollectionAssert.AreEquivalent(new[] { "NOAPS_MaconiSetupShop Bristol 26S4 R", "NOAPS_MaconiSetupShop IRP 26S4 R.sto" }, candidates,
            "Another track's recorded setup and the recording's own setup are both comparable.");
        CollectionAssert.AreEquivalent(new[]
            {
                $"NOAPS_MaconiSetupShop IRP 26S4 Q: {SetupComparability.UnknownValuesReason}",
                $"NOAPS_MaconiSetupShop Iowa 26S3 R: {SetupComparability.ExportSchemeReason}"
            },
            SetupPage.CompareExclusions(setups, irp).Select(item => $"{item.Setup.Name}: {item.Reason}").ToArray(),
            "Each excluded same-car setup is named with its reason; the other car's file of the same name is not this car's.");

        var bristol = setups.Single(setup => setup.Name == "NOAPS_MaconiSetupShop Bristol 26S4 R");
        var rows = SetupPage.Differences(irp, bristol).ToArray();
        Assert.HasCount(7, rows);
        Assert.AreEqual("Chassis · Front · Cross Weight", rows.Single(row => row.Kind == SetupPage.RowKind.Changed).Label);

        var recorded = setups.Single(setup => setup.Name == "NOAPS_MaconiSetupShop IRP 26S4 R.sto");
        var againstRecording = SetupPage.Differences(irp, recorded).ToArray();
        Assert.HasCount(7, againstRecording);
        Assert.IsTrue(againstRecording.All(row => row.Kind == SetupPage.RowKind.Identical),
            "The file's recorded values and the recording's own setup share every garage field; run readings are not compared.");
    }

    [TestMethod]
    [DataRow("stockcars2 camaro2019", "NASCAR Xfinity Chevrolet Camaro")]
    [DataRow("bmwm4gt3", "BMW M4 GT3")]
    public void CompareMatchesRecordingsAndSetupFilesByCarPath_NotByDisplayName(string carPath, string screenName)
    {
        // A linked .sto shows the folder's display name; its recording shows iRacing's
        // screen name. Only the car path says they are the same car.
        var catalog = JsonSerializer.SerializeToElement(new
        {
            entries = new[]
            {
                Entry(carPath, "Linked", new
                {
                    schema_version = 1, status = "linked",
                    uses = new[] { new { analysis_path = RacePath, session_start = RaceStart, session_type = "Race" } },
                    values = new { source = "recording", analysis_path = RacePath, session_start = RaceStart, session_type = "Race", setup = Garage(), recordings_agree = true }
                }, new { available = true, source = "recording" }),
                Entry(carPath, "Unknown", new { schema_version = 1, status = "none" }, new { available = false, reason = "Values unknown." }),
                Entry(carPath, "Sheet", new { schema_version = 1, status = "none" }, new { available = true, source = "setup_export" },
                    new { fields = new Dictionary<string, object> { ["chassis.rear.rear_end_ratio"] = new { raw = "4.57", label = "Rear end ratio", section = "REAR" } } })
            }
        });
        var recording = RuntimeMapper.ArchivedSetup(JsonSerializer.SerializeToElement(new
        {
            identity = new { setup = RecordedCarSetup(), setup_fingerprint = "recorded-" + carPath, setup_name = "Linked.sto", car_name = screenName, car_path = carPath }
        }), RacePath)!;
        var setups = RuntimeMapper.Setups(catalog).Append(recording).ToArray();
        var linked = setups.Single(setup => setup.Name == "Linked");
        Assert.AreNotEqual(linked.Car, recording.Car, "The display names differ; that must not matter.");
        Assert.AreEqual(carPath, recording.CarKey);

        CollectionAssert.AreEqual(new[] { recording.Id }, SetupPage.CompareCandidates(setups, linked).Select(setup => setup.Id).ToArray());
        CollectionAssert.AreEqual(new[] { linked.Id }, SetupPage.CompareCandidates(setups, recording).Select(setup => setup.Id).ToArray());
        CollectionAssert.AreEquivalent(new[] { $"Unknown: {SetupComparability.UnknownValuesReason}", $"Sheet: {SetupComparability.ExportSchemeReason}" },
            SetupPage.CompareExclusions(setups, recording).Select(item => $"{item.Setup.Name}: {item.Reason}").ToArray());
        var sheet = setups.Single(setup => setup.Name == "Sheet");
        CollectionAssert.AreEquivalent(new[] { $"Linked: {SetupComparability.RecordingSchemeReason}", "Unknown: " + SetupComparability.UnknownValuesReason, $"{recording.Name}: {SetupComparability.RecordingSchemeReason}" },
            SetupPage.CompareExclusions(setups, sheet).Select(item => $"{item.Setup.Name}: {item.Reason}").ToArray());

        // The capability gate offers comparison exactly when the Compare list has a candidate.
        foreach (var setup in setups)
            Assert.AreEqual(SetupPage.CompareCandidates(setups, setup).Count > 0, CapabilityRegistry.HasSetupComparison(setups, setup.Id), setup.Name);
        Assert.IsTrue(CapabilityRegistry.HasSetupComparison(setups));
        Assert.IsFalse(CapabilityRegistry.HasSetupComparison([sheet, recording]), "An exported sheet and a recording use different value schemes.");
        Assert.IsFalse(CapabilityRegistry.HasSetupComparison([sheet, recording], sheet.Id));
    }

    [TestMethod]
    public void TwoRecordingsOfTheSameSetup_DoNotReportTheirRunReadingsAsSetupChanges()
    {
        // Real 9/23 IRP practice and race: identical garage, different last-run tire readings.
        static LocalSetup Recording(string fingerprint, string hotPressure, string temps, string tread) => RuntimeMapper.ArchivedSetup(JsonSerializer.SerializeToElement(new
        {
            identity = new
            {
                setup_fingerprint = fingerprint, setup_name = "NOAPS_MaconiSetupShop IRP 26S4 R.sto", car_name = "Toyota Supra Class B",
                setup = new
                {
                    UpdateCount = fingerprint.Length,
                    Tires = new { LeftFront = new { ColdPressure = "96 kPa", LastHotPressure = hotPressure, LastTempsOMI = temps, TreadRemaining = tread } },
                    Chassis = new { Front = new { CrossWeight = "50.9%" } }
                }
            }
        }), fingerprint + ".json")!;
        var practice = Recording("ca84ebf3344710e6", "152 kPa", "42C, 42C, 42C", "100%, 100%, 100%");
        var race = Recording("560bc40e77bc5a53-race", "128 kPa", "61C, 52C, 45C", "99%, 100%, 100%");

        var rows = SetupPage.Differences(race, practice).ToArray();
        Assert.HasCount(2, rows);
        Assert.IsTrue(rows.All(row => row.Kind == SetupPage.RowKind.Identical));
        Assert.IsTrue(SetupReadings.IsRunReading("Tires.RightFront.LastTempsIMO"));
        Assert.IsTrue(SetupReadings.IsRunReading("UpdateCount"));
        Assert.IsFalse(SetupReadings.IsRunReading("Tires.LeftFront.ColdPressure"));
        Assert.IsFalse(SetupReadings.IsRunReading("tires.left_front.cold_pressure"));
    }

    [TestMethod]
    public void TuningALinkedSetupFileStartsFromTheRecordingsOwnSetup()
    {
        var recorded = RecordedRaceSetup();
        var setups = RuntimeMapper.Setups(Catalog()).Append(recorded).ToArray();
        var irp = setups.Single(setup => setup.Name == "NOAPS_MaconiSetupShop IRP 26S4 R" && setup.CarKey == "stockcars2 supra2019");
        Assert.AreEqual(recorded.Id, SetupPage.TuningSetup(setups, irp, RacePath).Id);
        Assert.AreEqual(recorded.Id, SetupPage.TuningSetup(setups, recorded, RacePath).Id);
        Assert.AreEqual(irp.Id, SetupPage.TuningSetup(setups, irp, Path.Combine(Root, "unknown.json")).Id);
    }

    [TestMethod]
    public void RunReadingsFromRecordingsAndExportedSheets_AreListedApartFromSetupValues()
    {
        // The recorded telemetry row for the 9/23 IRP race listed "14 values", half of
        // them last-run tire readings and a revision counter.
        var recorded = RecordedRaceSetup();
        CollectionAssert.AreEquivalent(new[]
        {
            "Tires.LeftFront.ColdPressure", "Tires.RightFront.ColdPressure", "Chassis.Front.CrossWeight", "Chassis.Front.FrontBrakeBias",
            "Chassis.LeftFront.RideHeight", "Chassis.LeftFront.SpringPerchOffset", "Chassis.Rear.RearEndRatio"
        }, recorded.Fields.Select(field => field.Key).ToArray(), "Setup values are the garage settings only.");
        CollectionAssert.AreEquivalent(new[]
        {
            "UpdateCount", "Tires.LeftFront.LastHotPressure", "Tires.LeftFront.LastTempsOMI", "Tires.LeftFront.TreadRemaining",
            "Tires.RightFront.LastHotPressure", "Tires.RightFront.LastTempsIMO", "Tires.RightFront.TreadRemaining"
        }, recorded.RunReadings!.Select(field => field.Key).ToArray());
        Assert.AreEqual("7 values", SetupPage.ValuesSummary(recorded));

        var sheet = RuntimeMapper.Setups(JsonSerializer.SerializeToElement(new
        {
            entries = new[]
            {
                Entry("stockcars2 supra2019", "Sheet", new { schema_version = 1, status = "none" }, new { available = true, source = "setup_export" },
                    new { fields = new Dictionary<string, object>
                    {
                        ["tires.left_front.cold_pressure"] = new { raw = "15.0 psi", label = "Cold pressure", section = "LEFT FRONT" },
                        ["tires.left_front.last_hot_pressure"] = new { raw = "17.0 psi", label = "Last hot pressure", section = "LEFT FRONT" },
                        ["tires.left_front.last_temps_omi"] = new { raw = "99F, 100F, 101F", label = "Last temps O M I", section = "LEFT FRONT" },
                        ["tires.left_front.tread_remaining"] = new { raw = "100%, 100%, 100%", label = "Tread remaining", section = "LEFT FRONT" },
                        ["chassis.rear.rear_end_ratio"] = new { raw = "4.57", label = "Rear end ratio", section = "REAR" }
                    } })
            }
        })).Single();
        CollectionAssert.AreEqual(new[] { "tires.left_front.cold_pressure", "chassis.rear.rear_end_ratio" }, sheet.Fields.Select(field => field.Key).ToArray());
        CollectionAssert.AreEqual(new[] { "Last hot pressure", "Last temps O M I", "Tread remaining" }, sheet.RunReadings!.Select(field => field.Label).ToArray());
        Assert.AreEqual("2 values", SetupPage.ValuesSummary(sheet));

        // A setup built elsewhere with readings still inside Fields is split the same way.
        var mixed = recorded with { Fields = [.. recorded.Fields, .. recorded.RunReadings!], RunReadings = null };
        Assert.HasCount(7, SetupReadings.Values(mixed));
        Assert.HasCount(7, SetupReadings.Readings(mixed));
        Assert.AreEqual("7 values", SetupPage.ValuesSummary(mixed));

        var page = File.ReadAllText(Path.Combine(TestRepositoryPaths.UiRoot, "SetupPage.razor"));
        StringAssert.Contains(page, "data-run-readings");
        StringAssert.Contains(page, "Run readings, not setup values");
        Assert.DoesNotContain("selected.Fields.GroupBy", page, "The setup value list must not include run readings.");
    }

    [TestMethod]
    public void TuningALinkedSetupFileUsesTheRecordingItsValuesCameFrom_NotTheFirstListed()
    {
        // The IRP file's values come from the 9/23 race; the practice was listed first,
        // so Tune started from the practice while the page showed the race's values.
        var recordedRace = RecordedRaceSetup();
        var setups = RuntimeMapper.Setups(Catalog()).Append(recordedRace).ToArray();
        var irp = setups.Single(setup => setup.Name == "NOAPS_MaconiSetupShop IRP 26S4 R" && setup.CarKey == "stockcars2 supra2019");
        Assert.AreEqual(RacePath, irp.ValuesAnalysisPath);
        Assert.AreEqual(RacePath, recordedRace.ValuesAnalysisPath);

        using var state = new CompanionState(new NoBackend(), new JsonSettingsStore(Path.Combine(Root, Guid.NewGuid().ToString("N"), "settings.json")));
        foreach (var setup in setups) state.Setups.Add(setup);
        state.Races.Add(new("practice", "Lucas Oil Indianapolis Raceway Park", "IRP", "Toyota Supra Class B", "Sep 23", "Open", "Ready", "Practice", false, true, 0, 0,
            AnalysisPath: PracticePath, StartTimeUtc: "2026-09-24T01:45:56Z", SessionType: "Practice"));
        state.Races.Add(new("race", "Lucas Oil Indianapolis Raceway Park", "IRP", "Toyota Supra Class B", "Sep 23", "Open", "Ready", "Race", false, true, 0, 0,
            AnalysisPath: RacePath, StartTimeUtc: RaceStart));

        var race = state.SetupTuningRace(irp);
        Assert.AreEqual("race", race?.Id, "Tuning starts from the recording whose values the file shows.");
        Assert.AreEqual(recordedRace.Id, SetupPage.TuningSetup(setups, irp, race!.AnalysisPath).Id);
        Assert.AreEqual("race", state.SetupTuningRace(recordedRace)?.Id);

        // Without a values recording (an exported sheet), the newest linked recording leads.
        var exported = irp with { ValuesSource = "export", ValuesAnalysisPath = "" };
        Assert.AreEqual("race", state.SetupTuningRace(exported)?.Id);

        var page = File.ReadAllText(Path.Combine(TestRepositoryPaths.UiRoot, "SetupPage.razor"));
        StringAssert.Contains(page, "State.SetupTuningRace(setup)");
        Assert.DoesNotContain("SetupEvidenceRaces(setup).FirstOrDefault()", page);
    }

    private sealed class NoBackend : IBackendClient
    {
        public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default) =>
            Task.FromResult(new BackendHealthResult(true, "test", "1", "test", 1, TimeSpan.Zero));
        public Task<JsonElement> CallToolAsync(BackendConfiguration configuration, string toolName, object arguments, CancellationToken cancellationToken = default) =>
            throw new InvalidOperationException($"Unexpected tool: {toolName}");
    }
}
