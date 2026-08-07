using System.Collections.Concurrent;
using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

public sealed record CoachEngineLogin(
    string Type,
    string? LoginId,
    string? Url,
    string? VerificationCode);

public sealed record CoachEngineReply(string ThreadId, string TurnId, string Text);

public interface ICoachEngineSupervisor : IAsyncDisposable
{
    CoachEngineConnection Current { get; }
    event Action<CoachEngineConnection>? Changed;
    event Action<string>? CoachMessageDelta;
    Task StartAsync(CompanionSettings settings, CancellationToken cancellationToken = default);
    Task RefreshAccountAsync(CancellationToken cancellationToken = default);
    Task<CoachEngineLogin> BeginChatGptLoginAsync(bool deviceCode = false, CancellationToken cancellationToken = default);
    Task CancelLoginAsync(string loginId, CancellationToken cancellationToken = default);
    Task<CoachEngineReply> AskCoachAsync(string? threadId, string question, string evidenceJson, CancellationToken cancellationToken = default);
    Task StopAsync(CancellationToken cancellationToken = default);
}

public sealed class DisabledCoachEngineSupervisor : ICoachEngineSupervisor
{
    public CoachEngineConnection Current { get; private set; } = new(false, false, false, "unavailable", "Coach Engine is disabled in this test host.");
    public event Action<CoachEngineConnection>? Changed { add { } remove { } }
    public event Action<string>? CoachMessageDelta { add { } remove { } }
    public Task StartAsync(CompanionSettings settings, CancellationToken cancellationToken = default) => Task.CompletedTask;
    public Task RefreshAccountAsync(CancellationToken cancellationToken = default) => Task.CompletedTask;
    public Task<CoachEngineLogin> BeginChatGptLoginAsync(bool deviceCode = false, CancellationToken cancellationToken = default) =>
        Task.FromException<CoachEngineLogin>(new InvalidOperationException(Current.Message));
    public Task CancelLoginAsync(string loginId, CancellationToken cancellationToken = default) => Task.CompletedTask;
    public Task<CoachEngineReply> AskCoachAsync(string? threadId, string question, string evidenceJson, CancellationToken cancellationToken = default) =>
        Task.FromException<CoachEngineReply>(new InvalidOperationException(Current.Message));
    public Task StopAsync(CancellationToken cancellationToken = default) => Task.CompletedTask;
    public ValueTask DisposeAsync() => ValueTask.CompletedTask;
}

public sealed record CoachEngineInstallation(
    bool Ready,
    string Message,
    string RuntimeVersion,
    string CodexExecutable,
    string CodexHome,
    string SchemaDirectory,
    string ConfigPath);

