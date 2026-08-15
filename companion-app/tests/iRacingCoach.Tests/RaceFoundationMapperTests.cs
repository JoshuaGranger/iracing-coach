using System.Text.Json;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class RaceFoundationMapperTests
{
    [TestMethod]
    public void Garage61References_MapsCompactProvenanceAndAlignmentWithoutPaths()
    {
        using var document = JsonDocument.Parse("""
        {
          "status":"complete",
          "garage61_representative_laps": {
            "target_derivation_version":"explicit-analysis-paths-v1",
            "status":"available",
            "comparison_scope":"own/team",
            "representative_laps":[{
              "comparison_role":"representative",
              "setup_type":"fixed",
              "lap":{"id":"42","lapTime":24.5,"canViewTelemetry":true,"driverName":"Driver"},
              "telemetry":{"status":"cached","path":"garage61/csv/private.csv","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
            }],
            "reference_comparisons":[{
              "lap_id":"42",
              "telemetry_path":"garage61/csv/private.csv",
              "quality":{"status":"usable","usable":true,"signals":["speed_mph","brake","throttle"],"aligned_bins":188,"coverage_fraction":0.94}
            }],
            "comparison_quality":{"status":"usable","setup_scope":"same_setup_only","usable_reference_laps":1,"median_coverage_fraction":0.94}
          },
          "cache":{"manifest":{"refreshed_at":"2026-08-07T20:15:00Z","source_hash":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}
        }
        """);

        var mapped = RuntimeMapper.Garage61References(document.RootElement);

        Assert.IsNotNull(mapped);
        Assert.AreEqual("Garage61", mapped.Provider);
        Assert.AreEqual(DateTimeOffset.Parse("2026-08-07T20:15:00Z"), mapped.RetrievedAt);
        Assert.AreEqual(64, mapped.SourceSha256?.Length);
        Assert.AreEqual("usable", mapped.ComparisonStatus);
        Assert.AreEqual("same_setup_only", mapped.SetupScope);
        Assert.AreEqual(1, mapped.UsableReferenceLaps);
        Assert.AreEqual(0.94d, mapped.MedianCoverageFraction);
        CollectionAssert.AreEquivalent(new[] { "brake", "speed_mph", "throttle" }, mapped.AvailableSignals?.ToArray());
        var lap = mapped.Laps.Single();
        Assert.AreEqual("usable", lap.AlignmentStatus);
        Assert.IsTrue(lap.AlignmentUsable);
        Assert.AreEqual(188, lap.AlignedBins);
        Assert.AreEqual(0.94d, lap.AlignmentCoverageFraction);
        Assert.AreEqual(64, lap.SourceSha256?.Length);
        Assert.IsFalse(lap.GetType().GetProperties().Any(property => property.Name.Contains("Path", StringComparison.OrdinalIgnoreCase)));
    }

    [TestMethod]
    public void Garage61References_OmitsLegacyUnversionedTargetDerivation()
    {
        using var document = JsonDocument.Parse("""
        {
          "garage61_representative_laps": {
            "status":"available",
            "representative_laps":[{"lap":{"id":"legacy","lapTime":24.5}}]
          }
        }
        """);

        Assert.IsNull(RuntimeMapper.Garage61References(document.RootElement));
    }

    [TestMethod]
    public void LiveReplayCaptureStore_WritesAtomicFinalizedChunksWithoutMixingSessions()
    {
        var root = Path.Combine(Path.GetTempPath(), $"iracing-coach-live-replay-{Guid.NewGuid():N}");
        try
        {
            var coverage = new[] { new LiveReplayChannelCoverage("CarIdxLapDistPct", true, null) };
            LiveReplayCaptureFrame frame(int index) => new(
                "session-a",
                DateTimeOffset.UtcNow.AddMilliseconds(index * 500),
                index / 2d,
                4,
                4,
                10,
                20,
                0,
                "Race",
                0,
                coverage,
                [new LiveReplayParticipant(0, "7", 1, "Class", "Car", "Driver", null, false)],
                [new LiveReplayCarSample(0, index / 21d, 1, 0, 1, 1, false, 3, 0, 25.1, 24.9)],
                index,
                2);
            using (var first = new LiveReplayCaptureStore(() => root))
            {
                for (var index = 0; index < 20; index++) first.Capture(frame(index));
                first.EndSession("disconnected");
            }
            using (var resumed = new LiveReplayCaptureStore(() => root))
            {
                resumed.Capture(frame(20));
                resumed.EndSession("disconnected");
            }

            var directory = Directory.GetDirectories(Path.Combine(root, "telemetry-traces", "live-replay")).Single();
            Assert.HasCount(2, Directory.GetFiles(directory, $"chunk-*{LiveReplayChunkCodec.FileExtension}"));
            using var manifest = JsonDocument.Parse(File.ReadAllText(Path.Combine(directory, "manifest.json")));
            Assert.AreEqual(2, manifest.RootElement.GetProperty("schemaVersion").GetInt32());
            Assert.AreEqual("iracing-coach-live-replay-v2-delta-gzip", manifest.RootElement.GetProperty("format").GetString());
            Assert.AreEqual("finalized", manifest.RootElement.GetProperty("status").GetString());
            Assert.AreEqual("disconnected", manifest.RootElement.GetProperty("finalizationReason").GetString());
            Assert.AreEqual(21, manifest.RootElement.GetProperty("frameCount").GetInt32());
            Assert.AreEqual(2, manifest.RootElement.GetProperty("chunks").GetArrayLength());
            Assert.AreEqual("Race", manifest.RootElement.GetProperty("sessionType").GetString());
            Assert.AreEqual(21, manifest.RootElement.GetProperty("captureMetrics").GetProperty("writtenFrameCount").GetInt32());
        }
        finally
        {
            if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
        }
    }

    [TestMethod]
    public void Analysis_MapsPortableGeometryReplayTireAndReferenceContracts()
    {
        using var document = JsonDocument.Parse("""
        {
          "analysis_id": "foundation-test",
          "analysis_view": {
            "analysis_profile_version":"post-race-foundations-v13",
            "schema_version": 1,
            "identity": { "track_name": "Iowa Speedway", "track_config": "Oval", "car_name": "NASCAR Truck", "is_fixed_setup": true },
            "race_summary": { "recorded_laps": 2, "scheduled_laps": 10, "pit_stops_detected": 0 },
            "lap_traces": { "traces": [], "additional_signal_catalog": [] },
            "track_profile": { "shape": [], "detected_corner_segments": [] },
            "strategy": { "forecast": {}, "limitations": [] },
            "damage_repair": { "summary": {}, "incident_points": { "events": [] }, "limitations": [] },
            "setup_telemetry": {},
            "data_quality": { "confidence": "high" },
            "race_grades": { "categories": [], "unavailable_categories": [] },
            "track_geometry": {
              "status": "usable", "track_configuration_key": "7-oval", "coordinate_system": "normalized_local_vector",
              "main_path": [{"x":0.1,"y":0.2,"lap_pct":0.0,"observations":3}],
              "pit_lane": [{"x":0.2,"y":0.3,"lap_pct":0.8,"observations":1}],
              "pit_entry_path": [], "pit_exit_path": [],
              "start_finish_line": {"a":{"x":0.1,"y":0.1},"b":{"x":0.1,"y":0.3}},
              "unavailable_reasons": [],
              "source_sha256": ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"],
              "contributing_source_sha256": ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],
               "observed_source_sha256": ["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"],
               "quality":{"main_path_points":500,"main_loop_complete":true,"lap_percent_coverage":0.992,"maximum_lap_percent_gap":0.008,"closure_distance":0.012},
               "transform":{"source_bounds":{"minimum_x":10,"maximum_x":20,"minimum_y":100,"maximum_y":108},"normalization_scale":10},
              "geometry_provenance":{
                "selected_observation_id":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
                "normalization_transform":{"source_bounds":{"minimum_x":10,"maximum_x":20,"minimum_y":100,"maximum_y":108},"normalization_scale":10},
                "observations":[{"observation_id":"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc","source_sha256":["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],"transform":{"source_bounds":{"minimum_x":10,"maximum_x":20,"minimum_y":100,"maximum_y":108},"normalization_scale":10},"geometry_fingerprint":"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd","quality":{"main_path_points":500}}]
              }
            },
            "race_replay": {
              "status": "usable", "unavailable_reasons": [], "limitations": [], "sample_rate_hz": 2,
              "player_car_index": 0, "interpolation": "linear",
              "representation":{"source_frame_count":3600,"display_frame_count":900,"source_sample_rate_hz":60,"display_sample_rate_hz":15,"frame_budget":10000,"decimated":true,"routine_interval_s":0.066667,"keyframes_preserved":true,"dropped_keyframe_count":0},
              "coverage": [{"channel":"CarIdxLapDistPct","status":"partial","recorded_segment_count":3,"segment_count":4,"recorded_fraction":0.75,"all_segments_recorded":false,"temporal_gap_count":1}],
              "temporal_coverage":{"status":"partial","recorded_frame_count":180,"expected_frame_count":200,"recorded_fraction":0.9,"gap_count":1,"largest_gap_s":4.5,"start_session_time_s":10,"end_session_time_s":110},
              "participant_coverage":[{"car_index":0,"status":"partial","recorded_frame_count":175,"total_frame_count":180,"recorded_fraction":0.9722,"recorded_segment_count":3,"segment_count":4,"first_session_time_s":10,"last_session_time_s":109.5}],
              "participants": [{"car_index":0,"car_number":"7","class_id":1,"driver_name":"Player","is_player":true,"is_spectator":false}],
              "car_columns":["car_index","lap_pct","lap","completed_laps","overall_position","class_position","on_pit_road","track_surface","pace_flags","last_lap_time_s","best_lap_time_s"],
              "frames": [{"session_time_s":10,"session_state":"racing","global_flags":4,"global_flag_labels":["green"],"gap_before":true,"player_telemetry":{"incidentPoints":2,"onPitRoad":false,"towing":false,"repairRequired":false,"speedMetersPerSecond":45.5,"throttle":0.8},"events":[{"kind":"incident_points","label":"Incident points changed","sourceChannel":"PlayerCarMyIncidentCount","delta":2}],"car_rows":[[0,0.25,1,null,2,null,false,3,0,24.7,24.5],[1,null,null,null,null,null,null,null,null,null,null]]}]
            },
            "tire_learning": {
              "context": {"family":"nascar_truck","track_id":7,"track_config":"Oval","setup_type":"fixed","tire_compound":0},
              "prediction": {"status":"predicted","evidence_class":"historical_local_prediction","confidence":"low","model_version":"nascar-tire-condition-load-match-v1","observation_set_fingerprint":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","total_observations":4,"eligible_observations":3,"excluded_observations":1,"matching_sessions":3,"effective_matched_observations":2.7,"median_feature_distance":0.8,"comparable_feature_count":4,"matched_features":["track_temp_c","air_temp_c","brake_energy","steering_work"],"exclusion_reasons":["1 observation lacked a confirmed fresh start"],"matching_scope":"exact model context","laps_remaining":30,"tires":{"RF":{"outer":{"remaining_percent":85,"low_percent":80,"high_percent":90,"wear_rate_percent_per_green_lap":1,"laps_remaining_to_zero":85}}}},
              "persistent_model": {"path":"tire-models/model.json","observation_count":4,"model_version":"nascar-tire-condition-load-match-v1","observation_set_fingerprint":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
            },
            "garage61_representative_laps": {
              "target_derivation_version":"explicit-analysis-paths-v1","status":"available","comparison_scope":"own/team","representative_laps":[{"comparison_role":"representative","lap":{"id":"42","lapTime":24.5,"canViewTelemetry":true,"driverName":"Driver"},"telemetry":{"status":"cached"}}]
            },
            "technical_insights": [{"key":"fuel","label":"Fuel","status":"available","rating":"safe","takeaway":"Range is supported.","metrics":[{"label":"Range","value":"30 laps","evidence_type":"derived","detail":"Green-lap range.","action":"Keep a reserve.","tone":"positive","group":"range"}],"evidence":["fuel level"],"unavailable_reasons":[]}]
          }
        }
        """);

        var mapped = RuntimeMapper.Analysis(document.RootElement);

        Assert.AreEqual("usable", mapped.VectorGeometry?.Status);
        Assert.AreEqual("7-oval", mapped.VectorGeometry?.TrackConfigurationKey);
        Assert.AreEqual("normalized_local_vector", mapped.VectorGeometry?.CoordinateSystem);
        Assert.AreEqual(1, mapped.VectorGeometry?.MainPath.Count);
        Assert.IsNotNull(mapped.VectorGeometry?.StartFinishLine);
        CollectionAssert.Contains(mapped.VectorGeometry?.SourceSha256.ToArray(), "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
        Assert.HasCount(1, mapped.VectorGeometry?.SourceSha256 ?? []);
        Assert.HasCount(2, mapped.VectorGeometry?.ObservedSourceSha256 ?? []);
        Assert.AreEqual("cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc", mapped.VectorGeometry?.GeometryProvenance?.SelectedObservationId);
        Assert.AreEqual(500d, mapped.VectorGeometry?.GeometryProvenance?.Observations.Single().Quality["main_path_points"]);
        Assert.IsNotNull(mapped.VectorGeometry?.Quality);
        Assert.IsTrue(mapped.VectorGeometry.Quality.MainLoopComplete.GetValueOrDefault());
        Assert.AreEqual(.992d, mapped.VectorGeometry?.Quality?.LapPercentCoverage);
        Assert.AreEqual(.008d, mapped.VectorGeometry?.Quality?.MaximumLapPercentGap);
        Assert.AreEqual(.012d, mapped.VectorGeometry?.Quality?.ClosureDistance);
        var geometryTransform = mapped.VectorGeometry?.Transform;
        Assert.IsNotNull(geometryTransform);
        Assert.IsTrue(geometryTransform.TryNormalize(12, 106, out var normalizedX, out var normalizedY));
        Assert.AreEqual(.2d, normalizedX, 0.000001);
        Assert.AreEqual(.2d, normalizedY, 0.000001);
        Assert.AreEqual("usable", mapped.Replay?.Status);
        Assert.AreEqual(.9d, mapped.Replay?.TemporalCoverage?.RecordedFraction);
        Assert.AreEqual(1, mapped.Replay?.TemporalCoverage?.GapCount);
        Assert.AreEqual(.9722d, mapped.Replay?.ParticipantCoverage?.Single().RecordedFraction);
        Assert.AreEqual(3, mapped.Replay?.Coverage.Single().RecordedSegmentCount);
        Assert.IsFalse(mapped.Replay?.Coverage.Single().AllSegmentsRecorded);
        Assert.AreEqual(2, mapped.Replay?.Frames[0].Cars[0].OverallPosition);
        Assert.AreEqual(24.7d, mapped.Replay?.Frames[0].Cars[0].LastLapSeconds);
        Assert.AreEqual(24.5d, mapped.Replay?.Frames[0].Cars[0].BestLapSeconds);
        Assert.IsNull(mapped.Replay?.Frames[0].Cars[1].LapPercent, "A missing car position must not be mapped to the start/finish line.");
        Assert.IsTrue(mapped.Replay!.Frames[0].GapBefore);
        Assert.AreEqual(2, mapped.Replay?.Frames[0].PlayerTelemetry?.IncidentPoints);
        Assert.AreEqual(45.5d, mapped.Replay?.Frames[0].PlayerTelemetry?.SpeedMetersPerSecond);
        Assert.AreEqual("incident_points", mapped.Replay?.Frames[0].Events?.Single().Kind);
        Assert.AreEqual("PlayerCarMyIncidentCount", mapped.Replay?.Frames[0].Events?.Single().SourceChannel);
        Assert.AreEqual(3_600, mapped.Replay?.Representation?.SourceFrameCount);
        Assert.AreEqual(900, mapped.Replay?.Representation?.DisplayFrameCount);
        Assert.AreEqual(15d, mapped.Replay?.Representation?.DisplaySampleRateHz);
        Assert.IsTrue(mapped.Replay!.Representation!.KeyframesPreserved!.Value);
        Assert.AreEqual(0, mapped.Replay.Representation.DroppedKeyframeCount);
        var replayCarProperties = mapped.Replay!.Frames[0].Cars[0].GetType().GetProperties().Select(property => property.Name).ToArray();
        foreach (var forbidden in new[] { "Fuel", "Throttle", "Brake", "Steering", "Setup", "TireWear", "TireTemperature" })
            CollectionAssert.DoesNotContain(replayCarProperties, forbidden, $"Replay must not invent competitor {forbidden} data.");
        Assert.AreEqual("predicted", mapped.TirePrediction?.Status);
        Assert.AreEqual("low", mapped.TirePrediction?.Confidence);
        Assert.AreEqual(85d, mapped.TirePrediction?.Tires.Single().Bands["outer"].RemainingPercent);
        Assert.AreEqual("nascar-tire-condition-load-match-v1", mapped.TirePrediction?.ModelVersion);
        Assert.AreEqual(64, mapped.TirePrediction?.ObservationSetFingerprint?.Length);
        Assert.AreEqual(2.7d, mapped.TirePrediction?.EffectiveMatchedObservations);
        Assert.AreEqual(4, mapped.TirePrediction?.ComparableFeatureCount);
        CollectionAssert.Contains(mapped.TirePrediction?.MatchedFeatures.ToArray(), "track_temp_c");
        Assert.AreEqual("Oval", mapped.TirePrediction?.MatchingContext["track_config"]);
        Assert.AreEqual("own/team", mapped.Garage61References?.ComparisonScope);
        Assert.AreEqual("42", mapped.Garage61References?.Laps.Single().Id);
        Assert.AreEqual("fuel", mapped.TechnicalInsights?.Single().Key);
        var technicalMetric = mapped.TechnicalInsights?.Single().Metrics.Single();
        Assert.AreEqual("Green-lap range.", technicalMetric?.Detail);
        Assert.AreEqual("Keep a reserve.", technicalMetric?.Action);
        Assert.AreEqual("positive", technicalMetric?.Tone);
        Assert.AreEqual("range", technicalMetric?.Group);
    }

    [TestMethod]
    public void GeometryTransform_PreservesFullRaceMapCoordinatesForNarrowTraceSubset()
    {
        var transform = new iRacingCoach.Contracts.AnalysisGeometryTransform(
            new iRacingCoach.Contracts.AnalysisGeometrySourceBounds(10, 20, 100, 108),
            10);

        // This trace only covers x=12..16 and y=102..106. Re-normalizing the
        // subset would incorrectly stretch it to the whole cached track.
        Assert.IsTrue(transform.TryNormalize(12, 106, out var firstX, out var firstY));
        Assert.IsTrue(transform.TryNormalize(16, 102, out var lastX, out var lastY));

        Assert.AreEqual(.2d, firstX, .000001);
        Assert.AreEqual(.2d, firstY, .000001);
        Assert.AreEqual(.6d, lastX, .000001);
        Assert.AreEqual(.6d, lastY, .000001);
        Assert.AreNotEqual(0d, firstX, "The narrow current trace must not be normalized as a new full-track observation.");
        Assert.AreNotEqual(1d, lastX, "The narrow current trace must remain in the cached full-race coordinate frame.");
    }

    [TestMethod]
    public void Analysis_MapsExplicitReplayUnavailableReasonsWithoutInventingFrames()
    {
        using var document = JsonDocument.Parse("""
        {
          "analysis_id": "replay-gap",
          "analysis_view": {
            "schema_version": 1,
            "identity": {}, "race_summary": {}, "lap_traces": {}, "track_profile": {},
            "strategy": {"forecast":{}}, "damage_repair": {"summary":{},"incident_points":{}},
            "setup_telemetry": {}, "data_quality": {}, "race_grades": {},
            "race_replay": {"status":"unavailable","unavailable_reasons":["CarIdxLapDistPct is required to place competitors on the track."],"limitations":[],"coverage":[],"participants":[],"frames":[]}
          }
        }
        """);

        var mapped = RuntimeMapper.Analysis(document.RootElement);

        Assert.AreEqual("unavailable", mapped.Replay?.Status);
        Assert.IsEmpty(mapped.Replay?.Frames ?? []);
        StringAssert.Contains(mapped.Replay?.UnavailableReasons.Single(), "CarIdxLapDistPct");
        Assert.IsEmpty(mapped.Replay?.Participants ?? []);
    }
}
