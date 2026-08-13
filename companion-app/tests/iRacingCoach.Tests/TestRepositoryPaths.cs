namespace iRacingCoach.Tests;

internal static class TestRepositoryPaths
{
    public static string RepositoryRoot { get; } = LocateRepositoryRoot();
    public static string CompanionAppRoot { get; } = Path.Combine(RepositoryRoot, "companion-app");
    public static string UiRoot { get; } = Path.Combine(CompanionAppRoot, "src", "iRacingCoach.UI");

    private static string LocateRepositoryRoot()
    {
        foreach (var start in new[] { AppContext.BaseDirectory, Environment.CurrentDirectory })
        {
            for (var directory = new DirectoryInfo(start); directory is not null; directory = directory.Parent)
            {
                var solution = Path.Combine(directory.FullName, "companion-app", "iRacingCoach.sln");
                if (File.Exists(solution)) return directory.FullName;
            }
        }
        throw new DirectoryNotFoundException("Could not locate the iRacing Coach repository root.");
    }
}