public sealed class CoachEngineProvisioner
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web) { WriteIndented = true };
    private readonly string? _componentRoot;
    private readonly string? _localRoot;
    private readonly string? _applicationRoot;

    public CoachEngineProvisioner(string? componentRoot = null, string? localRoot = null, string? applicationRoot = null)
    {
        _componentRoot = componentRoot;
        _localRoot = localRoot;
        _applicationRoot = applicationRoot;
    }

    public CoachEngineInstallation Prepare(CompanionSettings settings)
    {
        var appRoot = AppContext.BaseDirectory;
        var componentRoot = _componentRoot ?? Path.Combine(appRoot, "coach-engine");
        var executable = Path.Combine(componentRoot, "codex", "codex.exe");
        var schemaDirectory = Path.Combine(componentRoot, "schemas");
        var manifestPath = Path.Combine(componentRoot, "coach-engine-manifest.json");
        var developerRuntime = Environment.GetEnvironmentVariable("IRACING_COACH_CODEX");
        var packaged = File.Exists(executable) && File.Exists(manifestPath);

        if (!packaged && !string.IsNullOrWhiteSpace(developerRuntime))
        {
            executable = Path.GetFullPath(developerRuntime);
            schemaDirectory = FindDevelopmentSchemas() ?? schemaDirectory;
        }

        if (!File.Exists(executable))
            return Missing("The private Coach Engine runtime is missing. Use Repair installation.", executable, schemaDirectory);
        if (!File.Exists(Path.Combine(schemaDirectory, "codex_app_server_protocol.schemas.json")))
            return Missing("The Coach Engine protocol files are missing. Use Repair installation.", executable, schemaDirectory);

        var runtimeVersion = "development";
        if (packaged)
        {
            try
            {
                using var manifest = JsonDocument.Parse(File.ReadAllText(manifestPath));
                runtimeVersion = manifest.RootElement.GetProperty("runtimeVersion").GetString() ?? "unknown";
                var expectedHash = manifest.RootElement.GetProperty("runtimeSha256").GetString() ?? string.Empty;
                using var runtimeStream = File.OpenRead(executable);
                var actualHash = Convert.ToHexString(SHA256.HashData(runtimeStream)).ToLowerInvariant();
                if (!CryptographicOperations.FixedTimeEquals(
                    Encoding.ASCII.GetBytes(expectedHash.ToLowerInvariant()),
                    Encoding.ASCII.GetBytes(actualHash)))
                {
                    return Missing("The Coach Engine runtime did not pass its integrity check. Use Repair installation.", executable, schemaDirectory);
                }
            }
            catch (Exception ex) when (ex is IOException or JsonException or KeyNotFoundException)
            {
                return Missing("The Coach Engine manifest is invalid. Use Repair installation.", executable, schemaDirectory);
            }
        }

        var localRoot = _localRoot ?? Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "iRacingCoach",
            "CoachEngine");
        var codexHome = Path.Combine(localRoot, "codex-home");
        Directory.CreateDirectory(codexHome);
        Directory.CreateDirectory(Path.Combine(localRoot, "logs"));
        var configPath = Path.Combine(codexHome, "config.toml");
        var backendRelative = Path.Combine("iracing-coach", "skills", "analyze-iracing-race", "scripts", "start-mcp.ps1");
        var backendLauncher = _applicationRoot is null ? ResolveWorkspaceFile(backendRelative) : Path.Combine(_applicationRoot, backendRelative);
        if (backendLauncher is null || !File.Exists(settings.PythonPath))
            return Missing("The deterministic Coach Engine components are incomplete. Use Repair installation.", executable, schemaDirectory);

        var config = BuildConfig(settings, backendLauncher);
        WriteAtomic(configPath, config);
        WriteAtomic(Path.Combine(localRoot, "migration.json"), JsonSerializer.Serialize(new
        {
            schemaVersion = 1,
            runtimeVersion,
            configuredAt = DateTimeOffset.UtcNow
        }, JsonOptions));

        return new CoachEngineInstallation(true, "Coach Engine is ready.", runtimeVersion, executable, codexHome, schemaDirectory, configPath);
    }

    private static string BuildConfig(CompanionSettings settings, string backendLauncher)
    {
        const string instructions = "You are the private iRacing Coach Engine. Use the bounded iRacing Coach MCP tools as the source of truth. Never invent telemetry, tire wear, damage, pit loss, setup values, or targets. Use only the active capabilities supplied with the request and omit sections that do not apply or lack supported evidence. Preserve measured, derived, inferred, and proxy evidence labels. Never refer to hidden controls or features. Never recommend setup changes unless setupChangesAllowed is true, and reject damage-confounded tests. Keep responses concise, human, and driver-focused.";
        var values = new Dictionary<string, string>
        {
            ["IRACING_COACH_PYTHON"] = settings.PythonPath,
            ["IRACING_COACH_IRACING_ROOT"] = settings.IRacingRoot,
            ["IRACING_COACH_INSTALL_ROOT"] = settings.IRacingInstallRoot,
            ["IRACING_COACH_DATA"] = settings.ArchiveRoot,
            ["IRACING_COACH_HOME"] = settings.CoachHome,
            ["PYTHONUTF8"] = "1"
        };
        var environment = string.Join(", ", values.Select(pair => $"{Toml(pair.Key)} = {Toml(pair.Value)}"));
        return $$"""
            approval_policy = "never"
            sandbox_mode = "read-only"
            check_for_update_on_startup = false
            cli_auth_credentials_store = "auto"
            web_search = "disabled"
            developer_instructions = {{Toml(instructions)}}

            [analytics]
            enabled = false

            [agents]
            enabled = false

            [features]
            apps = false

            [mcp_servers.iracing_coach]
            enabled = true
            required = true
            command = "powershell.exe"
            args = ["-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", {{Toml(backendLauncher)}}]
            env = { {{environment}} }
            startup_timeout_sec = 20
            tool_timeout_sec = 180
            """;
    }

    private static string Toml(string value) => JsonSerializer.Serialize(value);

    private static void WriteAtomic(string path, string value)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var temporary = path + ".tmp";
        File.WriteAllText(temporary, value, new UTF8Encoding(false));
        File.Move(temporary, path, overwrite: true);
    }

    private static CoachEngineInstallation Missing(string message, string executable, string schemas) =>
        new(false, message, string.Empty, executable, string.Empty, schemas, string.Empty);

    private static string? ResolveWorkspaceFile(string relative)
    {
        var packaged = Path.Combine(AppContext.BaseDirectory, relative);
        if (File.Exists(packaged)) return packaged;
        var current = new DirectoryInfo(Directory.GetCurrentDirectory());
        while (current is not null)
        {
            var candidate = Path.Combine(current.FullName, relative);
            if (File.Exists(candidate)) return candidate;
            current = current.Parent;
        }
        return null;
    }

    private static string? FindDevelopmentSchemas()
    {
        var current = new DirectoryInfo(Directory.GetCurrentDirectory());
        while (current is not null)
        {
            var generated = Path.Combine(current.FullName, "companion-app", "generated");
            if (Directory.Exists(generated))
            {
                return Directory.EnumerateDirectories(generated, "codex-app-server-*", SearchOption.TopDirectoryOnly)
                    .OrderByDescending(path => path, StringComparer.OrdinalIgnoreCase)
                    .FirstOrDefault();
            }
            current = current.Parent;
        }
        return null;
    }
}

