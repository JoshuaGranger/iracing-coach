using System.IO.MemoryMappedFiles;
using System.Runtime.Versioning;
using System.Text;

namespace iRacingCoach.Coordinator;

public sealed class IRacingSdkTelemetrySource : ILiveTelemetrySource
{
    private const string MemoryMapName = @"Local\IRSDKMemMapFileName";
    private const int HeaderSize = 112;
    private const int VariableHeaderSize = 144;
    private const int ConnectedStatus = 1;
    private MemoryMappedFile? _map;
    private MemoryMappedViewAccessor? _view;
    private readonly Dictionary<string, Variable> _variables = new(StringComparer.Ordinal);
    private int _lastTick = -1;
    private int _variableCount = -1;
    private int _variableOffset = -1;
    private DateTimeOffset _lastOpenAttempt = DateTimeOffset.MinValue;

    public bool TryRead(out LiveTelemetrySample sample)
    {
        sample = new LiveTelemetrySample { Connected = false, Timestamp = DateTimeOffset.UtcNow };
        if (!OperatingSystem.IsWindows() || !EnsureOpen()) return ShouldPublishDisconnected();

        try
        {
            var status = _view!.ReadInt32(4);
            if ((status & ConnectedStatus) == 0) return ShouldPublishDisconnected();
            var tickRate = _view.ReadInt32(8);
            var variableCount = _view.ReadInt32(24);
            var variableOffset = _view.ReadInt32(28);
            var bufferCount = Math.Clamp(_view.ReadInt32(32), 0, 4);
            var bufferLength = _view.ReadInt32(36);
            if (variableCount <= 0 || variableCount > 16_384 || variableOffset < HeaderSize || bufferLength <= 0)
                throw new InvalidDataException("The live iRacing SDK header is outside supported bounds.");
            if (variableCount != _variableCount || variableOffset != _variableOffset)
                ReadVariables(variableCount, variableOffset, bufferLength);

            var bestTick = int.MinValue;
            var bestOffset = -1;
            for (var index = 0; index < bufferCount; index++)
            {
                var header = 48 + index * 16;
                var tick = _view.ReadInt32(header);
                var offset = _view.ReadInt32(header + 4);
                if (tick > bestTick && offset >= HeaderSize && offset + bufferLength <= _view.Capacity)
                {
                    bestTick = tick;
                    bestOffset = offset;
                }
            }
            if (bestOffset < 0 || bestTick == _lastTick) return false;
            _lastTick = bestTick;

            var playerIndex = ReadInt("PlayerCarIdx", bestOffset);
            var playerIndexValue = playerIndex ?? -1;
            var positions = ReadIntArray("CarIdxPosition", bestOffset);
            var classPositions = ReadIntArray("CarIdxClassPosition", bestOffset);
            var carLaps = ReadIntArray("CarIdxLap", bestOffset);
            var f2Times = ReadFloatArray("CarIdxF2Time", bestOffset);
            var lastLapTimes = ReadFloatArray("CarIdxLastLapTime", bestOffset);
            var overallPosition = ReadInt("PlayerCarPosition", bestOffset) ?? ArrayValue(positions, playerIndex);
            var classPosition = ReadInt("PlayerCarClassPosition", bestOffset) ?? ArrayValue(classPositions, playerIndex);
            var leaderIndex = FindByPosition(positions, 1);
            var classLeaderIndex = FindByPosition(classPositions, 1);
            var aheadIndex = classPosition is > 1 ? FindByPosition(classPositions, classPosition.Value - 1) : -1;
            var behindIndex = classPosition.HasValue ? FindByPosition(classPositions, classPosition.Value + 1) : -1;
            double? leaderGap = overallPosition == 1 ? 0 : SameLapGap(f2Times, carLaps, playerIndexValue, leaderIndex, GapKind.ToTarget);
            double? classLeaderGap = classPosition == 1 ? 0 : SameLapGap(f2Times, carLaps, playerIndexValue, classLeaderIndex, GapKind.ToTarget);
            var aheadGap = SameLapGap(f2Times, carLaps, playerIndexValue, aheadIndex, GapKind.ToTarget);
            var behindGap = SameLapGap(f2Times, carLaps, playerIndexValue, behindIndex, GapKind.FromTarget);
            var flags = ReadUInt("SessionFlags", bestOffset) ?? 0;
            var caution = (flags & (0x00000008u | 0x00004000u | 0x00008000u)) != 0;
            var black = (flags & (0x00010000u | 0x00020000u)) != 0;
            var repair = (flags & 0x00100000u) != 0;
            var lapRaw = ReadInt("Lap", bestOffset);
            var lapsRemaining = ReadDouble("SessionLapsRemainEx", bestOffset) ?? ReadDouble("SessionLapsRemain", bestOffset);

            sample = new LiveTelemetrySample
            {
                Connected = true,
                Timestamp = DateTimeOffset.UtcNow,
                Tick = bestTick,
                TickRate = tickRate,
                Flag = FlagLabel(flags),
                UnderCaution = caution,
                BlackFlag = black,
                RepairFlag = repair,
                Towing = (ReadDouble("PlayerCarTowTime", bestOffset) ?? 0) > 0,
                Lap = lapRaw.HasValue ? Math.Max(1, lapRaw.Value + 1) : null,
                LapsRemaining = lapsRemaining.HasValue && lapsRemaining.Value >= 0 ? Math.Max(0, (int)Math.Ceiling(lapsRemaining.Value)) : null,
                OverallPosition = Positive(overallPosition),
                ClassPosition = Positive(classPosition),
                GapToLeaderSeconds = NonNegative(leaderGap),
                GapToClassLeaderSeconds = NonNegative(classLeaderGap),
                GapToAheadSeconds = NonNegative(aheadGap),
                GapToBehindSeconds = NonNegative(behindGap),
                LeaderGapUnavailableReason = leaderIndex < 0 ? "Overall leader is not present in the scoring array." : "Leader is on a different lap or the scoring interval is invalid.",
                AheadGapUnavailableReason = aheadIndex < 0 ? "No class car is scored immediately ahead." : "The car ahead is on a different lap or its interval is invalid.",
                BehindGapUnavailableReason = behindIndex < 0 ? "No class car is scored immediately behind." : "The car behind is on a different lap or its interval is invalid.",
                LastLapSeconds = Positive(ReadDouble("LapLastLapTime", bestOffset)),
                LeaderLastLapSeconds = Positive(ArrayValue(lastLapTimes, leaderIndex)),
                FuelLiters = NonNegative(ReadDouble("FuelLevel", bestOffset)),
                FuelLevelPercent = Percentage(ReadDouble("FuelLevelPct", bestOffset)),
                TrackTemperatureC = ReadDouble("TrackTempCrew", bestOffset) ?? ReadDouble("TrackTemp", bestOffset),
                AirTemperatureC = ReadDouble("AirTemp", bestOffset),
                BrakeBiasPercent = ReadDouble("dcBrakeBias", bestOffset),
                OnPitRoad = ReadBool("OnPitRoad", bestOffset) ?? false,
                MandatoryRepairSeconds = NonNegative(ReadDouble("PitRepairLeft", bestOffset)),
                OptionalRepairSeconds = NonNegative(ReadDouble("PitOptRepairLeft", bestOffset)),
                SteeringWheelAngleRadians = ReadDouble("SteeringWheelAngle", bestOffset),
                Throttle = ReadDouble("Throttle", bestOffset),
                Brake = ReadDouble("Brake", bestOffset),
                Gear = ReadInt("Gear", bestOffset),
                Rpm = ReadDouble("RPM", bestOffset),
                YawRateRadiansPerSecond = ReadDouble("YawRate", bestOffset),
                LateralAccelerationG = ReadDouble("LatAccel", bestOffset) / 9.80665,
                LongitudinalAccelerationG = ReadDouble("LongAccel", bestOffset) / 9.80665,
                SpeedMetersPerSecond = ReadDouble("Speed", bestOffset),
                LapDistancePercent = ReadDouble("LapDistPct", bestOffset),
                Latitude = ReadDouble("Lat", bestOffset),
                Longitude = ReadDouble("Lon", bestOffset)
            };
            return true;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or ArgumentOutOfRangeException or InvalidDataException)
        {
            Close();
            return ShouldPublishDisconnected();
        }
    }

