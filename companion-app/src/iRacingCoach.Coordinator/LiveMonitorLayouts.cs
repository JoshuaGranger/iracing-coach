using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

public sealed record LiveMonitorLayoutChoice(LiveMonitorNamedLayout Layout, bool IsFactory);

public static class LiveMonitorLayouts
{
    public const string FactoryRaceId = "factory-race";
    public const string FactoryQualifyingId = "factory-qualifying";
    private const int MinimumGrid = 1;
    private const int MaximumGrid = 8;

    private static readonly IReadOnlyList<LiveMonitorNamedLayout> Factory =
    [
        BuildFactory(LiveMonitorLayout.FactoryDefaultId, "Default",
            ("position", LiveMonitorDisplayStyle.Number),
            ("last-lap", LiveMonitorDisplayStyle.Number),
            ("leader-gap", LiveMonitorDisplayStyle.Number),
            ("fuel", LiveMonitorDisplayStyle.Bar),
            ("pit-window", LiveMonitorDisplayStyle.Number),
            ("coach-cue", LiveMonitorDisplayStyle.Status))
    ];

    private static readonly IReadOnlyList<LiveMonitorNamedLayout> BuiltInUserTemplates =
    [
        BuildFactory(FactoryRaceId, "Race",
            ("position", LiveMonitorDisplayStyle.Number),
            ("ahead-gap", LiveMonitorDisplayStyle.Number),
            ("behind-gap", LiveMonitorDisplayStyle.Number),
            ("fuel", LiveMonitorDisplayStyle.Bar),
            ("pit-window", LiveMonitorDisplayStyle.Number),
            ("coach-cue", LiveMonitorDisplayStyle.Status)),
        BuildFactory(FactoryQualifyingId, "Qualifying",
            ("last-lap", LiveMonitorDisplayStyle.Number),
            ("pace-range", LiveMonitorDisplayStyle.Number),
            ("speed", LiveMonitorDisplayStyle.Trend),
            ("brake", LiveMonitorDisplayStyle.Trend),
            ("rpm", LiveMonitorDisplayStyle.Trend),
            ("coach-cue", LiveMonitorDisplayStyle.Status))
    ];

    public static IReadOnlyList<LiveMonitorLayoutChoice> Choices(LiveMonitorLayout preferences)
    {
        InitializeBuiltInDashboards(preferences);
        return Factory.Select(layout => new LiveMonitorLayoutChoice(Clone(layout), true))
            .Concat(preferences.UserLayouts
                .Where(layout => !IsFactory(layout.Id))
                .Select(layout => new LiveMonitorLayoutChoice(layout, false)))
            .ToArray();
    }

    public static LiveMonitorLayoutChoice Active(LiveMonitorLayout preferences)
    {
        InitializeBuiltInDashboards(preferences);
        if (IsFactory(preferences.ActiveLayoutId)) return new(Clone(Factory[0]), true);
        var user = preferences.UserLayouts.FirstOrDefault(layout => string.Equals(layout.Id, preferences.ActiveLayoutId, StringComparison.Ordinal));
        if (user is not null) return new(user, false);
        return new(Clone(Factory[0]), true);
    }

    public static bool IsFactory(string id) => string.Equals(id, LiveMonitorLayout.FactoryDefaultId, StringComparison.Ordinal);

    public static LiveMonitorNamedLayout EnsureEditable(LiveMonitorLayout preferences)
    {
        var active = Active(preferences);
        if (!active.IsFactory) return active.Layout;
        var copy = Clone(active.Layout);
        copy.Id = $"layout-{Guid.NewGuid():N}";
        copy.Name = UniqueName(preferences, $"{active.Layout.Name} Copy");
        preferences.UserLayouts.Add(copy);
        preferences.ActiveLayoutId = copy.Id;
        return copy;
    }

    public static LiveMonitorNamedLayout Create(LiveMonitorLayout preferences)
    {
        var created = Clone(Factory[0]);
        created.Id = $"layout-{Guid.NewGuid():N}";
        created.Name = UniqueName(preferences, "Custom");
        preferences.UserLayouts.Add(created);
        preferences.ActiveLayoutId = created.Id;
        return created;
    }

