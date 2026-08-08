using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

public sealed record AnalysisTraceLayoutChoice(AnalysisTraceNamedLayout Named, bool IsFactory);

/// <summary>
/// Named-layout lifecycle for Race Analysis. The factory layout is returned as
/// a clone so it cannot be mutated; editing it first creates a portable copy.
/// </summary>
public static class AnalysisTraceLayoutSets
{
    private static readonly AnalysisTraceNamedLayout Factory = BuildFactory();

    public static IReadOnlyList<AnalysisTraceLayoutChoice> Choices(AnalysisTraceLayoutSet preferences)
    {
        ValidateAndRepair(preferences);
        return new[] { new AnalysisTraceLayoutChoice(Clone(Factory), true) }
            .Concat(preferences.UserLayouts.Select(layout => new AnalysisTraceLayoutChoice(layout, false)))
            .ToArray();
    }

    public static AnalysisTraceLayoutChoice Active(AnalysisTraceLayoutSet preferences)
    {
        ValidateAndRepair(preferences);
        if (IsFactory(preferences.ActiveLayoutId)) return new(Clone(Factory), true);
        var active = preferences.UserLayouts.FirstOrDefault(layout =>
            string.Equals(layout.Id, preferences.ActiveLayoutId, StringComparison.Ordinal));
        return active is null ? new(Clone(Factory), true) : new(active, false);
    }

    public static bool IsFactory(string? id) =>
        string.Equals(id, AnalysisTraceLayoutSet.FactoryDefaultId, StringComparison.Ordinal);

    public static AnalysisTraceNamedLayout EnsureEditable(AnalysisTraceLayoutSet preferences)
    {
        var active = Active(preferences);
        if (!active.IsFactory) return active.Named;
        var copy = Clone(active.Named);
        copy.Id = $"analysis-layout-{Guid.NewGuid():N}";
        copy.Name = UniqueName(preferences, "Default Copy");
        preferences.UserLayouts.Add(copy);
        preferences.ActiveLayoutId = copy.Id;
        return copy;
    }

    public static AnalysisTraceNamedLayout Create(AnalysisTraceLayoutSet preferences)
    {
        ValidateAndRepair(preferences);
        var created = Clone(Factory);
        created.Id = $"analysis-layout-{Guid.NewGuid():N}";
        created.Name = UniqueName(preferences, "Custom");
        preferences.UserLayouts.Add(created);
        preferences.ActiveLayoutId = created.Id;
        return created;
    }

    public static AnalysisTraceNamedLayout Duplicate(AnalysisTraceLayoutSet preferences)
    {
        var source = Active(preferences).Named;
        var duplicate = Clone(source);
        duplicate.Id = $"analysis-layout-{Guid.NewGuid():N}";
        duplicate.Name = UniqueName(preferences, $"{source.Name} Copy");
        foreach (var row in duplicate.Layout.Rows) row.Id = $"trace-row-{Guid.NewGuid():N}";
        preferences.UserLayouts.Add(duplicate);
        preferences.ActiveLayoutId = duplicate.Id;
        return duplicate;
    }

    public static bool DeleteActive(AnalysisTraceLayoutSet preferences)
    {
        ValidateAndRepair(preferences);
        if (IsFactory(preferences.ActiveLayoutId)) return false;
        var removed = preferences.UserLayouts.RemoveAll(layout =>
            string.Equals(layout.Id, preferences.ActiveLayoutId, StringComparison.Ordinal)) > 0;
        if (removed) preferences.ActiveLayoutId = AnalysisTraceLayoutSet.FactoryDefaultId;
        return removed;
    }

    public static bool RenameActive(AnalysisTraceLayoutSet preferences, string? requestedName)
    {
        var active = Active(preferences);
        if (active.IsFactory) return false;
        var normalized = NormalizeName(requestedName);
        if (normalized.Length == 0) return false;
        active.Named.Name = UniqueName(preferences, normalized, active.Named.Id);
        return true;
    }

    public static bool Select(AnalysisTraceLayoutSet preferences, string? id)
    {
        ValidateAndRepair(preferences);
        if (IsFactory(id))
        {
            preferences.ActiveLayoutId = AnalysisTraceLayoutSet.FactoryDefaultId;
            return true;
        }

        if (preferences.UserLayouts.All(layout => !string.Equals(layout.Id, id, StringComparison.Ordinal)))
            return false;
        preferences.ActiveLayoutId = id!;
        return true;
    }

