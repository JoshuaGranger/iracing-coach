using System.Text.Json;
using iRacingCoach.BackendClient;

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
}
