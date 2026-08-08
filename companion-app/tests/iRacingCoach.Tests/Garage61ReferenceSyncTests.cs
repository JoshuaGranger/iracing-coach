using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class Garage61ReferenceSyncTests
{
    [TestMethod]
    public async Task ExplicitReferenceSearch_DeduplicatesUpdatesWorkspaceAndInvalidatesUiCache()
    {
        var root = Path.Combine(Path.GetTempPath(), $"iracing-coach-g61-{Guid.NewGuid():N}");
        Directory.CreateDirectory(root);
        try
        {
            var analysisPath = Path.Combine(root, "data", "reports", "race-reference.json");
            Directory.CreateDirectory(Path.GetDirectoryName(analysisPath)!);
            await File.WriteAllTextAsync(analysisPath, "{}");
            var settings = new CompanionSettings
            {
                CoachHome = root,
                IRacingRoot = root,
                IRacingInstallRoot = root,
                FirstRunComplete = true,
                LocalStateRootOverride = Path.Combine(root, "local")
            };
            var backend = new ReferenceBackend(analysisPath);
            using var state = new CompanionState(backend, new MemorySettingsStore(settings));
            await state.InitializeAsync();
            var race = new RecentRace(
                "race-1", "Recorded Track", "Oval", "Recorded Car", "Today", "Fixed", "Recorded", "20 laps",
                false, false, 4, 2, AnalysisPath: analysisPath, Selector: "subsession:100:1");
            state.Races.Add(race);
            state.EventSessions.Add(race);
            await state.AnalyzeRaceAsync(race);

            Assert.IsNotNull(state.CurrentAnalysis, $"Analysis did not open: {state.AnalysisMessage}; {string.Join(" | ", state.Jobs.Select(job => $"{job.Status}: {job.Stage}"))}");
            Assert.IsTrue(state.Garage61ReferenceActionVisible);
            var cacheDirectory = Path.Combine(root, "data", "ui-analysis-cache");
            Directory.CreateDirectory(cacheDirectory);
            var cacheKey = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes($"race|{race.EffectiveSelector}"))).ToLowerInvariant();
            await File.WriteAllTextAsync(Path.Combine(cacheDirectory, cacheKey + ".json"), "{}");
            Assert.AreEqual(1, Directory.EnumerateFiles(cacheDirectory, "*.json").Count());

            var first = state.SyncGarage61ReferencesAsync();
            await backend.SyncStarted.Task.WaitAsync(TimeSpan.FromSeconds(2));
            var duplicate = state.SyncGarage61ReferencesAsync();
            await Task.WhenAll(first, duplicate);

            Assert.AreEqual(1, backend.SyncCalls);
            Assert.IsFalse(state.IsGarage61ReferenceSyncing);
            Assert.AreEqual(1, state.CurrentAnalysis?.Garage61References?.Laps.Count);
            StringAssert.Contains(state.Garage61ReferenceMessage, "One Garage61 reference lap");
            Assert.IsEmpty(Directory.EnumerateFiles(cacheDirectory, "*.json"));
            Assert.IsNotNull(backend.SyncArguments);
            Assert.AreEqual(analysisPath, backend.SyncArguments.Value.GetProperty("analysis_path").GetString());
            Assert.AreEqual(Path.Combine(root, "data"), backend.SyncArguments.Value.GetProperty("archive_root").GetString());
            Assert.AreEqual(6, backend.SyncArguments.Value.GetProperty("maximum_laps").GetInt32());
            Assert.IsTrue(backend.SyncArguments.Value.GetProperty("download_telemetry").GetBoolean());
            CollectionAssert.AreEquivalent(
                new[] { "analysis_path", "archive_root", "maximum_laps", "download_telemetry" },
                backend.SyncArguments.Value.EnumerateObject().Select(property => property.Name).ToArray(),
                "The explicit reference action must never send raw telemetry or an IBT upload argument.");
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    private sealed class MemorySettingsStore(CompanionSettings settings) : ISettingsStore
    {
        public CompanionSettings Load() => settings;
        public void Save(CompanionSettings value) { }
    }

    private sealed class ReferenceBackend(string analysisPath) : IBackendClient
    {
        private int _syncCalls;
        public int SyncCalls => Volatile.Read(ref _syncCalls);
        public JsonElement? SyncArguments { get; private set; }
        public TaskCompletionSource SyncStarted { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);

        public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default) =>
            Task.FromResult(new BackendHealthResult(true, "iracing-coach-local", "0.15.0", "2026-08-08", 17, TimeSpan.Zero));

        public async Task<JsonElement> CallToolAsync(BackendConfiguration configuration, string toolName, object arguments, CancellationToken cancellationToken = default)
        {
            if (toolName == "sync_garage61_references")
            {
                Interlocked.Increment(ref _syncCalls);
                SyncArguments = JsonSerializer.SerializeToElement(arguments);
                SyncStarted.TrySetResult();
                await Task.Delay(80, cancellationToken);
                return SyncResponse();
            }
            return toolName switch
            {
                "iracing_companion_dashboard" => JsonSerializer.SerializeToElement(new { ok = true, races = Array.Empty<object>() }),
                "discover_iracing_sessions" => JsonSerializer.SerializeToElement(new { sessions = Array.Empty<object>() }),
                "catalog_iracing_setups" => JsonSerializer.SerializeToElement(new { ok = true, entries = Array.Empty<object>() }),
                "garage61_auth_status" => JsonSerializer.SerializeToElement(new { ok = true, configured = true, status = "ready" }),
                "analyze_iracing_race" => AnalysisResponse(RequestedSelector(arguments)),
                _ => JsonSerializer.SerializeToElement(new { ok = true })
            };
        }

        private JsonElement AnalysisResponse(string selector) => JsonSerializer.SerializeToElement(new
        {
            ok = true,
            analysis_id = "reference-sync-test",
            analysis_path = analysisPath,
            selection = new { sim_session_type = "Race", group_id = selector },
            race_card = new
            {
                title = "Recorded race",
                bottom_line = new { evidence_type = "measured", text = "Recorded telemetry is ready." },
                actions = Array.Empty<object>(),
                corner_playbook = new { rows = Array.Empty<object>() },
                race_triggers = Array.Empty<object>(),
                evidence_appendix = Array.Empty<object>()
            },
            analysis_view = new
            {
                schema_version = 1,
                identity = new { event_type = "Race", track_name = "Recorded Track", track_config = "Oval", car_name = "Recorded Car" },
                race_summary = new { recorded_laps = 20, scheduled_laps = 20, pit_stops_detected = 0 },
                runs = Array.Empty<object>(),
                laps = Array.Empty<object>(),
                lap_traces = new { traces = Array.Empty<object>() },
                track_profile = new { profile = Array.Empty<object>() },
                strategy = new { forecast = new { } },
                damage_repair = new { summary = new { }, incident_points = new { events = Array.Empty<object>() } },
                setup_telemetry = new { },
                data_quality = new { },
                race_grades = new { }
            }
        });

        private static JsonElement SyncResponse() => JsonSerializer.SerializeToElement(new
        {
            ok = true,
            status = "complete",
            garage61_representative_laps = new
            {
                status = "available",
                comparison_scope = "own/team",
                representative_laps = new[]
                {
                    new
                    {
                        comparison_role = "representative",
                        setup_type = "fixed",
                        lap = new { id = "42", lapTime = 24.5, canViewTelemetry = true },
                        telemetry = new { status = "cached", sha256 = new string('a', 64) }
                    }
                },
                reference_comparisons = new[]
                {
                    new { lap_id = "42", quality = new { status = "usable", usable = true, signals = new[] { "speed_mph" }, aligned_bins = 190, coverage_fraction = .95 } }
                },
                comparison_quality = new { status = "usable", usable_reference_laps = 1, median_coverage_fraction = .95 }
            },
            cache = new { manifest = new { refreshed_at = "2026-08-07T20:15:00Z", source_hash = new string('b', 64) } }
        });

        private static string RequestedSelector(object arguments)
        {
            var serialized = JsonSerializer.SerializeToElement(arguments);
            return serialized.TryGetProperty("selector", out var selector) ? selector.GetString() ?? string.Empty : string.Empty;
        }
    }
}
