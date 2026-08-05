using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class CalculatedTireWearTests
{
    [TestMethod]
    public void Build_AllocatesMeasuredRunWearByIntegratedStressAndCalibratesLaterRuns()
    {
        var traces = new[]
        {
            Trace(1, 1d),
            Trace(2, 3d),
            Trace(3, 2d)
        };
        var measuredRun = Run(1, [1, 2], new AnalysisPitStop(
            ServiceSeconds: 12,
            FuelAddedGallons: null,
            EstimatedFuelLapsRemaining: null,
            TiresChanged: ["LF", "RF", "LR", "RR"],
            LeftFrontTireWearPercent: 4,
            RightFrontTireWearPercent: 8,
            LeftRearTireWearPercent: 3,
            RightRearTireWearPercent: 5));
        var finalRun = Run(2, [3], null);

        var result = CalculatedTireWear.Build(traces, [measuredRun, finalRun]);

        Assert.HasCount(3, result);
        Assert.AreEqual(2d, result[1][^1]!.Value, .000001);
        Assert.AreEqual(6d, result[2][^1]!.Value, .000001);
        Assert.AreEqual(4d, result[3][^1]!.Value, .000001);
        Assert.AreEqual(1d, result[1][1]!.Value, .000001);
        Assert.AreEqual(0d, result[1][0]!.Value, .000001);
    }

    [TestMethod]
    public void Build_RequiresARecordedTireEndpointForCalibration()
    {
        var result = CalculatedTireWear.Build([Trace(1, 2d)], [Run(1, [1], null)]);

        Assert.IsEmpty(result);
    }

    private static AnalysisLapTrace Trace(int lap, double stress) => new(
        Lap: lap,
        LapTimeSeconds: 30,
        Complete: true,
        FlagState: "green",
        GreenFraction: 1,
        CautionFraction: 0,
        PitTimeSeconds: 0,
        Points:
        [
            Point(0, stress),
            Point(.5, stress),
            Point(1, stress)
        ]);

    private static AnalysisTracePoint Point(double percent, double stress) => new(
        LapPercent: percent,
        SessionTimeSeconds: percent * 30,
        SpeedMph: 100,
        SpeedMinimumMph: 100,
        SpeedMaximumMph: 100,
        Throttle: .5,
        ThrottleMinimum: .5,
        Brake: 0,
        BrakeMean: 0,
        SteeringRadians: 0,
        SteeringPeakRadians: 0,
        SlipAngleDegrees: 0,
        Gear: 4,
        Rpm: 6000,
        YawRateDegreesPerSecond: 0,
        LateralG: 0,
        LongitudinalG: 0,
        Latitude: null,
        Longitude: null,
        TireStressProxy: stress);

    private static AnalysisRun Run(int number, IReadOnlyList<int> laps, AnalysisPitStop? pitStop) => new(
        Number: number,
        Laps: laps,
        GreenLaps: laps.Count,
        CautionLaps: 0,
        FuelUsedGallons: null,
        PaceSlopeSecondsPerLap: null,
        TireEndpoint: pitStop is null ? "Tire reading unavailable" : "Recorded",
        ComparisonEligible: true,
        Status: "Recorded run",
        EarlyAverageLapSeconds: null,
        LateAverageLapSeconds: null,
        EarlyToLateDeltaSeconds: null,
        TireRemainingPercent: pitStop is null ? null : 92,
        TireName: pitStop is null ? string.Empty : "RF",
        EarlyBrakeVsLatePercent: null,
        EarlySteerVsLatePercent: null,
        PitStop: pitStop);
}