    [SupportedOSPlatform("windows")]
    private bool EnsureOpen()
    {
        if (_view is not null) return true;
        if (DateTimeOffset.UtcNow - _lastOpenAttempt < TimeSpan.FromSeconds(1)) return false;
        _lastOpenAttempt = DateTimeOffset.UtcNow;
        try
        {
            _map = MemoryMappedFile.OpenExisting(MemoryMapName, MemoryMappedFileRights.Read);
            _view = _map.CreateViewAccessor(0, 0, MemoryMappedFileAccess.Read);
            return _view.Capacity >= HeaderSize;
        }
        catch (FileNotFoundException) { Close(); return false; }
        catch (IOException) { Close(); return false; }
        catch (UnauthorizedAccessException) { Close(); return false; }
    }

    private bool ShouldPublishDisconnected()
    {
        if (DateTimeOffset.UtcNow - _lastOpenAttempt < TimeSpan.FromSeconds(1)) return false;
        _lastOpenAttempt = DateTimeOffset.UtcNow;
        return true;
    }

    private void ReadVariables(int count, int offset, int bufferLength)
    {
        if ((long)offset + (long)count * VariableHeaderSize > _view!.Capacity)
            throw new InvalidDataException("The live variable table exceeds the shared-memory bounds.");
        _variables.Clear();
        var textBuffer = new byte[64];
        for (var index = 0; index < count; index++)
        {
            var start = offset + index * VariableHeaderSize;
            var type = _view.ReadInt32(start);
            var variableOffset = _view.ReadInt32(start + 4);
            var variableCount = _view.ReadInt32(start + 8);
            if (type is < 0 or > 5 || variableOffset < 0 || variableCount <= 0) continue;
            Array.Clear(textBuffer);
            _view.ReadArray(start + 16, textBuffer, 0, 32);
            var name = Encoding.ASCII.GetString(textBuffer, 0, 32).TrimEnd('\0');
            var width = TypeWidth(type);
            if (name.Length > 0 && (long)variableOffset + (long)variableCount * width <= bufferLength)
                _variables[name] = new Variable(type, variableOffset, variableCount);
        }
        _variableCount = count;
        _variableOffset = offset;
    }

