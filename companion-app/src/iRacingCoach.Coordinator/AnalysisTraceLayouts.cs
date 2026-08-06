using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

public sealed record AnalysisTraceSignalDefinition(
    string Id,
    string Name,
    string Unit,
    string AccentGroup,
    string ShortLabel);

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
        layout.Rows.Add(new AnalysisTraceRow { PrimarySignalId = signal.Id });
        return true;
    }

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
        if (index < 0 || target < 0 || target >= layout.Rows.Count) return false;
        (layout.Rows[index], layout.Rows[target]) = (layout.Rows[target], layout.Rows[index]);
        return true;
    }

    public static bool SetPrimary(AnalysisTraceLayout layout, string rowId, string signalId)
    {
        ValidateAndRepair(layout);
        if (Signal(signalId) is null || layout.Rows.FirstOrDefault(row => row.Id == rowId) is not { } row) return false;
        row.PrimarySignalId = signalId;
        if (!IsValidSecondSignal(signalId, row.SecondarySignalId)) row.SecondarySignalId = string.Empty;
        return true;
    }

    public static bool SetSecondary(AnalysisTraceLayout layout, string rowId, string? signalId)
    {
        ValidateAndRepair(layout);
        if (layout.Rows.FirstOrDefault(row => row.Id == rowId) is not { } row) return false;
        var normalized = signalId?.Trim() ?? string.Empty;
        if (normalized.Length > 0 && !IsValidSecondSignal(row.PrimarySignalId, normalized)) return false;
        row.SecondarySignalId = normalized;
        return true;
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
