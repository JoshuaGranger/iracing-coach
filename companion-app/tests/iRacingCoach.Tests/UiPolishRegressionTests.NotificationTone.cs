using System.Reflection;
using System.Text.Json;
using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

public sealed partial class UiPolishRegressionTests
{
    // ---------- 1b. Notification tone comes from the call site, not the wording ----------

    [TestMethod]
    public void EveryNotificationStatesItsToneAndNoneIsGuessedFromWording()
    {
        var notify = typeof(CompanionState).GetMethods(BindingFlags.Public | BindingFlags.Instance)
            .Where(method => method.Name == nameof(CompanionState.Notify)).ToArray();
        Assert.IsNotEmpty(notify);
        foreach (var method in notify)
        {
            var tone = method.GetParameters().SingleOrDefault(parameter => parameter.ParameterType == typeof(ToastTone));
            Assert.IsNotNull(tone, $"{method} must take the tone from its caller.");
            Assert.IsFalse(tone.HasDefaultValue, "A default tone would let a failure clear itself unnoticed.");
        }
        Assert.IsNull(typeof(ToastNotice).GetMethod("ToneOf", BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static),
            "Tone is never inferred from the words of a message.");

        // The host's own notifications state their tone too (the WPF project is not referenced here).
        var mainWindow = File.ReadAllText(Path.Combine(TestRepositoryPaths.CompanionAppRoot, "src", "iRacingCoach.App", "MainWindow.xaml.cs"));
        StringAssert.Contains(mainWindow, "\"Settings saved, but that telemetry-popout hotkey is invalid or already in use.\", ToastTone.Warning");
        StringAssert.Contains(mainWindow, "\"Settings saved, but Windows sign-in startup could not be updated.\", ToastTone.Error");
    }

    [TestMethod]
    public void ARefusedSettingsSaveStaysUntilDismissed()
    {
        var root = TempRoot();
        var paths = new IsolatedCompanionPathProvider(root);
        var settings = new CompanionSettings(paths) { Compatibility = SettingsCompatibilityState.ReadOnly(99, "This settings file uses schema 99.") };
        using var state = new CompanionState(new QuietBackend(), new MemorySettingsStore(settings), new DisconnectedLiveTelemetrySource(),
            new DisabledCoachEngineSupervisor(), new DisabledGarage61CredentialStore(Path.Combine(root, "credentials")),
            new CopyCheckArchive(), paths, allowExternalHostActions: false);

        state.SaveSettings();

        AssertStaysUntilDismissed(state, "Settings were not changed.");
    }

    [TestMethod]
    public async Task ARecordingThatVanishedBeforeRetryStaysUntilDismissed()
    {
        using var state = NewState(new QuietBackend());

        await state.RetryJobAsync(new JobItem { Id = "job-gone", Title = "Analyze Bristol", CanonicalKey = "session:gone", Status = "failed" });

        AssertStaysUntilDismissed(state, "That recording is no longer available. Refresh your races and try again.");
    }

    [TestMethod]
    public async Task ACoachFolderThatCannotBeCopiedStaysUntilDismissed()
    {
        var root = TempRoot();
        var paths = new IsolatedCompanionPathProvider(root);
        var archive = new CopyCheckArchive();
        using var state = new CompanionState(new QuietBackend(), new MemorySettingsStore(new CompanionSettings(paths)), new DisconnectedLiveTelemetrySource(),
            new DisabledCoachEngineSupervisor(), new DisabledGarage61CredentialStore(Path.Combine(root, "credentials")),
            archive, paths, allowExternalHostActions: false);

        state.Jobs.Add(new JobItem { Id = "job-running", Title = "Analyze Bristol", CanonicalKey = "session:bristol", Status = "running" });
        await state.PrepareBackupAsync();
        AssertStaysUntilDismissed(state, "The Coach folder is still in use. Finish the listed work and try again.");

        state.Jobs.Clear();
        archive.Failure = new IOException("The Coach folder is locked by another program.");
        await state.PrepareBackupAsync();
        AssertStaysUntilDismissed(state, "The Coach folder is not ready to copy.");
        Assert.AreEqual(ToastTone.Error, state.CurrentToast!.Tone);
    }

    [TestMethod]
    public async Task ABackendRefusalInItsOwnWordsStaysUntilDismissed()
    {
        using var state = NewState(new RefusingBackend("load_driving_trial", "{\"message\":\"No saved test has that id.\"}"));

        await state.OpenDrivingTrialAsync("trial-missing");

        AssertStaysUntilDismissed(state, "No saved test has that id.");
        Assert.AreEqual(ToastTone.Error, state.CurrentToast!.Tone);
    }

    [TestMethod]
    public void AnErrorOutlivesNavigationAndInformationShownOverIt()
    {
        using var state = NewState();
        state.ReportUnhandledException("backend tool analyze_iracing_race", new InvalidOperationException("The recording ended before the green flag."));
        var error = state.CurrentToast!;
        Assert.AreEqual(ToastTone.Error, error.Tone);

        state.Navigate("live");
        Assert.AreEqual(error, state.CurrentToast, "Changing page does not clear an error the driver has not dismissed.");

        state.ToggleLiveCoaching();
        Assert.AreEqual("Live coaching paused.", state.Toast);
        Assert.IsTrue(state.CurrentToast!.AutoDismisses);
        state.DismissToast(state.CurrentToast.Revision); // The information's countdown ends.
        Assert.AreEqual(error, state.CurrentToast, "The error returns once the passing information clears.");

        state.ToggleLiveCoaching();
        state.Navigate("home");
        Assert.AreEqual(error, state.CurrentToast, "Leaving a page clears the information, not the error under it.");

        state.DismissToast(error.Revision);
        Assert.IsNull(state.CurrentToast);
    }

    private static void AssertStaysUntilDismissed(CompanionState state, string message)
    {
        Assert.AreEqual(message, state.Toast);
        Assert.AreNotEqual(ToastTone.Info, state.CurrentToast!.Tone, $"\"{message}\" reports a request that did not happen.");
        Assert.IsFalse(state.CurrentToast.AutoDismisses, $"\"{message}\" must stay until the driver dismisses it.");
        state.Navigate("home");
        Assert.AreEqual(message, state.Toast, "Navigation does not clear it either.");
    }

    private sealed class CopyCheckArchive : IDurableArchiveService
    {
        public Exception? Failure { get; set; }
        public ArchiveStatus Initialize(string root, string appVersion, string backendVersion = "unknown") =>
            new(true, null, 1, "synthetic", root, new(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0), null, "Ready");
        public BackupPreparationResult PrepareForCopy(string root, string appVersion, string backendVersion, IReadOnlyList<string>? blockingActivity = null)
        {
            if (Failure is not null) throw Failure;
            var blockers = blockingActivity ?? [];
            return new(blockers.Count == 0, root, DateTimeOffset.UtcNow, 0, 0, "synthetic", 0, blockers,
                blockers.Count == 0 ? "Ready" : $"Finish {string.Join(", ", blockers)} first.");
        }
        public void MarkActive(string root) { }
    }

    private sealed class RefusingBackend(string tool, string refusal) : IBackendClient
    {
        public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default) =>
            Task.FromResult(new BackendHealthResult(true, "test", "1", "test", 1, TimeSpan.Zero));
        public Task<JsonElement> CallToolAsync(BackendConfiguration configuration, string toolName, object arguments, CancellationToken cancellationToken = default) =>
            toolName == tool
                ? Task.FromException<JsonElement>(new BackendDomainException(refusal))
                : Task.FromResult(JsonSerializer.SerializeToElement(new { ok = true }));
    }
}
