using System.IO.Compression;
using System.Text;

namespace iRacingCoach.Coordinator;

/// <summary>
/// Compact, independently recoverable replay chunk. Car and player fields are
/// delta encoded before gzip compression; every chunk starts with a complete
/// state so a missing/corrupt chunk never poisons the following one.
/// </summary>
public static class LiveReplayChunkCodec
{
    public const int SchemaVersion = 2;
    public const string FileExtension = ".ircr2";
    public const int MaximumFramesPerChunk = 1_200;
    public const int MaximumCompressedBytes = 64 * 1024 * 1024;
    public const int MaximumUncompressedBytes = 128 * 1024 * 1024;
    public const int MaximumFileBytes = MaximumCompressedBytes + 56;
    private const int HeaderBytes = 56;
    private static readonly byte[] Magic = "IRCRPLY2"u8.ToArray();

    private const uint CarLapPercent = 1u << 0;
    private const uint CarLap = 1u << 1;
    private const uint CarCompletedLaps = 1u << 2;
    private const uint CarOverallPosition = 1u << 3;
    private const uint CarClassPosition = 1u << 4;
    private const uint CarOnPitRoad = 1u << 5;
    private const uint CarTrackSurface = 1u << 6;
    private const uint CarPaceFlags = 1u << 7;
    private const uint CarLastLap = 1u << 8;
    private const uint CarBestLap = 1u << 9;
    private const uint AllCarFields = (1u << 10) - 1;

    private const uint PlayerIncidentPoints = 1u << 0;
    private const uint PlayerDriverIncidentPoints = 1u << 1;
    private const uint PlayerTeamIncidentPoints = 1u << 2;
    private const uint PlayerTrackSurface = 1u << 3;
    private const uint PlayerOnPitRoad = 1u << 4;
    private const uint PlayerTowing = 1u << 5;
    private const uint PlayerRepairRequired = 1u << 6;
    private const uint PlayerMandatoryRepair = 1u << 7;
    private const uint PlayerOptionalRepair = 1u << 8;
    private const uint PlayerSpeed = 1u << 9;
    private const uint PlayerThrottle = 1u << 10;
    private const uint PlayerBrake = 1u << 11;
    private const uint PlayerSteering = 1u << 12;
    private const uint PlayerGear = 1u << 13;
    private const uint PlayerRpm = 1u << 14;
    private const uint PlayerYawRate = 1u << 15;
    private const uint PlayerLateralG = 1u << 16;
    private const uint PlayerLongitudinalG = 1u << 17;
    private const uint AllPlayerFields = (1u << 18) - 1;

