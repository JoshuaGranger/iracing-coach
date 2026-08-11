using System.Diagnostics;
using System.Reflection;
using System.Text.Json;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;
using iRacingCoach.UI;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class LiveReplayCaptureOverhaulTests
{
    public TestContext TestContext { get; set; } = null!;

    [TestMethod]
    public void LiveTelemetryReplayCapture_UsesDistinctNativeTicksUpTo60Hz()
    {
        using var service = new LiveTelemetryService(new DisconnectedSource(), new LiveMonitorLayout());
        var frames = new List<LiveReplayCaptureFrame>();
        service.ReplayFrameCaptured += frames.Add;
        var capture = typeof(LiveTelemetryService).GetMethod("CaptureReplayFrame", BindingFlags.Instance | BindingFlags.NonPublic);
        Assert.IsNotNull(capture);
        for (var tick = 0; tick < 240; tick++)
        {
            var sample = new LiveTelemetrySample
            {
                Connected = true,
                Timestamp = DateTimeOffset.Parse("2026-08-10T12:00:00Z").AddSeconds(tick / 240d),
                Tick = tick,
                TickRate = 240,
                SessionTimeSeconds = tick / 240d,
                SessionState = 4,
                SessionFlags = 4,
                SessionUniqueId = 10,
                SubsessionId = 20,
                SessionNumber = 0,
                SessionType = "Race",
                PlayerCarIndex = 0,
                PlayerIncidentPoints = tick >= 4 ? 2 : 0,
                DriverIncidentPoints = tick >= 4 ? 2 : 0,
                TeamIncidentPoints = tick >= 4 ? 2 : 0,
                PlayerTrackSurface = 3,
                ReplayCoverage = [new("CarIdxLapDistPct", true, null)],
                ReplayParticipants = [new(0, "1", 1, "Class", "Car", "Driver", null, false)],
                ReplayCars = [new(0, tick / 240d, 1, 0, 1, 1, false, 3, 0, 24, 24)]
            };
            capture.Invoke(service, [sample, tick == 0]);
        }

        Assert.HasCount(60, frames);
        Assert.AreEqual(0, frames[0].SourceTick);
        Assert.AreEqual(236, frames[^1].SourceTick);
        Assert.IsTrue(frames.Zip(frames.Skip(1)).All(pair => pair.Second.SourceTick - pair.First.SourceTick == 4));
        var incident = frames.SelectMany(frame => frame.Events ?? []).First(item => item.Kind == "incident_points");
        Assert.AreEqual(2d, incident.Delta);
        Assert.AreEqual("PlayerCarMyIncidentCount", incident.SourceChannel);
    }

    [TestMethod]
    public void LiveTelemetryReplayCapture_IdentityAndPhaseChangesStartCleanSessions()
    {
        using var service = new LiveTelemetryService(new DisconnectedSource(), new LiveMonitorLayout());
        var current = typeof(LiveTelemetryService).GetProperty(nameof(LiveTelemetryService.Current));
        var boundary = typeof(LiveTelemetryService).GetMethod("ObserveSessionBoundary", BindingFlags.Instance | BindingFlags.NonPublic);
        var capture = typeof(LiveTelemetryService).GetMethod("CaptureReplayFrame", BindingFlags.Instance | BindingFlags.NonPublic);
        Assert.IsNotNull(current);
        Assert.IsNotNull(boundary);
        Assert.IsNotNull(capture);
        current.SetValue(service, service.Current with { Snapshot = service.Current.Snapshot with { Connected = true } });

        var start = DateTimeOffset.Parse("2026-08-10T12:00:00Z");
        LiveTelemetrySample sample(long sessionId, string phase, int incidents, int tick) => new()
        {
            Connected = true,
            Timestamp = start.AddMilliseconds(tick * 17),
            Tick = tick,
            TickRate = 60,
            Lap = 1,
            SessionTimeSeconds = tick / 60d,
            SessionState = 4,
            SessionFlags = 4,
            SessionUniqueId = sessionId,
            SubsessionId = 20,
            SessionNumber = 0,
            SessionType = phase,
            PlayerCarIndex = 0,
            PlayerIncidentPoints = incidents,
            DriverIncidentPoints = incidents,
            TeamIncidentPoints = incidents,
            PlayerTrackSurface = 3,
            ReplayCoverage = [new("CarIdxLapDistPct", true, null)],
            ReplayParticipants = [new(0, "1", 1, "Class", "Car", "Driver", null, false)],
            ReplayCars = [new(0, .2, 1, 0, 1, 1, false, 3, 0, 24, 24)]
        };

        var frames = new List<LiveReplayCaptureFrame>();
        service.ReplayFrameCaptured += frames.Add;
        var first = sample(10, "Race", 0, 0);
        var firstBoundary = (bool)boundary.Invoke(service, [first])!;
        Assert.IsFalse(firstBoundary);
        capture.Invoke(service, [first, firstBoundary]);

        var identityChange = sample(11, "Race", 4, 1);
        var identityBoundary = (bool)boundary.Invoke(service, [identityChange])!;
        Assert.IsTrue(identityBoundary, "A session identity change must split capture even when lap and clock remain monotonic.");
        capture.Invoke(service, [identityChange, identityBoundary]);
        Assert.IsEmpty(frames[^1].Events ?? [], "Counters from the prior session must not become events in the new session.");
        Assert.AreNotEqual(frames[0].SessionKey, frames[^1].SessionKey);

        var phaseChange = sample(11, "Qualifying", 4, 2);
        Assert.IsTrue((bool)boundary.Invoke(service, [phaseChange])!, "A race-to-qualifying phase change must split replay state.");
    }

    [TestMethod]
    public void RaceReplay_IowaShapedCaptureStartsAtFirstPlayableGridLikeWarmupFrame()
    {
        static AnalysisReplayCarState Car(int index, double percent) => new(index, percent, 0, 0, null, null, false, 3, "On track", 0, null, null);
        var frames = new AnalysisReplayFrame[]
        {
            new(.4, "get_in_car", 0, [], []),
            new(15.133, "get_in_car", 0, [], [Car(9, .01)]),
            new(119.683, "warmup", 0, [], [Car(1, .20)]),
            new(120.183, "warmup", 0, [], Enumerable.Range(0, 21).Select(index => Car(index, index == 9 ? .21592295 : index / 21d)).ToArray()),
            new(187.05, "parade_laps", 0, [], [Car(1, .2)]),
            new(187.55, "parade_laps", 0, [], Enumerable.Range(0, 23).Select(index => Car(index, index == 9 ? .0799577 : index / 23d)).ToArray()),
            new(188.05, "parade_laps", 0, [], Enumerable.Range(0, 23).Select(index => Car(index, index == 9 ? .081 : index / 23d)).ToArray()),
            new(2_100, "racing", 4, ["green"], Enumerable.Range(0, 23).Select(index => Car(index, .5)).ToArray()),
            new(2_101, "cooldown", 1, ["checkered"], [Car(9, .6)]),
            new(2_102, "invalid", 0, [], [])
        };
        var trim = typeof(RaceReplayWorkspace).GetMethod("TrimToPlayableInterval", BindingFlags.Static | BindingFlags.NonPublic);
        Assert.IsNotNull(trim);

        var playable = (IReadOnlyList<AnalysisReplayFrame>)trim.Invoke(null, [frames.Where(frame =>
            frame.SessionState is "warmup" or "grid" or "parade_laps" or "racing" or "checkered" or "cooldown").ToArray(), (int?)9])!;

        Assert.HasCount(6, playable);
        Assert.AreEqual(120.183, playable[0].SessionTimeSeconds, .001);
        Assert.AreEqual("warmup", playable[0].SessionState);
        Assert.HasCount(21, playable[0].Cars);
        Assert.IsNotNull(playable[0].Cars.SingleOrDefault(car => car.CarIndex == 9));
        Assert.AreEqual("cooldown", playable[^1].SessionState);
    }

    [TestMethod]
    public void ReplayV2Codec_RoundTripsRecordedStateAndExplicitEvents()
    {
        var frames = BuildFrames(60, seconds: 2, cars: 24, includeEvent: true);

        var encoded = LiveReplayChunkCodec.Encode(frames);
        var decoded = LiveReplayChunkCodec.Decode(encoded.Bytes);

        Assert.HasCount(frames.Count, decoded.Frames);
        Assert.IsLessThan(frames.Count * 24 * 80, encoded.Bytes.Length,
            "A delta+gzip chunk should be materially smaller than an equivalent object-heavy payload.");
        for (var index = 0; index < frames.Count; index++)
        {
            var expected = frames[index];
            var actual = decoded.Frames[index];
            Assert.AreEqual(expected.SourceTick, actual.SourceTick);
            Assert.AreEqual(expected.SourceTickRate, actual.SourceTickRate);
            Assert.AreEqual(expected.SessionState, actual.SessionState);
            Assert.AreEqual(expected.SessionFlags, actual.SessionFlags);
            Assert.HasCount(expected.Cars.Count, actual.Cars);
            Assert.AreEqual(expected.PlayerTelemetry?.IncidentPoints, actual.PlayerTelemetry?.IncidentPoints);
            Assert.AreEqual(expected.PlayerTelemetry?.OnPitRoad, actual.PlayerTelemetry?.OnPitRoad);
            Assert.AreEqual(expected.PlayerTelemetry?.Throttle ?? 0, actual.PlayerTelemetry?.Throttle ?? 0, 0.00001);
            Assert.AreEqual(expected.Cars[7].LapDistancePercent ?? 0, actual.Cars[7].LapDistancePercent ?? 0, 0.00001);
        }
        var observed = decoded.Frames.SelectMany(frame => frame.Events ?? []).Single();
        Assert.AreEqual("incident_points", observed.Kind);
        Assert.AreEqual("PlayerCarMyIncidentCount", observed.SourceChannel);
        Assert.AreEqual(2d, observed.Delta);
        Assert.DoesNotContain("contact", observed.Label, StringComparison.OrdinalIgnoreCase,
            "An incident counter change must not invent wall/car contact or fault.");
    }

    [TestMethod]
    public void ReplayV2Codec_RejectsUnsafeHeadersAndExpansionBeforeAllocation()
    {
        var encoded = LiveReplayChunkCodec.Encode(BuildFrames(10, seconds: 1, cars: 1, includeEvent: false)).Bytes;

        var oversizedFrames = encoded.ToArray();
        BitConverter.GetBytes(LiveReplayChunkCodec.MaximumFramesPerChunk + 1).CopyTo(oversizedFrames, 12);
        Assert.Throws<InvalidDataException>(() => LiveReplayChunkCodec.Decode(oversizedFrames));

        var oversizedRaw = encoded.ToArray();
        BitConverter.GetBytes(LiveReplayChunkCodec.MaximumUncompressedBytes + 1).CopyTo(oversizedRaw, 16);
        Assert.Throws<InvalidDataException>(() => LiveReplayChunkCodec.Decode(oversizedRaw));

        var dishonestRaw = encoded.ToArray();
        BitConverter.GetBytes(32).CopyTo(dishonestRaw, 16);
        Assert.Throws<InvalidDataException>(() => LiveReplayChunkCodec.Decode(dishonestRaw),
            "The decoder must stop after the declared output length and reject extra expansion.");
    }

    [TestMethod]
    public void ReplayV2Capture_Benchmarks10To60HzWithNoSilentLoss()
    {
        foreach (var rate in new[] { 10, 20, 30, 60 })
        {
            var root = TemporaryDirectory($"replay-benchmark-{rate}");
            try
            {
                var frames = BuildFrames(rate, seconds: 10, cars: 24, includeEvent: true);
                var write = Stopwatch.StartNew();
                using (var store = new LiveReplayCaptureStore(() => root, queueCapacity: 4_096, chunkDuration: TimeSpan.FromSeconds(2)))
                {
                    foreach (var frame in frames) store.Capture(frame);
                    store.EndSession("benchmark_complete");
                    var metrics = store.Metrics;
                    Assert.AreEqual(frames.Count, metrics.ReceivedFrames);
                    Assert.AreEqual(0, metrics.DroppedFrames);
                }
                write.Stop();

                var directory = Directory.GetDirectories(Path.Combine(root, "telemetry-traces", "live-replay")).Single();
                using var manifest = JsonDocument.Parse(File.ReadAllText(Path.Combine(directory, "manifest.json")));
                var capture = manifest.RootElement.GetProperty("captureMetrics");
                Assert.AreEqual(frames.Count, capture.GetProperty("writtenFrameCount").GetInt32());
                Assert.AreEqual(0, capture.GetProperty("droppedFrameCount").GetInt32());
                Assert.AreEqual(0, capture.GetProperty("duplicateSourceTickCount").GetInt32());
                Assert.AreEqual(0, capture.GetProperty("gapCount").GetInt32());
                Assert.IsGreaterThanOrEqualTo(rate * .90, capture.GetProperty("observedSampleRateHz").GetDouble());
                Assert.IsLessThanOrEqualTo(rate * 1.10, capture.GetProperty("observedSampleRateHz").GetDouble());

                var load = Stopwatch.StartNew();
                var chunkFiles = Directory.GetFiles(directory, $"chunk-*{LiveReplayChunkCodec.FileExtension}");
                var decodedFrames = chunkFiles.Sum(path => LiveReplayChunkCodec.Decode(File.ReadAllBytes(path)).Frames.Count);
                load.Stop();
                Assert.AreEqual(frames.Count, decodedFrames);
                var bytes = chunkFiles.Sum(path => new FileInfo(path).Length);
                var projectedBytesPerHour = bytes / 10d * 3_600d;
                Assert.IsLessThan(200d * 1024 * 1024, projectedBytesPerHour,
                    $"The {rate} Hz replay projection exceeded the 200 MiB/hour safety ceiling.");
                Assert.IsLessThan(10_000, write.ElapsedMilliseconds);
                Assert.IsLessThan(10_000, load.ElapsedMilliseconds);
                TestContext.WriteLine($"{rate} Hz: {frames.Count:N0} frames, {bytes:N0} bytes / 10 s, projected {projectedBytesPerHour / 1024 / 1024:0.0} MiB/h, write {write.ElapsedMilliseconds} ms, load {load.ElapsedMilliseconds} ms");
            }
            finally
            {
                if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
            }
        }
    }

    [TestMethod]
    public void ReplayV2Capture_BenchmarksFull64CarFieldAt60Hz()
    {
        var root = TemporaryDirectory("replay-benchmark-64-car");
        try
        {
            var frames = BuildFrames(60, seconds: 10, cars: 64, includeEvent: true);
            var write = Stopwatch.StartNew();
            using (var store = new LiveReplayCaptureStore(() => root, queueCapacity: 4_096, chunkDuration: TimeSpan.FromSeconds(2)))
            {
                foreach (var frame in frames) store.Capture(frame);
                store.EndSession("benchmark_complete");
                Assert.AreEqual(0, store.Metrics.DroppedFrames);
            }
            write.Stop();
            var directory = Directory.GetDirectories(Path.Combine(root, "telemetry-traces", "live-replay")).Single();
            var chunks = Directory.GetFiles(directory, $"chunk-*{LiveReplayChunkCodec.FileExtension}");
            var load = Stopwatch.StartNew();
            Assert.AreEqual(frames.Count, chunks.Sum(path => LiveReplayChunkCodec.Decode(File.ReadAllBytes(path)).Frames.Count));
            load.Stop();
            var bytes = chunks.Sum(path => new FileInfo(path).Length);
            var projectedBytesPerHour = bytes / 10d * 3_600d;
            Assert.IsLessThan(400d * 1024 * 1024, projectedBytesPerHour);
            Assert.IsLessThan(10_000, write.ElapsedMilliseconds);
            Assert.IsLessThan(10_000, load.ElapsedMilliseconds);
            TestContext.WriteLine($"64 cars @ 60 Hz: {frames.Count:N0} frames, {bytes:N0} bytes / 10 s, projected {projectedBytesPerHour / 1024 / 1024:0.0} MiB/h, write {write.ElapsedMilliseconds} ms, load {load.ElapsedMilliseconds} ms");
        }
        finally
        {
            if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
        }
    }

    [TestMethod]
    public void ReplayV2Capture_BoundedQueueReportsDropsInsteadOfBlockingOrHidingThem()
    {
        var root = TemporaryDirectory("replay-bounded");
        try
        {
            using var store = new LiveReplayCaptureStore(() => root, queueCapacity: 2, chunkDuration: TimeSpan.FromSeconds(10));
            var template = BuildFrames(60, seconds: 1, cars: 1, includeEvent: false)[0];
            const int attempts = 25_000;
            for (var index = 0; index < attempts; index++)
                store.Capture(template with
                {
                    CapturedAt = template.CapturedAt.AddSeconds(index / 60d),
                    SessionTimeSeconds = index / 60d,
                    SourceTick = index,
                    Cars = [template.Cars[0] with { LapDistancePercent = index % 6_000 / 6_000d }]
                });
            store.EndSession("load_test_complete");
            var metrics = store.Metrics;
            Assert.AreEqual(attempts, metrics.ReceivedFrames);
            Assert.AreEqual(attempts, metrics.EnqueuedFrames + metrics.DroppedFrames);
            Assert.IsGreaterThan(0, metrics.DroppedFrames,
                "A two-slot nonblocking capture queue should shed load during this deliberate burst.");

            var directory = Directory.GetDirectories(Path.Combine(root, "telemetry-traces", "live-replay")).Single();
            using var manifest = JsonDocument.Parse(File.ReadAllText(Path.Combine(directory, "manifest.json")));
            var capture = manifest.RootElement.GetProperty("captureMetrics");
            Assert.AreEqual(metrics.DroppedFrames, capture.GetProperty("droppedFrameCount").GetInt64());
            Assert.AreEqual(attempts, capture.GetProperty("receivedFrameCount").GetInt64());
            Assert.IsTrue(capture.TryGetProperty("gapCount", out _));
            Assert.IsTrue(capture.TryGetProperty("missingSourceTickCount", out _));
        }
        finally
        {
            if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
        }
    }

    [TestMethod]
    public void ReplayV2Capture_RecordsNativeTickGapsExplicitly()
    {
        var root = TemporaryDirectory("replay-gap");
        try
        {
            var first = BuildFrames(60, seconds: 1, cars: 1, includeEvent: false)[0];
            using (var store = new LiveReplayCaptureStore(() => root))
            {
                store.Capture(first);
                store.Capture(first with
                {
                    CapturedAt = first.CapturedAt.AddSeconds(10d / 60),
                    SessionTimeSeconds = 10d / 60,
                    SourceTick = 10
                });
                store.EndSession("gap_test_complete");
            }
            var directory = Directory.GetDirectories(Path.Combine(root, "telemetry-traces", "live-replay")).Single();
            using var manifest = JsonDocument.Parse(File.ReadAllText(Path.Combine(directory, "manifest.json")));
            var capture = manifest.RootElement.GetProperty("captureMetrics");
            Assert.AreEqual(1, capture.GetProperty("gapCount").GetInt64());
            Assert.AreEqual(9, capture.GetProperty("missingSourceTickCount").GetInt64());
        }
        finally
        {
            if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
        }
    }

    [TestMethod]
    public void ReplayV2Capture_RecoversLegacyV1ChunksWithoutRewritingThem()
    {
        var root = TemporaryDirectory("replay-v1-recovery");
        try
        {
            var directory = Path.Combine(root, "telemetry-traces", "live-replay", "session-a");
            Directory.CreateDirectory(directory);
            var capturedAt = DateTimeOffset.Parse("2026-08-10T12:00:00Z");
            var legacyPath = Path.Combine(directory, "chunk-000000.json");
            File.WriteAllText(legacyPath, $$"""
            {"schemaVersion":1,"startSessionTimeSeconds":0,"endSessionTimeSeconds":0,"frames":[{"capturedAt":"{{capturedAt:O}}","sessionTimeSeconds":0,"cars":[{"carIndex":0}]}]}
            """);
            var originalLegacyBytes = File.ReadAllBytes(legacyPath);
            var frame = BuildFrames(10, seconds: 1, cars: 1, includeEvent: false)[0] with
            {
                SessionKey = "session-a",
                CapturedAt = capturedAt.AddSeconds(1),
                SessionTimeSeconds = 1,
                SourceTick = 1,
                SourceTickRate = 10
            };
            using (var store = new LiveReplayCaptureStore(() => root))
            {
                store.Capture(frame);
                store.EndSession("resumed");
            }

            CollectionAssert.AreEqual(originalLegacyBytes, File.ReadAllBytes(legacyPath));
            Assert.IsTrue(File.Exists(Path.Combine(directory, $"chunk-000001{LiveReplayChunkCodec.FileExtension}")));
            using var manifest = JsonDocument.Parse(File.ReadAllText(Path.Combine(directory, "manifest.json")));
            Assert.AreEqual(2, manifest.RootElement.GetProperty("frameCount").GetInt32());
            var formats = manifest.RootElement.GetProperty("chunks").EnumerateArray().Select(item => item.GetProperty("format").GetString()).ToArray();
            CollectionAssert.Contains(formats, "json-v1");
            CollectionAssert.Contains(formats, "delta-binary-gzip-v2");
        }
        finally
        {
            if (Directory.Exists(root)) Directory.Delete(root, recursive: true);
        }
    }

    private static IReadOnlyList<LiveReplayCaptureFrame> BuildFrames(int rate, int seconds, int cars, bool includeEvent)
    {
        var start = DateTimeOffset.Parse("2026-08-10T12:00:00Z");
        var coverage = new[]
        {
            new LiveReplayChannelCoverage("CarIdxLapDistPct", true, null),
            new LiveReplayChannelCoverage("PlayerCarMyIncidentCount", true, null)
        };
        var participants = Enumerable.Range(0, cars)
            .Select(index => new LiveReplayParticipant(index, (index + 1).ToString(), index % 2, $"Class {index % 2}", "Car", $"Driver {index}", null, false))
            .ToArray();
        var result = new List<LiveReplayCaptureFrame>(rate * seconds);
        for (var frameIndex = 0; frameIndex < rate * seconds; frameIndex++)
        {
            var time = frameIndex / (double)rate;
            var incidentPoints = includeEvent && frameIndex >= rate ? 2 : 0;
            var events = includeEvent && frameIndex == rate
                ? new[] { new LiveReplayObservedEvent("incident_points", "Incident points changed", "PlayerCarMyIncidentCount", 2) }
                : [];
            var carSamples = Enumerable.Range(0, cars).Select(index =>
            {
                var distance = (index / (double)Math.Max(1, cars) + time / 24d) % 1;
                return new LiveReplayCarSample(index, distance, 1, 0, index + 1, index / 2 + 1, false, 3, 0, 24.5 + index / 100d, 24.2 + index / 100d);
            }).ToArray();
            result.Add(new LiveReplayCaptureFrame(
                "session-a", start.AddSeconds(time), time, 4, 4, 10, 20, 0, "Race", 0,
                coverage, participants, carSamples, frameIndex, rate,
                new LiveReplayPlayerTelemetry(incidentPoints, incidentPoints, incidentPoints, 3, false, false, false, 0, 0,
                    45 + Math.Sin(time), .8 + Math.Sin(time * 2) * .1, .05, .1, 4, 7_500, .02, 1.2, .1),
                events));
        }
        return result;
    }

    private static string TemporaryDirectory(string name) =>
        Path.Combine(Path.GetTempPath(), $"iracing-coach-{name}-{Guid.NewGuid():N}");

    private sealed class DisconnectedSource : ILiveTelemetrySource
    {
        public bool TryRead(out LiveTelemetrySample sample) { sample = new(); return false; }
        public void Dispose() { }
    }
}
