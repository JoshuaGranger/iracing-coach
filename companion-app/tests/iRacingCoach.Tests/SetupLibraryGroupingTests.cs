using System.Text.Json;
using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;
using iRacingCoach.UI;

namespace iRacingCoach.Tests;

/// <summary>
/// Joshua's IRP Supra test day: the Setups library listed twelve "Recorded telemetry"
/// entries beside the six .sto files they loaded. A recording that loaded a file unchanged
/// and drove the file's own garage adjustments (the backend's verdict) is now listed as that
/// file; the rest stay, and one with other adjustments says it differs from the file.
/// </summary>
[TestClass]
public sealed class SetupLibraryGroupingTests
{
    private const string Car = "stockcars2 supra2019";
    private static readonly string Root = Path.Combine(Path.GetTempPath(), "iracing-setup-library");
    private static string Report(string name) => Path.Combine(Root, "reports", name, "analysis.json");

    private static readonly string V2First = Report("offline-v2-1");
    private static readonly string V2Second = Report("offline-v2-2");
    private static readonly string V4Newest = Report("offline-v4-2");
    private static readonly string V4Older = Report("offline-v4-1");
    private static readonly string SheetRun = Report("offline-sheet");
    private static readonly string Unsaved = Report("offline-unsaved");

    private static object Garage(string perch = "-95 mm") => new
    {
        Tires = new { LeftFront = new { ColdPressure = "96 kPa" } },
        Chassis = new { Front = new { CrossWeight = "50.9%" }, LeftFront = new { SpringPerchOffset = perch } }
    };

    private static object Use(string path, string start, bool? match, string fingerprint) => new
    {
        analysis_path = path, session_start = start, session_type = "Offline Testing",
        track_name = "Lucas Oil Indianapolis Raceway Park", track_config = "IRP",
        garage_fingerprint = match == false ? "0ther0ther0ther0" : fingerprint, values_match = match
    };

    private static object File(string stem, string fingerprint, object[] uses, object? parsedHtml = null) => new
    {
        stem,
        car_folder = Car,
        pair_status = "sto_only",
        filename_identity = new { season_key = "2026S4" },
        sources = new { sto = new[] { new { path = Path.Combine(Root, "setups", Car, stem + ".sto"), sha256 = "sto-" + stem } } },
        parsed_html = parsedHtml,
        recorded_setup = new
        {
            schema_version = 1, status = uses.Length > 0 ? "linked" : "none", uses,
            values = uses.Length > 0 ? new
            {
                source = "recording", analysis_path = JsonSerializer.SerializeToElement(uses[0]).GetProperty("analysis_path").GetString(),
                session_start = "2026-09-25T05:00:00+00:00", session_type = "Offline Testing", setup = Garage(),
                garage_fingerprint = fingerprint, recordings_agree = true, file_saved_after_recording = false
            } : null
        },
        values_status = new { available = true, source = parsedHtml is null ? "recording" : "setup_export" }
    };

    private static JsonElement Catalog() => JsonSerializer.SerializeToElement(new
    {
        entries = new[]
        {
            File("claude IRP test v2 LFcamber", "v2v2v2v2v2v2v2v2", [Use(V2Second, "2026-09-25T05:00:00+00:00", true, "v2v2v2v2v2v2v2v2"), Use(V2First, "2026-09-25T04:00:00+00:00", true, "v2v2v2v2v2v2v2v2")]),
            File("claude IRP test v4 wedge", "v4v4v4v4v4v4v4v4", [Use(V4Newest, "2026-09-25T03:00:00+00:00", true, "v4v4v4v4v4v4v4v4"), Use(V4Older, "2026-09-25T02:00:00+00:00", false, "v4v4v4v4v4v4v4v4")]),
            File("NOAPS_MaconiSetupShop IRP 26S4 R", "baselinebaseline", [Use(SheetRun, "2026-09-25T01:00:00+00:00", true, "baselinebaseline")],
                new { fields = new Dictionary<string, object> { ["chassis.rear.rear_end_ratio"] = new { raw = "5.57", label = "Rear end ratio", section = "REAR" } } })
        }
    });

    private static LocalSetup Recorded(string stem, string analysisPath, string fingerprint, string perch = "-95 mm", string hot = "128 kPa") => RuntimeMapper.ArchivedSetup(JsonSerializer.SerializeToElement(new
    {
        identity = new
        {
            // The whole-sheet fingerprint differs per run (tire readings); the adjustments' may be stale.
            setup_fingerprint = "whole-" + Path.GetFileName(Path.GetDirectoryName(analysisPath)), garage_setup_fingerprint = fingerprint,
            setup_name = stem + ".sto", car_name = "Toyota Supra Class B", car_path = Car,
            track_name = "Lucas Oil Indianapolis Raceway Park", track_config = "IRP", sim_session_type = "Offline Testing",
            session_start = "2026-09-25T05:00:00+00:00",
            setup = new
            {
                Tires = new { LeftFront = new { ColdPressure = "96 kPa", LastHotPressure = hot } },
                Chassis = new { Front = new { CrossWeight = "50.9%" }, LeftFront = new { SpringPerchOffset = perch } }
            }
        }
    }), analysisPath)!;

