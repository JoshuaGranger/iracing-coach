using System.Text;
using System.Text.Json;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class NonFiniteJsonTests
{
    [TestMethod]
    public void BareNonFiniteValuesBecomeUnknownButStringsAreUntouched()
    {
        const string source = """
            {"total_time_s": Infinity, "range":[NaN, -Infinity ,1.5],
             "note":"Infinity, NaN and -Infinity stay text", "key\"Infinity":"a\\", "after": NaN}
            """;
        var bytes = Encoding.UTF8.GetBytes(source);
        var sanitized = NonFiniteJson.NullBareNonFiniteNumbers(bytes, out var replaced);

        Assert.AreEqual(4, replaced);
        using var document = JsonDocument.Parse(sanitized);
        var root = document.RootElement;
        Assert.AreEqual(JsonValueKind.Null, root.GetProperty("total_time_s").ValueKind, "Unknown, never zero.");
        CollectionAssert.AreEqual(
            new[] { JsonValueKind.Null, JsonValueKind.Null, JsonValueKind.Number },
            root.GetProperty("range").EnumerateArray().Select(item => item.ValueKind).ToArray());
        Assert.AreEqual(1.5, root.GetProperty("range")[2].GetDouble());
        Assert.AreEqual("Infinity, NaN and -Infinity stay text", root.GetProperty("note").GetString());
        Assert.AreEqual("a\\", root.GetProperty("key\"Infinity").GetString());
        Assert.AreEqual(JsonValueKind.Null, root.GetProperty("after").ValueKind);
    }

    // Two of the report reads moved from File.ReadAllText, which drops a UTF-8 byte order
    // mark, to file bytes. A report saved with one must still parse.
    [TestMethod]
    public void ByteOrderMarkIsAcceptedLikeReadAllText()
    {
        var bytes = Encoding.UTF8.GetPreamble().Concat(Encoding.UTF8.GetBytes("{\"total_time_s\": Infinity, \"lap\": 3}")).ToArray();
        using var document = NonFiniteJson.Parse(bytes);
        Assert.AreEqual(JsonValueKind.Null, document.RootElement.GetProperty("total_time_s").ValueKind);
        Assert.AreEqual(3, document.RootElement.GetProperty("lap").GetInt32());

        using var standard = NonFiniteJson.Parse(Encoding.UTF8.GetPreamble().Concat("{\"lap\":4}"u8.ToArray()).ToArray());
        Assert.AreEqual(4, standard.RootElement.GetProperty("lap").GetInt32());
    }

    [TestMethod]
    public void StandardJsonIsReturnedWithoutCopying()
    {
        foreach (var source in new[] { "{\"lap\":1}", "{\"label\":\"NaN Infinity\"}", "[\"-Infinity\"]" })
        {
            var bytes = Encoding.UTF8.GetBytes(source);
            Assert.AreSame(bytes, NonFiniteJson.NullBareNonFiniteNumbers(bytes, out var replaced), source);
            Assert.AreEqual(0, replaced, source);
        }
    }

    [TestMethod]
    public void OnlyWholeValueTokensAreReplaced()
    {
        foreach (var source in new[] { "{\"a\":Infinityx}", "{\"a\":NaNa}", "{Infinity:1}", "{\"a\":1 NaN}" })
        {
            var bytes = Encoding.UTF8.GetBytes(source);
            var sanitized = NonFiniteJson.NullBareNonFiniteNumbers(bytes, out var replaced);
            Assert.AreEqual(0, replaced, source);
            Assert.Throws<JsonException>(() => JsonDocument.Parse(sanitized).Dispose(), source);
        }
    }
}
