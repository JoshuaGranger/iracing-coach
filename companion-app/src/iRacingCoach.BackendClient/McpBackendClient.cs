using System.Diagnostics;
using System.Text.Json;
using iRacingCoach.Contracts;

namespace iRacingCoach.BackendClient;

public sealed class McpBackendClient : IBackendClient
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web);
    private const string ProtocolVersion = "2025-06-18";

    public async Task<BackendHealthResult> CheckHealthAsync(
        BackendConfiguration configuration,
        CancellationToken cancellationToken = default)
    {
        var timer = Stopwatch.StartNew();
        try
        {
            await using var session = await McpSession.StartAsync(configuration, cancellationToken);
            using var initialize = await session.RequestAsync("initialize", new
            {
                protocolVersion = ProtocolVersion,
                clientInfo = new { name = "iracing_coach_companion", version = configuration.ClientVersion },
                capabilities = new { }
            }, cancellationToken);

            var result = initialize.RootElement.GetProperty("result");
            var server = result.GetProperty("serverInfo");
            var negotiated = result.GetProperty("protocolVersion").GetString() ?? ProtocolVersion;
            await session.NotifyAsync("notifications/initialized", new { }, cancellationToken);
            using var ping = await session.RequestAsync("ping", new { }, cancellationToken);
            using var tools = await session.RequestAsync("tools/list", new { }, cancellationToken);
            var count = tools.RootElement.GetProperty("result").GetProperty("tools").GetArrayLength();

            return new BackendHealthResult(
                true,
                server.GetProperty("name").GetString() ?? "iracing-coach-local",
                server.GetProperty("version").GetString() ?? "unknown",
                negotiated,
                count,
                timer.Elapsed);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception ex)
        {
            return new BackendHealthResult(false, "iracing-coach-local", "unavailable", ProtocolVersion, 0, timer.Elapsed, ex.Message);
        }
    }

    public async Task<JsonElement> CallToolAsync(
        BackendConfiguration configuration,
        string toolName,
        object arguments,
        CancellationToken cancellationToken = default)
    {
        await using var session = await McpSession.StartAsync(configuration, cancellationToken);
        using var initialize = await session.RequestAsync("initialize", new
        {
            protocolVersion = ProtocolVersion,
            clientInfo = new { name = "iracing_coach_companion", version = configuration.ClientVersion },
            capabilities = new { }
        }, cancellationToken);
        await session.NotifyAsync("notifications/initialized", new { }, cancellationToken);
        using var response = await session.RequestAsync("tools/call", new { name = toolName, arguments }, cancellationToken);
        return ParseToolResult(response.RootElement);
    }

    public static JsonElement ParseToolResult(JsonElement response)
    {
        if (response.TryGetProperty("error", out var protocolError))
        {
            throw new BackendProtocolException(protocolError.GetRawText());
        }

        var result = response.GetProperty("result");
        if (result.TryGetProperty("isError", out var isError) && isError.ValueKind == JsonValueKind.True)
        {
            var text = ExtractContentText(result);
            throw new BackendDomainException(text);
        }

        var domainJson = ExtractContentText(result);
        using var domain = JsonDocument.Parse(domainJson);
        return domain.RootElement.Clone();
    }

    private static string ExtractContentText(JsonElement result)
    {
        var content = result.GetProperty("content");
        if (content.GetArrayLength() == 0)
        {
            throw new BackendProtocolException("The backend returned an empty MCP content array.");
        }

        return content[0].GetProperty("text").GetString()
            ?? throw new BackendProtocolException("The backend returned MCP content without text.");
    }

    private sealed class McpSession : IAsyncDisposable
    {
        private readonly Process _process;
        private readonly Task<string> _stderr;
        private long _nextId;

        private McpSession(Process process)
        {
            _process = process;
            _stderr = process.StandardError.ReadToEndAsync();
        }

        public static Task<McpSession> StartAsync(BackendConfiguration configuration, CancellationToken cancellationToken)
        {
            cancellationToken.ThrowIfCancellationRequested();
            var start = new ProcessStartInfo
            {
                FileName = configuration.PowerShellPath,
                UseShellExecute = false,
                RedirectStandardInput = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden
            };
            start.ArgumentList.Add("-NoLogo");
            start.ArgumentList.Add("-NoProfile");
            start.ArgumentList.Add("-NonInteractive");
            start.ArgumentList.Add("-ExecutionPolicy");
            start.ArgumentList.Add("Bypass");
            start.ArgumentList.Add("-File");
            start.ArgumentList.Add(configuration.LauncherPath);
            start.Environment["IRACING_COACH_PYTHON"] = configuration.PythonPath;
            start.Environment["IRACING_COACH_IRACING_ROOT"] = configuration.IRacingRoot;
            if (!string.IsNullOrWhiteSpace(configuration.IRacingInstallRoot))
            {
                start.Environment["IRACING_COACH_INSTALL_ROOT"] = configuration.IRacingInstallRoot;
            }
            start.Environment["IRACING_COACH_DATA"] = configuration.ArchiveRoot;
            start.Environment["IRACING_COACH_HOME"] = configuration.CoachHomeRoot;
            start.Environment["PYTHONUTF8"] = "1";

            var process = Process.Start(start)
                ?? throw new BackendProtocolException("The backend worker could not be started.");
            return Task.FromResult(new McpSession(process));
        }

        public async Task<JsonDocument> RequestAsync(string method, object parameters, CancellationToken cancellationToken)
        {
            var id = Interlocked.Increment(ref _nextId);
            var request = JsonSerializer.Serialize(new { jsonrpc = "2.0", id, method, @params = parameters }, JsonOptions);
            await _process.StandardInput.WriteLineAsync(request.AsMemory(), cancellationToken);
            await _process.StandardInput.FlushAsync(cancellationToken);

            while (true)
            {
                var line = await _process.StandardOutput.ReadLineAsync(cancellationToken);
                if (line is null)
                {
                    var error = await _stderr;
                    throw new BackendProtocolException($"Backend worker exited before responding. {Bound(error)}".Trim());
                }

                var document = JsonDocument.Parse(line);
                if (document.RootElement.TryGetProperty("id", out var responseId) && responseId.GetInt64() == id)
                {
                    return document;
                }

                document.Dispose();
            }
        }

        public async Task NotifyAsync(string method, object parameters, CancellationToken cancellationToken)
        {
            var notification = JsonSerializer.Serialize(new { jsonrpc = "2.0", method, @params = parameters }, JsonOptions);
            await _process.StandardInput.WriteLineAsync(notification.AsMemory(), cancellationToken);
            await _process.StandardInput.FlushAsync(cancellationToken);
        }

        public async ValueTask DisposeAsync()
        {
            try
            {
                _process.StandardInput.Close();
                if (!_process.HasExited)
                {
                    _process.Kill(entireProcessTree: true);
                    await _process.WaitForExitAsync();
                }
            }
            catch (InvalidOperationException)
            {
                // Process has already exited.
            }
            finally
            {
                _process.Dispose();
            }
        }

        private static string Bound(string value) => value.Length <= 4_000 ? value : value[..4_000];
    }
}

public sealed class BackendProtocolException(string message) : Exception(message);
public sealed class BackendDomainException(string message) : Exception(message);
