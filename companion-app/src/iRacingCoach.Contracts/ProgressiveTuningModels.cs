using System.Text.Json.Serialization;

namespace iRacingCoach.Contracts;

// Progressive-tuning contracts are isolated from the legacy experiment view so
// contract-v1 callers can continue to deserialize while the graphical workflow
// adopts exact session identity and structured feedback.

public sealed record TuningMapPoint(double LapPercent, double X, double Y);

public sealed record TuningTurn(
    string CornerId,
    string Label,
    double StartPct,
    double ApexPct,
    double EndPct,
    bool IsOfficial,
    string Confidence,
    string? CorrectionHint = null,
    bool UserVerified = false,
    string VerificationSource = "");

public sealed record TuningTurnCorrectionDraft
{
    public string CornerId { get; init; } = string.Empty;
    public string Label { get; init; } = string.Empty;
    public double StartPct { get; init; }
    public double ApexPct { get; init; }
    public double EndPct { get; init; }
    public string Note { get; init; } = string.Empty;
    public string GeometryHash { get; init; } = string.Empty;
    public string MapIdentity { get; init; } = string.Empty;
    public DateTimeOffset VerifiedAt { get; init; } = DateTimeOffset.UtcNow;
}

public sealed record TuningTurnAnnotationSet
{
    public int SchemaVersion { get; init; } = 1;
    public string TrackConfigurationKey { get; init; } = string.Empty;
    public string MapIdentity { get; init; } = string.Empty;
    public IReadOnlyList<TuningTurnCorrectionDraft> Corrections { get; init; } = [];
    public DateTimeOffset UpdatedUtc { get; init; } = DateTimeOffset.UtcNow;
}

public sealed record TuningMapView(
    string MapIdentity,
    string SourceType,
    string SourceLabel,
    string? SourceUrl,
    string Confidence,
    string? VerificationMessage,
    bool IsVerified,
    IReadOnlyList<TuningMapPoint> Path,
    IReadOnlyList<TuningTurn> Turns,
    string? GeometryHash = null);

public sealed record TuningMapCornerIdentity(
    [property: JsonPropertyName("corner_id")] string CornerId,
    [property: JsonPropertyName("label")] string Label,
    [property: JsonPropertyName("start_pct")] double StartPct,
    [property: JsonPropertyName("apex_pct")] double ApexPct,
    [property: JsonPropertyName("end_pct")] double EndPct,
    [property: JsonPropertyName("is_official")] bool IsOfficial,
    [property: JsonPropertyName("user_verified")] bool UserVerified);

public sealed record TuningMapSubmissionIdentity(
    [property: JsonPropertyName("track_configuration_key")] string TrackConfigurationKey,
    [property: JsonPropertyName("map_identity")] string MapIdentity,
    [property: JsonPropertyName("geometry_hash")] string GeometryHash,
    [property: JsonPropertyName("annotation_hash")] string AnnotationHash,
    [property: JsonPropertyName("source_type")] string SourceType,
    [property: JsonPropertyName("source_label")] string SourceLabel,
    [property: JsonPropertyName("source_url")] string? SourceUrl,
    [property: JsonPropertyName("verified")] bool Verified,
    [property: JsonPropertyName("corners")] IReadOnlyList<TuningMapCornerIdentity> Corners);

public sealed record TuningSessionIdentity
{
    public string RaceId { get; init; } = string.Empty;
    public string EventKey { get; init; } = string.Empty;
    public string Selector { get; init; } = string.Empty;
    public string AnalysisId { get; init; } = string.Empty;
    public string AnalysisPath { get; init; } = string.Empty;
    public string SessionId { get; init; } = string.Empty;
    public string SessionUniqueId { get; init; } = string.Empty;
    public string SubsessionId { get; init; } = string.Empty;
    public string SessionType { get; init; } = string.Empty;
    public string TrackConfigurationKey { get; init; } = string.Empty;
    public string TrackId { get; init; } = string.Empty;
    public string Track { get; init; } = string.Empty;
    public string Layout { get; init; } = string.Empty;
    public string CarId { get; init; } = string.Empty;
    public string CarPath { get; init; } = string.Empty;
    public string Car { get; init; } = string.Empty;
    public string SetupType { get; init; } = "Unknown";
    public string SetupFingerprint { get; init; } = string.Empty;
    public bool EmbeddedSetupAvailable { get; init; }
    public IReadOnlyList<string> SourceSha256 { get; init; } = [];
}

public sealed record TuningEligibilityView
{
    public bool CanUseAsEvidence { get; init; }
    public bool CanReceiveGarageRecommendation { get; init; }
    public bool IsFinalized { get; init; }
    public bool IsOpenSetup { get; init; }
    public bool EmbeddedSetupAvailable { get; init; }
    public bool HasSetupFingerprint { get; init; }
    public bool ExactIdentityAvailable { get; init; }
    public IReadOnlyList<string> MissingRequired { get; init; } = [];
    public IReadOnlyList<string> Blockers { get; init; } = [];
}

