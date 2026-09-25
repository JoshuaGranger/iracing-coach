using System.Text.Json;
using System.Text.Json.Nodes;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

/// <summary>
/// The 9/24 IRP test day: the app started, then analysed the day's recordings in the
/// background. Their setups appeared in the library, but the setup files they loaded
/// still read "Values unknown · 0 linked recordings" and each recording's own setup said
/// "No recording uses this setup yet": the library is built by the inventory refresh,
/// which had run before the recordings were analysed.
/// </summary>
public sealed partial class CoordinatorTests
{
    [TestMethod]
    public async Task SetupLibraryRelinksOnceBackgroundAnalysisOfNewRecordingsFinishes()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-setup-relink", Guid.NewGuid().ToString("N"));
        var reports = Path.Combine(root, "data", "reports");
        string PathFor(string selector) => Path.Combine(reports, selector.Replace(':', '-'), "analysis.json");
        var stoPath = Path.Combine(root, "iRacing", "setups", "stockcars2 supra2019", "claude IRP test v2 LFcamber.sto");
        var backend = new FakeBackend(
            discovery: TestDayDiscovery(
                OfflineStint("local:v2", "2026-09-25T05:32:47Z", 1, 2, "Lucas Oil Indianapolis Raceway Park"),
                OfflineStint("local:v3", "2026-09-25T05:45:58Z", 2, 2, "Lucas Oil Indianapolis Raceway Park")),
            responseOverride: (tool, call, arguments) =>
            {
                var args = JsonSerializer.SerializeToElement(arguments);
                if (tool == "analyze_iracing_race")
                {
                    var selector = args.GetProperty("selector").GetString()!;
                    var response = WithRecordedSetup(HomeAnalysisResponse(PathFor(selector), "Offline Testing", selector), selector);
                    // The saved report is what the next refresh reads the recorded setup from.
                    var report = JsonNode.Parse(response.GetProperty("analysis_view").GetRawText())!.AsObject();
                    report["source"] = new JsonObject { ["selection"] = new JsonObject { ["group_id"] = selector, ["sim_session_type"] = "Offline Testing" } };
                    Directory.CreateDirectory(Path.GetDirectoryName(PathFor(selector))!);
                    File.WriteAllText(PathFor(selector), report.ToJsonString());
                    return response;
                }
                // The backend links the file only once an analysed recording names it.
                if (tool == "catalog_iracing_setups") return SetupFileCatalog(stoPath, call == 1 ? null : PathFor("local:v2"));
                return null;
            });
        using var state = new CompanionState(backend, new JsonSettingsStore(Path.Combine(root, "settings.json")));
        state.Settings.CoachHome = root;

        await state.RefreshDashboardAsync();
        await WaitUntilAsync(() => state.EventSessions.All(session => session.Analyzed), TimeSpan.FromSeconds(4));
        await WaitUntilAsync(() => backend.CallsFor("catalog_iracing_setups") >= 2 && !state.IsRefreshing, TimeSpan.FromSeconds(4));

        var file = state.Setups.Single(setup => setup.Name == "claude IRP test v2 LFcamber");
        Assert.AreEqual("recording", file.ValuesSource, "The library read the setup file's link again after the analyses.");
        Assert.HasCount(1, state.SetupEvidenceRaces(file));
        var recorded = state.Setups.Single(setup => setup.Name == "claude IRP test v2 LFcamber.sto");
        Assert.AreEqual("Offline testing", recorded.Role);
        Assert.HasCount(1, state.SetupEvidenceRaces(recorded), "The recording its setup came from is its evidence.");
        Assert.AreEqual(2, backend.CallsFor("catalog_iracing_setups"), "One refresh after the analyses, not one per recording.");
    }

    private static JsonElement WithRecordedSetup(JsonElement response, string selector)
    {
        var root = JsonNode.Parse(response.GetRawText())!.AsObject();
        var identity = root["analysis_view"]!["identity"]!.AsObject();
        var v2 = selector == "local:v2";
        identity["setup_name"] = v2 ? "claude IRP test v2 LFcamber.sto" : "claude IRP test v3 steer10.sto";
        identity["setup_fingerprint"] = v2 ? "c02d0f61d9698571" : "5f3bd0983c01c1b9";
        identity["garage_setup_fingerprint"] = v2 ? "47b8926d" : "6f82f3b7";
        identity["car_path"] = "stockcars2 supra2019";
        identity["session_start"] = v2 ? "2026-09-25T05:32:47Z" : "2026-09-25T05:45:58Z";
        identity["setup"] = new JsonObject { ["Chassis"] = new JsonObject { ["LeftFront"] = new JsonObject { ["Camber"] = v2 ? "+5.2 deg" : "+6.0 deg" } } };
        return JsonSerializer.SerializeToElement(root);
    }

    private static JsonElement SetupFileCatalog(string stoPath, string? linkedAnalysis) => JsonSerializer.SerializeToElement(new
    {
        ok = true,
        entries = new[]
        {
            new
            {
                stem = "claude IRP test v2 LFcamber", car_folder = "stockcars2 supra2019", pair_status = "sto_only",
                filename_identity = new { track_hint = (string?)null },
                sources = new { sto = new[] { new { path = stoPath, sha256 = "sto-v2" } } },
                recorded_setup = linkedAnalysis is null ? (object)new { schema_version = 1, status = "none" } : new
                {
                    schema_version = 1, status = "linked",
                    uses = new[] { new { analysis_path = linkedAnalysis, session_start = "2026-09-25T05:32:47Z", session_type = "Offline Testing" } },
                    values = new
                    {
                        source = "recording", analysis_path = linkedAnalysis, session_start = "2026-09-25T05:32:47Z", session_type = "Offline Testing",
                        garage_fingerprint = "47b8926d", setup = new { Chassis = new { LeftFront = new { Camber = "+5.2 deg" } } }, recordings_agree = true
                    }
                },
                values_status = linkedAnalysis is null ? (object)new { available = false, reason = "Values unknown." } : new { available = true, source = "recording" }
            }
        }
    });
}
