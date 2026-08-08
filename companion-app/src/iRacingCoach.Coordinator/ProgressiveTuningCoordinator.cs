using System.Collections.Concurrent;
using System.Security.Cryptography;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

public sealed record BoundedTuningAiRequest(
    string Json,
    IReadOnlySet<string> CandidateIds,
    IReadOnlySet<string> EvidenceIds);

public static class ProgressiveTuningCoordinator
{
    public static TuningRaceCandidate Candidate(RecentRace race, AnalysisWorkspace? workspace = null)
    {
        var exactWorkspace = workspace is not null && Matches(race, workspace.TuningIdentity);
        var identity = exactWorkspace
            ? Bind(workspace!.TuningIdentity!, race)
            : FromRace(race);
        var finalized = race.Analyzed && !string.IsNullOrWhiteSpace(race.AnalysisPath);
        var open = string.Equals(identity.SetupType, "Open", StringComparison.OrdinalIgnoreCase)
            || (!exactWorkspace && string.Equals(race.SetupType, "Open", StringComparison.OrdinalIgnoreCase));
        var embedded = exactWorkspace && identity.EmbeddedSetupAvailable;
        var fingerprint = exactWorkspace && !string.IsNullOrWhiteSpace(identity.SetupFingerprint);
        var exactIdentity = exactWorkspace
            && !string.IsNullOrWhiteSpace(identity.AnalysisId)
            && (!string.IsNullOrWhiteSpace(identity.TrackConfigurationKey)
                || !string.IsNullOrWhiteSpace(identity.Layout));
        var missing = new List<string>();
        var blockers = new List<string>();
        if (!finalized) missing.Add("A finalized analyzed recording is required.");
        if (!exactWorkspace) missing.Add("Load this recording to verify its exact analysis and setup identity.");
        if (!open) blockers.Add("This fixed-setup recording may supply driving evidence, but it cannot be the target of a garage recommendation.");
        if (exactWorkspace && !embedded) missing.Add("The recording does not contain an embedded setup tree.");
        if (exactWorkspace && !fingerprint) missing.Add("The embedded setup fingerprint is unavailable.");
        if (exactWorkspace && !exactIdentity) missing.Add("The exact analysis or track-configuration identity is unavailable.");

        return new TuningRaceCandidate(
            race,
            identity,
            new TuningEligibilityView
            {
                CanUseAsEvidence = finalized,
                CanReceiveGarageRecommendation = finalized && open && embedded && fingerprint && exactIdentity,
                IsFinalized = finalized,
                IsOpenSetup = open,
                EmbeddedSetupAvailable = embedded,
                HasSetupFingerprint = fingerprint,
                ExactIdentityAvailable = exactIdentity,
                MissingRequired = missing,
                Blockers = blockers
            },
            !exactWorkspace);
    }

    public static TuningSetupTarget? OpenTarget(TuningRaceCandidate candidate) =>
        candidate.Eligibility.CanReceiveGarageRecommendation
            ? new TuningSetupTarget(
                TuningIdentityKey(candidate.Identity),
                candidate.Identity,
                true,
                [])
            : null;

    public static bool CompatibleOpenTarget(TuningSessionIdentity evidence, TuningSessionIdentity target)
    {
        if (!string.Equals(target.SetupType, "Open", StringComparison.OrdinalIgnoreCase)
            || !target.EmbeddedSetupAvailable
            || string.IsNullOrWhiteSpace(target.SetupFingerprint)
            || string.IsNullOrWhiteSpace(evidence.TrackConfigurationKey)
            || string.IsNullOrWhiteSpace(target.TrackConfigurationKey)
            || !string.Equals(evidence.TrackConfigurationKey, target.TrackConfigurationKey, StringComparison.Ordinal))
            return false;

        var carMatches = !string.IsNullOrWhiteSpace(evidence.CarId) && !string.IsNullOrWhiteSpace(target.CarId)
            ? string.Equals(evidence.CarId, target.CarId, StringComparison.Ordinal)
            : !string.IsNullOrWhiteSpace(evidence.CarPath) && !string.IsNullOrWhiteSpace(target.CarPath)
                && string.Equals(evidence.CarPath, target.CarPath, StringComparison.OrdinalIgnoreCase);
        return carMatches;
    }

