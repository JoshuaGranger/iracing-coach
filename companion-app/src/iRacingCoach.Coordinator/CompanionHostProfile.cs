using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

public sealed partial class CompanionHostProfile
{
    private const string IsolatedProfileArgument = "--isolated-profile";
    private const string IsolatedNoWebViewArgument = "--isolated-no-webview";

    private CompanionHostProfile(bool isIsolated, ICompanionPathProvider paths, string? root, bool allowEmbeddedBrowser)
    {
        IsIsolated = isIsolated;
        Paths = paths;
        Root = root;
        AllowEmbeddedBrowser = allowEmbeddedBrowser;
        ProcessIdentity = isIsolated && root is not null
            ? "isolated-" + Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(root))).ToLowerInvariant()[..16]
            : "production";
    }

    public bool IsIsolated { get; }
    public bool AllowMachineIntegration => !IsIsolated;
    public bool AllowEmbeddedBrowser { get; }
    public ICompanionPathProvider Paths { get; }
    public string? Root { get; }
    public string ProcessIdentity { get; }

    public static CompanionHostProfile FromArguments(IReadOnlyList<string> arguments)
    {
        ArgumentNullException.ThrowIfNull(arguments);
        var matches = arguments
            .Select((value, index) => (value, index))
            .Where(item => string.Equals(item.value, IsolatedProfileArgument, StringComparison.OrdinalIgnoreCase))
            .ToArray();
        var noWebViewMatches = arguments.Count(value =>
            string.Equals(value, IsolatedNoWebViewArgument, StringComparison.OrdinalIgnoreCase));
        if (matches.Length == 0)
        {
            if (noWebViewMatches != 0)
                throw new ArgumentException("The no-WebView option is available only with an isolated profile.");
            return new CompanionHostProfile(false, WindowsCompanionPathProvider.Instance, null, allowEmbeddedBrowser: true);
        }
        if (matches.Length != 1)
            throw new ArgumentException("The isolated profile option may be supplied only once.");
        if (noWebViewMatches > 1)
            throw new ArgumentException("The no-WebView option may be supplied only once.");

        var valueIndex = matches[0].index + 1;
        if (valueIndex >= arguments.Count || string.IsNullOrWhiteSpace(arguments[valueIndex]) || arguments[valueIndex].StartsWith("--", StringComparison.Ordinal))
            throw new ArgumentException("The isolated profile option requires one absolute local path.");

        var root = ValidateAndCreateRoot(arguments[valueIndex]);
        var paths = new IsolatedCompanionPathProvider(root);
        CreateConfinedTree(paths);
        return new CompanionHostProfile(true, paths, root, allowEmbeddedBrowser: noWebViewMatches == 0);
    }

    public CompanionState CreateState()
    {
        if (!IsIsolated) return new CompanionState();

        var settings = new CompanionSettings(Paths);
        var credentials = new DisabledGarage61CredentialStore(Path.Combine(settings.LocalStateRoot, "credentials", "disabled"));
        var store = new JsonSettingsStore(
            settings.SettingsPath,
            credentials,
            Path.Combine(settings.LocalStateRoot, "machine-settings.json"),
            Paths,
            lockToProviderRoots: true);
        var backend = new LocalOnlyBackendClient(new McpBackendClient(), Root!);
        return new CompanionState(
            backend,
            store,
            new DisconnectedLiveTelemetrySource(),
            new DisabledCoachEngineSupervisor(),
            credentials,
            new DurableArchiveService(),
            Paths,
            allowExternalHostActions: false);
    }

    private static string ValidateAndCreateRoot(string candidate)
    {
        if (!Path.IsPathFullyQualified(candidate))
            throw new ArgumentException("The isolated profile root must be absolute.");
        if (candidate.StartsWith(@"\\", StringComparison.Ordinal) ||
            candidate.StartsWith(@"\\?\", StringComparison.Ordinal) ||
            candidate.StartsWith(@"\\.\", StringComparison.Ordinal))
            throw new ArgumentException("The isolated profile root must be a local filesystem path.");

        var temporaryParent = Path.GetFullPath(Path.GetTempPath());
        var root = Path.GetFullPath(candidate);
        if (!IsStrictDescendant(temporaryParent, root))
            throw new ArgumentException("The isolated profile root must be below the local temporary directory.");
        if (!IsolatedLeafPattern().IsMatch(Path.GetFileName(root)))
            throw new ArgumentException("The isolated profile root must use the generated iracing-coach-host identifier.");

        var cursor = new DirectoryInfo(root);
        while (cursor is not null && IsStrictDescendant(temporaryParent, cursor.FullName))
        {
            if (cursor.Exists && cursor.Attributes.HasFlag(FileAttributes.ReparsePoint))
                throw new ArgumentException("The isolated profile root cannot contain a reparse point.");
            cursor = cursor.Parent;
        }

        Directory.CreateDirectory(root);
        if (File.GetAttributes(root).HasFlag(FileAttributes.ReparsePoint))
            throw new ArgumentException("The isolated profile root cannot be a reparse point.");
        return root;
    }

    private static void CreateConfinedTree(IsolatedCompanionPathProvider paths)
    {
        var settings = new CompanionSettings(paths);
        foreach (var path in new[]
        {
            paths.UserProfile,
            paths.Documents,
            paths.Desktop,
            paths.LocalApplicationData,
            paths.ProgramFiles,
            paths.ProgramFilesX86,
            settings.CoachHome,
            settings.IRacingRoot,
            settings.IRacingInstallRoot,
            settings.LocalStateRoot,
            settings.ArchiveRoot,
            settings.LogsRoot
        })
        {
            if (!IsStrictDescendant(paths.Root, path))
                throw new InvalidOperationException("An isolated host path escaped its profile root.");
            Directory.CreateDirectory(path);
        }
    }

    internal static bool IsStrictDescendant(string parent, string candidate)
    {
        var parentPath = Path.TrimEndingDirectorySeparator(Path.GetFullPath(parent));
        var candidatePath = Path.TrimEndingDirectorySeparator(Path.GetFullPath(candidate));
        var relative = Path.GetRelativePath(parentPath, candidatePath);
        return relative.Length > 0 && relative != "." && !Path.IsPathRooted(relative) &&
            !relative.Equals("..", StringComparison.Ordinal) &&
            !relative.StartsWith(".." + Path.DirectorySeparatorChar, StringComparison.Ordinal);
    }

    [GeneratedRegex("^iracing-coach-host-[0-9a-f]{32}$", RegexOptions.CultureInvariant)]
    private static partial Regex IsolatedLeafPattern();
}

public sealed class DisabledGarage61CredentialStore(string credentialPath) : IGarage61CredentialStore
{
    public bool IsConfigured => false;
    public string CredentialPath { get; } = Path.GetFullPath(credentialPath);

    public void Store(string token) =>
        throw new InvalidOperationException("Garage61 credentials are disabled in the isolated host profile.");

    public void Remove() { }
}

public sealed class LocalOnlyBackendClient : IBackendClient
{
    private static readonly HashSet<string> AllowedTools = new(StringComparer.Ordinal)
    {
        "analyze_iracing_race",
        "build_open_setup_package",
        "catalog_iracing_setups",
        "copy_iracing_setup_to_coach",
        "discover_iracing_sessions",
        "iracing_companion_dashboard",
        "iracing_strategy_history",
        "recommend_open_setup_tuning",
        "recommend_structured_open_setup_tuning",
        "record_open_setup_feedback"
    };

    private readonly IBackendClient _inner;
    private readonly string _profileRoot;

    public LocalOnlyBackendClient(IBackendClient inner, string profileRoot)
    {
        _inner = inner ?? throw new ArgumentNullException(nameof(inner));
        _profileRoot = Path.GetFullPath(profileRoot);
    }

    public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default)
    {
        Validate(configuration);
        return _inner.CheckHealthAsync(configuration, cancellationToken);
    }

    public Task<JsonElement> CallToolAsync(
        BackendConfiguration configuration,
        string toolName,
        object arguments,
        CancellationToken cancellationToken = default)
    {
        Validate(configuration);
        if (!AllowedTools.Contains(toolName))
            throw new InvalidOperationException($"Backend tool '{toolName}' is disabled in the isolated host profile.");
        return _inner.CallToolAsync(configuration, toolName, arguments, cancellationToken);
    }

    private void Validate(BackendConfiguration configuration)
    {
        ArgumentNullException.ThrowIfNull(configuration);
        if (configuration.NetworkAllowed)
            throw new InvalidOperationException("Network-enabled backend configuration is not permitted in the isolated host profile.");
        foreach (var path in new[]
        {
            configuration.IRacingRoot,
            configuration.ArchiveRoot,
            configuration.CoachHomeRoot,
            configuration.IRacingInstallRoot,
            configuration.LocalStateRoot,
            configuration.UserProfileRoot,
            configuration.TemporaryRoot
        })
        {
            if (!CompanionHostProfile.IsStrictDescendant(_profileRoot, path))
                throw new InvalidOperationException("The isolated backend configuration contains a path outside its profile root.");
        }
    }
}
