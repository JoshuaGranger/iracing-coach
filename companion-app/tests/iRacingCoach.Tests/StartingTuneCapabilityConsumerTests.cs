using System.Text.Json;
using iRacingCoach.BackendClient;
using iRacingCoach.Coordinator;
using iRacingCoach.Contracts;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class StartingTuneCapabilityConsumerTests
{
    [TestMethod]
    public void HtmlOnlyCapability_RemainsEvidenceButNeverOffersLoad()
    {
        using var response = Package(Capability("race", "race", "exact-purpose", "html_only", false, "parameters-readable", "no-loadable-sto-in-source"));

        var package = RuntimeMapper.SetupPackage(response.RootElement, "Synthetic car", "Synthetic track", "2026S3", "Race");

        Assert.IsNotNull(package.Capability);
        Assert.IsTrue(package.Capability.UsableAsEvidence);
        Assert.IsFalse(package.Capability.LoadPermitted);
        Assert.IsTrue(package.BaselineChecks[0].StartsWith("No simulator Load action", StringComparison.Ordinal));
        Assert.IsFalse(package.BaselineChecks.Any(check => check.StartsWith("Load the validated", StringComparison.Ordinal)));
    }

    [TestMethod]
    public void ValidatedStoCapability_AloneOffersLoadAndPreservesUnknownFields()
    {
        using var response = Package(Capability("qualifying", "qualifying", "exact-purpose", "paired", true, "parameters-readable", extra: "\"future_hint\":{\"value\":7}"));

        var package = RuntimeMapper.SetupPackage(response.RootElement, "Synthetic car", "Synthetic track", "2026S3", "Qualifying");

        Assert.IsTrue(package.Capability!.LoadPermitted);
        StringAssert.StartsWith(package.BaselineChecks[0], "Load the validated source setup");
        Assert.AreEqual(7, package.Capability.ExtensionData["future_hint"].GetProperty("value").GetInt32());
        Assert.AreEqual("qualifying-source", package.Donor);
    }

    [TestMethod]
    [DataRow("null")]
    [DataRow("{\"contract_version\":2,\"requested_purpose\":\"race\",\"resolved_purpose\":\"race\",\"purpose_match\":\"exact-purpose\",\"source_shape\":\"paired\",\"load_permitted\":true,\"evidence_level\":\"identity-only\",\"reasons\":[],\"source_files_read_only\":true,\"usable_as_evidence\":true}")]
    public void PresentUnreadableOrFutureCapability_FailsClosed(string capability)
    {
        using var response = Package(capability);

        Assert.Throws<InvalidDataException>(() => RuntimeMapper.SetupPackage(response.RootElement, "Synthetic car", "Synthetic track", "2026S3", "Race"));
    }

    [TestMethod]
    public void CapabilityForAnotherRequestPurpose_FailsClosed()
    {
        using var response = Package(Capability("qualifying", "qualifying", "exact-purpose", "paired", true, "identity-only"));

        var error = Assert.Throws<InvalidDataException>(() => RuntimeMapper.SetupPackage(response.RootElement, "Synthetic car", "Synthetic track", "2026S3", "Race"));
        StringAssert.Contains(error.Message, "different session purpose");
    }

    [TestMethod]
    public async Task PurposeChangedDuringRequest_CannotPublishOlderPackage()
    {
        using var response = Package(Capability("race", "race", "exact-purpose", "paired", true, "identity-only"));
        var backend = new DeferredStartingTuneBackend(response.RootElement.Clone());
        using var state = new CompanionState(backend)
        {
            StartingTuneSeason = "2026S3",
            StartingTuneCar = "Synthetic car",
            StartingTuneTrack = "Synthetic track",
            StartingTunePurpose = "Race"
        };

        var build = state.BuildStartingTuneAsync();
        await backend.Started.Task.WaitAsync(TimeSpan.FromSeconds(5));
        state.StartingTunePurpose = "Qualifying";
        backend.Release.TrySetResult(true);
        await build;

        Assert.IsNull(state.StartingTunePackage);
        StringAssert.Contains(state.SetupMessage, "older result was not shown");
        Assert.AreEqual("2026S3", backend.Arguments.GetProperty("season").GetString());
        Assert.AreEqual("Synthetic car", backend.Arguments.GetProperty("car").GetString());
        Assert.AreEqual("Synthetic track", backend.Arguments.GetProperty("track").GetString());
    }

    private static JsonDocument Package(string capability) => JsonDocument.Parse($$"""
    {
      "ok": true,
      "status": "exact-track-baseline",
      "package_id": "pkg-1",
      "package_path": "package.json",
      "baseline": {"stem":"race-source","fingerprint":"race-fingerprint","identity_warnings":[]},
      "qualifying": {"stem":"qualifying-source","fingerprint":"qualifying-fingerprint","identity_warnings":[]},
      "baseline_confirmation": {"confirmed":true,"reason":"exact"},
      "donor": null,
      "simulator_loadable_setup_produced": false,
      "source_setup_files_modified": false,
      "starting_tune_capability": {{capability}}
    }
    """);

    private static string Capability(
        string requested,
        string? resolved,
        string match,
        string shape,
        bool load,
        string evidence,
        string? reason = null,
        string? extra = null)
    {
        var reasons = reason is null ? "[]" : $"[\"{reason}\"]";
        var extension = extra is null ? string.Empty : $",{extra}";
        var resolvedValue = resolved is null ? "null" : $"\"{resolved}\"";
        return $$"""{"contract_version":1,"requested_purpose":"{{requested}}","resolved_purpose":{{resolvedValue}},"purpose_match":"{{match}}","source_shape":"{{shape}}","load_permitted":{{load.ToString().ToLowerInvariant()}},"evidence_level":"{{evidence}}","reasons":{{reasons}},"source_files_read_only":true,"usable_as_evidence":{{(evidence != "none").ToString().ToLowerInvariant()}}{{extension}}}""";
    }

    private sealed class DeferredStartingTuneBackend(JsonElement result) : IBackendClient
    {
        public TaskCompletionSource<bool> Started { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public TaskCompletionSource<bool> Release { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public JsonElement Arguments { get; private set; }

        public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default) =>
            Task.FromResult(new BackendHealthResult(true, "test", "test", "test", 1, TimeSpan.Zero, "ready"));

        public async Task<JsonElement> CallToolAsync(BackendConfiguration configuration, string toolName, object arguments, CancellationToken cancellationToken = default)
        {
            Assert.AreEqual("build_open_setup_package", toolName);
            Arguments = JsonSerializer.SerializeToElement(arguments);
            Started.TrySetResult(true);
            await Release.Task.WaitAsync(cancellationToken);
            return result;
        }
    }
}