public sealed class CodexAppServerSupervisor : ICoachEngineSupervisor
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);
    private readonly CoachEngineProvisioner _provisioner;
    private readonly SemaphoreSlim _lifecycle = new(1, 1);
    private readonly SemaphoreSlim _writer = new(1, 1);
    private readonly ConcurrentDictionary<long, TaskCompletionSource<JsonElement>> _pending = new();
    private readonly ConcurrentDictionary<string, TurnCollector> _turns = new(StringComparer.Ordinal);
    private readonly ConcurrentDictionary<string, ConcurrentQueue<string>> _earlyTurnDeltas = new(StringComparer.Ordinal);
    private readonly ConcurrentDictionary<string, string> _earlyTurnCompletions = new(StringComparer.Ordinal);
    private Process? _process;
    private Task? _reader;
    private Task? _stderr;
    private long _nextId;
    private bool _stopping;
    private int _restartAttempts;
    private CompanionSettings? _settings;
    private CoachEngineInstallation? _installation;

    public CodexAppServerSupervisor(CoachEngineProvisioner? provisioner = null)
    {
        _provisioner = provisioner ?? new CoachEngineProvisioner();
    }

    public CoachEngineConnection Current { get; private set; } = new(false, false, false, "checking", "Checking Coach Engine…");
    public event Action<CoachEngineConnection>? Changed;
    public event Action<string>? CoachMessageDelta;

    public async Task StartAsync(CompanionSettings settings, CancellationToken cancellationToken = default)
    {
        _settings = settings;
        await _lifecycle.WaitAsync(cancellationToken);
        try
        {
            if (_process is { HasExited: false }) return;
            var installation = _provisioner.Prepare(settings);
            _installation = installation;
            if (!installation.Ready)
            {
                SetCurrent(new(false, false, false, "repair", installation.Message));
                return;
            }

            _stopping = false;
            var start = new ProcessStartInfo
            {
                FileName = installation.CodexExecutable,
                UseShellExecute = false,
                RedirectStandardInput = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
                WorkingDirectory = settings.CoachHome
            };
            start.ArgumentList.Add("app-server");
            start.ArgumentList.Add("--listen");
            start.ArgumentList.Add("stdio://");
            start.ArgumentList.Add("--strict-config");
            start.Environment["CODEX_HOME"] = installation.CodexHome;
            start.Environment["CODEX_SQLITE_HOME"] = installation.CodexHome;
            start.Environment["RUST_LOG"] = "warn";

            _process = Process.Start(start) ?? throw new IOException("Coach Engine could not start.");
            _process.EnableRaisingEvents = true;
            _process.Exited += OnProcessExited;
            _reader = ReadLoopAsync(_process);
            _stderr = DrainErrorAsync(_process);

            await RequestAsync("initialize", new
            {
                clientInfo = new { name = "iracing_coach", title = "iRacing Coach", version = "0.14.0" }
            }, cancellationToken);
            await NotifyAsync("initialized", new { }, cancellationToken);
            SetCurrent(new(true, true, false, "running", "Coach Engine is running.", installation.RuntimeVersion));
            await RefreshAccountCoreAsync(cancellationToken);
            _restartAttempts = 0;
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception ex) when (ex is IOException or InvalidOperationException or JsonException or TimeoutException)
        {
            SetCurrent(new(true, false, false, "unavailable", $"Coach Engine could not start: {Safe(ex.Message)}"));
            await StopProcessCoreAsync();
        }
        finally
        {
            _lifecycle.Release();
        }
    }

    public async Task RefreshAccountAsync(CancellationToken cancellationToken = default)
    {
        if (_process is not { HasExited: false } && _settings is not null)
            await StartAsync(_settings, cancellationToken);
        if (_process is { HasExited: false }) await RefreshAccountCoreAsync(cancellationToken);
    }

    public async Task<CoachEngineLogin> BeginChatGptLoginAsync(bool deviceCode = false, CancellationToken cancellationToken = default)
    {
        if (_process is not { HasExited: false })
            throw new InvalidOperationException("Coach Engine is not running. Use Repair installation first.");
        var result = await RequestAsync("account/login/start", deviceCode
            ? new { type = "chatgptDeviceCode" }
            : new { type = "chatgpt", useHostedLoginSuccessPage = true, appBrand = "chatgpt" }, cancellationToken);
        var type = result.GetProperty("type").GetString() ?? "chatgpt";
        var loginId = result.TryGetProperty("loginId", out var id) ? id.GetString() : null;
        var url = result.TryGetProperty("authUrl", out var authUrl) ? authUrl.GetString() :
            result.TryGetProperty("verificationUrl", out var verificationUrl) ? verificationUrl.GetString() : null;
        var code = result.TryGetProperty("userCode", out var userCode) ? userCode.GetString() : null;
        SetCurrent(Current with { Status = "connecting", Message = "Finish signing in to ChatGPT in your browser.", LoginUrl = url, VerificationCode = code });
        return new CoachEngineLogin(type, loginId, url, code);
    }

    public async Task CancelLoginAsync(string loginId, CancellationToken cancellationToken = default)
    {
        if (string.IsNullOrWhiteSpace(loginId) || _process is not { HasExited: false }) return;
        await RequestAsync("account/login/cancel", new { loginId }, cancellationToken);
        await RefreshAccountCoreAsync(cancellationToken);
    }

    public async Task<CoachEngineReply> AskCoachAsync(string? threadId, string question, string evidenceJson, CancellationToken cancellationToken = default)
    {
        if (_process is not { HasExited: false } || !Current.ChatGptConnected || _settings is null || _installation is null)
            throw new InvalidOperationException("Connect ChatGPT before requesting AI coaching.");
        if (string.IsNullOrWhiteSpace(question)) throw new ArgumentException("Enter a coaching question.", nameof(question));

        var activeThreadId = await ResolveThreadAsync(threadId, cancellationToken);
        var outputSchemaPath = Path.Combine(_installation.SchemaDirectory, "ai-coaching-output.schema.json");
        JsonElement? outputSchema = null;
        if (File.Exists(outputSchemaPath))
        {
            using var schemaDocument = JsonDocument.Parse(File.ReadAllText(outputSchemaPath));
            outputSchema = schemaDocument.RootElement.Clone();
        }
        var prompt = $"""
            Driver question:
            {question.Trim()}

            Deterministic race evidence (authoritative JSON):
            {evidenceJson}

            Answer only from this evidence and the bounded iRacing Coach tools. Use the supplied active capabilities, omit unsupported or missing sections, and never invent a replacement value.
            """;
        var turnResult = await RequestAsync("turn/start", new
        {
            threadId = activeThreadId,
            input = new[] { new { type = "text", text = prompt } },
            outputSchema,
            approvalPolicy = "never",
            cwd = _settings.CoachHome,
            runtimeWorkspaceRoots = RuntimeRoots(_settings),
            environments = Array.Empty<object>()
        }, cancellationToken);
        var turnId = turnResult.GetProperty("turn").GetProperty("id").GetString()
            ?? throw new InvalidOperationException("Coach Engine returned a turn without an identifier.");
        var collector = new TurnCollector(activeThreadId, turnId);
        if (!_turns.TryAdd(turnId, collector)) throw new InvalidOperationException("Coach Engine turn tracking failed.");
        if (_earlyTurnDeltas.TryRemove(turnId, out var earlyDeltas))
            while (earlyDeltas.TryDequeue(out var earlyDelta)) collector.Append(earlyDelta);
        if (_earlyTurnCompletions.TryRemove(turnId, out var earlyStatus)) CompleteTurn(collector, earlyStatus);
        using var registration = cancellationToken.Register(() => _ = InterruptTurnAsync(activeThreadId, turnId));
        try
        {
            var text = await collector.Completion.Task.WaitAsync(TimeSpan.FromMinutes(5), cancellationToken);
            return new CoachEngineReply(activeThreadId, turnId, text);
        }
        finally
        {
            _turns.TryRemove(turnId, out _);
        }
    }

    public async Task StopAsync(CancellationToken cancellationToken = default)
    {
        await _lifecycle.WaitAsync(cancellationToken);
        try
        {
            _stopping = true;
            await StopProcessCoreAsync();
            SetCurrent(Current with { Running = false, Status = Current.Installed ? "stopped" : Current.Status, Message = Current.Installed ? "Coach Engine is stopped." : Current.Message });
        }
        finally
        {
            _lifecycle.Release();
        }
    }

    private async Task RefreshAccountCoreAsync(CancellationToken cancellationToken)
    {
        var result = await RequestAsync("account/read", new { refreshToken = false }, cancellationToken);
        var connected = false;
        string? label = null;
        if (result.TryGetProperty("account", out var account) && account.ValueKind == JsonValueKind.Object)
        {
            var type = account.TryGetProperty("type", out var accountType) ? accountType.GetString() : null;
            connected = string.Equals(type, "chatgpt", StringComparison.OrdinalIgnoreCase);
            label = connected && account.TryGetProperty("email", out var email) ? email.GetString() : null;
        }
        SetCurrent(Current with
        {
            Installed = true,
            Running = true,
            ChatGptConnected = connected,
            Status = connected ? "connected" : "not_connected",
            Message = connected ? "ChatGPT is connected." : "Connect ChatGPT to enable AI coaching.",
            AccountLabel = label,
            LoginUrl = null,
            VerificationCode = null
        });
    }

    private async Task<JsonElement> RequestAsync(string method, object parameters, CancellationToken cancellationToken)
    {
        var process = _process;
        if (process is null || process.HasExited) throw new InvalidOperationException("Coach Engine is not running.");
        var id = Interlocked.Increment(ref _nextId);
        var completion = new TaskCompletionSource<JsonElement>(TaskCreationOptions.RunContinuationsAsynchronously);
        if (!_pending.TryAdd(id, completion)) throw new InvalidOperationException("Coach Engine request tracking failed.");
        try
        {
            await WriteAsync(new { method, id, @params = parameters }, cancellationToken);
            return await completion.Task.WaitAsync(TimeSpan.FromSeconds(30), cancellationToken);
        }
        finally
        {
            _pending.TryRemove(id, out _);
        }
    }

    private Task NotifyAsync(string method, object parameters, CancellationToken cancellationToken) =>
        WriteAsync(new { method, @params = parameters }, cancellationToken);

    private async Task WriteAsync(object message, CancellationToken cancellationToken)
    {
        var process = _process;
        if (process is null || process.HasExited) throw new InvalidOperationException("Coach Engine is not running.");
        var json = JsonSerializer.Serialize(message, JsonOptions);
        await _writer.WaitAsync(cancellationToken);
        try
        {
            await process.StandardInput.WriteLineAsync(json.AsMemory(), cancellationToken);
            await process.StandardInput.FlushAsync(cancellationToken);
        }
        finally
        {
            _writer.Release();
        }
    }

    private async Task ReadLoopAsync(Process process)
    {
        try
        {
            while (!process.HasExited)
            {
                var line = await process.StandardOutput.ReadLineAsync();
                if (line is null) break;
                using var document = JsonDocument.Parse(line);
                var root = document.RootElement;
                if (root.TryGetProperty("id", out var responseId) && responseId.TryGetInt64(out var id) && _pending.TryGetValue(id, out var completion))
                {
                    if (root.TryGetProperty("error", out var error))
                        completion.TrySetException(new InvalidOperationException(Safe(error.GetRawText())));
                    else if (root.TryGetProperty("result", out var result))
                        completion.TrySetResult(result.Clone());
                    continue;
                }
                if (root.TryGetProperty("method", out var methodElement))
                {
                    var method = methodElement.GetString();
                    if (method is "account/login/completed" or "account/updated")
                        _ = RefreshAccountAfterNotificationAsync();
                    if (root.TryGetProperty("params", out var notificationParameters))
                        HandleTurnNotification(method, notificationParameters);
                }
            }
        }
        catch (Exception ex) when (ex is IOException or JsonException or InvalidOperationException)
        {
            FailPending(ex);
        }
    }

    private static async Task DrainErrorAsync(Process process)
    {
        try
        {
            while (await process.StandardError.ReadLineAsync() is not null) { }
        }
        catch (IOException) { }
    }

    private async Task RefreshAccountAfterNotificationAsync()
    {
        try { await Task.Delay(100); await RefreshAccountAsync(); }
        catch (Exception ex) when (ex is IOException or InvalidOperationException or JsonException or TimeoutException) { }
    }

    private async Task<string> ResolveThreadAsync(string? existingThreadId, CancellationToken cancellationToken)
    {
        if (!string.IsNullOrWhiteSpace(existingThreadId))
        {
            try
            {
                var resumed = await RequestAsync("thread/resume", new { threadId = existingThreadId }, cancellationToken);
                return resumed.GetProperty("thread").GetProperty("id").GetString() ?? existingThreadId;
            }
            catch (InvalidOperationException) { }
        }

        var models = await RequestAsync("model/list", new { limit = 100, includeHidden = false }, cancellationToken);
        string? selectedModel = null;
        if (models.TryGetProperty("data", out var data) && data.ValueKind == JsonValueKind.Array)
        {
            foreach (var model in data.EnumerateArray())
            {
                if (model.TryGetProperty("isDefault", out var isDefault) && isDefault.ValueKind == JsonValueKind.True &&
                    model.TryGetProperty("model", out var modelName))
                {
                    selectedModel = modelName.GetString();
                    break;
                }
            }
        }
        var started = await RequestAsync("thread/start", new
        {
            model = selectedModel,
            cwd = _settings!.CoachHome,
            approvalPolicy = "never",
            sandbox = "read-only",
            runtimeWorkspaceRoots = RuntimeRoots(_settings),
            environments = Array.Empty<object>(),
            ephemeral = false
        }, cancellationToken);
        return started.GetProperty("thread").GetProperty("id").GetString()
            ?? throw new InvalidOperationException("Coach Engine returned a thread without an identifier.");
    }

    private static string[] RuntimeRoots(CompanionSettings settings) =>
        new[] { settings.CoachHome, settings.IRacingRoot, settings.IRacingInstallRoot }
            .Where(path => !string.IsNullOrWhiteSpace(path) && Path.IsPathFullyQualified(path))
            .Select(Path.GetFullPath)
            .Distinct(StringComparer.OrdinalIgnoreCase)
            .ToArray();

    private void HandleTurnNotification(string? method, JsonElement parameters)
    {
        if (method == "item/agentMessage/delta" &&
            parameters.TryGetProperty("turnId", out var deltaTurn) &&
            parameters.TryGetProperty("delta", out var deltaElement) &&
            deltaTurn.GetString() is { Length: > 0 } deltaTurnId)
        {
            var delta = deltaElement.GetString() ?? string.Empty;
            if (_turns.TryGetValue(deltaTurnId, out var collector)) collector.Append(delta);
            else _earlyTurnDeltas.GetOrAdd(deltaTurnId, _ => new ConcurrentQueue<string>()).Enqueue(delta);
            CoachMessageDelta?.Invoke(delta);
            return;
        }
        if (method == "turn/completed" &&
            parameters.TryGetProperty("turn", out var turn) &&
            turn.TryGetProperty("id", out var completedId) &&
            completedId.GetString() is { Length: > 0 } completedTurnId)
        {
            var status = turn.TryGetProperty("status", out var statusElement) ? statusElement.GetString() : null;
            if (_turns.TryGetValue(completedTurnId, out var completed)) CompleteTurn(completed, status);
            else _earlyTurnCompletions[completedTurnId] = status ?? "unknown";
        }
    }

    private static void CompleteTurn(TurnCollector collector, string? status)
    {
        if (string.Equals(status, "completed", StringComparison.OrdinalIgnoreCase)) collector.Complete();
        else collector.Fail(new InvalidOperationException($"Coach Engine turn ended with status {status ?? "unknown"}."));
    }

    private async Task InterruptTurnAsync(string threadId, string turnId)
    {
        try { await RequestAsync("turn/interrupt", new { threadId, turnId }, CancellationToken.None); }
        catch (Exception ex) when (ex is IOException or InvalidOperationException or TimeoutException) { }
    }

    private void OnProcessExited(object? sender, EventArgs e)
    {
        FailPending(new IOException("Coach Engine stopped unexpectedly."));
        if (_stopping) return;
        SetCurrent(Current with { Running = false, ChatGptConnected = false, Status = "restarting", Message = "Coach Engine is restarting…" });
        if (_restartAttempts++ < 3 && _settings is not null) _ = RestartAfterDelayAsync(_settings, _restartAttempts);
        else SetCurrent(Current with { Status = "unavailable", Message = "Coach Engine stopped repeatedly. Use Repair installation." });
    }

    private async Task RestartAfterDelayAsync(CompanionSettings settings, int attempt)
    {
        await Task.Delay(TimeSpan.FromSeconds(Math.Min(15, attempt * attempt)));
        if (!_stopping) await StartAsync(settings);
    }

    private async Task StopProcessCoreAsync()
    {
        var process = _process;
        _process = null;
        if (process is null) return;
        process.Exited -= OnProcessExited;
        try
        {
            if (!process.HasExited)
            {
                // App-server has no separate shutdown request. Close stdin for a
                // short graceful exit, then always verify the full tree ended.
                process.StandardInput.Close();
                if (!process.WaitForExit(1500)) process.Kill(entireProcessTree: true);
                await process.WaitForExitAsync();
            }
        }
        catch (Exception ex) when (ex is InvalidOperationException or System.ComponentModel.Win32Exception) { }
        finally
        {
            process.Dispose();
        }
    }

    private void FailPending(Exception exception)
    {
        foreach (var completion in _pending.Values) completion.TrySetException(exception);
    }

    private void SetCurrent(CoachEngineConnection value)
    {
        Current = value;
        Changed?.Invoke(value);
    }

    private static string Safe(string value)
    {
        value = value.Replace('\r', ' ').Replace('\n', ' ').Trim();
        return value.Length <= 240 ? value : value[..240];
    }

    public async ValueTask DisposeAsync()
    {
        await StopAsync();
        _lifecycle.Dispose();
        _writer.Dispose();
    }

    private sealed class TurnCollector(string threadId, string turnId)
    {
        private readonly StringBuilder _text = new();
        private readonly object _sync = new();
        public string ThreadId { get; } = threadId;
        public string TurnId { get; } = turnId;
        public TaskCompletionSource<string> Completion { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public void Append(string value) { lock (_sync) _text.Append(value); }
        public void Complete() { lock (_sync) Completion.TrySetResult(_text.ToString()); }
        public void Fail(Exception exception) => Completion.TrySetException(exception);
    }
}
