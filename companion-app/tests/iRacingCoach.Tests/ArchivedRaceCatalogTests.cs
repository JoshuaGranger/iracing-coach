using System.Text.Json;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class ArchivedRaceCatalogTests
{
    [TestMethod]
    public void CatalogReusesProjectionAcrossInstancesAndRefreshesChangedReports()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-catalog-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        var report = Path.Combine(root, "analysis.json");
        var cache = Path.Combine(root, "cache");
        void WriteReport(string track) => File.WriteAllText(report, JsonSerializer.Serialize(new
        {
            analysis_id = "test", analysis_profile_version = "post-race-foundations-v20",
            identity = new { track_name = track, event_type = "Race" },
            race_summary = new { recorded_laps = 10 }
        }));
        try
        {
            WriteReport("First track");
            var writes = 0;
            var first = new ArchivedRaceCatalog().Read(report, cache, () => writes++);
            Assert.IsNotNull(first);
            Assert.AreEqual("First Track", first.Race.Track);
            Assert.AreEqual(1, writes);
            using (var locked = new FileStream(report, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
            {
                var again = new ArchivedRaceCatalog().Read(report, cache, () => writes++);
                Assert.IsNotNull(again);
                Assert.AreEqual(first.Race, again.Race, "A warm catalog must not reopen the large report.");
            }
            Assert.AreEqual(1, writes, "A new app instance should read the small projection.");
            WriteReport("Replacement Speedway");
            var updated = new ArchivedRaceCatalog().Read(report, cache, () => writes++);
            Assert.IsNotNull(updated);
            Assert.AreEqual("Replacement Speedway", updated.Race.Track);
            Assert.AreEqual(2, writes);
            File.WriteAllText(Directory.GetFiles(cache).Single(), "{broken");
            var repaired = new ArchivedRaceCatalog().Read(report, cache, () => writes++);
            Assert.IsNotNull(repaired);
            Assert.AreEqual(updated.Race, repaired.Race);
            Assert.AreEqual(3, writes, "Corrupt projections must rebuild from the original report.");
        }
        finally { Directory.Delete(root, recursive: true); }
    }

    [TestMethod]
    public void InvalidReportIsReadOnceUntilItsRevisionChanges()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-catalog-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        var report = Path.Combine(root, "analysis.json");
        var cache = Path.Combine(root, "cache");
        var reader = new ArchivedRaceCatalog();
        try
        {
            File.WriteAllText(report, "{\"total_time_s\":Infinity}");
            Assert.Throws<JsonException>(() => reader.Read(report, cache, () => { }));
            using (var locked = new FileStream(report, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
                for (var refresh = 0; refresh < 100; refresh++)
                    Assert.IsNull(reader.Read(report, cache, () => Assert.Fail("Invalid data must not replace a valid projection.")));

            File.WriteAllText(report, "{\"identity\":{\"track_name\":\"Repaired Speedway\"},\"race_summary\":{\"recorded_laps\":159}}");
            var repaired = reader.Read(report, cache, () => { });
            Assert.IsNotNull(repaired);
            Assert.AreEqual("Repaired Speedway", repaired.Race.Track);

            File.WriteAllText(report, "{broken again}");
            Assert.Throws<JsonException>(() => reader.Read(report, cache, () => { }));
            Assert.IsNull(reader.Read(report, cache, () => { }));
        }
        finally { Directory.Delete(root, recursive: true); }
    }
}
