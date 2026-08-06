using System.Text.Json;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class SlipAngleMappingTests
{
    [TestMethod]
    public void Analysis_MapsRecordedSideslipAndPreservesUnavailableGaps()
    {
        using var response = JsonDocument.Parse("""
            {
              "analysis_id": "slip-angle-mapping",
              "analysis_view": {
                "lap_traces": {
                  "traces": [
                    {
                      "lap": 1,
                      "complete": true,
                      "flag_state": "green",
                      "points": [
                        { "lap_pct": 0.1, "slip_angle_deg": -3.25 },
                        { "lap_pct": 0.2, "slip_angle_deg": null }
                      ]
                    }
                  ]
                }
              }
            }
            """);

        var workspace = RuntimeMapper.Analysis(response.RootElement);
        var points = workspace.Traces.Single().Points;

        Assert.AreEqual(-3.25, points[0].SlipAngleDegrees);
        Assert.IsNull(points[1].SlipAngleDegrees);
    }
}
