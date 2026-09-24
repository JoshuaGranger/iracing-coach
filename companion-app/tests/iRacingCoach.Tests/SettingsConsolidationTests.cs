using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;
using iRacingCoach.UI;
using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Components.Web;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class SettingsConsolidationTests
{
    [TestMethod]
    public void OnlyWarningsBecomeIssuesAndEachCarriesARealRecovery()
    {
        var issues = CompanionState.IssuesFrom(
        [
            new DiagnosticFact("App", "v0.18 · Windows x64", "ready"),
            new DiagnosticFact("Race analysis service", "Python runtime missing", "warning"),
            new DiagnosticFact("Service protocol", "Check the local analysis service", "warning"),
            new DiagnosticFact("Coach Engine", "Coach Engine is disabled in this test host.", "warning"),
            new DiagnosticFact("ChatGPT", "Not connected", "neutral"),
            new DiagnosticFact("Garage61", "Not connected", "neutral"),
            new DiagnosticFact("iRacing folder", @"D:\missing", "warning"),
            new DiagnosticFact("Copy readiness", "Coach files changed since the last copy check", "warning"),
            new DiagnosticFact("Local setups", "Stale index", "warning"),
            new DiagnosticFact("Replay capture", "Chunk write failed", "warning"),
            new DiagnosticFact("Live update pipeline", "3 dropped", "warning")
        ]);

        CollectionAssert.AreEqual(
            new[] { "Race analysis service", "iRacing folder", "Copy readiness", "Local setups", "Replay capture", "Live update pipeline" },
            issues.Select(issue => issue.Label).ToArray(),
            "One service failure is one issue; ChatGPT, its Coach Engine and Garage61 are never Settings issues.");
        Assert.AreEqual(SettingsRecovery.Repair, issues[0].Recovery);
        Assert.AreEqual(SettingsRecovery.Folders, issues[1].Recovery);
        Assert.AreEqual(SettingsRecovery.Backup, issues[2].Recovery);
        Assert.AreEqual(SettingsRecovery.CheckAgain, issues[3].Recovery);
        Assert.AreEqual(SettingsRecovery.Logs, issues[4].Recovery);
        Assert.AreEqual(SettingsRecovery.None, issues[5].Recovery, "No invented action when the app cannot fix it.");
    }

    [TestMethod]
    public async Task HealthySettingsStateOneLineAndNameProviderStatus()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-settings", Guid.NewGuid().ToString("N"));
        using var state = new CompanionState(new NoBackend(), null, new DisconnectedLiveTelemetrySource(), null, allowExternalHostActions: false);
        state.Settings.CoachHome = Path.Combine(root, "iRacing Coach");
        state.Settings.LocalStateRootOverride = Path.Combine(root, "local");
        var html = await Render(state);

        StringAssert.Contains(html, "data-settings-status");
        StringAssert.Contains(html, "Local analysis is ready.");
        StringAssert.Contains(html, "without ChatGPT, Garage61 or the internet");
        StringAssert.Contains(html, "ChatGPT not connected · Garage61");
        Assert.DoesNotContain("Needs attention", html);
        Assert.DoesNotContain("App home", html, "The Coach folder row names the folder, not a placeholder.");
        StringAssert.Contains(html, ">iRacing Coach</strong>");
    }

    [TestMethod]
    public void AnIRacingFolderWithoutSetupsIsAnEmptyLibraryNotAFailure()
    {
        var root = Directory.CreateTempSubdirectory("iracing-coach-no-setups").FullName;
        try
        {
            Assert.IsTrue(CompanionState.NoSetupsSaved(root), "Nothing saved yet is an empty library.");
            Assert.IsFalse(CompanionState.NoSetupsSaved(Path.Combine(root, "missing")), "A wrong iRacing folder must still fail visibly.");
            Directory.CreateDirectory(Path.Combine(root, "setups"));
            Assert.IsFalse(CompanionState.NoSetupsSaved(root));
            Assert.IsFalse(CompanionState.NoSetupsSaved(Path.Combine(root, "setups")), "A folder that is itself the setups folder is catalogued.");
        }
        finally { Directory.Delete(root, recursive: true); }
    }

    private sealed class NoBackend : IBackendClient
    {
        public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default) => throw new NotSupportedException();
        public Task<System.Text.Json.JsonElement> CallToolAsync(BackendConfiguration configuration, string toolName, object arguments, CancellationToken cancellationToken = default) =>
            throw new InvalidOperationException("Rendering Settings must not call the backend.");
    }

    private static async Task<string> Render(CompanionState state)
    {
        using var services = new ServiceCollection().AddSingleton<ILoggerFactory>(Microsoft.Extensions.Logging.Abstractions.NullLoggerFactory.Instance).BuildServiceProvider();
        await using var renderer = new HtmlRenderer(services, services.GetRequiredService<ILoggerFactory>());
        var html = await renderer.Dispatcher.InvokeAsync(async () => (await renderer.RenderComponentAsync<SettingsPage>(ParameterView.FromDictionary(
            new Dictionary<string, object?> { [nameof(SettingsPage.State)] = state }))).ToHtmlString());
        return System.Net.WebUtility.HtmlDecode(html);
    }
}
