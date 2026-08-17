using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class DurableCompatibilityTests
{
    [TestMethod]
    public void ArchiveManifest_InspectsVersionAndIdentityBeforeAnyWrite()
    {
        foreach (var payload in new[]
        {
            "{}",
            "{\"schemaVersion\":null,\"archiveId\":\"bad\"}",
            "{\"schemaVersion\":\"1\",\"archiveId\":\"bad\"}",
            "{\"schemaVersion\":-1,\"archiveId\":\"bad\"}",
            "{\"schemaVersion\":1}",
            "{\"schemaVersion\":1,\"archiveId\":null}"
        })
        {
            var root = NewRoot();
            var path = Path.Combine(root, DurableArchiveService.ManifestFileName);
            File.WriteAllText(path, payload, new UTF8Encoding(false));
            var before = SHA256.HashData(File.ReadAllBytes(path));

            Assert.Throws<InvalidDataException>(() => new DurableArchiveService().Initialize(root, "test"), payload);

            CollectionAssert.AreEqual(before, SHA256.HashData(File.ReadAllBytes(path)), payload);
            Assert.IsFalse(File.Exists(Path.Combine(root, DurableArchiveService.PortableStateFileName)), payload);
            Assert.IsFalse(Directory.Exists(Path.Combine(root, "data")), payload);
        }
    }

    [TestMethod]
    public void ArchiveMigration_BacksUpExactSourceBytesAndPreservesUnknownFields()
    {
        var root = NewRoot();
        var path = Path.Combine(root, DurableArchiveService.ManifestFileName);
        var source = "{\r\n  \"schemaVersion\": 0,\r\n  \"archiveId\": \"legacy-exact\",\r\n  \"createdUtc\": \"2026-01-01T00:00:00Z\",\r\n  \"futureTop\": {\"token\":17},\r\n  \"migrationHistory\": [{\"fromVersion\":0,\"toVersion\":0,\"startedUtc\":\"2026-01-01T00:00:00Z\",\"completedUtc\":\"2026-01-01T00:00:00Z\",\"status\":\"complete\",\"detail\":\"legacy\",\"futureNested\":\"kept\"}]\r\n}";
        File.WriteAllText(path, source, new UTF8Encoding(false));

        _ = new DurableArchiveService().Initialize(root, "test");

        var backup = Directory.EnumerateFiles(Path.Combine(root, "backups"), "archive-manifest-before-schema-1-*.json").Single();
        Assert.AreEqual(source, File.ReadAllText(backup));
        using var migrated = JsonDocument.Parse(File.ReadAllBytes(path));
        Assert.AreEqual(17, migrated.RootElement.GetProperty("futureTop").GetProperty("token").GetInt32());
        Assert.AreEqual("kept", migrated.RootElement.GetProperty("migrationHistory")[0].GetProperty("futureNested").GetString());
    }

    [TestMethod]
    public void ArchiveRefresh_PreservesUnknownTopLevelAndComponentFields()
    {
        var root = NewRoot();
        var path = Path.Combine(root, DurableArchiveService.ManifestFileName);
        File.WriteAllText(path, "{\"schemaVersion\":1,\"archiveId\":\"current\",\"futureTop\":true,\"components\":[{\"path\":\"data/reports\",\"fileCount\":0,\"bytes\":0,\"sha256\":\"old\",\"futureComponent\":{\"kind\":\"v2\"}}]}");

        _ = new DurableArchiveService().PrepareForCopy(root, "test", "test");

        using var refreshed = JsonDocument.Parse(File.ReadAllBytes(path));
        Assert.IsTrue(refreshed.RootElement.GetProperty("futureTop").GetBoolean());
        var reports = refreshed.RootElement.GetProperty("components").EnumerateArray()
            .Single(item => item.GetProperty("path").GetString() == "data/reports");
        Assert.AreEqual("v2", reports.GetProperty("futureComponent").GetProperty("kind").GetString());
    }

    [TestMethod]
    public void Settings_FutureAndMalformedFilesStartReadOnlyWithoutChangingBytes()
    {
        foreach (var payload in new[]
        {
            "{\"settingsSchemaVersion\":99,\"future\":true}",
            "{}",
            "{\"settingsSchemaVersion\":null}",
            "{\"settingsSchemaVersion\":\"5\"}",
            "{\"settingsSchemaVersion\":0}",
            "[]"
        })
        {
            var root = NewRoot();
            var path = Path.Combine(root, "settings.json");
            File.WriteAllText(path, payload, new UTF8Encoding(false));
            var before = SHA256.HashData(File.ReadAllBytes(path));
            var store = new JsonSettingsStore(path, new NoopCredentialStore(Path.Combine(root, "credential")));

            var settings = store.Load();

            Assert.IsFalse(settings.Compatibility.Writable, payload);
            CollectionAssert.AreEqual(before, SHA256.HashData(File.ReadAllBytes(path)), payload);
            Assert.Throws<SettingsCompatibilityException>(() => store.Save(settings), payload);
            CollectionAssert.AreEqual(before, SHA256.HashData(File.ReadAllBytes(path)), payload);
        }
    }

    [TestMethod]
    public void SettingsMigration_BacksUpExactBytesAndPreservesUnknownFields()
    {
        var root = NewRoot();
        var path = Path.Combine(root, "settings.json");
        var source = "{\r\n  \"settingsSchemaVersion\": 4,\r\n  \"themeColor\": \"mint\",\r\n  \"customThemeColor\": \"#5CE8C3\",\r\n  \"futureTop\": {\"value\":19},\r\n  \"liveMonitor\": {\"activeLayoutId\":\"factory-default\",\"futureNested\":\"kept\"}\r\n}";
        File.WriteAllText(path, source, new UTF8Encoding(false));
        var store = new JsonSettingsStore(path, new NoopCredentialStore(Path.Combine(root, "credential")));

        var settings = store.Load();

        Assert.IsTrue(settings.Compatibility.Writable);
        Assert.AreEqual(source, File.ReadAllText(path + $".before-schema-{JsonSettingsStore.CurrentSchemaVersion}.backup.json"));
        using var migrated = JsonDocument.Parse(File.ReadAllBytes(path));
        Assert.AreEqual(JsonSettingsStore.CurrentSchemaVersion, migrated.RootElement.GetProperty("settingsSchemaVersion").GetInt32());
        Assert.AreEqual(19, migrated.RootElement.GetProperty("futureTop").GetProperty("value").GetInt32());
        Assert.AreEqual("kept", migrated.RootElement.GetProperty("liveMonitor").GetProperty("futureNested").GetString());
    }

    [TestMethod]
    public void MachineSettings_PreserveUnknownsAndRefuseFutureVersionsWithoutWrites()
    {
        var root = NewRoot();
        var portable = Path.Combine(root, "settings.json");
        var machine = Path.Combine(root, "machine.json");
        File.WriteAllText(portable, "{\"settingsSchemaVersion\":5}");
        File.WriteAllText(machine, "{\"schemaVersion\":2,\"futureTop\":7,\"liveMonitor\":{\"left\":12,\"futureNested\":\"kept\"}}");
        var store = new JsonSettingsStore(portable, new NoopCredentialStore(Path.Combine(root, "credential")), machine);
        var settings = store.Load();
        settings.LiveMonitor.Left = 20;
        store.Save(settings);

        using (var current = JsonDocument.Parse(File.ReadAllBytes(machine)))
        {
            Assert.AreEqual(7, current.RootElement.GetProperty("futureTop").GetInt32());
            Assert.AreEqual("kept", current.RootElement.GetProperty("liveMonitor").GetProperty("futureNested").GetString());
            Assert.AreEqual(20d, current.RootElement.GetProperty("liveMonitor").GetProperty("left").GetDouble());
        }

        var future = "{\"schemaVersion\":99,\"futureBytes\":\"unchanged\",\"liveMonitor\":{\"left\":99}}";
        File.WriteAllText(machine, future, new UTF8Encoding(false));
        var before = SHA256.HashData(File.ReadAllBytes(machine));
        var futureStore = new JsonSettingsStore(portable, new NoopCredentialStore(Path.Combine(root, "credential")), machine);
        var futureSettings = futureStore.Load();
        futureSettings.LiveMonitor.Left = 44;
        futureStore.Save(futureSettings);

        CollectionAssert.AreEqual(before, SHA256.HashData(File.ReadAllBytes(machine)));
        Assert.AreEqual(future, File.ReadAllText(machine));
    }

    [TestMethod]
    public void FutureSettings_KeepTheAppShellAvailableAndDisableSave()
    {
        var root = NewRoot();
        var path = Path.Combine(root, "settings.json");
        var source = "{\"settingsSchemaVersion\":99,\"future\":true}";
        File.WriteAllText(path, source, new UTF8Encoding(false));
        var store = new JsonSettingsStore(path, new NoopCredentialStore(Path.Combine(root, "credential")));

        using var state = new CompanionState(new NoopBackend(), store);
        state.SaveSettings();

        Assert.IsFalse(state.SettingsWritable);
        StringAssert.Contains(state.SettingsMessage, "schema 99");
        Assert.AreEqual("Settings were not changed.", state.Toast);
        Assert.AreEqual(source, File.ReadAllText(path));
    }

    private static string NewRoot()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-durable-contract", Guid.NewGuid().ToString("N"), "iRacing Coach");
        Directory.CreateDirectory(root);
        return root;
    }

    private sealed class NoopCredentialStore(string credentialPath) : IGarage61CredentialStore
    {
        public bool IsConfigured => false;
        public string CredentialPath { get; } = credentialPath;
        public void Store(string token) { }
        public void Remove() { }
    }

    private sealed class NoopBackend : IBackendClient
    {
        public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default) =>
            Task.FromResult(new BackendHealthResult(false, "offline", "test", "test", 0, TimeSpan.Zero, "offline"));

        public Task<JsonElement> CallToolAsync(BackendConfiguration configuration, string toolName, object arguments, CancellationToken cancellationToken = default) =>
            throw new InvalidOperationException("No backend calls are expected.");
    }
}
