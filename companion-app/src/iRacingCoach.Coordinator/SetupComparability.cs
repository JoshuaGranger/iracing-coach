using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

/// <summary>A same-car setup that is not offered for comparison, and why (SU-011).</summary>
public sealed record SetupComparisonExclusion(LocalSetup Setup, string Reason);

/// <summary>
/// Which setups compare with which. The Setups page's Compare list, its excluded-setup
/// note and the setup-comparison capability all use this one rule: same car (by iRacing
/// car path), values known on both sides, and one value naming and unit scheme.
/// </summary>
public static class SetupComparability
{
    public const string UnknownValuesReason = "Values unknown";
    public const string ExportSchemeReason = "Values only from an exported setup sheet, in its own names and units";
    public const string RecordingSchemeReason = "Recorded values, in iRacing's garage names and units";

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

    public static bool HasValues(LocalSetup setup) => setup.Fields.Count > 0;

    // Recorded values use iRacing's garage names and units; an exported setup
    // sheet uses its own names and display units, so the two never compare as equal.
    public static string ValueScheme(LocalSetup setup) => setup.ValuesSource == "export" ? "export" : "recording";

    /// <summary>Other setups of the same car whose values are known and comparable with this one's.</summary>
    public static IReadOnlyList<LocalSetup> Candidates(IEnumerable<LocalSetup> setups, LocalSetup setup) => !HasValues(setup) ? []
        : setups.Where(item => item.Id != setup.Id && SameCar(item, setup) && Exclusion(item, setup) is null).ToArray();

    /// <summary>Every other setup of this car that is not offered for comparison, each with its reason.</summary>
    public static IReadOnlyList<SetupComparisonExclusion> Exclusions(IEnumerable<LocalSetup> setups, LocalSetup setup) => !HasValues(setup) ? []
        : setups.Where(item => item.Id != setup.Id && SameCar(item, setup))
            .Select(item => Exclusion(item, setup) is { } reason ? new SetupComparisonExclusion(item, reason) : null)
            .OfType<SetupComparisonExclusion>().ToArray();

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

    private static string? Exclusion(LocalSetup item, LocalSetup selected) =>
        !HasValues(item) ? UnknownValuesReason
        : ValueScheme(item) != ValueScheme(selected) ? (ValueScheme(item) == "export" ? ExportSchemeReason : RecordingSchemeReason)
        : null;

    private static string CarKey(LocalSetup setup) => (setup.CarKey ?? string.Empty).Trim().Replace('\\', '/');
}
