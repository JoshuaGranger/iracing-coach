using System.Globalization;
using System.Text.RegularExpressions;
using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

/// <summary>A same-car setup that is not offered for comparison, and why (SU-011).</summary>
public sealed record SetupComparisonExclusion(LocalSetup Setup, string Reason);

/// <summary>A labelled group of the Compare list: setups recorded at the selected setup's track, then the rest.</summary>
public sealed record SetupCandidateGroup(string Label, IReadOnlyList<LocalSetup> Setups);

/// <summary>
/// Which setups compare with which. The Setups page's Compare list, its excluded-setup
/// note and the setup-comparison capability all use this one rule: same car (by iRacing
/// car path), values known on both sides, one value naming and unit scheme, and one
/// entry per set of garage adjustments.
/// </summary>
public static partial class SetupComparability
{
    public const string UnknownValuesReason = "Values unknown";
    public const string ExportSchemeReason = "Values only from an exported setup sheet, in its own names and units";
    public const string RecordingSchemeReason = "Recorded values, in iRacing's garage names and units";
    public const string SameAdjustmentsAsSelectedReason = "Same garage adjustments as the selected setup";
    public const string SameTrackGroup = "Same track";
    public const string OtherTracksGroup = "Other tracks";

    /// <summary>
    /// Setups of one car share its iRacing car path: a setups folder and a recording's
    /// CarPath name it identically, while display names differ between the two
    /// ("Camaro 2019" and "NASCAR Xfinity Chevrolet Camaro"). Only a setup mapped
    /// without a car path falls back to its display name.
    /// </summary>
    public static bool SameCar(LocalSetup left, LocalSetup right)
    {
        var leftKey = CarKey(left);
        var rightKey = CarKey(right);
        return leftKey.Length > 0 && rightKey.Length > 0
            ? string.Equals(leftKey, rightKey, StringComparison.OrdinalIgnoreCase)
            : string.Equals(left.Car, right.Car, StringComparison.OrdinalIgnoreCase);
    }

    /// <summary>Known garage values; run readings alone do not make a setup's values known.</summary>
    public static bool HasValues(LocalSetup setup) => SetupReadings.Values(setup).Count > 0;

    // Recorded values use iRacing's garage names and units; an exported setup
    // sheet uses its own names and display units, so the two never compare as equal.
    public static string ValueScheme(LocalSetup setup) => setup.ValuesSource == "export" ? "export" : "recording";

    /// <summary>
    /// Other setups of the same car whose values are known and comparable with this one's:
    /// one entry per set of garage adjustments (a linked file and every recording that loaded
    /// it unchanged are one setup), none with the selected setup's own adjustments, recorded
    /// at the selected setup's track first and newest first.
    /// </summary>
    public static IReadOnlyList<LocalSetup> Candidates(IEnumerable<LocalSetup> setups, LocalSetup setup) =>
        Groups(setups, setup).SelectMany(group => group.Setups).ToArray();

    /// <summary>The Compare list in its groups: same track, then other tracks; empty groups are left out.</summary>
    public static IReadOnlyList<SetupCandidateGroup> Groups(IEnumerable<LocalSetup> setups, LocalSetup setup)
    {
        if (!HasValues(setup)) return [];
        var ordered = Distinct(setups, setup)
            .OrderByDescending(item => SameTrack(item, setup))
            .ThenByDescending(Recorded)
            .ThenBy(item => item.Name, StringComparer.CurrentCultureIgnoreCase)
            .ToArray();
        return new[]
            {
                new SetupCandidateGroup(SameTrackGroup, ordered.Where(item => SameTrack(item, setup)).ToArray()),
                new SetupCandidateGroup(OtherTracksGroup, ordered.Where(item => !SameTrack(item, setup)).ToArray())
            }
            .Where(group => group.Setups.Count > 0).ToArray();
    }

    /// <summary>Every other setup of this car that is not offered for comparison, each with its reason.</summary>
    public static IReadOnlyList<SetupComparisonExclusion> Exclusions(IEnumerable<LocalSetup> setups, LocalSetup setup)
    {
        if (!HasValues(setup)) return [];
        var all = setups.ToArray();
        var offered = Distinct(all, setup).Select(item => item.Id).ToHashSet(StringComparer.Ordinal);
        var comparable = all.Where(item => item.Id != setup.Id && SameCar(item, setup) && Exclusion(item, setup) is null).ToArray();
        return all.Where(item => item.Id != setup.Id && SameCar(item, setup) && !offered.Contains(item.Id))
            .Select(item => new SetupComparisonExclusion(item, Exclusion(item, setup) ?? DuplicateReason(item, setup, comparable)))
            .ToArray();
    }

