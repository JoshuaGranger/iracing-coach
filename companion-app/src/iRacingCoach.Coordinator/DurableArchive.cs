using System.Collections.Concurrent;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace iRacingCoach.Coordinator;

public sealed record ArchiveComponentSummary(string Path, int FileCount, long Bytes, string Sha256);

public sealed record ArchiveMigration(
    int FromVersion,
    int ToVersion,
    DateTimeOffset StartedUtc,
    DateTimeOffset CompletedUtc,
    string Status,
    string Detail);

public sealed record UnresolvedSourceReference(string StableId, string FileName, string Reason);

public sealed class ArchiveManifest
{
    public int SchemaVersion { get; set; } = DurableArchiveService.CurrentSchemaVersion;
    public string ArchiveId { get; set; } = Guid.NewGuid().ToString("N");
    public DateTimeOffset CreatedUtc { get; set; } = DateTimeOffset.UtcNow;
    public DateTimeOffset UpdatedUtc { get; set; } = DateTimeOffset.UtcNow;
    public string AppVersion { get; set; } = string.Empty;
    public string BackendVersion { get; set; } = string.Empty;
    public DateTimeOffset? LastIntegrityCheckUtc { get; set; }
    public string IntegritySha256 { get; set; } = string.Empty;
    public List<string> ExpectedDirectories { get; set; } = [];
    public List<ArchiveComponentSummary> Components { get; set; } = [];
    public List<ArchiveMigration> MigrationHistory { get; set; } = [];
    public List<UnresolvedSourceReference> UnresolvedSources { get; set; } = [];
}

public sealed record ArchiveRestoreSummary(
    int Reports,
    int RaceRecords,
    int SeasonalBundles,
    int Setups,
    int TuningExperiments,
    int StrategyRecords,
    int Garage61Files,
    int AiCoachingFiles,
    int TelemetryTraces,
    int TireModels,
    int DriverModels,
    int TargetLaps,
    int UnresolvedSources)
{
    public int TotalItems => Reports + RaceRecords + SeasonalBundles + Setups + TuningExperiments + StrategyRecords + Garage61Files + AiCoachingFiles + TelemetryTraces + TireModels + DriverModels + TargetLaps;
}

public sealed record ArchiveStatus(
    bool Compatible,
    bool? IntegrityVerified,
    int SchemaVersion,
    string ArchiveId,
    string Root,
    ArchiveRestoreSummary Restored,
    DateTimeOffset? LastIntegrityCheckUtc,
    string Message);

public sealed record BackupPreparationResult(
    bool SafeToCopy,
    string Root,
    DateTimeOffset CheckedUtc,
    int FileCount,
    long Bytes,
    string IntegritySha256,
    int UnresolvedSources,
    IReadOnlyList<string> BlockingActivity,
    string Message);

public sealed record PortableCoachingRecord(
    string Id,
    DateTimeOffset CreatedUtc,
    string WorkflowKey,
    string Question,
    string Response);

public sealed class ArchiveCompatibilityException(string message) : IOException(message);

public interface IDurableArchiveService
{
    ArchiveStatus Initialize(string root, string appVersion, string backendVersion = "unknown");
    BackupPreparationResult PrepareForCopy(string root, string appVersion, string backendVersion, IReadOnlyList<string>? blockingActivity = null);
    void MarkActive(string root);
}

/// <summary>
/// Owns the versioned, relocatable contract for Documents\iRacing Coach.
/// All writes are atomic. Existing artifacts are indexed but never deleted or
/// rewritten by archive initialization.
/// </summary>
public sealed class DurableArchiveService : IDurableArchiveService
{
    public const int CurrentSchemaVersion = 1;
    public const string ManifestFileName = "archive-manifest.json";
    public const string PortableStateFileName = "portable-state.json";