    public static TuningSessionIdentity Bind(TuningSessionIdentity identity, RecentRace race) => identity with
    {
        RaceId = race.Id,
        EventKey = race.EventKey,
        Selector = race.EffectiveSelector,
        AnalysisPath = string.IsNullOrWhiteSpace(identity.AnalysisPath) ? race.AnalysisPath : identity.AnalysisPath,
        SessionType = string.IsNullOrWhiteSpace(identity.SessionType) ? race.SessionType : identity.SessionType,
        Track = string.IsNullOrWhiteSpace(identity.Track) ? race.Track : identity.Track,
        Layout = string.IsNullOrWhiteSpace(identity.Layout) ? race.Layout : identity.Layout,
        CarPath = string.IsNullOrWhiteSpace(identity.CarPath) ? race.CarPath : identity.CarPath,
        Car = string.IsNullOrWhiteSpace(identity.Car) ? race.Car : identity.Car,
        SetupType = string.Equals(identity.SetupType, "Unknown", StringComparison.OrdinalIgnoreCase)
            ? race.SetupType
            : identity.SetupType
    };

    public static TuningSessionIdentity FromRace(RecentRace race) => new()
    {
        RaceId = race.Id,
        EventKey = race.EventKey,
        Selector = race.EffectiveSelector,
        AnalysisPath = race.AnalysisPath,
        SessionType = race.SessionType,
        Track = race.Track,
        Layout = race.Layout,
        CarPath = race.CarPath,
        Car = race.Car,
        SetupType = string.IsNullOrWhiteSpace(race.SetupType) ? "Unknown" : race.SetupType
    };

    public static bool Matches(RecentRace race, TuningSessionIdentity? identity)
    {
        if (identity is null) return false;
        if (!SameContext(race, identity)) return false;

        var exactMarkerCompared = false;
        if (!string.IsNullOrWhiteSpace(race.AnalysisPath) && !string.IsNullOrWhiteSpace(identity.AnalysisPath))
        {
            exactMarkerCompared = true;
            if (!SamePath(race.AnalysisPath, identity.AnalysisPath)) return false;
        }
        if (!string.IsNullOrWhiteSpace(identity.RaceId))
        {
            exactMarkerCompared = true;
            if (!string.Equals(race.Id, identity.RaceId, StringComparison.Ordinal)) return false;
        }
        if (!string.IsNullOrWhiteSpace(race.EventKey) && !string.IsNullOrWhiteSpace(identity.EventKey))
        {
            exactMarkerCompared = true;
            if (!string.Equals(race.EventKey, identity.EventKey, StringComparison.Ordinal)) return false;
        }
        if (!string.IsNullOrWhiteSpace(race.EffectiveSelector) && !string.IsNullOrWhiteSpace(identity.Selector))
        {
            exactMarkerCompared = true;
            if (!string.Equals(race.EffectiveSelector, identity.Selector, StringComparison.Ordinal)) return false;
        }
        return exactMarkerCompared;
    }

