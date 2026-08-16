using System.Text.Json;
using System.Diagnostics;
using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class HostIsolationTests
{
    [TestMethod]
    public void ProductionProfile_PreservesWindowsDefaultPathContract()
    {
        var profile = CompanionHostProfile.FromArguments(Array.Empty<string>());
        var settings = new CompanionSettings();

        Assert.IsFalse(profile.IsIsolated);
        Assert.IsTrue(profile.AllowMachineIntegration);
        Assert.IsTrue(profile.AllowEmbeddedBrowser);
        Assert.AreEqual("production", profile.ProcessIdentity);
        Assert.AreEqual(Path.GetFullPath(Path.Combine(profile.Paths.Documents, "iRacing Coach")), Path.GetFullPath(settings.CoachHome));
        Assert.AreEqual(Path.GetFullPath(Path.Combine(profile.Paths.Documents, "iRacing")), Path.GetFullPath(settings.IRacingRoot));
    }

    [TestMethod]
    public void IsolatedProfile_ConfinesEveryExposedRootAndNamespacesInstances()
    {
        using var first = IsolatedProfileRoot.Create();
        using var second = IsolatedProfileRoot.Create();
        var profile = first.Profile;
        var settings = new CompanionSettings(profile.Paths);

        Assert.IsTrue(profile.IsIsolated);
        Assert.IsFalse(profile.AllowMachineIntegration);
        Assert.IsTrue(profile.AllowEmbeddedBrowser);
        Assert.AreNotEqual(profile.ProcessIdentity, second.Profile.ProcessIdentity);
        foreach (var path in new[]
        {
            profile.Paths.UserProfile,
            profile.Paths.Documents,
            profile.Paths.Desktop,
            profile.Paths.LocalApplicationData,
            profile.Paths.ProgramFiles,
            profile.Paths.ProgramFilesX86,
            settings.CoachHome,
            settings.IRacingRoot,
            settings.IRacingInstallRoot,
            settings.LocalStateRoot,
            settings.ArchiveRoot,
            settings.LogsRoot
        })
        {
            Assert.IsTrue(IsDescendant(first.Root, path), path);
            Assert.IsTrue(Directory.Exists(path), path);
        }
        Assert.IsEmpty(profile.Paths.FixedDriveRoots);
    }

    [TestMethod]
    public void IsolatedNoWebView_IsExplicitUniqueAndUnavailableInProduction()
    {
        using var isolated = IsolatedProfileRoot.Create(disableWebView: true);

        Assert.IsTrue(isolated.Profile.IsIsolated);
        Assert.IsFalse(isolated.Profile.AllowEmbeddedBrowser);
        Assert.Throws<ArgumentException>(() => CompanionHostProfile.FromArguments(["--isolated-no-webview"]));
        Assert.Throws<ArgumentException>(() => CompanionHostProfile.FromArguments([
            "--isolated-profile", isolated.Root,
            "--isolated-no-webview", "--isolated-no-webview"]));
    }

    [TestMethod]
    public void IsolatedProfile_RejectsMissingDuplicateRelativeBroadAndNetworkRoots()
    {
        Assert.Throws<ArgumentException>(() => CompanionHostProfile.FromArguments(["--isolated-profile"]));
        Assert.Throws<ArgumentException>(() => CompanionHostProfile.FromArguments(["--isolated-profile", "relative", "--isolated-profile", "other"]));
        Assert.Throws<ArgumentException>(() => CompanionHostProfile.FromArguments(["--isolated-profile", "relative"]));
        Assert.Throws<ArgumentException>(() => CompanionHostProfile.FromArguments(["--isolated-profile", Path.GetTempPath()]));
        Assert.Throws<ArgumentException>(() => CompanionHostProfile.FromArguments(["--isolated-profile", @"\\server\share\iracing-coach-host-00000000000000000000000000000000"]));
        Assert.Throws<ArgumentException>(() => CompanionHostProfile.FromArguments(["--isolated-profile", Path.Combine(Path.GetTempPath(), "wrong-leaf")]));
    }

    [TestMethod]
    public void IsolatedProfile_RejectsExistingReparseRootBeforeComposition()
    {
        var target = Path.Combine(Path.GetTempPath(), "iracing-coach-host-target-" + Guid.NewGuid().ToString("N"));
        var link = Path.Combine(Path.GetTempPath(), "iracing-coach-host-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(target);
        try
        {
            try
            {
                Directory.CreateSymbolicLink(link, target);
            }
            catch (Exception ex) when (ex is UnauthorizedAccessException or PlatformNotSupportedException or IOException)
            {
                using var junction = Process.Start(new ProcessStartInfo("cmd.exe")
                {
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardError = true,
                    RedirectStandardOutput = true,
                    ArgumentList = { "/d", "/c", "mklink", "/J", link, target }
                });
                Assert.IsNotNull(junction, $"Neither symlink nor junction creation was available: {ex.GetType().Name}");
                junction.WaitForExit();
                Assert.AreEqual(0, junction.ExitCode, junction.StandardError.ReadToEnd());
            }
            Assert.Throws<ArgumentException>(() => CompanionHostProfile.FromArguments(["--isolated-profile", link]));
        }
        finally
        {
            if (Directory.Exists(link)) Directory.Delete(link);
            if (Directory.Exists(target)) Directory.Delete(target, recursive: true);
        }
    }

    [TestMethod]
    public void IsolatedSettingsStore_RepairsPersistedEscapesAndCannotSaveThemBack()
    {
        using var isolated = IsolatedProfileRoot.Create();
        var defaults = new CompanionSettings(isolated.Profile.Paths);
        Directory.CreateDirectory(defaults.CoachHome);
        File.WriteAllText(defaults.SettingsPath, JsonSerializer.Serialize(new
        {
            coachHome = Path.GetPathRoot(defaults.CoachHome),
            iRacingRoot = Path.GetPathRoot(defaults.IRacingRoot),
            iRacingInstallRoot = Path.GetPathRoot(defaults.IRacingInstallRoot),
            garage61ApiKey = "synthetic-legacy-never-import",
            firstRunComplete = true,
            settingsSchemaVersion = 5
        }));
        var credentials = new DisabledGarage61CredentialStore(Path.Combine(defaults.LocalStateRoot, "credentials", "disabled"));
        var store = new JsonSettingsStore(
            defaults.SettingsPath,
            credentials,
            Path.Combine(defaults.LocalStateRoot, "machine-settings.json"),
            isolated.Profile.Paths,
            lockToProviderRoots: true);

        var loaded = store.Load();
        AssertExactIsolatedRoots(defaults, loaded);
        Assert.AreEqual(string.Empty, loaded.Garage61ApiKey);
        Assert.IsFalse(credentials.IsConfigured);

        loaded.CoachHome = Path.GetPathRoot(defaults.CoachHome)!;
        loaded.IRacingRoot = Path.GetPathRoot(defaults.IRacingRoot)!;
        loaded.IRacingInstallRoot = Path.GetPathRoot(defaults.IRacingInstallRoot)!;
        loaded.LocalStateRootOverride = Path.GetPathRoot(defaults.LocalStateRoot)!;
        loaded.LaunchAtSignIn = true;
        store.Save(loaded);

        var saved = store.Load();
        AssertExactIsolatedRoots(defaults, saved);
        Assert.IsFalse(saved.LaunchAtSignIn);
    }

    [TestMethod]
    public void IsolatedProfile_DoesNotImportDesktopCredentialAndCredentialAdapterDeniesStorage()
    {
        using var isolated = IsolatedProfileRoot.Create();
        var sentinel = Path.Combine(isolated.Profile.Paths.Desktop, "garage61-key.txt");
        File.WriteAllText(sentinel, "synthetic-never-import");

        using var state = isolated.Profile.CreateState();
        Assert.IsTrue(File.Exists(sentinel));
        Assert.AreEqual(string.Empty, state.Settings.Garage61ApiKey);

        var credentials = new DisabledGarage61CredentialStore(Path.Combine(state.Settings.LocalStateRoot, "credentials", "disabled"));
        Assert.IsFalse(credentials.IsConfigured);
        Assert.Throws<InvalidOperationException>(() => credentials.Store("synthetic-token"));
        Assert.IsFalse(File.Exists(credentials.CredentialPath));
    }

    [TestMethod]
    public async Task LocalOnlyBackend_DeniesGarageUnknownAndEscapedRootsBeforeDispatch()
    {
        using var isolated = IsolatedProfileRoot.Create();
        var inner = new RecordingBackend();
        var backend = new LocalOnlyBackendClient(inner, isolated.Root);
        var settings = new CompanionSettings(isolated.Profile.Paths);
        var configuration = Configuration(settings);

        await backend.CallToolAsync(configuration, "iracing_companion_dashboard", new { });
        Assert.AreEqual(1, inner.ToolCalls);
        await Assert.ThrowsAsync<InvalidOperationException>(() => backend.CallToolAsync(configuration, "garage61_auth_status", new { }));
        await Assert.ThrowsAsync<InvalidOperationException>(() => backend.CallToolAsync(configuration, "unknown_tool", new { }));
        Assert.AreEqual(1, inner.ToolCalls);

        var escaped = configuration with { ArchiveRoot = Path.GetPathRoot(isolated.Root)! };
        await Assert.ThrowsAsync<InvalidOperationException>(() => backend.CheckHealthAsync(escaped));
        Assert.AreEqual(0, inner.HealthCalls);
    }

    [TestMethod]
    public async Task McpBackendChild_ReceivesOnlyConfinedHomeCredentialAndTempEnvironment()
    {
        using var isolated = IsolatedProfileRoot.Create();
        var settings = new CompanionSettings(isolated.Profile.Paths);
        var launcher = Path.Combine(settings.CoachHome, "synthetic-mcp.ps1");
        File.WriteAllText(launcher, """
            $marker = Join-Path $env:IRACING_COACH_HOME 'backend-child-environment.json'
            [ordered]@{
              localAppData = $env:LOCALAPPDATA
              userProfile = $env:USERPROFILE
              home = $env:HOME
              temp = $env:TEMP
              tmp = $env:TMP
              networkDisabled = $env:IRACING_COACH_NETWORK_DISABLED
            } | ConvertTo-Json -Compress | Set-Content -LiteralPath $marker -Encoding UTF8
            while (($line = [Console]::In.ReadLine()) -ne $null) {
              $request = $line | ConvertFrom-Json
              if ($null -eq $request.id) { continue }
              if ($request.method -eq 'initialize') {
                $result = @{ protocolVersion = '2025-06-18'; serverInfo = @{ name = 'synthetic'; version = '1' } }
              } elseif ($request.method -eq 'tools/list') {
                $result = @{ tools = @() }
              } else {
                $result = @{}
              }
              @{ jsonrpc = '2.0'; id = $request.id; result = $result } | ConvertTo-Json -Compress -Depth 8 | Write-Output
            }
            """);
        var configuration = Configuration(settings) with { LauncherPath = launcher };
        var backend = new LocalOnlyBackendClient(new McpBackendClient(), isolated.Root);

        var health = await backend.CheckHealthAsync(configuration);
        Assert.IsTrue(health.Ok, health.Error);

        var marker = Path.Combine(settings.CoachHome, "backend-child-environment.json");
        Assert.IsTrue(File.Exists(marker));
        using var document = JsonDocument.Parse(File.ReadAllText(marker));
        var values = document.RootElement;
        Assert.AreEqual(Path.GetFullPath(settings.LocalStateRoot), Path.GetFullPath(values.GetProperty("localAppData").GetString()!));
        Assert.AreEqual(Path.GetFullPath(configuration.UserProfileRoot), Path.GetFullPath(values.GetProperty("userProfile").GetString()!));
        Assert.AreEqual(Path.GetFullPath(configuration.UserProfileRoot), Path.GetFullPath(values.GetProperty("home").GetString()!));
        Assert.AreEqual(Path.GetFullPath(configuration.TemporaryRoot), Path.GetFullPath(values.GetProperty("temp").GetString()!));
        Assert.AreEqual(Path.GetFullPath(configuration.TemporaryRoot), Path.GetFullPath(values.GetProperty("tmp").GetString()!));
        Assert.AreEqual("1", values.GetProperty("networkDisabled").GetString());
    }

    [TestMethod]
    public async Task IsolatedState_LogsInsideProfileAndRefusesExternalHostActions()
    {
        using var isolated = IsolatedProfileRoot.Create();
        using var state = isolated.Profile.CreateState();

        state.ReportUnhandledException("host isolation", new InvalidOperationException(isolated.Profile.Paths.UserProfile + " synthetic failure"));
        var log = Path.Combine(state.Settings.LogsRoot, "app-errors.jsonl");
        Assert.IsTrue(File.Exists(log));
        StringAssert.Contains(File.ReadAllText(log), "%USERPROFILE%");
        Assert.IsFalse(File.ReadAllText(log).Contains(isolated.Profile.Paths.UserProfile, StringComparison.OrdinalIgnoreCase));

        state.RepairInstallation();
        StringAssert.Contains(state.Toast, "disabled");
        state.OpenLogs();
        StringAssert.Contains(state.Toast, "disabled");
        await state.ConnectChatGptAsync();
        StringAssert.Contains(state.Toast, "disabled");
    }

    [TestMethod]
    public void AppComposition_WiresConfinedWebViewAndSuppressesWindowsIntegration()
    {
        var root = TestRepositoryPaths.CompanionAppRoot;
        var xaml = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.App", "MainWindow.xaml"));
        var window = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.App", "MainWindow.xaml.cs"));
        var app = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.App", "App.xaml.cs"));

        StringAssert.Contains(xaml, "BlazorWebViewInitializing=\"OnBlazorWebViewInitializing\"");
        StringAssert.Contains(xaml, "UrlLoading=\"OnBlazorUrlLoading\"");
        StringAssert.Contains(window, "e.UserDataFolder = userData;");
        StringAssert.Contains(window, "--disable-background-networking");
        StringAssert.Contains(window, "--proxy-server=http://127.0.0.1:9");
        StringAssert.Contains(window, "UrlLoadingStrategy.CancelLoad");
        var initializeComponent = window.IndexOf("InitializeComponent();", StringComparison.Ordinal);
        var suppressBrowser = window.IndexOf("if (!hostProfile.AllowEmbeddedBrowser) Content = BuildIsolatedEvidenceSurface();", StringComparison.Ordinal);
        Assert.IsGreaterThan(-1, initializeComponent);
        Assert.IsGreaterThan(initializeComponent, suppressBrowser);
        StringAssert.Contains(window, "private static UIElement BuildIsolatedEvidenceSurface()");
        Assert.DoesNotContain("BlazorWebView", window[window.IndexOf("private static UIElement BuildIsolatedEvidenceSurface()", StringComparison.Ordinal)..]);
        StringAssert.Contains(window, "if (hostProfile.AllowMachineIntegration) _ = StartupRegistration.Apply");
        StringAssert.Contains(window, "if (hostProfile.AllowMachineIntegration)");
        StringAssert.Contains(app, "MutexName + instanceSuffix");
        StringAssert.Contains(app, "ActivationEventName + instanceSuffix");
    }

    private static void AssertExactIsolatedRoots(CompanionSettings expected, CompanionSettings actual)
    {
        Assert.AreEqual(Path.GetFullPath(expected.CoachHome), Path.GetFullPath(actual.CoachHome));
        Assert.AreEqual(Path.GetFullPath(expected.IRacingRoot), Path.GetFullPath(actual.IRacingRoot));
        Assert.AreEqual(Path.GetFullPath(expected.IRacingInstallRoot), Path.GetFullPath(actual.IRacingInstallRoot));
        Assert.AreEqual(Path.GetFullPath(expected.LocalStateRoot), Path.GetFullPath(actual.LocalStateRoot));
    }

    private static BackendConfiguration Configuration(CompanionSettings settings) => new(
        "powershell.exe",
        Path.Combine(AppContext.BaseDirectory, "start-mcp.ps1"),
        settings.PythonPath,
        settings.IRacingRoot,
        settings.ArchiveRoot,
        settings.CoachHome,
        settings.IRacingInstallRoot,
        LocalStateRoot: settings.LocalStateRoot,
        UserProfileRoot: Path.GetFullPath(Path.Combine(settings.LocalStateRoot, "..", "..", "..")),
        TemporaryRoot: Path.Combine(settings.LocalStateRoot, "temp"),
        NetworkAllowed: false);

    private static bool IsDescendant(string parent, string child)
    {
        var relative = Path.GetRelativePath(Path.GetFullPath(parent), Path.GetFullPath(child));
        return relative.Length > 0 && relative != "." && !Path.IsPathRooted(relative) &&
            relative != ".." && !relative.StartsWith(".." + Path.DirectorySeparatorChar, StringComparison.Ordinal);
    }

    private sealed class IsolatedProfileRoot : IDisposable
    {
        private IsolatedProfileRoot(string root, CompanionHostProfile profile)
        {
            Root = root;
            Profile = profile;
        }

        public string Root { get; }
        public CompanionHostProfile Profile { get; }

        public static IsolatedProfileRoot Create(bool disableWebView = false)
        {
            var root = Path.Combine(Path.GetTempPath(), "iracing-coach-host-" + Guid.NewGuid().ToString("N"));
            var arguments = disableWebView
                ? new[] { "--isolated-profile", root, "--isolated-no-webview" }
                : new[] { "--isolated-profile", root };
            var profile = CompanionHostProfile.FromArguments(arguments);
            return new IsolatedProfileRoot(root, profile);
        }

        public void Dispose()
        {
            if (Directory.Exists(Root)) Directory.Delete(Root, recursive: true);
        }
    }

    private sealed class RecordingBackend : IBackendClient
    {
        public int HealthCalls { get; private set; }
        public int ToolCalls { get; private set; }

        public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default)
        {
            HealthCalls++;
            return Task.FromResult(new BackendHealthResult(true, "test", "test", "test", 1, TimeSpan.Zero));
        }

        public Task<JsonElement> CallToolAsync(BackendConfiguration configuration, string toolName, object arguments, CancellationToken cancellationToken = default)
        {
            ToolCalls++;
            using var document = JsonDocument.Parse("{}");
            return Task.FromResult(document.RootElement.Clone());
        }
    }
}
