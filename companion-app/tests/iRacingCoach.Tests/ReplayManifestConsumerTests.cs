using System.Text.Json;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class ReplayManifestConsumerTests
{
    [TestMethod]
    public void LegacyInlineManifest_RemainsCompatible()
    {
        var manifest = Read("""{"schema_version":2,"frame_count":12,"car_count":4,"cadence_hz":60}""");

        Assert.AreEqual("inline", manifest.Format);
        Assert.AreEqual("complete", manifest.Status);
        Assert.AreEqual("legacy-inline", manifest.Revision);
        Assert.AreEqual(48, manifest.TotalRows);
    }

    [TestMethod]
    public void WindowedIncompleteManifest_StatesGapsAndRevision()
    {
        var manifest = Read("""{"contract_version":1,"schema_version":2,"format":"windowed","status":"incomplete","revision":"rev-2","frame_count":100,"car_count":24,"cadence_hz":60,"gaps":[{"start_frame":8,"end_frame":10}]}""");

        Assert.AreEqual("rev-2", manifest.Revision);
        Assert.AreEqual(3, manifest.MissingFrames);
        Assert.IsTrue(manifest.IsReadable);
    }

    [TestMethod]
    public void HalfDiscriminatorAndFutureSchema_AreRefused()
    {
        Assert.Throws<InvalidDataException>(() => Read("""{"schema_version":2,"format":"windowed","frame_count":1,"car_count":1,"cadence_hz":60}"""));
        Assert.Throws<InvalidDataException>(() => Read("""{"schema_version":3,"format":"inline","status":"complete","revision":"x","frame_count":1,"car_count":1,"cadence_hz":60}"""));
    }

    [TestMethod]
    public void FalseCompletenessAndOverlappingGaps_AreRefused()
    {
        Assert.Throws<InvalidDataException>(() => Read("""{"schema_version":2,"format":"windowed","status":"complete","revision":"x","frame_count":10,"car_count":2,"cadence_hz":60,"gaps":[{"start_frame":2,"end_frame":3}]}"""));
        Assert.Throws<InvalidDataException>(() => Read("""{"schema_version":2,"format":"windowed","status":"incomplete","revision":"x","frame_count":10,"car_count":2,"cadence_hz":60,"gaps":[{"start_frame":2,"end_frame":4},{"start_frame":4,"end_frame":5}]}"""));
    }

    [TestMethod]
    public void CursorIsBoundToExactRevisionAndFrameRange()
    {
        var manifest = Read("""{"schema_version":2,"format":"windowed","status":"complete","revision":"rev-a","frame_count":10,"car_count":2,"cadence_hz":60}""");

        Assert.IsTrue(ReplayManifestConsumer.CursorIsValid(manifest, new("rev-a", 10)));
        Assert.IsFalse(ReplayManifestConsumer.CursorIsValid(manifest, new("rev-b", 4)));
        Assert.IsFalse(ReplayManifestConsumer.CursorIsValid(manifest, new("rev-a", 11)));
    }

    [TestMethod]
    public void BooleansCannotMasqueradeAsManifestIntegers()
    {
        Assert.Throws<InvalidDataException>(() => Read("""{"schema_version":true,"frame_count":1,"car_count":1,"cadence_hz":60}"""));
        Assert.Throws<InvalidDataException>(() => Read("""{"schema_version":2,"frame_count":true,"car_count":1,"cadence_hz":60}"""));
    }

    private static iRacingCoach.Contracts.AnalysisReplayManifest Read(string json)
    {
        using var document = JsonDocument.Parse(json);
        return ReplayManifestConsumer.Read(document.RootElement);
    }
}
