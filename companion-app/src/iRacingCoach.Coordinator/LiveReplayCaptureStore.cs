using System.Collections.Concurrent;
using System.Diagnostics;
using System.Security.Cryptography;
using System.Text.Json;
using System.Threading.Channels;

namespace iRacingCoach.Coordinator;

/// <summary>
/// Persists recorded live SDK fields without blocking the telemetry thread.
/// Capture commands are bounded, chunks are independently recoverable, and
/// no source IBT or legacy v1 replay chunk is ever modified.
/// </summary>
public sealed class LiveReplayCaptureStore : IDisposable
{
    private const int DefaultQueueCapacity = 2_048;
    private const int MaximumFramesPerChunk = LiveReplayChunkCodec.MaximumFramesPerChunk;
    private const int MaximumLegacyChunkBytes = 128 * 1024 * 1024;
    private const int MaximumManifestBytes = 4 * 1024 * 1024;
    private static readonly TimeSpan DefaultChunkDuration = TimeSpan.FromSeconds(10);
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web) { WriteIndented = true };
    private readonly Func<string> _archiveRoot;
    private readonly object _lifecycleGate = new();
    private readonly Channel<ReplayWriteCommand> _commands;
    private readonly Task _writerTask;
    private readonly TimeSpan _chunkDuration;
    private readonly ConcurrentDictionary<string, CaptureIngressMetrics> _sessionIngress = new(StringComparer.Ordinal);
    private ActiveCapture? _active;
    private bool _disposed;
    private long _receivedFrames;
    private long _enqueuedFrames;
    private long _droppedFrames;
    private int _queueDepth;
    private int _maximumQueueDepth;

    public LiveReplayCaptureStore(Func<string> archiveRoot, int queueCapacity = DefaultQueueCapacity, TimeSpan? chunkDuration = null)
    {
        ArgumentNullException.ThrowIfNull(archiveRoot);
        if (queueCapacity < 2) throw new ArgumentOutOfRangeException(nameof(queueCapacity), "Replay queue capacity must be at least two.");
        _archiveRoot = archiveRoot;
        _chunkDuration = chunkDuration is { } configured && configured > TimeSpan.Zero ? configured : DefaultChunkDuration;
        _commands = Channel.CreateBounded<ReplayWriteCommand>(new BoundedChannelOptions(queueCapacity)
        {
            SingleReader = true,
            SingleWriter = false,
            AllowSynchronousContinuations = false,
            FullMode = BoundedChannelFullMode.Wait
        });
        _writerTask = Task.Run(ProcessQueueAsync);
    }

    public string? LastError { get; private set; }

    public LiveReplayCaptureMetrics Metrics => new(
        Interlocked.Read(ref _receivedFrames),
        Interlocked.Read(ref _enqueuedFrames),
        Interlocked.Read(ref _droppedFrames),
        Volatile.Read(ref _queueDepth),
        Volatile.Read(ref _maximumQueueDepth),
        _active?.SessionKey);

    public void Capture(LiveReplayCaptureFrame frame)
    {
        ArgumentNullException.ThrowIfNull(frame);
        lock (_lifecycleGate)
        {
            if (_disposed) return;
            Interlocked.Increment(ref _receivedFrames);
            var ingress = _sessionIngress.GetOrAdd(frame.SessionKey, static _ => new CaptureIngressMetrics());
            Interlocked.Increment(ref ingress.Received);
            if (_commands.Writer.TryWrite(new CaptureCommand(frame)))
            {
                Interlocked.Increment(ref _enqueuedFrames);
                Interlocked.Increment(ref ingress.Enqueued);
                var depth = Interlocked.Increment(ref _queueDepth);
                UpdateMaximum(ref _maximumQueueDepth, depth);
                UpdateMaximum(ref ingress.MaximumQueueDepth, depth);
                return;
            }
            Interlocked.Increment(ref _droppedFrames);
            Interlocked.Increment(ref ingress.Dropped);
            LastError = "Replay capture could not keep up; skipped frames are recorded in capture metrics.";
        }
    }

    public void EndSession(string reason)
    {
        Task completion;
        lock (_lifecycleGate)
        {
            if (_disposed) return;
            var barrier = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
            try
            {
                _commands.Writer.WriteAsync(new EndSessionCommand(
                    string.IsNullOrWhiteSpace(reason) ? "ended" : reason,
                    barrier)).AsTask().GetAwaiter().GetResult();
            }
            catch (ChannelClosedException)
            {
                barrier.TrySetResult();
            }
            completion = barrier.Task;
        }
        completion.GetAwaiter().GetResult();
    }

    private async Task ProcessQueueAsync()
    {
        await foreach (var command in _commands.Reader.ReadAllAsync())
        {
            if (command is CaptureCommand) Interlocked.Decrement(ref _queueDepth);
            try
            {
                switch (command)
                {
                    case CaptureCommand capture:
                        ProcessCapture(capture.Frame);
                        break;
                    case EndSessionCommand end:
                        FinalizeActive(end.Reason);
                        end.Completion.TrySetResult();
                        break;
                }
                if (LastError is not { Length: > 0 } || !LastError.Contains("skipped frames", StringComparison.Ordinal))
                    LastError = null;
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or ArgumentException or JsonException or InvalidDataException or InvalidOperationException)
            {
                LastError = ex.Message;
                if (command is EndSessionCommand end) end.Completion.TrySetResult();
            }
        }
    }

    private void ProcessCapture(LiveReplayCaptureFrame frame)
    {
        if (_active is null || !string.Equals(_active.SessionKey, frame.SessionKey, StringComparison.Ordinal))
        {
            FinalizeActive("session_changed");
            _active = Begin(frame);
        }
        if (!_active.AcceptSourceTick(frame)) return;
        _active.Frames.Add(frame);
        _active.Coverage = frame.Coverage;
        if (frame.Participants.Count > 0) _active.Participants = frame.Participants;
        var chunkElapsed = frame.CapturedAt - _active.Frames[0].CapturedAt;
        if (_active.Frames.Count >= MaximumFramesPerChunk || chunkElapsed >= _chunkDuration) FlushChunk(_active);
    }

    private ActiveCapture Begin(LiveReplayCaptureFrame frame)
    {
        var archiveRoot = Path.GetFullPath(_archiveRoot());
        var baseDirectory = Path.GetFullPath(Path.Combine(archiveRoot, "telemetry-traces", "live-replay"));
        if (!baseDirectory.StartsWith(archiveRoot.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("The live replay path escaped the Coach data folder.");
        var safeKey = string.Concat(frame.SessionKey.Select(character =>
            char.IsLetterOrDigit(character) || character is '-' or '_' ? character : '-'));
        if (safeKey.Length == 0) throw new InvalidOperationException("Live replay session identity is empty.");
        var directory = Path.Combine(baseDirectory, safeKey);
        Directory.CreateDirectory(directory);
        var existingFiles = Directory.EnumerateFiles(directory, "chunk-*.*", SearchOption.TopDirectoryOnly)
            .Where(path => path.EndsWith(".json", StringComparison.OrdinalIgnoreCase) || path.EndsWith(LiveReplayChunkCodec.FileExtension, StringComparison.OrdinalIgnoreCase))
            .ToArray();
        var nextChunk = existingFiles
            .Select(path => int.TryParse(Path.GetFileNameWithoutExtension(path).AsSpan("chunk-".Length), out var value) ? value : -1)
            .DefaultIfEmpty(-1)
            .Max() + 1;
        var recovered = RecoverManifestMetrics(Path.Combine(directory, "manifest.json"));
        var ingress = _sessionIngress.GetOrAdd(frame.SessionKey, static _ => new CaptureIngressMetrics());
        var active = new ActiveCapture(
            frame.SessionKey,
            directory,
            frame.CapturedAt,
            frame.SessionUniqueId,
            frame.SubsessionId,
            frame.SessionNumber,
            frame.SessionType,
            frame.PlayerCarIndex,
            nextChunk,
            frame.Coverage,
            frame.Participants,
            ingress,
            recovered);
        active.Chunks.AddRange(RecoverChunks(existingFiles));
        return active;
    }

    private static IReadOnlyList<CaptureChunk> RecoverChunks(IEnumerable<string> paths)
    {
        var result = new List<CaptureChunk>();
        foreach (var path in paths.Order(StringComparer.OrdinalIgnoreCase))
        {
            try
            {
                var fileLength = new FileInfo(path).Length;
                var maximumLength = path.EndsWith(LiveReplayChunkCodec.FileExtension, StringComparison.OrdinalIgnoreCase)
                    ? LiveReplayChunkCodec.MaximumFileBytes
                    : MaximumLegacyChunkBytes;
                if (fileLength <= 0 || fileLength > maximumLength) continue;
                var bytes = File.ReadAllBytes(path);
                if (path.EndsWith(LiveReplayChunkCodec.FileExtension, StringComparison.OrdinalIgnoreCase))
                {
                    var header = LiveReplayChunkCodec.ReadHeader(bytes);
                    result.Add(new CaptureChunk(
                        Path.GetFileName(path),
                        Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant(),
                        "delta-binary-gzip-v2",
                        header.FrameCount,
                        header.StartCapturedAt,
                        header.EndCapturedAt,
                        header.StartSessionTimeSeconds,
                        header.EndSessionTimeSeconds,
                        header.UncompressedBytes,
                        bytes.Length));
                    continue;
                }
                using var document = JsonDocument.Parse(bytes);
                var root = document.RootElement;
                if (!root.TryGetProperty("frames", out var frames) || frames.ValueKind != JsonValueKind.Array || frames.GetArrayLength() == 0) continue;
                var first = frames[0];
                var last = frames[frames.GetArrayLength() - 1];
                var firstAt = first.TryGetProperty("capturedAt", out var firstValue) && firstValue.TryGetDateTimeOffset(out var parsedFirst)
                    ? parsedFirst : DateTimeOffset.MinValue;
                var lastAt = last.TryGetProperty("capturedAt", out var lastValue) && lastValue.TryGetDateTimeOffset(out var parsedLast)
                    ? parsedLast : firstAt;
                result.Add(new CaptureChunk(
                    Path.GetFileName(path),
                    Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant(),
                    "json-v1",
                    frames.GetArrayLength(),
                    firstAt,
                    lastAt,
                    JsonNullableDouble(root, "startSessionTimeSeconds"),
                    JsonNullableDouble(root, "endSessionTimeSeconds"),
                    bytes.Length,
                    bytes.Length));
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException or InvalidDataException or ArgumentException)
            {
                // Keep a corrupt/incomplete immutable chunk for diagnostics,
                // but never advertise it as usable in the manifest.
            }
        }
        return result;
    }

    private static RecoveredMetrics RecoverManifestMetrics(string path)
    {
        if (!File.Exists(path)) return new();
        try
        {
            if (new FileInfo(path).Length > MaximumManifestBytes) return new();
            using var document = JsonDocument.Parse(File.ReadAllBytes(path));
            var root = document.RootElement;
            if (!root.TryGetProperty("captureMetrics", out var metrics) || metrics.ValueKind != JsonValueKind.Object) return new();
            return new RecoveredMetrics(
                JsonInt64(metrics, "receivedFrameCount"),
                JsonInt64(metrics, "enqueuedFrameCount"),
                JsonInt64(metrics, "droppedFrameCount"),
                JsonInt64(metrics, "duplicateSourceTickCount"),
                JsonInt64(metrics, "gapCount"),
                JsonInt64(metrics, "missingSourceTickCount"),
                JsonInt32(metrics, "maximumQueueDepth"),
                JsonInt64(metrics, "uncompressedBytes"),
                JsonInt64(metrics, "storedBytes"),
                JsonDouble(metrics, "totalChunkWriteMilliseconds"),
                JsonInt64(metrics, "chunkWriteCount"));
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException)
        {
            return new();
        }
    }

    private static void FlushChunk(ActiveCapture capture)
    {
        if (capture.Frames.Count == 0) return;
        var frames = capture.Frames.ToArray();
        var stopwatch = Stopwatch.StartNew();
        var encoded = LiveReplayChunkCodec.Encode(frames);
        var sha256 = Convert.ToHexString(SHA256.HashData(encoded.Bytes)).ToLowerInvariant();
        string path;
        while (true)
        {
            path = Path.Combine(capture.Directory, $"chunk-{capture.NextChunk:000000}{LiveReplayChunkCodec.FileExtension}");
            if (!File.Exists(path)) break;
            string existing;
            using (var existingStream = File.OpenRead(path))
                existing = Convert.ToHexString(SHA256.HashData(existingStream)).ToLowerInvariant();
            if (existing == sha256)
            {
                capture.Chunks.Add(CaptureChunkFromEncoded(path, sha256, encoded));
                capture.NextChunk++;
                capture.Frames.Clear();
                WriteManifest(capture, "recording", null);
                return;
            }
            capture.NextChunk++;
        }
        AtomicWrite(path, encoded.Bytes, overwrite: false);
        stopwatch.Stop();
        capture.Chunks.Add(CaptureChunkFromEncoded(path, sha256, encoded));
        capture.UncompressedBytes += encoded.UncompressedBytes;
        capture.StoredBytes += encoded.Bytes.Length;
        capture.TotalChunkWriteMilliseconds += stopwatch.Elapsed.TotalMilliseconds;
        capture.ChunkWriteCount++;
        capture.NextChunk++;
        capture.Frames.Clear();
        WriteManifest(capture, "recording", null);
    }

    private static CaptureChunk CaptureChunkFromEncoded(string path, string sha256, LiveReplayEncodedChunk encoded) => new(
        Path.GetFileName(path), sha256, "delta-binary-gzip-v2", encoded.FrameCount,
        encoded.StartCapturedAt, encoded.EndCapturedAt, encoded.StartSessionTimeSeconds,
        encoded.EndSessionTimeSeconds, encoded.UncompressedBytes, encoded.Bytes.Length);

    private void FinalizeActive(string reason)
    {
        if (_active is null) return;
        FlushChunk(_active);
        WriteManifest(_active, "finalized", reason);
        _sessionIngress.TryRemove(_active.SessionKey, out _);
        _active = null;
    }

    private static void WriteManifest(ActiveCapture capture, string status, string? finalizationReason)
    {
        var unavailable = capture.Coverage
            .Where(item => !item.Recorded && !string.IsNullOrWhiteSpace(item.UnavailableReason))
            .Select(item => item.UnavailableReason!)
            .Distinct(StringComparer.Ordinal)
            .ToArray();
        var received = capture.Recovered.ReceivedFrames + Interlocked.Read(ref capture.Ingress.Received);
        var enqueued = capture.Recovered.EnqueuedFrames + Interlocked.Read(ref capture.Ingress.Enqueued);
        var dropped = capture.Recovered.DroppedFrames + Interlocked.Read(ref capture.Ingress.Dropped);
        var written = capture.Chunks.Sum(item => (long)item.FrameCount);
        var start = capture.Chunks.MinBy(item => item.StartCapturedAt)?.StartCapturedAt ?? capture.StartedAt;
        var end = capture.Chunks.MaxBy(item => item.EndCapturedAt)?.EndCapturedAt ?? start;
        var duration = Math.Max(0, (end - start).TotalSeconds);
        var observedRate = duration > 0 && written > 1 ? (written - 1) / duration : 0;
        var uncompressedBytes = capture.Recovered.UncompressedBytes + capture.UncompressedBytes;
        var storedBytes = capture.Recovered.StoredBytes + capture.StoredBytes;
        var totalWriteMilliseconds = capture.Recovered.TotalWriteMilliseconds + capture.TotalChunkWriteMilliseconds;
        var writeCount = capture.Recovered.WriteCount + capture.ChunkWriteCount;
        var manifest = new
        {
            schemaVersion = 2,
            format = "iracing-coach-live-replay-v2-delta-gzip",
            status,
            finalizationReason,
            capture.SessionKey,
            capture.SessionUniqueId,
            capture.SubsessionId,
            capture.SessionNumber,
            capture.SessionType,
            capture.PlayerCarIndex,
            startedAt = start,
            updatedAt = DateTimeOffset.UtcNow,
            frameCount = written,
            sampleRateHz = observedRate,
            targetSampleRateHz = 60,
            coverage = capture.Coverage,
            unavailableReasons = unavailable,
            participants = capture.Participants,
            chunks = capture.Chunks,
            captureMetrics = new
            {
                receivedFrameCount = received,
                enqueuedFrameCount = enqueued,
                writtenFrameCount = written,
                droppedFrameCount = dropped,
                duplicateSourceTickCount = capture.Recovered.DuplicateSourceTicks + capture.DuplicateSourceTicks,
                gapCount = capture.Recovered.GapCount + capture.GapCount,
                missingSourceTickCount = capture.Recovered.MissingSourceTicks + capture.MissingSourceTicks,
                currentQueueDepth = 0,
                maximumQueueDepth = Math.Max(capture.Recovered.MaximumQueueDepth, Volatile.Read(ref capture.Ingress.MaximumQueueDepth)),
                observedSampleRateHz = observedRate,
                minimumSourceTickRateHz = capture.MinimumSourceTickRate == int.MaxValue ? 0 : capture.MinimumSourceTickRate,
                maximumSourceTickRateHz = capture.MaximumSourceTickRate,
                uncompressedBytes,
                storedBytes,
                compressionRatio = uncompressedBytes > 0 ? (double)storedBytes / uncompressedBytes : 0,
                totalChunkWriteMilliseconds = totalWriteMilliseconds,
                averageChunkWriteMilliseconds = writeCount > 0 ? totalWriteMilliseconds / writeCount : 0,
                chunkWriteCount = writeCount
            },
            interpolation = "linear lap-distance interpolation only between contiguous recorded frames",
            retention = "append-only portable capture; source IBT and earlier replay chunks are never deleted or modified",
            eventSemantics = "Events are direct transitions in recorded player telemetry channels; contact type and fault are not inferred.",
            limitations = new[]
            {
                "Only channels marked recorded in coverage are present.",
                "Competitor fuel, tire condition, setup, incidents, and private penalties are not inferred.",
                "An incident-point increase does not identify car contact, wall contact, loss of control, or fault."
            }
        };
        AtomicWrite(
            Path.Combine(capture.Directory, "manifest.json"),
            JsonSerializer.SerializeToUtf8Bytes(manifest, JsonOptions),
            overwrite: true);
    }

    private static void AtomicWrite(string path, byte[] bytes, bool overwrite)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        var temporary = path + $".{Guid.NewGuid():N}.tmp";
        try
        {
            using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None, 64 * 1024, FileOptions.WriteThrough))
            {
                stream.Write(bytes);
                stream.Flush(true);
            }
            File.Move(temporary, path, overwrite);
        }
        finally
        {
            if (File.Exists(temporary)) File.Delete(temporary);
        }
    }

    public void Dispose()
    {
        Task completion;
        lock (_lifecycleGate)
        {
            if (_disposed) return;
            _disposed = true;
            var barrier = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
            try
            {
                _commands.Writer.WriteAsync(new EndSessionCommand("disposed", barrier)).AsTask().GetAwaiter().GetResult();
            }
            catch (ChannelClosedException)
            {
                barrier.TrySetResult();
            }
            _commands.Writer.TryComplete();
            completion = barrier.Task;
        }
        completion.GetAwaiter().GetResult();
        _writerTask.GetAwaiter().GetResult();
    }

    private static void UpdateMaximum(ref int target, int value)
    {
        var current = Volatile.Read(ref target);
        while (value > current)
        {
            var observed = Interlocked.CompareExchange(ref target, value, current);
            if (observed == current) return;
            current = observed;
        }
    }

    private static long JsonInt64(JsonElement root, string name) => root.TryGetProperty(name, out var value) && value.TryGetInt64(out var number) ? number : 0;
    private static int JsonInt32(JsonElement root, string name) => root.TryGetProperty(name, out var value) && value.TryGetInt32(out var number) ? number : 0;
    private static double JsonDouble(JsonElement root, string name) => root.TryGetProperty(name, out var value) && value.TryGetDouble(out var number) && double.IsFinite(number) ? number : 0;
    private static double? JsonNullableDouble(JsonElement root, string name) => root.TryGetProperty(name, out var value) && value.TryGetDouble(out var number) && double.IsFinite(number) ? number : null;

    private sealed class ActiveCapture(
        string sessionKey,
        string directory,
        DateTimeOffset startedAt,
        long? sessionUniqueId,
        long? subsessionId,
        int? sessionNumber,
        string? sessionType,
        int? playerCarIndex,
        int nextChunk,
        IReadOnlyList<LiveReplayChannelCoverage> coverage,
        IReadOnlyList<LiveReplayParticipant> participants,
        CaptureIngressMetrics ingress,
        RecoveredMetrics recovered)
    {
        public string SessionKey { get; } = sessionKey;
        public string Directory { get; } = directory;
        public DateTimeOffset StartedAt { get; } = startedAt;
        public long? SessionUniqueId { get; } = sessionUniqueId;
        public long? SubsessionId { get; } = subsessionId;
        public int? SessionNumber { get; } = sessionNumber;
        public string? SessionType { get; } = sessionType;
        public int? PlayerCarIndex { get; } = playerCarIndex;
        public int NextChunk { get; set; } = nextChunk;
        public List<LiveReplayCaptureFrame> Frames { get; } = [];
        public List<CaptureChunk> Chunks { get; } = [];
        public IReadOnlyList<LiveReplayChannelCoverage> Coverage { get; set; } = coverage;
        public IReadOnlyList<LiveReplayParticipant> Participants { get; set; } = participants;
        public CaptureIngressMetrics Ingress { get; } = ingress;
        public RecoveredMetrics Recovered { get; } = recovered;
        public long DuplicateSourceTicks { get; private set; }
        public long GapCount { get; private set; }
        public long MissingSourceTicks { get; private set; }
        public int MinimumSourceTickRate { get; private set; } = int.MaxValue;
        public int MaximumSourceTickRate { get; private set; }
        public long UncompressedBytes { get; set; }
        public long StoredBytes { get; set; }
        public double TotalChunkWriteMilliseconds { get; set; }
        public long ChunkWriteCount { get; set; }
        private int? _lastSourceTick;

        public bool AcceptSourceTick(LiveReplayCaptureFrame frame)
        {
            if (frame.SourceTickRate > 0)
            {
                MinimumSourceTickRate = Math.Min(MinimumSourceTickRate, frame.SourceTickRate);
                MaximumSourceTickRate = Math.Max(MaximumSourceTickRate, frame.SourceTickRate);
            }
            if (!_lastSourceTick.HasValue)
            {
                _lastSourceTick = frame.SourceTick;
                return true;
            }
            var delta = unchecked((uint)(frame.SourceTick - _lastSourceTick.Value));
            if (delta == 0)
            {
                DuplicateSourceTicks++;
                return false;
            }
            if (delta < 0x80000000u)
            {
                var expectedStep = Math.Max(1, (int)Math.Ceiling((frame.SourceTickRate > 0 ? frame.SourceTickRate : 60) / 60d));
                if (delta > expectedStep)
                {
                    GapCount++;
                    MissingSourceTicks += Math.Max(1, (long)Math.Ceiling(delta / (double)expectedStep) - 1);
                }
            }
            _lastSourceTick = frame.SourceTick;
            return true;
        }
    }

    private sealed class CaptureIngressMetrics
    {
        public long Received;
        public long Enqueued;
        public long Dropped;
        public int MaximumQueueDepth;
    }

    private sealed record CaptureChunk(
        string File,
        string Sha256,
        string Format,
        int FrameCount,
        DateTimeOffset StartCapturedAt,
        DateTimeOffset EndCapturedAt,
        double? StartSessionTimeSeconds,
        double? EndSessionTimeSeconds,
        long UncompressedBytes,
        long StoredBytes);

    private sealed record RecoveredMetrics(
        long ReceivedFrames = 0,
        long EnqueuedFrames = 0,
        long DroppedFrames = 0,
        long DuplicateSourceTicks = 0,
        long GapCount = 0,
        long MissingSourceTicks = 0,
        int MaximumQueueDepth = 0,
        long UncompressedBytes = 0,
        long StoredBytes = 0,
        double TotalWriteMilliseconds = 0,
        long WriteCount = 0);

    private abstract record ReplayWriteCommand;
    private sealed record CaptureCommand(LiveReplayCaptureFrame Frame) : ReplayWriteCommand;
    private sealed record EndSessionCommand(string Reason, TaskCompletionSource Completion) : ReplayWriteCommand;
}

public sealed record LiveReplayCaptureMetrics(
    long ReceivedFrames,
    long EnqueuedFrames,
    long DroppedFrames,
    int QueueDepth,
    int MaximumQueueDepth,
    string? ActiveSessionKey);
