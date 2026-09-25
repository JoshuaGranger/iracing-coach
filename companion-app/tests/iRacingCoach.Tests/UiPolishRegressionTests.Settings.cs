using System.Collections.Concurrent;
using System.Text.Json;
using System.Text.RegularExpressions;
using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;
using iRacingCoach.UI;
using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Components.Web;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using Microsoft.JSInterop;

namespace iRacingCoach.Tests;

public sealed partial class UiPolishRegressionTests
{
    // ---------- 4. Settings save bar ----------

    [TestMethod]
    public async Task RestoredCoachDataIsQuietStatusAndOnlyRealEditsShowTheSaveBar()
    {
        using var fixture = new SettingsFixture("Restored 2,606 Coach items.");
        await fixture.State.InitializeAsync();
        await fixture.WaitForIdle();
        Assert.AreEqual("Restored 2,606 Coach items.", fixture.State.SettingsMessage);
        Assert.IsFalse(fixture.State.SettingsDirty, "Restoring Coach data is not an edit.");

        var html = await RenderSettings(fixture.State);
        Assert.DoesNotContain("settings-savebar", html, "No save bar when nothing was changed.");
        StringAssert.Contains(html, "data-settings-quiet");
        StringAssert.Contains(html, "Restored 2,606 Coach items.");

        var original = fixture.State.Settings.UseReducedMotion;
        fixture.State.Settings.UseReducedMotion = !original;
        Assert.IsTrue(fixture.State.SettingsDirty);
        html = await RenderSettings(fixture.State);
        StringAssert.Contains(html, "data-settings-savebar");
        StringAssert.Contains(html, "Unsaved changes");
        StringAssert.Contains(html, "Save to keep these changes.");
        Assert.DoesNotContain("Restored 2,606 Coach items.", html, "Restore status never reads as the reason to save.");

        fixture.State.Settings.UseReducedMotion = original;
        Assert.IsFalse(fixture.State.SettingsDirty, "Undoing the edit clears the save bar.");

        fixture.State.SetThemeColor("violet");
        Assert.IsTrue(fixture.State.SettingsDirty);
        fixture.State.SaveSettings();
        Assert.IsFalse(fixture.State.SettingsDirty, "A successful save is the new baseline.");
        html = await RenderSettings(fixture.State);
        Assert.DoesNotContain("settings-savebar", html);
        StringAssert.Contains(html, "Settings saved.");

        fixture.State.Settings.IRacingRoot = "not-an-absolute-folder";
        fixture.State.SaveSettings();
        Assert.IsTrue(fixture.State.SettingsDirty, "A refused save keeps the edit pending.");
        Assert.AreEqual(fixture.State.SettingsMessage, fixture.State.SettingsEditMessage, "The bar explains why the save was refused.");
        Assert.AreNotEqual("Settings saved.", fixture.State.SettingsEditMessage);
    }

    private static Task<string> RenderSettings(CompanionState state) =>
        Render<SettingsPage>(state, new() { [nameof(SettingsPage.State)] = state });

    private sealed class SettingsFixture : IDisposable
    {
        public string Root { get; } = TempRoot();
        public CompanionState State { get; }
        public SettingsFixture(string archiveMessage)
        {
            var paths = new IsolatedCompanionPathProvider(Root);
            State = new CompanionState(new QuietBackend(), new MemorySettingsStore(new CompanionSettings(paths)), new DisconnectedLiveTelemetrySource(),
                new DisabledCoachEngineSupervisor(), new DisabledGarage61CredentialStore(Path.Combine(Root, "credentials")),
                new RestoredArchive(archiveMessage), paths, allowExternalHostActions: false);
        }
        public async Task WaitForIdle()
        {
            var until = DateTime.UtcNow + TimeSpan.FromSeconds(5);
            while (State.IsRefreshing && DateTime.UtcNow < until) await Task.Delay(10);
            Assert.IsFalse(State.IsRefreshing);
        }
        public void Dispose() => State.Dispose();
    }

    private sealed class MemorySettingsStore(CompanionSettings settings) : ISettingsStore
    {
        public CompanionSettings Load() => settings;
        public void Save(CompanionSettings value) { }
    }

    private sealed class RestoredArchive(string message) : IDurableArchiveService
    {
        public ArchiveStatus Initialize(string root, string appVersion, string backendVersion = "unknown") =>
            new(true, null, 1, "synthetic", root, new(2000, 1, 0, 600, 5, 0, 0, 0, 0, 0, 0, 0, 0), null, message);
        public BackupPreparationResult PrepareForCopy(string root, string appVersion, string backendVersion, IReadOnlyList<string>? blockingActivity = null) =>
            new(true, root, DateTimeOffset.UtcNow, 0, 0, "synthetic", 0, [], "Ready");
        public void MarkActive(string root) { }
    }

    private sealed class QuietBackend : IBackendClient
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
                _ => new { ok = true }
            }));
    }
}
