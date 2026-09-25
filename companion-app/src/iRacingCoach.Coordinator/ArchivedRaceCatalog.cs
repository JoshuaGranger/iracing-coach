using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

// Catalogs need a few kilobytes from reports that can exceed 80 MB. Keep the
// projection separate from the immutable report and invalidate it on changes.
internal sealed class ArchivedRaceCatalog
{
    private const int ProjectionVersion = 1;
    private const int UnreadableLedgerVersion = 1;
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);
    private readonly object _gate = new();
    private readonly Dictionary<string, Entry> _entries = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<string, UnreadableReport> _invalidReports = new(StringComparer.OrdinalIgnoreCase);
    private string? _ledgerPath;
    private bool _ledgerWritable;
    private readonly string _reader;

    /// <param name="appVersion">Added to <see cref="CurrentReader"/>, so every app release also retries once.</param>
    public ArchivedRaceCatalog(string? appVersion = null) =>
        _reader = string.IsNullOrWhiteSpace(appVersion) ? CurrentReader : $"{appVersion}/{CurrentReader}";

    internal sealed record Entry(int Version, string ReportPath, long Length, DateTime LastWriteUtc, RecentRace Race, LocalSetup? Setup);

    /// <summary>
    /// A report revision that could not be read. It is remembered, not retried, until its
    /// size or time changes or a different <paramref name="Reader"/> is running.
    /// </summary>
    internal sealed record UnreadableReport(string Path, long Length, DateTime LastWriteUtc, bool Acknowledged = false, string? Reader = null);

    private sealed record UnreadableLedger(int SchemaVersion, IReadOnlyList<UnreadableReport> Reports);

    /// <summary>
    /// This build of the report reader: the parsing and mapping code in this assembly and
    /// the contract models it fills. A report hidden by a reader bug must not stay hidden
    /// after an upgrade fixes the bug, so a failure recorded by any other build is retried
    /// once. Development builds share an app version, so the build itself is the key.
    /// </summary>
    internal static string CurrentReader { get; } = ReaderBuild();

    private static string ReaderBuild()
    {
        string Module(System.Reflection.Assembly assembly)
        {
            try { return assembly.ManifestModule.ModuleVersionId.ToString("N"); }
            catch (Exception ex) when (ex is InvalidOperationException or NotSupportedException) { return assembly.FullName ?? "unknown"; }
        }
        var modules = string.Join('|', Module(typeof(ArchivedRaceCatalog).Assembly), Module(typeof(RecentRace).Assembly));
        return $"catalog-{ProjectionVersion}-" + Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(modules)))[..16].ToLowerInvariant();
    }

    /// <param name="unreadableLedgerPath">
    /// App-owned local state that remembers unreadable report revisions across launches.
    /// Without it, the memory lasts for this process only.
    /// </param>
    public Entry? Read(string path, string cacheRoot, Action beforeWrite, string? unreadableLedgerPath = null)
    {
        path = Path.GetFullPath(path);
        lock (_gate)
        {
            UseLedger(unreadableLedgerPath);
            var info = new FileInfo(path);
            if (!info.Exists) throw new FileNotFoundException("The saved race could not be found.", path);
            var alreadyReported = false;
            if (_invalidReports.TryGetValue(path, out var failed))
            {
                if (SameRevision(failed, info))
                {
                    if (string.Equals(failed.Reader, _reader, StringComparison.Ordinal)) return null;
                    // Another reader judged this revision; retry it once. If it still cannot be
                    // read, the driver has already been told about this same file.
                    alreadyReported = failed.Acknowledged;
                }
                _invalidReports.Remove(path);
                SaveLedger();
            }
            bool Current(Entry? entry) => entry is not null && entry.Race is not null && entry.Version == ProjectionVersion
                && string.Equals(entry.ReportPath, path, StringComparison.OrdinalIgnoreCase)
                && entry.Length == info.Length && entry.LastWriteUtc == info.LastWriteTimeUtc;
            if (_entries.TryGetValue(path, out var existing) && Current(existing)) return existing;
            var key = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(path.ToLowerInvariant())));
            var cachedPath = Path.Combine(cacheRoot, key + ".json");
            try
            {
                if (File.Exists(cachedPath))
                {
                    var cached = JsonSerializer.Deserialize<Entry>(File.ReadAllBytes(cachedPath), Json);
                    if (Current(cached)) return _entries[path] = cached!;
                }
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException) { }

            Entry entry;
            try
            {
                // Reports from older analyzers can contain bare NaN/Infinity values.
                // The catalog reads them as unknown instead of discarding the race.
                using var document = NonFiniteJson.Parse(File.ReadAllBytes(path));
                entry = new Entry(ProjectionVersion, path, info.Length, info.LastWriteTimeUtc,
                    RuntimeMapper.ArchivedRace(document.RootElement, path), RuntimeMapper.ArchivedSetup(document.RootElement, path));
            }
            catch (Exception ex) when (ex is JsonException or InvalidDataException or InvalidOperationException)
            {
                // Report this revision once. Both catalogs share this reader, so an
                // unchanged invalid report is neither reparsed nor logged per refresh,
                // and the ledger keeps the next launch from reading it again.
                _invalidReports[path] = new UnreadableReport(path, info.Length, info.LastWriteTimeUtc, alreadyReported, _reader);
                SaveLedger();
                throw;
            }
            info.Refresh();
            if (!Current(entry)) throw new IOException("The saved race changed while its summary was loading. Retry the refresh.");
            _entries[path] = entry;
            var temporary = cachedPath + "." + Guid.NewGuid().ToString("N") + ".tmp";
            try
            {
                beforeWrite();
                Directory.CreateDirectory(cacheRoot);
                File.WriteAllBytes(temporary, JsonSerializer.SerializeToUtf8Bytes(entry, Json));
                File.Move(temporary, cachedPath, overwrite: true);
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
            finally
            {
                try { if (File.Exists(temporary)) File.Delete(temporary); }
                catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
            }
            return entry;
        }
    }

    /// <summary>
    /// The remembered unreadable reports below <paramref name="reportsRoot"/> whose
    /// revision is unchanged. Repaired, replaced or removed reports are forgotten.
    /// </summary>
    public IReadOnlyList<UnreadableReport> UnreadableReports(string reportsRoot, string? unreadableLedgerPath = null)
    {
        var root = Path.TrimEndingDirectorySeparator(Path.GetFullPath(reportsRoot)) + Path.DirectorySeparatorChar;
        lock (_gate)
        {
            UseLedger(unreadableLedgerPath);
            var changed = false;
            var result = new List<UnreadableReport>();
            foreach (var report in _invalidReports.Values.ToArray())
            {
                var info = new FileInfo(report.Path);
                if (!info.Exists || !SameRevision(report, info))
                {
                    changed |= _invalidReports.Remove(report.Path);
                    continue;
                }
                if (report.Path.StartsWith(root, StringComparison.OrdinalIgnoreCase)) result.Add(report);
            }
            if (changed) SaveLedger();
            return result.OrderBy(report => report.Path, StringComparer.OrdinalIgnoreCase).ToArray();
        }
    }

    /// <summary>Marks these unreadable revisions as already reported to the driver.</summary>
    public void AcknowledgeUnreadable(IEnumerable<string> paths, string? unreadableLedgerPath = null)
    {
        ArgumentNullException.ThrowIfNull(paths);
        lock (_gate)
        {
            UseLedger(unreadableLedgerPath);
            var changed = false;
            foreach (var path in paths)
            {
                var fullPath = Path.GetFullPath(path);
                if (_invalidReports.TryGetValue(fullPath, out var report) && !report.Acknowledged)
                {
                    _invalidReports[fullPath] = report with { Acknowledged = true };
                    changed = true;
                }
            }
            if (changed) SaveLedger();
        }
    }

    private static bool SameRevision(UnreadableReport report, FileInfo info) =>
        report.Length == info.Length && report.LastWriteUtc == info.LastWriteTimeUtc;

    private void UseLedger(string? ledgerPath)
    {
        if (string.IsNullOrWhiteSpace(ledgerPath)) return;
        var fullPath = Path.GetFullPath(ledgerPath);
        if (string.Equals(fullPath, _ledgerPath, StringComparison.OrdinalIgnoreCase)) return;
        _ledgerPath = fullPath;
        _ledgerWritable = true;
        try
        {
            if (!File.Exists(fullPath)) return;
            using var document = JsonDocument.Parse(File.ReadAllBytes(fullPath));
            var root = document.RootElement;
            if (root.ValueKind != JsonValueKind.Object
                || !root.TryGetProperty("schemaVersion", out var version)
                || version.ValueKind != JsonValueKind.Number
                || !version.TryGetInt32(out var schemaVersion)
                || schemaVersion < 1)
                return;
            if (schemaVersion > UnreadableLedgerVersion)
            {
                // A newer app owns this ledger. Use nothing from it and leave it unchanged.
                _ledgerWritable = false;
                return;
            }
            var ledger = root.Deserialize<UnreadableLedger>(Json);
            foreach (var report in ledger?.Reports ?? [])
                if (report is { Path.Length: > 0, Length: >= 0 } && Path.IsPathFullyQualified(report.Path))
                    _invalidReports.TryAdd(Path.GetFullPath(report.Path), report with { Path = Path.GetFullPath(report.Path) });
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException or ArgumentException or NotSupportedException)
        {
            // The ledger is only a cache of failures; an unreadable ledger means rereading once.
        }
    }

    private void SaveLedger()
    {
        if (_ledgerPath is null || !_ledgerWritable) return;
        var temporary = _ledgerPath + "." + Guid.NewGuid().ToString("N") + ".tmp";
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(_ledgerPath)!);
            var ledger = new UnreadableLedger(UnreadableLedgerVersion,
                _invalidReports.Values.OrderBy(report => report.Path, StringComparer.OrdinalIgnoreCase).ToArray());
            File.WriteAllBytes(temporary, JsonSerializer.SerializeToUtf8Bytes(ledger, Json));
            File.Move(temporary, _ledgerPath, overwrite: true);
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
        finally
        {
            try { if (File.Exists(temporary)) File.Delete(temporary); }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
        }
    }
}
