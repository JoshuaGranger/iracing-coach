using System.Text.Json;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class RaceCoachPacketConsumerTests
{
    [TestMethod]
    public void Packet_CarriesNumbersUnitsWindowsAndEvidenceLinksTogether()
    {
        var packet = RaceCoachPacketBuilder.Build(Workspace(31.2));

        Assert.IsTrue(packet.HasNumericEvidence);
        Assert.IsTrue(packet.PacketId.StartsWith("pk-", StringComparison.Ordinal));
        foreach (var section in packet.Sections.Where(section => section.Available))
        {
            Assert.IsNotEmpty(section.Series);
            Assert.IsTrue(section.Claim.EvidenceId.StartsWith("ev-", StringComparison.Ordinal));
            foreach (var series in section.Series)
            {
                Assert.IsFalse(string.IsNullOrWhiteSpace(series.Unit));
                Assert.IsNotEmpty(series.Values);
                Assert.IsTrue(series.Values.All(double.IsFinite));
                Assert.IsGreaterThanOrEqualTo(series.Window.Start, series.Window.End);
            }
        }
    }

    [TestMethod]
    public void IdenticalProseWithDifferentNumbers_HasDifferentPacketIdentity()
    {
        var first = RaceCoachPacketBuilder.Build(Workspace(31.2));
        var second = RaceCoachPacketBuilder.Build(Workspace(32.2));

        Assert.AreNotEqual(first.PacketId, second.PacketId);
    }

    [TestMethod]
    public void ProseOnlyWorkspace_IsUnavailableInsteadOfSuccessful()
    {
        using var document = JsonDocument.Parse("""
            {"analysis_id":"empty","analysis_view":{"schema_version":1,"identity":{"track_name":"Track","car_name":"Car","event_type":"Race"},"race_summary":{},"runs":[],"laps":[],"lap_traces":{"traces":[]},"track_profile":{"detected_corner_segments":[]},"strategy":{},"damage_repair":{},"setup_telemetry":{},"data_quality":{}}}
            """);

        var packet = RaceCoachPacketBuilder.Build(RuntimeMapper.Analysis(document.RootElement));

        Assert.IsFalse(packet.HasNumericEvidence, string.Join(",", packet.SupportedSubjects));
        Assert.IsTrue(packet.Sections.All(section => !section.Available));
        Assert.IsTrue(packet.Sections.All(section => section.Claim.Kind == "unavailable"));
    }

    private static iRacingCoach.Contracts.AnalysisWorkspace Workspace(double lapTime)
    {
        var json = """
            {"analysis_id":"analysis-1","analysis_view":{"schema_version":1,
            "identity":{"track_name":"Test Track","track_config":"Road","car_name":"Test Car","event_type":"Race","is_fixed_setup":false,"setup_fingerprint":"abc123"},
            "race_summary":{"recorded_laps":2,"scheduled_laps":10,"pit_stops_detected":0},
            "runs":[{"run_number":1,"lap_numbers":[1,2],"green_laps":2,"caution_laps":0,"pace":{"green_lap_time_slope_s_per_lap":0.1},"damage_repair_context":{"automatic_coaching_reference_eligible":true,"reason_codes":[]}}],
            "laps":[{"lap":1,"lap_time_s":LAP_TIME_VALUE,"complete":true,"flag_state":"green","green_fraction":1,"caution_fraction":0,"pit_time_s":0,"damage_repair_context":{"automatic_coaching_reference_eligible":true,"exclusion_reason_codes":[]}}],
            "lap_traces":{"traces":[]},"track_profile":{"detected_corner_segments":[]},
            "strategy":{"forecast":{"status":"available","green_fuel_gal_per_lap":0.6,"all_green_range_laps":20,"minimum_stops_all_green":1}},
            "damage_repair":{"status":"available"},"setup_telemetry":{},"data_quality":{"confidence":"high"}}}
            """.Replace("LAP_TIME_VALUE", lapTime.ToString(System.Globalization.CultureInfo.InvariantCulture), StringComparison.Ordinal);
        using var document = JsonDocument.Parse(json);
        return RuntimeMapper.Analysis(document.RootElement);
    }
}
