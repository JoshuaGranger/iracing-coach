using iRacingCoach.Contracts;

namespace iRacingCoach.UI;

/// <summary>
/// Formats only incident facts supplied by the analysis contract. Incident-point
/// values are never translated into a contact, loss-of-control, or off-track type.
/// </summary>
public static class IncidentPresentation
{
    private static readonly HashSet<string> GenericContactTypes = new(StringComparer.OrdinalIgnoreCase)
    {
        "contact",
        "collision",
        "impact"
    };

    public static string Describe(AnalysisIncident incident, bool includePoints)
    {
        ArgumentNullException.ThrowIfNull(incident);

        var facts = ExplicitFacts(incident);
        var parts = new List<string>(facts);
        if (includePoints || facts.Count == 0)
        {
            parts.Add($"x{incident.Points}");
        }

        if (incident.SessionTimeSeconds is { } sessionTime)
        {
            parts.Add(FormatSessionTime(sessionTime));
        }

        return string.Join(" \u00B7 ", parts);
    }

    public static bool IsMeasuredOffTrack(string? value) =>
        string.Equals(Normalize(value), "off track", StringComparison.OrdinalIgnoreCase);

    private static List<string> ExplicitFacts(AnalysisIncident incident)
    {
        var facts = new List<string>();
        var eventType = Normalize(incident.EventType);
        var contactTarget = Normalize(incident.ContactTarget);

        if (eventType.Length > 0)
        {
            if (GenericContactTypes.Contains(eventType) && contactTarget.Length > 0)
            {
                facts.Add(ContactFact(contactTarget));
            }
            else
            {
                facts.Add(Humanize(eventType));
                if (contactTarget.Length > 0 && !eventType.Contains(contactTarget, StringComparison.OrdinalIgnoreCase))
                {
                    facts.Add($"Contact: {Humanize(contactTarget)}");
                }
            }
        }
        else if (contactTarget.Length > 0)
        {
            facts.Add(ContactFact(contactTarget));
        }

        if (IsMeasuredOffTrack(incident.TrackLocation)
            && !eventType.Equals("off track", StringComparison.OrdinalIgnoreCase))
        {
            facts.Add("Off track");
        }

        return facts;
    }

    private static string ContactFact(string target) =>
        target.EndsWith(" contact", StringComparison.OrdinalIgnoreCase)
            ? Humanize(target)
            : $"{Humanize(target)} contact";

    private static string Normalize(string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return string.Empty;
        }

        return string.Join(' ', value
            .Trim()
            .Split([' ', '_', '-'], StringSplitOptions.RemoveEmptyEntries));
    }

    private static string Humanize(string value) =>
        value.Length == 0 ? string.Empty : char.ToUpperInvariant(value[0]) + value[1..];

    private static string FormatSessionTime(double seconds)
    {
        var time = TimeSpan.FromSeconds(Math.Max(0, seconds));
        return time.TotalHours >= 1 ? time.ToString(@"h\:mm\:ss\.f") : time.ToString(@"m\:ss\.f");
    }
}