    public static LiveReplayEncodedChunk Encode(IReadOnlyList<LiveReplayCaptureFrame> frames)
    {
        ArgumentNullException.ThrowIfNull(frames);
        if (frames.Count == 0) throw new ArgumentException("A replay chunk needs at least one frame.", nameof(frames));
        if (frames.Count > MaximumFramesPerChunk) throw new ArgumentException($"A replay chunk cannot exceed {MaximumFramesPerChunk:N0} frames.", nameof(frames));

        var first = frames[0];
        using var raw = new MemoryStream(Math.Max(16 * 1024, frames.Count * 256));
        using (var writer = new BinaryWriter(raw, Encoding.UTF8, leaveOpen: true))
        {
            WriteCommonMetadata(writer, first);
            writer.Write(frames.Count);
            var previousCars = new Dictionary<int, LiveReplayCarSample>();
            LiveReplayPlayerTelemetry? previousPlayer = null;
            foreach (var frame in frames)
            {
                writer.Write(frame.CapturedAt.ToUnixTimeMilliseconds());
                WriteNullableDouble(writer, frame.SessionTimeSeconds);
                WriteNullableInt32(writer, frame.SessionState);
                WriteNullableInt64(writer, frame.SessionFlags);
                writer.Write(frame.SourceTick);
                writer.Write(frame.SourceTickRate);
                WritePlayerTelemetry(writer, frame.PlayerTelemetry, previousPlayer);
                previousPlayer = frame.PlayerTelemetry;
                WriteEvents(writer, frame.Events);
                WriteCars(writer, frame.Cars, previousCars);
            }
        }

        if (raw.Length > MaximumUncompressedBytes) throw new InvalidDataException("Replay chunk payload exceeds the safe uncompressed limit.");
        var uncompressed = raw.ToArray();
        byte[] compressed;
        using (var output = new MemoryStream(uncompressed.Length / 2))
        {
            using (var gzip = new GZipStream(output, CompressionLevel.Optimal, leaveOpen: true))
                gzip.Write(uncompressed);
            compressed = output.ToArray();
        }
        if (compressed.Length > MaximumCompressedBytes) throw new InvalidDataException("Replay chunk payload exceeds the safe compressed limit.");

        using var file = new MemoryStream(compressed.Length + 64);
        using (var writer = new BinaryWriter(file, Encoding.UTF8, leaveOpen: true))
        {
            writer.Write(Magic);
            writer.Write(SchemaVersion);
            writer.Write(frames.Count);
            writer.Write(uncompressed.Length);
            writer.Write(compressed.Length);
            writer.Write(frames[0].CapturedAt.ToUnixTimeMilliseconds());
            writer.Write(frames[^1].CapturedAt.ToUnixTimeMilliseconds());
            writer.Write(frames[0].SessionTimeSeconds ?? double.NaN);
            writer.Write(frames[^1].SessionTimeSeconds ?? double.NaN);
            writer.Write(compressed);
        }
        return new LiveReplayEncodedChunk(
            file.ToArray(),
            frames.Count,
            frames[0].CapturedAt,
            frames[^1].CapturedAt,
            frames[0].SessionTimeSeconds,
            frames[^1].SessionTimeSeconds,
            uncompressed.Length,
            compressed.Length);
    }

