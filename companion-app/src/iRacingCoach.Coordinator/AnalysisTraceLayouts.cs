using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

public sealed record AnalysisTraceSignalDefinition(
    string Id,
    string Name,
    string Unit,
    string AccentGroup,
    string ShortLabel);

public enum TraceSignalSlot { Primary, Secondary }

public static class AnalysisTraceLayouts
{
    public const int MaximumRows = 10;

    public static IReadOnlyList<AnalysisTraceSignalDefinition> Signals { get; } =
    [
        new("speed", "Speed", "mph", "speed", "Speed"),
        new("delta", "Time delta", "s", "time", "Delta"),
        new("throttle", "Throttle", "%", "pedals", "T"),
        new("brake", "Brake", "%", "pedals", "B"),
        new("tire-wear", "Calculated tire wear", "% this lap", "tire", "Wear"),
        new("gear", "Gear", "selected gear", "drivetrain", "Gear"),
        new("rpm", "RPM", "rpm", "drivetrain", "RPM"),
        new("steering", "Steering", "left / right", "rotation", "Steer"),
        new("slip", "Slip angle", "deg", "rotation", "Slip"),
        new("yaw", "Yaw rate", "deg/s", "rotation", "Yaw"),
        new("lateral-g", "Lateral G", "g", "acceleration", "Lat"),
        new("longitudinal-g", "Longitudinal G", "g", "acceleration", "Long")
    ];

    public static bool ValidateAndRepair(AnalysisTraceLayout layout)
    {
        var changed = false;
        layout.Rows ??= [];
        if (layout.Rows.Count == 0)
        {
            layout.Rows = DefaultRows();
            return true;
        }

        var validSignals = Signals.Select(signal => signal.Id).ToHashSet(StringComparer.Ordinal);
        var repaired = new List<AnalysisTraceRow>(Math.Min(layout.Rows.Count, MaximumRows));
        var rowIds = new HashSet<string>(StringComparer.Ordinal);
        foreach (var source in layout.Rows.Take(MaximumRows))
        {
            var primary = validSignals.Contains(source.PrimarySignalId) ? source.PrimarySignalId : "speed";
            var secondary = IsValidSecondSignal(primary, source.SecondarySignalId) ? source.SecondarySignalId : string.Empty;
            var id = string.IsNullOrWhiteSpace(source.Id) || !rowIds.Add(source.Id)
                ? $"trace-row-{Guid.NewGuid():N}"
                : source.Id;
            rowIds.Add(id);
            if (!string.Equals(primary, source.PrimarySignalId, StringComparison.Ordinal) ||
                !string.Equals(secondary, source.SecondarySignalId, StringComparison.Ordinal) ||
                !string.Equals(id, source.Id, StringComparison.Ordinal))
                changed = true;
            repaired.Add(new AnalysisTraceRow { Id = id, PrimarySignalId = primary, SecondarySignalId = secondary });
        }
        if (layout.Rows.Count > MaximumRows) changed = true;
        layout.Rows = repaired;
        return changed;
    }

    public static bool AddRow(AnalysisTraceLayout layout)
    {
        ValidateAndRepair(layout);
        if (layout.Rows.Count >= MaximumRows) return false;
        var used = layout.Rows.SelectMany(row => new[] { row.PrimarySignalId, row.SecondarySignalId })
            .Where(id => !string.IsNullOrWhiteSpace(id)).ToHashSet(StringComparer.Ordinal);
        var signal = Signals.FirstOrDefault(candidate => !used.Contains(candidate.Id)) ?? Signals[0];
        return InsertSignalRow(layout, signal.Id, layout.Rows.Count);
    }

    public static bool InsertSignalRow(AnalysisTraceLayout layout, string signalId, int targetIndex)
    {
        ValidateAndRepair(layout);
        var normalized = signalId?.Trim() ?? string.Empty;
        if (layout.Rows.Count >= MaximumRows || Signal(normalized) is null ||
            targetIndex < 0 || targetIndex > layout.Rows.Count)
            return false;

        layout.Rows.Insert(targetIndex, new AnalysisTraceRow { PrimarySignalId = normalized });
        return true;
    }

    public static bool InsertSignalRow(AnalysisTraceLayout layout, string signalId) =>
        InsertSignalRow(layout, signalId, layout.Rows?.Count ?? 0);

