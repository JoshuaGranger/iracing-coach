<#
.SYNOPSIS
  Gate G4 recorder and window driver for the sim PC (workstreams.md, gate G4; method of prove/P3).

.DESCRIPTION
  Windows PowerShell 5.1 with an inline C# sampler (compiled by Add-Type, no build step). Read-only
  towards iRacing: it opens the SDK shared-memory map `Local\IRSDKMemMapFileName` for reading and
  never touches Documents\iRacing. It reads the collector process's CPU time, working set, private
  bytes and Win32 I/O counters.

  -Mode windows  Runs the window plan at 1 Hz (one CSV row per second, the same recorder in every
                 window). B = collector not running (asked to exit through its quit event
                 `Local\iRacingCoach.Collector.Quit`), C = collector running (`<exe> run`, the HKCU Run
                 value's command line, started through ShellExecute so it inherits no handles).
                 Before a C window the collector is started and -StartSettle seconds pass; before a
                 B window it exits and -StopSettle seconds pass. Every window is -WindowSeconds long.
                 After the plan the collector is left in the state of the last window.
  -Mode fast     Samples IsOnTrack and the collector's I/O counters in a 1 ms loop for -Seconds and
                 writes a row whenever one of them changes (plus every 100 ms), for the abort check.
  -Mode probe    Prints one sample (map, values, collector) and exits.

  A status line (`<utc> <label>`) is written to -Status when given, so a remote caller can follow.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File g4-perf.ps1 -Mode windows -Out C:\scratch\g4.csv -Status C:\scratch\g4.status
  powershell -NoProfile -ExecutionPolicy Bypass -File g4-perf.ps1 -Mode fast -Seconds 60 -Out C:\scratch\abort.csv
#>
param(
    [ValidateSet('windows', 'fast', 'probe')] [string] $Mode = 'probe',
    [string] $Out = '',
    [string] $Status = '',
    [double] $Seconds = 60,
    [string] $Plan = 'B,C,B,C,B,C,B,C,B',
    [int] $WindowSeconds = 100,
    [int] $StartSettle = 20,
    [int] $StopSettle = 10,
    [string] $CollectorExe = (Join-Path $env:LOCALAPPDATA 'Programs\iRacingCoach\coach-collector.exe')
)

$ErrorActionPreference = 'Stop'
# Remote jobs inherit a PowerShell 7 module path that breaks 5.1 cmdlets.
$env:PSModulePath = [Environment]::GetEnvironmentVariable('PSModulePath', 'Machine')

$source = @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.IO.MemoryMappedFiles;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

public static class G4
{
    [StructLayout(LayoutKind.Sequential)]
    struct IO_COUNTERS
    {
        public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount;
        public ulong ReadTransferCount, WriteTransferCount, OtherTransferCount;
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool GetProcessIoCounters(IntPtr hProcess, out IO_COUNTERS counters);

    [DllImport("winmm.dll")] static extern uint timeBeginPeriod(uint ms);
    [DllImport("winmm.dll")] static extern uint timeEndPeriod(uint ms);

    static MemoryMappedFile mmf;
    static MemoryMappedViewAccessor acc;
    static Dictionary<string, int[]> vars;
    static readonly string[] Wanted = { "FrameRate", "CpuUsageFG", "CpuUsageBG", "GpuUsage", "IsOnTrack", "Speed", "SessionTick" };

    static bool Open()
    {
        if (acc != null) return true;
        try
        {
            mmf = MemoryMappedFile.OpenExisting("Local\\IRSDKMemMapFileName", MemoryMappedFileRights.Read);
            acc = mmf.CreateViewAccessor(0, 0, MemoryMappedFileAccess.Read);
            vars = null;
            return true;
        }
        catch { acc = null; mmf = null; return false; }
    }

    static void LoadVars()
    {
        int numVars = acc.ReadInt32(24);
        int headerOffset = acc.ReadInt32(28);
        var map = new Dictionary<string, int[]>();
        var name = new byte[32];
        for (int i = 0; i < numVars; i++)
        {
            long at = headerOffset + (long)i * 144;
            int type = acc.ReadInt32(at);
            int offset = acc.ReadInt32(at + 4);
            acc.ReadArray(at + 16, name, 0, 32);
            int len = Array.IndexOf(name, (byte)0);
            string n = Encoding.ASCII.GetString(name, 0, len < 0 ? 32 : len);
            if (Array.IndexOf(Wanted, n) >= 0) map[n] = new[] { type, offset };
        }
        vars = map;
    }

    /// <summary>Returns "connected,FrameRate,CpuUsageFG,CpuUsageBG,GpuUsage,IsOnTrack,Speed,SessionTick".</summary>
    public static string Sdk()
    {
        if (!Open()) return "nomap,,,,,,,";
        try
        {
            int status = acc.ReadInt32(4);
            if ((status & 1) == 0) { vars = null; return "0,,,,,,,"; }
            if (vars == null || vars.Count < Wanted.Length) LoadVars();
            int numBuf = acc.ReadInt32(32);
            int best = -1, bestTick = int.MinValue;
            for (int i = 0; i < numBuf && i < 4; i++)
            {
                int tick = acc.ReadInt32(48 + i * 16);
                if (tick > bestTick) { bestTick = tick; best = i; }
            }
            int buf = acc.ReadInt32(48 + best * 16 + 4);
            var sb = new StringBuilder("1");
            foreach (var w in Wanted)
            {
                sb.Append(',');
                int[] v;
                if (!vars.TryGetValue(w, out v)) continue;
                long at = buf + (long)v[1];
                switch (v[0])
                {
                    case 1: sb.Append(acc.ReadByte(at) != 0 ? "1" : "0"); break;
                    case 2: case 3: sb.Append(acc.ReadInt32(at).ToString(CultureInfo.InvariantCulture)); break;
                    case 4: sb.Append(acc.ReadSingle(at).ToString("R", CultureInfo.InvariantCulture)); break;
                    case 5: sb.Append(acc.ReadDouble(at).ToString("R", CultureInfo.InvariantCulture)); break;
                }
            }
            return sb.ToString();
        }
        catch { acc = null; mmf = null; vars = null; return "err,,,,,,,"; }
    }

    public static Process Collector()
    {
        var list = Process.GetProcessesByName("coach-collector");
        return list.Length > 0 ? list[0] : null;
    }

    /// <summary>"pid,cpu_ms,ws,private,io_r,io_w,io_o,ops_r,ops_w,ops_o"</summary>
    public static string Proc(Process p)
    {
        if (p == null) return ",,,,,,,,,";
        try
        {
            p.Refresh();
            if (p.HasExited) return ",,,,,,,,,";
            IO_COUNTERS io;
            GetProcessIoCounters(p.Handle, out io);
            return string.Join(",", new string[] {
                p.Id.ToString(), ((long)p.TotalProcessorTime.TotalMilliseconds).ToString(),
                p.WorkingSet64.ToString(), p.PrivateMemorySize64.ToString(),
                io.ReadTransferCount.ToString(), io.WriteTransferCount.ToString(), io.OtherTransferCount.ToString(),
                io.ReadOperationCount.ToString(), io.WriteOperationCount.ToString(), io.OtherOperationCount.ToString() });
        }
        catch { return ",,,,,,,,,"; }
    }

    const string Header = "utc,t_s,label,in_window,connected,FrameRate,CpuUsageFG,CpuUsageBG,GpuUsage,IsOnTrack,Speed,SessionTick,pid,cpu_ms,ws,private,io_r,io_w,io_o,ops_r,ops_w,ops_o";

    static void SetStatus(string path, string text)
    {
        if (string.IsNullOrEmpty(path)) return;
        try { File.WriteAllText(path, DateTime.UtcNow.ToString("o") + " " + text + Environment.NewLine); } catch { }
    }

    static void StartCollector(string exe)
    {
        var psi = new ProcessStartInfo(exe, "run");
        psi.UseShellExecute = true;
        psi.WindowStyle = ProcessWindowStyle.Hidden;
        psi.WorkingDirectory = Path.GetDirectoryName(exe);
        Process.Start(psi);
    }

    static bool StopCollector(int waitMs)
    {
        var p = Collector();
        if (p == null) return true;
        try
        {
            using (var ev = System.Threading.EventWaitHandle.OpenExisting("Local\\iRacingCoach.Collector.Quit"))
            {
                ev.Set();
            }
        }
        catch { }
        return p.WaitForExit(waitMs);
    }

    /// <summary>Window plan at 1 Hz. Returns a summary line.</summary>
    public static string Windows(string plan, int windowS, int startSettle, int stopSettle, string exe, string outCsv, string status)
    {
        var steps = plan.Split(',');
        var sw = Stopwatch.StartNew();
        var start = DateTime.UtcNow;
        var notes = new StringBuilder();
        using (var w = new StreamWriter(outCsv, false, new UTF8Encoding(false)))
        {
            w.WriteLine(Header);
            long next = 0;
            Process proc = Collector();
            Action<string, bool> row = (label, inWindow) =>
            {
                long wait = next - sw.ElapsedMilliseconds;
                if (wait > 0) Thread.Sleep((int)wait);
                next += 1000;
                if (proc == null || proc.HasExited) proc = Collector();
                w.WriteLine(DateTime.UtcNow.ToString("o") + "," + (sw.ElapsedMilliseconds / 1000.0).ToString("F3", CultureInfo.InvariantCulture)
                    + "," + label + "," + (inWindow ? "1" : "0") + "," + Sdk() + "," + Proc(proc));
                w.Flush();
            };
            int nb = 0, nc = 0;
            foreach (var s in steps)
            {
                bool c = s.Trim() == "C";
                string label = c ? "C" + (++nc) : "B" + (++nb);
                if (c)
                {
                    if (Collector() == null) { StartCollector(exe); notes.Append(label + ":started "); }
                    SetStatus(status, label + " settle");
                    for (int i = 0; i < startSettle; i++) row(label + "-settle", false);
                }
                else
                {
                    bool stopped = StopCollector(15000);
                    notes.Append(label + (stopped ? ":stopped " : ":STOP-FAILED "));
                    SetStatus(status, label + " settle");
                    for (int i = 0; i < stopSettle; i++) row(label + "-settle", false);
                }
                SetStatus(status, label + " window");
                for (int i = 0; i < windowS; i++) row(label, true);
            }
            SetStatus(status, "done");
        }
        return "start " + start.ToString("o") + " end " + DateTime.UtcNow.ToString("o") + " " + notes.ToString();
    }

    /// <summary>1 ms loop: rows on change of IsOnTrack or the collector's I/O counters, and every 100 ms.</summary>
    public static string Fast(double seconds, string outCsv, string status)
    {
        var sw = Stopwatch.StartNew();
        var start = DateTime.UtcNow;
        long rows = 0, loops = 0;
        // 1 ms sleeps for this check only (iRacing itself runs at 1 ms timer resolution).
        timeBeginPeriod(1);
        try {
        using (var w = new StreamWriter(outCsv, false, new UTF8Encoding(false)))
        {
            w.WriteLine("ms,utc,connected,FrameRate,CpuUsageFG,CpuUsageBG,GpuUsage,IsOnTrack,Speed,SessionTick,pid,cpu_ms,ws,private,io_r,io_w,io_o,ops_r,ops_w,ops_o");
            Process proc = Collector();
            string lastKey = null;
            long lastRow = -1000, lastFind = 0;
            SetStatus(status, "fast running");
            while (sw.Elapsed.TotalSeconds < seconds)
            {
                long ms = sw.ElapsedMilliseconds;
                if ((proc == null || proc.HasExited) && ms - lastFind > 100) { proc = Collector(); lastFind = ms; }
                string sdk = Sdk();
                string pr = Proc(proc);
                string[] sp = sdk.Split(',');
                string[] pp = pr.Split(',');
                // change key: IsOnTrack and the I/O transfer counters (not CPU or memory)
                string key = sp[5] + "|" + pp[4] + "|" + pp[5] + "|" + pp[6];
                if (key != lastKey || ms - lastRow >= 100)
                {
                    w.WriteLine(ms + "," + start.AddMilliseconds(sw.Elapsed.TotalMilliseconds).ToString("o") + "," + sdk + "," + pr);
                    lastKey = key; lastRow = ms; rows++;
                }
                loops++;
                Thread.Sleep(1);
            }
            SetStatus(status, "fast done");
        }
        } finally { timeEndPeriod(1); }
        return "loops " + loops + " rows " + rows + " seconds " + sw.Elapsed.TotalSeconds.ToString("F1", CultureInfo.InvariantCulture);
    }
}
'@

Add-Type -TypeDefinition $source -Language CSharp

switch ($Mode) {
    'probe' {
        "sdk  connected,FrameRate,CpuUsageFG,CpuUsageBG,GpuUsage,IsOnTrack,Speed,SessionTick"
        "     " + [G4]::Sdk()
        "proc pid,cpu_ms,ws,private,io_r,io_w,io_o,ops_r,ops_w,ops_o"
        "     " + [G4]::Proc([G4]::Collector())
    }
    'windows' {
        if (-not $Out) { throw '-Out is required' }
        if (-not (Test-Path $CollectorExe)) { throw "collector not found: $CollectorExe" }
        [G4]::Windows($Plan, $WindowSeconds, $StartSettle, $StopSettle, $CollectorExe, $Out, $Status)
    }
    'fast' {
        if (-not $Out) { throw '-Out is required' }
        [G4]::Fast($Seconds, $Out, $Status)
    }
}