    public static LiveMonitorNamedLayout Duplicate(LiveMonitorLayout preferences)
    {
        var source = Active(preferences).Layout;
        var duplicate = Clone(source);
        duplicate.Id = $"layout-{Guid.NewGuid():N}";
        duplicate.Name = UniqueName(preferences, $"{source.Name} Copy");
        foreach (var tile in duplicate.Tiles) tile.Id = $"tile-{Guid.NewGuid():N}";
        preferences.UserLayouts.Add(duplicate);
        preferences.ActiveLayoutId = duplicate.Id;
        return duplicate;
    }

    public static bool DeleteActive(LiveMonitorLayout preferences)
    {
        InitializeBuiltInDashboards(preferences);
        if (IsFactory(preferences.ActiveLayoutId)) return false;
        var removed = preferences.UserLayouts.RemoveAll(layout => string.Equals(layout.Id, preferences.ActiveLayoutId, StringComparison.Ordinal)) > 0;
        if (removed) preferences.ActiveLayoutId = LiveMonitorLayout.FactoryDefaultId;
        return removed;
    }

    public static bool ResetActive(LiveMonitorLayout preferences)
    {
        var active = Active(preferences);
        if (active.IsFactory) return false;

        var name = active.Layout.Name;
        var template = BuiltInUserTemplates.FirstOrDefault(layout => string.Equals(layout.Id, active.Layout.Id, StringComparison.Ordinal)) ?? Factory[0];
        var reset = Clone(template);
        reset.Id = active.Layout.Id;
        reset.Name = name;
        var index = preferences.UserLayouts.FindIndex(layout => layout.Id == active.Layout.Id);
        preferences.UserLayouts[index] = reset;
        return true;
    }

    public static bool TryAddMetric(LiveMonitorNamedLayout layout, string metricId, out string? tileId)
        => TryAddMetricCore(layout, metricId, null, null, out tileId);

    public static bool TryAddMetric(LiveMonitorNamedLayout layout, string metricId, int row, int column, out string? tileId)
        => TryAddMetricCore(layout, metricId, row, column, out tileId);

    private static bool TryAddMetricCore(LiveMonitorNamedLayout layout, string metricId, int? row, int? column, out string? tileId)
    {
        tileId = null;
        if (!LiveTelemetryCatalog.TryGet(metricId, out var definition)) return false;
        var tile = new LiveMonitorTile
        {
            MetricId = metricId,
            DisplayStyle = definition.DefaultStyle,
            Unit = definition.DefaultUnit,
            Precision = definition.DefaultPrecision
        };
        var candidate = Clone(layout);
        candidate.Tiles.Add(tile);
        if (!TryPack(candidate, tile.Id, row, column)) return false;
        CopyInto(candidate, layout);
        tileId = tile.Id;
        return true;
    }

    public static bool TryReplaceMetric(LiveMonitorNamedLayout layout, string tileId, string metricId)
    {
        if (!LiveTelemetryCatalog.TryGet(metricId, out var definition)) return false;
        var candidate = Clone(layout);
        var tile = candidate.Tiles.FirstOrDefault(item => item.Id == tileId);
        if (tile is null || string.Equals(tile.MetricId, metricId, StringComparison.Ordinal)) return false;

        tile.MetricId = metricId;
        tile.DisplayStyle = definition.DefaultStyle;
        tile.Unit = definition.DefaultUnit;
        tile.Precision = definition.DefaultPrecision;
        tile.TrendDuration = LiveMonitorTrendDuration.Seconds30;
        tile.Accent = "default";
        CopyInto(candidate, layout);
        return true;
    }

    public static bool TryMoveTile(LiveMonitorNamedLayout layout, string tileId, int row, int column)
    {
        var tile = layout.Tiles.FirstOrDefault(item => item.Id == tileId);
        return tile is not null && TryPlaceTile(layout, tileId, row, column, tile.RowSpan, tile.ColumnSpan);
    }

    public static bool TryResizeTile(LiveMonitorNamedLayout layout, string tileId, int rowSpan, int columnSpan)
    {
        var tile = layout.Tiles.FirstOrDefault(item => item.Id == tileId);
        return tile is not null && TryPlaceTile(layout, tileId, tile.Row, tile.Column, rowSpan, columnSpan);
    }

