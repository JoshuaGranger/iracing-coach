using System.Text.Json;
using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

internal sealed record ReplayWindowCursor(string Revision, int NextFrame);

internal static class ReplayManifestConsumer
{
    public const int SupportedSchemaVersion = 2;

    public static AnalysisReplayManifest Read(JsonElement payload)
    {
        if (payload.ValueKind != JsonValueKind.Object)
            throw new InvalidDataException("A replay manifest must be an object.");

        var schemaVersion = RequiredInteger(payload, "schema_version", "schemaVersion", minimum: 1);
        if (schemaVersion > SupportedSchemaVersion)
            throw new InvalidDataException($"Replay schema {schemaVersion} is newer than supported schema {SupportedSchemaVersion}.");

        var hasFormat = payload.TryGetProperty("format", out var formatElement);
        var hasStatus = payload.TryGetProperty("status", out var statusElement);
        string format;
        string status;
        string revision;
        if (!hasFormat && !hasStatus)
        {
            format = "inline";
            status = "complete";
            revision = OptionalText(payload, "revision") ?? "legacy-inline";
        }
        else
        {
            if (hasFormat != hasStatus)
                throw new InvalidDataException("A replay manifest must carry format and status together.");
            format = RequiredText(formatElement, "format");
            status = RequiredText(statusElement, "status");
            revision = OptionalText(payload, "revision") ?? string.Empty;
            if (revision.Length == 0) throw new InvalidDataException("A replay manifest must carry a revision.");
        }

        if (format is not "inline" and not "windowed")
            throw new InvalidDataException($"Unknown replay format: {format}.");
        if (status is not "complete" and not "incomplete" and not "failed")
            throw new InvalidDataException($"Unknown replay status: {status}.");

        var frameCount = RequiredInteger(payload, "frame_count", minimum: 0);
        var carCount = RequiredInteger(payload, "car_count", minimum: 0);
        var cadence = OptionalNumber(payload, "cadence_hz") ?? 60;
        if (!double.IsFinite(cadence) || cadence <= 0 || cadence > 1000)
            throw new InvalidDataException("Replay cadence must be positive and plausible.");
        if (status == "failed" && frameCount != 0)
            throw new InvalidDataException("A failed replay cannot claim delivered frames.");

        var gaps = ReadGaps(payload, frameCount);
        if (status == "complete" && gaps.Count > 0)
            throw new InvalidDataException("A complete replay cannot declare missing frames.");
        return new AnalysisReplayManifest(
            OptionalInteger(payload, "contract_version") ?? 1,
            schemaVersion,
            format,
            status,
            revision,
            frameCount,
            carCount,
            cadence,
            gaps);
    }

    public static bool CursorIsValid(AnalysisReplayManifest manifest, ReplayWindowCursor cursor)
    {
        ArgumentNullException.ThrowIfNull(manifest);
        ArgumentNullException.ThrowIfNull(cursor);
        return cursor.NextFrame >= 0 &&
            cursor.NextFrame <= manifest.FrameCount &&
            string.Equals(cursor.Revision, manifest.Revision, StringComparison.Ordinal);
    }

    private static IReadOnlyList<AnalysisReplayFrameGap> ReadGaps(JsonElement payload, int frameCount)
    {
        if (!payload.TryGetProperty("gaps", out var element)) return [];
        if (element.ValueKind != JsonValueKind.Array)
            throw new InvalidDataException("Replay gaps must be an array.");
        var gaps = new List<AnalysisReplayFrameGap>();
        foreach (var item in element.EnumerateArray())
        {
            if (item.ValueKind != JsonValueKind.Object)
                throw new InvalidDataException("Each replay gap must be an object.");
            var start = RequiredInteger(item, "start_frame", minimum: 0);
            var end = RequiredInteger(item, "end_frame", minimum: 0);
            if (end < start) throw new InvalidDataException("A replay gap cannot end before it starts.");
            if (end >= frameCount) throw new InvalidDataException("A replay gap must fall inside the frame range.");
            gaps.Add(new(start, end));
        }
        var ordered = gaps.OrderBy(gap => gap.StartFrame).ToArray();
        for (var index = 1; index < ordered.Length; index++)
            if (ordered[index].StartFrame <= ordered[index - 1].EndFrame)
                throw new InvalidDataException("Replay gaps cannot overlap.");
        return ordered;
    }

    private static int RequiredInteger(JsonElement payload, string name, int minimum) =>
        OptionalInteger(payload, name) is { } value && value >= minimum
            ? value
            : throw new InvalidDataException($"Replay manifest {name} must be an integer of at least {minimum}.");

    private static int RequiredInteger(JsonElement payload, string primary, string legacy, int minimum)
    {
        if (payload.TryGetProperty(primary, out var value)) return RequiredIntegerValue(value, primary, minimum);
        if (payload.TryGetProperty(legacy, out value)) return RequiredIntegerValue(value, legacy, minimum);
        throw new InvalidDataException($"Replay manifest {primary} is required.");
    }

    private static int RequiredIntegerValue(JsonElement value, string name, int minimum) =>
        value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var integer) && integer >= minimum
            ? integer
            : throw new InvalidDataException($"Replay manifest {name} must be an integer of at least {minimum}.");

    private static int? OptionalInteger(JsonElement payload, string name) =>
        payload.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var integer)
            ? integer
            : null;

    private static double? OptionalNumber(JsonElement payload, string name) =>
        payload.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out var number)
            ? number
            : null;

    private static string? OptionalText(JsonElement payload, string name) =>
        payload.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString()
            : null;

    private static string RequiredText(JsonElement value, string name) =>
        value.ValueKind == JsonValueKind.String && !string.IsNullOrWhiteSpace(value.GetString())
            ? value.GetString()!
            : throw new InvalidDataException($"Replay manifest {name} must be a non-empty string.");
}
