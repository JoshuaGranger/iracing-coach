using System.Text.Json;
using iRacingCoach.Coordinator;
using iRacingCoach.Contracts;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class LiveTruthPolicyConsumerTests
{
    [TestMethod]
    public void EveryGeneratedFlagVector_DecodesExactlyLikeBackendPolicy()
    {
        using var fixture = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "fixtures", "live-truth-conformance-v1.json")));
        Assert.AreEqual(LiveTruthPolicy.ContractVersion, fixture.RootElement.GetProperty("policy_version").GetInt32());
        Assert.AreEqual(LiveTruthPolicy.CautionMask, fixture.RootElement.GetProperty("caution_mask").GetUInt32());

        foreach (var vector in fixture.RootElement.GetProperty("flag_vectors").EnumerateArray())
        {
            var raw = vector.GetProperty("session_flags");
            uint? flags = raw.ValueKind == JsonValueKind.Number && raw.TryGetUInt32(out var value) ? value : null;
            Assert.AreEqual(vector.GetProperty("expected_racing_state").GetString(), LiveTruthPolicy.DecodeRacingState(flags), vector.GetProperty("case").GetString());
            Assert.AreEqual(vector.GetProperty("expected_repair_state").GetString(), LiveTruthPolicy.DecodeRepairState(flags), vector.GetProperty("case").GetString());
        }

        foreach (var vector in fixture.RootElement.GetProperty("lap_distance_percent_vectors").EnumerateArray())
        {
            var raw = vector.GetProperty("value");
            double? input = raw.ValueKind == JsonValueKind.Number && raw.TryGetDouble(out var number) ? number : null;
            var expectedRaw = vector.GetProperty("expected");
            double? expected = expectedRaw.ValueKind == JsonValueKind.Number ? expectedRaw.GetDouble() : null;
            Assert.AreEqual(expected, LiveTruthPolicy.NormalizeLapDistance(input), vector.GetProperty("case").GetString());
        }
    }

    [TestMethod]
    public void MissingFlagChannel_CannotCreateCleanLapOrCompetitiveCue()
    {
        var engine = new LiveTelemetryEngine();
        var start = DateTimeOffset.UtcNow;
        LiveRaceSnapshot sample(int lap, int second) => engine.Update(new LiveTelemetrySample
        {
            Connected = true,
            Timestamp = start.AddSeconds(second),
            Lap = lap,
            LastLapSeconds = 30,
            FuelLiters = 20 - lap,
            SessionFlagsKnown = false,
            Flag = "UNKNOWN",
            GapToAheadSeconds = .2,
            GapToBehindSeconds = .2
        }, false, false);

        _ = sample(1, 0);
        _ = sample(2, 1);
        _ = sample(3, 2);
        _ = sample(4, 3);
        var result = sample(5, 4);

        Assert.IsNull(result.PaceTarget.MinimumSeconds);
        Assert.AreEqual(EvidenceKind.Unavailable, result.PrimaryCue.Evidence);
        StringAssert.Contains(result.PrimaryCue.Message, "Race flag unavailable");
        Assert.AreEqual(LiveGapTrend.Stale, result.AheadGap.Trend);
        Assert.AreEqual("Unavailable", result.PenaltyStatus);
    }
}
