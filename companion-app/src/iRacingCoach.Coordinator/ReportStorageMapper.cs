using System.Globalization;
using System.Text.Json;
using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

/// <summary>
/// Reads reclaim_report_storage responses (report-storage-reclaim-v1). It never decides
/// eligibility: a missing or mistyped required field, another contract or schema version,
/// or the wrong mode fails with the field's path, so a malformed plan can never be shown
/// or confirmed. The whole response is kept so unknown optional fields survive.
/// </summary>
public static class ReportStorageMapper
{
    private static readonly string[] Statuses = ["ready", "nothing", "empty", "index-unreadable"];
    private static readonly string[] ResultStatuses = ["complete", "partial", "nothing"];
    private static readonly string[] Kinds = ["duplicate", "superseded"];

    public static ReportStoragePlan MapPlan(JsonElement response)
    {
        Header(response, "plan", readOnly: true);
        var status = OneOf(response, "status", "$", Statuses);
        var remove = RequiredObject(response, "remove", "$");
        var directories = Array(remove, "directories", "remove").Select((item, index) => Copy(item, $"remove.directories[{index}]", planned: true)).ToArray();
        var count = RequiredInt(remove, "count", "remove");
        if (count != directories.Length)
            throw new JsonException($"The storage plan lists {directories.Length} copies but reports remove.count {count}.");
        var bytes = RequiredLong(remove, "bytes", "remove");
        if (bytes != directories.Sum(item => item.Bytes))
            throw new JsonException("The storage plan's remove.bytes does not equal the listed copies.");
        var kept = RequiredObject(response, "kept", "$");
        var reasons = RequiredObject(kept, "by_reason", "kept");
        var byReason = new Dictionary<string, int>(StringComparer.Ordinal);
        foreach (var property in reasons.EnumerateObject())
        {
            if (property.Value.ValueKind != JsonValueKind.Number || !property.Value.TryGetInt32(out var value) || value < 0)
                throw new JsonException($"Invalid storage plan field: kept.by_reason.{property.Name}.");
            byReason[property.Name] = value;
        }
        var keptCount = RequiredInt(kept, "count", "kept");
        if (byReason.Values.Sum() != keptCount)
            throw new JsonException("The storage plan's kept reasons do not account for every kept copy.");
        return new ReportStoragePlan(
            status,
            RequiredText(response, "plan_id", "$"),
            RequiredText(response, "reports_root", "$"),
            RequiredText(response, "recovery_root", "$"),
            new ReportStorageRemoval(
                count,
                bytes,
                RequiredInt(remove, "duplicate_count", "remove"),
                RequiredLong(remove, "duplicate_bytes", "remove"),
                RequiredInt(remove, "superseded_count", "remove"),
                RequiredLong(remove, "superseded_bytes", "remove"),
                OptionalTime(remove, "oldest_utc", "remove"),
                OptionalTime(remove, "newest_utc", "remove"),
                directories),
            new ReportStorageKept(keptCount, RequiredLong(kept, "bytes", "kept"), byReason),
            Recovery(RequiredObject(response, "recovery", "$")),
            response.Clone());
    }

    public static ReportStorageResult MapResult(JsonElement response)
    {
        Header(response, "execute", readOnly: false);
        var moved = RequiredObject(response, "moved", "$");
        var movedCopies = Array(moved, "directories", "moved").Select((item, index) => Copy(item, $"moved.directories[{index}]", planned: false)).ToArray();
        var movedCount = RequiredInt(moved, "count", "moved");
        if (movedCount != movedCopies.Length)
            throw new JsonException($"The removal lists {movedCopies.Length} moved copies but reports moved.count {movedCount}.");
        var skipped = RequiredObject(response, "skipped", "$");
        var skips = Array(skipped, "directories", "skipped").Select((item, index) =>
        {
            var path = $"skipped.directories[{index}]";
            if (item.ValueKind != JsonValueKind.Object) throw new JsonException($"Invalid removal field: {path}.");
            return new ReportStorageSkip(RequiredText(item, "directory", path), RequiredText(item, "reason", path));
        }).ToArray();
        if (RequiredInt(skipped, "count", "skipped") != skips.Length)
            throw new JsonException("The removal's skipped.count does not match its list.");
        return new ReportStorageResult(
            OneOf(response, "status", "$", ResultStatuses),
            RequiredText(response, "plan_id", "$"),
            RequiredText(response, "confirmed_plan_id", "$"),
            OptionalText(response, "batch_directory", "$"),
            movedCount,
            RequiredLong(moved, "bytes", "moved"),
            movedCopies,
            skips,
            Recovery(RequiredObject(response, "recovery", "$")),
            response.Clone());
    }