    public static bool TryPlaceTile(LiveMonitorNamedLayout layout, string tileId, int row, int column, int rowSpan, int columnSpan)
    {
        var candidate = Clone(layout);
        var tile = candidate.Tiles.FirstOrDefault(item => item.Id == tileId);
        if (tile is null) return false;
        tile.RowSpan = Math.Clamp(rowSpan, 1, candidate.Rows);
        tile.ColumnSpan = Math.Clamp(columnSpan, 1, candidate.Columns);
        tile.Row = Math.Clamp(row, 0, candidate.Rows - tile.RowSpan);
        tile.Column = Math.Clamp(column, 0, candidate.Columns - tile.ColumnSpan);
        if (!TryPack(candidate, tileId, tile.Row, tile.Column)) return false;
        if (SamePlacement(layout, candidate)) return false;
        CopyInto(candidate, layout);
        return true;
    }

    public static bool TryResizeGrid(LiveMonitorNamedLayout layout, int rows, int columns, out int movedTiles)
    {
        movedTiles = 0;
        rows = Math.Clamp(rows, MinimumGrid, MaximumGrid);
        columns = Math.Clamp(columns, MinimumGrid, MaximumGrid);
        var candidate = Clone(layout);
        candidate.Rows = rows;
        candidate.Columns = columns;
        if (candidate.Tiles.Any(tile => tile.RowSpan > rows || tile.ColumnSpan > columns)) return false;
        var before = candidate.Tiles.ToDictionary(tile => tile.Id, tile => (tile.Row, tile.Column));
        if (!TryPack(candidate, null, null, null)) return false;
        movedTiles = candidate.Tiles.Count(tile => before.TryGetValue(tile.Id, out var value) && value != (tile.Row, tile.Column));
        CopyInto(candidate, layout);
        return true;
    }

    public static bool RemoveTile(LiveMonitorNamedLayout layout, string tileId) =>
        layout.Tiles.RemoveAll(tile => tile.Id == tileId) > 0;

    public static bool ValidateAndRepair(LiveMonitorLayout preferences, out bool corruptionFound)
    {
        corruptionFound = false;
        var changed = false;
        preferences.UserLayouts ??= [];
        var validLayouts = new List<LiveMonitorNamedLayout>();
        var knownIds = new HashSet<string>(Factory.Select(layout => layout.Id), StringComparer.Ordinal);
        foreach (var layout in preferences.UserLayouts)
        {
            if (layout is null || string.IsNullOrWhiteSpace(layout.Id) || string.IsNullOrWhiteSpace(layout.Name) || !knownIds.Add(layout.Id) ||
                layout.Rows is < MinimumGrid or > MaximumGrid || layout.Columns is < MinimumGrid or > MaximumGrid || layout.Tiles is null ||
                layout.Tiles.Any(tile => tile is null || string.IsNullOrWhiteSpace(tile.Id) || !LiveTelemetryCatalog.TryGet(tile.MetricId, out _)))
            {
                corruptionFound = true;
                changed = true;
                continue;
            }
            var candidate = Clone(layout);
            var tileIds = new HashSet<string>(StringComparer.Ordinal);
            foreach (var tile in candidate.Tiles)
            {
                if (!tileIds.Add(tile.Id))
                {
                    var suffix = 2;
                    var repairedId = $"{tile.Id}-repaired";
                    while (tileIds.Contains(repairedId) || candidate.Tiles.Any(item => item != tile && item.Id == repairedId))
                        repairedId = $"{tile.Id}-repaired-{suffix++}";
                    tile.Id = repairedId;
                    tileIds.Add(repairedId);
                    changed = true;
                    corruptionFound = true;
                }

                var definition = LiveTelemetryCatalog.Get(tile.MetricId);
                if (!definition.Styles.Contains(tile.DisplayStyle))
                {
                    tile.DisplayStyle = definition.DefaultStyle;
                    changed = true;
                    corruptionFound = true;
                }
                if (!definition.Units.Contains(tile.Unit, StringComparer.Ordinal))
                {
                    tile.Unit = definition.DefaultUnit;
                    changed = true;
                    corruptionFound = true;
                }
                var precision = Math.Clamp(tile.Precision, 0, 3);
                if (tile.Precision != precision)
                {
                    tile.Precision = precision;
                    changed = true;
                    corruptionFound = true;
                }
                if (!Enum.IsDefined(typeof(LiveMonitorTrendDuration), tile.TrendDuration))
                {
                    tile.TrendDuration = LiveMonitorTrendDuration.Seconds30;
                    changed = true;
                    corruptionFound = true;
                }
                var accent = AllowedAccents.FirstOrDefault(value => string.Equals(value, tile.Accent, StringComparison.OrdinalIgnoreCase));
                if (accent is null || !string.Equals(accent, tile.Accent, StringComparison.Ordinal))
                {
                    tile.Accent = accent ?? "default";
                    changed = true;
                    corruptionFound = true;
                }
            }
            if (!TryPack(candidate, null, null, null))
            {
                corruptionFound = true;
                changed = true;
                continue;
            }
            if (!SamePlacement(layout, candidate)) changed = true;
            validLayouts.Add(candidate);
        }
        preferences.UserLayouts = validLayouts;
        if (InitializeBuiltInDashboards(preferences)) changed = true;
        if (!IsFactory(preferences.ActiveLayoutId) && preferences.UserLayouts.All(layout => layout.Id != preferences.ActiveLayoutId))
        {
            preferences.ActiveLayoutId = LiveMonitorLayout.FactoryDefaultId;
            changed = true;
        }
        if (!double.IsFinite(preferences.OverallScale) || preferences.OverallScale is < .7 or > 2)
        {
            preferences.OverallScale = 1;
            changed = true;
            corruptionFound = true;
        }
        return changed;
    }

