namespace iRacingCoach.Tests;

[TestClass]
public sealed class PreviewStaticAssetTests
{
    [TestMethod]
    public void PreviewHost_EnablesProductionStaticWebAssetsAndFingerprintedFavicon()
    {
        var root = CompanionAppRoot();
        var program = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.Preview", "Program.cs"));
        var host = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.Preview", "Components", "App.razor"));

        StringAssert.Contains(program, "builder.WebHost.UseStaticWebAssets();",
            "Release-mode Preview runs need the runtime manifest for referenced-project and framework assets.");
        StringAssert.Contains(host, "href=\"@Assets[\"favicon.png\"]\"",
            "The Preview favicon should use the same fingerprinted static-asset pipeline as CSS and JavaScript.");
    }

    private static string CompanionAppRoot()
    {
        var directory = new DirectoryInfo(AppContext.BaseDirectory);
        while (directory is not null && !File.Exists(Path.Combine(directory.FullName, "iRacingCoach.sln")))
            directory = directory.Parent;

        return directory?.FullName ?? throw new DirectoryNotFoundException("Could not find companion app root.");
    }
}
