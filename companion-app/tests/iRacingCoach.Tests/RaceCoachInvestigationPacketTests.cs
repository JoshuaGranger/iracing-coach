using System.Text.Json;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class RaceCoachInvestigationPacketTests
{
    [TestMethod]
    public void PacketPreservesExactLapPairingAndFinalObservedExclusions()
    {
        var packet = RaceCoachPacketBuilder.Build(Workspace());
        var lapSection = packet.Sections.Single(section => section.Subject == "lap_series");
        CollectionAssert.AreEqual(new[] { 12d, 33d }, lapSection.Series.Single(series => series.Name == "observed_lap_number").Values.ToArray());
        CollectionAssert.AreEqual(new[] { 15.5d, 16.9d }, lapSection.Series.Single(series => series.Name == "observed_lap_time").Values.ToArray());
        CollectionAssert.AreEqual(new[] { 1d, 0d }, lapSection.Series.Single(series => series.Name == "reference_screening_passed").Values.ToArray());
        CollectionAssert.AreEqual(new[] { 31d, 32d, 33d }, lapSection.Series.Single(series => series.Name == "later-run/compared/lap_number").Values.ToArray());
        Assert.AreEqual(0d, lapSection.Series.Single(series => series.Name == "later-run/all_laps_passed_screening").Values.Single());
        Assert.IsFalse(lapSection.Series.Any(series => series.Name.Contains("wear/", StringComparison.Ordinal)), "Missing wear measurements must not turn into zero-valued evidence.");
        StringAssert.Contains(lapSection.Claim.Text, "not clean coaching references");
    }

    [TestMethod]
    public void CornerPacketHasBackendRegionBoundsAndCohortLapIds()
    {
        var packet = RaceCoachPacketBuilder.Build(Workspace());
        var section = packet.Sections.Single(section => section.Subject == "corner_window");
        Assert.IsTrue(section.Available);
        var delta = section.Series.Single(series => series.Name.EndsWith("/area_time_delta", StringComparison.Ordinal));
        Assert.AreEqual("corner", delta.Window.Kind);
        Assert.AreEqual(.1d, delta.Window.Start);
        Assert.AreEqual(.4d, delta.Window.End);
        Assert.AreEqual(.4d, delta.Values.Single());
        CollectionAssert.AreEqual(new[] { 12d, 13d, 14d }, section.Series.Single(series => series.Name == "later-run/baseline/lap_number").Values.ToArray());
        StringAssert.Contains(section.Claim.Text, "not named official turns or telemetry targets");
        Assert.HasCount(7, packet.Sections);
        CollectionAssert.AreEquivalent(new[] { "lap_series", "run_series", "corner_window", "plan", "tire", "setup", "progress" }, packet.Sections.Select(item => item.Subject).ToArray());
    }

    [TestMethod]
    public void ChangedScreeningOrLapIdentityInvalidatesPacketIdentity()
    {
        var workspace = Workspace();
        var original = RaceCoachPacketBuilder.Build(workspace);
        var investigation = workspace.Investigation!;
        var changedFlag = investigation with { Laps = investigation.Laps.Select(lap => lap with { Eligible = true }).ToArray() };
        var changedLap = investigation with { Laps = investigation.Laps.Select(lap => lap with { Lap = lap.Lap + 1 }).ToArray() };
        Assert.AreNotEqual(original.PacketId, RaceCoachPacketBuilder.Build(workspace with { Investigation = changedFlag }).PacketId);
        Assert.AreNotEqual(original.PacketId, RaceCoachPacketBuilder.Build(workspace with { Investigation = changedLap }).PacketId);
    }

    [TestMethod]
    public void ContextWhitelistsEvidenceAndStatesSampleCoverageWithoutPromotingStartingUsage()
    {
        var workspace = Workspace();
        var investigation = workspace.Investigation!;
        var largeLaps = Enumerable.Range(1, 700).Select(lap => new RaceInvestigationLap(lap, 15 + lap * .001, 3, "green", false, ["close_traffic"], 1, true)).ToArray();
        workspace = workspace with { Investigation = investigation with { Laps = largeLaps } };
        var packet = RaceCoachPacketBuilder.Build(workspace);
        var values = packet.Sections.Single(section => section.Subject == "lap_series").Series.Single(series => series.Name == "observed_lap_number").Values;
        Assert.HasCount(512, values);
        Assert.AreEqual(1d, values[0]);
        Assert.AreEqual(700d, values[^1]);
        var contextJson = JsonSerializer.Serialize(RaceCoachPacketBuilder.InvestigationContext(workspace));
        Assert.IsFalse(contextJson.Contains("future_evidence", StringComparison.Ordinal));
        Assert.IsFalse(contextJson.Contains("\"Source\"", StringComparison.Ordinal));
        StringAssert.Contains(contextJson, "moderately high usage");
        StringAssert.Contains(contextJson, "not measured evolution");
        StringAssert.Contains(contextJson, "No changing track-usage channel was recorded.");
        using var context = JsonDocument.Parse(contextJson);
        Assert.IsFalse(context.RootElement.GetProperty("coverage").GetProperty("complete_lap_series").GetBoolean());
    }

    [TestMethod]
    public void InvalidAreaBoundsDoNotBecomeNumericCornerSupport()
    {
        var workspace = Workspace();
        var investigation = workspace.Investigation!;
        var comparison = investigation.Comparisons.Single();
        var reversed = comparison.Corners.Single() with { StartPct = .8, EndPct = .2 };
        workspace = workspace with { Investigation = investigation with { Comparisons = [comparison with { Corners = [reversed] }] } };
        var section = RaceCoachPacketBuilder.Build(workspace).Sections.Single(section => section.Subject == "corner_window");
        Assert.IsFalse(section.Available);
        Assert.IsEmpty(section.Series);
    }

    private static AnalysisWorkspace Workspace()
    {
        using var baseDocument = JsonDocument.Parse("""
            {"analysis_id":"investigation-packet-test","analysis_view":{"schema_version":1,"identity":{"track_name":"Track","car_name":"Car","event_type":"Race"},"race_summary":{},"runs":[],"laps":[],"lap_traces":{"traces":[]},"track_profile":{"detected_corner_segments":[]},"strategy":{},"damage_repair":{},"setup_telemetry":{},"data_quality":{}}}
            """);
        using var investigationDocument = JsonDocument.Parse(RaceInvestigationMapperTests.InvestigationJson);
        return RuntimeMapper.Analysis(baseDocument.RootElement) with { Investigation = RaceInvestigationMapper.Map(investigationDocument.RootElement) };
    }
}
