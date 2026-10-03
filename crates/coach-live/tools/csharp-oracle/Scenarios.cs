namespace Oracle;

public static class Scenarios
{
    private const long T0 = 1_700_000_000_000;
    private const string Tests = "companion-app/tests/iRacingCoach.Tests/";

    public static List<Scn> All()
    {
        var list = new List<Scn>();
        list.AddRange(Converted());
        for (var seed = 1; seed <= 8; seed++) list.Add(RaceSim(seed));
        return list;
    }

    private static List<Scn> Converted()
    {
        var all = new List<Scn>();

        all.Add(new Scn("critical_and_urgent_override", Tests + "CoordinatorTests.cs:LiveTelemetry_PrioritizesCriticalStateAndUrgentSafeGlanceOverride")
            .Step(T0, v => v.Lap(10).Green().Brake(0).Wheel(0).LatG(0))
            .Step(T0 + 100, v => v.Black().Brake(0.8).Wheel(0.5).LatG(1.8)).Last());

        all.Add(new Scn("rolling_gap_trend_and_caution", Tests + "CoordinatorTests.cs:LiveTelemetry_UsesRollingGapTrendAndSuppressesItUnderCaution")
            .Step(T0, v => v.Lap(20).Green().AheadGap(1.1).BehindGap(1.2).Brake(0).Wheel(0).LatG(0))
            .Step(T0 + 4000, v => v.AheadGap(0.7).BehindGap(1.8))
            .Step(T0 + 4100, v => v.Caution().AheadGap(0.5)).Last());

        {
            var s = new Scn("fuel_hard_limit_vs_window", Tests + "CoordinatorTests.cs:LiveTelemetry_SeparatesFuelHardLimitFromStrategicWindow");
            double[] fuel = [6, 5, 4, 3];
            for (var i = 0; i < 4; i++) { var f = fuel[i]; var lap = i + 1; s.Step(T0 + i * 1000, v => v.Lap(lap).Green().Fuel(f).LastLap(31).Brake(0)); }
            all.Add(s.Last());
        }

        {
            var s = new Scn("three_clean_laps_pace_band", Tests + "CoordinatorTests.cs:LiveTelemetry_RequiresThreeCleanLapsAndKeepsPaceSeparateFromRaceGap");
            double[] last = [31.1, 31.0, 30.9, 30.8, 30.8];
            double[] gap = [4.2, 4.3, 4.4, 4.5, 4.5];
            for (var i = 0; i < 5; i++) { var l = last[i]; var g = gap[i]; var lap = i + 1; s.Step(T0 + i * 1000, v => v.Lap(lap).Green().LastLap(l).LeaderLapAndGap(30.4, g), tiles: i >= 3); }
            all.Add(s);
        }

        all.Add(new Scn("safe_glance_delays_traffic_cue", Tests + "CoordinatorTests.cs:LiveTelemetry_SafeGlanceDelaysOrdinaryTrafficCueUntilStraight")
            .Step(T0, v => v.Lap(8).Green().AheadGap(1.2).Brake(0).Wheel(0).LatG(0))
            .Step(T0 + 4000, v => v.AheadGap(0.7).Brake(0.35).Wheel(0.4).LatG(1.5))
            .Step(T0 + 4200, v => v.AheadGap(0.68).Brake(0).Wheel(0).LatG(0.05)).Last());

        all.Add(new Scn("missing_dynamics_are_not_a_straight", Tests + "CoordinatorTests.cs:LiveTelemetry_MissingDynamicsCannotBeAssumedToBeAStraight")
            .Step(T0, v => v.Lap(8).Green().AheadGap(1.2).Brake(0).Wheel(0).LatG(0))
            .Step(T0 + 4000, v => v.AheadGap(0.7).Set("Brake", float.NaN).Set("SteeringWheelAngle", float.NaN).Set("LatAccel", float.NaN))
            .Step(T0 + 4200, v => v.AheadGap(0.68).Brake(0).Wheel(0).LatG(0.05)).Last());

        {
            var s = new Scn("fully_observed_clean_laps_only", Tests + "CoordinatorTests.cs:LiveTelemetry_UsesOnlyFullyObservedCleanLapsForPaceAndFuelEvidence");
            (int lap, int sec, double fuel, bool caution)[] steps = [(10, 0, 10, false), (11, 1, 9, false), (12, 2, 8, false), (12, 3, 7.8, true), (13, 4, 7, false), (14, 5, 6, false), (15, 6, 5, false)];
            foreach (var (lap, sec, fuel, caution) in steps)
                s.Step(T0 + sec * 1000L, v => { v.Lap(lap).Fuel(fuel).LastLap(30).Brake(0).Wheel(0).LatG(0); if (caution) v.Caution(); else v.Green(); });
            all.Add(s.Last());
        }

        {
            var s = new Scn("run_phase_resets_on_pit_road", Tests + "CoordinatorTests.cs:LiveTelemetry_CurrentRunPhaseResetsWhenPitRoadIsObserved");
            for (var lap = 1; lap <= 7; lap++) { var l = lap; s.Step(T0 + lap * 1000L, v => v.Lap(l).Green().LastLap(30).Fuel(20 - l).Pit(false).Brake(0).Wheel(0).LatG(0)); }
            s.Step(T0 + 7000 + 1, v => v.Lap(7).Pit(true));
            all.Add(s.Last());
        }

        {
            var s = new Scn("run_phase_counts_clean_green_laps", Tests + "CoordinatorTests.cs:LiveTelemetry_RunPhaseCountsOnlyFullyObservedCleanGreenLaps");
            (int lap, int sec, bool repair, bool caution)[] steps = [(1, 0, false, false), (2, 1, false, false), (2, 2, true, false), (3, 3, false, false), (3, 4, false, true), (4, 5, false, false), (5, 6, false, false)];
            foreach (var (lap, sec, repair, caution) in steps)
                s.Step(T0 + sec * 1000L, v => { v.Lap(lap).LastLap(30).Brake(0).Wheel(0).LatG(0); if (repair) v.Repair(); else if (caution) v.Caution(); else v.Green(); });
            all.Add(s.Last());
        }

        {
            var s = new Scn("driver_input_cue_needs_baseline_and_persistence", Tests + "CoordinatorTests.cs:LiveTelemetry_DriverInputCueRequiresCleanPersonalBaselineAndPersistence");
            double[] brakes = [0.5, 0.5, 0.5, 0.5, 0.8, 0.8, 0.8, 0.8];
            for (var i = 0; i < brakes.Length; i++)
            {
                var n = i + 1; var b = brakes[i];
                s.Step(T0 + n * 1000L, v => v.Lap(n).Green().Remaining(20 - n).AheadGap(3).BehindGap(3).LastLap(30).LeaderLastLap(30).Dist(0.25).Brake(b).Wheel(0).LatG(0));
            }
            all.Add(s.Last());
        }

        {
            var s = new Scn("repair_confounded_laps_make_no_driver_cue", Tests + "CoordinatorTests.cs:LiveTelemetry_RepairConfoundedLapsCannotCreateDriverInputCue");
            (double brake, bool repair)[] steps = [(0.5, false), (0.5, false), (0.5, false), (0.8, true), (0.8, true), (0.8, true), (0.8, true)];
            for (var i = 0; i < steps.Length; i++)
            {
                var n = i + 1; var (b, r) = steps[i];
                s.Step(T0 + n * 1000L, v => { v.Lap(n).Remaining(20 - n).AheadGap(3).BehindGap(3).LastLap(30).LeaderLastLap(30).Dist(0.25).Brake(b).Wheel(0).LatG(0); if (r) v.Repair(); else v.Green(); });
            }
            all.Add(s.Last());
        }

        {
            var s = new Scn("disconnect_clears_baselines", Tests + "CoordinatorTests.cs:LiveTelemetry_DisconnectClearsSessionSpecificPaceAndInputBaselines");
            for (var lap = 1; lap <= 5; lap++) { var l = lap; s.Step(T0 + (lap - 1) * 1000L, v => v.Lap(l).LastLap(30).Green()); }
            s.Step(T0 + 5000, connected: false);
            s.Step(T0 + 6000, v => v.Lap(1).LastLap(35).Green());
            all.Add(s.Last());
        }

        {
            var s = new Scn("session_epoch_boundaries", Tests + "CoordinatorTests.cs:LiveTelemetryService_SessionEpochAdvancesOnlyAtStreamBoundaries");
            s.Step(T0, v => v.Lap(2), connected: false);
            s.Step(T0 + 100, v => v.Lap(2).Green().LastLap(30).Fuel(10));
            for (var lap = 3; lap <= 6; lap++) { var l = lap; s.Step(T0 + 100 + (lap - 2) * 1000L, v => v.Lap(l).LastLap(30).Fuel(10 - l * 0.8)); }
            s.Step(T0 + 6000, v => v.Lap(1));          // lap counter reset: a new session, evidence cleared
            s.Step(T0 + 7000, connected: false);
            s.Step(T0 + 8000, v => v.Lap(1));          // reconnect: another epoch
            s.Step(T0 + 9000, v => v.Lap(2)).Last();
            all.Add(s);
        }

        {
            var s = new Scn("laps_remaining_sentinels", Tests + "LiveMonitorTests.cs:LapsRemaining_TreatsTheIRacingUnlimitedSentinelAsUnknown");
            double[] values = [32767, 32768, 50000, -1, double.NaN, 0, 4.2, 43, 9999.5, 10000, 1];
            for (var i = 0; i < values.Length; i++) { var x = values[i]; s.Step(T0 + i * 100L, v => v.Lap(3).Remaining(x)); }
            all.Add(s.Last());
        }

        {
            var s = new Scn("laps_remaining_falls_back_to_the_plain_count", "IRacingSdkTelemetrySource.cs:ReadDouble(Ex) ?? ReadDouble(plain)");
            s.Omit.Add("SessionLapsRemainEx");
            s.Step(T0, v => v.Lap(3).Set("SessionLapsRemain", 12));
            s.Step(T0 + 100, v => v.Set("SessionLapsRemain", 32767)).Last();
            all.Add(s);
        }

        {
            var s = new Scn("catalog_ranges_and_stepped_metrics", Tests + "LiveMonitorTests.cs:TelemetryCatalog_AppliesSemanticRangesToBarCapableReadings, TelemetryCatalog_BooleanAndCategoricalMetricsKeepTruthfulSteppedHistory");
            s.Step(T0, v => v.Lap(4).Green().Fuel(8).FuelPct(0.4).Pit(false));
            s.Step(T0 + 500, v => v.Caution().Pit(true).Fuel(10).FuelPct(0.5));
            s.Step(T0 + 1000, v => v.Caution().Pit(true).Fuel(10).FuelPct(0.5));
            all.Add(s.Last());
        }

        {
            var s = new Scn("road_wheel_steering_from_the_setup_ratio", Tests + "SteeringBasisTests.cs:LiveRatioComesFromTheCarSetupBlockOnly, LiveRoadWheelSteeringIsTheWheelAngleOverTheRatio, LiveTilesShowRoadWheelSteeringOrSayTheRatioIsMissing, EngineCarriesTheLiveRatioIntoTheSnapshot");
            var wheel = 71.53 * Math.PI / 180;
            s.Step(T0, v => v.Lap(3).Green().Wheel(0.6));
            s.Step(T0 + 100, v => v.Wheel(wheel).LatG(0));
            s.Step(T0 + 200, session: Session.Default with { Ratio = "10:1" });
            s.Step(T0 + 300, session: Session.Default with { Ratio = null });
            s.Step(T0 + 400, session: Session.Default with { Ratio = "12:2" });
            s.Step(T0 + 500, session: Session.Default with { Ratio = "16.5:1" });
            s.Step(T0 + 600, v => v.Wheel(0.11).Brake(0).LatG(0).Throttle(0.2));
            s.Step(T0 + 700, session: Session.Default with { Ratio = "10:1" });
            all.Add(s.Last());
        }

        {
            var s = new Scn("epoch_on_identity_and_phase_changes", "LiveTelemetry.cs:ObserveSessionBoundary");
            s.Step(T0, v => v.Lap(5).Green());
            s.Step(T0 + 100, v => v.Lap(6));
            s.Step(T0 + 200, v => v.Lap(6).Set("SessionNum", 1));                       // practice -> race
            s.Step(T0 + 300, v => v.Lap(7).Set("SessionUniqueID", 123457));
            s.Step(T0 + 400, v => v.Lap(7), session: Session.Default with { SubSessionId = 8002 });
            s.Step(T0 + 500, v => v.Lap(7), session: Session.Default with { SubSessionId = 8002, Types = [(0, "Practice"), (1, "Lone Qualify")] });
            s.Step(T0 + 600, v => v.Lap(8));
            s.Step(T0 + 300, v => v.Lap(8));                                            // the clock went backwards
            s.Step(T0 + 400, v => v.Lap(8).Set("SessionNum", 0));
            s.Step(T0 + 500, v => v.Lap(8).Set("SessionNum", 7));                       // a session number with no type
            all.Add(s.Last());
        }

        {
            var s = new Scn("flags_missing", "LiveTruthPolicy.cs: unknown is not racing");
            s.Omit.Add("SessionFlags");
            s.Step(T0, v => v.Lap(3).AheadGap(1.0));
            s.Step(T0 + 4000, v => v.AheadGap(0.5));
            s.Step(T0 + 4100, v => v.AheadGap(0.4)).Last();
            all.Add(s);
        }

        {
            var s = new Scn("scoring_arrays_missing", "IRacingSdkTelemetrySource.cs: ArrayValue and SameLapGap");
            s.Omit.Add("CarIdxF2Time");
            s.Omit.Add("PlayerCarPosition");
            s.Omit.Add("PlayerCarClassPosition");
            s.Step(T0, v => v.Lap(3).Position(2).Class(2));
            s.Step(T0 + 100, v => v.Position(1).Class(1)).Last();
            all.Add(s);
        }

        {
            var s = new Scn("odd_class_positions", "IRacingSdkTelemetrySource.cs:81-82 behind search is unfiltered");
            s.Step(T0, v => v.Lap(3).Class(0).Position(4));
            s.Step(T0 + 100, v => v.Class(-1));
            s.Step(T0 + 200, v => v.Class(1).Position(1));
            s.Step(T0 + 300, v => v.Class(2).Position(300));
            s.Step(T0 + 400, v => v.Class(40).Position(40));
            all.Add(s.Last());
        }

        {
            var s = new Scn("pause_and_safe_glance_off", Tests + "CoordinatorTests.cs (engine Update paused/safeGlance parameters)");
            s.SafeGlance = false;
            s.Step(T0, v => v.Lap(8).Green().AheadGap(1.2).Brake(0).Wheel(0).LatG(0));
            s.Step(T0 + 4000, v => v.AheadGap(0.7).Brake(0.35).Wheel(0.4).LatG(1.5));
            s.Step(T0 + 4100, v => v.AheadGap(0.7), paused: true);
            s.Step(T0 + 4200, v => v.Tow(3), paused: true);
            s.Step(T0 + 4300, v => v.Tow(0), paused: false);
            s.Step(T0 + 4400, connected: false, paused: true);
            all.Add(s.Last());
        }

        {
            var s = new Scn("critical_cues_and_repair", "LiveTelemetry.cs:BuildCue order");
            s.Step(T0, v => v.Lap(3).Green());
            s.Step(T0 + 100, v => v.Tow(2.5));
            s.Step(T0 + 200, v => v.Tow(0).Repairing(12.2, 4));
            s.Step(T0 + 300, v => v.Repairing(0, 6));
            s.Step(T0 + 400, v => v.Repairing(0, 0).Set("OnPitRoad", true));
            s.Step(T0 + 500, v => v.Pit(false).Flags(0x10000 | 0x4));
            s.Step(T0 + 600, v => v.Flags(0x20000 | 0x4));
            s.Step(T0 + 700, v => v.Flags(0x1));
            s.Step(T0 + 800, v => v.Flags(0x2));
            s.Step(T0 + 900, v => v.Flags(0x10));
            s.Step(T0 + 1000, v => v.Flags(0x4000));
            s.Step(T0 + 1100, v => v.Flags(0x200));
            s.Step(T0 + 1200, v => v.Flags(0x80000000u));
            s.Step(T0 + 1300, v => v.Flags(0));
            all.Add(s.Last());
        }

        {
            var s = new Scn("fuel_cues", "LiveTelemetry.cs:BuildPit and BuildCue 4 and 5");
            double[] fuel = [9, 8, 7, 6, 5.2, 4.4, 3.6, 2.8, 2.0, 1.2];
            for (var i = 0; i < fuel.Length; i++) { var f = fuel[i]; var lap = i + 1; s.Step(T0 + i * 1000L, v => v.Lap(lap).Green().Fuel(f).LastLap(31).Brake(0)); }
            all.Add(s.Last());
        }

        {
            var s = new Scn("pace_outside_the_band", "LiveTelemetry.cs:BuildCue 10");
            for (var lap = 1; lap <= 6; lap++) { var l = lap; s.Step(T0 + lap * 1000L, v => v.Lap(l).Green().LastLap(30).AheadGap(3).BehindGap(3)); }
            s.Step(T0 + 7000, v => v.Lap(7).LastLap(31));
            s.Step(T0 + 7100, v => v.Brake(0.3).Wheel(0.4).LatG(1.2));
            s.Step(T0 + 8000, v => v.Lap(8).LastLap(30.4).Brake(0).Wheel(0).LatG(0));
            s.Step(T0 + 9000, v => v.Lap(9).LastLap(30.5));
            all.Add(s.Last());
        }

        {
            var s = new Scn("braking_overshoot_large_ratio", Tests + "CoordinatorTests.cs:LiveTelemetry_DriverInputCueRequiresCleanPersonalBaselineAndPersistence (P0 above 100%)");
            double[] brakes = [0.06, 0.06, 0.06, 0.06, 1.0, 1.0, 1.0, 1.0, 0.3, 0.06];
            for (var i = 0; i < brakes.Length; i++)
            {
                var n = i + 1; var b = brakes[i];
                s.Step(T0 + n * 1000L, v => v.Lap(n).Green().AheadGap(3).BehindGap(3).LastLap(30).LeaderLastLap(30).Dist(0.25).Brake(b).Wheel(0).LatG(0), tiles: n == 8);
            }
            all.Add(s.Last());
        }

        {
            var s = new Scn("paused_tile", "LiveTelemetry.cs: paused cue and tile");
            s.Step(T0, v => v.Lap(8).Green().AheadGap(1.2).Brake(0).Wheel(0).LatG(0));
            s.Step(T0 + 100, v => v.AheadGap(0.9), paused: true, tiles: true);
            s.Step(T0 + 200, connected: false, paused: true, tiles: true);
            all.Add(s);
        }

        return all;
    }

