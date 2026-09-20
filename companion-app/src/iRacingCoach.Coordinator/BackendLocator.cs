namespace iRacingCoach.Coordinator;

/// <summary>Bind the host to its shipped backend before considering a development checkout.</summary>
public static class BackendLocator
{
    public static string? Find(string relativePath, string applicationDirectory, string workingDirectory)
    {
        foreach (var start in new[] { applicationDirectory, workingDirectory })
        {
            var current = new DirectoryInfo(start);
            while (current is not null)
            {
                var candidate = Path.Combine(current.FullName, relativePath);
                if (File.Exists(candidate)) return candidate;
                current = current.Parent;
            }
        }
        return null;
    }
}