public sealed record TuningRaceCandidate(
    RecentRace Session,
    TuningSessionIdentity Identity,
    TuningEligibilityView Eligibility,
    bool RequiresAnalysisLoad);

public sealed record TuningSetupTarget(
    string TargetId,
    TuningSessionIdentity Baseline,
    bool CanReceiveGarageRecommendation,
    IReadOnlyList<string> UnavailableReasons);

public sealed record ProgressiveTuningFeedback
{
    public string FeedbackId { get; init; } = Guid.NewGuid().ToString("N");
    public string CornerId { get; init; } = string.Empty;
    public string CornerLabel { get; init; } = string.Empty;
    public double? StartPct { get; init; }
    public double? ApexPct { get; init; }
    public double? EndPct { get; init; }
    public string RunPhase { get; init; } = "middle";
    public IReadOnlyList<string> CornerPhases { get; init; } = [];
    public string SymptomId { get; init; } = string.Empty;
    public int Severity { get; init; } = 3;
    public int DriverConfidence { get; init; } = 3;
    public string Note { get; init; } = string.Empty;
    public int Priority { get; init; } = 3;
}

public sealed record ProgressiveTuningDraft
{
    public int SchemaVersion { get; init; } = 2;
    public string DraftId { get; init; } = Guid.NewGuid().ToString("N");
    public TuningSessionIdentity RepresentativeSession { get; init; } = new();
    public TuningSetupTarget? OpenSetupTarget { get; init; }
    public IReadOnlyList<string> RepresentativeRunIds { get; init; } = [];
    public string MapIdentity { get; init; } = string.Empty;
    public string RulesetId { get; init; } = "nascar-oreilly-xfinity-2026s3-v1";
    public string Goal { get; init; } = "long-run-pace";
    public string GeneralNote { get; init; } = string.Empty;
    public IReadOnlyList<ProgressiveTuningFeedback> Feedback { get; init; } = [];
    public IReadOnlyList<TuningTurnCorrectionDraft> TurnCorrections { get; init; } = [];
    public DateTimeOffset UpdatedUtc { get; init; } = DateTimeOffset.UtcNow;
}

public sealed record TuningEvidenceView(
    string EvidenceId,
    EvidenceKind Evidence,
    string Label,
    string Value,
    string Unit,
    string Source,
    string Limitation);

public sealed record TuningCandidateChangeView(
    string CandidateId,
    string System,
    string Change,
    string PredictedEffect,
    string Risk,
    IReadOnlyList<string> Verify,
    string Source,
    string Confidence,
    IReadOnlyList<string> EvidenceIds,
    IReadOnlyList<string> Conflicts);

public sealed record TuningHistoryView(
    string ExperimentId,
    string Outcome,
    string SetupFingerprint,
    DateTimeOffset? RecordedUtc,
    IReadOnlyList<string> EvidenceIds);

public sealed record TuningTestProtocolView(
    string Control,
    IReadOnlyList<string> Sequence,
    bool OneChangeRule,
    IReadOnlyList<string> ComparisonRequirements);

public sealed record StructuredTuningRecommendationView(
    string Status,
    string SelectedCandidateId,
    string Summary,
    IReadOnlyList<string> EvidenceIds,
    IReadOnlyList<string> Conflicts,
    IReadOnlyList<string> ConfidenceReasons);

public sealed record StructuredTuningResultView(
    string ExperimentId,
    string ExperimentPath,
    TuningEligibilityView Eligibility,
    IReadOnlyList<TuningEvidenceView> Evidence,
    IReadOnlyList<TuningCandidateChangeView> CandidateWhitelist,
    StructuredTuningRecommendationView Recommendation,
    IReadOnlyList<string> Limitations,
    IReadOnlyList<string> MissingRequired,
    IReadOnlyList<TuningHistoryView> History,
    TuningTestProtocolView? TestProtocol);

public sealed record TuningAiEvidenceView(
    string WorkflowKey,
    string ExperimentId,
    TuningEligibilityView Eligibility,
    IReadOnlyList<TuningEvidenceView> Evidence,
    IReadOnlyList<TuningCandidateChangeView> CandidateWhitelist,
    IReadOnlyList<string> Limitations);

public sealed record TuningAiSelection(
    [property: JsonPropertyName("selected_candidate_id")] string SelectedCandidateId,
    [property: JsonPropertyName("summary")] string Summary,
    [property: JsonPropertyName("evidence_ids")] IReadOnlyList<string> EvidenceIds,
    [property: JsonPropertyName("conflicts")] IReadOnlyList<string> Conflicts,
    [property: JsonPropertyName("confidence_reasons")] IReadOnlyList<string> ConfidenceReasons);

public sealed record TuningAiSelectionValidation(
    bool Valid,
    TuningAiSelection? Selection,
    IReadOnlyList<string> Errors);