    private static Scn RaceSim(int seed)
    {
        var rng = new Random(seed * 7919);
        var s = new Scn($"race_sim_seed_{seed}", $"generated: seed {seed}");
        s.SafeGlance = seed % 4 != 0;
        if (seed == 3) s.Omit.Add("PlayerCarPosition");
        const int stepsPerLap = 8;
        const int dt = 400;
        var lap = 1;
        double fuel = 12;
        var lastLap = 0.0;
        double ahead = 1.4, behind = 2.0, leader = 12;
        var t = T0;
        var cautionLeft = 0;
        var pitLeft = 0;
        var repairLeft = 0;
        var towLeft = 0;
        var overshoot = 0;
        var pauseLeft = 0;
        var disconnectLeft = 0;
        var position = 6;
        var totalLaps = 14;
        var stepIndex = 0;
        for (var lapNo = 0; lapNo < totalLaps; lapNo++)
        {
            if (cautionLeft == 0 && rng.NextDouble() < 0.10) cautionLeft = 1 + rng.Next(2);
            if (pitLeft == 0 && rng.NextDouble() < 0.07) pitLeft = 1;
            if (repairLeft == 0 && rng.NextDouble() < 0.04) repairLeft = 2;
            if (towLeft == 0 && rng.NextDouble() < 0.02) towLeft = 1;
            if (overshoot == 0 && lapNo >= 5 && rng.NextDouble() < 0.35) overshoot = 4 + rng.Next(3);
            var lapCaution = cautionLeft > 0; if (lapCaution) cautionLeft--;
            var lapPit = pitLeft > 0; if (lapPit) pitLeft--;
            var lapRepair = repairLeft > 0; if (lapRepair) repairLeft--;
            var lapTow = towLeft > 0; if (lapTow) towLeft--;
            var lapOvershoot = overshoot > 0; if (lapOvershoot) overshoot--;
            var black = rng.NextDouble() < 0.02;
            if (rng.NextDouble() < 0.12) position = Math.Clamp(position + rng.Next(-1, 2), 1, 12);
            var baseLap = 31.0 + rng.NextDouble() * 0.4 + (lapCaution ? 6 : 0);
            lastLap = lapNo == 0 ? 0 : lastLap;
            var burn = 0.8 + rng.NextDouble() * 0.15;
            for (var step = 0; step < stepsPerLap; step++)
            {
                var dist = step / (double)stepsPerLap;
                var first = step == 0;
                if (first && lapNo > 0) lastLap = Math.Round(baseLap, 3);
                if (first && lapNo > 0) fuel = lapPit ? 12 : Math.Max(0.3, fuel - burn);
                ahead = Math.Clamp(ahead + (rng.NextDouble() - 0.52) * 0.25, 0.2, 3.5);
                behind = Math.Clamp(behind + (rng.NextDouble() - 0.5) * 0.25, 0.3, 4.0);
                leader = Math.Clamp(leader + (rng.NextDouble() - 0.5) * 0.4, 0.5, 30);
                var braking = step is 2 or 5;
                var zonePeak = step == 2 ? 0.55 : 0.70;
                var brake = braking ? zonePeak * (lapOvershoot && step == 2 ? 1.6 : 1.0) + (rng.NextDouble() - 0.5) * 0.05 : 0.0;
                var corner = braking || step == 3;
                var wheel = corner ? 0.5 + rng.NextDouble() * 0.3 : (rng.NextDouble() - 0.5) * 0.02;
                var latG = corner ? 1.0 + rng.NextDouble() * 0.6 : (rng.NextDouble() - 0.5) * 0.2;
                var curLap = lap;
                var isDisconnected = disconnectLeft > 0;
                var pause = pauseLeft > 0;
                var onPit = lapPit && step >= 3 && step <= 5;
                var hideAhead = rng.NextDouble() < 0.05;
                var hideBehind = rng.NextDouble() < 0.05;
                t += dt;
                var captured = step;
                s.Step(t, v =>
                {
                    v.Lap(curLap).Position(position).Class(position).AheadGap(hideAhead ? null : ahead).BehindGap(hideBehind ? null : behind)
                        .LeaderGap(leader).LeaderLastLap(30.3 + rng.NextDouble() * 0.2).LastLap(lastLap).Fuel(fuel).Dist(dist)
                        .Brake(brake).Throttle(braking ? 0 : 0.6 + rng.NextDouble() * 0.4).Wheel(wheel).LatG(latG).Pit(onPit)
                        .Set("Gear", 2 + rng.Next(4)).Set("RPM", (float)(4000 + rng.Next(3500))).Set("Speed", (float)(30 + rng.NextDouble() * 35))
                        .Set("TrackTempCrew", (float)(30 + lapNo * 0.2));
                    v.Repairing(lapRepair && step >= 2 ? 6.5 - step * 0.5 : 0, 0);
                    v.Tow(lapTow ? 4 : 0);
                    v.Set("PlayerCarMyIncidentCount", lapNo / 3);
                    v.Set("BrakeABSactive", braking && rng.NextDouble() < 0.4);
                    if (black) v.Black();
                    else if (lapCaution) v.Flags(rng.NextDouble() < 0.5 ? 0x8u : 0x4000u);
                    else v.Green();
                    v.Remaining(Math.Max(0, totalLaps - lapNo));
                }, connected: !isDisconnected, paused: pause, tiles: stepIndex % 27 == 0);
                stepIndex++;
                if (disconnectLeft > 0) disconnectLeft--;
                if (pauseLeft > 0) pauseLeft--;
                if (lapNo == 7 && step == 3 && seed % 2 == 1) disconnectLeft = 2;
                if (lapNo == 9 && step == 1 && seed % 3 == 0) pauseLeft = 6;
            }
            lap++;
            if (lapNo == 10 && seed % 2 == 0) lap = 1;     // a lap-counter reset (new session)
        }
        return s;
    }
}