    public static LiveReplayDecodedChunk Decode(ReadOnlySpan<byte> fileBytes)
    {
        ValidateFileLength(fileBytes.Length);
        using var file = new MemoryStream(fileBytes.ToArray(), writable: false);
        using var reader = new BinaryReader(file, Encoding.UTF8, leaveOpen: true);
        var magic = reader.ReadBytes(Magic.Length);
        if (!magic.AsSpan().SequenceEqual(Magic)) throw new InvalidDataException("This is not an iRacing Coach replay v2 chunk.");
        var version = reader.ReadInt32();
        if (version != SchemaVersion) throw new InvalidDataException($"Unsupported replay chunk version {version}.");
        var headerFrameCount = reader.ReadInt32();
        var uncompressedLength = reader.ReadInt32();
        var compressedLength = reader.ReadInt32();
        var startCapturedAt = DateTimeOffset.FromUnixTimeMilliseconds(reader.ReadInt64());
        var endCapturedAt = DateTimeOffset.FromUnixTimeMilliseconds(reader.ReadInt64());
        var startSessionTime = FiniteOrNull(reader.ReadDouble());
        var endSessionTime = FiniteOrNull(reader.ReadDouble());
        if (headerFrameCount <= 0 || headerFrameCount > MaximumFramesPerChunk
            || uncompressedLength <= 0 || uncompressedLength > MaximumUncompressedBytes
            || compressedLength <= 0 || compressedLength > MaximumCompressedBytes
            || compressedLength != file.Length - file.Position)
            throw new InvalidDataException("Replay chunk header lengths are invalid.");
        var compressed = reader.ReadBytes(compressedLength);
        if (compressed.Length != compressedLength || file.Position != file.Length)
            throw new InvalidDataException("Replay chunk is truncated or has trailing data.");

        var uncompressed = GC.AllocateUninitializedArray<byte>(uncompressedLength);
        using (var compressedStream = new MemoryStream(compressed, writable: false))
        using (var gzip = new GZipStream(compressedStream, CompressionMode.Decompress))
        {
            var offset = 0;
            while (offset < uncompressed.Length)
            {
                var read = gzip.Read(uncompressed, offset, uncompressed.Length - offset);
                if (read == 0) break;
                offset += read;
            }
            if (offset != uncompressed.Length || gzip.ReadByte() != -1)
                throw new InvalidDataException("Replay chunk decompressed length is invalid.");
        }

        using var raw = new MemoryStream(uncompressed, writable: false);
        using var payload = new BinaryReader(raw, Encoding.UTF8, leaveOpen: true);
        var common = ReadCommonMetadata(payload);
        var frameCount = payload.ReadInt32();
        if (frameCount != headerFrameCount) throw new InvalidDataException("Replay frame counts disagree.");
        var frames = new List<LiveReplayCaptureFrame>(frameCount);
        var previousCars = new Dictionary<int, LiveReplayCarSample>();
        LiveReplayPlayerTelemetry? previousPlayer = null;
        for (var index = 0; index < frameCount; index++)
        {
            var capturedAt = DateTimeOffset.FromUnixTimeMilliseconds(payload.ReadInt64());
            var sessionTime = ReadNullableDouble(payload);
            var sessionState = ReadNullableInt32(payload);
            var sessionFlags = ReadNullableInt64(payload);
            var sourceTick = payload.ReadInt32();
            var sourceTickRate = payload.ReadInt32();
            var player = ReadPlayerTelemetry(payload, previousPlayer);
            previousPlayer = player;
            var events = ReadEvents(payload);
            var cars = ReadCars(payload, previousCars);
            frames.Add(new LiveReplayCaptureFrame(
                common.SessionKey, capturedAt, sessionTime, sessionState, sessionFlags,
                common.SessionUniqueId, common.SubsessionId, common.SessionNumber,
                common.SessionType, common.PlayerCarIndex, common.Coverage,
                common.Participants, cars, sourceTick, sourceTickRate, player, events));
        }
        if (raw.Position != raw.Length) throw new InvalidDataException("Replay payload has trailing data.");
        return new LiveReplayDecodedChunk(
            frames, startCapturedAt, endCapturedAt, startSessionTime, endSessionTime,
            uncompressedLength, compressedLength);
    }

    public static LiveReplayChunkHeader ReadHeader(ReadOnlySpan<byte> fileBytes)
    {
        ValidateFileLength(fileBytes.Length);
        using var file = new MemoryStream(fileBytes.ToArray(), writable: false);
        using var reader = new BinaryReader(file, Encoding.UTF8, leaveOpen: true);
        if (!reader.ReadBytes(Magic.Length).AsSpan().SequenceEqual(Magic)) throw new InvalidDataException("This is not an iRacing Coach replay v2 chunk.");
        var version = reader.ReadInt32();
        if (version != SchemaVersion) throw new InvalidDataException($"Unsupported replay chunk version {version}.");
        var frames = reader.ReadInt32();
        var uncompressed = reader.ReadInt32();
        var compressed = reader.ReadInt32();
        var start = DateTimeOffset.FromUnixTimeMilliseconds(reader.ReadInt64());
        var end = DateTimeOffset.FromUnixTimeMilliseconds(reader.ReadInt64());
        var startSession = FiniteOrNull(reader.ReadDouble());
        var endSession = FiniteOrNull(reader.ReadDouble());
        if (frames <= 0 || frames > MaximumFramesPerChunk
            || uncompressed <= 0 || uncompressed > MaximumUncompressedBytes
            || compressed <= 0 || compressed > MaximumCompressedBytes
            || compressed != file.Length - file.Position)
            throw new InvalidDataException("Replay chunk header lengths are invalid.");
        return new LiveReplayChunkHeader(frames, start, end, startSession, endSession, uncompressed, compressed);
    }

