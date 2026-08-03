using System.Text.Json;
using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

public interface ISettingsStore
{
    CompanionSettings Load();
    void Save(CompanionSettings settings);
}

public sealed class JsonSettingsStore : ISettingsStore
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        WriteIndented = true
    };

    private readonly string _path;
    private readonly string _machinePath;
    private readonly IGarage61CredentialStore _credentials;
    private readonly bool _allowDesktopImport;

    public JsonSettingsStore() : this(Path.Combine(
        CompanionSettings.DefaultCoachHome,
        "settings.json"), new PowerShellGarage61CredentialStore(), allowDesktopImport: true,
        machinePath: Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "iRacingCoach", "machine-settings.json"))
    {
    }

    public JsonSettingsStore(string path, IGarage61CredentialStore? credentials = null)
        : this(path, credentials ?? new PowerShellGarage61CredentialStore(), allowDesktopImport: false,
            machinePath: path + ".machine-local.json")
    {
    }

    public JsonSettingsStore(string path, IGarage61CredentialStore credentials, string machinePath)
        : this(path, credentials, allowDesktopImport: false, machinePath)
    {
    }

    private JsonSettingsStore(string path, IGarage61CredentialStore credentials, bool allowDesktopImport, string machinePath)
    {
        _path = path;
        _machinePath = machinePath;
        _credentials = credentials;
        _allowDesktopImport = allowDesktopImport;
    }

    public CompanionSettings Load()
    {
        try
        {
            var serialized = File.Exists(_path) ? File.ReadAllText(_path) : null;
            var settings = serialized is not null
                ? JsonSerializer.Deserialize<CompanionSettings>(serialized, JsonOptions) ?? new CompanionSettings()
                : new CompanionSettings();
            var legacyCredential = string.Empty;
            var legacyCredentialPresent = serialized is not null && TryReadLegacyGarage61Credential(serialized, out legacyCredential);
            if (legacyCredentialPresent) settings.Garage61ApiKey = legacyCredential;

            settings.CoachHome = Path.GetDirectoryName(_path) ?? CompanionSettings.DefaultCoachHome;
            settings.LiveMonitor ??= new LiveMonitorLayout();
            var migratedMachineLayout = serialized is not null && !File.Exists(_machinePath) && TryReadLegacyMachineLayout(serialized, settings.LiveMonitor);
            ApplyMachineSettings(settings.LiveMonitor);
            try
            {
                var migrated = TryMigrateGarage61Credential(settings);
                var schemaMigrated = settings.SettingsSchemaVersion < 3;
                settings.SettingsSchemaVersion = Math.Max(settings.SettingsSchemaVersion, 3);
                if (migrated || legacyCredentialPresent || migratedMachineLayout || schemaMigrated) Save(settings);
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or InvalidOperationException or ArgumentException or TimeoutException or PlatformNotSupportedException) { }
            return settings;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException)
        {
            return new CompanionSettings();
        }
    }

    public void Save(CompanionSettings settings)
    {
        if (!string.IsNullOrWhiteSpace(settings.Garage61ApiKey))
        {
            _credentials.Store(settings.Garage61ApiKey);
            settings.Garage61ApiKey = string.Empty;
        }
        settings.SettingsSchemaVersion = Math.Max(settings.SettingsSchemaVersion, 3);
        SaveMachineSettings(settings.LiveMonitor);
        var directory = Path.GetDirectoryName(_path) ?? throw new InvalidOperationException("The settings path has no parent directory.");
        Directory.CreateDirectory(directory);
        var temporary = _path + ".tmp";
        File.WriteAllText(temporary, JsonSerializer.Serialize(settings, JsonOptions));
        File.Move(temporary, _path, overwrite: true);
    }

    private void ApplyMachineSettings(LiveMonitorLayout layout)
    {
        if (!File.Exists(_machinePath)) return;
        try
        {
            var local = JsonSerializer.Deserialize<MachineSettings>(File.ReadAllText(_machinePath), JsonOptions);
            if (local is null) return;
            layout.Left = local.LiveMonitor.Left;
            layout.Top = local.LiveMonitor.Top;
            layout.Width = local.LiveMonitor.Width;
            layout.Height = local.LiveMonitor.Height;
            layout.MonitorDeviceName = local.LiveMonitor.MonitorDeviceName;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException) { }
    }

    private void SaveMachineSettings(LiveMonitorLayout layout)
    {
        var local = new MachineSettings
        {
            LiveMonitor = new MachineMonitorPlacement
            {
                Left = layout.Left,
                Top = layout.Top,
                Width = layout.Width,
                Height = layout.Height,
                MonitorDeviceName = layout.MonitorDeviceName
            }
        };
        var directory = Path.GetDirectoryName(_machinePath) ?? throw new InvalidOperationException("The machine settings path has no parent directory.");
        Directory.CreateDirectory(directory);
        var temporary = _machinePath + ".tmp";
        File.WriteAllText(temporary, JsonSerializer.Serialize(local, JsonOptions));
        File.Move(temporary, _machinePath, overwrite: true);
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
            Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
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
            if (monitor.TryGetProperty("width", out var width) && width.TryGetDouble(out var widthValue)) layout.Width = widthValue;
            if (monitor.TryGetProperty("height", out var height) && height.TryGetDouble(out var heightValue)) layout.Height = heightValue;
            if (monitor.TryGetProperty("monitorDeviceName", out var monitorName) && monitorName.ValueKind == JsonValueKind.String)
                layout.MonitorDeviceName = monitorName.GetString() ?? string.Empty;
            return true;
        }
        catch (JsonException)
        {
            return false;
        }
    }

    private sealed class MachineSettings
    {
        public int SchemaVersion { get; set; } = 1;
        public MachineMonitorPlacement LiveMonitor { get; set; } = new();
    }

    private sealed class MachineMonitorPlacement
    {
        public double? Left { get; set; }
        public double? Top { get; set; }
        public double Width { get; set; } = 560;
        public double Height { get; set; } = 275;
        public string MonitorDeviceName { get; set; } = string.Empty;
    }
}
