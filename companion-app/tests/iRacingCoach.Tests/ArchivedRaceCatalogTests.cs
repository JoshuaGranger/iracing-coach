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
            analysis_id = "test", analysis_profile_version = "post-race-foundations-v19",
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
            File.WriteAllText(report, "{\"identity\":{\"track_name\":\"Truncated");
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

    // 77 of 158 real archived analyses from an older analyzer carry a bare
    // "total_time_s": Infinity for an infeasible strategy. The fixture is a minimised
    // copy of one of them; the catalog must list it, not reject it.
    [TestMethod]
    public void OlderReportWithBareInfinityIsCatalogedWithoutRewritingIt()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-catalog-" + Guid.NewGuid().ToString("N"));
        var report = Path.Combine(root, "reports", "season-1", "subsession-1000001-2", "2026-09-21t00-07-38-00-00", "analysis.json");
        var cache = Path.Combine(root, "cache");
        Directory.CreateDirectory(Path.GetDirectoryName(report)!);
        File.Copy(Path.Combine(AppContext.BaseDirectory, "fixtures", "archived-analysis-nonfinite-v16.json"), report);
        var original = File.ReadAllBytes(report);
        StringAssert.Contains(System.Text.Encoding.UTF8.GetString(original), "\"total_time_s\": Infinity");
        try
        {
            var writes = 0;
            var entry = new ArchivedRaceCatalog().Read(report, cache, () => writes++);
            Assert.IsNotNull(entry, "A report with one unknown strategy time must still be listed.");
            Assert.AreEqual("World Wide Technology Raceway", entry.Race.Track);
            Assert.AreEqual("Race", entry.Race.SessionType);
            Assert.AreEqual(79, entry.Race.Overview!.RecordedLaps);
            Assert.AreEqual(2, entry.Race.Overview.PitStops);
            Assert.IsNotNull(entry.Setup, "The archived setup catalog shares this reader.");
            Assert.AreEqual("nonfinite-regression-setup", entry.Setup.Id);
            Assert.AreEqual(1, writes);
            CollectionAssert.AreEqual(original, File.ReadAllBytes(report), "The saved analysis must not be rewritten.");

            using (new FileStream(report, FileMode.Open, FileAccess.Read, FileShare.None))
            {
                var warm = new ArchivedRaceCatalog().Read(report, cache, () => writes++);
                Assert.IsNotNull(warm);
                Assert.AreEqual(entry.Race, warm.Race, "The next launch reads the small projection, not the report.");
            }
            Assert.AreEqual(1, writes);
        }
        finally { Directory.Delete(root, recursive: true); }
    }

    [TestMethod]
    public void UnreadableReportIsRememberedAcrossLaunchesUntilItChanges()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-catalog-" + Guid.NewGuid().ToString("N"));
        var reports = Path.Combine(root, "reports");
        var broken = Path.Combine(reports, "a", "analysis.json");
        var other = Path.Combine(reports, "b", "analysis.json");
        var cache = Path.Combine(root, "cache");
        var ledger = Path.Combine(root, "local-state", "catalog", "unreadable-analyses.json");
        Directory.CreateDirectory(Path.GetDirectoryName(broken)!);
        Directory.CreateDirectory(Path.GetDirectoryName(other)!);
        try
        {
            File.WriteAllText(broken, "{\"identity\":{\"track_name\":\"Truncated");
            File.WriteAllText(other, "{broken");
            var first = new ArchivedRaceCatalog();
            Assert.Throws<JsonException>(() => first.Read(broken, cache, () => { }, ledger));
            Assert.Throws<JsonException>(() => first.Read(other, cache, () => { }, ledger));
            Assert.IsTrue(File.Exists(ledger), "Unreadable revisions are remembered in app-owned local state.");
            Assert.HasCount(2, first.UnreadableReports(reports, ledger));

            // A new launch neither reopens nor re-reports an unchanged unreadable report.
            var second = new ArchivedRaceCatalog();
            using (new FileStream(broken, FileMode.Open, FileAccess.Read, FileShare.None))
                Assert.IsNull(second.Read(broken, cache, () => Assert.Fail("An unreadable report has no projection."), ledger));
            var remembered = second.UnreadableReports(reports, ledger);
            Assert.HasCount(2, remembered);
            Assert.IsTrue(remembered.All(report => !report.Acknowledged));

            second.AcknowledgeUnreadable([broken], ledger);
            var third = new ArchivedRaceCatalog();
            var acknowledged = third.UnreadableReports(reports, ledger);
            Assert.IsTrue(acknowledged.Single(report => report.Path == Path.GetFullPath(broken)).Acknowledged);
            Assert.IsFalse(acknowledged.Single(report => report.Path == Path.GetFullPath(other)).Acknowledged);

            // Repairing or removing a report forgets it.
            File.WriteAllText(broken, "{\"identity\":{\"track_name\":\"Repaired Speedway\"},\"race_summary\":{\"recorded_laps\":12}}");
            File.Delete(other);
            var fourth = new ArchivedRaceCatalog();
            var repaired = fourth.Read(broken, cache, () => { }, ledger);
            Assert.IsNotNull(repaired);
            Assert.AreEqual("Repaired Speedway", repaired.Race.Track);
            Assert.IsEmpty(fourth.UnreadableReports(reports, ledger));
            Assert.IsEmpty(new ArchivedRaceCatalog().UnreadableReports(reports, ledger));
        }
        finally { Directory.Delete(root, recursive: true); }
    }

    [TestMethod]
    public void NewerUnreadableLedgerIsNeitherUsedNorOverwritten()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-catalog-" + Guid.NewGuid().ToString("N"));
        var report = Path.Combine(root, "reports", "a", "analysis.json");
        var ledger = Path.Combine(root, "catalog", "unreadable-analyses.json");
        Directory.CreateDirectory(Path.GetDirectoryName(report)!);
        Directory.CreateDirectory(Path.GetDirectoryName(ledger)!);
        try
        {
            File.WriteAllText(report, "{broken");
            const string future = "{\"schemaVersion\":99,\"reports\":[]}";
            File.WriteAllText(ledger, future);
            var catalog = new ArchivedRaceCatalog();
            Assert.Throws<JsonException>(() => catalog.Read(report, Path.Combine(root, "cache"), () => { }, ledger));
            Assert.AreEqual(future, File.ReadAllText(ledger));
            Assert.IsNull(catalog.Read(report, Path.Combine(root, "cache"), () => { }, ledger), "The process still remembers it.");
        }
        finally { Directory.Delete(root, recursive: true); }
    }
}