    public static bool InsertSignal(AnalysisTraceLayout layout, string signalId, int targetIndex) =>
        InsertSignalRow(layout, signalId, targetIndex);

    public static bool RemoveRow(AnalysisTraceLayout layout, string rowId)
    {
        ValidateAndRepair(layout);
        if (layout.Rows.Count <= 1) return false;
        return layout.Rows.RemoveAll(row => string.Equals(row.Id, rowId, StringComparison.Ordinal)) > 0;
    }

    public static bool MoveRow(AnalysisTraceLayout layout, string rowId, int direction)
    {
        ValidateAndRepair(layout);
        var index = layout.Rows.FindIndex(row => string.Equals(row.Id, rowId, StringComparison.Ordinal));
        var target = index + Math.Sign(direction);
        return index >= 0 && MoveRowToIndex(layout, rowId, target);
    }

    public static bool MoveRowToIndex(AnalysisTraceLayout layout, string rowId, int targetIndex)
    {
        ValidateAndRepair(layout);
        var sourceIndex = layout.Rows.FindIndex(row => string.Equals(row.Id, rowId, StringComparison.Ordinal));
        if (sourceIndex < 0 || targetIndex < 0 || targetIndex >= layout.Rows.Count || sourceIndex == targetIndex)
            return false;

        var row = layout.Rows[sourceIndex];
        layout.Rows.RemoveAt(sourceIndex);
        layout.Rows.Insert(targetIndex, row);
        return true;
    }

    public static bool SetPrimary(AnalysisTraceLayout layout, string rowId, string signalId) =>
        AssignSignal(layout, rowId, TraceSignalSlot.Primary, signalId);

    public static bool SetSecondary(AnalysisTraceLayout layout, string rowId, string? signalId) =>
        AssignSignal(layout, rowId, TraceSignalSlot.Secondary, signalId);

    public static bool AssignSignal(AnalysisTraceLayout layout, string rowId, TraceSignalSlot slot, string? signalId)
    {
        ValidateAndRepair(layout);
        if (layout.Rows.FirstOrDefault(row => string.Equals(row.Id, rowId, StringComparison.Ordinal)) is not { } row)
            return false;

        var normalized = signalId?.Trim() ?? string.Empty;
        switch (slot)
        {
            case TraceSignalSlot.Primary:
                if (normalized.Length == 0) return RemoveSignalCore(layout, row, slot);
                if (Signal(normalized) is null) return false;
                row.PrimarySignalId = normalized;
                if (string.Equals(row.SecondarySignalId, normalized, StringComparison.Ordinal))
                    row.SecondarySignalId = string.Empty;
                return true;

            case TraceSignalSlot.Secondary:
                if (normalized.Length == 0)
                {
                    row.SecondarySignalId = string.Empty;
                    return true;
                }
                if (!IsValidSecondSignal(row.PrimarySignalId, normalized)) return false;
                row.SecondarySignalId = normalized;
                return true;

            default:
                return false;
        }
    }

    public static bool PlaceSignal(AnalysisTraceLayout layout, string rowId, string signalId)
    {
        ValidateAndRepair(layout);
        var normalized = signalId?.Trim() ?? string.Empty;
        var row = layout.Rows.FirstOrDefault(candidate => string.Equals(candidate.Id, rowId, StringComparison.Ordinal));
        if (row is null || Signal(normalized) is null ||
            string.Equals(row.PrimarySignalId, normalized, StringComparison.Ordinal) ||
            string.Equals(row.SecondarySignalId, normalized, StringComparison.Ordinal))
            return false;

        row.SecondarySignalId = normalized;
        return true;
    }

