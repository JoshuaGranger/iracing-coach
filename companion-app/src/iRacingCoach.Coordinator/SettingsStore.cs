using System.Text.Json;
using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

public interface ISettingsStore
{
    CompanionSettings Load();
    void Save(CompanionSettings settings);
}

public sealed class SettingsCompatibilityException(string message, int? schemaVersion = null) : IOException(message)
{
    public int? SchemaVersion { get; } = schemaVersion;
}

public sealed class JsonSettingsStore : ISettingsStore
{
    public const int CurrentSchemaVersion = 5;
    private static readonly HashSet<string> LegacySettingsFields = new(StringComparer.Ordinal)
    {
        "coachHome", "iRacingRoot", "iRacingInstallRoot", "garage61ApiKey", "firstRunComplete",
        "coachThreadIds", "launchAtSignIn", "useReducedMotion", "themeColor", "customThemeColor",
        "diagnosticIncludeConfounded", "liveMonitor", "raceAnalysisTraces", "raceAnalysisTraceLayouts"
    };
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        WriteIndented = true
    };

    private readonly object _saveGate = new();
    private readonly string _path;
    private readonly string _machinePath;
    private readonly IGarage61CredentialStore _credentials;
    private readonly bool _allowDesktopImport;
    private readonly ICompanionPathProvider _pathProvider;
    private readonly bool _lockToProviderRoots;
    private MachineSettings? _machineSettingsSnapshot;
    private bool _machineSettingsWritable = true;

    public JsonSettingsStore() : this(Path.Combine(
        CompanionSettings.DefaultCoachHome,
        "settings.json"), new PowerShellGarage61CredentialStore(), allowDesktopImport: true,
        machinePath: Path.Combine(WindowsCompanionPathProvider.Instance.LocalApplicationData, "iRacingCoach", "machine-settings.json"),
        WindowsCompanionPathProvider.Instance, lockToProviderRoots: false)
    {
    }

    public JsonSettingsStore(string path, IGarage61CredentialStore? credentials = null)
        : this(path, credentials ?? new PowerShellGarage61CredentialStore(), allowDesktopImport: false,
            machinePath: path + ".machine-local.json", WindowsCompanionPathProvider.Instance, lockToProviderRoots: false)
    {
    }

    public JsonSettingsStore(string path, IGarage61CredentialStore credentials, string machinePath)
        : this(path, credentials, allowDesktopImport: false, machinePath, WindowsCompanionPathProvider.Instance, lockToProviderRoots: false)
    {
    }

    public JsonSettingsStore(
        string path,
        IGarage61CredentialStore credentials,
        string machinePath,
        ICompanionPathProvider pathProvider,
        bool lockToProviderRoots)
        : this(path, credentials, allowDesktopImport: false, machinePath, pathProvider, lockToProviderRoots)
    {
    }

    private JsonSettingsStore(
        string path,
        IGarage61CredentialStore credentials,
        bool allowDesktopImport,
        string machinePath,
        ICompanionPathProvider pathProvider,
        bool lockToProviderRoots)
    {
        _path = Path.GetFullPath(path);
        _machinePath = Path.GetFullPath(machinePath);
        _credentials = credentials ?? throw new ArgumentNullException(nameof(credentials));
        _allowDesktopImport = allowDesktopImport;
        _pathProvider = pathProvider ?? throw new ArgumentNullException(nameof(pathProvider));
        _lockToProviderRoots = lockToProviderRoots;
    }

    public CompanionSettings Load()
    {
        try
        {
            var sourceBytes = File.Exists(_path) ? File.ReadAllBytes(_path) : null;
            var serialized = sourceBytes is not null ? File.ReadAllText(_path) : null;
            var sourceVersion = sourceBytes is not null ? RequireReadableSchemaVersion(sourceBytes) : CurrentSchemaVersion;
            var settings = sourceBytes is not null
                ? JsonSerializer.Deserialize<CompanionSettings>(sourceBytes, JsonOptions) ?? new CompanionSettings(_pathProvider)
                : new CompanionSettings(_pathProvider);
            settings.Compatibility = SettingsCompatibilityState.Current(sourceVersion);
            var legacyCredential = string.Empty;
            var legacyCredentialPresent = !_lockToProviderRoots && serialized is not null && TryReadLegacyGarage61Credential(serialized, out legacyCredential);
            if (legacyCredentialPresent) settings.Garage61ApiKey = legacyCredential;
            if (_lockToProviderRoots) settings.Garage61ApiKey = string.Empty;

            settings.CoachHome = Path.GetDirectoryName(_path) ?? new CompanionSettings(_pathProvider).CoachHome;
            ApplyPathPolicy(settings);
            settings.LiveMonitor ??= new LiveMonitorLayout();
            settings.RaceAnalysisTraces ??= new AnalysisTraceLayout();
            settings.RaceAnalysisTraceLayouts ??= new AnalysisTraceLayoutSet();
            var normalizedThemeColor = ThemeColors.Normalize(settings.ThemeColor);
            var repairedThemeColor = !string.Equals(settings.ThemeColor, normalizedThemeColor, StringComparison.Ordinal);
            settings.ThemeColor = normalizedThemeColor;
            var normalizedCustomThemeColor = ThemeColors.NormalizeCustomHex(settings.CustomThemeColor);
            var repairedCustomThemeColor = !string.Equals(settings.CustomThemeColor, normalizedCustomThemeColor, StringComparison.Ordinal);
            settings.CustomThemeColor = normalizedCustomThemeColor;
            var repairedAnalysisTraces = AnalysisTraceLayouts.ValidateAndRepair(settings.RaceAnalysisTraces);
            var repairedAnalysisTraceLayouts = AnalysisTraceLayoutSets.ValidateAndRepair(settings.RaceAnalysisTraceLayouts, settings.RaceAnalysisTraces);
            var migratedMonitor = serialized is not null && settings.SettingsSchemaVersion < 4 && TryMigrateLegacyMonitor(serialized, settings.LiveMonitor);
            var migratedMachineLayout = serialized is not null && !File.Exists(_machinePath) && TryReadLegacyMachineLayout(serialized, settings.LiveMonitor);
            ApplyMachineSettings(settings.LiveMonitor);
            var repairedMonitor = LiveMonitorLayouts.ValidateAndRepair(settings.LiveMonitor, out var monitorCorruption);
            if ((migratedMonitor || monitorCorruption) && serialized is not null) PreserveLegacyMonitor(serialized, monitorCorruption ? "rejected" : "v0.9.3");
            try
            {
                var migrated = TryMigrateGarage61Credential(settings);
                var schemaMigrated = sourceVersion < CurrentSchemaVersion;
                settings.SettingsSchemaVersion = CurrentSchemaVersion;
                if (schemaMigrated && !legacyCredentialPresent && sourceBytes is not null)
                    PreservePreMigrationSettings(sourceBytes);
                if (migrated || legacyCredentialPresent || migratedMachineLayout || migratedMonitor || repairedMonitor || repairedAnalysisTraces || repairedAnalysisTraceLayouts || repairedThemeColor || repairedCustomThemeColor || schemaMigrated) Save(settings);
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or InvalidOperationException or ArgumentException or TimeoutException or PlatformNotSupportedException) { }
            return settings;
        }
        catch (SettingsCompatibilityException ex)
        {
            return ReadOnlySettings(ex.SchemaVersion, ex.Message);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException)
        {
            return File.Exists(_path)
                ? ReadOnlySettings(null, "The settings file is unreadable or incompatible. The app started without changing it; copy the Coach folder before repairing or replacing settings.json.")
                : new CompanionSettings(_pathProvider) { Compatibility = SettingsCompatibilityState.Current(CurrentSchemaVersion) };
        }
    }

    public void Save(CompanionSettings settings)
    {
        lock (_saveGate)
        {
            if (!settings.Compatibility.Writable)
                throw new SettingsCompatibilityException(settings.Compatibility.Message, settings.Compatibility.SchemaVersion);
            ApplyPathPolicy(settings);
            if (_lockToProviderRoots)
            {
                settings.Garage61ApiKey = string.Empty;
            }
            else if (!string.IsNullOrWhiteSpace(settings.Garage61ApiKey))
            {
                _credentials.Store(settings.Garage61ApiKey);
                settings.Garage61ApiKey = string.Empty;
            }
            settings.ThemeColor = ThemeColors.Normalize(settings.ThemeColor);
            settings.CustomThemeColor = ThemeColors.NormalizeCustomHex(settings.CustomThemeColor);
            settings.SettingsSchemaVersion = CurrentSchemaVersion;
            _ = LiveMonitorLayouts.ValidateAndRepair(settings.LiveMonitor, out _);
            settings.RaceAnalysisTraces ??= new AnalysisTraceLayout();
            settings.RaceAnalysisTraceLayouts ??= new AnalysisTraceLayoutSet();
            _ = AnalysisTraceLayoutSets.ValidateAndRepair(settings.RaceAnalysisTraceLayouts, settings.RaceAnalysisTraces);
            settings.RaceAnalysisTraces = AnalysisTraceLayoutSets.CloneLayout(
                AnalysisTraceLayoutSets.Active(settings.RaceAnalysisTraceLayouts).Named.Layout);
            SaveMachineSettings(settings.LiveMonitor);
            WriteAtomically(_path, JsonSerializer.Serialize(settings, JsonOptions));
            settings.Compatibility = SettingsCompatibilityState.Current(CurrentSchemaVersion);
        }
    }

    private static int RequireReadableSchemaVersion(byte[] sourceBytes)
    {
        using var document = JsonDocument.Parse(sourceBytes);
        var root = document.RootElement;
        if (root.ValueKind != JsonValueKind.Object)
            throw new SettingsCompatibilityException("The settings file must be a JSON object. It was left unchanged.");
        if (!root.TryGetProperty("settingsSchemaVersion", out var versionValue))
        {
            if (root.EnumerateObject().Any(property => LegacySettingsFields.Contains(property.Name))) return 1;
            throw new SettingsCompatibilityException("The settings file requires a settingsSchemaVersion or a recognized legacy settings field. It was left unchanged.");
        }
        if (versionValue.ValueKind != JsonValueKind.Number
            || !versionValue.TryGetInt32(out var version)
            || version < 1)
            throw new SettingsCompatibilityException("The settings file requires a positive integer settingsSchemaVersion. It was left unchanged.");
        if (version > CurrentSchemaVersion)
            throw new SettingsCompatibilityException(
                $"This settings file uses schema {version}, but this app supports up to {CurrentSchemaVersion}. The app is available read-only and settings.json was left unchanged.",
                version);
        return version;
    }

    private CompanionSettings ReadOnlySettings(int? version, string message)
    {
        var settings = new CompanionSettings(_pathProvider)
        {
            CoachHome = Path.GetDirectoryName(_path) ?? new CompanionSettings(_pathProvider).CoachHome,
            Compatibility = SettingsCompatibilityState.ReadOnly(version, message)
        };
        ApplyPathPolicy(settings);
        return settings;
    }

    private void PreservePreMigrationSettings(byte[] sourceBytes)
    {
        var backup = _path + $".before-schema-{CurrentSchemaVersion}.backup.json";
        if (File.Exists(backup)) return;
        var directory = Path.GetDirectoryName(backup) ?? throw new InvalidOperationException("The settings backup path has no parent directory.");
        Directory.CreateDirectory(directory);
        var temporary = backup + $".{Guid.NewGuid():N}.tmp";
        try
        {
            File.WriteAllBytes(temporary, sourceBytes);
            File.Move(temporary, backup, overwrite: false);
        }
        catch (IOException) when (File.Exists(backup)) { }
        finally
        {
            try { File.Delete(temporary); }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
        }
    }

    private void ApplyMachineSettings(LiveMonitorLayout layout)
    {
        if (!File.Exists(_machinePath)) return;
        var local = ReadMachineSettings();
        if (local is null) return;
        layout.Left = local.LiveMonitor.Left;
        layout.Top = local.LiveMonitor.Top;
        layout.OverallScale = local.LiveMonitor.OverallScale is >= .7 and <= 2
            ? local.LiveMonitor.OverallScale
            : local.LiveMonitor.Width is > 0
                ? Math.Clamp(local.LiveMonitor.Width.Value / 560d, .7, 2)
                : 1;
        layout.MonitorDeviceName = local.LiveMonitor.MonitorDeviceName;
        layout.PlacementRecoveredAt = local.LiveMonitor.PlacementRecoveredAt;
    }

    private MachineSettings? ReadMachineSettings()
    {
        if (_machineSettingsSnapshot is not null) return _machineSettingsSnapshot;
        try
        {
            var sourceBytes = File.ReadAllBytes(_machinePath);
            using var document = JsonDocument.Parse(sourceBytes);
            var root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object
                || !root.TryGetProperty("schemaVersion", out var versionValue)
                || versionValue.ValueKind != JsonValueKind.Number
                || !versionValue.TryGetInt32(out var version)
                || version is < 1 or > MachineSettings.CurrentSchemaVersion)
            {
                _machineSettingsWritable = false;
                return null;
            }
            var local = JsonSerializer.Deserialize<MachineSettings>(sourceBytes, JsonOptions);
            if (local?.LiveMonitor is null)
            {
                _machineSettingsWritable = false;
                return null;
            }
            _machineSettingsSnapshot = local;
            return local;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException)
        {
            _machineSettingsWritable = false;
            return null;
        }
    }

    private void SaveMachineSettings(LiveMonitorLayout layout)
    {
        if (_machineSettingsSnapshot is null && File.Exists(_machinePath)) _ = ReadMachineSettings();
        if (!_machineSettingsWritable) return;
        var local = _machineSettingsSnapshot ?? new MachineSettings();
        local.SchemaVersion = MachineSettings.CurrentSchemaVersion;
        local.LiveMonitor.Left = layout.Left;
        local.LiveMonitor.Top = layout.Top;
        local.LiveMonitor.OverallScale = Math.Clamp(layout.OverallScale, .7, 2);
        local.LiveMonitor.MonitorDeviceName = layout.MonitorDeviceName;
        local.LiveMonitor.PlacementRecoveredAt = layout.PlacementRecoveredAt;
        WriteAtomically(_machinePath, JsonSerializer.Serialize(local, JsonOptions));
        _machineSettingsSnapshot = local;
    }

    private static void WriteAtomically(string path, string contents)
    {
        var directory = Path.GetDirectoryName(path) ?? throw new InvalidOperationException("The settings path has no parent directory.");
        Directory.CreateDirectory(directory);
        var temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            File.WriteAllText(temporary, contents);
            File.Move(temporary, path, overwrite: true);
        }
        finally
        {
            try { File.Delete(temporary); }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
        }
    }

    private bool TryMigrateGarage61Credential(CompanionSettings settings)
    {
        if (!string.IsNullOrWhiteSpace(settings.Garage61ApiKey))
        {
            _credentials.Store(settings.Garage61ApiKey);
            settings.Garage61ApiKey = string.Empty;
            settings.SettingsSchemaVersion = Math.Max(settings.SettingsSchemaVersion, 2);
            return true;
        }
        if (_credentials.IsConfigured || !_allowDesktopImport) return false;

        var legacyPath = Path.Combine(
            _pathProvider.Desktop,
            "garage61-key.txt");
        if (!File.Exists(legacyPath))
        {
            return false;
        }

        var key = File.ReadAllText(legacyPath).Trim();
        if (key.Length == 0 || key.IndexOfAny(['\r', '\n', '\0']) >= 0)
        {
            return false;
        }

        _credentials.Store(key);
        settings.SettingsSchemaVersion = Math.Max(settings.SettingsSchemaVersion, 2);
        return true;
    }

    private void ApplyPathPolicy(CompanionSettings settings)
    {
        settings.LocalStateRootOverride ??= Path.Combine(_pathProvider.LocalApplicationData, "iRacingCoach");
        if (!_lockToProviderRoots) return;

        var defaults = new CompanionSettings(_pathProvider);
        settings.CoachHome = defaults.CoachHome;
        settings.IRacingRoot = defaults.IRacingRoot;
        settings.IRacingInstallRoot = defaults.IRacingInstallRoot;
        settings.LocalStateRootOverride = defaults.LocalStateRootOverride;
        settings.LaunchAtSignIn = false;
    }

    private static bool TryReadLegacyGarage61Credential(string serialized, out string credential)
    {
        credential = string.Empty;
        using var document = JsonDocument.Parse(serialized);
        if (document.RootElement.ValueKind != JsonValueKind.Object) return false;
        foreach (var property in document.RootElement.EnumerateObject())
        {
            if (!string.Equals(property.Name, nameof(CompanionSettings.Garage61ApiKey), StringComparison.OrdinalIgnoreCase)) continue;
            if (property.Value.ValueKind == JsonValueKind.String) credential = property.Value.GetString() ?? string.Empty;
            return true;
        }
        return false;
    }

    private static bool TryReadLegacyMachineLayout(string serialized, LiveMonitorLayout layout)
    {
        try
        {
            using var document = JsonDocument.Parse(serialized);
            if (!document.RootElement.TryGetProperty("liveMonitor", out var monitor) || monitor.ValueKind != JsonValueKind.Object) return false;
            if (monitor.TryGetProperty("left", out var left) && left.TryGetDouble(out var leftValue)) layout.Left = leftValue;
            if (monitor.TryGetProperty("top", out var top) && top.TryGetDouble(out var topValue)) layout.Top = topValue;
            if (monitor.TryGetProperty("width", out var width) && width.TryGetDouble(out var widthValue)) layout.OverallScale = Math.Clamp(widthValue / 560d, .7, 2);
            if (monitor.TryGetProperty("monitorDeviceName", out var monitorName) && monitorName.ValueKind == JsonValueKind.String)
                layout.MonitorDeviceName = monitorName.GetString() ?? string.Empty;
            return true;
        }
        catch (JsonException)
        {
            return false;
        }
    }

    private static bool TryMigrateLegacyMonitor(string serialized, LiveMonitorLayout layout)
    {
        try
        {
            using var document = JsonDocument.Parse(serialized);
            if (!document.RootElement.TryGetProperty("liveMonitor", out var monitor) || monitor.ValueKind != JsonValueKind.Object) return false;
            if (monitor.TryGetProperty("positionLocked", out var locked) && locked.ValueKind is JsonValueKind.True or JsonValueKind.False)
                layout.IsLocked = locked.GetBoolean();
            if (!monitor.TryGetProperty("secondaryFields", out var fields) || fields.ValueKind != JsonValueKind.Array) return true;
            var mapped = fields.EnumerateArray()
                .Where(item => item.ValueKind == JsonValueKind.String)
                .Select(item => LegacyMetric(item.GetString()))
                .Where(item => item is not null)
                .Cast<string>()
                .Distinct(StringComparer.Ordinal)
                .ToArray();
            if (mapped.Length == 0) return true;
            var rows = Math.Clamp((int)Math.Ceiling(mapped.Length / 3d), 1, 8);
            var migrated = new LiveMonitorNamedLayout { Name = "Migrated 0.9.3", Rows = rows, Columns = 3 };
            for (var index = 0; index < mapped.Length; index++)
            {
                var definition = LiveTelemetryCatalog.Get(mapped[index]);
                migrated.Tiles.Add(new LiveMonitorTile
                {
                    MetricId = definition.Id,
                    Row = index / 3,
                    Column = index % 3,
                    DisplayStyle = definition.DefaultStyle,
                    Unit = definition.DefaultUnit,
                    Precision = definition.DefaultPrecision
                });
            }
            layout.UserLayouts.Add(migrated);
            layout.ActiveLayoutId = migrated.Id;
            return true;
        }
        catch (JsonException)
        {
            return false;
        }
    }

    private static string? LegacyMetric(string? value) => value?.ToLowerInvariant() switch
    {
        "leaderlap" => "leader-last-lap",
        "gaptrends" => "ahead-gap",
        "tirephase" => "tire-phase",
        "fuel" => "fuel",
        "weather" => "track-temperature",
        "repairs" => "mandatory-repair",
        "adjustment" => "brake-bias",
        _ => null
    };

    private void PreserveLegacyMonitor(string serialized, string label)
    {
        try
        {
            using var document = JsonDocument.Parse(serialized);
            if (!document.RootElement.TryGetProperty("liveMonitor", out var monitor)) return;
            var supportPath = _machinePath + $".{label}-monitor.json";
            if (File.Exists(supportPath)) return;
            var directory = Path.GetDirectoryName(supportPath) ?? throw new InvalidOperationException("The support path has no parent directory.");
            Directory.CreateDirectory(directory);
            var temporary = supportPath + ".tmp";
            File.WriteAllText(temporary, JsonSerializer.Serialize(monitor, JsonOptions));
            File.Move(temporary, supportPath, overwrite: false);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException or InvalidOperationException) { }
    }

    private sealed class MachineSettings
    {
        public const int CurrentSchemaVersion = 2;
        public int SchemaVersion { get; set; } = CurrentSchemaVersion;
        public MachineMonitorPlacement LiveMonitor { get; set; } = new();
        [System.Text.Json.Serialization.JsonExtensionData]
        public Dictionary<string, JsonElement>? ExtensionData { get; set; }
    }

    private sealed class MachineMonitorPlacement
    {
        public double? Left { get; set; }
        public double? Top { get; set; }
        public double OverallScale { get; set; } = 1;
        public double? Width { get; set; }
        public string MonitorDeviceName { get; set; } = string.Empty;
        public DateTimeOffset? PlacementRecoveredAt { get; set; }
        [System.Text.Json.Serialization.JsonExtensionData]
        public Dictionary<string, JsonElement>? ExtensionData { get; set; }
    }
}
