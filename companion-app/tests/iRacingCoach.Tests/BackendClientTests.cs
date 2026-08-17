using System.Text.Json;
using System.Diagnostics;
using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class BackendClientTests
{
    [TestMethod]
    public void ParseToolResult_UnwrapsMcpContentJson()
    {
        using var response = JsonDocument.Parse("""
        {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"{\"ok\":true,\"contract_version\":1,\"future_field\":42}"}],"isError":false}}
        """);
        var parsed = McpBackendClient.ParseToolResult(response.RootElement);
        Assert.IsTrue(parsed.GetProperty("ok").GetBoolean());
        Assert.AreEqual(42, parsed.GetProperty("future_field").GetInt32());
    }

    [TestMethod]
    public void ParseToolResult_PreservesUnicodeDriverFacingText()
    {
        using var response = JsonDocument.Parse("""
        {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"{\"label\":\"RF inner · run 1\",\"phase\":\"early→late\"}"}],"isError":false}}
        """);

        var parsed = McpBackendClient.ParseToolResult(response.RootElement);

        Assert.AreEqual("RF inner · run 1", parsed.GetProperty("label").GetString());
        Assert.AreEqual("early→late", parsed.GetProperty("phase").GetString());
    }

    [TestMethod]
    public void ParseToolResult_ThrowsActionableDomainError()
    {
        using var response = JsonDocument.Parse("""
        {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text","text":"active file is still changing"}],"isError":true}}
        """);
        var error = Assert.ThrowsExactly<BackendDomainException>(() => McpBackendClient.ParseToolResult(response.RootElement));
        StringAssert.Contains(error.Message, "active file");
    }

    [TestMethod]
    public void ParseToolResult_RejectsProtocolErrors()
    {
        using var response = JsonDocument.Parse("""{"jsonrpc":"2.0","id":2,"error":{"code":-32601,"message":"unknown"}}""");
        Assert.ThrowsExactly<BackendProtocolException>(() => McpBackendClient.ParseToolResult(response.RootElement));
    }

    [TestMethod]
    public void OperationDeadlines_DistinguishLocalAnalysisAndOptionalNetworkWork()
    {
        var deadlines = new McpBackendDeadlines(
            TimeSpan.FromSeconds(1),
            TimeSpan.FromSeconds(2),
            TimeSpan.FromSeconds(3),
            TimeSpan.FromSeconds(4));

        Assert.AreEqual(TimeSpan.FromSeconds(2), deadlines.ForTool("iracing_companion_dashboard"));
        Assert.AreEqual(TimeSpan.FromSeconds(3), deadlines.ForTool("analyze_iracing_race"));
        Assert.AreEqual(TimeSpan.FromSeconds(4), deadlines.ForTool("sync_garage61_references"));
    }

    [TestMethod]
    [DoNotParallelize]
    public async Task SilentBackendWorker_IsKilledAtTheOperationDeadline()
    {
        var (configuration, root) = SilentBackendConfiguration();
        var deadlines = new McpBackendDeadlines(
            TimeSpan.FromMilliseconds(200),
            TimeSpan.FromMilliseconds(200),
            TimeSpan.FromMilliseconds(200),
            TimeSpan.FromMilliseconds(200));
        var client = new McpBackendClient(deadlines);
        var timer = Stopwatch.StartNew();
        try
        {
            var error = await Assert.ThrowsExactlyAsync<BackendOperationTimeoutException>(() =>
                client.CallToolAsync(configuration, "iracing_companion_dashboard", new { }));

            Assert.AreEqual("iracing_companion_dashboard", error.Operation);
            Assert.IsLessThan(TimeSpan.FromSeconds(5), timer.Elapsed);
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    [TestMethod]
    [DoNotParallelize]
    public async Task SilentBackendWorker_UserCancellationRemainsCancellationNotTimeout()
    {
        var (configuration, root) = SilentBackendConfiguration();
        var deadlines = new McpBackendDeadlines(
            TimeSpan.FromSeconds(10),
            TimeSpan.FromSeconds(10),
            TimeSpan.FromSeconds(10),
            TimeSpan.FromSeconds(10));
        var client = new McpBackendClient(deadlines);
        using var cancellation = new CancellationTokenSource(TimeSpan.FromMilliseconds(200));
        try
        {
            await Assert.ThrowsAsync<OperationCanceledException>(() =>
                client.CallToolAsync(configuration, "iracing_companion_dashboard", new { }, cancellation.Token));
        }
        finally
        {
            Directory.Delete(root, recursive: true);
        }
    }

    private static (BackendConfiguration Configuration, string Root) SilentBackendConfiguration()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-silent-backend", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        var launcher = Path.Combine(root, "silent-worker.ps1");
        File.WriteAllText(launcher, "$null = [Console]::In.ReadLine()\nStart-Sleep -Seconds 30\n");
        var windows = Environment.GetFolderPath(Environment.SpecialFolder.Windows);
        var powershell = Path.Combine(windows, "System32", "WindowsPowerShell", "v1.0", "powershell.exe");
        return (new BackendConfiguration(
            powershell,
            launcher,
            "python.exe",
            root,
            root,
            root,
            LocalStateRoot: root,
            UserProfileRoot: root,
            TemporaryRoot: root,
            NetworkAllowed: false), root);
    }
}
