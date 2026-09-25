using System.Collections.Concurrent;
using System.Text.Json;
using System.Text.Json.Nodes;
using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;
using iRacingCoach.UI;

namespace iRacingCoach.Tests;

/// <summary>
/// GAP-025 / PORT-007: Settings shows reclaimable report storage from the backend's plan and
/// moves copies only after the driver reviews and confirms that exact plan.
/// </summary>
public sealed partial class UiPolishRegressionTests
{
    private const long GiB = 1024L * 1024 * 1024;

    // ---------- Contract ----------

    private static JsonObject ReclaimFixture(string name) =>
        JsonNode.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "fixtures", name)))!.AsObject();

    [TestMethod]
    public void ReclaimFixturesMapAndKeepUnknownFields()
    {
        var planNode = ReclaimFixture("report-storage-reclaim-plan.json");
        planNode["future_optional"] = "kept";
        var plan = ReportStorageMapper.MapPlan(JsonSerializer.SerializeToElement(planNode));
        Assert.AreEqual("ready", plan.Status);
        Assert.IsTrue(plan.HasReclaimable);
        Assert.AreEqual(4, plan.Remove.Count);
        Assert.AreEqual(plan.Remove.Directories.Sum(copy => copy.Bytes), plan.Remove.Bytes);
        Assert.AreEqual(2, plan.Remove.DuplicateCount);
        Assert.AreEqual(2, plan.Remove.SupersededCount);
        Assert.AreEqual(3, plan.Kept.Count);
        Assert.AreEqual(1, plan.Kept.ByReason["saved-test"]);
        Assert.AreEqual(@"season-fixture\bristol\copy-2", plan.Remove.Directories[0].KeptCopy);
        Assert.AreEqual("kept", plan.Source.GetProperty("future_optional").GetString());

        var result = ReportStorageMapper.MapResult(JsonSerializer.SerializeToElement(ReclaimFixture("report-storage-reclaim-result.json")));
        Assert.AreEqual("complete", result.Status);
        Assert.AreEqual(plan.PlanId, result.ConfirmedPlanId);
        Assert.AreEqual(4, result.MovedCount);
        Assert.AreEqual(plan.Remove.Bytes, result.MovedBytes);
        Assert.IsEmpty(result.Skipped);
    }

    [TestMethod]
    public void ReclaimMapperRefusesAPlanItCannotTrust()
    {
        foreach (var (label, change) in new (string, Action<JsonObject>)[]
        {
            ("other contract", node => node["contract"] = "report-storage-reclaim-v2"),
            ("other schema", node => node["schema_version"] = 2),
            ("execute read as plan", node => node["mode"] = "execute"),
            ("plan not read-only", node => node["read_only"] = false),
            ("unknown status", node => node["status"] = "deleted"),
            ("missing plan id", node => node.Remove("plan_id")),
            ("count differs from list", node => node["remove"]!["count"] = 5),
            ("bytes differ from list", node => node["remove"]!["bytes"] = 1),
            ("unknown kind", node => node["remove"]!["directories"]![0]!["kind"] = "current"),
            ("missing kept copy", node => node["remove"]!["directories"]![0]!.AsObject().Remove("kept_copy")),
            ("kept reasons do not add up", node => node["kept"]!["by_reason"]!["only-copy"] = 7),
            ("missing recovery", node => node.Remove("recovery")),
        })
        {
            var node = ReclaimFixture("report-storage-reclaim-plan.json");
            change(node);
            Assert.ThrowsExactly<JsonException>(() => ReportStorageMapper.MapPlan(JsonSerializer.SerializeToElement(node)), label);
        }
        var result = ReclaimFixture("report-storage-reclaim-result.json");
        result["moved"]!["count"] = 3;
        Assert.ThrowsExactly<JsonException>(() => ReportStorageMapper.MapResult(JsonSerializer.SerializeToElement(result)));
        Assert.ThrowsExactly<JsonException>(() => ReportStorageMapper.MapResult(JsonSerializer.SerializeToElement(ReclaimFixture("report-storage-reclaim-plan.json"))),
            "A plan is never read as a removal result.");
    }

    [TestMethod]
    public void ReclaimSizesReadAsExplorerShowsThem()
    {
        Assert.AreEqual("2.3 GB", ReportStoragePresentation.Size(2_472_354_627));
        Assert.AreEqual("307 MB", ReportStoragePresentation.Size(322_037_964));
        Assert.AreEqual("under 1 MB", ReportStoragePresentation.Size(191_768));
        Assert.AreEqual("1 copy", ReportStoragePresentation.Copies(1));
        Assert.AreEqual("167 copies", ReportStoragePresentation.Copies(167));
        var aug1 = new DateTimeOffset(2026, 8, 1, 12, 0, 0, TimeSpan.Zero);
        Assert.AreEqual(aug1.ToLocalTime().ToString("d MMM", System.Globalization.CultureInfo.InvariantCulture) + " – "
            + aug1.AddDays(16).ToLocalTime().ToString("d MMM yyyy", System.Globalization.CultureInfo.InvariantCulture),
            ReportStoragePresentation.DateRange(aug1, aug1.AddDays(16)));
        Assert.IsNull(ReportStoragePresentation.DateRange(null, null));
    }

    // ---------- Settings ----------

    [TestMethod]
    public async Task NothingReclaimableShowsNoStorageLineAndOffersNoAction()
    {
        var backend = new ReclaimBackend(ReclaimPlan(status: "nothing", sizes: []));
        using var state = NewState(backend);
        await state.RefreshReportStorageAsync(force: true);

        Assert.IsNotNull(state.ReportStorage);
        Assert.IsFalse(state.ReportStorage.HasReclaimable);
        var html = await RenderSettings(state);
        Assert.DoesNotContain("data-report-storage", html);
        Assert.DoesNotContain("Review and remove", html);
        Assert.DoesNotContain("Older copies of saved analyses", html);

        state.ReviewReportStorage();
        Assert.IsFalse(state.ReportStorageReviewOpen, "There is nothing to review.");
        await state.ConfirmReportStorageRemovalAsync();
        Assert.AreEqual(0, backend.Executions, "No removal runs when nothing is reclaimable.");
    }

    [TestMethod]
    public async Task RemovalRunsOnlyAfterTheDriverConfirmsTheReviewedPlan()
    {
        var sizes = new[] { 1_200 * 1024L * 1024, 900 * 1024L * 1024, 400 * 1024L * 1024 };
        var backend = new ReclaimBackend(ReclaimPlan(status: "ready", sizes: sizes));
        using var state = NewState(backend);
        await state.RefreshReportStorageAsync(force: true);

        var html = await RenderSettings(state);
        StringAssert.Contains(html, "data-report-storage");
        StringAssert.Contains(html, "Older copies of saved analyses");
        StringAssert.Contains(html, "2.4 GB can be removed");
        StringAssert.Contains(html, "Review and remove");
        Assert.DoesNotContain("data-report-storage-review", html, "The review opens only on request.");

        await state.ConfirmReportStorageRemovalAsync();
        Assert.AreEqual(0, backend.Executions, "A confirmation without an open review does nothing.");

        state.ReviewReportStorage();
        Assert.IsTrue(state.ReportStorageReviewOpen);
        html = await RenderSettings(state);
        StringAssert.Contains(html, "data-report-storage-review");
        StringAssert.Contains(html, "Remove older copies of saved analyses");
        StringAssert.Contains(html, "3 copies · 2.4 GB");
        StringAssert.Contains(html, "2 from an older analyzer version");
        StringAssert.Contains(html, "1 repeat of a kept analysis");
        StringAssert.Contains(html, "Saved ");
        StringAssert.Contains(html, "Keep");
        StringAssert.Contains(html, "22 copies · 307 MB");
        StringAssert.Contains(html, ReportStoragePresentation.KeepDetail);
        StringAssert.Contains(html, "Recordings (IBT files), setups and everything outside saved analyses stay untouched.");
        StringAssert.Contains(html, "Remove 3 copies");
        Assert.AreEqual(0, backend.Executions, "Reviewing moves nothing.");

        // A newer plan read while the review is open is not what gets confirmed.
        backend.Plan = ReclaimPlan(status: "ready", sizes: [5 * 1024L * 1024], planId: "ffffffffffffffffffffffffffffffff");
        await state.RefreshReportStorageAsync(force: true);

        await state.ConfirmReportStorageRemovalAsync();

        Assert.AreEqual(1, backend.Executions);
        var sent = backend.LastExecution!.Value;
        Assert.IsTrue(sent.GetProperty("confirm").GetBoolean());
        Assert.AreEqual("0123456789abcdef0123456789abcdef", sent.GetProperty("plan_id").GetString());
        CollectionAssert.AreEqual(new[] { @"season-6361\iowa\copy-0", @"season-6361\iowa\copy-1", @"season-6361\iowa\copy-2" },
            sent.GetProperty("directories").EnumerateArray().Select(item => item.GetString()).ToArray());
        Assert.IsNotNull(state.ReportStorageOutcome);
        html = await RenderSettings(state);
        StringAssert.Contains(html, "Moved 3 copies (2.4 GB)");
        StringAssert.Contains(html, "They are in removed-reports. Delete that folder to free the space.");

        await state.ConfirmReportStorageRemovalAsync();
        Assert.AreEqual(1, backend.Executions, "One review confirms one removal.");
        state.CloseReportStorageReview();
        Assert.IsFalse(state.ReportStorageReviewOpen);
    }

    [TestMethod]
    public async Task RemovalWaitsForRunningWorkAndReportsSkippedCopies()
    {
        var backend = new ReclaimBackend(ReclaimPlan(status: "ready", sizes: [2 * GiB, GiB]))
        {
            Skip = [(@"season-6361\iowa\copy-1", "in-use")]
        };
        using var state = NewState(backend);
        await state.RefreshReportStorageAsync(force: true);
        state.ReviewReportStorage();
        state.Jobs.Add(new JobItem { Id = "job", Title = "Analyze Bristol", CanonicalKey = "analysis:bristol", Status = "running" });

        await state.ConfirmReportStorageRemovalAsync();

        Assert.AreEqual(0, backend.Executions);
        Assert.AreEqual("Wait for Analyze Bristol to finish, then remove the copies.", state.ReportStorageMessage);
        StringAssert.Contains(await RenderSettings(state), "Wait for Analyze Bristol to finish");

        state.Jobs.Clear();
        await state.ConfirmReportStorageRemovalAsync();

        Assert.AreEqual(1, backend.Executions);
        var outcome = state.ReportStorageOutcome!;
        Assert.AreEqual(1, outcome.MovedCount);
        Assert.AreEqual("Moved 1 copy (2.0 GB)", ReportStoragePresentation.ResultHeadline(outcome));
        StringAssert.Contains(ReportStoragePresentation.ResultDetail(outcome), "1 copy stayed because it is in use.");
    }

    [TestMethod]
    public async Task AppCachesNamingAMovedCopyAreDroppedAndOthersKept()
    {
        var backend = new ReclaimBackend(ReclaimPlan(status: "ready", sizes: [GiB]));
        using var state = NewState(backend);
        var cache = Path.Combine(state.Settings.ArchiveRoot, "ui-analysis-cache");
        Directory.CreateDirectory(cache);
        var reports = Path.Combine(state.Settings.ArchiveRoot, "reports");
        var naming = Path.Combine(cache, "moved.json");
        var other = Path.Combine(cache, "kept.json");
        File.WriteAllText(naming, JsonSerializer.Serialize(new { schemaVersion = 14, response = new { analysis_path = Path.Combine(reports, "season-6361", "iowa", "copy-0", "analysis.json") } }));
        // A Coach folder copied from another PC still names the copy below reports.
        var migrated = Path.Combine(cache, "migrated.json");
        File.WriteAllText(migrated, JsonSerializer.Serialize(new { response = new { artifacts = new { analysis = @"D:\Old\iRacing Coach\data\reports\season-6361\iowa\copy-0\analysis.json" } } }));
        File.WriteAllText(other, JsonSerializer.Serialize(new { response = new { analysis_path = Path.Combine(reports, "season-6361", "iowa", "current", "analysis.json") } }));
        await state.RefreshReportStorageAsync(force: true);
        state.ReviewReportStorage();

        await state.ConfirmReportStorageRemovalAsync();

        Assert.IsFalse(File.Exists(naming));
        Assert.IsFalse(File.Exists(migrated));
        Assert.IsTrue(File.Exists(other));
    }

    [TestMethod]
    public async Task RemovedCopiesWaitingToBeDeletedAreStatedQuietly()
    {
        var backend = new ReclaimBackend(ReclaimPlan(status: "nothing", sizes: [], recoveryBytes: 2_472_354_627));
        using var state = NewState(backend);
        await state.RefreshReportStorageAsync(force: true);

        var html = await RenderSettings(state);

        StringAssert.Contains(html, "Removed copies: 2.3 GB in removed-reports. Delete that folder to free the space.");
        Assert.DoesNotContain("Open folder", html, "The isolated profile cannot open folders, so it offers none.");
        Assert.DoesNotContain("Review and remove", html);
    }

    [TestMethod]
    public async Task CopiesATuningDraftNamesAreSentToBeKept()
    {
        var backend = new ReclaimBackend(ReclaimPlan(status: "nothing", sizes: []));
        using var state = NewState(backend);
        var drafts = Path.Combine(state.Settings.CoachHome, "portable-settings", "tuning-drafts");
        Directory.CreateDirectory(drafts);
        // Written before the Coach folder moved: the path below reports still names the copy.
        File.WriteAllText(Path.Combine(drafts, "draft.json"), JsonSerializer.Serialize(new
        {
            representativeSession = new { analysisPath = @"D:\Old\iRacing Coach\data\reports\season-6361\iowa\copy-0\analysis.json" },
            notes = "not a path"
        }));
        File.WriteAllText(Path.Combine(drafts, "broken.json"), "{ not json");

        await state.RefreshReportStorageAsync(force: true);

        var keep = backend.LastPlanRequest!.Value.GetProperty("keep_analysis_paths").EnumerateArray().Select(item => item.GetString()).ToArray();
        CollectionAssert.AreEqual(new[] { Path.Combine(state.Settings.ArchiveRoot, "reports", "season-6361", "iowa", "copy-0", "analysis.json") }, keep);
    }

    [TestMethod]
    public void IsolatedProfileAllowsTheConfinedReclaimTool()
    {
        var client = new LocalOnlyBackendClient(new ReclaimBackend(ReclaimPlan("nothing", [])), TempRoot());
        Assert.IsTrue(client.SupportsTool("reclaim_report_storage"));
    }

    [TestMethod]
    public void ReportCacheMatchingUsesThePathBelowReports()
    {
        var moved = new HashSet<string>(StringComparer.OrdinalIgnoreCase) { @"season-6361\iowa\copy-0" };
        Assert.IsTrue(ReportStorageCaches.NamesMovedCopy(JsonSerializer.SerializeToUtf8Bytes(new { a = @"C:\X\reports\Season-6361\iowa\copy-0\report.md" }), moved));
        Assert.IsFalse(ReportStorageCaches.NamesMovedCopy(JsonSerializer.SerializeToUtf8Bytes(new { a = @"C:\X\reports\season-6361\iowa\copy-00\analysis.json" }), moved));
        Assert.IsFalse(ReportStorageCaches.NamesMovedCopy(JsonSerializer.SerializeToUtf8Bytes(new { a = @"C:\X\reports\season-6361\iowa\copy-0\notes.txt" }), moved));
        Assert.IsFalse(ReportStorageCaches.NamesMovedCopy("{ not json"u8, moved));
        Assert.AreEqual(@"a\b", ReportStorageCaches.RelativeBelowReports(@"C:\data\reports\a\b\analysis.json"));
        Assert.IsNull(ReportStorageCaches.RelativeBelowReports(@"C:\data\reports\analysis.json"));
    }

    // ---------- Helpers ----------

    private static JsonObject ReclaimPlan(string status, long[] sizes, string planId = "0123456789abcdef0123456789abcdef", long recoveryBytes = 0)
    {
        var directories = new JsonArray();
        for (var index = 0; index < sizes.Length; index++)
        {
            directories.Add(new JsonObject
            {
                ["directory"] = $@"season-6361\iowa\copy-{index}",
                ["bytes"] = sizes[index],
                ["kind"] = index == sizes.Length - 1 && sizes.Length > 1 ? "duplicate" : "superseded",
                ["analysis_id"] = $"analysis-{index}",
                ["modified_utc"] = $"2026-08-{index + 1:00}T12:00:00+00:00",
                ["kept_copy"] = @"season-6361\iowa\current"
            });
        }
        var duplicate = sizes.Length > 1 ? sizes[^1] : 0;
        return new JsonObject
        {
            ["contract"] = "report-storage-reclaim-v1",
            ["schema_version"] = 1,
            ["mode"] = "plan",
            ["status"] = status,
            ["read_only"] = true,
            ["reports_root"] = @"C:\Coach\data\reports",
            ["recovery_root"] = @"C:\Coach\data\removed-reports",
            ["plan_id"] = planId,
            ["remove"] = new JsonObject
            {
                ["count"] = sizes.Length,
                ["bytes"] = sizes.Sum(),
                ["duplicate_count"] = sizes.Length > 1 ? 1 : 0,
                ["duplicate_bytes"] = duplicate,
                ["superseded_count"] = sizes.Length - (sizes.Length > 1 ? 1 : 0),
                ["superseded_bytes"] = sizes.Sum() - duplicate,
                ["oldest_utc"] = sizes.Length > 0 ? "2026-08-01T12:00:00+00:00" : null,
                ["newest_utc"] = sizes.Length > 0 ? $"2026-08-{sizes.Length:00}T12:00:00+00:00" : null,
                ["directories"] = directories
            },
            ["kept"] = new JsonObject
            {
                ["count"] = 22,
                ["bytes"] = 322_037_964,
                ["by_reason"] = new JsonObject
                {
                    ["history-index"] = 14, ["saved-test"] = 0, ["tuning"] = 0, ["open-in-app"] = 0,
                    ["latest-analysis"] = 7, ["only-copy"] = 1, ["recent"] = 0, ["unverified"] = 0, ["deferred"] = 0
                }
            },
            ["recovery"] = new JsonObject { ["path"] = @"C:\Coach\data\removed-reports", ["bytes"] = recoveryBytes, ["batch_count"] = recoveryBytes > 0 ? 1 : 0 }
        };
    }

    private sealed class ReclaimBackend(JsonObject plan) : IBackendClient
    {
        public JsonObject Plan { get; set; } = plan;
        public (string Directory, string Reason)[] Skip { get; init; } = [];
        public ConcurrentQueue<string> Calls { get; } = new();
        public int Executions;
        public JsonElement? LastExecution { get; private set; }
        public JsonElement? LastPlanRequest { get; private set; }
        private readonly ConcurrentDictionary<string, JsonObject> _served = new(StringComparer.Ordinal);

        public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default) =>
            Task.FromResult(new BackendHealthResult(true, "test", "1", "test", 1, TimeSpan.Zero));

        public Task<JsonElement> CallToolAsync(BackendConfiguration configuration, string toolName, object arguments, CancellationToken cancellationToken = default)
        {
            Calls.Enqueue(toolName);
            if (toolName != "reclaim_report_storage")
                return Task.FromResult(JsonSerializer.SerializeToElement(new { ok = true }));
            var sent = JsonSerializer.SerializeToElement(arguments);
            if (!sent.TryGetProperty("confirm", out var confirm) || !confirm.GetBoolean())
            {
                LastPlanRequest = sent;
                _served[Plan["plan_id"]!.GetValue<string>()] = Plan.DeepClone().AsObject();
                return Task.FromResult(JsonSerializer.SerializeToElement(Plan));
            }
            Interlocked.Increment(ref Executions);
            LastExecution = sent;
            var directories = sent.GetProperty("directories").EnumerateArray().Select(item => item.GetString()!).ToArray();
            var reviewed = _served[sent.GetProperty("plan_id").GetString()!]["remove"]!["directories"]!.AsArray();
            var moved = new JsonArray();
            long movedBytes = 0;
            foreach (var directory in directories)
            {
                if (Skip.Any(item => item.Directory == directory)) continue;
                var bytes = SizeOf(directory);
                movedBytes += bytes;
                moved.Add(new JsonObject { ["directory"] = directory, ["bytes"] = bytes, ["kind"] = "superseded", ["analysis_id"] = "a", ["kept_copy"] = null });
            }
            var skipped = new JsonArray(Skip.Select(item => (JsonNode)new JsonObject { ["directory"] = item.Directory, ["reason"] = item.Reason }).ToArray());
            return Task.FromResult(JsonSerializer.SerializeToElement(new JsonObject
            {
                ["contract"] = "report-storage-reclaim-v1",
                ["schema_version"] = 1,
                ["mode"] = "execute",
                ["status"] = skipped.Count > 0 ? "partial" : "complete",
                ["read_only"] = false,
                ["reports_root"] = @"C:\Coach\data\reports",
                ["recovery_root"] = @"C:\Coach\data\removed-reports",
                ["plan_id"] = sent.GetProperty("plan_id").GetString(),
                ["confirmed_plan_id"] = sent.GetProperty("plan_id").GetString(),
                ["batch_directory"] = @"C:\Coach\data\removed-reports\20260925T000000Z-abc123",
                ["moved"] = new JsonObject { ["count"] = moved.Count, ["bytes"] = movedBytes, ["directories"] = moved },
                ["skipped"] = new JsonObject { ["count"] = skipped.Count, ["directories"] = skipped },
                ["recovery"] = new JsonObject { ["path"] = @"C:\Coach\data\removed-reports", ["bytes"] = movedBytes, ["batch_count"] = 1 }
            }));

            long SizeOf(string directory) => reviewed.FirstOrDefault(item => item!["directory"]!.GetValue<string>() == directory)?["bytes"]?.GetValue<long>() ?? 0;
        }
    }
}
