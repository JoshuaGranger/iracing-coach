using System.Reflection;
using System.Text.Json;
using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;
using iRacingCoach.UI;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class SettingsNavigationTests
{
    [TestMethod]
    public void ConnectionsIntentOpensOnceAndDoesNotOverrideALaterUserCollapse()
    {
        using var state = new CompanionState(new UnusedBackend());
        var page = new SettingsPage();
        typeof(SettingsPage).GetProperty(nameof(SettingsPage.State))!.SetValue(page, state);

        state.Navigate("connections");
        Assert.AreEqual("settings", state.CurrentPage);
        Invoke(page, "OnParametersSet");
        Assert.IsTrue(ConnectionsOpen(page));
        Assert.IsFalse(state.ConnectionsNavigationRequested);

        Invoke(page, "ToggleConnections");
        Invoke(page, "OnParametersSet");
        Assert.IsFalse(ConnectionsOpen(page), "A status refresh must not reopen a panel the user closed.");

        state.Navigate("connections");
        Invoke(page, "OnParametersSet");
        Assert.IsTrue(ConnectionsOpen(page), "A new explicit connection request should work on the existing Settings page.");
    }

    [TestMethod]
    public void OrdinarySettingsNavigationClearsAnUnconsumedConnectionIntent()
    {
        using var state = new CompanionState(new UnusedBackend());
        state.Navigate("connections");
        state.Navigate("home");
        state.Navigate("settings");
        var page = new SettingsPage();
        typeof(SettingsPage).GetProperty(nameof(SettingsPage.State))!.SetValue(page, state);

        Invoke(page, "OnParametersSet");

        Assert.IsFalse(ConnectionsOpen(page));
        Assert.IsFalse(state.ConsumeConnectionsNavigationRequest());
    }

    private static bool ConnectionsOpen(SettingsPage page) =>
        (bool)typeof(SettingsPage).GetField("_connectionsOpen", BindingFlags.Instance | BindingFlags.NonPublic)!.GetValue(page)!;

    private static void Invoke(SettingsPage page, string method) =>
        typeof(SettingsPage).GetMethod(method, BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(page, null);

    private sealed class UnusedBackend : IBackendClient
    {
        public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default) =>
            throw new InvalidOperationException("Navigation must not invoke the backend.");
        public Task<JsonElement> CallToolAsync(BackendConfiguration configuration, string toolName, object arguments, CancellationToken cancellationToken = default) =>
            throw new InvalidOperationException("Navigation must not invoke the backend.");
    }
}