    private static void ValidateFileLength(int length)
    {
        if (length < HeaderBytes + 1 || length > MaximumFileBytes)
            throw new InvalidDataException("Replay chunk size is invalid.");
    }

    private static void WriteCommonMetadata(BinaryWriter writer, LiveReplayCaptureFrame frame)
    {
        writer.Write(frame.SessionKey);
        WriteNullableInt64(writer, frame.SessionUniqueId);
        WriteNullableInt64(writer, frame.SubsessionId);
        WriteNullableInt32(writer, frame.SessionNumber);
        WriteNullableString(writer, frame.SessionType);
        WriteNullableInt32(writer, frame.PlayerCarIndex);
        writer.Write(frame.Coverage.Count);
        foreach (var item in frame.Coverage)
        {
            writer.Write(item.Channel);
            writer.Write(item.Recorded);
            WriteNullableString(writer, item.UnavailableReason);
        }
        writer.Write(frame.Participants.Count);
        foreach (var item in frame.Participants)
        {
            writer.Write(item.CarIndex);
            WriteNullableString(writer, item.CarNumber);
            WriteNullableInt32(writer, item.ClassId);
            WriteNullableString(writer, item.ClassName);
            WriteNullableString(writer, item.CarName);
            WriteNullableString(writer, item.DriverName);
            WriteNullableString(writer, item.TeamName);
            WriteNullableBoolean(writer, item.IsSpectator);
        }
    }

    private static CommonMetadata ReadCommonMetadata(BinaryReader reader)
    {
        var sessionKey = reader.ReadString();
        var sessionUniqueId = ReadNullableInt64(reader);
        var subsessionId = ReadNullableInt64(reader);
        var sessionNumber = ReadNullableInt32(reader);
        var sessionType = ReadNullableString(reader);
        var playerCarIndex = ReadNullableInt32(reader);
        var coverageCount = ReadBoundedCount(reader, 1_024, "coverage");
        var coverage = new List<LiveReplayChannelCoverage>(coverageCount);
        for (var index = 0; index < coverageCount; index++)
            coverage.Add(new(reader.ReadString(), reader.ReadBoolean(), ReadNullableString(reader)));
        var participantCount = ReadBoundedCount(reader, 512, "participants");
        var participants = new List<LiveReplayParticipant>(participantCount);
        for (var index = 0; index < participantCount; index++)
            participants.Add(new(
                reader.ReadInt32(), ReadNullableString(reader), ReadNullableInt32(reader),
                ReadNullableString(reader), ReadNullableString(reader), ReadNullableString(reader),
                ReadNullableString(reader), ReadNullableBoolean(reader)));
        return new CommonMetadata(sessionKey, sessionUniqueId, subsessionId, sessionNumber, sessionType, playerCarIndex, coverage, participants);
    }

    private static void WriteCars(BinaryWriter writer, IReadOnlyList<LiveReplayCarSample> cars, IDictionary<int, LiveReplayCarSample> prior)
    {
        writer.Write(cars.Count);
        var present = new HashSet<int>();
        foreach (var car in cars)
        {
            present.Add(car.CarIndex);
            writer.Write(car.CarIndex);
            var hasPrevious = prior.TryGetValue(car.CarIndex, out var previous);
            var mask = hasPrevious ? CarChangeMask(previous!, car) : AllCarFields;
            writer.Write(mask);
            if ((mask & CarLapPercent) != 0) WriteNullableSingle(writer, car.LapDistancePercent);
            if ((mask & CarLap) != 0) WriteNullableInt32(writer, car.Lap);
            if ((mask & CarCompletedLaps) != 0) WriteNullableInt32(writer, car.CompletedLaps);
            if ((mask & CarOverallPosition) != 0) WriteNullableInt32(writer, car.OverallPosition);
            if ((mask & CarClassPosition) != 0) WriteNullableInt32(writer, car.ClassPosition);
            if ((mask & CarOnPitRoad) != 0) WriteNullableBoolean(writer, car.OnPitRoad);
            if ((mask & CarTrackSurface) != 0) WriteNullableInt32(writer, car.TrackSurface);
            if ((mask & CarPaceFlags) != 0) WriteNullableInt32(writer, car.PaceFlags);
            if ((mask & CarLastLap) != 0) WriteNullableSingle(writer, car.LastLapSeconds);
            if ((mask & CarBestLap) != 0) WriteNullableSingle(writer, car.BestLapSeconds);
            prior[car.CarIndex] = car;
        }
        foreach (var missing in prior.Keys.Where(index => !present.Contains(index)).ToArray()) prior.Remove(missing);
    }

