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
    public void ProjectionOfAnOlderRecordedSetupShape_IsRebuilt()
    {
        // A projection written before recorded setups carried CarKey would keep them
        // from ever matching their own linked setup files for comparison; one without
        // ValuesAnalysisPath would let tuning start from another recording; one with
        // run readings among Fields would list them as setup values.
        var root = Path.Combine(Path.GetTempPath(), "iracing-coach-catalog-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        var report = Path.Combine(root, "analysis.json");
        var cache = Path.Combine(root, "cache");
        try
        {
            File.WriteAllText(report, JsonSerializer.Serialize(new
            {
                analysis_id = "camaro", identity = new
                {
                    track_name = "Lucas Oil Indianapolis Raceway Park", car_name = "NASCAR Xfinity Chevrolet Camaro", car_path = "stockcars2 camaro2019",
                    setup_fingerprint = "camaro-setup", setup = new { Chassis = new { Front = new { CrossWeight = "50.9%" } }, Tires = new { LeftFront = new { LastHotPressure = "128 kPa" } } }
                },
                race_summary = new { recorded_laps = 10 }
            }));
            var fresh = new ArchivedRaceCatalog().Read(report, cache, () => { })!.Setup!;
            Assert.AreEqual("stockcars2 camaro2019", fresh.CarKey);
            Assert.AreEqual(Path.GetFullPath(report), fresh.ValuesAnalysisPath);
            Assert.AreEqual("Chassis.Front.CrossWeight", fresh.Fields.Single().Key);
            Assert.AreEqual("Tires.LeftFront.LastHotPressure", fresh.RunReadings!.Single().Key);

            // Version 1 lacked CarKey; version 2 the report the values came from; version 3
            // kept run readings among the setup values.
            var cachedPath = Directory.GetFiles(cache).Single();
            foreach (var (version, missing) in new[] { (1, new[] { "carKey", "valuesAnalysisPath" }), (2, new[] { "valuesAnalysisPath" }), (3, Array.Empty<string>()) })
            {
                var older = System.Text.Json.Nodes.JsonNode.Parse(File.ReadAllText(cachedPath))!.AsObject();
                var setup = older["setup"]!.AsObject();
                older["version"] = version;
                foreach (var name in missing) setup.Remove(name);
                var readings = setup["runReadings"]!.AsArray();
                foreach (var reading in readings.ToArray()) { readings.Remove(reading); setup["fields"]!.AsArray().Add(reading); }
                setup.Remove("runReadings");
                File.WriteAllText(cachedPath, older.ToJsonString());

                var writes = 0;
                var reread = new ArchivedRaceCatalog().Read(report, cache, () => writes++)!.Setup!;
                Assert.AreEqual("stockcars2 camaro2019", reread.CarKey, $"version {version}");
                Assert.AreEqual(Path.GetFullPath(report), reread.ValuesAnalysisPath, $"version {version}");
                Assert.HasCount(1, reread.Fields, $"version {version}");
                Assert.HasCount(1, reread.RunReadings!, $"version {version}");
                Assert.AreEqual(1, writes, $"A version {version} projection is rebuilt from the report, not reused.");
            }
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
