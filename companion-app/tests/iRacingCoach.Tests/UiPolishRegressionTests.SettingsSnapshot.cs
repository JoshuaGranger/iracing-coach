using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

public sealed partial class UiPolishRegressionTests
{
    // ---------- 4b. The save bar follows everything the settings files persist ----------

    [TestMethod]
    public void RestoreDefaultsIsUnsavedWhateverPersistedValueItChanges()
    {
        // Every value Restore defaults resets, changed on its own while the page values are
        // already default: the reset must show as unsaved, or the next launch brings it back.
        var cases = new (string Name, Action<CompanionSettings> Change)[]
        {
            ("DiagnosticIncludeConfounded", settings => settings.DiagnosticIncludeConfounded = true),
            ("LiveMonitor.Visible", settings => settings.LiveMonitor.Visible = true),
            ("LiveMonitor.UserLayouts and ActiveLayoutId", settings => LiveMonitorLayouts.Create(settings.LiveMonitor)),
            ("LiveMonitor.IsLocked", settings => settings.LiveMonitor.IsLocked = false),
            ("LiveMonitor.Left", settings => settings.LiveMonitor.Left = 120),
            ("LiveMonitor.Top", settings => settings.LiveMonitor.Top = 80),
            ("LiveMonitor.OverallScale", settings => settings.LiveMonitor.OverallScale = 1.2),
            ("LiveMonitor.MonitorDeviceName", settings => settings.LiveMonitor.MonitorDeviceName = @"\\.\DISPLAY2"),
            ("LiveMonitor.PlacementRecoveredAt", settings => settings.LiveMonitor.PlacementRecoveredAt = DateTimeOffset.UtcNow),
            ("LiveMonitor.GlobalHotkey", settings => settings.LiveMonitor.GlobalHotkey = "Ctrl+Shift+L"),
            ("UseReducedMotion", settings => settings.UseReducedMotion = true)
        };
        foreach (var (name, change) in cases)
        {
            var root = TempRoot();
            var paths = new IsolatedCompanionPathProvider(root);
            var settings = new CompanionSettings(paths);
            change(settings);
            using var state = NewSettingsState(root, paths, new MemorySettingsStore(settings));
            Assert.IsFalse(state.SettingsDirty, $"{name}: loaded values are not an edit.");

            state.ResetSettings();

            Assert.IsTrue(state.SettingsDirty, $"Restore defaults changed {name}; the save bar must offer to keep it.");
        }
    }

    [TestMethod]
    public void RestoredDefaultsAreStillDefaultAfterSavingAndRestarting()
    {
        var root = TempRoot();
        var paths = new IsolatedCompanionPathProvider(root);
        var coachHome = Path.Combine(paths.Documents, "iRacing Coach");
        JsonSettingsStore Store() => new(Path.Combine(coachHome, "settings.json"), new DisabledGarage61CredentialStore(Path.Combine(root, "credentials")),
            Path.Combine(root, "machine-settings.json"), paths, lockToProviderRoots: false);
        var seed = new CompanionSettings(paths);
        LiveMonitorLayouts.Create(seed.LiveMonitor);
        seed.LiveMonitor.IsLocked = false;
        seed.LiveMonitor.Left = 120;
        seed.LiveMonitor.Top = 80;
        seed.LiveMonitor.OverallScale = 1.2;
        seed.LiveMonitor.MonitorDeviceName = @"\\.\DISPLAY2";
        seed.DiagnosticIncludeConfounded = true;
        Store().Save(seed);

        using (var state = NewSettingsState(root, paths, Store()))
        {
            Assert.IsNotEmpty(state.Settings.LiveMonitor.UserLayouts);
            Assert.IsFalse(state.SettingsDirty);
            state.ResetSettings();
            Assert.IsTrue(state.SettingsDirty, "Only dashboards, placement and diagnostics changed; the reset is still unsaved.");
            state.SaveSettings();
            Assert.AreEqual("Settings saved.", state.SettingsMessage);
            Assert.IsFalse(state.SettingsDirty);
        }

        var reloaded = Store().Load();
        Assert.IsEmpty(reloaded.LiveMonitor.UserLayouts);
        Assert.AreEqual(LiveMonitorLayout.FactoryDefaultId, reloaded.LiveMonitor.ActiveLayoutId);
        Assert.IsTrue(reloaded.LiveMonitor.IsLocked);
        Assert.IsNull(reloaded.LiveMonitor.Left);
        Assert.IsNull(reloaded.LiveMonitor.Top);
        Assert.AreEqual(1d, reloaded.LiveMonitor.OverallScale);
        Assert.AreEqual(string.Empty, reloaded.LiveMonitor.MonitorDeviceName);
        Assert.IsFalse(reloaded.DiagnosticIncludeConfounded);
    }

    [TestMethod]
    public async Task AQuietSaveOfOtherStateKeepsThePendingEditOnTheSaveBar()
    {
        using var fixture = new SettingsFixture("Restored 25 Coach items.");
        var state = fixture.State;
        var original = state.Settings.UseReducedMotion;
        state.Settings.UseReducedMotion = !original;
        Assert.IsTrue(state.SettingsDirty);

        // The Live page saves its chart history by itself while the Settings edit is pending.
        state.Settings.LiveMonitor.HistoryLaps = 5;
        state.SaveLiveMonitorPreferences();
        Assert.IsTrue(state.SettingsDirty, "A quiet save of popout preferences must not hide the save bar mid-edit.");
        StringAssert.Contains(await RenderSettings(state), "data-settings-savebar");

        await state.DisconnectGarage61Async();
        Assert.IsTrue(state.SettingsDirty, "Disconnecting Garage61 is not a save of the Settings page.");

        state.Settings.UseReducedMotion = original;
        Assert.IsTrue(state.SettingsDirty, "The quiet save wrote the edit to the file, so undoing it is itself unsaved.");
        state.SaveSettings();
        Assert.IsFalse(state.SettingsDirty);

        // State the app saves by itself never raises the Settings save bar.
        state.Settings.LiveMonitor.HistoryLaps = 7;
        state.SaveLiveMonitorPreferences();
        Assert.IsFalse(state.SettingsDirty);
    }

    private static CompanionState NewSettingsState(string root, ICompanionPathProvider paths, ISettingsStore store) =>
        new(new QuietBackend(), store, new DisconnectedLiveTelemetrySource(), new DisabledCoachEngineSupervisor(),
            new DisabledGarage61CredentialStore(Path.Combine(root, "credentials")), new RestoredArchive("Restored 1 Coach item."), paths,
            allowExternalHostActions: false);
}