    private static IReadOnlyList<LiveReplayCarSample> ReadCars(BinaryReader reader, IDictionary<int, LiveReplayCarSample> prior)
    {
        var count = ReadBoundedCount(reader, 512, "cars");
        var result = new List<LiveReplayCarSample>(count);
        var present = new HashSet<int>();
        for (var index = 0; index < count; index++)
        {
            var carIndex = reader.ReadInt32();
            present.Add(carIndex);
            prior.TryGetValue(carIndex, out var previous);
            var mask = reader.ReadUInt32();
            if (previous is null && mask != AllCarFields) throw new InvalidDataException("A replay car delta is missing its base state.");
            var car = new LiveReplayCarSample(
                carIndex,
                (mask & CarLapPercent) != 0 ? ReadNullableSingle(reader) : previous!.LapDistancePercent,
                (mask & CarLap) != 0 ? ReadNullableInt32(reader) : previous!.Lap,
                (mask & CarCompletedLaps) != 0 ? ReadNullableInt32(reader) : previous!.CompletedLaps,
                (mask & CarOverallPosition) != 0 ? ReadNullableInt32(reader) : previous!.OverallPosition,
                (mask & CarClassPosition) != 0 ? ReadNullableInt32(reader) : previous!.ClassPosition,
                (mask & CarOnPitRoad) != 0 ? ReadNullableBoolean(reader) : previous!.OnPitRoad,
                (mask & CarTrackSurface) != 0 ? ReadNullableInt32(reader) : previous!.TrackSurface,
                (mask & CarPaceFlags) != 0 ? ReadNullableInt32(reader) : previous!.PaceFlags,
                (mask & CarLastLap) != 0 ? ReadNullableSingle(reader) : previous!.LastLapSeconds,
                (mask & CarBestLap) != 0 ? ReadNullableSingle(reader) : previous!.BestLapSeconds);
            result.Add(car);
            prior[carIndex] = car;
        }
        foreach (var missing in prior.Keys.Where(index => !present.Contains(index)).ToArray()) prior.Remove(missing);
        return result;
    }

    private static uint CarChangeMask(LiveReplayCarSample previous, LiveReplayCarSample current)
    {
        uint mask = 0;
        if (!Nullable.Equals(previous.LapDistancePercent, current.LapDistancePercent)) mask |= CarLapPercent;
        if (previous.Lap != current.Lap) mask |= CarLap;
        if (previous.CompletedLaps != current.CompletedLaps) mask |= CarCompletedLaps;
        if (previous.OverallPosition != current.OverallPosition) mask |= CarOverallPosition;
        if (previous.ClassPosition != current.ClassPosition) mask |= CarClassPosition;
        if (previous.OnPitRoad != current.OnPitRoad) mask |= CarOnPitRoad;
        if (previous.TrackSurface != current.TrackSurface) mask |= CarTrackSurface;
        if (previous.PaceFlags != current.PaceFlags) mask |= CarPaceFlags;
        if (!Nullable.Equals(previous.LastLapSeconds, current.LastLapSeconds)) mask |= CarLastLap;
        if (!Nullable.Equals(previous.BestLapSeconds, current.BestLapSeconds)) mask |= CarBestLap;
        return mask;
    }

