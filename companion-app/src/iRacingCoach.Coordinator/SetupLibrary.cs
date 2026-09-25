using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

/// <summary>
/// The Setups library as the driver reads it: one entry per setup he loads. The IRP Supra
/// test day listed twelve "Recorded telemetry" entries beside the six .sto files they came
/// from. A recorded setup whose recordings all loaded one library file unchanged (the
/// backend's link: DriverSetupName, user load type, not modified) and drove exactly the
/// garage adjustments shown for that file (the backend's verdict, with run readings and
/// computed readouts set aside) is listed as that file. Every other recorded setup stays;
/// one whose recording loaded a file but drove different adjustments says so. Nothing is
/// removed from <see cref="CompanionState.Setups"/> or from disk: tuning still starts from
/// a recording's own setup.
/// </summary>
public static class SetupLibrary
{
    public static IReadOnlyList<LocalSetup> Group(IEnumerable<LocalSetup> setups)
    {
        var all = setups.ToArray();
        // Each linked recording names one file; a recording claimed by two files is left unmatched.
        var verdicts = new Dictionary<string, (LocalSetup File, bool Match)?>(StringComparer.OrdinalIgnoreCase);
        foreach (var file in all.Where(IsSetupFile))
        {
            foreach (var (path, match) in (file.MatchingAnalysisPaths ?? []).Select(path => (path, true))
                .Concat((file.DifferingAnalysisPaths ?? []).Select(path => (path, false))))
            {
                var key = PathKey(path);
                if (key.Length == 0) continue;
                if (!verdicts.TryGetValue(key, out var existing)) verdicts[key] = (file, match);
                else if (existing is not { } seen || seen.File.Id != file.Id) verdicts[key] = null;
            }
        }
        if (verdicts.Count == 0) return all;

        var absorbed = new Dictionary<string, List<LocalSetup>>(StringComparer.Ordinal);
        var differs = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (var recorded in all.Where(setup => !IsSetupFile(setup)))
        {
            // A recorded setup is one whole CarSetup (tire readings included), so each of its
            // analyses is the same recording; one the archive index no longer lists has no verdict.
            var found = RecordingPaths(recorded)
                .Select(path => verdicts.TryGetValue(path, out var verdict) ? verdict : null)
                .OfType<(LocalSetup File, bool Match)>().ToArray();
            if (found.Length == 0) continue;
            if (found.All(verdict => verdict.Match) && found.Select(verdict => verdict.File.Id).Distinct(StringComparer.Ordinal).Count() == 1)
            {
                var fileId = found[0].File.Id;
                if (!absorbed.TryGetValue(fileId, out var list)) absorbed[fileId] = list = [];
                list.Add(recorded);
            }
            else if (found.Where(verdict => !verdict.Match).Select(verdict => verdict.File.Name).FirstOrDefault() is { } different)
                differs[recorded.Id] = different;
        }
        var hidden = absorbed.Values.SelectMany(list => list).Select(setup => setup.Id).ToHashSet(StringComparer.Ordinal);
        return all.Where(setup => !hidden.Contains(setup.Id))
            .Select(setup => absorbed.TryGetValue(setup.Id, out var group) && IsSetupFile(setup)
                ? setup with
                {
                    GroupedSetupIds = group.Select(item => item.Id).ToArray(),
                    SearchAliases = string.Join(' ', group.SelectMany(item => new[] { item.Name, item.Car, item.CarKey, item.Track, item.Season, item.Role })
                        .Where(text => !string.IsNullOrWhiteSpace(text)).Distinct(StringComparer.CurrentCultureIgnoreCase))
                }
                : differs.TryGetValue(setup.Id, out var file) ? setup with { DiffersFromFile = file } : setup)
            .ToArray();
    }

    /// <summary>The library entry that lists this setup: the file standing for a grouped recording, else the setup itself.</summary>
    public static string EntryId(IReadOnlyList<LocalSetup> library, string? setupId)
    {
        if (string.IsNullOrEmpty(setupId)) return string.Empty;
        if (library.Any(setup => setup.Id == setupId)) return setupId;
        return library.FirstOrDefault(setup => (setup.GroupedSetupIds ?? []).Contains(setupId, StringComparer.Ordinal))?.Id ?? setupId;
    }

    public static bool IsSetupFile(LocalSetup setup) => Path.GetExtension(setup.StoPath).Equals(".sto", StringComparison.OrdinalIgnoreCase);

    private static IEnumerable<string> RecordingPaths(LocalSetup setup) =>
        (setup.AnalysisPaths ?? []).Append(setup.ValuesAnalysisPath).Select(PathKey).Where(path => path.Length > 0)
            .Distinct(StringComparer.OrdinalIgnoreCase);

    // The archive index and the reports folder can spell one report with either separator.
    private static string PathKey(string? path) => (path ?? string.Empty).Trim().Replace('/', '\\');
}
