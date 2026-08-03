using System.Security.Cryptography;
using System.Text;
using System.IO;
using iRacingCoach.Contracts;

namespace iRacingCoach.App;

internal sealed record QaLaunchOptions(
    bool Enabled,
    string? FixtureRoot,
    string? ArchiveRoot,
    string? LiveReplayPath,
    double TimeScale,
    string Scenario,
    string? Page,
    string? Size,
    bool OpenMonitor)
{
    public static QaLaunchOptions Parse(IReadOnlyList<string> arguments)
    {
        var explicitFixture = Option(arguments, "--qa-fixture-root");
        var replay = Option(arguments, "--qa-live-replay");
        var enabled = !string.IsNullOrWhiteSpace(explicitFixture) || !string.IsNullOrWhiteSpace(replay);
        if (!enabled) return new(false, null, null, null, 1, string.Empty, null, null, false);

        var fixture = string.IsNullOrWhiteSpace(explicitFixture) ? InferPacketRoot(replay!) : Path.GetFullPath(explicitFixture);
        if (!Directory.Exists(fixture)) throw new DirectoryNotFoundException($"The QA fixture root does not exist: {fixture}");
        replay = string.IsNullOrWhiteSpace(replay) ? null : Path.GetFullPath(replay);
        if (replay is not null && !File.Exists(replay)) throw new FileNotFoundException("The QA live replay file does not exist.", replay);

        var archiveOption = Option(arguments, "--qa-archive-root");
        if (!string.IsNullOrWhiteSpace(explicitFixture) && string.IsNullOrWhiteSpace(archiveOption))
            throw new ArgumentException("--qa-archive-root is required with --qa-fixture-root so real Coach data cannot be touched.");
        var archive = string.IsNullOrWhiteSpace(archiveOption) ? AutomaticArchiveRoot(fixture, replay) : Path.GetFullPath(archiveOption);
        EnsureIsolatedArchive(archive);

        var scaleText = Option(arguments, "--qa-time-scale");
        var scale = string.IsNullOrWhiteSpace(scaleText) ? 1 : double.TryParse(scaleText, out var parsed) ? parsed : double.NaN;
        if (!double.IsFinite(scale) || scale is <= 0 or > 100) throw new ArgumentOutOfRangeException(nameof(arguments), "--qa-time-scale must be greater than 0 and no more than 100.");

        return new(
            true,
            fixture,
            archive,
            replay,
            scale,
            Option(arguments, "--qa-scenario")?.Trim().ToLowerInvariant() ?? "kentucky",
            Option(arguments, "--qa-page")?.Trim().ToLowerInvariant(),
            Option(arguments, "--qa-size")?.Trim().ToLowerInvariant(),
            arguments.Any(argument => string.Equals(argument, "--qa-open-monitor", StringComparison.OrdinalIgnoreCase)));
    }

    private static string? Option(IReadOnlyList<string> arguments, string name)
    {
        for (var index = 0; index < arguments.Count; index++)
        {
            var argument = arguments[index];
            if (argument.StartsWith(name + "=", StringComparison.OrdinalIgnoreCase)) return argument[(name.Length + 1)..].Trim('"');
            if (string.Equals(argument, name, StringComparison.OrdinalIgnoreCase) && index + 1 < arguments.Count) return arguments[index + 1].Trim('"');
        }
        return null;
    }

    private static string InferPacketRoot(string replay)
    {
        var directory = new FileInfo(Path.GetFullPath(replay)).Directory;
        while (directory is not null)
        {
            if (File.Exists(Path.Combine(directory.FullName, "fixture-manifest.json")) && Directory.Exists(Path.Combine(directory.FullName, "fixtures")))
                return directory.FullName;
            directory = directory.Parent;
        }
        throw new DirectoryNotFoundException("The packet root could not be inferred from --qa-live-replay; also supply --qa-fixture-root and --qa-archive-root.");
    }

    private static string AutomaticArchiveRoot(string fixture, string? replay)
    {
        var identity = fixture + "|" + replay;
        var suffix = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(identity)))[..12];
        return Path.Combine(Path.GetTempPath(), "iRacingCoach-QA", suffix);
    }

    private static void EnsureIsolatedArchive(string archive)
    {
        var candidate = Path.GetFullPath(archive).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        var durable = Path.GetFullPath(CompanionSettings.DefaultCoachHome).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
        if (candidate.Equals(durable, StringComparison.OrdinalIgnoreCase) || candidate.StartsWith(durable + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("QA archive data cannot be stored inside the real Documents\\iRacing Coach folder.");
        Directory.CreateDirectory(candidate);
    }
}
