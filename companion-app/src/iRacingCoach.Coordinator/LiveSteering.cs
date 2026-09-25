using System.Globalization;
using System.Text.RegularExpressions;

namespace iRacingCoach.Coordinator;

/// <summary>
/// Live steering applied at the front wheels. The live SDK carries the same
/// <c>CarSetup</c> the recording will (O'Reilly Supra: <c>Chassis.Front.SteeringRatio:
/// 12:1</c>), so live steering uses the rules of the backend's <c>steering_basis.py</c>:
/// every <c>SteeringRatio</c> key in <c>CarSetup</c>, an <c>n:1</c> or bare ratio between 4
/// and 40, and nothing when it is absent, unreadable or stated twice differently. A missing
/// ratio is never guessed; live road-wheel steering is then unavailable and the recorded
/// steering-wheel angle stays available under its own name.
/// </summary>
public static partial class LiveSteering
{
    public const double MinimumRatio = 4.0;
    public const double MaximumRatio = 40.0;
    /// <summary>Wheel-angle thresholds become road-wheel thresholds divided by this, as in the backend.</summary>
    public const double ThresholdReferenceRatio = 12.0;

    public static double? ParseRatio(string? value)
    {
        if (string.IsNullOrWhiteSpace(value)) return null;
        var text = value.Trim();
        if (text.Length >= 2 && ((text[0] == '"' && text[^1] == '"') || (text[0] == '\'' && text[^1] == '\'')))
            text = text[1..^1];
        var match = RatioText().Match(text);
        if (!match.Success) match = NumberText().Match(text);
        if (!match.Success || !double.TryParse(match.Groups[1].Value, NumberStyles.Float, CultureInfo.InvariantCulture, out var ratio))
            return null;
        return double.IsFinite(ratio) && ratio is >= MinimumRatio and <= MaximumRatio ? ratio : null;
    }

    /// <summary>The one steering ratio the live <c>CarSetup</c> states, or null.</summary>
    public static double? RatioFromSessionInfo(string? sessionInfo)
    {
        if (string.IsNullOrEmpty(sessionInfo)) return null;
        var lines = sessionInfo.Replace("\r\n", "\n", StringComparison.Ordinal).Split('\n');
        var inSetup = false;
        var ratios = new List<double>();
        foreach (var line in lines)
        {
            if (line.Length > 0 && !char.IsWhiteSpace(line[0]) && !line.StartsWith('-'))
            {
                inSetup = line.TrimEnd().Equals("CarSetup:", StringComparison.Ordinal);
                continue;
            }
            if (!inSetup) continue;
            var pair = KeyValue().Match(line);
            if (!pair.Success) continue;
            var key = new string(pair.Groups["key"].Value.Where(char.IsLetterOrDigit).ToArray());
            if (!key.Equals("SteeringRatio", StringComparison.OrdinalIgnoreCase)) continue;
            if (ParseRatio(pair.Groups["value"].Value) is not { } ratio) return null;
            ratios.Add(ratio);
        }
        return ratios.Count > 0 && ratios.Distinct().Count() == 1 ? ratios[0] : null;
    }

    public static double? RoadWheelRadians(double? wheelRadians, double? ratio) =>
        wheelRadians is { } wheel && double.IsFinite(wheel) && ratio is > 0 ? wheel / ratio.Value : null;

    /// <summary>
    /// A near-centre wheel (0.12 rad of steering-wheel angle, or 0.12/12 rad of road-wheel
    /// angle when the ratio is known) for the safe-glance straight test.
    /// </summary>
    public static bool NearCenter(double? wheelRadians, double? ratio)
    {
        if (RoadWheelRadians(wheelRadians, ratio) is { } road) return Math.Abs(road) < 0.12 / ThresholdReferenceRatio;
        return wheelRadians is { } wheel && double.IsFinite(wheel) && Math.Abs(wheel) < 0.12;
    }

    [GeneratedRegex(@"^\s*(\d+(?:\.\d+)?)\s*:\s*1(?:\.0+)?\s*$", RegexOptions.CultureInvariant)]
    private static partial Regex RatioText();

    [GeneratedRegex(@"^\s*(\d+(?:\.\d+)?)\s*$", RegexOptions.CultureInvariant)]
    private static partial Regex NumberText();

    [GeneratedRegex(@"^\s+(?<key>[A-Za-z0-9 _]+):\s*(?<value>.*?)\s*$", RegexOptions.CultureInvariant)]
    private static partial Regex KeyValue();
}