    private int? ReadInt(string name, int row) => ReadNumber(name, row) is { } value ? (int)value : null;
    private uint? ReadUInt(string name, int row)
    {
        if (!_variables.TryGetValue(name, out var variable) || variable.Type is not (2 or 3)) return null;
        return _view!.ReadUInt32(row + variable.Offset);
    }
    private double? ReadDouble(string name, int row) => ReadNumber(name, row);
    private bool? ReadBool(string name, int row)
    {
        if (!_variables.TryGetValue(name, out var variable) || variable.Type != 1) return null;
        return _view!.ReadByte(row + variable.Offset) != 0;
    }
    private double? ReadNumber(string name, int row)
    {
        if (!_variables.TryGetValue(name, out var variable)) return null;
        var offset = row + variable.Offset;
        return variable.Type switch
        {
            0 => _view!.ReadByte(offset),
            1 => _view!.ReadByte(offset) != 0 ? 1 : 0,
            2 => _view!.ReadInt32(offset),
            3 => _view!.ReadUInt32(offset),
            4 => _view!.ReadSingle(offset),
            5 => _view!.ReadDouble(offset),
            _ => null
        };
    }
    private int[] ReadIntArray(string name, int row)
    {
        if (!_variables.TryGetValue(name, out var variable) || variable.Type is not (2 or 3)) return [];
        var values = new int[variable.Count];
        for (var index = 0; index < values.Length; index++) values[index] = _view!.ReadInt32(row + variable.Offset + index * 4);
        return values;
    }
    private float[] ReadFloatArray(string name, int row)
    {
        if (!_variables.TryGetValue(name, out var variable) || variable.Type != 4) return [];
        var values = new float[variable.Count];
        for (var index = 0; index < values.Length; index++) values[index] = _view!.ReadSingle(row + variable.Offset + index * 4);
        return values;
    }

    private static int FindByPosition(int[] positions, int value)
    {
        for (var index = 0; index < positions.Length; index++) if (positions[index] == value) return index;
        return -1;
    }
    private static double? SameLapGap(float[] f2, int[] laps, int player, int target, GapKind kind)
    {
        if (player < 0 || target < 0 || player >= f2.Length || target >= f2.Length || player >= laps.Length || target >= laps.Length || laps[player] != laps[target]) return null;
        var value = kind == GapKind.ToTarget ? f2[player] - f2[target] : f2[target] - f2[player];
        return float.IsFinite((float)value) && value >= 0 ? value : null;
    }
    private static T? ArrayValue<T>(T[] values, int? index) where T : struct => index is >= 0 && index.Value < values.Length ? values[index.Value] : null;
    private static int? Positive(int? value) => value is > 0 ? value : null;
    private static double? Positive(double? value) => value is > 0 && double.IsFinite(value.Value) ? value : null;
    private static double? NonNegative(double? value) => value is >= 0 && double.IsFinite(value.Value) ? value : null;
    private static double? Percentage(double? value) => value is >= 0 and <= 1 && double.IsFinite(value.Value) ? value : null;
    private static int TypeWidth(int type) => type switch { 0 or 1 => 1, 2 or 3 or 4 => 4, 5 => 8, _ => 0 };
    private static string FlagLabel(uint flags)
    {
        if ((flags & 0x00020000u) != 0) return "DISQUALIFIED";
        if ((flags & 0x00010000u) != 0) return "BLACK FLAG";
        if ((flags & 0x00000010u) != 0) return "RED";
        if ((flags & (0x00004000u | 0x00008000u | 0x00000008u)) != 0) return "CAUTION";
        if ((flags & 0x00000001u) != 0) return "CHECKERED";
        if ((flags & 0x00000002u) != 0) return "WHITE";
        if ((flags & (0x00000004u | 0x80000000u)) != 0) return "GREEN";
        return "RACING";
    }

    private void Close()
    {
        _view?.Dispose();
        _map?.Dispose();
        _view = null;
        _map = null;
        _variables.Clear();
        _variableCount = -1;
        _variableOffset = -1;
        _lastTick = -1;
    }

    public void Dispose() => Close();
    private readonly record struct Variable(int Type, int Offset, int Count);
    private enum GapKind { ToTarget, FromTarget }
}