    private static void WritePlayerTelemetry(BinaryWriter writer, LiveReplayPlayerTelemetry? player, LiveReplayPlayerTelemetry? previous)
    {
        if (player is null) { writer.Write((byte)0); return; }
        writer.Write((byte)1);
        var mask = previous is null ? AllPlayerFields : PlayerChangeMask(previous, player);
        writer.Write(mask);
        if ((mask & PlayerIncidentPoints) != 0) WriteNullableInt32(writer, player.IncidentPoints);
        if ((mask & PlayerDriverIncidentPoints) != 0) WriteNullableInt32(writer, player.DriverIncidentPoints);
        if ((mask & PlayerTeamIncidentPoints) != 0) WriteNullableInt32(writer, player.TeamIncidentPoints);
        if ((mask & PlayerTrackSurface) != 0) WriteNullableInt32(writer, player.TrackSurface);
        if ((mask & PlayerOnPitRoad) != 0) writer.Write(player.OnPitRoad);
        if ((mask & PlayerTowing) != 0) writer.Write(player.Towing);
        if ((mask & PlayerRepairRequired) != 0) writer.Write(player.RepairRequired);
        if ((mask & PlayerMandatoryRepair) != 0) WriteNullableSingle(writer, player.MandatoryRepairSeconds);
        if ((mask & PlayerOptionalRepair) != 0) WriteNullableSingle(writer, player.OptionalRepairSeconds);
        if ((mask & PlayerSpeed) != 0) WriteNullableSingle(writer, player.SpeedMetersPerSecond);
        if ((mask & PlayerThrottle) != 0) WriteNullableSingle(writer, player.Throttle);
        if ((mask & PlayerBrake) != 0) WriteNullableSingle(writer, player.Brake);
        if ((mask & PlayerSteering) != 0) WriteNullableSingle(writer, player.SteeringWheelAngleRadians);
        if ((mask & PlayerGear) != 0) WriteNullableInt32(writer, player.Gear);
        if ((mask & PlayerRpm) != 0) WriteNullableSingle(writer, player.Rpm);
        if ((mask & PlayerYawRate) != 0) WriteNullableSingle(writer, player.YawRateRadiansPerSecond);
        if ((mask & PlayerLateralG) != 0) WriteNullableSingle(writer, player.LateralAccelerationG);
        if ((mask & PlayerLongitudinalG) != 0) WriteNullableSingle(writer, player.LongitudinalAccelerationG);
    }

    private static LiveReplayPlayerTelemetry? ReadPlayerTelemetry(BinaryReader reader, LiveReplayPlayerTelemetry? previous)
    {
        var present = reader.ReadByte();
        if (present == 0) return null;
        if (present != 1) throw new InvalidDataException("Replay player state marker is invalid.");
        var mask = reader.ReadUInt32();
        if (previous is null && mask != AllPlayerFields) throw new InvalidDataException("A replay player delta is missing its base state.");
        return new LiveReplayPlayerTelemetry(
            (mask & PlayerIncidentPoints) != 0 ? ReadNullableInt32(reader) : previous!.IncidentPoints,
            (mask & PlayerDriverIncidentPoints) != 0 ? ReadNullableInt32(reader) : previous!.DriverIncidentPoints,
            (mask & PlayerTeamIncidentPoints) != 0 ? ReadNullableInt32(reader) : previous!.TeamIncidentPoints,
            (mask & PlayerTrackSurface) != 0 ? ReadNullableInt32(reader) : previous!.TrackSurface,
            (mask & PlayerOnPitRoad) != 0 ? reader.ReadBoolean() : previous!.OnPitRoad,
            (mask & PlayerTowing) != 0 ? reader.ReadBoolean() : previous!.Towing,
            (mask & PlayerRepairRequired) != 0 ? reader.ReadBoolean() : previous!.RepairRequired,
            (mask & PlayerMandatoryRepair) != 0 ? ReadNullableSingle(reader) : previous!.MandatoryRepairSeconds,
            (mask & PlayerOptionalRepair) != 0 ? ReadNullableSingle(reader) : previous!.OptionalRepairSeconds,
            (mask & PlayerSpeed) != 0 ? ReadNullableSingle(reader) : previous!.SpeedMetersPerSecond,
            (mask & PlayerThrottle) != 0 ? ReadNullableSingle(reader) : previous!.Throttle,
            (mask & PlayerBrake) != 0 ? ReadNullableSingle(reader) : previous!.Brake,
            (mask & PlayerSteering) != 0 ? ReadNullableSingle(reader) : previous!.SteeringWheelAngleRadians,
            (mask & PlayerGear) != 0 ? ReadNullableInt32(reader) : previous!.Gear,
            (mask & PlayerRpm) != 0 ? ReadNullableSingle(reader) : previous!.Rpm,
            (mask & PlayerYawRate) != 0 ? ReadNullableSingle(reader) : previous!.YawRateRadiansPerSecond,
            (mask & PlayerLateralG) != 0 ? ReadNullableSingle(reader) : previous!.LateralAccelerationG,
            (mask & PlayerLongitudinalG) != 0 ? ReadNullableSingle(reader) : previous!.LongitudinalAccelerationG);
    }