    public static bool ValidateAndRepair(AnalysisTraceLayoutSet preferences, AnalysisTraceLayout? legacy = null)
    {
        var changed = false;
        preferences.ActiveLayoutId ??= AnalysisTraceLayoutSet.FactoryDefaultId;
        preferences.UserLayouts ??= [];

        var repaired = new List<AnalysisTraceNamedLayout>();
        var ids = new HashSet<string>(StringComparer.Ordinal);
        foreach (var source in preferences.UserLayouts)
        {
            if (source is null) { changed = true; continue; }
            source.Layout ??= new AnalysisTraceLayout();
            changed |= AnalysisTraceLayouts.ValidateAndRepair(source.Layout);
            if (string.IsNullOrWhiteSpace(source.Id) || IsFactory(source.Id) || !ids.Add(source.Id))
            {
                source.Id = $"analysis-layout-{Guid.NewGuid():N}";
                ids.Add(source.Id);
                changed = true;
            }
            var name = UniqueNameCore(repaired.Select(item => item.Name), NormalizeName(source.Name), "Custom");
            if (!string.Equals(name, source.Name, StringComparison.Ordinal))
            {
                source.Name = name;
                changed = true;
            }
            repaired.Add(source);
        }
        preferences.UserLayouts = repaired;

        // A pre-layout-set installation had one mutable trace layout. Preserve
        // a genuinely customized arrangement once, while leaving an untouched
        // default on the immutable factory layout. Completion must be durable:
        // an empty modern set may mean the user deliberately deleted its last
        // custom layout and must never make the legacy bridge eligible again.
        if (legacy is not null && !preferences.LegacyLayoutImportCompleted)
        {
            _ = AnalysisTraceLayouts.ValidateAndRepair(legacy);
            if (preferences.UserLayouts.Count == 0 && !Equivalent(legacy, Factory.Layout))
            {
                var migrated = new AnalysisTraceNamedLayout
                {
                    Name = "Previous layout",
                    Layout = CloneLayout(legacy)
                };
                preferences.UserLayouts.Add(migrated);
                preferences.ActiveLayoutId = migrated.Id;
            }
            preferences.LegacyLayoutImportCompleted = true;
            changed = true;
        }

        if (!IsFactory(preferences.ActiveLayoutId) &&
            preferences.UserLayouts.All(layout => !string.Equals(layout.Id, preferences.ActiveLayoutId, StringComparison.Ordinal)))
        {
            preferences.ActiveLayoutId = AnalysisTraceLayoutSet.FactoryDefaultId;
            changed = true;
        }
        return changed;
    }

    public static AnalysisTraceLayout CloneLayout(AnalysisTraceLayout source) => new()
    {
        Rows = source.Rows.Select(row => new AnalysisTraceRow
        {
            Id = row.Id,
            PrimarySignalId = row.PrimarySignalId,
            SecondarySignalId = row.SecondarySignalId
        }).ToList()
    };

    private static AnalysisTraceNamedLayout BuildFactory()
    {
        var layout = new AnalysisTraceLayout();
        _ = AnalysisTraceLayouts.ValidateAndRepair(layout);
        return new AnalysisTraceNamedLayout
        {
            Id = AnalysisTraceLayoutSet.FactoryDefaultId,
            Name = "Default",
            Layout = layout
        };
    }

    private static AnalysisTraceNamedLayout Clone(AnalysisTraceNamedLayout source) => new()
    {
        Id = source.Id,
        Name = source.Name,
        Layout = CloneLayout(source.Layout)
    };

    private static bool Equivalent(AnalysisTraceLayout left, AnalysisTraceLayout right) =>
        left.Rows.Count == right.Rows.Count && left.Rows.Zip(right.Rows).All(pair =>
            string.Equals(pair.First.PrimarySignalId, pair.Second.PrimarySignalId, StringComparison.Ordinal) &&
            string.Equals(pair.First.SecondarySignalId, pair.Second.SecondarySignalId, StringComparison.Ordinal));

    private static string UniqueName(AnalysisTraceLayoutSet preferences, string requested, string? excludeId = null) =>
        UniqueNameCore(
            preferences.UserLayouts.Where(layout => !string.Equals(layout.Id, excludeId, StringComparison.Ordinal)).Select(layout => layout.Name),
            NormalizeName(requested),
            "Custom");

    private static string UniqueNameCore(IEnumerable<string> existingNames, string requested, string fallback)
    {
        var baseName = requested.Length == 0 ? fallback : requested;
        var existing = existingNames.ToHashSet(StringComparer.OrdinalIgnoreCase);
        if (!existing.Contains(baseName)) return baseName;
        for (var suffix = 2; suffix < 10_000; suffix++)
        {
            var suffixText = $" {suffix}";
            var stem = baseName[..Math.Min(baseName.Length, 40 - suffixText.Length)].TrimEnd();
            var candidate = stem + suffixText;
            if (!existing.Contains(candidate)) return candidate;
        }
        return $"Layout {Guid.NewGuid():N}"[..40];
    }

    private static string NormalizeName(string? value)
    {
        if (string.IsNullOrWhiteSpace(value)) return string.Empty;
        var normalized = string.Join(' ', value.Trim().Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries));
        return normalized[..Math.Min(40, normalized.Length)];
    }
}
