using System.Text.RegularExpressions;

namespace iRacingCoach.Coordinator;

/// <summary>
/// iRacing writes the last run's tire readings (hot pressures, temperatures, tread)
/// and a revision counter into a recording's CarSetup. They describe the run, not the
/// garage setup, so setup comparisons skip them. Mirrors the backend rule in
/// <c>setup_recordings.is_setup_reading</c>, which strips the same leaves from the
/// values it attributes to a setup file.
/// </summary>
public static partial class SetupReadings
{
    private static readonly string[] ReadingPrefixes = ["lasthotpressure", "lasttemps", "treadremaining"];

    public static bool IsRunReading(string? key)
    {
        var leaf = (key ?? string.Empty).Split('.').LastOrDefault() ?? string.Empty;
        var normalized = NonAlphanumeric().Replace(leaf.ToLowerInvariant(), string.Empty);
        return normalized == "updatecount" || ReadingPrefixes.Any(prefix => normalized.StartsWith(prefix, StringComparison.Ordinal));
    }

    [GeneratedRegex("[^a-z0-9]+", RegexOptions.CultureInvariant)]
    private static partial Regex NonAlphanumeric();
}
