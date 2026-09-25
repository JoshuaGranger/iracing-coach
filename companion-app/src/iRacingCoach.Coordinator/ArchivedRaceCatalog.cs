using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

// Catalogs need a few kilobytes from reports that can exceed 80 MB. Keep the
// projection separate from the immutable report and invalidate it on changes.
internal sealed class ArchivedRaceCatalog
{
    // 2: recorded setups carry the recording's car path (CarKey) for same-car comparison.
    private const int ProjectionVersion = 2;
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);
    private readonly object _gate = new();
    private readonly Dictionary<string, Entry> _entries = new(StringComparer.OrdinalIgnoreCase);
    private readonly Dictionary<string, (long Length, DateTime LastWriteUtc)> _invalidReports = new(StringComparer.OrdinalIgnoreCase);

    internal sealed record Entry(int Version, string ReportPath, long Length, DateTime LastWriteUtc, RecentRace Race, LocalSetup? Setup);

    public Entry? Read(string path, string cacheRoot, Action beforeWrite)
    {
        path = Path.GetFullPath(path);
        lock (_gate)
        {
            var info = new FileInfo(path);
            if (!info.Exists) throw new FileNotFoundException("The saved race could not be found.", path);
            if (_invalidReports.TryGetValue(path, out var failed)
                && failed.Length == info.Length && failed.LastWriteUtc == info.LastWriteTimeUtc) return null;
            _invalidReports.Remove(path);
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
                using var document = JsonDocument.Parse(File.ReadAllBytes(path));
                entry = new Entry(ProjectionVersion, path, info.Length, info.LastWriteTimeUtc,
                    RuntimeMapper.ArchivedRace(document.RootElement, path), RuntimeMapper.ArchivedSetup(document.RootElement, path));
            }
            catch (Exception ex) when (ex is JsonException or InvalidDataException or InvalidOperationException)
            {
                // Report this revision once. Both catalogs share this reader, so an
                // unchanged invalid report is neither reparsed nor logged per refresh.
                _invalidReports[path] = (info.Length, info.LastWriteTimeUtc);
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
}