    public static LiveMonitorNamedLayout Clone(LiveMonitorNamedLayout source) => new()
    {
        Id = source.Id,
        Name = source.Name,
        Rows = source.Rows,
        Columns = source.Columns,
        Tiles = source.Tiles.Select(tile => new LiveMonitorTile
        {
            Id = tile.Id,
            MetricId = tile.MetricId,
            Row = tile.Row,
            Column = tile.Column,
            RowSpan = tile.RowSpan,
            ColumnSpan = tile.ColumnSpan,
            DisplayStyle = tile.DisplayStyle,
            Unit = tile.Unit,
            Precision = tile.Precision,
            TrendDuration = tile.TrendDuration,
            Accent = tile.Accent
        }).ToList()
    };

    public static string EditorSignature(LiveMonitorLayout preferences) =>
        $"{preferences.BuiltInDashboardsInitialized}/{preferences.ActiveLayoutId}|{string.Join(";", preferences.UserLayouts.Select(layout => $"{layout.Id}/{layout.Name}/{layout.Rows}/{layout.Columns}:{string.Join(",", layout.Tiles.Select(tile => $"{tile.Id}/{tile.MetricId}/{tile.Row}/{tile.Column}/{tile.RowSpan}/{tile.ColumnSpan}/{tile.DisplayStyle}/{tile.Unit}/{tile.Precision}/{tile.TrendDuration}/{tile.Accent}"))}"))}";

    private static bool InitializeBuiltInDashboards(LiveMonitorLayout preferences)
    {
        preferences.UserLayouts ??= [];
        if (preferences.BuiltInDashboardsInitialized) return false;

        var seeded = BuiltInUserTemplates
            .Where(template => preferences.UserLayouts.All(layout => !string.Equals(layout.Id, template.Id, StringComparison.Ordinal)))
            .Select(Clone)
            .ToArray();
        if (seeded.Length > 0) preferences.UserLayouts.InsertRange(0, seeded);
        preferences.BuiltInDashboardsInitialized = true;
        return true;
    }

    private static LiveMonitorNamedLayout BuildFactory(string id, string name, params (string Metric, LiveMonitorDisplayStyle Style)[] metrics)
    {
        var layout = new LiveMonitorNamedLayout { Id = id, Name = name, Rows = 2, Columns = 3 };
        for (var index = 0; index < metrics.Length; index++)
        {
            var definition = LiveTelemetryCatalog.Get(metrics[index].Metric);
            layout.Tiles.Add(new LiveMonitorTile
            {
                Id = $"{id}-tile-{index + 1}",
                MetricId = definition.Id,
                Row = index / 3,
                Column = index % 3,
                DisplayStyle = definition.Styles.Contains(metrics[index].Style) ? metrics[index].Style : definition.DefaultStyle,
                Unit = definition.DefaultUnit,
                Precision = definition.DefaultPrecision
            });
        }
        return layout;
    }

