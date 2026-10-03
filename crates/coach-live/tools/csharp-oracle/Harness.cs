using System.IO.MemoryMappedFiles;
using System.Reflection;
using System.Text;
using System.Text.Json.Nodes;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace Oracle;

/// <summary>One SDK variable the C# reader (and the Rust Sample) reads.</summary>
public sealed record VarDef(string Name, int Type, int Count, string Rust);

public static class Vars
{
    public const int Cars = 8;
    // type: 0 char, 1 bool, 2 int, 3 bitfield, 4 float, 5 double
    public static readonly VarDef[] All =
    [
        new("PlayerCarIdx", 2, 1, "player_car_idx"),
        new("PlayerCarPosition", 2, 1, "player_car_position"),
        new("PlayerCarClassPosition", 2, 1, "player_car_class_position"),
        new("CarIdxPosition", 2, Cars, "car_idx_position"),
        new("CarIdxClassPosition", 2, Cars, "car_idx_class_position"),
        new("CarIdxLap", 2, Cars, "car_idx_lap"),
        new("CarIdxF2Time", 4, Cars, "car_idx_f2_time"),
        new("CarIdxLastLapTime", 4, Cars, "car_idx_last_lap_time"),
        new("SessionFlags", 3, 1, "session_flags"),
        new("PlayerCarTowTime", 4, 1, "player_car_tow_time"),
        new("Lap", 2, 1, "lap"),
        new("SessionLapsRemainEx", 5, 1, "session_laps_remain_ex"),
        new("SessionLapsRemain", 2, 1, "session_laps_remain"),
        new("LapLastLapTime", 4, 1, "lap_last_lap_time"),
        new("FuelLevel", 4, 1, "fuel_level"),
        new("FuelLevelPct", 4, 1, "fuel_level_pct"),
        new("TrackTempCrew", 4, 1, "track_temp_crew"),
        new("TrackTemp", 4, 1, "track_temp"),
        new("AirTemp", 4, 1, "air_temp"),
        new("dcBrakeBias", 4, 1, "dc_brake_bias"),
        new("OnPitRoad", 1, 1, "on_pit_road"),
        new("PitRepairLeft", 4, 1, "pit_repair_left"),
        new("PitOptRepairLeft", 4, 1, "pit_opt_repair_left"),
        new("SteeringWheelAngle", 4, 1, "steering_wheel_angle"),
        new("Throttle", 4, 1, "throttle"),
        new("Brake", 4, 1, "brake"),
        new("BrakeABSactive", 1, 1, "brake_abs_active"),
        new("Gear", 2, 1, "gear"),
        new("RPM", 4, 1, "rpm"),
        new("YawRate", 4, 1, "yaw_rate"),
        new("LatAccel", 4, 1, "lat_accel"),
        new("LongAccel", 4, 1, "long_accel"),
        new("Speed", 4, 1, "speed"),
        new("LapDistPct", 4, 1, "lap_dist_pct"),
        new("Lat", 5, 1, "lat"),
        new("Lon", 5, 1, "lon"),
        new("SessionTime", 5, 1, "session_time"),
        new("SessionNum", 2, 1, "session_num"),
        new("SessionUniqueID", 2, 1, "session_unique_id"),
        new("PlayerCarMyIncidentCount", 2, 1, "player_car_my_incident_count"),
        new("PlayerIncidents", 2, 1, "player_incidents"),
        new("IsOnTrack", 1, 1, "is_on_track"),
        new("IsInGarage", 1, 1, "is_in_garage"),
    ];

    public static int Width(int type) => type switch { 0 or 1 => 1, 5 => 8, _ => 4 };
}

/// <summary>A synthetic iRacing SDK shared-memory map under the name the C# reader opens.</summary>
public sealed class SdkMap : IDisposable
{
    private const int HeaderSize = 112;
    private const int VarHeaderSize = 144;
    private const int SessionReserve = 16384;
    private readonly MemoryMappedFile _map;
    private readonly MemoryMappedViewAccessor _view;
    private readonly Dictionary<string, (VarDef Def, int Offset)> _layout = new();
    private readonly int _bufLen;
    private readonly int[] _bufOffsets = new int[3];
    private readonly int _sessionOffset;
    private int _sessionUpdate;

    public SdkMap(IReadOnlyList<VarDef> vars)
    {
        var offset = 0;
        foreach (var def in vars)
        {
            var width = Vars.Width(def.Type);
            offset = (offset + width - 1) / width * width;
            _layout[def.Name] = (def, offset);
            offset += width * def.Count;
        }
        _bufLen = (offset + 15) / 16 * 16;
        var varEnd = HeaderSize + vars.Count * VarHeaderSize;
        _sessionOffset = (varEnd + 15) / 16 * 16;
        var bufStart = _sessionOffset + SessionReserve;
        for (var i = 0; i < 3; i++) _bufOffsets[i] = bufStart + i * _bufLen;
        var size = bufStart + 3 * _bufLen;
        _map = MemoryMappedFile.CreateNew(@"Local\IRSDKMemMapFileName", size, MemoryMappedFileAccess.ReadWrite);
        _view = _map.CreateViewAccessor(0, size, MemoryMappedFileAccess.ReadWrite);

        _view.Write(0, 2);               // version
        _view.Write(4, 0);               // status
        _view.Write(8, 60);              // tick rate
        _view.Write(24, vars.Count);     // num vars
        _view.Write(28, HeaderSize);     // var header offset
        _view.Write(32, 3);              // num bufs
        _view.Write(36, _bufLen);        // buf len
        for (var i = 0; i < 3; i++)
        {
            _view.Write(48 + i * 16, 0);
            _view.Write(48 + i * 16 + 4, _bufOffsets[i]);
        }
        var index = 0;
        foreach (var def in vars)
        {
            var start = HeaderSize + index * VarHeaderSize;
            _view.Write(start, def.Type);
            _view.Write(start + 4, _layout[def.Name].Offset);
            _view.Write(start + 8, def.Count);
            var name = Encoding.ASCII.GetBytes(def.Name);
            _view.WriteArray(start + 16, name, 0, name.Length);
            index++;
        }
    }

