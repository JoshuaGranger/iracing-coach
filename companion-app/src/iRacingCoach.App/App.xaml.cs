using System.Windows;
using System.Diagnostics;
using System.IO;
using iRacingCoach.Coordinator;

namespace iRacingCoach.App;

public partial class App : System.Windows.Application
{
    private const string MutexName = @"Local\iRacingCoach.SingleInstance";
    private const string ActivationEventName = @"Local\iRacingCoach.Activate";
    private Mutex? _mutex;
    private EventWaitHandle? _activationEvent;
    private RegisteredWaitHandle? _activationWait;
    private MainWindow? _mainWindow;
    // Static by design: Application.Current can be released during WPF shutdown,
    // but the process-level deadline must remain rooted until the process is gone.
    private static System.Threading.Timer? _exitWatchdog;
    private static int _exitDeadlineArmed;
    private int _exitStarted;

    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        CompanionHostProfile profile;
        try
        {
            profile = CompanionHostProfile.FromArguments(e.Args);
        }
        catch (Exception error) when (error is ArgumentException or IOException or UnauthorizedAccessException or NotSupportedException)
        {
            ShowStartupError(error);
            return;
        }

        var instanceSuffix = profile.IsIsolated ? "." + profile.ProcessIdentity : string.Empty;
        _mutex = new Mutex(true, MutexName + instanceSuffix, out var firstInstance);
        _activationEvent = new EventWaitHandle(false, EventResetMode.AutoReset, ActivationEventName + instanceSuffix);
        if (!firstInstance)
        {
            _activationEvent.Set();
            Shutdown(0);
            return;
        }

        try
        {
            _mainWindow = new MainWindow(profile);
        }
        catch (Exception error)
        {
            ShowStartupError(error);
            return;
        }
        MainWindow = _mainWindow;
        _mainWindow.ExitRequested += ExitApplication;
        _activationWait = ThreadPool.RegisterWaitForSingleObject(_activationEvent, (_, _) =>
            Dispatcher.BeginInvoke(_mainWindow.ShowFromTray), null, Timeout.Infinite, false);
        _mainWindow.Show();
    }

    private void ShowStartupError(Exception error)
    {
        var startupError = new StartupErrorWindow(error);
        MainWindow = startupError;
        startupError.Closed += (_, _) => Shutdown(2);
        startupError.Show();
    }

    private void ExitApplication()
    {
        if (Interlocked.Exchange(ref _exitStarted, 1) != 0) return;
        ArmExitDeadline();
        try
        {
            _mainWindow?.DisposeApplication();
        }
        catch (Exception error)
        {
            Trace.WriteLine($"Application cleanup failed during exit: {error}");
        }
        finally
        {
            try
            {
                if (!Dispatcher.HasShutdownStarted) Shutdown(0);
            }
            finally
            {
                // WPF, WebView2, and hosted services can own foreground threads.
                // Normal cleanup has completed at this point; terminate any residue.
                Environment.Exit(0);
            }
        }
    }

    internal static void ArmExitDeadline()
    {
        if (Interlocked.Exchange(ref _exitDeadlineArmed, 1) != 0) return;
        // Service cleanup includes process and database shutdown. If one of those
        // dependencies stalls, Exit must still mean that the desktop process ends.
        // MainWindow calls this directly from the WinForms menu callback, before
        // any WPF Dispatcher handoff or UI cleanup can stall.
        _exitWatchdog = new System.Threading.Timer(
            static _ => ForceTerminateProcess(),
            null,
            TimeSpan.FromSeconds(5),
            Timeout.InfiniteTimeSpan);
    }

    protected override void OnExit(ExitEventArgs e)
    {
        try
        {
            try { _mainWindow?.DisposeApplication(); } catch (Exception error) { Trace.WriteLine($"Final application cleanup failed: {error}"); }
            // A normal non-tray shutdown can release the unused timer. Once an
            // explicit Exit is armed it must remain alive until the OS process is
            // gone; disposing it here recreated the exact background-process bug.
            if (Volatile.Read(ref _exitDeadlineArmed) == 0) _exitWatchdog?.Dispose();
            _activationWait?.Unregister(null);
            _activationEvent?.Dispose();
            if (_mutex is not null)
            {
                try { _mutex.ReleaseMutex(); } catch (ApplicationException) { }
                _mutex.Dispose();
            }
        }
        finally { base.OnExit(e); }
    }

    private static void ForceTerminateProcess()
    {
        using var current = Process.GetCurrentProcess();
        try
        {
            // Kill the full WebView/native child tree as well as the WPF host.
            // This is the bounded fallback only; normal cleanup receives five
            // seconds first and normally exits before this callback runs.
            current.Kill(entireProcessTree: true);
            return;
        }
        catch (Exception error)
        {
            Trace.WriteLine($"Whole-tree application-exit fallback failed: {error}");
        }

        try
        {
            // Tree enumeration can fail because a WebView child exits during the
            // walk. A direct host kill still guarantees the app itself is gone.
            current.Kill();
        }
        catch (Exception error)
        {
            Trace.WriteLine($"Direct application-exit fallback failed: {error}");
            Environment.Exit(0);
        }
    }
}