    public static bool MoveSignal(
        AnalysisTraceLayout layout,
        string sourceRowId,
        TraceSignalSlot sourceSlot,
        string targetRowId,
        TraceSignalSlot targetSlot)
    {
        ValidateAndRepair(layout);
        var source = layout.Rows.FirstOrDefault(row => string.Equals(row.Id, sourceRowId, StringComparison.Ordinal));
        var target = layout.Rows.FirstOrDefault(row => string.Equals(row.Id, targetRowId, StringComparison.Ordinal));
        if (source is null || target is null) return false;

        var signalId = SignalInSlot(source, sourceSlot);
        if (signalId.Length == 0 || Signal(signalId) is null) return false;

        if (ReferenceEquals(source, target))
        {
            if (sourceSlot == targetSlot || string.IsNullOrWhiteSpace(source.SecondarySignalId)) return false;
            (source.PrimarySignalId, source.SecondarySignalId) = (source.SecondarySignalId, source.PrimarySignalId);
            return true;
        }

        if (targetSlot == TraceSignalSlot.Secondary &&
            string.Equals(target.PrimarySignalId, signalId, StringComparison.Ordinal))
            return false;
        if (targetSlot is not (TraceSignalSlot.Primary or TraceSignalSlot.Secondary)) return false;

        if (targetSlot == TraceSignalSlot.Primary)
        {
            target.PrimarySignalId = signalId;
            if (string.Equals(target.SecondarySignalId, signalId, StringComparison.Ordinal))
                target.SecondarySignalId = string.Empty;
        }
        else
        {
            target.SecondarySignalId = signalId;
        }

        return RemoveSignalCore(layout, source, sourceSlot);
    }

    public static bool RemoveSignal(AnalysisTraceLayout layout, string rowId, TraceSignalSlot slot)
    {
        ValidateAndRepair(layout);
        return layout.Rows.FirstOrDefault(row => string.Equals(row.Id, rowId, StringComparison.Ordinal)) is { } row &&
            RemoveSignalCore(layout, row, slot);
    }

    public static bool RemoveSignal(AnalysisTraceLayout layout, string rowId, string signalId)
    {
        ValidateAndRepair(layout);
        var normalized = signalId?.Trim() ?? string.Empty;
        if (layout.Rows.FirstOrDefault(row => string.Equals(row.Id, rowId, StringComparison.Ordinal)) is not { } row)
            return false;
        if (string.Equals(row.PrimarySignalId, normalized, StringComparison.Ordinal))
            return RemoveSignalCore(layout, row, TraceSignalSlot.Primary);
        if (string.Equals(row.SecondarySignalId, normalized, StringComparison.Ordinal))
            return RemoveSignalCore(layout, row, TraceSignalSlot.Secondary);
        return false;
    }

    public static IReadOnlyList<AnalysisTraceSignalDefinition> SecondarySignalOptions(string primarySignalId) =>
        Signal(primarySignalId) is { } primary
            ? Signals.Where(signal => signal.Id != primary.Id).ToArray()
            : [];

    public static AnalysisTraceSignalDefinition? Signal(string signalId) =>
        Signals.FirstOrDefault(signal => string.Equals(signal.Id, signalId, StringComparison.Ordinal));

    private static bool IsValidSecondSignal(string primarySignalId, string? secondarySignalId)
    {
        if (string.IsNullOrWhiteSpace(secondarySignalId)) return true;
        var primary = Signal(primarySignalId);
        var secondary = Signal(secondarySignalId);
        return primary is not null && secondary is not null && primary.Id != secondary.Id;
    }

    private static string SignalInSlot(AnalysisTraceRow row, TraceSignalSlot slot) => slot switch
    {
        TraceSignalSlot.Primary => row.PrimarySignalId,
        TraceSignalSlot.Secondary => row.SecondarySignalId,
        _ => string.Empty
    };

    private static bool RemoveSignalCore(AnalysisTraceLayout layout, AnalysisTraceRow row, TraceSignalSlot slot)
    {
        if (slot == TraceSignalSlot.Secondary)
        {
            if (string.IsNullOrWhiteSpace(row.SecondarySignalId)) return false;
            row.SecondarySignalId = string.Empty;
            return true;
        }
        if (slot != TraceSignalSlot.Primary) return false;
        if (!string.IsNullOrWhiteSpace(row.SecondarySignalId))
        {
            row.PrimarySignalId = row.SecondarySignalId;
            row.SecondarySignalId = string.Empty;
            return true;
        }
        if (layout.Rows.Count <= 1) return false;
        return layout.Rows.Remove(row);
    }

    private static List<AnalysisTraceRow> DefaultRows() =>
    [
        Row("speed"),
        Row("delta"),
        Row("throttle"),
        Row("brake"),
        Row("tire-wear"),
        Row("gear"),
        Row("rpm"),
        Row("steering"),
        Row("slip", "yaw"),
        Row("lateral-g", "longitudinal-g")
    ];

    private static AnalysisTraceRow Row(string primary, string secondary = "") =>
        new() { PrimarySignalId = primary, SecondarySignalId = secondary };
}
