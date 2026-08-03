using System.Diagnostics;

namespace iRacingCoach.Coordinator;

public interface IGarage61CredentialStore
{
    bool IsConfigured { get; }
    string CredentialPath { get; }
    void Store(string token);
    void Remove();
}

public sealed class PowerShellGarage61CredentialStore : IGarage61CredentialStore
{
    private readonly string _scriptPath;

    public PowerShellGarage61CredentialStore(string? scriptPath = null, string? credentialPath = null)
    {
        _scriptPath = scriptPath ?? ResolveConfigurationScript();
        CredentialPath = credentialPath ?? Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "iRacingCoach",
            "credentials",
            "garage61.pat.dpapi");
    }

    public bool IsConfigured => File.Exists(CredentialPath);
    public string CredentialPath { get; }

    public void Store(string token)
    {
        ArgumentNullException.ThrowIfNull(token);
        var normalized = token.Trim();
        if (normalized.Length == 0)
            throw new ArgumentException("The Garage61 API key cannot be empty.", nameof(token));
        if (normalized.IndexOfAny(['\r', '\n', '\0']) >= 0)
            throw new ArgumentException("The Garage61 API key contains an invalid control character.", nameof(token));
        if (!OperatingSystem.IsWindows())
            throw new PlatformNotSupportedException("Garage61 credential storage requires Windows.");
        if (!File.Exists(_scriptPath))
            throw new FileNotFoundException("The packaged Garage61 credential helper is missing.", _scriptPath);

        var start = new ProcessStartInfo
        {
            FileName = "powershell.exe",
            UseShellExecute = false,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden
        };
        foreach (var argument in new[]
        {
            "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", _scriptPath, "-CredentialPath", CredentialPath, "-FromStdin", "-Quiet"
        })
        {
            start.ArgumentList.Add(argument);
        }

        using var process = Process.Start(start)
            ?? throw new IOException("Windows could not start the Garage61 credential helper.");
        process.StandardInput.WriteLine(normalized);
        process.StandardInput.Close();
        normalized = string.Empty;

        if (!process.WaitForExit(30_000))
        {
            try { process.Kill(entireProcessTree: true); } catch (InvalidOperationException) { }
            throw new TimeoutException("Saving the Garage61 connection timed out.");
        }
        if (process.ExitCode != 0 || !File.Exists(CredentialPath))
            throw new IOException("Windows could not protect the Garage61 connection for this user.");
    }

    public void Remove()
    {
        if (File.Exists(CredentialPath)) File.Delete(CredentialPath);
    }

    private static string ResolveConfigurationScript()
    {
        var relative = Path.Combine("iracing-coach", "skills", "analyze-iracing-race", "scripts", "configure-garage61.ps1");
        var packaged = Path.Combine(AppContext.BaseDirectory, relative);
        if (File.Exists(packaged)) return packaged;

        var current = new DirectoryInfo(Directory.GetCurrentDirectory());
        while (current is not null)
        {
            var candidate = Path.Combine(current.FullName, relative);
            if (File.Exists(candidate)) return candidate;
            current = current.Parent;
        }
        return packaged;
    }
}
