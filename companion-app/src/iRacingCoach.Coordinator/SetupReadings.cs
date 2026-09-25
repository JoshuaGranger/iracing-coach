using System.Text.RegularExpressions;
using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

/// <summary>
/// iRacing writes the last run's tire readings (hot pressures, temperatures, tread)
/// and a revision counter into a recording's CarSetup and an exported setup sheet.
/// They describe the run, not the garage setup, so the mapper keeps them out of
/// <c>LocalSetup.Fields</c> (in <c>RunReadings</c>) and comparisons skip them. Mirrors the backend rule in
/// <c>setup_recordings.is_setup_reading</c>, which strips the same leaves from the
/// values it attributes to a setup file.
/// </summary>
public static partial class SetupReadings
{
    private static readonly string[] ReadingPrefixes = ["lasthotpressure", "lasttemps", "treadremaining"];

    /// <summary>The setup's garage values, without any run reading.</summary>
    public static IReadOnlyList<SetupField> Values(LocalSetup setup) => setup.Fields.Where(field => !IsRunReading(field.Key)).ToArray();

    /// <summary>The run readings stored with the setup, shown apart from its values.</summary>
    public static IReadOnlyList<SetupField> Readings(LocalSetup setup) =>
        (setup.RunReadings ?? []).Concat(setup.Fields.Where(field => IsRunReading(field.Key))).ToArray();

    public static bool IsRunReading(string? key)
    {
        var leaf = (key ?? string.Empty).Split('.').LastOrDefault() ?? string.Empty;
        var normalized = NonAlphanumeric().Replace(leaf.ToLowerInvariant(), string.Empty);
        return normalized == "updatecount" || ReadingPrefixes.Any(prefix => normalized.StartsWith(prefix, StringComparison.Ordinal));
    }

    [GeneratedRegex("[^a-z0-9]+", RegexOptions.CultureInvariant)]
    private static partial Regex NonAlphanumeric();
}
