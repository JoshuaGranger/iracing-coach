using System.Text.Json;
using System.Text.Json.Nodes;
using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

public sealed partial class CompanionState
{
    private static readonly JsonSerializerOptions SettingsSnapshotOptions = new(JsonSerializerDefaults.Web);
    private const string MachineLocalSnapshotKey = "$machineLocal";

    // Everything the settings files held after the last load or write, including quiet saves.
    private JsonObject? _writtenSettings;
    // The Settings page values the driver last saved (or that were loaded). A quiet save of
    // unrelated state writes the file but does not confirm edits still pending on the page.
    private SettingsEdits? _confirmedSettingsEdits;
    private JsonObject? _settingsSaveAttempt;

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
    /// Everything the settings files persist: settings.json plus the popout placement the
    /// store keeps in its machine-local file. Restore defaults reaches all of it.
    /// </summary>
    private static JsonObject PersistedSettings(CompanionSettings settings)
    {
        var snapshot = JsonSerializer.SerializeToNode(settings, SettingsSnapshotOptions)?.AsObject() ?? new JsonObject();
        var monitor = settings.LiveMonitor;
        snapshot[MachineLocalSnapshotKey] = new JsonObject
        {
            ["left"] = monitor?.Left,
            ["top"] = monitor?.Top,
            ["overallScale"] = monitor?.OverallScale,
            ["monitorDeviceName"] = monitor?.MonitorDeviceName,
            ["placementRecoveredAt"] = monitor?.PlacementRecoveredAt
        };
        return snapshot;
    }

    /// <summary>
    /// True while anything the settings files persist differs from what they hold, or a
    /// Settings page value differs from the one the driver last saved. Restoring Coach data
    /// or checking folders is status, not an edit.
    /// </summary>
    public bool SettingsDirty =>
        _writtenSettings is { } written && _confirmedSettingsEdits is { } confirmed
        && (confirmed != SettingsEdits.Of(Settings) || !JsonNode.DeepEquals(written, PersistedSettings(Settings)));

    /// <summary>What the save bar says about the pending edits.</summary>
    public string SettingsEditMessage =>
        !SettingsWritable ? Settings.Compatibility.Message
        : _settingsSaveAttempt is { } attempt && JsonNode.DeepEquals(attempt, PersistedSettings(Settings)) ? SettingsMessage
        : "Save to keep these changes.";

    /// <summary>The files now hold the current values, and the driver confirmed the page.</summary>
    private void MarkSettingsSaved()
    {
        _writtenSettings = PersistedSettings(Settings);
        _confirmedSettingsEdits = SettingsEdits.Of(Settings);
    }

    /// <summary>
    /// A write made for another reason (popout placement, dashboards, coaching threads,
    /// connections). The files now hold the current values, but Settings page edits the driver
    /// has not saved stay pending, so the save bar does not disappear mid-edit.
    /// </summary>
    private void MarkSettingsWrittenQuietly() => _writtenSettings = PersistedSettings(Settings);

    /// <summary>Remembers the values of a save attempt so a refusal can say why.</summary>
    private void MarkSettingsSaveAttempt() => _settingsSaveAttempt = PersistedSettings(Settings);

    /// <summary>A folder the app detected by itself is not a driver's edit.</summary>
    private void AdoptDetectedInstallRoot()
    {
        var detected = Settings.IRacingInstallRoot ?? string.Empty;
        if (_confirmedSettingsEdits is { } confirmed)
            _confirmedSettingsEdits = confirmed with { IRacingInstallRoot = detected };
        if (_writtenSettings?.DeepClone().AsObject() is { } written)
        {
            written[JsonNamingPolicy.CamelCase.ConvertName(nameof(CompanionSettings.IRacingInstallRoot))] = detected;
            _writtenSettings = written;
        }
    }
}
