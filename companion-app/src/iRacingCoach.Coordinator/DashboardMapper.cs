using System.Globalization;
using System.Text.Json;
using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

public static class DashboardMapper
{
    public static IReadOnlyList<RecentRace> Map(JsonElement dashboard)
    {
        if (!dashboard.TryGetProperty("ok", out var ok) || ok.ValueKind != JsonValueKind.True)
        {
            throw new InvalidDataException("The dashboard response did not confirm a successful read.");
        }

        if (!dashboard.TryGetProperty("races", out var races) || races.ValueKind != JsonValueKind.Array)
        {
            throw new InvalidDataException("The dashboard response did not include a races array.");
        }

        return races.EnumerateArray().Select(race => MapSession(race, race)).ToArray();
    }

    public static IReadOnlyList<RecentRace> MapEvents(JsonElement dashboard, JsonElement discovery)
    {
        var dashboardRaces = dashboard.TryGetProperty("races", out var races) && races.ValueKind == JsonValueKind.Array
            ? races.EnumerateArray().ToArray()
            : [];
        if (!discovery.TryGetProperty("sessions", out var sessions) || sessions.ValueKind != JsonValueKind.Array)
        {
            return dashboardRaces.Select(race => MapSession(race, race)).OrderByDescending(SortTime).ToArray();
        }

        var mapped = new List<RecentRace>();
        foreach (var session in sessions.EnumerateArray())
        {
            var match = MatchDashboardRace(session, dashboardRaces);
            mapped.Add(MapSession(session, match.ValueKind == JsonValueKind.Object ? match : session));
        }

        foreach (var dashboardRace in dashboardRaces)
        {
            var candidate = MapSession(dashboardRace, dashboardRace);
            if (mapped.All(item => !string.Equals(item.Id, candidate.Id, StringComparison.OrdinalIgnoreCase)))
            {
                mapped.Add(candidate);
            }
        }

        return mapped.OrderByDescending(SortTime).ToArray();
    }

    public static IReadOnlyList<RaceEventGroup> GroupEvents(IEnumerable<RecentRace> sessions) =>
        sessions
            .GroupBy(session => string.IsNullOrWhiteSpace(session.EventKey) ? session.Id : session.EventKey, StringComparer.OrdinalIgnoreCase)
            .Select(group =>
            {
                var ordered = group.OrderBy(SessionOrder).ThenByDescending(SortTime).ToArray();
                var primary = ordered.FirstOrDefault(session => session.IsRace) ?? ordered[0];
                return new RaceEventGroup(
                    group.Key,
                    primary.Track,
                    primary.Layout,
                    primary.Car,
                    primary.Date,
                    primary.SetupType,
                    primary.EventScope,
                    ordered);
            })
            .OrderByDescending(group => group.Sessions.Max(SortTime))
            .ToArray();

    private static RecentRace MapSession(JsonElement session, JsonElement dashboardRace)
    {
        var analysis = dashboardRace.TryGetProperty("analysis", out var value) && value.ValueKind == JsonValueKind.Object
            ? value
            : default;
        var summary = analysis.ValueKind == JsonValueKind.Object && analysis.TryGetProperty("summary", out var summaryValue)
            ? summaryValue
            : default;

        var groupId = Text(session, "group_id");
        var selector = groupId ?? GroupSelector(session) ?? Text(session, "subsession_id") ?? Text(session, "source_path") ?? FirstFile(session) ?? string.Empty;
        var id = groupId ?? selector;
        if (string.IsNullOrWhiteSpace(id)) id = Guid.NewGuid().ToString("N");
        var eventKey = Text(session, "subsession_id") ?? Text(session, "session_id") ?? id;
        var track = RuntimeMapper.DisplayTrack(Text(session, "track_name") ?? Text(session, "track_path")) ?? "Unknown track";
        var layout = RuntimeMapper.DisplayLayout(Text(session, "track_config_name") ?? Text(session, "track_config"));
        var car = RuntimeMapper.DisplayCar(Text(session, "car_name") ?? Text(session, "car_path")) ?? "Unknown car";
        var fixedSetup = Boolean(session, "is_fixed_setup");
        var sessionType = Text(session, "sim_session_type") ?? Text(session, "event_type") ?? "Recorded session";
        var isRace = string.Equals(sessionType, "Race", StringComparison.OrdinalIgnoreCase) || Boolean(session, "is_race") == true;
        var analyzed = isRace && string.Equals(Text(dashboardRace, "analysis_status"), "analyzed", StringComparison.OrdinalIgnoreCase);
        var recordedLaps = Integer(summary, "recorded_laps");
        var start = Integer(summary, "starting_position");
        var finish = Integer(summary, "final_recorded_position");
        var cautions = Integer(summary, "caution_laps_estimated");
        var greenLaps = Integer(summary, "green_laps_estimated");
        var pitStops = Integer(summary, "pit_stops_detected");
        var runs = Integer(summary, "runs_detected");
        var status = analyzed ? "Analyzed" : isRace ? "Needs analysis" : "Recorded";
        var summaryText = analyzed
            ? $"{Display(recordedLaps, "laps")} · {Position(start)} → {Position(finish)}"
            : isRace ? "Finalized race recording" : $"Finalized {sessionType.ToLowerInvariant()} recording";

        return new RecentRace(
            id,
            track,
            layout,
            car,
            LocalDate(Text(session, "start_time_utc")),
            fixedSetup == true ? "Fixed" : fixedSetup == false ? "Open" : "Unknown",
            status,
            summaryText,
            cautions > 0,
            analyzed,
            start,
            finish,
            Text(session, "car_path") ?? string.Empty,
            Text(analysis, "analysis_path") ?? string.Empty,
            Text(session, "source_path") ?? FirstFile(session) ?? string.Empty,
            Text(session, "start_time_utc") ?? string.Empty,
            eventKey,
            sessionType,
            EventScope(session),
            Math.Max(1, Integer(session, "file_count")),
            Text(session, "series_name") ?? string.Empty,
            Text(session, "season_name") ?? string.Empty,
            selector,
            new RaceOverview(recordedLaps, greenLaps, cautions, pitStops, runs,
                LongestGreenRun: Integer(summary, "longest_green_run"),
                PaceSlopeSecondsPerLap: Number(summary, "pace_slope_s_per_lap"),
                PaceConsistencyPercent: Number(summary, "pace_consistency_percent"),
                FuelUsedGallons: Number(summary, "fuel_used_gal"),
                BestCleanLapSeconds: Number(summary, "best_clean_lap_s")));
    }