    /// <summary>
    /// True when the selected setup (or, with none selected, any setup) has a comparison
    /// the Setups page would offer.
    /// </summary>
    public static bool Any(IEnumerable<LocalSetup> setups, string? selectedSetupId = null)
    {
        // A candidate always has known values, so only those setups can pair up.
        var known = setups.Where(HasValues).ToArray();
        if (string.IsNullOrWhiteSpace(selectedSetupId)) return known.Any(setup => Candidates(known, setup).Count > 0);
        var selected = known.FirstOrDefault(setup => string.Equals(setup.Id, selectedSetupId, StringComparison.Ordinal));
        return selected is not null && Candidates(known, selected).Count > 0;
    }

    /// <summary>
    /// Whether the candidate was recorded at the selected setup's track. Recorded setups name
    /// the track and layout ("Lucas Oil Indianapolis Raceway Park · IRP"); a shop file name
    /// names a short form ("IRP"), so one name's words all appearing in the other's counts.
    /// </summary>
    public static bool SameTrack(LocalSetup candidate, LocalSetup selected)
    {
        var left = TrackWords(candidate.Track);
        var right = TrackWords(selected.Track);
        if (left.Count == 0 || right.Count == 0) return false;
        return left.IsSubsetOf(right) || right.IsSubsetOf(left);
    }

    /// <summary>When the setup's values were recorded; a setup without a recording sorts last.</summary>
    public static DateTimeOffset Recorded(LocalSetup setup) =>
        DateTimeOffset.TryParse(setup.RecordedUtc, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out var recorded)
            ? recorded : DateTimeOffset.MinValue;

    private static IEnumerable<LocalSetup> Distinct(IEnumerable<LocalSetup> setups, LocalSetup setup) =>
        setups.Where(item => item.Id != setup.Id && SameCar(item, setup) && Exclusion(item, setup) is null && !SameAdjustments(item, setup))
            .GroupBy(item => item.GarageFingerprint.Length > 0 ? "adjustments:" + item.GarageFingerprint : "setup:" + item.Id, StringComparer.Ordinal)
            .Select(group => Representative(group));

    // A named file is what the driver loads, so it stands for the recordings that loaded it.
    private static LocalSetup Representative(IEnumerable<LocalSetup> duplicates) => duplicates
        .OrderByDescending(IsSetupFile)
        .ThenByDescending(Recorded)
        .ThenBy(item => item.Id, StringComparer.Ordinal)
        .First();

    private static bool SameAdjustments(LocalSetup item, LocalSetup selected) =>
        item.GarageFingerprint.Length > 0 && string.Equals(item.GarageFingerprint, selected.GarageFingerprint, StringComparison.Ordinal);

    private static string DuplicateReason(LocalSetup item, LocalSetup selected, IReadOnlyList<LocalSetup> comparable)
    {
        if (SameAdjustments(item, selected)) return SameAdjustmentsAsSelectedReason;
        var representative = Representative(comparable.Where(candidate => candidate.GarageFingerprint == item.GarageFingerprint));
        return $"Same garage adjustments as {representative.Name}";
    }

    private static bool IsSetupFile(LocalSetup setup) => Path.GetExtension(setup.StoPath).Equals(".sto", StringComparison.OrdinalIgnoreCase);

    private static string? Exclusion(LocalSetup item, LocalSetup selected) =>
        !HasValues(item) ? UnknownValuesReason
        : ValueScheme(item) != ValueScheme(selected) ? (ValueScheme(item) == "export" ? ExportSchemeReason : RecordingSchemeReason)
        : null;

    private static HashSet<string> TrackWords(string? track) =>
        NonWord().Split((track ?? string.Empty).ToLowerInvariant()).Where(word => word.Length > 0).ToHashSet(StringComparer.Ordinal);

    private static string CarKey(LocalSetup setup) => (setup.CarKey ?? string.Empty).Trim().Replace('\\', '/');

    [GeneratedRegex("[^a-z0-9]+", RegexOptions.CultureInvariant)]
    private static partial Regex NonWord();
}