    public static string TuningIdentityKey(TuningSessionIdentity identity)
    {
        var canonical = string.Join('|', new[]
        {
            identity.AnalysisId,
            identity.SessionUniqueId,
            identity.SubsessionId,
            identity.SessionType,
            identity.TrackConfigurationKey,
            identity.CarId,
            identity.CarPath,
            identity.SetupFingerprint,
            string.Join(',', identity.SourceSha256.OrderBy(value => value, StringComparer.OrdinalIgnoreCase)),
            identity.RaceId,
            identity.EventKey,
            identity.Selector
        }.Select(value => value?.Trim().ToLowerInvariant() ?? string.Empty));
        return Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(canonical))).ToLowerInvariant();
    }

    public static string SetupLineageWorkflowKey(TuningSessionIdentity identity)
    {
        var lineage = string.Join("|", new[] { identity.CarPath, identity.TrackConfigurationKey, identity.SetupFingerprint });
        var digest = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(lineage.ToLowerInvariant()))).ToLowerInvariant();
        return $"progressive-tuning:{digest[..24]}";
    }

    public static string GeometryHash(TuningMapView map)
    {
        var value = map.GeometryHash?.Trim().ToLowerInvariant() ?? string.Empty;
        return value.Length == 64 && value.All(Uri.IsHexDigit) ? value : string.Empty;
    }

    public static TuningMapSubmissionIdentity BuildMapSubmission(
        TuningSessionIdentity identity,
        TuningMapView map,
        IReadOnlyList<TuningTurnCorrectionDraft> corrections)
    {
        if (string.IsNullOrWhiteSpace(identity.TrackConfigurationKey))
            throw new InvalidOperationException("Exact track configuration identity is required for structured tuning.");
        if (string.IsNullOrWhiteSpace(map.MapIdentity) || map.Path.Count < 2)
            throw new InvalidOperationException("A stable track map identity and geometry are required for structured tuning.");
        var geometryHash = GeometryHash(map);
        if (geometryHash.Length == 0)
            throw new InvalidOperationException("The authoritative analysis geometry hash is unavailable. Reanalyze this recording before structured tuning.");
        var sourceType = CanonicalMapSourceType(map.SourceType);
        var corners = map.Turns
            .OrderBy(turn => Math.Round(turn.StartPct, 6, MidpointRounding.ToEven))
            .ThenBy(turn => Math.Round(turn.ApexPct, 6, MidpointRounding.ToEven))
            .ThenBy(turn => Math.Round(turn.EndPct, 6, MidpointRounding.ToEven))
            .ThenBy(turn => turn.CornerId, StringComparer.Ordinal)
            .Select(turn => new TuningMapCornerIdentity(
                turn.CornerId,
                turn.Label,
                Math.Round(turn.StartPct, 6, MidpointRounding.ToEven),
                Math.Round(turn.ApexPct, 6, MidpointRounding.ToEven),
                Math.Round(turn.EndPct, 6, MidpointRounding.ToEven),
                turn.IsOfficial,
                turn.UserVerified)).ToArray();
        var annotationHash = MapAnnotationHash(identity.TrackConfigurationKey, geometryHash, sourceType, corners);
        return new TuningMapSubmissionIdentity(
            identity.TrackConfigurationKey,
            map.MapIdentity,
            geometryHash,
            annotationHash,
            sourceType,
            map.SourceLabel,
            map.SourceUrl,
            map.IsVerified,
            corners);
    }

    public static string MapAnnotationHash(
        string trackConfigurationKey,
        string geometryHash,
        string sourceType,
        IReadOnlyList<TuningMapCornerIdentity> corners)
    {
        using var stream = new MemoryStream();
        using (var writer = new Utf8JsonWriter(stream, new JsonWriterOptions
        {
            Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
            Indented = false
        }))
        {
            // Key order matches Python json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False).
            writer.WriteStartObject();
            writer.WritePropertyName("corners");
            writer.WriteStartArray();
            foreach (var corner in corners
                .OrderBy(item => item.StartPct)
                .ThenBy(item => item.ApexPct)
                .ThenBy(item => item.EndPct)
                .ThenBy(item => item.CornerId, StringComparer.Ordinal))
            {
                writer.WriteStartObject();
                writer.WriteNumber("apex_pct", Math.Round(corner.ApexPct, 6, MidpointRounding.ToEven));
                writer.WriteString("corner_id", corner.CornerId);
                writer.WriteNumber("end_pct", Math.Round(corner.EndPct, 6, MidpointRounding.ToEven));
                writer.WriteBoolean("is_official", corner.IsOfficial);
                writer.WriteString("label", corner.Label);
                writer.WriteNumber("start_pct", Math.Round(corner.StartPct, 6, MidpointRounding.ToEven));
                writer.WriteBoolean("user_verified", corner.UserVerified);
                writer.WriteEndObject();
            }
            writer.WriteEndArray();
            writer.WriteString("geometry_hash", geometryHash.Trim().ToLowerInvariant());
            writer.WriteString("source_type", CanonicalMapSourceType(sourceType));
            writer.WriteString("track_configuration_key", trackConfigurationKey);
            writer.WriteEndObject();
        }
        return Convert.ToHexString(SHA256.HashData(stream.ToArray())).ToLowerInvariant();
    }

    private static string CanonicalMapSourceType(string value) => value.Trim().ToLowerInvariant() switch
    {
        "official-iracing" or "iracing-game" => "iracing-official",
        "official-nascar" => "nascar-official",
        "official-track" => "venue-official",
        "user-confirmed" => "verified-manual",
        var normalized => normalized
    };

    public static TuningAiEvidenceView BuildAiEvidence(StructuredTuningResultView result, TuningSessionIdentity identity) => new(
        SetupLineageWorkflowKey(identity),
        result.ExperimentId,
        result.Eligibility,
        result.Evidence,
        result.CandidateWhitelist,
        result.Limitations);

    public static BoundedTuningAiRequest? BuildBoundedAiRequest(
        StructuredTuningResultView deterministic,
        TuningSessionIdentity identity)
    {
        const int maximumBytes = 64 * 1024;
        var evidence = BuildAiEvidence(deterministic, identity);
        var evidenceById = evidence.Evidence
            .Where(item => !string.IsNullOrWhiteSpace(item.EvidenceId))
            .GroupBy(item => item.EvidenceId, StringComparer.Ordinal)
            .ToDictionary(group => group.Key, group => group.First(), StringComparer.Ordinal);
        var candidates = evidence.CandidateWhitelist
            .Take(20)
            .Select(item => new
            {
                candidate_id = item.CandidateId,
                system = item.System,
                change = item.Change,
                predicted_effect = item.PredictedEffect,
                risk = item.Risk,
                verify = item.Verify.Take(20).ToArray(),
                source = item.Source,
                confidence = item.Confidence,
                evidence_ids = item.EvidenceIds
                    .Where(evidenceById.ContainsKey)
                    .Distinct(StringComparer.Ordinal)
                    .Take(24)
                    .ToArray(),
                conflicts = item.Conflicts.Take(12).ToArray()
            })
            .Where(item => item.candidate_id.Length > 0 && item.evidence_ids.Length > 0)
            .ToArray();
        if (candidates.Length == 0) return null;

        var candidateIds = candidates.Select(item => item.candidate_id).ToHashSet(StringComparer.Ordinal);
        var evidenceIds = candidates.SelectMany(item => item.evidence_ids).ToHashSet(StringComparer.Ordinal);
        var compactEvidence = evidence.Evidence
            .Where(item => evidenceIds.Contains(item.EvidenceId))
            .Select(item => new
            {
                evidence_id = item.EvidenceId,
                kind = item.Evidence.ToString().ToLowerInvariant(),
                label = item.Label,
                value = item.Value,
                unit = item.Unit,
                source = item.Source,
                limitation = item.Limitation
            })
            .ToArray();
        var json = JsonSerializer.Serialize(new
        {
            contract = "tuning_ai_request_v1",
            workflow_key = evidence.WorkflowKey,
            experiment_id = evidence.ExperimentId,
            eligibility = new
            {
                can_use_as_driving_evidence = evidence.Eligibility.CanUseAsEvidence,
                can_receive_garage_recommendation = evidence.Eligibility.CanReceiveGarageRecommendation,
                exact_identity_available = evidence.Eligibility.ExactIdentityAvailable,
                missing_required = evidence.Eligibility.MissingRequired,
                blockers = evidence.Eligibility.Blockers
            },
            evidence = compactEvidence,
            candidate_whitelist = candidates,
            limitations = evidence.Limitations.Take(30).ToArray()
        });
        return Encoding.UTF8.GetByteCount(json) <= maximumBytes
            ? new BoundedTuningAiRequest(json, candidateIds, evidenceIds)
            : null;
    }

    public static TuningAiSelectionValidation ValidateAiSelection(
        StructuredTuningResultView deterministic,
        TuningAiSelection selection,
        IReadOnlySet<string>? suppliedCandidateIds = null,
        IReadOnlySet<string>? suppliedEvidenceIds = null)
    {
        var errors = new List<string>();
        var summary = selection.Summary?.Trim() ?? string.Empty;
        var evidenceIds = selection.EvidenceIds ?? [];
        var conflicts = selection.Conflicts ?? [];
        var confidenceReasons = selection.ConfidenceReasons ?? [];
        var candidate = deterministic.CandidateWhitelist.FirstOrDefault(item =>
            string.Equals(item.CandidateId, selection.SelectedCandidateId, StringComparison.Ordinal));
        if (candidate is null)
            errors.Add("The AI selected a candidate outside the deterministic whitelist.");
        else if (suppliedCandidateIds is not null && !suppliedCandidateIds.Contains(candidate.CandidateId))
            errors.Add("The AI selected a candidate that was not included in its bounded request.");
        if (summary.Length is < 1 or > 1200)
            errors.Add("The AI summary must contain 1-1200 characters.");

        // Evidence is not interchangeable across allowed changes. A citation
        // must both exist in the deterministic contract and be explicitly linked
        // to the candidate the Coach selected.
        var knownEvidence = deterministic.Evidence.Select(item => item.EvidenceId).ToHashSet(StringComparer.Ordinal);
        var candidateEvidence = candidate?.EvidenceIds.ToHashSet(StringComparer.Ordinal)
            ?? new HashSet<string>(StringComparer.Ordinal);
        if (evidenceIds.Count is < 1 or > 24)
            errors.Add("The AI selection did not cite deterministic evidence.");
        if (evidenceIds.Any(item => string.IsNullOrWhiteSpace(item) || item.Length > 160)
            || evidenceIds.Distinct(StringComparer.Ordinal).Count() != evidenceIds.Count)
            errors.Add("The AI evidence IDs are malformed or duplicated.");
        foreach (var evidenceId in evidenceIds.Distinct(StringComparer.Ordinal))
        {
            if (!knownEvidence.Contains(evidenceId))
                errors.Add($"Unknown evidence ID: {evidenceId}");
            else if (!candidateEvidence.Contains(evidenceId))
                errors.Add($"Evidence ID is not linked to the selected candidate: {evidenceId}");
            else if (suppliedEvidenceIds is not null && !suppliedEvidenceIds.Contains(evidenceId))
                errors.Add($"Evidence ID was not included in the bounded request: {evidenceId}");
        }
        if (conflicts.Count > 12
            || conflicts.Any(item => string.IsNullOrWhiteSpace(item) || item.Length > 500))
            errors.Add("The AI conflict list is malformed or too long.");
        if (confidenceReasons.Count is < 1 or > 12
            || confidenceReasons.Any(item => string.IsNullOrWhiteSpace(item) || item.Length > 500))
            errors.Add("The AI confidence reasons are malformed or missing.");
        var normalized = errors.Count == 0
            ? selection with
            {
                Summary = summary,
                EvidenceIds = evidenceIds.Select(item => item.Trim()).ToArray(),
                Conflicts = conflicts.Select(item => item.Trim()).ToArray(),
                ConfidenceReasons = confidenceReasons.Select(item => item.Trim()).ToArray()
            }
            : null;
        return new TuningAiSelectionValidation(errors.Count == 0, normalized, errors);
    }

    private static bool SameContext(RecentRace race, TuningSessionIdentity identity) =>
        Same(race.Track, identity.Track)
        && Same(race.Layout, identity.Layout)
        && Same(race.Car, identity.Car)
        && Same(race.SessionType, identity.SessionType);

    private static bool Same(string left, string right) =>
        string.IsNullOrWhiteSpace(left)
        || string.IsNullOrWhiteSpace(right)
        || string.Equals(left.Trim(), right.Trim(), StringComparison.OrdinalIgnoreCase);

    private static bool SamePath(string left, string right)
    {
        try { return string.Equals(Path.GetFullPath(left), Path.GetFullPath(right), StringComparison.OrdinalIgnoreCase); }
        catch (Exception ex) when (ex is ArgumentException or NotSupportedException or PathTooLongException)
        {
            return string.Equals(left, right, StringComparison.OrdinalIgnoreCase);
        }
    }
}

