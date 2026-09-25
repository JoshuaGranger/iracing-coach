using System.Text.Json;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

/// <summary>
/// Test day in the coordinator: discovery tags group stints without merging them.
/// </summary>
public sealed partial class CoordinatorTests
{
    private const string TestDayTag = "test-day-0123456789abcdef";

    private static object OfflineStint(string id, string start, int? run, int? count, string track = "New Hampshire Motor Speedway") => new
    {
        group_id = id, subsession_id = 0, session_id = 0, sim_session_type = "Offline Testing", event_type = "Test", is_race = false,
        valid = true, is_fixed_setup = false, track_display_name = track, track_config_name = "Oval", car_name = "Toyota Supra Class B",
        car_path = "stockcars2 supra2019", start_time_utc = start, file_count = 1, files = new[] { id + ".ibt" },
        test_day_id = run is null ? null : TestDayTag, test_day_run = run, test_day_run_count = count
    };

    private static JsonElement TestDayDiscovery(params object[] sessions) => JsonSerializer.SerializeToElement(new { sessions });

    [TestMethod]
    public void TestDayStintsShareOneRowGroupButKeepTheirOwnRecordingIdentity()
    {
        var discovery = TestDayDiscovery(
            OfflineStint("local:stint-b", "2026-08-06T23:50:00Z", 2, 3),
            OfflineStint("local:stint-a", "2026-08-06T23:00:00Z", 1, 3),
            OfflineStint("local:stint-c", "2026-08-07T00:35:00Z", 3, 3),
            OfflineStint("local:lone", "2026-08-02T18:30:00Z", null, null),
            new
            {
                group_id = "subsession:9001:2", subsession_id = 9001, session_id = 0, sim_session_type = "Race", event_type = "Race", is_race = true,
                valid = true, is_fixed_setup = false, track_display_name = "New Hampshire Motor Speedway", track_config_name = "Oval",
                car_name = "Toyota Supra Class B", car_path = "stockcars2 supra2019", start_time_utc = "2026-08-07T02:09:00Z", file_count = 1,
                files = new[] { "race.ibt" }, test_day_id = TestDayTag, test_day_run = 4, test_day_run_count = 4
            });

        var sessions = DashboardMapper.MapEvents(JsonSerializer.SerializeToElement(new { races = Array.Empty<object>() }), discovery);
        var groups = DashboardMapper.GroupEvents(sessions);

        Assert.HasCount(3, groups, "The test day is one group; the lone stint and the race stay separate.");
        var day = groups.Single(group => group.IsTestDay);
        Assert.AreEqual(TestDayTag, day.Id);
        CollectionAssert.AreEqual(new[] { "local:stint-a", "local:stint-b", "local:stint-c" }, day.Sessions.Select(session => session.Id).ToArray(),
            "A test day lists its stints in the order they were driven.");
        foreach (var stint in day.Sessions)
            Assert.AreEqual(stint.Id, stint.EventKey, "The tag must never become the EventKey: SameRace would treat every stint as one recording.");
        Assert.HasCount(3, day.Sessions.Select(session => session.EventKey).Distinct().ToArray());
        Assert.IsNull(sessions.Single(session => session.IsRace).TestDayId, "A race is never part of a test day, even if tagged.");
        Assert.IsFalse(groups.Single(group => group.Sessions.Any(session => session.Id == "local:lone")).IsTestDay);
    }
}
