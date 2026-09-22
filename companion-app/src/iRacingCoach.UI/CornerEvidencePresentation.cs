using System.Text.RegularExpressions;

namespace iRacingCoach.UI;

public static partial class CornerEvidencePresentation
{
    // Expand only the backend's observed-value notation. Unknown prose and
    // target claims pass through unchanged; no values or targets are inferred.
    public static string Describe(string text)
    {
        var match = ObservedValues().Match(text);
        if (!match.Success) return text;
        string Value(string name, string unit) => match.Groups[name].Value is "-"
            ? "unavailable" : match.Groups[name].Value + unit;
        var result = $"Observed speed: entry {Value("entry", " mph")}; minimum {Value("minimum", " mph")}; exit {Value("exit", " mph")}. Peak brake: {Value("brake", "%")}.";
        if (!match.Groups["onset"].Success) return result;
        return result + $" Brake onset: {Timing(match.Groups["onset"].Value)}; brake release: {Timing(match.Groups["release"].Value)}; turn-in: {Timing(match.Groups["turn"].Value)}.";
    }

    private static string Timing(string value) => value switch
    {
        "-" => "unavailable",
        "c" => "not captured within this section",
        "n1" => "only one observation",
        _ => value + "% of lap"
    };

    [GeneratedRegex(@"^Obs E(?<entry>\d+(?:\.\d+)?|-)/M(?<minimum>\d+(?:\.\d+)?|-)/X(?<exit>\d+(?:\.\d+)?|-)(?: mph)?; B(?<brake>\d+(?:\.\d+)?|-)%?(?:; BO(?<onset>\d+(?:\.\d+)?|n1|c|-)/BR(?<release>\d+(?:\.\d+)?|n1|c|-)/TI(?<turn>\d+(?:\.\d+)?|n1|c|-))?$")]
    private static partial Regex ObservedValues();
}
