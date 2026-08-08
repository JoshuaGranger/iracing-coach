using System.Security.Cryptography;
using System.Text.Json;
using System.Threading.Channels;

namespace iRacingCoach.Coordinator;

/// <summary>
/// Persists only recorded live SDK fields in small atomic chunks. The writer
/// never manufactures competitor rows and never modifies source IBT files.
/// </summary>
public sealed class LiveReplayCaptureStore : IDisposable
{
    private const int FramesPerChunk = 20;
    private static readonly JsonSerializerOptions JsonOptions = new(JsonSerializerDefaults.Web) { WriteIndented = true };
    private readonly Func<string> _archiveRoot;
    private readonly object _lifecycleGate = new();
    private readonly Channel<ReplayWriteCommand> _commands;
    private readonly Task _writerTask;
    private ActiveCapture? _active;
    private bool _disposed;

    public LiveReplayCaptureStore(Func<string> archiveRoot)
    {
        _archiveRoot = archiveRoot;
        _commands = Channel.CreateUnbounded<ReplayWriteCommand>(new UnboundedChannelOptions
        {
            SingleReader = true,
            SingleWriter = false,
            AllowSynchronousContinuations = false
        });
        _writerTask = Task.Run(ProcessQueueAsync);
    }

    public string? LastError { get; private set; }

    public void Capture(LiveReplayCaptureFrame frame)
    {
        lock (_lifecycleGate)
        {
            if (_disposed) return;
            if (!_commands.Writer.TryWrite(new CaptureCommand(frame)))
                LastError = "The ordered live-replay writer is unavailable.";
        }
    }

    public void EndSession(string reason)
    {
        Task completion;
        lock (_lifecycleGate)
        {
            if (_disposed) return;
            var barrier = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
            if (!_commands.Writer.TryWrite(new EndSessionCommand(
                    string.IsNullOrWhiteSpace(reason) ? "ended" : reason,
                    barrier)))
                barrier.TrySetResult();
            completion = barrier.Task;
        }
        completion.GetAwaiter().GetResult();
    }

    private async Task ProcessQueueAsync()
    {
        await foreach (var command in _commands.Reader.ReadAllAsync())
        {
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
                LastError = null;
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or ArgumentException or JsonException or InvalidOperationException)
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
        _active.Frames.Add(frame);
        _active.Coverage = frame.Coverage;
        if (frame.Participants.Count > 0) _active.Participants = frame.Participants;
        if (_active.Frames.Count >= FramesPerChunk) FlushChunk(_active);
    }