    private static JsonElement MatchDashboardRace(JsonElement session, IReadOnlyList<JsonElement> dashboardRaces)
    {
        var groupId = Text(session, "group_id");
        var subsessionId = Text(session, "subsession_id");
        foreach (var race in dashboardRaces)
        {
            if (!string.IsNullOrWhiteSpace(groupId) && string.Equals(groupId, Text(race, "group_id"), StringComparison.OrdinalIgnoreCase))
            {
                return race;
            }
            if (Boolean(session, "is_race") == true && !string.IsNullOrWhiteSpace(subsessionId) &&
                string.Equals(subsessionId, Text(race, "subsession_id"), StringComparison.OrdinalIgnoreCase))
            {
                return race;
            }
        }
        return default;
    }

    private static string EventScope(JsonElement session)
    {
        var scope = Text(session, "event_scope") ?? Text(session, "event_kind");
        if (!string.IsNullOrWhiteSpace(scope)) return scope;
        if (Boolean(session, "is_ai") == true) return "AI";
        if (Boolean(session, "is_hosted") == true || Boolean(session, "is_league") == true) return "Hosted / League";
        if (Boolean(session, "is_official") == true) return "Official";
        return string.Empty;
    }

    private static string? FirstFile(JsonElement session)
    {
        if (session.ValueKind != JsonValueKind.Object || !session.TryGetProperty("files", out var files) || files.ValueKind != JsonValueKind.Array)
        {
            return null;
        }
        foreach (var file in files.EnumerateArray())
        {
            if (file.ValueKind == JsonValueKind.String) return file.GetString();
        }
        return null;
    }

    private static string? GroupSelector(JsonElement session)
    {
        var subsession = Text(session, "subsession_id");
        var simSession = Text(session, "sim_session_num");
        return string.IsNullOrWhiteSpace(subsession) || string.IsNullOrWhiteSpace(simSession)
            ? null
            : $"subsession:{subsession}:{simSession}";
    }

    private static string? Text(JsonElement element, string property)
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(property, out var value))
        {
            return null;
        }

        return value.ValueKind switch
        {
            JsonValueKind.String => value.GetString(),
            JsonValueKind.Number => value.GetRawText(),
            _ => null
        };
    }

    private static bool? Boolean(JsonElement element, string property) =>
        element.ValueKind == JsonValueKind.Object && element.TryGetProperty(property, out var value)
            ? value.ValueKind switch
            {
                JsonValueKind.True => true,
                JsonValueKind.False => false,
                _ => null
            }
            : null;

    private static int Integer(JsonElement element, string property)
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(property, out var value))
        {
            return 0;
        }
        if (value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var integer))
        {
            return integer;
        }
        return value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out var number)
            ? (int)Math.Round(number, MidpointRounding.AwayFromZero)
            : 0;
    }

    private static double? Number(JsonElement element, string property)
    {
        if (element.ValueKind != JsonValueKind.Object || !element.TryGetProperty(property, out var value) || value.ValueKind != JsonValueKind.Number)
        {
            return null;
        }
        return value.TryGetDouble(out var number) ? number : null;
    }

    private static string LocalDate(string? timestamp)
    {
        if (!DateTimeOffset.TryParse(timestamp, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out var date))
        {
            return "Recorded locally";
        }
        return date.ToLocalTime().ToString("MMM d · h:mm tt", CultureInfo.CurrentCulture);
    }

    private static string? Humanize(string? value)
    {
        if (string.IsNullOrWhiteSpace(value)) return null;
        var leaf = value.Replace('\\', '/').Split('/', StringSplitOptions.RemoveEmptyEntries).LastOrDefault() ?? value;
        return CultureInfo.CurrentCulture.TextInfo.ToTitleCase(leaf.Replace('_', ' ').Replace('-', ' '));
    }

    private static int SessionOrder(RecentRace session) => session.IsQualifying ? 0 : session.IsRace ? 1 : 2;
    private static DateTimeOffset SortTime(RecentRace race) =>
        DateTimeOffset.TryParse(race.StartTimeUtc, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal, out var value)
            ? value
            : DateTimeOffset.MinValue;
    private static string Display(int value, string suffix) => value > 0 ? $"{value} {suffix}" : $"Recorded {suffix}";
    private static string Position(int value) => value > 0 ? $"P{value}" : "P—";
}