    public void WriteSession(string text)
    {
        var bytes = Encoding.UTF8.GetBytes(text + "\0");
        if (bytes.Length > SessionReserve) throw new InvalidOperationException("session text too long");
        _view.WriteArray(_sessionOffset, bytes, 0, bytes.Length);
        _view.Write(16, bytes.Length);
        _view.Write(20, _sessionOffset);
        _sessionUpdate++;
        _view.Write(12, _sessionUpdate);
    }

    public void SetStatus(int status) => _view.Write(4, status);

    public void WriteRow(int tick, int tickRate, IReadOnlyDictionary<string, object?> values)
    {
        var slot = tick % 3;
        var baseOffset = _bufOffsets[slot];
        _view.WriteArray(baseOffset, new byte[_bufLen], 0, _bufLen);
        foreach (var (name, (def, offset)) in _layout)
        {
            if (!values.TryGetValue(name, out var value) || value is null) continue;
            var position = baseOffset + offset;
            if (def.Count == 1) WriteScalar(position, def.Type, value);
            else
            {
                var array = ((System.Collections.IEnumerable)value).Cast<object>().ToArray();
                for (var i = 0; i < def.Count && i < array.Length; i++)
                    WriteScalar(position + i * Vars.Width(def.Type), def.Type, array[i]);
            }
        }
        _view.Write(8, tickRate);
        _view.Write(48 + slot * 16 + 4, baseOffset);
        _view.Write(48 + slot * 16, tick);
    }

    private void WriteScalar(int position, int type, object value)
    {
        switch (type)
        {
            case 0: _view.Write(position, Convert.ToByte(value)); break;
            case 1: _view.Write(position, (byte)((bool)value ? 1 : 0)); break;
            case 2: _view.Write(position, Convert.ToInt32(value)); break;
            case 3: _view.Write(position, Convert.ToUInt32(value)); break;
            case 4: _view.Write(position, Convert.ToSingle(value)); break;
            case 5: _view.Write(position, Convert.ToDouble(value)); break;
            default: throw new InvalidOperationException();
        }
    }

    public void Dispose()
    {
        _view.Dispose();
        _map.Dispose();
    }
}

public sealed class ScriptedSource : ILiveTelemetrySource
{
    public LiveTelemetrySample? Pending;
    public bool TryRead(out LiveTelemetrySample sample)
    {
        sample = Pending ?? new LiveTelemetrySample { Connected = false };
        var had = Pending is not null;
        Pending = null;
        return had;
    }
    public void Dispose() { }
}

/// <summary>Drives the real C# service (reflection on its private Poll) from scripted SDK bytes.</summary>
public sealed class Runner : IDisposable
{
    private static readonly MethodInfo PollMethod = typeof(LiveTelemetryService).GetMethod("Poll", BindingFlags.NonPublic | BindingFlags.Instance)!;
    private readonly SdkMap _map;
    private readonly ScriptedSource _source = new();
    private readonly LiveTelemetryService _service;
    private IRacingSdkTelemetrySource? _real;
    private bool _paused;
    private long _csEpochAfterPrevious;
    public uint RustEpoch;
    public int Tick = 100;

    public Runner(IReadOnlyList<VarDef> vars, bool safeGlance)
    {
        _map = new SdkMap(vars);
        _service = new LiveTelemetryService(_source, new LiveMonitorLayout { SafeGlanceEnabled = safeGlance });
    }

    public record Outcome(LiveMonitorState State, bool Changed);

    public Outcome Step(long timestampMs, bool connected, bool paused, string? sessionText, IReadOnlyDictionary<string, object?> values, int tickRate)
    {
        var ts = DateTimeOffset.FromUnixTimeMilliseconds(timestampMs);
        if (paused != _paused)
        {
            _service.SetCoachingPaused(paused);
            _paused = paused;
        }
        if (sessionText is not null) _map.WriteSession(sessionText);
        if (!connected)
        {
            _real?.Dispose();
            _real = null;
            _map.SetStatus(0);
            _source.Pending = new LiveTelemetrySample { Connected = false, Timestamp = ts };
        }
        else
        {
            _map.SetStatus(1);
            Tick++;
            _map.WriteRow(Tick, tickRate, values);
            _real ??= new IRacingSdkTelemetrySource();
            if (!_real.TryRead(out var sample) || !sample.Connected) throw new InvalidOperationException("real source did not read the scripted row");
            _source.Pending = sample with { Timestamp = ts };
        }
        PollMethod.Invoke(_service, null);
        var state = _service.Current;
        var changed = connected && state.SessionEpoch != _csEpochAfterPrevious;
        if (changed) RustEpoch++;
        _csEpochAfterPrevious = state.SessionEpoch;
        return new Outcome(state, changed);
    }

    public void Dispose()
    {
        _real?.Dispose();
        _service.Dispose();
        _map.Dispose();
    }
}
