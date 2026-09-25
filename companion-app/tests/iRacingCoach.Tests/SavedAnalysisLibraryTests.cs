using System.Text.Json;
using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

// Saved analyses from older analyzers must stay in the library and setup catalogs.
// Truly unreadable ones are reported once, quietly, and are not reread every launch.
[TestClass]
public sealed class SavedAnalysisLibraryTests
{
    private const string GenericFailureToast = "Something went wrong, but the app is still running.";

    [TestMethod]
    public async Task SavedAnalysisWithBareInfinityIsListedInLibraryAndSetups()
    {
        using var library = new Library();
        var report = library.AddReport("2026-09-21t00-07-38-00-00",
            File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "fixtures", "archived-analysis-nonfinite-v16.json")));
        var original = File.ReadAllBytes(report);

        using (var state = await library.LaunchAsync())
        {
            Assert.IsTrue(state.Races.Any(race => race.Track == "World Wide Technology Raceway" && race.AnalysisPath == report),
                "An analysis with one unknown strategy time must not vanish from the library.");
            Assert.IsTrue(state.Setups.Any(setup => setup.Id == "nonfinite-regression-setup"),
                "The archived setup catalog reads the same report.");
            Assert.AreNotEqual(GenericFailureToast, state.Toast);
            Assert.IsNull(state.Toast);
        }
        Assert.IsEmpty(library.ErrorLogLines());
        CollectionAssert.AreEqual(original, File.ReadAllBytes(report), "The user's archive file must not be rewritten.");
    }

    [TestMethod]
    public async Task UnreadableSavedAnalysisIsLoggedOnceAcrossLaunchesWithoutGenericToast()
    {
        using var library = new Library();
        var report = library.AddReport("broken", "{\"identity\":{\"track_name\":\"Truncated");

        using (var first = await library.LaunchAsync())
        {
            Assert.AreNotEqual(GenericFailureToast, first.Toast, "An unreadable saved analysis is not an application failure.");
            Assert.IsFalse(first.Races.Any(race => race.AnalysisPath == report));
        }
        var logged = library.ErrorLogLines();
        Assert.HasCount(1, logged, "The race and setup catalogs share one read and one log entry.");
        StringAssert.Contains(logged[0], "broken");

        // The next launch must not reopen the unchanged file (it is locked here) or log it again.
        using (new FileStream(report, FileMode.Open, FileAccess.Read, FileShare.None))
        using (var second = await library.LaunchAsync())
            Assert.AreNotEqual(GenericFailureToast, second.Toast);
        Assert.HasCount(1, library.ErrorLogLines());
    }

    [TestMethod]
    public async Task UnreadableSavedAnalysesShowOneSpecificNoticeAndDismissalIsRemembered()
    {
        using var library = new Library();
        library.AddReport("first", "{broken");
        var repairable = library.AddReport("second", "{\"identity\":{\"track_name\":\"Truncated");

        using (var first = await library.LaunchAsync())
        {
            Assert.AreEqual("2 saved analyses could not be read", first.UnreadableAnalysisNotice);
            Assert.IsNull(first.Toast);
            first.DismissUnreadableAnalysisNotice();
            Assert.IsNull(first.UnreadableAnalysisNotice);
        }
        Assert.IsTrue(File.Exists(Path.Combine(library.Settings.LocalStateRoot, "catalog", "unreadable-analyses.json")),
            "Unreadable revisions are remembered in app-owned local state.");
        Assert.IsFalse(Directory.EnumerateFiles(library.Settings.ArchiveRoot, "unreadable-analyses.json", SearchOption.AllDirectories).Any(),
            "Nothing about unreadable reports is written into the archive.");

        using (var second = await library.LaunchAsync())
            Assert.IsNull(second.UnreadableAnalysisNotice, "A dismissed notice stays dismissed for the same files.");

        library.AddReport("third", "[broken");
        File.WriteAllText(repairable, "{\"identity\":{\"track_name\":\"Repaired Speedway\",\"event_type\":\"Race\"},\"race_summary\":{\"recorded_laps\":12}}");
        using (var third = await library.LaunchAsync())
        {
            Assert.AreEqual("1 saved analysis could not be read", third.UnreadableAnalysisNotice);
            Assert.IsTrue(third.Races.Any(race => race.Track == "Repaired Speedway"));
        }
    }

    private sealed class Library : IDisposable
    {
        private readonly string _root = Path.Combine(Path.GetTempPath(), "iracing-coach-saved-library", Guid.NewGuid().ToString("N"));

        public Library()
        {
            Paths = new IsolatedCompanionPathProvider(_root);
            Settings = new CompanionSettings(Paths) { FirstRunComplete = true };
            Directory.CreateDirectory(Settings.IRacingRoot);
            // Keep startup from adopting this machine's real iRacing installation.
            Directory.CreateDirectory(Settings.IRacingInstallRoot);
        }

        public IsolatedCompanionPathProvider Paths { get; }
        public CompanionSettings Settings { get; }
        private string Reports => Path.Combine(Settings.ArchiveRoot, "reports", "season-1", "subsession-1000001-2");

        public string AddReport(string name, string contents)
        {
            var path = Path.Combine(Reports, name, "analysis.json");
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            File.WriteAllText(path, contents);
            return path;
        }

        public async Task<CompanionState> LaunchAsync()
        {
            var state = new CompanionState(new LibraryBackend(), new FixedSettingsStore(Settings), new DisconnectedLiveTelemetrySource(),
                new DisabledCoachEngineSupervisor(), new NoCredentials(Path.Combine(_root, "credentials")), new ReadyArchive(), Paths,
                allowExternalHostActions: false);
            await state.InitializeAsync();
            var until = DateTime.UtcNow + TimeSpan.FromSeconds(10);
            while ((state.IsRefreshing || !state.HomeDataReady) && DateTime.UtcNow < until) await Task.Delay(10);
            Assert.IsFalse(state.IsRefreshing);
            return state;
        }

        public string[] ErrorLogLines()
        {
            var log = Path.Combine(Settings.LogsRoot, "app-errors.jsonl");
            return File.Exists(log) ? File.ReadAllLines(log).Where(line => line.Length > 0).ToArray() : [];
        }

        public void Dispose()
        {
            try { Directory.Delete(_root, recursive: true); }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
        }
    }

    private sealed class FixedSettingsStore(CompanionSettings settings) : ISettingsStore
    {
        public CompanionSettings Load() => settings;
        public void Save(CompanionSettings value) { }
    }

    private sealed class NoCredentials(string path) : IGarage61CredentialStore
    {
        public bool IsConfigured => false;
        public string CredentialPath => path;
        public void Store(string token) { }
        public void Remove() { }
    }

    private sealed class ReadyArchive : IDurableArchiveService
    {
        public ArchiveStatus Initialize(string root, string appVersion, string backendVersion = "unknown") =>
            new(true, null, 1, "synthetic", root, new(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0), null, "Ready");
        public BackupPreparationResult PrepareForCopy(string root, string appVersion, string backendVersion, IReadOnlyList<string>? blockingActivity = null) =>
            new(true, root, DateTimeOffset.UtcNow, 0, 0, "synthetic", 0, [], "Ready");
        public void MarkActive(string root) { }
    }

    private sealed class LibraryBackend : IBackendClient
    {
        public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default) =>
            Task.FromResult(new BackendHealthResult(true, "test", "test", "test", 1, TimeSpan.Zero));

        public Task<JsonElement> CallToolAsync(BackendConfiguration configuration, string toolName, object arguments, CancellationToken cancellationToken = default) =>
            Task.FromResult(JsonSerializer.SerializeToElement(toolName switch
            {
                "iracing_companion_dashboard" => (object)new { ok = true, races = Array.Empty<object>() },
                "discover_iracing_sessions" => new { sessions = Array.Empty<object>() },
                "catalog_iracing_setups" => new { ok = true, entries = Array.Empty<object>() },
                "garage61_auth_status" => new { ok = false, configured = false, status = "not_configured" },
                "list_driving_trials" => new { ok = true, trials = Array.Empty<object>() },
                _ => new { ok = true }
            }));
    }
}