public sealed class PortableTuningDraftStore
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web) { WriteIndented = true };
    private static readonly ConcurrentDictionary<string, SemaphoreSlim> WriteGates = new(StringComparer.OrdinalIgnoreCase);
    private readonly string _draftRoot;

    public PortableTuningDraftStore(string coachHome)
    {
        var root = Path.GetFullPath(coachHome);
        _draftRoot = Path.GetFullPath(Path.Combine(root, "portable-settings", "tuning-drafts"));
        if (!_draftRoot.StartsWith(root.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("The tuning draft path escaped the Coach archive.");
    }

    public string PathFor(TuningSessionIdentity identity) =>
        Path.Combine(_draftRoot, ProgressiveTuningCoordinator.TuningIdentityKey(identity) + ".json");

    public ProgressiveTuningDraft? Load(TuningSessionIdentity identity)
    {
        var path = PathFor(identity);
        if (!File.Exists(path)) return null;
        try
        {
            return JsonSerializer.Deserialize<ProgressiveTuningDraft>(File.ReadAllText(path), JsonOptions);
        }
        catch (JsonException ex)
        {
            throw new InvalidDataException("The saved tuning draft is not valid JSON.", ex);
        }
    }

    public async Task SaveAsync(ProgressiveTuningDraft draft, CancellationToken cancellationToken = default)
    {
        var path = PathFor(draft.RepresentativeSession);
        var gate = WriteGates.GetOrAdd(path, static _ => new SemaphoreSlim(1, 1));
        await gate.WaitAsync(cancellationToken);
        string? temporary = null;
        try
        {
            Directory.CreateDirectory(_draftRoot);
            temporary = path + $".{Guid.NewGuid():N}.tmp";
            var bytes = new UTF8Encoding(false).GetBytes(JsonSerializer.Serialize(draft, JsonOptions));
            await using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None, 16_384, FileOptions.Asynchronous))
            {
                await stream.WriteAsync(bytes, cancellationToken);
                await stream.FlushAsync(cancellationToken);
                stream.Flush(flushToDisk: true);
            }
            if (File.Exists(path)) File.Replace(temporary, path, null, ignoreMetadataErrors: true);
            else File.Move(temporary, path);
            temporary = null;
        }
        finally
        {
            if (temporary is not null)
            {
                try { File.Delete(temporary); }
                catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
            }
            gate.Release();
        }
    }
}

