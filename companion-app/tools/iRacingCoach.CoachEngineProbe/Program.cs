using System.Text.Json;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

if (args.Length == 1 && string.Equals(args[0], "--prepare-local-archive", StringComparison.Ordinal))
{
    var root = Path.GetFullPath(Path.Combine(WindowsKnownFolders.Documents, "iRacing Coach"));
    var service = new DurableArchiveService();
    _ = service.Initialize(root, "0.8.0", "0.3.0");
    var prepared = service.PrepareForCopy(root, "0.8.0", "0.3.0");
    Console.WriteLine(JsonSerializer.Serialize(new
    {
        ok = prepared.SafeToCopy,
        prepared.SafeToCopy,
        prepared.Root,
        prepared.FileCount,
        prepared.Bytes,
        prepared.IntegritySha256,
        prepared.UnresolvedSources
    }));
    return prepared.SafeToCopy ? 0 : 1;
}

if (args.Length == 1 && string.Equals(args[0], "--migrate-settings", StringComparison.Ordinal))
{
    var migrated = new JsonSettingsStore().Load();
    Console.WriteLine(JsonSerializer.Serialize(new
    {
        ok = string.IsNullOrEmpty(migrated.Garage61ApiKey),
        portableCredentialRemoved = string.IsNullOrEmpty(migrated.Garage61ApiKey),
        migrated.SettingsSchemaVersion
    }));
    return string.IsNullOrEmpty(migrated.Garage61ApiKey) ? 0 : 1;
}

if (args.Length == 2 && string.Equals(args[0], "--cleanup-probe", StringComparison.Ordinal))
{
    var cleanupTarget = Path.GetFullPath(args[1]);
    var cleanupRoot = Path.GetFullPath(Path.GetTempPath()).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
    if (!cleanupTarget.StartsWith(cleanupRoot, StringComparison.OrdinalIgnoreCase) ||
        !Path.GetFileName(cleanupTarget).StartsWith("iRacingCoach-CoachEngine-Probe-", StringComparison.Ordinal))
        throw new InvalidOperationException("Refusing to clean an unguarded Coach Engine probe directory.");
    if (Directory.Exists(cleanupTarget)) Directory.Delete(cleanupTarget, recursive: true);
    Console.WriteLine(JsonSerializer.Serialize(new { ok = !Directory.Exists(cleanupTarget) }));
    return Directory.Exists(cleanupTarget) ? 1 : 0;
}

if (args.Length == 2 && string.Equals(args[0], "--package", StringComparison.Ordinal))
{
    var payload = Path.GetFullPath(args[1]);
    var temporaryRoot = Path.Combine(Path.GetTempPath(), "iRacingCoach-CoachEngine-Probe-" + Guid.NewGuid().ToString("N"));
    Directory.CreateDirectory(temporaryRoot);
    try
    {
        Environment.SetEnvironmentVariable("IRACING_COACH_PYTHON", Path.Combine(payload, "python", "python.exe"));
        var packagedSettings = new CompanionSettings { CoachHome = Path.Combine(temporaryRoot, "coach-home") };
        Directory.CreateDirectory(packagedSettings.CoachHome);
        await using var packagedSupervisor = new CodexAppServerSupervisor(new CoachEngineProvisioner(
            Path.Combine(payload, "coach-engine"),
            Path.Combine(temporaryRoot, "state"),
            payload));
        await packagedSupervisor.StartAsync(packagedSettings);
        await packagedSupervisor.RefreshAccountAsync();
        var packagedResult = packagedSupervisor.Current;
        await packagedSupervisor.StopAsync();
        Console.WriteLine(JsonSerializer.Serialize(new
        {
            ok = packagedResult.Installed && packagedResult.Running,
            packagedResult.Installed,
            packagedResult.Running,
            packagedResult.ChatGptConnected,
            packagedResult.Status,
            packagedResult.RuntimeVersion
        }));
        return packagedResult.Installed && packagedResult.Running ? 0 : 1;
    }
    finally
    {
        var resolved = Path.GetFullPath(temporaryRoot);
        var expectedRoot = Path.GetFullPath(Path.GetTempPath()).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        if (!resolved.StartsWith(expectedRoot, StringComparison.OrdinalIgnoreCase) || !Path.GetFileName(resolved).StartsWith("iRacingCoach-CoachEngine-Probe-", StringComparison.Ordinal))
            throw new InvalidOperationException("Refusing to clean an unguarded Coach Engine probe directory.");
        for (var attempt = 0; attempt < 5 && Directory.Exists(resolved); attempt++)
        {
            try { Directory.Delete(resolved, recursive: true); }
            catch (IOException) when (attempt < 4) { await Task.Delay(250); }
        }
    }
}

if (args.Length != 2)
{
    Console.Error.WriteLine("Usage: CoachEngineProbe <codex.exe> <python.exe> | --package <payload> | --migrate-settings");
    return 2;
}

Environment.SetEnvironmentVariable("IRACING_COACH_CODEX", Path.GetFullPath(args[0]));
Environment.SetEnvironmentVariable("IRACING_COACH_PYTHON", Path.GetFullPath(args[1]));
var settings = new CompanionSettings();
Directory.CreateDirectory(settings.CoachHome);
await using var supervisor = new CodexAppServerSupervisor();
await supervisor.StartAsync(settings);
await supervisor.RefreshAccountAsync();
var result = supervisor.Current;
await supervisor.StopAsync();
Console.WriteLine(JsonSerializer.Serialize(new
{
    ok = result.Installed && result.Running,
    result.Installed,
    result.Running,
    result.ChatGptConnected,
    result.Status,
    result.Message,
    result.RuntimeVersion
}));
return result.Installed && result.Running ? 0 : 1;
