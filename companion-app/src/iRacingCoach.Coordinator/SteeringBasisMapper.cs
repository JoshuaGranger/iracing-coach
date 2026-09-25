using System.Text.Json;
using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

/// <summary>
/// Reads the backend's steering declarations (<c>steering_basis.py</c>). The backend decides
/// the basis from the recording's own steering ratio; this mapper never infers one. A
/// declaration that is not a usable road-wheel basis is read as steering-wheel angle,
/// whatever else it claims, so a road-wheel label can never sit over wheel-angle values.
/// </summary>
public static class SteeringBasisMapper
{
    public const string NotComparedLabel = "Steering — not compared";

    public static AnalysisSteeringBasis? Map(JsonElement item)
    {
        if (item.ValueKind != JsonValueKind.Object) return null;
        var ratio = Number(item, "steering_ratio");
        var ratioText = Text(item, "steering_ratio_text");
        var source = Text(item, "ratio_source");
        var offset = Text(item, "setup_steering_offset");
        var reason = Text(item, "reason");
        var definition = Text(item, "definition") ?? string.Empty;
        var status = Text(item, "status") ?? "not_recorded";
        if (Text(item, "basis") == AnalysisSteeringBasis.RoadWheelBasis && ratio is > 0)
            return new(AnalysisSteeringBasis.RoadWheelBasis, status, AnalysisSteeringBasis.RoadWheelLabel,
                AnalysisSteeringBasis.RoadWheelLabel, ratio, ratioText, source, offset, reason, definition);
        return new(AnalysisSteeringBasis.SteeringWheelBasis, status, WheelLabel(Text(item, "label")),
            AnalysisSteeringBasis.SteeringWheelLabel, null, ratioText, source, offset,
            reason ?? AnalysisSteeringBasis.Undeclared.Reason, definition);
    }

    /// <summary>A pass pair's steering declaration; absent on projections before sections-trials-v7.</summary>
    public static RaceSteeringPair? MapPair(JsonElement item)
    {
        if (item.ValueKind != JsonValueKind.Object) return null;
        var comparable = !(item.TryGetProperty("comparable", out var flag) && flag.ValueKind == JsonValueKind.False);
        if (!comparable)
            return new(null, NotComparedLabel, "Steering", 1, false,
                Text(item, "reason") ?? "Steering is not compared between these recordings.");
        if (Text(item, "basis") == AnalysisSteeringBasis.RoadWheelBasis)
            return new(AnalysisSteeringBasis.RoadWheelBasis, AnalysisSteeringBasis.RoadWheelLabel, AnalysisSteeringBasis.RoadWheelLabel, 2, true, null);
        return new(AnalysisSteeringBasis.SteeringWheelBasis, WheelLabel(Text(item, "label")),
            AnalysisSteeringBasis.SteeringWheelLabel, 1, true, null);
    }

    /// <summary>The backend's explicit fallback label ("Steering wheel angle — ratio not recorded").</summary>
    private static string WheelLabel(string? label) =>
        label is { } text && text.StartsWith(AnalysisSteeringBasis.SteeringWheelLabel, StringComparison.Ordinal)
            ? text
            : AnalysisSteeringBasis.Undeclared.Label;

    private static string? Text(JsonElement item, string name) =>
        item.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String ? value.GetString() : null;

    private static double? Number(JsonElement item, string name) =>
        item.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.Number &&
        value.TryGetDouble(out var number) && double.IsFinite(number) ? number : null;
}