    private static uint PlayerChangeMask(LiveReplayPlayerTelemetry previous, LiveReplayPlayerTelemetry current)
    {
        uint mask = 0;
        if (previous.IncidentPoints != current.IncidentPoints) mask |= PlayerIncidentPoints;
        if (previous.DriverIncidentPoints != current.DriverIncidentPoints) mask |= PlayerDriverIncidentPoints;
        if (previous.TeamIncidentPoints != current.TeamIncidentPoints) mask |= PlayerTeamIncidentPoints;
        if (previous.TrackSurface != current.TrackSurface) mask |= PlayerTrackSurface;
        if (previous.OnPitRoad != current.OnPitRoad) mask |= PlayerOnPitRoad;
        if (previous.Towing != current.Towing) mask |= PlayerTowing;
        if (previous.RepairRequired != current.RepairRequired) mask |= PlayerRepairRequired;
        if (!Nullable.Equals(previous.MandatoryRepairSeconds, current.MandatoryRepairSeconds)) mask |= PlayerMandatoryRepair;
        if (!Nullable.Equals(previous.OptionalRepairSeconds, current.OptionalRepairSeconds)) mask |= PlayerOptionalRepair;
        if (!Nullable.Equals(previous.SpeedMetersPerSecond, current.SpeedMetersPerSecond)) mask |= PlayerSpeed;
        if (!Nullable.Equals(previous.Throttle, current.Throttle)) mask |= PlayerThrottle;
        if (!Nullable.Equals(previous.Brake, current.Brake)) mask |= PlayerBrake;
        if (!Nullable.Equals(previous.SteeringWheelAngleRadians, current.SteeringWheelAngleRadians)) mask |= PlayerSteering;
        if (previous.Gear != current.Gear) mask |= PlayerGear;
        if (!Nullable.Equals(previous.Rpm, current.Rpm)) mask |= PlayerRpm;
        if (!Nullable.Equals(previous.YawRateRadiansPerSecond, current.YawRateRadiansPerSecond)) mask |= PlayerYawRate;
        if (!Nullable.Equals(previous.LateralAccelerationG, current.LateralAccelerationG)) mask |= PlayerLateralG;
        if (!Nullable.Equals(previous.LongitudinalAccelerationG, current.LongitudinalAccelerationG)) mask |= PlayerLongitudinalG;
        return mask;
    }

    private static void WriteEvents(BinaryWriter writer, IReadOnlyList<LiveReplayObservedEvent>? events)
    {
        writer.Write(events?.Count ?? 0);
        foreach (var item in events ?? [])
        {
            writer.Write(item.Kind);
            writer.Write(item.Label);
            writer.Write(item.SourceChannel);
            WriteNullableDouble(writer, item.Delta);
        }
    }