    private static List<LocalSetup> Library() =>
    [
        .. RuntimeMapper.Setups(Catalog()),
        // Also analysed under an earlier profile whose report the archive index no longer lists.
        Recorded("claude IRP test v2 LFcamber", V2First, "v2v2v2v2v2v2v2v2", hot: "131 kPa") with { AnalysisPaths = [V2First, Report("offline-v2-1-earlier")] },
        Recorded("claude IRP test v2 LFcamber", V2Second, "v2v2v2v2v2v2v2v2", hot: "135 kPa"),
        Recorded("claude IRP test v4 wedge", V4Newest, "v4v4v4v4v4v4v4v4"),
        Recorded("claude IRP test v4 wedge", V4Older, "0ther0ther0ther0", perch: "-92 mm"),
        Recorded("NOAPS_MaconiSetupShop IRP 26S4 R", SheetRun, "baselinebaseline"),
        // Loaded with unsaved garage changes: the backend never links it to the file.
        Recorded("claude IRP test v3 steering", Unsaved, "unsavedunsaved00", perch: "-90 mm")
    ];

    private static CompanionState State(IEnumerable<LocalSetup> setups)
    {
        var state = new CompanionState(new NoBackend(), new JsonSettingsStore(Path.Combine(Root, Guid.NewGuid().ToString("N"), "settings.json")));
        foreach (var setup in setups) state.Setups.Add(setup);
        foreach (var (id, path) in new[] { ("v2-1", V2First), ("v2-2", V2Second), ("v4-2", V4Newest), ("v4-1", V4Older), ("sheet", SheetRun), ("unsaved", Unsaved) })
            state.Races.Add(new(id, "Lucas Oil Indianapolis Raceway Park", "IRP", "Toyota Supra Class B", "Sep 25", "Open", "Ready", "Offline Testing", false, true, 0, 0, AnalysisPath: path));
        return state;
    }

    [TestMethod]
    public void MapperReadsTheBackendVerdictForEachLinkedRecording()
    {
        var files = RuntimeMapper.Setups(Catalog());
        var v4 = files.Single(setup => setup.Name == "claude IRP test v4 wedge");
        CollectionAssert.AreEqual(new[] { V4Newest }, v4.MatchingAnalysisPaths!.ToArray());
        CollectionAssert.AreEqual(new[] { V4Older }, v4.DifferingAnalysisPaths!.ToArray());
        var sheet = files.Single(setup => setup.Name == "NOAPS_MaconiSetupShop IRP 26S4 R");
        Assert.AreEqual("export", sheet.ValuesSource);
        Assert.IsEmpty(sheet.MatchingAnalysisPaths!, "A sheet's values are in its own names and units; no recording is matched to them.");

        var unknown = RuntimeMapper.Setups(JsonSerializer.SerializeToElement(new
        {
            entries = new[] { File("claude IRP test v5", "v5v5v5v5v5v5v5v5", [Use(V2First, "2026-09-25T05:00:00+00:00", null, "v5v5v5v5v5v5v5v5")]) }
        })).Single();
        Assert.IsEmpty(unknown.MatchingAnalysisPaths!, "No verdict is neither a match nor a difference.");
        Assert.IsEmpty(unknown.DifferingAnalysisPaths!);
    }

    [TestMethod]
    public void RecordingsWithTheFilesOwnAdjustments_AreListedOnceAsTheFile()
    {
        using var state = State(Library());
        var library = state.LibrarySetups;
        var v2 = library.Single(setup => setup.Name == "claude IRP test v2 LFcamber");
        Assert.AreEqual(".sto", Path.GetExtension(v2.StoPath), "The file is the entry the driver loads.");
        Assert.HasCount(2, v2.GroupedSetupIds!);
        Assert.HasCount(2, state.SetupEvidenceRaces(v2), "The file carries both linked recordings.");
        Assert.AreEqual("recording", v2.ValuesSource, "…and the recorded values.");
        Assert.IsFalse(library.Any(setup => setup.Name == "claude IRP test v2 LFcamber.sto"), "No Recorded telemetry entry for the same setup.");
        Assert.HasCount(9, state.Setups, "Nothing is removed from the setups tuning starts from.");

        // A recording tuned from (or selected elsewhere) selects its file in the library.
        var recordedId = state.Setups.Single(setup => setup.StoPath == V2First).Id;
        Assert.AreEqual(v2.Id, state.LibrarySetupId(recordedId));
        Assert.AreEqual(v2.Id, state.LibrarySetupId(v2.Id));
    }