public sealed class PortableTuningTurnAnnotationStore
{
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web) { WriteIndented = true };
    private static readonly ConcurrentDictionary<string, SemaphoreSlim> WriteGates = new(StringComparer.OrdinalIgnoreCase);
    private readonly string _root;

    public PortableTuningTurnAnnotationStore(string coachHome)
    {
        var portableRoot = Path.GetFullPath(coachHome);
        _root = Path.GetFullPath(Path.Combine(portableRoot, "portable-settings", "tuning-turn-annotations"));
        if (!_root.StartsWith(portableRoot.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("The tuning turn annotation path escaped the Coach home.");
    }

    public string PathFor(string trackConfigurationKey, string mapIdentity)
    {
        if (string.IsNullOrWhiteSpace(trackConfigurationKey) || string.IsNullOrWhiteSpace(mapIdentity))
            throw new InvalidOperationException("Exact track configuration and map identity are required for turn annotations.");
        var key = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(
            $"{trackConfigurationKey.Trim()}|{mapIdentity.Trim()}"))).ToLowerInvariant();
        return Path.Combine(_root, key + ".json");
    }

    public TuningTurnAnnotationSet? Load(string trackConfigurationKey, string mapIdentity)
    {
        var path = PathFor(trackConfigurationKey, mapIdentity);
        if (!File.Exists(path)) return null;
        try
        {
            return JsonSerializer.Deserialize<TuningTurnAnnotationSet>(File.ReadAllText(path), JsonOptions);
        }
        catch (JsonException ex)
        {
            throw new InvalidDataException("The saved turn annotations are not valid JSON.", ex);
        }
    }

    public async Task SaveAsync(TuningTurnAnnotationSet annotations, CancellationToken cancellationToken = default)
    {
        var path = PathFor(annotations.TrackConfigurationKey, annotations.MapIdentity);
        var gate = WriteGates.GetOrAdd(path, static _ => new SemaphoreSlim(1, 1));
        await gate.WaitAsync(cancellationToken);
        string? temporary = null;
        try
        {
            Directory.CreateDirectory(_root);
            temporary = path + $".{Guid.NewGuid():N}.tmp";
            var bytes = new UTF8Encoding(false).GetBytes(JsonSerializer.Serialize(annotations, JsonOptions));
            await using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None, 16_384, FileOptions.Asynchronous))
            {
                await stream.WriteAsync(bytes, cancellationToken);
                await stream.FlushAsync(cancellationToken);
                stream.Flush(flushToDisk: true);
            }
            if (File.Exists(path)) File.Replace(temporary, path, null, ignoreMetadataErrors: true);
            else File.Move(temporary, path);
            temporary = null;
        }
        finally
        {
            if (temporary is not null)
            {
                try { File.Delete(temporary); }
                catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { }
            }
            gate.Release();
        }
    }

    public static TuningMapView Merge(TuningMapView map, TuningTurnAnnotationSet? annotations)
    {
        if (annotations is null
            || !string.Equals(annotations.MapIdentity, map.MapIdentity, StringComparison.Ordinal)
            || annotations.Corrections.Count == 0)
            return map;
        var geometryHash = ProgressiveTuningCoordinator.GeometryHash(map);
        if (geometryHash.Length == 0)
            return map with { VerificationMessage = "Reanalyze this recording before applying saved turn corrections." };
        var corrections = annotations.Corrections
            .Where(item => string.Equals(item.MapIdentity, map.MapIdentity, StringComparison.Ordinal)
                && string.Equals(item.GeometryHash, geometryHash, StringComparison.Ordinal))
            .GroupBy(item => item.CornerId, StringComparer.Ordinal)
            .ToDictionary(group => group.Key, group => group.OrderBy(item => item.VerifiedAt).Last(), StringComparer.Ordinal);
        var turns = map.Turns.Select(turn => corrections.TryGetValue(turn.CornerId, out var correction)
            ? turn with
            {
                Label = correction.Label,
                StartPct = correction.StartPct,
                ApexPct = correction.ApexPct,
                EndPct = correction.EndPct,
                IsOfficial = turn.IsOfficial && string.Equals(turn.Label, correction.Label, StringComparison.Ordinal),
                CorrectionHint = correction.Note,
                UserVerified = true,
                VerificationSource = "Driver correction"
            }
            : turn).ToArray();
        // An official corner label does not prove that its telemetry bounds are
        // authoritative.  A driver correction must verify every mapped turn
        // before a telemetry-aligned map can become tuning evidence.
        var allTurnsVerified = turns.Length > 0 && turns.All(turn => turn.UserVerified);
        return map with
        {
            Turns = turns,
            IsVerified = map.IsVerified || allTurnsVerified,
            VerificationMessage = map.IsVerified || allTurnsVerified
                ? map.VerificationMessage
                : annotations.Corrections.Any(item => !string.Equals(item.GeometryHash, geometryHash, StringComparison.Ordinal))
                    ? "Saved turn corrections belong to older geometry and were not applied."
                    : map.VerificationMessage
        };
    }
}
