using System.Collections.Concurrent;
using System.Text.Json;
using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class SettingsRefreshBehaviorTests
{
    [TestMethod]
    public async Task VisualAndHotkeySavePersistsAndNotifiesWithoutRestartingDataWork()
    {
        using var fixture = new Fixture();
        await fixture.State.InitializeAsync();
        await fixture.WaitForIdle();
        var health = fixture.Backend.HealthCalls;
        var calls = fixture.Backend.ToolCalls;
        var archiveInitializations = fixture.Archive.InitializeCalls;
        var events = 0;
        fixture.State.SettingsSaved += saved => { events++; Assert.AreSame(fixture.State.Settings, saved); };
        fixture.State.Settings.ThemeColor = "violet";
        fixture.State.Settings.UseReducedMotion = true;
        fixture.State.Settings.LiveMonitor.GlobalHotkey = "Ctrl+Alt+M";
        fixture.State.SaveSettings();
        await Task.Delay(100);
        Assert.AreEqual("Settings saved.", fixture.State.SettingsMessage);
        Assert.AreEqual(1, fixture.Store.SaveCalls);
        Assert.AreEqual(1, events);
        Assert.AreEqual(health, fixture.Backend.HealthCalls);
        Assert.AreEqual(calls, fixture.Backend.ToolCalls);
        Assert.AreEqual(archiveInitializations, fixture.Archive.InitializeCalls);
        Assert.IsFalse(fixture.State.IsRefreshing);
    }

    [TestMethod]
    public async Task DataRootAndCredentialChangesRefreshButOnlyHomeChangeReinitializesArchive()
    {
        using var fixture = new Fixture();
        await fixture.State.InitializeAsync();
        await fixture.WaitForIdle();
        var expectedArchives = fixture.Archive.InitializeCalls;
        foreach (var change in new Action[]
        {
            () => fixture.State.Settings.IRacingRoot = Path.Combine(fixture.Root, "other-telemetry"),
            () => fixture.State.Settings.IRacingInstallRoot = Path.Combine(fixture.Root, "other-install"),
            () => { fixture.State.Settings.CoachHome = Path.Combine(fixture.Root, "other-coach"); expectedArchives++; },
            () => fixture.State.Garage61KeyInput = "synthetic-test-token"
        })
        {
            var before = fixture.Backend.DashboardCalls;
            change();
            fixture.State.SaveSettings();
            await fixture.WaitForDashboardAfter(before);
            Assert.AreEqual(expectedArchives, fixture.Archive.InitializeCalls);
        }
        Assert.AreEqual(1, fixture.Credentials.StoreCalls);
        Assert.AreEqual(string.Empty, fixture.State.Garage61KeyInput);
        var finalCalls = fixture.Backend.ToolCalls;
        fixture.State.SaveSettings();
        await Task.Delay(100);
        Assert.AreEqual(finalCalls, fixture.Backend.ToolCalls);
        Assert.AreEqual(expectedArchives, fixture.Archive.InitializeCalls);
    }

    [TestMethod]
    public async Task FailedSaveDoesNotRefreshOrLosePendingCredentialRefresh()
    {
        using var fixture = new Fixture();
        await fixture.State.InitializeAsync();
        await fixture.WaitForIdle();
        var before = fixture.Backend.DashboardCalls;
        fixture.Store.FailSave = true;
        fixture.State.Garage61KeyInput = "synthetic-replacement-token";
        fixture.State.SaveSettings();
        await Task.Delay(100);
        Assert.AreEqual(before, fixture.Backend.DashboardCalls);
        Assert.AreEqual("Settings were not saved.", fixture.State.Toast);
        Assert.AreEqual(string.Empty, fixture.State.Garage61KeyInput);
        fixture.Store.FailSave = false;
        fixture.State.SaveSettings();
        await fixture.WaitForDashboardAfter(before);
        Assert.AreEqual(1, fixture.Credentials.StoreCalls);
    }

    private sealed class Fixture : IDisposable
    {
        public string Root { get; } = Path.Combine(Path.GetTempPath(), "iracing-coach-settings-refresh", Guid.NewGuid().ToString("N"));
        public CountingBackend Backend { get; } = new();
        public CountingArchive Archive { get; } = new();
        public CountingCredentials Credentials { get; }
        public MemorySettingsStore Store { get; }
        public CompanionState State { get; }
        public Fixture()
        {
            var paths = new IsolatedCompanionPathProvider(Root);
            Credentials = new CountingCredentials(Path.Combine(Root, "synthetic-credential-store"));
            Store = new MemorySettingsStore(new CompanionSettings(paths));
            State = new CompanionState(Backend, Store, new DisconnectedLiveTelemetrySource(),
                new DisabledCoachEngineSupervisor(), Credentials, Archive, paths, allowExternalHostActions: false);
        }
        public async Task WaitForIdle()
        {
            var until = DateTime.UtcNow + TimeSpan.FromSeconds(3);
            while (State.IsRefreshing && DateTime.UtcNow < until) await Task.Delay(10);
            Assert.IsFalse(State.IsRefreshing);
        }
        public async Task WaitForDashboardAfter(int count)
        {
            var until = DateTime.UtcNow + TimeSpan.FromSeconds(3);
            while (Backend.DashboardCalls <= count && DateTime.UtcNow < until) await Task.Delay(10);
            Assert.IsGreaterThan(count, Backend.DashboardCalls);
            await WaitForIdle();
        }
        public void Dispose() => State.Dispose();
    }

    private sealed class MemorySettingsStore(CompanionSettings settings) : ISettingsStore
    {
        public int SaveCalls { get; private set; }
        public bool FailSave { get; set; }
        public CompanionSettings Load() => settings;
        public void Save(CompanionSettings value) { if (FailSave) throw new IOException("Synthetic settings write failure."); SaveCalls++; }
    }

    private sealed class CountingCredentials(string path) : IGarage61CredentialStore
    {
        public int StoreCalls { get; private set; }
        public bool IsConfigured { get; private set; }
        public string CredentialPath => path;
        public void Store(string token) { StoreCalls++; IsConfigured = true; }
        public void Remove() => IsConfigured = false;
    }

    private sealed class CountingArchive : IDurableArchiveService
    {
        public int InitializeCalls { get; private set; }
        public ArchiveStatus Initialize(string root, string appVersion, string backendVersion = "unknown")
        {
            InitializeCalls++;
            return new(true, null, 1, "synthetic", root, new(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0), null, "Ready");
        }
        public BackupPreparationResult PrepareForCopy(string root, string appVersion, string backendVersion, IReadOnlyList<string>? blockingActivity = null) => new(true, root, DateTimeOffset.UtcNow, 0, 0, "synthetic", 0, [], "Ready");
        public void MarkActive(string root) { }
    }

    private sealed class CountingBackend : IBackendClient
    {
        private int _healthCalls;
        private readonly ConcurrentDictionary<string, int> _calls = new();
        public int HealthCalls => Volatile.Read(ref _healthCalls);
        public int ToolCalls => _calls.Values.Sum();
        public int DashboardCalls => _calls.GetValueOrDefault("iracing_companion_dashboard");
        public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default)
        {
            Interlocked.Increment(ref _healthCalls);
            return Task.FromResult(new BackendHealthResult(true, "test", "test", "test", 1, TimeSpan.Zero));
        }
        public Task<JsonElement> CallToolAsync(BackendConfiguration configuration, string toolName, object arguments, CancellationToken cancellationToken = default)
        {
            _calls.AddOrUpdate(toolName, 1, (_, count) => count + 1);
            return Task.FromResult(JsonSerializer.SerializeToElement(toolName switch
            {
                "iracing_companion_dashboard" => (object)new { ok = true, races = Array.Empty<object>() },
                "discover_iracing_sessions" => new { sessions = Array.Empty<object>() },
                "catalog_iracing_setups" => new { ok = true, entries = Array.Empty<object>() },
                "garage61_auth_status" => new { ok = false, configured = false, status = "not_configured" },
                _ => new { ok = true }
            }));
        }
    }
}