    private static bool TryPack(LiveMonitorNamedLayout layout, string? firstTileId, int? preferredRow, int? preferredColumn)
    {
        var occupied = new bool[layout.Rows, layout.Columns];
        var ordered = layout.Tiles
            .OrderBy(tile => tile.Id == firstTileId ? 0 : 1)
            .ThenBy(tile => tile.Row)
            .ThenBy(tile => tile.Column)
            .ThenBy(tile => tile.Id, StringComparer.Ordinal)
            .ToArray();
        foreach (var tile in ordered)
        {
            tile.RowSpan = Math.Clamp(tile.RowSpan, 1, layout.Rows);
            tile.ColumnSpan = Math.Clamp(tile.ColumnSpan, 1, layout.Columns);
            var requestedRow = tile.Id == firstTileId && preferredRow.HasValue ? preferredRow.Value : tile.Row;
            var requestedColumn = tile.Id == firstTileId && preferredColumn.HasValue ? preferredColumn.Value : tile.Column;
            if (!Fits(occupied, requestedRow, requestedColumn, tile.RowSpan, tile.ColumnSpan))
            {
                if (!FindFirstFit(occupied, tile.RowSpan, tile.ColumnSpan, out requestedRow, out requestedColumn)) return false;
            }
            tile.Row = requestedRow;
            tile.Column = requestedColumn;
            Occupy(occupied, tile);
        }
        return true;
    }

    private static bool Fits(bool[,] occupied, int row, int column, int rowSpan, int columnSpan)
    {
        if (row < 0 || column < 0 || row + rowSpan > occupied.GetLength(0) || column + columnSpan > occupied.GetLength(1)) return false;
        for (var r = row; r < row + rowSpan; r++)
            for (var c = column; c < column + columnSpan; c++)
                if (occupied[r, c]) return false;
        return true;
    }

    private static bool FindFirstFit(bool[,] occupied, int rowSpan, int columnSpan, out int row, out int column)
    {
        for (row = 0; row <= occupied.GetLength(0) - rowSpan; row++)
            for (column = 0; column <= occupied.GetLength(1) - columnSpan; column++)
                if (Fits(occupied, row, column, rowSpan, columnSpan)) return true;
        row = column = -1;
        return false;
    }

    private static void Occupy(bool[,] occupied, LiveMonitorTile tile)
    {
        for (var row = tile.Row; row < tile.Row + tile.RowSpan; row++)
            for (var column = tile.Column; column < tile.Column + tile.ColumnSpan; column++)
                occupied[row, column] = true;
    }

    private static void CopyInto(LiveMonitorNamedLayout source, LiveMonitorNamedLayout target)
    {
        target.Name = source.Name;
        target.Rows = source.Rows;
        target.Columns = source.Columns;
        target.Tiles = Clone(source).Tiles;
    }

    private static bool SamePlacement(LiveMonitorNamedLayout left, LiveMonitorNamedLayout right) =>
        left.Rows == right.Rows && left.Columns == right.Columns && left.Tiles.Count == right.Tiles.Count &&
        left.Tiles.Zip(right.Tiles).All(pair => pair.First.Id == pair.Second.Id && pair.First.Row == pair.Second.Row && pair.First.Column == pair.Second.Column && pair.First.RowSpan == pair.Second.RowSpan && pair.First.ColumnSpan == pair.Second.ColumnSpan);

    private static string UniqueName(LiveMonitorLayout preferences, string basis)
    {
        var used = Choices(preferences).Select(choice => choice.Layout.Name).ToHashSet(StringComparer.OrdinalIgnoreCase);
        if (!used.Contains(basis)) return basis;
        for (var index = 2; ; index++) if (!used.Contains($"{basis} {index}")) return $"{basis} {index}";
    }

    private static readonly string[] AllowedAccents = ["default", "blue", "green", "amber", "coral", "violet"];
}