    private static void Header(JsonElement response, string mode, bool readOnly)
    {
        if (response.ValueKind != JsonValueKind.Object)
            throw new JsonException("The saved-analysis storage response is not an object.");
        var contract = RequiredText(response, "contract", "$");
        if (!string.Equals(contract, ReportStoragePlan.Contract, StringComparison.Ordinal))
            throw new JsonException($"The storage response uses contract {contract}; this app reads {ReportStoragePlan.Contract}.");
        var schema = RequiredInt(response, "schema_version", "$");
        if (schema != ReportStoragePlan.CurrentSchemaVersion)
            throw new JsonException($"The storage response uses schema version {schema}; this app reads version {ReportStoragePlan.CurrentSchemaVersion}.");
        var actual = RequiredText(response, "mode", "$");
        if (!string.Equals(actual, mode, StringComparison.Ordinal))
            throw new JsonException($"Expected a storage {mode} response but received {actual}.");
        if (!response.TryGetProperty("read_only", out var flag) || flag.ValueKind is not (JsonValueKind.True or JsonValueKind.False) || flag.GetBoolean() != readOnly)
            throw new JsonException($"A storage {mode} response must have read_only {(readOnly ? "true" : "false")}.");
    }

    private static ReportStorageCopy Copy(JsonElement item, string path, bool planned)
    {
        if (item.ValueKind != JsonValueKind.Object) throw new JsonException($"Invalid storage field: {path}.");
        if (planned) _ = RequiredText(item, "modified_utc", path);
        if (!item.TryGetProperty("kept_copy", out var kept) || kept.ValueKind is not (JsonValueKind.String or JsonValueKind.Null))
            throw new JsonException($"Missing or invalid storage field: {path}.kept_copy.");
        return new ReportStorageCopy(
            RequiredText(item, "directory", path),
            RequiredLong(item, "bytes", path),
            OneOf(item, "kind", path, Kinds),
            RequiredText(item, "analysis_id", path),
            kept.ValueKind == JsonValueKind.String ? kept.GetString() : null);
    }

    private static ReportStorageRecovery Recovery(JsonElement item) =>
        new(RequiredText(item, "path", "recovery"), RequiredLong(item, "bytes", "recovery"), RequiredInt(item, "batch_count", "recovery"));

    private static JsonElement RequiredObject(JsonElement item, string name, string path) =>
        item.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.Object
            ? value
            : throw new JsonException($"Missing or invalid storage field: {path}.{name}.");

    private static IEnumerable<JsonElement> Array(JsonElement item, string name, string path) =>
        item.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.Array
            ? value.EnumerateArray().ToArray()
            : throw new JsonException($"Missing or invalid storage field: {path}.{name}.");

    private static string RequiredText(JsonElement item, string name, string path) =>
        item.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String && !string.IsNullOrWhiteSpace(value.GetString())
            ? value.GetString()!
            : throw new JsonException($"Missing or invalid storage field: {path}.{name}.");

    private static string? OptionalText(JsonElement item, string name, string path)
    {
        if (!item.TryGetProperty(name, out var value) || value.ValueKind == JsonValueKind.Null) return null;
        return value.ValueKind == JsonValueKind.String ? value.GetString() : throw new JsonException($"Invalid storage field: {path}.{name}.");
    }

    private static string OneOf(JsonElement item, string name, string path, IReadOnlyCollection<string> allowed)
    {
        var value = RequiredText(item, name, path);
        return allowed.Contains(value, StringComparer.Ordinal) ? value : throw new JsonException($"Unknown storage value {value} at {path}.{name}.");
    }

    private static int RequiredInt(JsonElement item, string name, string path) =>
        item.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var number) && number >= 0
            ? number
            : throw new JsonException($"Missing or invalid storage field: {path}.{name}.");

    private static long RequiredLong(JsonElement item, string name, string path) =>
        item.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.Number && value.TryGetInt64(out var number) && number >= 0
            ? number
            : throw new JsonException($"Missing or invalid storage field: {path}.{name}.");

    private static DateTimeOffset? OptionalTime(JsonElement item, string name, string path)
    {
        var text = OptionalText(item, name, path);
        if (text is null) return null;
        return DateTimeOffset.TryParse(text, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out var value)
            ? value
            : throw new JsonException($"Invalid storage time at {path}.{name}.");
    }
}
