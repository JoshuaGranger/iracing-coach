using System.Text;
using System.Text.Json.Nodes;
using iRacingCoach.Coordinator;

namespace Oracle;

public sealed record Session(long SubSessionId, (int Num, string Type)[] Types, string? Ratio, string Track = "Indianapolis Raceway Park")
{
    public static readonly Session Default = new(8001, [(0, "Practice"), (1, "Race")], "12:1");

    public string Text()
    {
        var sb = new StringBuilder();
        sb.Append("---\nWeekendInfo:\n TrackName: irp\n TrackDisplayName: ").Append(Track).Append("\n TrackConfigName: Road\n SubSessionID: ").Append(SubSessionId).Append('\n');
        sb.Append("SessionInfo:\n Sessions:\n");
        foreach (var (num, type) in Types) sb.Append(" - SessionNum: ").Append(num).Append("\n   SessionType: ").Append(type).Append('\n');
        sb.Append("DriverInfo:\n DriverCarIdx: 0\n Drivers:\n - CarIdx: 0\n   UserID: 321\n   UserName: Joshua Granger\n   CarPath: toyotagr86\n");
        sb.Append("CarSetup:\n UpdateCount: 10\n Chassis:\n  Front:\n   BallastForward: 914 mm\n");
        if (Ratio is not null) sb.Append("   SteeringRatio: ").Append(Ratio).Append('\n');
        sb.Append("  Rear:\n   RearEndRatio: 5.5\n...\n");
        return sb.ToString();
    }

    public JsonObject ToJson()
    {
        var types = new JsonArray();
        foreach (var (num, type) in Types) types.Add(new JsonArray(num, type));
        var ratio = LiveSteering.RatioFromSessionInfo(Text());
        return new JsonObject
        {
            ["subsession_id"] = SubSessionId,
            ["session_types"] = types,
            ["steering_ratio"] = ratio.HasValue ? Json.Num(ratio.Value) : null,
            ["info_text"] = Text(),
        };
    }
}

/// <summary>The SDK variable values of one tick, with helpers that keep the CarIdx arrays consistent.</summary>
public sealed class Vs
{
    public Dictionary<string, object?> V = new();
    public int Pos = 5, ClassPos = 5, RawLap = 9;
    public double? Ahead = 1.5, Behind = 1.5, Leader = 20, LeaderLast = 30.4;
    public double PlayerF2 = 200;

    public static Vs Default()
    {
        var v = new Vs();
        var d = v.V;
        d["PlayerCarIdx"] = 0;
        d["SessionFlags"] = 0x4u;
        d["PlayerCarTowTime"] = 0f;
        d["SessionLapsRemainEx"] = 20.0;
        d["SessionLapsRemain"] = 20;
        d["LapLastLapTime"] = 0f;
        d["FuelLevel"] = 10f;
        d["FuelLevelPct"] = 0.5f;
        d["TrackTempCrew"] = 30f;
        d["TrackTemp"] = 29f;
        d["AirTemp"] = 21f;
        d["dcBrakeBias"] = 54.7f;
        d["OnPitRoad"] = false;
        d["PitRepairLeft"] = 0f;
        d["PitOptRepairLeft"] = 0f;
        d["SteeringWheelAngle"] = 0f;
        d["Throttle"] = 0.3f;
        d["Brake"] = 0f;
        d["BrakeABSactive"] = false;
        d["Gear"] = 3;
        d["RPM"] = 6000f;
        d["YawRate"] = 0f;
        d["LatAccel"] = 0f;
        d["LongAccel"] = 0f;
        d["Speed"] = 40f;
        d["LapDistPct"] = 0.5f;
        d["Lat"] = 39.78;
        d["Lon"] = -86.24;
        d["SessionTime"] = 100.0;
        d["SessionNum"] = 0;
        d["SessionUniqueID"] = 123456;
        d["PlayerCarMyIncidentCount"] = 2;
        d["PlayerIncidents"] = 3;
        d["IsOnTrack"] = true;
        d["IsInGarage"] = false;
        v.Sync();
        return v;
    }

    public Vs Clone()
    {
        var copy = new Vs { Pos = Pos, ClassPos = ClassPos, RawLap = RawLap, Ahead = Ahead, Behind = Behind, Leader = Leader, LeaderLast = LeaderLast, PlayerF2 = PlayerF2 };
        foreach (var (key, value) in V) copy.V[key] = value is Array array ? array.Clone() : value;
        return copy;
    }

