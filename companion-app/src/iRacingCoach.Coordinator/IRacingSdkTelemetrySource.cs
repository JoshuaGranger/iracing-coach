using System.IO.MemoryMappedFiles;
using System.Runtime.Versioning;
using System.Text;
using System.Text.RegularExpressions;

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
    private int _sessionInfoUpdate = -1;
    private long? _subsessionId;
    private IReadOnlyDictionary<int, string> _sessionTypes = new Dictionary<int, string>();
    private IReadOnlyList<LiveReplayParticipant> _replayParticipants = [];
    private IReadOnlyList<LiveReplayChannelCoverage> _replayCoverage = [];
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
            ReadSessionInfoIfChanged(_view.ReadInt32(12), _view.ReadInt32(16), _view.ReadInt32(20));

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
            var carLapsCompleted = ReadIntArray("CarIdxLapCompleted", bestOffset);
            var carLapDistance = ReadFloatArray("CarIdxLapDistPct", bestOffset);
            var carOnPitRoad = ReadBoolArray("CarIdxOnPitRoad", bestOffset);
            var carTrackSurface = ReadIntArray("CarIdxTrackSurface", bestOffset);
            var carPaceFlags = ReadIntArray("CarIdxPaceFlags", bestOffset);
            var f2Times = ReadFloatArray("CarIdxF2Time", bestOffset);
            var lastLapTimes = ReadFloatArray("CarIdxLastLapTime", bestOffset);
            var bestLapTimes = ReadFloatArray("CarIdxBestLapTime", bestOffset);
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
            var flags = ReadUInt("SessionFlags", bestOffset);
            var caution = LiveTruthPolicy.IsUnderCaution(flags);
            var black = LiveTruthPolicy.DecodeRacingState(flags) is "black" or "disqualified";
            var repair = LiveTruthPolicy.DecodeRepairState(flags) == "required";
            var lapRaw = ReadInt("Lap", bestOffset);
            var lapsRemaining = ReadDouble("SessionLapsRemainEx", bestOffset) ?? ReadDouble("SessionLapsRemain", bestOffset);
            var replayCars = ReplayCars(
                carLapDistance,
                carLaps,
                carLapsCompleted,
                positions,
                classPositions,
                carOnPitRoad,
                carTrackSurface,
                carPaceFlags,
                lastLapTimes,
                bestLapTimes);

            var sessionNumber = ReadInt("SessionNum", bestOffset);
            sample = new LiveTelemetrySample
            {
                Connected = true,
                Timestamp = DateTimeOffset.UtcNow,
                Tick = bestTick,
                TickRate = tickRate,
                Flag = LiveTruthPolicy.DisplayFlag(flags),
                SessionFlagsKnown = flags.HasValue,
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
                BrakeAbsActive = ReadBool("BrakeABSactive", bestOffset) ??
                    (ReadDouble("BrakeABSactive", bestOffset) is { } absActive ? absActive > .5 : null),
                BrakeAbsCutPercent = Percentage(ReadDouble("BrakeABScutPct", bestOffset)),
                Gear = ReadInt("Gear", bestOffset),
                Rpm = ReadDouble("RPM", bestOffset),
                YawRateRadiansPerSecond = ReadDouble("YawRate", bestOffset),
                LateralAccelerationG = ReadDouble("LatAccel", bestOffset) / 9.80665,
                LongitudinalAccelerationG = ReadDouble("LongAccel", bestOffset) / 9.80665,
                SpeedMetersPerSecond = ReadDouble("Speed", bestOffset),
                LapDistancePercent = LiveTruthPolicy.NormalizeLapDistance(ReadDouble("LapDistPct", bestOffset)),
                Latitude = ReadDouble("Lat", bestOffset),
                Longitude = ReadDouble("Lon", bestOffset),
                SessionTimeSeconds = ReadDouble("SessionTime", bestOffset),
                SessionState = ReadInt("SessionState", bestOffset),
                SessionFlags = flags,
                SessionUniqueId = ReadLong("SessionUniqueID", bestOffset),
                SubsessionId = _subsessionId,
                SessionNumber = sessionNumber,
                SessionType = sessionNumber.HasValue && _sessionTypes.TryGetValue(sessionNumber.Value, out var sessionType)
                    ? sessionType
                    : null,
                PlayerCarIndex = playerIndex,
                PlayerIncidentPoints = ReadInt("PlayerCarMyIncidentCount", bestOffset)
                    ?? ReadInt("PlayerIncidents", bestOffset),
                DriverIncidentPoints = ReadInt("PlayerCarDriverIncidentCount", bestOffset),
                TeamIncidentPoints = ReadInt("PlayerCarTeamIncidentCount", bestOffset),
                PlayerTrackSurface = ReadInt("PlayerTrackSurface", bestOffset),
                ReplayCoverage = _replayCoverage,
                ReplayParticipants = _replayParticipants,
                ReplayCars = replayCars
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
        RefreshReplayCoverage();
    }

    private void ReadSessionInfoIfChanged(int update, int length, int offset)
    {
        if (update == _sessionInfoUpdate) return;
        if (length <= 0 || length > 16 * 1024 * 1024 || offset < HeaderSize || (long)offset + length > _view!.Capacity)
            throw new InvalidDataException("The live SessionInfo block exceeds shared-memory bounds.");
        var bytes = new byte[length];
        _view.ReadArray(offset, bytes, 0, bytes.Length);
        var nul = Array.IndexOf(bytes, (byte)0);
        var text = Encoding.UTF8.GetString(bytes, 0, nul >= 0 ? nul : bytes.Length);
        _subsessionId = Regex.Match(text, @"(?m)^\s*SubSessionID:\s*(\d+)\s*$") is { Success: true } subsession &&
            long.TryParse(subsession.Groups[1].Value, out var parsedSubsession)
                ? parsedSubsession
                : null;
        _sessionTypes = ParseSessionTypes(text);
        _replayParticipants = ParseParticipants(text);
        _sessionInfoUpdate = update;
        RefreshReplayCoverage();
    }

    private static IReadOnlyDictionary<int, string> ParseSessionTypes(string sessionInfo)
    {
        var result = new Dictionary<int, string>();
        foreach (Match match in Regex.Matches(
                     sessionInfo,
                     @"(?ms)^\s*-\s*SessionNum:\s*(?<number>-?\d+)\s*(?<body>.*?)(?=^\s*-\s*SessionNum:|^\s*DriverInfo:|\z)"))
        {
            if (!int.TryParse(match.Groups["number"].Value, out var number)) continue;
            var type = Regex.Match(match.Groups["body"].Value, @"(?m)^\s*SessionType:\s*(.*?)\s*$");
            if (!type.Success) continue;
            var value = type.Groups[1].Value.Trim().Trim('"', '\'');
            if (value.Length > 0 && value != "~") result[number] = value;
        }
        return result;
    }

    private static IReadOnlyList<LiveReplayParticipant> ParseParticipants(string sessionInfo)
    {
        var result = new List<LiveReplayParticipant>();
        foreach (Match match in Regex.Matches(
                     sessionInfo,
                     @"(?ms)^\s*-\s*CarIdx:\s*(?<index>\d+)\s*(?<body>.*?)(?=^\s*-\s*CarIdx:|\z)"))
        {
            if (!int.TryParse(match.Groups["index"].Value, out var carIndex)) continue;
            var body = match.Groups["body"].Value;
            string? field(string name)
            {
                var value = Regex.Match(body, $@"(?m)^\s*{Regex.Escape(name)}:\s*(.*?)\s*$");
                if (!value.Success) return null;
                var normalized = value.Groups[1].Value.Trim();
                if (normalized.Length >= 2 && ((normalized[0] == '"' && normalized[^1] == '"') || (normalized[0] == '\'' && normalized[^1] == '\'')))
                    normalized = normalized[1..^1];
                return normalized.Length == 0 || normalized == "~" ? null : normalized;
            }
            int? integer(string name) => int.TryParse(field(name), out var value) ? value : null;
            bool? boolean(string name) => field(name)?.Trim().ToLowerInvariant() switch
            {
                "1" or "true" => true,
                "0" or "false" => false,
                _ => null
            };
            result.Add(new LiveReplayParticipant(
                carIndex,
                field("CarNumber") ?? field("CarNumberRaw"),
                integer("CarClassID"),
                field("CarClassShortName"),
                field("CarScreenName") ?? field("CarPath"),
                field("UserName") ?? field("AbbrevName"),
                field("TeamName"),
                boolean("IsSpectator")));
        }
        return result.GroupBy(item => item.CarIndex).Select(group => group.First()).OrderBy(item => item.CarIndex).ToArray();
    }

    private void RefreshReplayCoverage()
    {
        var definitions = new (string Name, string Reason)[]
        {
            ("SessionTime", "SessionTime was not present in live shared memory."),
            ("SessionState", "SessionState was not present in live shared memory."),
            ("SessionFlags", "Global session flags were not present in live shared memory."),
            ("CarIdxLapDistPct", "Full-field lap distance was not present; rivals cannot be placed."),
            ("CarIdxLap", "Per-car lap number was not present."),
            ("CarIdxLapCompleted", "Per-car completed laps were not present."),
            ("CarIdxPosition", "Per-car overall position was not present."),
            ("CarIdxClassPosition", "Per-car class position was not present."),
            ("CarIdxOnPitRoad", "Per-car pit-road state was not present."),
            ("CarIdxTrackSurface", "Per-car track-surface state was not present."),
            ("CarIdxPaceFlags", "Per-car pace flags were not present."),
            ("CarIdxLastLapTime", "Per-car last-lap timing was not present."),
            ("CarIdxBestLapTime", "Per-car best-lap timing was not present."),
        };
        var coverage = definitions.Select(item => new LiveReplayChannelCoverage(
            item.Name,
            _variables.ContainsKey(item.Name),
            _variables.ContainsKey(item.Name) ? null : item.Reason)).ToList();
        coverage.Add(new LiveReplayChannelCoverage(
            "DriverInfo.Drivers",
            _replayParticipants.Count > 0,
            _replayParticipants.Count > 0 ? null : "DriverInfo participants were not present, so car number/class/name are unavailable."));
        _replayCoverage = coverage;
    }

    private static IReadOnlyList<LiveReplayCarSample> ReplayCars(
        float[] lapDistance,
        int[] laps,
        int[] completedLaps,
        int[] positions,
        int[] classPositions,
        bool[] onPitRoad,
        int[] trackSurface,
        int[] paceFlags,
        float[] lastLapTimes,
        float[] bestLapTimes)
    {
        var count = new[] { lapDistance.Length, laps.Length, completedLaps.Length, positions.Length, classPositions.Length, onPitRoad.Length, trackSurface.Length, paceFlags.Length, lastLapTimes.Length, bestLapTimes.Length }.Max();
        var result = new List<LiveReplayCarSample>(count);
        for (var index = 0; index < count; index++)
        {
            var rawDistance = ArrayValue(lapDistance, index);
            double? distance = rawDistance is { } value && float.IsFinite(value) && value >= 0 ? value % 1d : null;
            var position = Positive(ArrayValue(positions, index));
            var classPosition = Positive(ArrayValue(classPositions, index));
            var surface = ArrayValue(trackSurface, index);
            if (!distance.HasValue && !position.HasValue && !classPosition.HasValue && surface is null or -1) continue;
            result.Add(new LiveReplayCarSample(
                index,
                distance,
                ArrayValue(laps, index),
                ArrayValue(completedLaps, index),
                position,
                classPosition,
                ArrayValue(onPitRoad, index),
                surface,
                ArrayValue(paceFlags, index),
                Positive(ArrayValue(lastLapTimes, index)),
                Positive(ArrayValue(bestLapTimes, index))));
        }
        return result;
    }

    private int? ReadInt(string name, int row) => ReadNumber(name, row) is { } value ? (int)value : null;
    private long? ReadLong(string name, int row) => ReadNumber(name, row) is { } value && double.IsFinite(value) ? (long)Math.Round(value) : null;
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
    private bool[] ReadBoolArray(string name, int row)
    {
        if (!_variables.TryGetValue(name, out var variable) || variable.Type != 1) return [];
        var values = new bool[variable.Count];
        for (var index = 0; index < values.Length; index++) values[index] = _view!.ReadByte(row + variable.Offset + index) != 0;
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
    private void Close()
    {
        _view?.Dispose();
        _map?.Dispose();
        _view = null;
        _map = null;
        _variables.Clear();
        _variableCount = -1;
        _variableOffset = -1;
        _sessionInfoUpdate = -1;
        _subsessionId = null;
        _sessionTypes = new Dictionary<int, string>();
        _replayParticipants = [];
        _replayCoverage = [];
        _lastTick = -1;
    }

    public void Dispose() => Close();
    private readonly record struct Variable(int Type, int Offset, int Count);
    private enum GapKind { ToTarget, FromTarget }
}