    public static readonly IReadOnlyList<string> DurableDirectories =
    [
        "data/reports",
        "data/race-index",
        "data/analysis-cache",
        "data/season-cache",
        "data/garage61",
        "data/track-geometry",
        "data/telemetry-traces",
        "data/strategy-history",
        "data/setup-history",
        "data/tuning-experiments",
        "data/tire-models",
        "data/driver-models",
        "data/target-laps",
        "data/ai-coaching",
        "data/activity-history",
        "data/tuning",
        "data/auth",
        "user-library",
        "portable-settings",
        "exports",
        "backups",
        "setups"
    ];

    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web)
    {
        WriteIndented = true
    };
    private static readonly ConcurrentDictionary<string, object> AtomicWriteGates = new(StringComparer.OrdinalIgnoreCase);

    public ArchiveStatus Initialize(string root, string appVersion, string backendVersion = "unknown")
    {
        var canonicalRoot = CanonicalRoot(root);
        var manifestPath = Path.Combine(canonicalRoot, ManifestFileName);
        var manifest = ReadManifest(manifestPath);
        if (manifest is not null && manifest.SchemaVersion > CurrentSchemaVersion)
        {
            throw new ArchiveCompatibilityException(
                $"This Coach folder uses archive schema {manifest.SchemaVersion}, but this app supports up to {CurrentSchemaVersion}. Install a newer iRacing Coach version. No archive files were changed.");
        }

        Directory.CreateDirectory(canonicalRoot);
        foreach (var relative in DurableDirectories)
            Directory.CreateDirectory(ResolveInside(canonicalRoot, relative));

        var now = DateTimeOffset.UtcNow;
        if (manifest is null)
        {
            manifest = new ArchiveManifest
            {
                AppVersion = appVersion,
                BackendVersion = backendVersion,
                CreatedUtc = now,
                UpdatedUtc = now,
                MigrationHistory =
                [
                    new ArchiveMigration(0, CurrentSchemaVersion, now, now, "complete", "Adopted the existing Coach folder without changing or deleting earlier artifacts.")
                ]
            };
        }
        else if (manifest.SchemaVersion < CurrentSchemaVersion)
        {
            manifest = Migrate(canonicalRoot, manifest, appVersion, backendVersion);
        }

        bool? integrityVerified = null;
        if (!string.IsNullOrWhiteSpace(manifest.IntegritySha256))
        {
            var currentComponents = BuildComponentSummaries(canonicalRoot);
            integrityVerified = string.Equals(AggregateHash(currentComponents), manifest.IntegritySha256, StringComparison.OrdinalIgnoreCase);
        }

        manifest.AppVersion = appVersion;
        manifest.BackendVersion = backendVersion;
        manifest.ExpectedDirectories = DurableDirectories.Select(path => path.Replace('\\', '/')).ToList();
        manifest.UpdatedUtc = now;
        WriteAtomic(manifestPath, manifest);
        WritePortableState(canonicalRoot, manifest, safeToCopy: false, "Archive is active. Use Prepare Backup / Migration Copy before copying it.");

        var restored = BuildRestoreSummary(canonicalRoot, manifest);
        var restoredMessage = restored.TotalItems == 0 ? "Portable Coach folder is ready." : $"Restored {restored.TotalItems:N0} portable item{(restored.TotalItems == 1 ? string.Empty : "s")}.";
        if (integrityVerified == true) restoredMessage += " The last prepared-copy checksum was verified.";
        if (integrityVerified == false) restoredMessage += " Portable files changed since the last prepared copy; prepare a new copy to refresh its integrity record.";
        return new(true, integrityVerified, manifest.SchemaVersion, manifest.ArchiveId, canonicalRoot, restored, manifest.LastIntegrityCheckUtc, restoredMessage);
    }

    public BackupPreparationResult PrepareForCopy(string root, string appVersion, string backendVersion, IReadOnlyList<string>? blockingActivity = null)
    {
        var canonicalRoot = CanonicalRoot(root);
        var blockers = (blockingActivity ?? []).Where(value => !string.IsNullOrWhiteSpace(value)).Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
        var checkedUtc = DateTimeOffset.UtcNow;
        if (blockers.Length > 0)
        {
            var blocked = new BackupPreparationResult(false, canonicalRoot, checkedUtc, 0, 0, string.Empty, 0, blockers,
                $"Wait for {string.Join(", ", blockers)} to finish, then prepare the folder again.");
            var existing = ReadManifest(Path.Combine(canonicalRoot, ManifestFileName));
            if (existing is not null && existing.SchemaVersion <= CurrentSchemaVersion)
                WritePortableState(canonicalRoot, existing, false, blocked.Message);
            return blocked;
        }

        var initialized = Initialize(canonicalRoot, appVersion, backendVersion);
        var manifestPath = Path.Combine(canonicalRoot, ManifestFileName);
        var manifest = ReadManifest(manifestPath) ?? throw new InvalidDataException("The archive manifest could not be read after initialization.");
        var components = BuildComponentSummaries(canonicalRoot);
        var integrity = AggregateHash(components);
        manifest.Components = components;
        manifest.IntegritySha256 = integrity;
        manifest.LastIntegrityCheckUtc = checkedUtc;
        manifest.UpdatedUtc = checkedUtc;
        manifest.UnresolvedSources = FindUnresolvedSources(canonicalRoot);
        WriteAtomic(manifestPath, manifest);
        WritePortableState(canonicalRoot, manifest, true, $"All durable writes are complete. Copy the entire folder: {canonicalRoot}");

        var fileCount = components.Sum(component => component.FileCount) + 2;
        var bytes = components.Sum(component => component.Bytes);
        return new(true, canonicalRoot, checkedUtc, fileCount, bytes, integrity, manifest.UnresolvedSources.Count, [],
            $"All durable writes are complete. Copy the entire folder: {canonicalRoot}");
    }

    public void MarkActive(string root)
    {
        var canonicalRoot = CanonicalRoot(root);
        var manifest = ReadManifest(Path.Combine(canonicalRoot, ManifestFileName));
        if (manifest is null || manifest.SchemaVersion > CurrentSchemaVersion) return;
        WritePortableState(canonicalRoot, manifest, false, "Archive is active. Close the app or use Prepare Backup / Migration Copy before copying it.");
    }

    private static ArchiveManifest Migrate(string root, ArchiveManifest manifest, string appVersion, string backendVersion)
    {
        var started = DateTimeOffset.UtcNow;
        var migrationRoot = Path.Combine(root, "backups");
        var backup = Path.Combine(migrationRoot, $"archive-manifest-before-schema-{CurrentSchemaVersion}-backup.json");
        var journal = Path.Combine(migrationRoot, $"archive-schema-{CurrentSchemaVersion}-migration.json");
        Directory.CreateDirectory(migrationRoot);
        if (!File.Exists(journal))
            WriteAtomic(journal, new { schemaVersion = 1, fromVersion = manifest.SchemaVersion, toVersion = CurrentSchemaVersion, status = "started", startedUtc = started });
        if (!File.Exists(backup)) WriteAtomic(backup, manifest);

        // Schema 1 only adds the manifest and directory contract. Existing data
        // stays at its original path, so interruption is safe and retryable.
        manifest.SchemaVersion = CurrentSchemaVersion;
        manifest.AppVersion = appVersion;
        manifest.BackendVersion = backendVersion;
        var completed = DateTimeOffset.UtcNow;
        if (!manifest.MigrationHistory.Any(item => item.ToVersion == CurrentSchemaVersion && item.Status == "complete"))
            manifest.MigrationHistory.Add(new ArchiveMigration(0, CurrentSchemaVersion, started, completed, "complete",
                $"Non-destructive manifest migration; previous manifest retained as backups/{Path.GetFileName(backup)}."));
        WriteAtomic(journal, new { schemaVersion = 1, fromVersion = 0, toVersion = CurrentSchemaVersion, status = "complete", startedUtc = started, completedUtc = completed, backup = Path.GetFileName(backup) });
        return manifest;
    }

    private static ArchiveManifest? ReadManifest(string path)
    {
        if (!File.Exists(path)) return null;
        try
        {
            return JsonSerializer.Deserialize<ArchiveManifest>(File.ReadAllText(path), JsonOptions)
                ?? throw new InvalidDataException("The archive manifest is empty.");
        }
        catch (JsonException ex)
        {
            throw new InvalidDataException("The archive manifest is not valid JSON. No archive files were changed.", ex);
        }
    }

    private static List<ArchiveComponentSummary> BuildComponentSummaries(string root)
    {
        var results = new List<ArchiveComponentSummary>();
        foreach (var relative in DurableDirectories.Where(path => !string.Equals(path, "backups", StringComparison.OrdinalIgnoreCase)))
        {
            var directory = ResolveInside(root, relative);
            var files = Directory.Exists(directory)
                ? Directory.EnumerateFiles(directory, "*", SearchOption.AllDirectories)
                    .Where(path => !path.EndsWith(".tmp", StringComparison.OrdinalIgnoreCase))
                    .OrderBy(path => Path.GetRelativePath(root, path), StringComparer.OrdinalIgnoreCase)
                    .ToArray()
                : [];
            long bytes = 0;
            using var aggregate = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
            foreach (var file in files)
            {
                var info = new FileInfo(file);
                bytes += info.Length;
                var relativeFile = Path.GetRelativePath(root, file).Replace('\\', '/').ToLowerInvariant();
                aggregate.AppendData(Encoding.UTF8.GetBytes(relativeFile));
                using var stream = File.OpenRead(file);
                var hash = SHA256.HashData(stream);
                aggregate.AppendData(hash);
            }
            results.Add(new(relative.Replace('\\', '/'), files.Length, bytes, Convert.ToHexString(aggregate.GetHashAndReset()).ToLowerInvariant()));
        }
        results.Add(SummarizeFiles(root, "data/root-files", Directory.EnumerateFiles(ResolveInside(root, "data"), "*", SearchOption.TopDirectoryOnly)));
        results.Add(SummarizeFiles(root, "portable-root-files", Directory.EnumerateFiles(root, "*", SearchOption.TopDirectoryOnly)
            .Where(path => !string.Equals(Path.GetFileName(path), ManifestFileName, StringComparison.OrdinalIgnoreCase)
                && !string.Equals(Path.GetFileName(path), PortableStateFileName, StringComparison.OrdinalIgnoreCase)
                && !path.EndsWith(".tmp", StringComparison.OrdinalIgnoreCase))));
        return results;
    }

    private static ArchiveComponentSummary SummarizeFiles(string root, string componentPath, IEnumerable<string> sourceFiles)
    {
        var files = sourceFiles.OrderBy(path => Path.GetRelativePath(root, path), StringComparer.OrdinalIgnoreCase).ToArray();
        long bytes = 0;
        using var aggregate = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        foreach (var file in files)
        {
            var info = new FileInfo(file);
            bytes += info.Length;
            aggregate.AppendData(Encoding.UTF8.GetBytes(Path.GetRelativePath(root, file).Replace('\\', '/').ToLowerInvariant()));
            using var stream = File.OpenRead(file);
            aggregate.AppendData(SHA256.HashData(stream));
        }
        return new(componentPath, files.Length, bytes, Convert.ToHexString(aggregate.GetHashAndReset()).ToLowerInvariant());
    }

    private static string AggregateHash(IReadOnlyList<ArchiveComponentSummary> components)
    {
        var canonical = string.Join("\n", components.OrderBy(component => component.Path, StringComparer.Ordinal)
            .Select(component => $"{component.Path}|{component.FileCount}|{component.Bytes}|{component.Sha256}"));
        return Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(canonical))).ToLowerInvariant();
    }

    private static ArchiveRestoreSummary BuildRestoreSummary(string root, ArchiveManifest manifest) => new(
        CountFilesNamed(root, "data/reports", "analysis.json"),
        CountFiles(root, "data/race-index") + (File.Exists(ResolveInside(root, "data/history.sqlite3")) ? 1 : 0),
        CountFiles(root, "data/season-cache"),
        CountFiles(root, "setups") + CountFiles(root, "data/setup-history"),
        CountFiles(root, "data/tuning-experiments") + CountFiles(root, "data/tuning"),
        CountFiles(root, "data/strategy-history"),
        CountFiles(root, "data/garage61") + CountFilesInNamedSegments(root, "data/reports", "garage61"),
        CountFiles(root, "data/ai-coaching"),
        CountFiles(root, "data/telemetry-traces"),
        CountFiles(root, "data/tire-models"),
        CountFiles(root, "data/driver-models"),
        CountFiles(root, "data/target-laps"),
        manifest.UnresolvedSources.Count);

    private static int CountFilesInNamedSegments(string root, string relative, string segment)
    {
        var path = ResolveInside(root, relative);
        if (!Directory.Exists(path)) return 0;
        return Directory.EnumerateFiles(path, "*", SearchOption.AllDirectories)
            .Count(file => Path.GetRelativePath(path, file).Split(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar)
                .Any(part => string.Equals(part, segment, StringComparison.OrdinalIgnoreCase)));
    }

    private static int CountFiles(string root, string relative)
    {
        var path = ResolveInside(root, relative);
        return Directory.Exists(path) ? Directory.EnumerateFiles(path, "*", SearchOption.AllDirectories).Count() : 0;
    }

    private static int CountFilesNamed(string root, string relative, string fileName)
    {
        var path = ResolveInside(root, relative);
        return Directory.Exists(path) ? Directory.EnumerateFiles(path, fileName, SearchOption.AllDirectories).Count() : 0;
    }

    private static List<UnresolvedSourceReference> FindUnresolvedSources(string root)
    {
        var results = new Dictionary<string, UnresolvedSourceReference>(StringComparer.OrdinalIgnoreCase);
        var mappedNames = ReadMappedSourceNames(root);
        foreach (var component in new[] { "data/reports", "data/race-index", "data/analysis-cache" })
        {
            var directory = ResolveInside(root, component);
            if (!Directory.Exists(directory)) continue;
            foreach (var file in Directory.EnumerateFiles(directory, "*.json", SearchOption.AllDirectories))
            {
                try
                {
                    if (new FileInfo(file).Length > 16 * 1024 * 1024) continue;
                    using var document = JsonDocument.Parse(File.ReadAllText(file));
                    VisitStrings(document.RootElement, value =>
                    {
                        if (!value.EndsWith(".ibt", StringComparison.OrdinalIgnoreCase) || File.Exists(value)) return;
                        var name = Path.GetFileName(value);
                        if (mappedNames.Contains(name)) return;
                        var id = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(name.ToLowerInvariant()))).ToLowerInvariant()[..16];
                        results.TryAdd(id, new(id, name, "Original iRacing telemetry is not present on this PC. Archived reports remain available; native-rate queries need the source file to be located."));
                    });
                }
                catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException or ArgumentException) { }
            }
        }
        return results.Values.OrderBy(item => item.FileName, StringComparer.OrdinalIgnoreCase).ToList();
    }

    private static HashSet<string> ReadMappedSourceNames(string root)
    {
        var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        var directory = ResolveInside(root, "data/race-index/source-locations");
        if (!Directory.Exists(directory)) return names;
        foreach (var path in Directory.EnumerateFiles(directory, "*.json", SearchOption.TopDirectoryOnly))
        {
            try
            {
                using var document = JsonDocument.Parse(File.ReadAllText(path));
                var item = document.RootElement;
                if (!item.TryGetProperty("fileName", out var nameValue) || string.IsNullOrWhiteSpace(nameValue.GetString())) continue;
                if (!item.TryGetProperty("currentPath", out var pathValue) || !File.Exists(pathValue.GetString())) continue;
                names.Add(nameValue.GetString()!);
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException or ArgumentException) { }
        }
        return names;
    }

    private static void VisitStrings(JsonElement element, Action<string> visit)
    {
        switch (element.ValueKind)
        {
            case JsonValueKind.String:
                var value = element.GetString();
                if (!string.IsNullOrWhiteSpace(value)) visit(value);
                break;
            case JsonValueKind.Array:
                foreach (var child in element.EnumerateArray()) VisitStrings(child, visit);
                break;
            case JsonValueKind.Object:
                foreach (var property in element.EnumerateObject()) VisitStrings(property.Value, visit);
                break;
        }
    }

    private static void WritePortableState(string root, ArchiveManifest manifest, bool safeToCopy, string message)
    {
        WriteAtomic(Path.Combine(root, PortableStateFileName), new
        {
            schemaVersion = CurrentSchemaVersion,
            manifest.ArchiveId,
            safeToCopy,
            checkedUtc = DateTimeOffset.UtcNow,
            message,
            connectionsRequiredOnNewPc = new[] { "ChatGPT", "Garage61" },
            credentialsIncluded = false
        });
    }

    private static void WriteAtomic<T>(string path, T value)
    {
        var destination = Path.GetFullPath(path);
        var contents = JsonSerializer.Serialize(value, JsonOptions);
        var gate = AtomicWriteGates.GetOrAdd(destination, static _ => new object());
        lock (gate)
        {
            string? temporary = null;
            Exception? writeFailure = null;
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
                temporary = destination + $".{Guid.NewGuid():N}.tmp";
                File.WriteAllText(temporary, contents, new UTF8Encoding(false));
                File.Move(temporary, destination, overwrite: true);
            }
            catch (Exception ex)
            {
                writeFailure = ex;
                throw;
            }
            finally
            {
                if (temporary is not null)
                {
                    try { File.Delete(temporary); }
                    catch when (writeFailure is not null) { }
                }
            }
        }
    }

    private static string CanonicalRoot(string root)
    {
        if (string.IsNullOrWhiteSpace(root)) throw new ArgumentException("The Coach folder is required.", nameof(root));
        if (!Path.IsPathRooted(root)) throw new ArgumentException("The Coach folder must be an absolute local path.", nameof(root));
        if (root.StartsWith("\\\\", StringComparison.Ordinal) || root.StartsWith("\\\\?\\", StringComparison.Ordinal))
            throw new ArgumentException("The Coach folder must be on a local Windows drive.", nameof(root));
        var canonical = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        if (string.Equals(canonical, Path.GetPathRoot(canonical)?.TrimEnd(Path.DirectorySeparatorChar), StringComparison.OrdinalIgnoreCase))
            throw new ArgumentException("A drive root cannot be used as the Coach folder.", nameof(root));
        return canonical;
    }

    private static string ResolveInside(string root, string relative)
    {
        var full = Path.GetFullPath(Path.Combine(root, relative.Replace('/', Path.DirectorySeparatorChar)));
        if (!full.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("An archive path escaped the Coach folder boundary.");
        return full;
    }
}