    public void Sync()
    {
        int n = Vars.Cars;
        var pos = new int[n];
        var cpos = new int[n];
        pos[0] = Pos; pos[1] = Pos - 1; pos[2] = Pos + 1; pos[3] = Pos == 1 ? 2 : 1;
        cpos[0] = ClassPos; cpos[1] = ClassPos - 1; cpos[2] = ClassPos + 1; cpos[3] = ClassPos == 1 ? 2 : 1;
        var laps = new int[n];
        for (var i = 0; i < n; i++) laps[i] = RawLap;
        if (Ahead is null) laps[1] = RawLap + 1;
        if (Behind is null) laps[2] = RawLap + 1;
        if (Leader is null) laps[3] = RawLap + 1;
        var f2 = new float[n];
        f2[0] = (float)PlayerF2;
        f2[1] = (float)(PlayerF2 - (Ahead ?? 0));
        f2[2] = (float)(PlayerF2 + (Behind ?? 0));
        f2[3] = (float)(PlayerF2 - (Leader ?? 0));
        var last = new float[n];
        last[3] = (float)(LeaderLast ?? 0);
        V["CarIdxPosition"] = pos;
        V["CarIdxClassPosition"] = cpos;
        V["CarIdxLap"] = laps;
        V["CarIdxF2Time"] = f2;
        V["CarIdxLastLapTime"] = last;
        V["PlayerCarPosition"] = Pos;
        V["PlayerCarClassPosition"] = ClassPos;
        V["Lap"] = RawLap;
    }

    // Engine-lap oriented helpers (engine lap = raw + 1).
    public Vs Lap(int engineLap) { RawLap = engineLap - 1; Sync(); return this; }
    public Vs Flags(uint? flags) { V["SessionFlags"] = flags ?? 0u; return this; }
    public Vs Green() => Flags(0x4);
    public Vs Caution() => Flags(0x8);
    public Vs Black() => Flags(0x10000);
    public Vs Repair() => Flags(0x4 | 0x100000);
    public Vs Brake(double x) { V["Brake"] = (float)x; return this; }
    public Vs Throttle(double x) { V["Throttle"] = (float)x; return this; }
    public Vs Wheel(double x) { V["SteeringWheelAngle"] = (float)x; return this; }
    public Vs LatG(double g) { V["LatAccel"] = (float)(g * 9.80665); return this; }
    public Vs Fuel(double x) { V["FuelLevel"] = (float)x; return this; }
    public Vs FuelPct(double x) { V["FuelLevelPct"] = (float)x; return this; }
    public Vs LastLap(double x) { V["LapLastLapTime"] = (float)x; return this; }
    public Vs Pit(bool on) { V["OnPitRoad"] = on; return this; }
    public Vs Dist(double x) { V["LapDistPct"] = (float)x; return this; }
    public Vs Set(string name, object? value) { V[name] = value; return this; }
    public Vs AheadGap(double? x) { Ahead = x; Sync(); return this; }
    public Vs BehindGap(double? x) { Behind = x; Sync(); return this; }
    public Vs LeaderGap(double? x) { Leader = x; Sync(); return this; }
    public Vs LeaderLastLap(double? x) { LeaderLast = x; Sync(); return this; }
    public Vs LeaderLapAndGap(double? last, double? gap) { LeaderLast = last; Leader = gap; Sync(); return this; }
    public Vs Position(int p) { Pos = p; Sync(); return this; }
    public Vs Class(int p) { ClassPos = p; Sync(); return this; }
    public Vs Remaining(double x) { V["SessionLapsRemainEx"] = x; return this; }
    public Vs Repairing(double mandatory, double optional = 0) { V["PitRepairLeft"] = (float)mandatory; V["PitOptRepairLeft"] = (float)optional; return this; }
    public Vs Tow(double seconds) { V["PlayerCarTowTime"] = (float)seconds; return this; }
}

public sealed class StepSpec
{
    public long T;
    public bool Connected = true;
    public bool Paused;
    public Session? NewSession;
    public bool Tiles;
    public Dictionary<string, object?> Values = new();
}

public sealed class Scn
{
    public string Name = "";
    public string Origin = "";
    public bool SafeGlance = true;
    public HashSet<string> Omit = new();
    public Session Session = Session.Default;
    public List<StepSpec> Steps = new();
    public int TickRate = 60;
    private Vs _cur = Vs.Default();
    private bool _paused;

    public Scn(string name, string origin) { Name = name; Origin = origin; }

    public Scn Step(long t, Action<Vs>? change = null, bool connected = true, bool? paused = null, Session? session = null, bool tiles = false)
    {
        change?.Invoke(_cur);
        _cur.V["SessionTime"] = t / 1000.0;
        if (paused.HasValue) _paused = paused.Value;
        Steps.Add(new StepSpec
        {
            T = t, Connected = connected, Paused = _paused, NewSession = session, Tiles = tiles,
            Values = _cur.Clone().V,
        });
        return this;
    }

    public Scn Last(bool tiles = true) { Steps[^1].Tiles = tiles; return this; }
}
