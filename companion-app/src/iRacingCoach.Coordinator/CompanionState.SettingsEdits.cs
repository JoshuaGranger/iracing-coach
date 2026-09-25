using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

public sealed partial class CompanionState
{
    private SettingsEdits? _savedSettingsEdits;
    private SettingsEdits? _settingsSaveAttempt;

    /// <summary>The preferences the Settings page edits, compared by value.</summary>
    private sealed record SettingsEdits(
        string IRacingRoot,
        string IRacingInstallRoot,
        string ThemeColor,
        string CustomThemeColor,
        bool LaunchAtSignIn,
        bool UseReducedMotion,
        string GlobalHotkey,
        bool SafeGlanceEnabled,
        bool ReopenOnConnect)
    {
        public static SettingsEdits Of(CompanionSettings settings) => new(
            settings.IRacingRoot ?? string.Empty,
            settings.IRacingInstallRoot ?? string.Empty,
            settings.ThemeColor ?? string.Empty,
            settings.CustomThemeColor ?? string.Empty,
            settings.LaunchAtSignIn,
            settings.UseReducedMotion,
            settings.LiveMonitor?.GlobalHotkey ?? string.Empty,
            settings.LiveMonitor?.SafeGlanceEnabled ?? false,
            settings.LiveMonitor?.ReopenOnConnect ?? false);
    }

    /// <summary>
    /// True only while a Settings value differs from what was loaded or last written.
    /// Restoring Coach data or checking folders is status, not an edit.
    /// </summary>
    public bool SettingsDirty => _savedSettingsEdits is { } saved && saved != SettingsEdits.Of(Settings);

    /// <summary>What the save bar says about the pending edits.</summary>
    public string SettingsEditMessage =>
        !SettingsWritable ? Settings.Compatibility.Message
        : _settingsSaveAttempt is { } attempt && attempt == SettingsEdits.Of(Settings) ? SettingsMessage
        : "Save to keep these changes.";

    /// <summary>Records the current values as the saved ones.</summary>
    private void MarkSettingsSaved() => _savedSettingsEdits = SettingsEdits.Of(Settings);

    /// <summary>Remembers the values of a save attempt so a refusal can say why.</summary>
    private void MarkSettingsSaveAttempt() => _settingsSaveAttempt = SettingsEdits.Of(Settings);

    /// <summary>A folder the app detected by itself is not a driver's edit.</summary>
    private void AdoptDetectedInstallRoot()
    {
        if (_savedSettingsEdits is { } saved)
            _savedSettingsEdits = saved with { IRacingInstallRoot = Settings.IRacingInstallRoot ?? string.Empty };
    }
}
