using System.Text.Json;
using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class ProgressiveTuningCoordinatorTests
{
    [TestMethod]
    public void Candidate_AllowsFixedRaceAsEvidenceButNeverAsGarageTarget()
    {
        var race = Race("fixed", "Fixed", @"C:\coach\data\reports\fixed\analysis.json");
        var identity = Identity(race, "Fixed", embedded: true, fingerprint: "fixed-fingerprint");

        var candidate = ProgressiveTuningCoordinator.Candidate(race, Workspace(identity));

        Assert.IsTrue(candidate.Eligibility.CanUseAsEvidence);
        Assert.IsFalse(candidate.Eligibility.CanReceiveGarageRecommendation);
        Assert.IsNull(ProgressiveTuningCoordinator.OpenTarget(candidate));
        StringAssert.Contains(string.Join(" ", candidate.Eligibility.Blockers), "fixed-setup");
    }

    [TestMethod]
    public void Candidate_RequiresExactOpenEmbeddedSetupFingerprintForGarageTarget()
    {
        var race = Race("open", "Open", @"C:\coach\data\reports\open\analysis.json");
        var missingSetup = ProgressiveTuningCoordinator.Candidate(
            race,
            Workspace(Identity(race, "Open", embedded: false, fingerprint: string.Empty)));
        var eligible = ProgressiveTuningCoordinator.Candidate(
            race,
            Workspace(Identity(race, "Open", embedded: true, fingerprint: "open-fingerprint")));

        Assert.IsFalse(missingSetup.Eligibility.CanReceiveGarageRecommendation);
        Assert.IsTrue(eligible.Eligibility.CanReceiveGarageRecommendation);
        Assert.IsNotNull(ProgressiveTuningCoordinator.OpenTarget(eligible));
    }

    [TestMethod]
    public void ExactIdentity_RejectsSimilarRaceWithDifferentAnalysisPath()
    {
        var race = Race("race-a", "Open", @"C:\coach\data\reports\a\analysis.json");
        var identity = Identity(race, "Open", embedded: true, fingerprint: "fingerprint") with
        {
            AnalysisPath = @"C:\coach\data\reports\b\analysis.json"
        };

        Assert.IsFalse(ProgressiveTuningCoordinator.Matches(race, identity));
    }

    [TestMethod]
    public void OpenTargetCompatibility_RequiresExactCarAndTrackConfiguration()
    {
        var evidence = Identity(Race("evidence", "Fixed", "evidence.json"), "Fixed", true, "fixed");
        var target = Identity(Race("target", "Open", "target.json"), "Open", true, "open");

        Assert.IsTrue(ProgressiveTuningCoordinator.CompatibleOpenTarget(evidence, target));
        Assert.IsFalse(ProgressiveTuningCoordinator.CompatibleOpenTarget(evidence, target with { TrackConfigurationKey = "iowa-road" }));
        Assert.IsFalse(ProgressiveTuningCoordinator.CompatibleOpenTarget(evidence, target with { CarPath = "different-car", CarId = "different" }));
        Assert.IsFalse(ProgressiveTuningCoordinator.CompatibleOpenTarget(evidence, target with { SetupFingerprint = string.Empty }));
    }

    [TestMethod]
    public async Task DraftStore_AtomicallyPreservesNotesOutsideBackendArchive()
    {
        var home = Path.Combine(Path.GetTempPath(), $"iracing-coach-tuning-{Guid.NewGuid():N}");
        try
        {
            var race = Race("draft", "Open", Path.Combine(home, "data", "reports", "draft", "analysis.json"));
            var identity = Identity(race, "Open", true, "baseline");
            var feedback = new ProgressiveTuningFeedback
            {
                FeedbackId = "feedback-1",
                CornerId = "turn-1",
                CornerLabel = "Turn 1",
                RunPhase = "late",
                CornerPhases = ["entry", "center"],
                SymptomId = "tight",
                Note = "Builds after ten green laps",
                Priority = 3
            };
            var draft = new ProgressiveTuningDraft
            {
                RepresentativeSession = identity,
                GeneralNote = "Rear grip was stable.",
                Feedback = [feedback]
            };
            var store = new PortableTuningDraftStore(home);

            await store.SaveAsync(draft);
            var loaded = store.Load(identity);

            Assert.IsNotNull(loaded);
            Assert.AreEqual("Rear grip was stable.", loaded.GeneralNote);
            Assert.AreEqual("Builds after ten green laps", loaded.Feedback.Single().Note);
            Assert.AreEqual(3, loaded.Feedback.Single().Priority);
            StringAssert.Contains(store.PathFor(identity), Path.Combine("portable-settings", "tuning-drafts"));
            Assert.IsFalse(store.PathFor(identity).Contains(Path.Combine("data", "tuning"), StringComparison.OrdinalIgnoreCase));
            Assert.IsEmpty(Directory.GetFiles(Path.GetDirectoryName(store.PathFor(identity))!, "*.tmp"));
        }
        finally
        {
            if (Directory.Exists(home)) Directory.Delete(home, recursive: true);
        }
    }

    [TestMethod]
    public async Task TurnAnnotations_ApplyOnlyToTheExactCurrentGeometry()
    {
        var home = Path.Combine(Path.GetTempPath(), $"iracing-coach-turns-{Guid.NewGuid():N}");
        try
        {
            var map = Map() with
            {
                Turns = [Map().Turns.Single() with { IsOfficial = true }]
            };
            var currentHash = ProgressiveTuningCoordinator.GeometryHash(map);
            var correction = new TuningTurnCorrectionDraft
            {
                CornerId = "turn-1",
                Label = "Turn One",
                StartPct = .15,
                ApexPct = .20,
                EndPct = .25,
                MapIdentity = map.MapIdentity,
                GeometryHash = currentHash
            };
            var store = new PortableTuningTurnAnnotationStore(home);
            await store.SaveAsync(new TuningTurnAnnotationSet
            {
                TrackConfigurationKey = "iowa-oval",
                MapIdentity = map.MapIdentity,
                Corrections = [correction]
            });

            var merged = PortableTuningTurnAnnotationStore.Merge(map, store.Load("iowa-oval", map.MapIdentity));
            var changedGeometry = map with
            {
                Path = [.. map.Path, new TuningMapPoint(.75, .4, .8)],
                GeometryHash = new string('b', 64)
            };
            var staleMerge = PortableTuningTurnAnnotationStore.Merge(changedGeometry, store.Load("iowa-oval", map.MapIdentity));

            Assert.AreEqual("Turn One", merged.Turns.Single().Label);
            Assert.IsTrue(merged.Turns.Single().UserVerified);
            Assert.IsFalse(merged.Turns.Single().IsOfficial, "A corrected label must not retain official-label provenance.");
            Assert.AreEqual("Turn 1", staleMerge.Turns.Single().Label);
            Assert.IsFalse(staleMerge.Turns.Single().UserVerified);
            StringAssert.Contains(staleMerge.VerificationMessage ?? string.Empty, "older geometry");
        }
        finally
        {
            if (Directory.Exists(home)) Directory.Delete(home, recursive: true);
        }
    }

    [TestMethod]
    public void MapSubmission_IsBoundToNormalizedGeometryAndAnnotations()
    {
        var race = Race("map", "Open", "map-analysis.json");
        var identity = Identity(race, "Open", true, "fingerprint");
        var map = Map();
        var correction = new TuningTurnCorrectionDraft
        {
            CornerId = "turn-1",
            Label = "Turn One",
            StartPct = .15,
            ApexPct = .20,
            EndPct = .25,
            GeometryHash = ProgressiveTuningCoordinator.GeometryHash(map),
            MapIdentity = map.MapIdentity
        };

        var payload = ProgressiveTuningCoordinator.BuildMapSubmission(identity, map, [correction]);

        Assert.AreEqual("iowa-oval", payload.TrackConfigurationKey);
        Assert.AreEqual(64, payload.GeometryHash.Length);
        Assert.AreEqual(64, payload.AnnotationHash.Length);
        Assert.AreEqual(.20d, payload.Corners.Single().ApexPct);
    }

    [TestMethod]
    public void AnnotationHash_MatchesBackendCanonicalFixture()
    {
        var corners = new[]
        {
            new TuningMapCornerIdentity("turn-3", "Turn 3", .55, .65, .75, true, false),
            new TuningMapCornerIdentity("turn-1", "Turn 1", .05, .15, .25, true, false)
        };

        var hash = ProgressiveTuningCoordinator.MapAnnotationHash(
            "95-oval", new string('a', 64), "iracing-official", corners);

        Assert.AreEqual("e15b85a5711571f5a7248007b0c5392217469cd53a2dadd403a9031ccec73d5b", hash);
    }

    [TestMethod]
    public void AiSelection_IsRestrictedToDeterministicCandidatesAndEvidence()
    {
        var result = StructuredResult();
        var valid = ProgressiveTuningCoordinator.ValidateAiSelection(result,
            new TuningAiSelection("candidate-1", "Try one step.", ["evidence-1"], [], ["Direct evidence"]));
        var invalid = ProgressiveTuningCoordinator.ValidateAiSelection(result,
            new TuningAiSelection("invented", "Invented.", ["unknown"], [], []));

        Assert.IsTrue(valid.Valid);
        Assert.IsFalse(invalid.Valid);
        Assert.IsGreaterThanOrEqualTo(2, invalid.Errors.Count);
    }

    [TestMethod]
    public void AiSelection_CannotBorrowEvidenceFromAnotherAllowedCandidate()
    {
        var original = StructuredResult();
        var result = original with
        {
            Evidence = original.Evidence.Concat(
                [new TuningEvidenceView("evidence-2", EvidenceKind.Measured, "Other", "1", "", "Recorded", "")])
                .ToArray(),
            CandidateWhitelist = original.CandidateWhitelist.Concat(
                [new TuningCandidateChangeView("candidate-2", "Other", "Other change", "Other effect", "Other risk", [], "Rule", "High", ["evidence-2"], [])])
                .ToArray()
        };

        var validation = ProgressiveTuningCoordinator.ValidateAiSelection(
            result,
            new TuningAiSelection("candidate-1", "Borrow unrelated evidence.", ["evidence-2"], [], ["Known, but unrelated."]));

        Assert.IsFalse(validation.Valid);
        Assert.IsTrue(validation.Errors.Any(error => error.Contains("selected candidate", StringComparison.OrdinalIgnoreCase)));
    }

    [TestMethod]
    public void AiSelection_RoundTripsStrictSnakeCaseContract()
    {
        var original = new TuningAiSelection(
            "candidate-1", "Try one change.", ["evidence-1"], ["No conflict"], ["Exact setup"]);

        var json = JsonSerializer.Serialize(original);
        var roundTrip = JsonSerializer.Deserialize<TuningAiSelection>(json);

        StringAssert.Contains(json, "\"selected_candidate_id\"");
        StringAssert.Contains(json, "\"confidence_reasons\"");
        Assert.IsNotNull(roundTrip);
        Assert.AreEqual(original.SelectedCandidateId, roundTrip.SelectedCandidateId);
        Assert.AreEqual(original.Summary, roundTrip.Summary);
        CollectionAssert.AreEqual(original.EvidenceIds.ToArray(), roundTrip.EvidenceIds.ToArray());
        CollectionAssert.AreEqual(original.Conflicts.ToArray(), roundTrip.Conflicts.ToArray());
        CollectionAssert.AreEqual(original.ConfidenceReasons.ToArray(), roundTrip.ConfidenceReasons.ToArray());
    }

    [TestMethod]
    public void RuntimeMapper_MapsExactTuningIdentityAndNormalizedTurnBounds()
    {
        using var document = JsonDocument.Parse("""
        {
          "analysis_id":"analysis-1",
          "analysis_path":"C:/coach/data/reports/analysis-1/analysis.json",
          "selection":{"selector":"subsession:42:0","sim_session_type":"Race","subsession_id":42},
          "analysis_view":{
            "schema_version":1,
            "identity":{"track_id":7,"track_name":"Iowa Speedway","track_config":"Oval","car_id":10,"car_path":"stockcars2/supra2019","car_name":"Toyota Supra","event_type":"Race","is_fixed_setup":false,"setup_fingerprint":"abc123","setup_parameter_count":2,"setup":{"Tires":{"LF":1}}},
            "source":{"fingerprints":[{"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]},
            "race_summary":{},"laps":[],"runs":[],
            "lap_traces":{"traces":[]},
            "track_profile":{"shape":[],"detected_corner_segments":[{"segment":1,"label":"Turn 1","start_pct":0.9,"end_pct":0.1,"wraps_start_finish":true}]},
            "track_geometry":{"status":"usable","track_configuration_key":"iowa-oval","main_path":[{"lap_pct":0.0,"x":0,"y":0},{"lap_pct":0.5,"x":1,"y":1}],"source_sha256":[]},
            "strategy":{"forecast":{},"limitations":[]},"damage_repair":{"summary":{},"incident_points":{"events":[]},"limitations":[]},"setup_telemetry":{},"data_quality":{},"race_grades":{"categories":[],"unavailable_categories":[]}
          }
        }
        """);

        var mapped = RuntimeMapper.Analysis(document.RootElement);

        Assert.AreEqual("iowa-oval", mapped.TuningIdentity?.TrackConfigurationKey);
        Assert.AreEqual("Open", mapped.TuningIdentity?.SetupType);
        Assert.IsTrue(mapped.TuningIdentity?.EmbeddedSetupAvailable);
        Assert.AreEqual("abc123", mapped.TuningIdentity?.SetupFingerprint);
        Assert.AreEqual(.0d, mapped.TuningMap?.Turns.Single().ApexPct ?? double.NaN, .000001);
        Assert.IsTrue(mapped.TuningMap?.Turns.Single().ApexPct is >= 0 and < 1);
    }

    [TestMethod]
    public void RuntimeMapper_PreservesStructuredEvidenceCandidatesAndTestProtocol()
    {
        using var document = JsonDocument.Parse("""
        {
          "experiment_id":"experiment-1","experiment_path":"C:/coach/data/tuning/experiments/experiment-1.json","status":"ready",
          "eligibility":{"can_use_as_driving_evidence":true,"can_receive_garage_recommendation":true,"exact_map_identity":true,"exact_open_setup_identity":true},
          "candidate_whitelist":[{"candidate_id":"candidate-1","system":"static-balance","change":"One click","predicted_effect":"More rotation","risk":"Loose exit","verify":["entry balance"],"confidence":{"driver_report":0.8,"telemetry_context":0.6,"overall":0.85},"evidence_ids":["evidence-1"],"conflicts":[]}],
          "recommendation":{"status":"ready","selected_candidate_id":"candidate-1","summary":"Try one click.","evidence_ids":["evidence-1"],"conflicts":[],"confidence_reasons":["Measured evidence"]},
          "limitations":["One race"],"missing_required":[],
          "history":[{"experiment_id":"prior-1","outcome":"improved","setup_fingerprint":"old","recorded_utc":"2026-08-08T12:00:00Z","evidence_ids":["evidence-1"]}],
          "test_protocol":{"control":"Same conditions","sequence":["Baseline","Change","Compare"],"one_change_rule":true,"comparison_requirements":["Same fuel"]},
          "tuning_evidence_v2":{"observations":[{"evidence_id":"evidence-1","corner_id":"turn-1","corner_label":"Turn 1","run_phase":"late","metrics":{"entry_speed_mph":142.3},"source":"derived-from-recorded-telemetry","limitation":"Context only"}]}
        }
        """);

        var mapped = RuntimeMapper.StructuredTuning(document.RootElement);

        Assert.IsTrue(mapped.Eligibility.CanReceiveGarageRecommendation);
        Assert.AreEqual(EvidenceKind.Derived, mapped.Evidence.Single().Evidence);
        Assert.AreEqual("candidate-1", mapped.CandidateWhitelist.Single().CandidateId);
        Assert.AreEqual("Static Balance", mapped.CandidateWhitelist.Single().System);
        Assert.AreEqual("85%", mapped.CandidateWhitelist.Single().Confidence);
        Assert.AreEqual("candidate-1", mapped.Recommendation.SelectedCandidateId);
        Assert.AreEqual("prior-1", mapped.History.Single().ExperimentId);
        Assert.IsTrue(mapped.TestProtocol?.OneChangeRule);
    }

    [TestMethod]
    public async Task State_SubmitsBoundedStructuredPayloadWithoutDuplicatingExperimentPersistence()
    {
        var home = Path.Combine(Path.GetTempPath(), $"iracing-coach-state-tuning-{Guid.NewGuid():N}");
        try
        {
            var analysisPath = Path.Combine(home, "data", "reports", "analysis-open", "analysis.json");
            using var analysisDocument = JsonDocument.Parse(AnalysisEnvelope(analysisPath));
            using var resultDocument = JsonDocument.Parse("""
            {"experiment_id":"experiment-1","experiment_path":"canonical-experiment.json","status":"ready",
             "eligibility":{"can_use_as_driving_evidence":true,"can_receive_garage_recommendation":true,"exact_map_identity":true,"exact_open_setup_identity":true},
             "evidence":[{"evidence_id":"evidence-1","source":"driver-report","corner_label":"Turn 1","run_phase":"late","symptom_id":"tight","severity":3}],
             "candidate_whitelist":[{"candidate_id":"candidate-1","system":"static-balance","change":"One small step","predicted_effect":"More rotation","risk":"Loose exit","verify":["lap time"],"confidence":{"overall":0.8},"evidence_ids":["evidence-1"],"conflicts":[]}],
             "recommendation":{"status":"ready","selected_candidate_id":"candidate-1","summary":"Test one step.","evidence_ids":["evidence-1"],"conflicts":[],"confidence_reasons":["Exact setup"]},
             "limitations":[],"missing_required":[],"history":[]}
            """);
            var backend = new CapturingTuningBackend(analysisDocument.RootElement.Clone(), resultDocument.RootElement.Clone());
            using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(home, "settings.json")));
            state.Settings.CoachHome = home;
            var race = Race("open-state", "Open", analysisPath);
            state.Races.Add(race);

            await state.SelectTuningRaceAsync(race.Id);
            Assert.IsNotNull(state.CurrentAnalysis, state.TuningMessage);
            Assert.IsFalse(string.IsNullOrWhiteSpace(state.TuningDraft.RepresentativeSession.AnalysisId), state.TuningMessage);
            Assert.IsFalse(string.IsNullOrWhiteSpace(state.TuningDraft.RepresentativeSession.AnalysisPath), state.TuningMessage);
            await state.UpsertTuningFeedbackAsync(new ProgressiveTuningFeedback
            {
                FeedbackId = "feedback-1",
                CornerId = "detected-1",
                CornerLabel = "Turn 1",
                StartPct = .10,
                ApexPct = .20,
                EndPct = .30,
                RunPhase = "late",
                CornerPhases = ["entry"],
                SymptomId = "tight",
                Severity = 3,
                DriverConfidence = 4,
                Note = "Builds after ten laps",
                Priority = 3
            });
            await state.SaveTuningGeneralNoteAsync("Stable on exit");
            await state.SaveTuningGoalAsync("tire-life");
            await state.SubmitStructuredTuningAsync();

            Assert.AreEqual("recommend_structured_open_setup_tuning", backend.LastTool);
            Assert.IsNotNull(backend.LastArguments);
            var payload = backend.LastArguments.Value;
            Assert.AreEqual(Path.GetFullPath(analysisPath), Path.GetFullPath(payload.GetProperty("analysis_path").GetString()!));
            Assert.AreEqual(Path.GetFullPath(analysisPath), Path.GetFullPath(payload.GetProperty("open_target_analysis_path").GetString()!));
            Assert.AreEqual("Stable on exit", payload.GetProperty("generic_note").GetString());
            Assert.AreEqual("tire-life", payload.GetProperty("goal").GetString());
            Assert.AreEqual("1", payload.GetProperty("representative_run_ids")[0].GetString());
            Assert.AreEqual("Builds after ten laps", payload.GetProperty("feedback")[0].GetProperty("note").GetString());
            Assert.AreEqual(3, payload.GetProperty("feedback")[0].GetProperty("priority").GetInt32());
            Assert.AreEqual(new string('a', 64), payload.GetProperty("map_identity").GetProperty("geometry_hash").GetString());
            Assert.AreEqual(64, payload.GetProperty("map_identity").GetProperty("annotation_hash").GetString()?.Length);
            Assert.AreEqual("canonical-experiment.json", state.StructuredTuningResult?.ExperimentPath);
            Assert.IsFalse(Directory.Exists(Path.Combine(home, "data", "tuning-experiments")), "The coordinator must not create a duplicate experiment wrapper.");
        }
        finally
        {
            if (Directory.Exists(home)) Directory.Delete(home, recursive: true);
        }
    }

    [TestMethod]
    public async Task State_ConnectedCoachAppliesOnlyValidatedBoundedSelectionAndPersistsLineageThread()
    {
        var home = Path.Combine(Path.GetTempPath(), $"iracing-coach-state-ai-tuning-{Guid.NewGuid():N}");
        try
        {
            var analysisPath = Path.Combine(home, "data", "reports", "analysis-open", "analysis.json");
            using var analysisDocument = JsonDocument.Parse(AnalysisEnvelope(analysisPath));
            using var deterministicDocument = JsonDocument.Parse(StructuredResponse("Deterministic summary."));
            using var synthesizedDocument = JsonDocument.Parse(StructuredResponse("Bounded Coach summary."));
            var backend = new CapturingTuningBackend(
                analysisDocument.RootElement.Clone(),
                deterministicDocument.RootElement.Clone(),
                synthesizedDocument.RootElement.Clone());
            var coach = new FakeTuningCoachEngine("""
                {"selected_candidate_id":"candidate-1","summary":"Bounded Coach summary.","evidence_ids":["evidence-1"],"conflicts":[],"confidence_reasons":["Exact setup and supplied evidence."]}
                """);
            var settingsPath = Path.Combine(home, "settings.json");
            using var state = await ReadyStructuredStateAsync(home, analysisPath, backend, coach, settingsPath);
            var workflowKey = ProgressiveTuningCoordinator.SetupLineageWorkflowKey(state.TuningDraft.OpenSetupTarget!.Baseline);
            state.Settings.CoachThreadIds[workflowKey] = "existing-thread";

            await state.SubmitStructuredTuningAsync();

            Assert.AreEqual(2, backend.StructuredCalls);
            Assert.AreEqual("existing-thread", coach.LastThreadId);
            Assert.AreEqual("ai-tuning-output.schema.json", coach.LastSchemaFileName);
            StringAssert.Contains(coach.LastEvidenceJson, "candidate-1");
            Assert.IsLessThanOrEqualTo(64 * 1024, System.Text.Encoding.UTF8.GetByteCount(coach.LastEvidenceJson));
            Assert.IsFalse(coach.LastEvidenceJson.Contains(".ibt", StringComparison.OrdinalIgnoreCase));
            Assert.IsFalse(coach.LastEvidenceJson.Contains("analysis_path", StringComparison.OrdinalIgnoreCase));
            var aiPayload = backend.StructuredArguments[1].GetProperty("ai_response");
            Assert.AreEqual("candidate-1", aiPayload.GetProperty("selected_candidate_id").GetString());
            Assert.AreEqual("Bounded Coach summary.", state.StructuredTuningResult?.Recommendation.Summary);
            var reloaded = new JsonSettingsStore(settingsPath).Load();
            Assert.AreEqual("continued-thread", reloaded.CoachThreadIds[workflowKey]);
        }
        finally
        {
            if (Directory.Exists(home)) Directory.Delete(home, recursive: true);
        }
    }

    [TestMethod]
    [DataRow("{")]
    [DataRow("{\"selected_candidate_id\":\"invented\",\"summary\":\"Outside whitelist.\",\"evidence_ids\":[\"evidence-1\"],\"conflicts\":[],\"confidence_reasons\":[\"Unsupported.\"]}")]
    public async Task State_InvalidCoachOutputLeavesDeterministicRecommendationUntouched(string coachResponse)
    {
        var home = Path.Combine(Path.GetTempPath(), $"iracing-coach-state-invalid-ai-{Guid.NewGuid():N}");
        try
        {
            var analysisPath = Path.Combine(home, "data", "reports", "analysis-open", "analysis.json");
            using var analysisDocument = JsonDocument.Parse(AnalysisEnvelope(analysisPath));
            using var deterministicDocument = JsonDocument.Parse(StructuredResponse("Deterministic summary."));
            var backend = new CapturingTuningBackend(analysisDocument.RootElement.Clone(), deterministicDocument.RootElement.Clone());
            var coach = new FakeTuningCoachEngine(coachResponse);
            using var state = await ReadyStructuredStateAsync(
                home,
                analysisPath,
                backend,
                coach,
                Path.Combine(home, "settings.json"));

            await state.SubmitStructuredTuningAsync();

            Assert.AreEqual(1, backend.StructuredCalls, "Invalid AI output must never be sent back to the tuning backend.");
            Assert.AreEqual("Deterministic summary.", state.StructuredTuningResult?.Recommendation.Summary);
            Assert.AreEqual("candidate-1", state.StructuredTuningResult?.Recommendation.SelectedCandidateId);
        }
        finally
        {
            if (Directory.Exists(home)) Directory.Delete(home, recursive: true);
        }
    }

    [TestMethod]
    public async Task State_AiUpsertFailureLeavesDeterministicRecommendationUntouched()
    {
        var home = Path.Combine(Path.GetTempPath(), $"iracing-coach-state-ai-upsert-{Guid.NewGuid():N}");
        try
        {
            var analysisPath = Path.Combine(home, "data", "reports", "analysis-open", "analysis.json");
            using var analysisDocument = JsonDocument.Parse(AnalysisEnvelope(analysisPath));
            using var deterministicDocument = JsonDocument.Parse(StructuredResponse("Deterministic summary."));
            var backend = new CapturingTuningBackend(
                analysisDocument.RootElement.Clone(),
                deterministicDocument.RootElement.Clone(),
                synthesizedFailure: new BackendDomainException("Optional AI upsert unavailable."));
            var coach = new FakeTuningCoachEngine("""
                {"selected_candidate_id":"candidate-1","summary":"Bounded Coach summary.","evidence_ids":["evidence-1"],"conflicts":[],"confidence_reasons":["Exact setup and supplied evidence."]}
                """);
            using var state = await ReadyStructuredStateAsync(
                home,
                analysisPath,
                backend,
                coach,
                Path.Combine(home, "settings.json"));

            await state.SubmitStructuredTuningAsync();

            Assert.AreEqual(2, backend.StructuredCalls);
            Assert.AreEqual("Deterministic summary.", state.StructuredTuningResult?.Recommendation.Summary);
            Assert.AreEqual("candidate-1", state.StructuredTuningResult?.Recommendation.SelectedCandidateId);
        }
        finally
        {
            if (Directory.Exists(home)) Directory.Delete(home, recursive: true);
        }
    }

    [TestMethod]
    public async Task State_RejectsStaleFeedbackBoundsAndInvalidTurnCorrectionArcs()
    {
        var home = Path.Combine(Path.GetTempPath(), $"iracing-coach-state-invalid-map-{Guid.NewGuid():N}");
        try
        {
            var analysisPath = Path.Combine(home, "data", "reports", "analysis-open", "analysis.json");
            using var analysisDocument = JsonDocument.Parse(AnalysisEnvelope(analysisPath));
            using var resultDocument = JsonDocument.Parse(StructuredResponse("Deterministic summary."));
            var backend = new CapturingTuningBackend(analysisDocument.RootElement.Clone(), resultDocument.RootElement.Clone());
            using var state = await ReadyStructuredStateAsync(
                home,
                analysisPath,
                backend,
                new DisabledCoachEngineSupervisor(),
                Path.Combine(home, "settings.json"));

            await Assert.ThrowsExactlyAsync<InvalidOperationException>(() => state.UpsertTuningFeedbackAsync(
                state.TuningDraft.Feedback.Single() with { ApexPct = .21 }));
            Assert.HasCount(1, state.TuningDraft.Feedback);

            var map = state.SelectedTuningMap!;
            var turn = map.Turns.Single();
            await Assert.ThrowsExactlyAsync<InvalidOperationException>(() => state.SaveTuningTurnCorrectionAsync(new TuningTurnCorrectionDraft
            {
                CornerId = turn.CornerId,
                Label = turn.Label,
                StartPct = 1.0,
                ApexPct = turn.ApexPct,
                EndPct = turn.EndPct,
                MapIdentity = map.MapIdentity
            }));
        }
        finally
        {
            if (Directory.Exists(home)) Directory.Delete(home, recursive: true);
        }
    }

    [TestMethod]
    public async Task State_OutOfOrderRaceLoadCannotCommitThePreviouslySelectedRace()
    {
        var home = Path.Combine(Path.GetTempPath(), $"iracing-coach-state-race-order-{Guid.NewGuid():N}");
        try
        {
            var firstPath = Path.Combine(home, "data", "reports", "analysis-first", "analysis.json");
            var secondPath = Path.Combine(home, "data", "reports", "analysis-second", "analysis.json");
            using var firstDocument = JsonDocument.Parse(AnalysisEnvelope(firstPath, "first"));
            using var secondDocument = JsonDocument.Parse(AnalysisEnvelope(secondPath, "second"));
            var backend = new OutOfOrderTuningBackend(
                firstDocument.RootElement.Clone(),
                secondDocument.RootElement.Clone());
            using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(home, "settings.json")));
            state.Settings.CoachHome = home;
            var first = Race("first", "Open", firstPath);
            var second = Race("second", "Open", secondPath);
            state.Races.AddRange([first, second]);

            var firstLoad = state.SelectTuningRaceAsync(first.Id);
            await backend.FirstStarted.Task.WaitAsync(TimeSpan.FromSeconds(2));
            await state.SelectTuningRaceAsync(second.Id);
            backend.ReleaseFirst.TrySetResult(true);
            await firstLoad;

            Assert.AreEqual(second.Id, state.SelectedTuningRaceId);
            Assert.AreEqual(second.Id, state.TuningDraft.RepresentativeSession.RaceId);
            Assert.AreEqual(Path.GetFullPath(secondPath), Path.GetFullPath(state.TuningDraft.RepresentativeSession.AnalysisPath));
        }
        finally
        {
            if (Directory.Exists(home)) Directory.Delete(home, recursive: true);
        }
    }

    [TestMethod]
    public async Task State_FailedRaceLoadCannotExposeThePreviouslySelectedRaceWorkspace()
    {
        var home = Path.Combine(Path.GetTempPath(), $"iracing-coach-state-race-failure-{Guid.NewGuid():N}");
        try
        {
            var firstPath = Path.Combine(home, "data", "reports", "analysis-first", "analysis.json");
            var secondPath = Path.Combine(home, "data", "reports", "analysis-second", "analysis.json");
            using var firstDocument = JsonDocument.Parse(AnalysisEnvelope(firstPath, "first"));
            var backend = new FailingSecondTuningBackend(firstDocument.RootElement.Clone());
            using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(home, "settings.json")));
            state.Settings.CoachHome = home;
            var first = Race("first", "Open", firstPath);
            var second = Race("second", "Open", secondPath);
            state.Races.AddRange([first, second]);

            await state.SelectTuningRaceAsync(first.Id);
            Assert.AreEqual(first.Id, state.TuningDraft.RepresentativeSession.RaceId);
            Assert.IsNotNull(state.CurrentAnalysis);

            await Assert.ThrowsExactlyAsync<BackendDomainException>(() => state.SelectTuningRaceAsync(second.Id));

            Assert.AreEqual(second.Id, state.SelectedTuningRaceId);
            Assert.IsNull(state.CurrentAnalysis);
            Assert.IsNull(state.CurrentRaceCard);
            Assert.IsNull(state.SelectedTuningMap);
            Assert.AreEqual(string.Empty, state.TuningDraft.RepresentativeSession.RaceId);
            Assert.HasCount(0, state.TuningDraft.Feedback);
        }
        finally
        {
            if (Directory.Exists(home)) Directory.Delete(home, recursive: true);
        }
    }

    [TestMethod]
    public async Task State_OutOfOrderOpenTargetLoadCannotReplaceTheNewerTargetSelection()
    {
        var home = Path.Combine(Path.GetTempPath(), $"iracing-coach-state-target-order-{Guid.NewGuid():N}");
        try
        {
            var representativePath = Path.Combine(home, "data", "reports", "analysis-representative", "analysis.json");
            var firstTargetPath = Path.Combine(home, "data", "reports", "analysis-target-first", "analysis.json");
            var secondTargetPath = Path.Combine(home, "data", "reports", "analysis-target-second", "analysis.json");
            using var representativeDocument = JsonDocument.Parse(AnalysisEnvelope(representativePath, "representative"));
            using var firstTargetDocument = JsonDocument.Parse(AnalysisEnvelope(firstTargetPath, "target-first"));
            using var secondTargetDocument = JsonDocument.Parse(AnalysisEnvelope(secondTargetPath, "target-second"));
            var backend = new OutOfOrderTargetBackend(
                representativeDocument.RootElement.Clone(),
                firstTargetDocument.RootElement.Clone(),
                secondTargetDocument.RootElement.Clone());
            using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(home, "settings.json")));
            state.Settings.CoachHome = home;
            var representative = Race("representative", "Open", representativePath);
            var firstTarget = Race("target-first", "Open", firstTargetPath);
            var secondTarget = Race("target-second", "Open", secondTargetPath);
            state.Races.AddRange([representative, firstTarget, secondTarget]);
            await state.SelectTuningRaceAsync(representative.Id);

            var firstSelection = state.SelectTuningOpenTargetAsync(firstTarget.Id);
            await backend.FirstTargetStarted.Task.WaitAsync(TimeSpan.FromSeconds(2));
            await state.SelectTuningOpenTargetAsync(secondTarget.Id);
            backend.ReleaseFirstTarget.TrySetResult(true);
            await firstSelection;

            Assert.AreEqual(secondTarget.Id, state.SelectedTuningTargetRaceId);
            Assert.AreEqual(secondTarget.Id, state.SelectedTuningTarget?.Baseline.RaceId);
            Assert.AreEqual(secondTarget.Id, state.TuningDraft.OpenSetupTarget?.Baseline.RaceId);
        }
        finally
        {
            if (Directory.Exists(home)) Directory.Delete(home, recursive: true);
        }
    }

    [TestMethod]
    public async Task State_ConcurrentDraftMutationsComposeAndPersistWithoutLostUpdates()
    {
        var home = Path.Combine(Path.GetTempPath(), $"iracing-coach-state-draft-compose-{Guid.NewGuid():N}");
        try
        {
            var analysisPath = Path.Combine(home, "data", "reports", "analysis-open-state", "analysis.json");
            using var analysisDocument = JsonDocument.Parse(AnalysisEnvelope(analysisPath));
            using var resultDocument = JsonDocument.Parse(StructuredResponse("Deterministic recommendation."));
            var backend = new CapturingTuningBackend(
                analysisDocument.RootElement.Clone(),
                resultDocument.RootElement.Clone());
            using var state = await ReadyStructuredStateAsync(
                home,
                analysisPath,
                backend,
                new DisabledCoachEngineSupervisor(),
                Path.Combine(home, "settings.json"));

            await Task.WhenAll(
                state.SaveTuningGeneralNoteAsync("Preserve both updates."),
                state.SaveTuningGoalAsync("tire-life"));

            Assert.AreEqual("Preserve both updates.", state.TuningDraft.GeneralNote);
            Assert.AreEqual("tire-life", state.TuningDraft.Goal);
            var persisted = new PortableTuningDraftStore(home).Load(state.TuningDraft.RepresentativeSession);
            Assert.IsNotNull(persisted);
            Assert.AreEqual("Preserve both updates.", persisted.GeneralNote);
            Assert.AreEqual("tire-life", persisted.Goal);
        }
        finally
        {
            if (Directory.Exists(home)) Directory.Delete(home, recursive: true);
        }
    }

    [TestMethod]
    public async Task State_TurnCorrectionsForTwoGeometryHashesSurviveSwitchAndReload()
    {
        var home = Path.Combine(Path.GetTempPath(), $"iracing-coach-state-geometry-corrections-{Guid.NewGuid():N}");
        try
        {
            var firstPath = Path.Combine(home, "data", "reports", "analysis-geometry-first", "analysis.json");
            var secondPath = Path.Combine(home, "data", "reports", "analysis-geometry-second", "analysis.json");
            var firstHash = new string('a', 64);
            var secondHash = new string('b', 64);
            using var firstDocument = JsonDocument.Parse(AnalysisEnvelope(firstPath, "geometry-first", firstHash));
            using var secondDocument = JsonDocument.Parse(AnalysisEnvelope(secondPath, "geometry-second", secondHash));
            var first = Race("geometry-first", "Open", firstPath);
            var second = Race("geometry-second", "Open", secondPath);

            using (var state = new CompanionState(
                new GeometrySwitchBackend(firstDocument.RootElement.Clone(), secondDocument.RootElement.Clone()),
                new JsonSettingsStore(Path.Combine(home, "settings.json"))))
            {
                state.Settings.CoachHome = home;
                state.Races.AddRange([first, second]);

                await state.SelectTuningRaceAsync(first.Id);
                await state.SaveTuningTurnCorrectionAsync("detected-1", "First geometry turn");
                await state.SelectTuningRaceAsync(second.Id);
                await state.SaveTuningTurnCorrectionAsync("detected-1", "Second geometry turn");

                var annotations = new PortableTuningTurnAnnotationStore(home).Load("iowa-oval", "iowa-oval");
                Assert.IsNotNull(annotations);
                Assert.HasCount(2, annotations.Corrections);
                Assert.IsTrue(annotations.Corrections.Any(item => item.GeometryHash == firstHash && item.Label == "First geometry turn"));
                Assert.IsTrue(annotations.Corrections.Any(item => item.GeometryHash == secondHash && item.Label == "Second geometry turn"));

                await state.SelectTuningRaceAsync(first.Id);
                Assert.AreEqual("First geometry turn", state.SelectedTuningMap?.Turns.Single().Label);
                await state.SelectTuningRaceAsync(second.Id);
                Assert.AreEqual("Second geometry turn", state.SelectedTuningMap?.Turns.Single().Label);
            }

            using var reloaded = new CompanionState(
                new GeometrySwitchBackend(firstDocument.RootElement.Clone(), secondDocument.RootElement.Clone()),
                new JsonSettingsStore(Path.Combine(home, "settings.json")));
            reloaded.Settings.CoachHome = home;
            reloaded.Races.AddRange([first, second]);
            await reloaded.SelectTuningRaceAsync(first.Id);
            Assert.AreEqual("First geometry turn", reloaded.SelectedTuningMap?.Turns.Single().Label);
            await reloaded.SelectTuningRaceAsync(second.Id);
            Assert.AreEqual("Second geometry turn", reloaded.SelectedTuningMap?.Turns.Single().Label);
        }
        finally
        {
            if (Directory.Exists(home)) Directory.Delete(home, recursive: true);
        }
    }

    private static RecentRace Race(string id, string setupType, string analysisPath) => new(
        id, "Iowa Speedway", "Oval", "Toyota Supra", "Today", setupType, "Analyzed", "Recorded",
        false, true, 10, 5, "stockcars2/supra2019", analysisPath, "race.ibt", "", $"event-{id}", "Race", Selector: $"selector-{id}");

    private static TuningSessionIdentity Identity(RecentRace race, string setupType, bool embedded, string fingerprint) => new()
    {
        RaceId = race.Id,
        EventKey = race.EventKey,
        Selector = race.EffectiveSelector,
        AnalysisId = $"analysis-{race.Id}",
        AnalysisPath = race.AnalysisPath,
        SessionType = race.SessionType,
        TrackConfigurationKey = "iowa-oval",
        TrackId = "7",
        Track = race.Track,
        Layout = race.Layout,
        CarId = "10",
        CarPath = race.CarPath,
        Car = race.Car,
        SetupType = setupType,
        SetupFingerprint = fingerprint,
        EmbeddedSetupAvailable = embedded
    };

    private static AnalysisWorkspace Workspace(TuningSessionIdentity identity) => new(
        SchemaVersion: 1,
        AnalysisId: identity.AnalysisId,
        Track: identity.Track,
        Layout: identity.Layout,
        Car: identity.Car,
        SetupType: identity.SetupType,
        SessionType: identity.SessionType,
        RecordedLaps: 20,
        ScheduledLaps: 20,
        PitStops: 0,
        Runs: [],
        Laps: [],
        Traces: [],
        TrackShape: [],
        Segments: [],
        GeometryMode: "track_shape",
        TireStressDefinition: string.Empty,
        StrategyStatus: string.Empty,
        DamageStatus: string.Empty,
        Strategy: new AnalysisStrategy(null, null, null, null, [], null, null, string.Empty, [], []),
        Damage: new AnalysisDamage(0, 0, 0, 0, null, null, []),
        SetupFingerprint: identity.SetupFingerprint,
        DataConfidence: "High",
        BackendElapsedMilliseconds: 1,
        OverallGrade: "B",
        Grades: [],
        TuningIdentity: identity,
        TuningMap: Map());

    private static TuningMapView Map() => new(
        "iowa-official-v1",
        "nascar-official",
        "Official map",
        null,
        "High",
        null,
        false,
        [new TuningMapPoint(0, 0, 0), new TuningMapPoint(.5, 1, 1)],
        [new TuningTurn("turn-1", "Turn 1", .1, .2, .3, false, "Detected")],
        new string('a', 64));

    private static StructuredTuningResultView StructuredResult() => new(
        "experiment-1",
        "experiment.json",
        new TuningEligibilityView { CanUseAsEvidence = true, CanReceiveGarageRecommendation = true },
        [new TuningEvidenceView("evidence-1", EvidenceKind.Measured, "Entry", "3.2", "mph", "IBT", "")],
        [new TuningCandidateChangeView("candidate-1", "Shock", "One click", "Rotate", "Loose", [], "rule", "medium", ["evidence-1"], [])],
        new StructuredTuningRecommendationView("ready", "candidate-1", "Try one click", ["evidence-1"], [], []),
        [], [], [], null);

    private static async Task<CompanionState> ReadyStructuredStateAsync(
        string home,
        string analysisPath,
        IBackendClient backend,
        ICoachEngineSupervisor coach,
        string settingsPath)
    {
        var state = new CompanionState(
            backend,
            new JsonSettingsStore(settingsPath),
            new DisconnectedLiveTelemetrySource(),
            coach);
        state.Settings.CoachHome = home;
        var race = Race("open-state", "Open", analysisPath);
        state.Races.Add(race);
        await state.SelectTuningRaceAsync(race.Id);
        await state.UpsertTuningFeedbackAsync(new ProgressiveTuningFeedback
        {
            FeedbackId = "feedback-1",
            CornerId = "detected-1",
            CornerLabel = "Turn 1",
            StartPct = .10,
            ApexPct = .20,
            EndPct = .30,
            RunPhase = "late",
            CornerPhases = ["entry"],
            SymptomId = "tight",
            Severity = 3,
            DriverConfidence = 4,
            Note = "Builds after ten laps",
            Priority = 3
        });
        return state;
    }

    private static string StructuredResponse(string summary) => $$"""
    {"experiment_id":"experiment-1","experiment_path":"canonical-experiment.json","status":"ready",
     "eligibility":{"can_use_as_driving_evidence":true,"can_receive_garage_recommendation":true,"exact_map_identity":true,"exact_open_setup_identity":true},
     "evidence":[{"evidence_id":"evidence-1","source":"driver-report","corner_label":"Turn 1","run_phase":"late","symptom_id":"tight","severity":3}],
     "candidate_whitelist":[{"candidate_id":"candidate-1","system":"static-balance","change":"One small step","predicted_effect":"More rotation","risk":"Loose exit","verify":["lap time"],"confidence":{"overall":0.8},"evidence_ids":["evidence-1"],"conflicts":[]}],
     "recommendation":{"status":"ready","selected_candidate_id":"candidate-1","summary":{{JsonSerializer.Serialize(summary)}},"evidence_ids":["evidence-1"],"conflicts":[],"confidence_reasons":["Exact setup"]},
     "limitations":[],"missing_required":[],"history":[]}
    """;

    private static string AnalysisEnvelope(
        string analysisPath,
        string raceId = "open-state",
        string? geometryHash = null) => """
    {
      "analysis_id":"analysis-__RACE_ID__","analysis_path":"__ANALYSIS_PATH__",
      "selection":{"selector":"selector-__RACE_ID__","group_id":"selector-__RACE_ID__","sim_session_type":"Race"},
      "race_card":{"title":"Race","bottom_line":{"evidence_type":"measured","text":"Recorded"},"actions":[],"race_triggers":[],"evidence_appendix":[]},
      "analysis_view":{
        "schema_version":1,
        "identity":{"track_id":7,"track_name":"Iowa Speedway","track_config":"Oval","car_id":10,"car_path":"stockcars2/supra2019","car_name":"Toyota Supra","event_type":"Race","is_fixed_setup":false,"setup_fingerprint":"open-fingerprint","setup_parameter_count":2,"setup":{"Chassis":{"CrossWeight":50.0}}},
        "race_summary":{"recorded_laps":6,"scheduled_laps":20},"laps":[],
        "runs":[{"run_number":1,"lap_numbers":[1,2,3,4,5,6],"coaching_reference_lap_numbers":[1,2,3,4,5,6],"green_laps":6,"caution_laps":0,"damage_repair_context":{}}],
        "lap_traces":{"traces":[]},
        "track_profile":{"shape":[],"detected_corner_segments":[{"segment":1,"label":"Turn 1","start_pct":0.1,"end_pct":0.3}]},
        "track_geometry":{"status":"usable","track_configuration_key":"iowa-oval","geometry_hash":"__GEOMETRY_HASH__","main_path":[{"lap_pct":0.0,"x":0,"y":0},{"lap_pct":0.5,"x":1,"y":1}],"source_sha256":[]},
        "strategy":{"forecast":{},"limitations":[]},"damage_repair":{"summary":{},"incident_points":{"events":[]},"limitations":[]},"setup_telemetry":{},"data_quality":{},"race_grades":{"categories":[],"unavailable_categories":[]}
      }
    }
    """
    .Replace("__ANALYSIS_PATH__", analysisPath.Replace("\\", "/"), StringComparison.Ordinal)
    .Replace("__RACE_ID__", raceId, StringComparison.Ordinal)
    .Replace("__GEOMETRY_HASH__", geometryHash ?? new string('a', 64), StringComparison.Ordinal);

    private sealed class CapturingTuningBackend(
        JsonElement analysis,
        JsonElement result,
        JsonElement? synthesizedResult = null,
        Exception? synthesizedFailure = null) : IBackendClient
    {
        public string LastTool { get; private set; } = string.Empty;
        public JsonElement? LastArguments { get; private set; }
        public List<JsonElement> StructuredArguments { get; } = [];
        public int StructuredCalls => StructuredArguments.Count;

        public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default) =>
            Task.FromResult(new BackendHealthResult(true, "local", "0.3.0", "v1", 17, TimeSpan.Zero));

        public Task<JsonElement> CallToolAsync(BackendConfiguration configuration, string toolName, object arguments, CancellationToken cancellationToken = default)
        {
            if (toolName == "analyze_iracing_race") return Task.FromResult(analysis);
            if (toolName == "recommend_structured_open_setup_tuning")
            {
                LastTool = toolName;
                LastArguments = JsonSerializer.SerializeToElement(arguments);
                StructuredArguments.Add(LastArguments.Value);
                if (LastArguments.Value.TryGetProperty("ai_response", out _))
                {
                    if (synthesizedFailure is not null) return Task.FromException<JsonElement>(synthesizedFailure);
                    if (synthesizedResult.HasValue) return Task.FromResult(synthesizedResult.Value);
                }
                return Task.FromResult(result);
            }
            return Task.FromResult(JsonSerializer.SerializeToElement(new { ok = true }));
        }
    }

    private sealed class FakeTuningCoachEngine(string response) : ICoachEngineSupervisor
    {
        public CoachEngineConnection Current { get; } = new(true, true, true, "connected", "ChatGPT connected");
        public string? LastThreadId { get; private set; }
        public string LastEvidenceJson { get; private set; } = string.Empty;
        public string LastSchemaFileName { get; private set; } = string.Empty;
        public event Action<CoachEngineConnection>? Changed { add { } remove { } }
        public event Action<string>? CoachMessageDelta { add { } remove { } }
        public Task StartAsync(CompanionSettings settings, CancellationToken cancellationToken = default) => Task.CompletedTask;
        public Task RefreshAccountAsync(CancellationToken cancellationToken = default) => Task.CompletedTask;
        public Task<CoachEngineLogin> BeginChatGptLoginAsync(bool deviceCode = false, CancellationToken cancellationToken = default) =>
            Task.FromException<CoachEngineLogin>(new InvalidOperationException("Not used by this test."));
        public Task CancelLoginAsync(string loginId, CancellationToken cancellationToken = default) => Task.CompletedTask;
        public Task<CoachEngineReply> AskCoachAsync(string? threadId, string question, string evidenceJson, CancellationToken cancellationToken = default) =>
            Task.FromException<CoachEngineReply>(new InvalidOperationException("The tuning workflow must use structured Coach output."));
        public Task<CoachEngineReply> AskStructuredCoachAsync(
            string? threadId,
            string instruction,
            string evidenceJson,
            string outputSchemaFileName,
            CancellationToken cancellationToken = default)
        {
            LastThreadId = threadId;
            LastEvidenceJson = evidenceJson;
            LastSchemaFileName = outputSchemaFileName;
            return Task.FromResult(new CoachEngineReply("continued-thread", "turn-1", response));
        }
        public Task StopAsync(CancellationToken cancellationToken = default) => Task.CompletedTask;
        public ValueTask DisposeAsync() => ValueTask.CompletedTask;
    }

    private sealed class OutOfOrderTuningBackend(JsonElement first, JsonElement second) : IBackendClient
    {
        public TaskCompletionSource<bool> FirstStarted { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public TaskCompletionSource<bool> ReleaseFirst { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);

        public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default) =>
            Task.FromResult(new BackendHealthResult(true, "test", "test", "test", 1, TimeSpan.Zero, "ready"));

        public async Task<JsonElement> CallToolAsync(
            BackendConfiguration configuration,
            string toolName,
            object arguments,
            CancellationToken cancellationToken = default)
        {
            if (!string.Equals(toolName, "analyze_iracing_race", StringComparison.Ordinal))
                return JsonSerializer.SerializeToElement(new { ok = true });
            var payload = JsonSerializer.SerializeToElement(arguments);
            var selector = payload.GetProperty("selector").GetString();
            if (string.Equals(selector, "selector-first", StringComparison.Ordinal))
            {
                FirstStarted.TrySetResult(true);
                await ReleaseFirst.Task.WaitAsync(cancellationToken);
                return first;
            }
            return second;
        }
    }

    private sealed class FailingSecondTuningBackend(JsonElement first) : IBackendClient
    {
        public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default) =>
            Task.FromResult(new BackendHealthResult(true, "test", "test", "test", 1, TimeSpan.Zero, "ready"));

        public Task<JsonElement> CallToolAsync(
            BackendConfiguration configuration,
            string toolName,
            object arguments,
            CancellationToken cancellationToken = default)
        {
            if (!string.Equals(toolName, "analyze_iracing_race", StringComparison.Ordinal))
                return Task.FromResult(JsonSerializer.SerializeToElement(new { ok = true }));
            var payload = JsonSerializer.SerializeToElement(arguments);
            return string.Equals(payload.GetProperty("selector").GetString(), "selector-first", StringComparison.Ordinal)
                ? Task.FromResult(first)
                : Task.FromException<JsonElement>(new BackendDomainException("The second recording could not be analyzed."));
        }
    }

    private sealed class OutOfOrderTargetBackend(
        JsonElement representative,
        JsonElement firstTarget,
        JsonElement secondTarget) : IBackendClient
    {
        public TaskCompletionSource<bool> FirstTargetStarted { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public TaskCompletionSource<bool> ReleaseFirstTarget { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);

        public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default) =>
            Task.FromResult(new BackendHealthResult(true, "test", "test", "test", 1, TimeSpan.Zero, "ready"));

        public async Task<JsonElement> CallToolAsync(
            BackendConfiguration configuration,
            string toolName,
            object arguments,
            CancellationToken cancellationToken = default)
        {
            if (!string.Equals(toolName, "analyze_iracing_race", StringComparison.Ordinal))
                return JsonSerializer.SerializeToElement(new { ok = true });
            var selector = JsonSerializer.SerializeToElement(arguments).GetProperty("selector").GetString();
            if (string.Equals(selector, "selector-target-first", StringComparison.Ordinal))
            {
                FirstTargetStarted.TrySetResult(true);
                await ReleaseFirstTarget.Task.WaitAsync(cancellationToken);
                return firstTarget;
            }
            return string.Equals(selector, "selector-target-second", StringComparison.Ordinal)
                ? secondTarget
                : representative;
        }
    }

    private sealed class GeometrySwitchBackend(JsonElement first, JsonElement second) : IBackendClient
    {
        public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default) =>
            Task.FromResult(new BackendHealthResult(true, "test", "test", "test", 1, TimeSpan.Zero, "ready"));

        public Task<JsonElement> CallToolAsync(
            BackendConfiguration configuration,
            string toolName,
            object arguments,
            CancellationToken cancellationToken = default)
        {
            if (!string.Equals(toolName, "analyze_iracing_race", StringComparison.Ordinal))
                return Task.FromResult(JsonSerializer.SerializeToElement(new { ok = true }));
            var selector = JsonSerializer.SerializeToElement(arguments).GetProperty("selector").GetString();
            return Task.FromResult(string.Equals(selector, "selector-geometry-first", StringComparison.Ordinal) ? first : second);
        }
    }
}