    private static IReadOnlyList<LiveReplayObservedEvent> ReadEvents(BinaryReader reader)
    {
        var count = ReadBoundedCount(reader, 1_024, "events");
        var result = new List<LiveReplayObservedEvent>(count);
        for (var index = 0; index < count; index++)
            result.Add(new(reader.ReadString(), reader.ReadString(), reader.ReadString(), ReadNullableDouble(reader)));
        return result;
    }

    private static int ReadBoundedCount(BinaryReader reader, int maximum, string name)
    {
        var count = reader.ReadInt32();
        if (count < 0 || count > maximum) throw new InvalidDataException($"Replay {name} count is invalid.");
        return count;
    }

    private static void WriteNullableString(BinaryWriter writer, string? value) { writer.Write(value is not null); if (value is not null) writer.Write(value); }
    private static string? ReadNullableString(BinaryReader reader) => reader.ReadBoolean() ? reader.ReadString() : null;
    private static void WriteNullableInt32(BinaryWriter writer, int? value) { writer.Write(value.HasValue); if (value.HasValue) writer.Write(value.Value); }
    private static int? ReadNullableInt32(BinaryReader reader) => reader.ReadBoolean() ? reader.ReadInt32() : null;
    private static void WriteNullableInt64(BinaryWriter writer, long? value) { writer.Write(value.HasValue); if (value.HasValue) writer.Write(value.Value); }
    private static long? ReadNullableInt64(BinaryReader reader) => reader.ReadBoolean() ? reader.ReadInt64() : null;
    private static void WriteNullableBoolean(BinaryWriter writer, bool? value) { writer.Write(value.HasValue); if (value.HasValue) writer.Write(value.Value); }
    private static bool? ReadNullableBoolean(BinaryReader reader) => reader.ReadBoolean() ? reader.ReadBoolean() : null;
    private static void WriteNullableDouble(BinaryWriter writer, double? value) { writer.Write(value.HasValue); if (value.HasValue) writer.Write(value.Value); }
    private static double? ReadNullableDouble(BinaryReader reader) => reader.ReadBoolean() ? reader.ReadDouble() : null;
    private static void WriteNullableSingle(BinaryWriter writer, double? value) { writer.Write(value.HasValue); if (value.HasValue) writer.Write((float)value.Value); }
    private static double? ReadNullableSingle(BinaryReader reader) => reader.ReadBoolean() ? reader.ReadSingle() : null;
    private static double? FiniteOrNull(double value) => double.IsFinite(value) ? value : null;

    private sealed record CommonMetadata(
        string SessionKey,
        long? SessionUniqueId,
        long? SubsessionId,
        int? SessionNumber,
        string? SessionType,
        int? PlayerCarIndex,
        IReadOnlyList<LiveReplayChannelCoverage> Coverage,
        IReadOnlyList<LiveReplayParticipant> Participants);
}

public sealed record LiveReplayEncodedChunk(
    byte[] Bytes,
    int FrameCount,
    DateTimeOffset StartCapturedAt,
    DateTimeOffset EndCapturedAt,
    double? StartSessionTimeSeconds,
    double? EndSessionTimeSeconds,
    int UncompressedBytes,
    int CompressedBytes);

public sealed record LiveReplayDecodedChunk(
    IReadOnlyList<LiveReplayCaptureFrame> Frames,
    DateTimeOffset StartCapturedAt,
    DateTimeOffset EndCapturedAt,
    double? StartSessionTimeSeconds,
    double? EndSessionTimeSeconds,
    int UncompressedBytes,
    int CompressedBytes);

public sealed record LiveReplayChunkHeader(
    int FrameCount,
    DateTimeOffset StartCapturedAt,
    DateTimeOffset EndCapturedAt,
    double? StartSessionTimeSeconds,
    double? EndSessionTimeSeconds,
    int UncompressedBytes,
    int CompressedBytes);