    private ActiveCapture Begin(LiveReplayCaptureFrame frame)
    {
        var archiveRoot = Path.GetFullPath(_archiveRoot());
        var baseDirectory = Path.GetFullPath(Path.Combine(archiveRoot, "telemetry-traces", "live-replay"));
        if (!baseDirectory.StartsWith(archiveRoot.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("The live replay path escaped the portable archive.");
        var safeKey = string.Concat(frame.SessionKey.Select(character =>
            char.IsLetterOrDigit(character) || character is '-' or '_' ? character : '-'));
        if (safeKey.Length == 0) throw new InvalidOperationException("Live replay session identity is empty.");
        var directory = Path.Combine(baseDirectory, safeKey);
        Directory.CreateDirectory(directory);
        var existingFiles = Directory.EnumerateFiles(directory, "chunk-*.json", SearchOption.TopDirectoryOnly).ToArray();
        var nextChunk = existingFiles
            .Select(path => int.TryParse(Path.GetFileNameWithoutExtension(path).AsSpan("chunk-".Length), out var value) ? value : -1)
            .DefaultIfEmpty(-1)
            .Max() + 1;
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
            frame.Participants);
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
                var bytes = File.ReadAllBytes(path);
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
                    frames.GetArrayLength(),
                    firstAt,
                    lastAt));
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or JsonException)
            {
                // A corrupt/incomplete chunk is excluded. The immutable file is
                // retained for diagnostics and never rewritten into the index.
            }
        }
        return result;
    }

    private static void FlushChunk(ActiveCapture capture)
    {
        if (capture.Frames.Count == 0) return;
        var frames = capture.Frames.ToArray();
        var payload = new
        {
            schemaVersion = 1,
            sessionKey = capture.SessionKey,
            frameCount = frames.Length,
            startSessionTimeSeconds = frames.First().SessionTimeSeconds,
            endSessionTimeSeconds = frames.Last().SessionTimeSeconds,
            frames
        };
        var bytes = JsonSerializer.SerializeToUtf8Bytes(payload, JsonOptions);
        var sha256 = Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
        string path;
        while (true)
        {
            path = Path.Combine(capture.Directory, $"chunk-{capture.NextChunk:000000}.json");
            if (!File.Exists(path)) break;
            var existing = Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(path))).ToLowerInvariant();
            if (existing == sha256)
            {
                capture.Chunks.Add(new CaptureChunk(Path.GetFileName(path), sha256, frames.Length, frames.First().CapturedAt, frames.Last().CapturedAt));
                capture.NextChunk++;
                capture.Frames.Clear();
                WriteManifest(capture, "recording", null);
                return;
            }
            capture.NextChunk++;
        }
        AtomicWrite(path, bytes, overwrite: false);
        capture.Chunks.Add(new CaptureChunk(Path.GetFileName(path), sha256, frames.Length, frames.First().CapturedAt, frames.Last().CapturedAt));
        capture.NextChunk++;
        capture.Frames.Clear();
        WriteManifest(capture, "recording", null);
    }

    private void FinalizeActive(string reason)
    {
        if (_active is null) return;
        FlushChunk(_active);
        WriteManifest(_active, "finalized", reason);
        _active = null;
    }

    private static void WriteManifest(ActiveCapture capture, string status, string? finalizationReason)
    {
        var unavailable = capture.Coverage
            .Where(item => !item.Recorded && !string.IsNullOrWhiteSpace(item.UnavailableReason))
            .Select(item => item.UnavailableReason!)
            .Distinct(StringComparer.Ordinal)
            .ToArray();
        var manifest = new
        {
            schemaVersion = 1,
            status,
            finalizationReason,
            capture.SessionKey,
            capture.SessionUniqueId,
            capture.SubsessionId,
            capture.SessionNumber,
            capture.SessionType,
            capture.PlayerCarIndex,
            startedAt = capture.StartedAt,
            updatedAt = DateTimeOffset.UtcNow,
            frameCount = capture.Chunks.Sum(item => item.FrameCount),
            coverage = capture.Coverage,
            unavailableReasons = unavailable,
            participants = capture.Participants,
            chunks = capture.Chunks,
            interpolation = "linear lap-distance interpolation between recorded capture frames",
            retention = "append-only portable capture; no source IBT is deleted or modified",
            limitations = new[]
            {
                "Only channels marked recorded in coverage are present.",
                "Competitor fuel, tire condition, setup, and private penalties are not inferred."
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
            if (!_commands.Writer.TryWrite(new EndSessionCommand("disposed", barrier)))
                barrier.TrySetResult();
            _commands.Writer.TryComplete();
            completion = barrier.Task;
        }
        completion.GetAwaiter().GetResult();
        _writerTask.GetAwaiter().GetResult();
    }

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
        IReadOnlyList<LiveReplayParticipant> participants)
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
    }

    private sealed record CaptureChunk(
        string File,
        string Sha256,
        int FrameCount,
        DateTimeOffset StartCapturedAt,
        DateTimeOffset EndCapturedAt);

    private abstract record ReplayWriteCommand;
    private sealed record CaptureCommand(LiveReplayCaptureFrame Frame) : ReplayWriteCommand;
    private sealed record EndSessionCommand(
        string Reason,
        TaskCompletionSource Completion) : ReplayWriteCommand;
}
