using System.Text.Json;
using IRacingCoach.Telemetry;
using Microsoft.VisualStudio.TestTools.UnitTesting;

namespace iRacingCoach.Tests;

/// <summary>
/// Proves the managed <see cref="IbtDecoder"/> is byte-identical to the Python
/// backend's <c>ibt_reader.load_telemetry</c>. Python is the authoritative
/// oracle; <c>tools/parity/emit_ibt_golden.py</c> records the catalogue, the
/// sampling plan, and a content hash of the decoded value matrix, and this test
/// fails the moment the C# port diverges by a single byte. That is the gate that
/// lets decode move out of Python without changing any answer downstream.
/// </summary>
[TestClass]
public sealed class IbtDecoderParityTests
{
    private static readonly string IbtPath =
        Path.Combine(TestRepositoryPaths.RepositoryRoot, "test-data", "ibt", "synthetic-race.ibt");

    private static readonly string GoldenPath =
        Path.Combine(TestRepositoryPaths.RepositoryRoot, "test-data", "ibt", "synthetic-race.decode-golden.json");

    private static JsonElement LoadGolden()
    {
        using var document = JsonDocument.Parse(File.ReadAllText(GoldenPath));
        return document.RootElement.Clone();
    }

    [TestMethod]
    public void FullRateDecodeMatchesThePythonOracleByteForByte()
    {
        AssertMatchesGolden(LoadGolden().GetProperty("full_rate"), targetHz: null);
    }

    [TestMethod]
    public void TwentyHertzDownsampleMatchesThePythonOracleByteForByte()
    {
        AssertMatchesGolden(LoadGolden().GetProperty("downsampled_20hz"), targetHz: 20);
    }

    private static void AssertMatchesGolden(JsonElement golden, double? targetHz)
    {
        IbtDecodeResult result = IbtDecoder.Load(IbtPath, targetHz);

        Assert.AreEqual(golden.GetProperty("native_tick_rate_hz").GetInt32(), result.NativeTickRateHz, "native tick rate");
        Assert.AreEqual(golden.GetProperty("sample_rate_hz").GetDouble(), result.SampleRateHz, "output sample rate");
        Assert.AreEqual(golden.GetProperty("source_record_count").GetInt32(), result.SourceRecordCount, "source record count");
        Assert.AreEqual(golden.GetProperty("sample_count").GetInt32(), result.SampleCount, "retained sample count");

        int[] goldenIndices = golden.GetProperty("sample_indices").EnumerateArray().Select(e => e.GetInt32()).ToArray();
        CollectionAssert.AreEqual(goldenIndices, result.SampleIndices, "the retained record indices must match the Python decimation exactly");

        JsonElement goldenVars = golden.GetProperty("variables");
        Assert.HasCount(goldenVars.GetArrayLength(), result.Variables, "variable catalogue size");
        for (int i = 0; i < result.Variables.Count; i++)
        {
            JsonElement expected = goldenVars[i];
            IbtVariable actual = result.Variables[i];
            Assert.AreEqual(expected.GetProperty("name").GetString(), actual.Name, $"variable #{i} name");
            Assert.AreEqual(expected.GetProperty("type_code").GetInt32(), actual.TypeCode, $"variable {actual.Name} type");
            Assert.AreEqual(expected.GetProperty("count").GetInt32(), actual.Count, $"variable {actual.Name} count");
            Assert.AreEqual(expected.GetProperty("offset").GetInt32(), actual.Offset, $"variable {actual.Name} offset");
            Assert.AreEqual(expected.GetProperty("unit").GetString(), actual.Unit, $"variable {actual.Name} unit");
        }

        // The decisive assertion: identical hashes over the decoded value matrix
        // mean the C# reader pulled the same bytes out of the same records as
        // Python, for every channel and every retained sample.
        Assert.AreEqual(
            golden.GetProperty("matrix_sha256").GetString(),
            result.MatrixSha256(),
            "decoded value matrix hash must match the Python oracle byte-for-byte");

        AssertSpotValues(golden.GetProperty("spot_values"), result);
    }

    private static void AssertSpotValues(JsonElement spot, IbtDecodeResult result)
    {
        foreach (JsonProperty channel in spot.EnumerateObject())
        {
            Assert.IsTrue(result.TryColumn(channel.Name, out IbtColumn column), $"channel {channel.Name} present");
            foreach (JsonElement pick in channel.Value.EnumerateArray())
            {
                int index = pick[0].GetInt32();
                JsonElement expected = pick[1];
                if (column.Variable.TypeCode == 0)
                {
                    Assert.AreEqual(expected.GetString(), column.GetString(index), $"{channel.Name}[{index}]");
                    continue;
                }

                object value = column.GetValue(index);
                if (column.Variable.TypeCode == 4)
                {
                    // Golden stores float32-widened values as round-trippable repr.
                    double expectedDouble = double.Parse(expected.GetString()!, System.Globalization.CultureInfo.InvariantCulture);
                    Assert.AreEqual(expectedDouble, (double)value, $"{channel.Name}[{index}] float value");
                }
                else if (column.Variable.TypeCode == 5)
                {
                    Assert.AreEqual(expected.GetDouble(), (double)value, $"{channel.Name}[{index}] double value");
                }
                else if (column.Variable.TypeCode == 3)
                {
                    Assert.AreEqual(expected.GetUInt32(), (uint)value, $"{channel.Name}[{index}] bitfield value");
                }
                else if (column.Variable.TypeCode == 2)
                {
                    Assert.AreEqual(expected.GetInt32(), (int)value, $"{channel.Name}[{index}] int value");
                }
                else if (column.Variable.TypeCode == 1)
                {
                    Assert.AreEqual(expected.GetBoolean(), (bool)value, $"{channel.Name}[{index}] bool value");
                }
            }
        }
    }
}