    [TestMethod]
    public void RecordingWithOtherAdjustmentsThanItsFile_StaysAndSaysItDiffers()
    {
        using var state = State(Library());
        var library = state.LibrarySetups;
        Assert.HasCount(1, library.Single(setup => setup.Name == "claude IRP test v4 wedge").GroupedSetupIds!);
        var older = library.Single(setup => setup.AnalysisPaths!.Contains(V4Older) && setup.StoPath == V4Older);
        Assert.AreEqual("claude IRP test v4 wedge", older.DiffersFromFile);
        Assert.IsFalse(library.Any(setup => setup.StoPath == V4Newest), "The matching v4 recording is the file.");

        var page = System.IO.File.ReadAllText(Path.Combine(TestRepositoryPaths.UiRoot, "SetupPage.razor"));
        StringAssert.Contains(page, "differs from the file");
        StringAssert.Contains(page, "data-differs-from-file");
    }

    [TestMethod]
    public void RecordedOnlySetups_AndRecordingsOfASheetBackedFile_Stay()
    {
        using var state = State(Library());
        var library = state.LibrarySetups;
        var unsaved = library.Single(setup => setup.StoPath == Unsaved);
        Assert.AreEqual(string.Empty, unsaved.DiffersFromFile, "No linked file, so no claim about one.");
        var sheetRun = library.Single(setup => setup.StoPath == SheetRun);
        Assert.AreEqual(string.Empty, sheetRun.DiffersFromFile, "The file shows its sheet's values; the recording is not matched to them.");
        CollectionAssert.AreEquivalent(new[]
            {
                "claude IRP test v2 LFcamber", "claude IRP test v4 wedge", "NOAPS_MaconiSetupShop IRP 26S4 R",
                "claude IRP test v4 wedge.sto", "NOAPS_MaconiSetupShop IRP 26S4 R.sto", "claude IRP test v3 steering.sto"
            },
            library.Select(setup => setup.Name).ToArray());
    }

    [TestMethod]
    public void CompareCandidatesAndSearch_FollowTheGroupedLibrary()
    {
        using var state = State(Library());
        var library = state.LibrarySetups;
        var hidden = state.Setups.Select(setup => setup.Id).Except(library.Select(setup => setup.Id)).ToHashSet();
        Assert.HasCount(3, hidden);
        foreach (var setup in library)
        {
            Assert.IsFalse(SetupPage.CompareCandidates(library, setup).Any(candidate => hidden.Contains(candidate.Id)), setup.Name);
            Assert.IsFalse(SetupPage.CompareExclusions(library, setup).Any(item => hidden.Contains(item.Setup.Id)), setup.Name);
        }
        var v2 = library.Single(setup => setup.Name == "claude IRP test v2 LFcamber");
        var older = library.Single(setup => setup.StoPath == V4Older);
        CollectionAssert.Contains(SetupPage.CompareCandidates(library, v2).Select(setup => setup.Id).ToArray(), older.Id,
            "A recording that differs from its file is its own setup to compare.");

        state.SetupSearch = "supra IRP";
        CollectionAssert.AreEquivalent(library.Select(setup => setup.Id).ToArray(), state.FilteredSetups.Select(setup => setup.Id).ToArray(),
            "Search lists library entries, never a grouped recording.");
        state.SetupSearch = "Toyota v2";
        CollectionAssert.AreEqual(new[] { v2.Id }, state.FilteredSetups.Select(setup => setup.Id).ToArray(),
            "A file matches the words of the recordings it stands for.");
        state.SetupSearch = "offline testing";
        Assert.IsTrue(state.FilteredSetups.Any(setup => setup.Id == v2.Id));
        Assert.IsFalse(state.FilteredSetups.Any(setup => hidden.Contains(setup.Id)));

        var page = System.IO.File.ReadAllText(Path.Combine(TestRepositoryPaths.UiRoot, "SetupPage.razor"));
        Assert.DoesNotContain("CompareGroups(State.Setups", page);
        Assert.DoesNotContain("CompareExclusions(State.Setups", page);
        Assert.DoesNotContain("CompareCandidates(State.Setups", page);
    }

    private sealed class NoBackend : IBackendClient
    {
        public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default) =>
            Task.FromResult(new BackendHealthResult(true, "test", "1", "test", 1, TimeSpan.Zero));
        public Task<JsonElement> CallToolAsync(BackendConfiguration configuration, string toolName, object arguments, CancellationToken cancellationToken = default) =>
            throw new InvalidOperationException($"Unexpected tool: {toolName}");
    }
}
