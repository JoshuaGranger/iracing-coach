using System.Windows;
using System.Security.Cryptography;
using System.Text;
using System.Diagnostics;

namespace iRacingCoach.App;

public partial class App : System.Windows.Application
{
    private const string MutexName = @"Local\iRacingCoach.SingleInstance";
    private const string ActivationEventName = @"Local\iRacingCoach.Activate";
    private Mutex? _mutex;
    private EventWaitHandle? _activationEvent;
    private RegisteredWaitHandle? _activationWait;
    private MainWindow? _mainWindow;
    private System.Threading.Timer? _exitWatchdog;
    private int _exitStarted;

    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        var qaInstance = e.Args.Any(argument =>
            string.Equals(argument, "--qa-isolated", StringComparison.OrdinalIgnoreCase) ||
            argument.StartsWith("--qa-fixture-root", StringComparison.OrdinalIgnoreCase) ||
            argument.StartsWith("--qa-live-replay", StringComparison.OrdinalIgnoreCase));
        var instanceSuffix = qaInstance
            ? ".QA." + Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(AppContext.BaseDirectory + "|" + string.Join('|', e.Args))))[..12]
            : string.Empty;
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
            _mainWindow = new MainWindow();
        }
        catch (Exception error)
        {
            var startupError = new StartupErrorWindow(error);
            MainWindow = startupError;
            startupError.Closed += (_, _) => Shutdown(2);
            startupError.Show();
            return;
        }
        MainWindow = _mainWindow;
        _mainWindow.ExitRequested += ExitApplication;
        _activationWait = ThreadPool.RegisterWaitForSingleObject(_activationEvent, (_, _) =>
            Dispatcher.BeginInvoke(_mainWindow.ShowFromTray), null, Timeout.Infinite, false);
        _mainWindow.Show();
    }

    private void ExitApplication()
    {
        if (Interlocked.Exchange(ref _exitStarted, 1) != 0) return;
        // Service cleanup includes process and database shutdown. If one of those
        // dependencies stalls, Exit must still mean that the desktop process ends.
        _exitWatchdog = new System.Threading.Timer(
            _ => Environment.Exit(0),
            null,
            TimeSpan.FromSeconds(5),
            Timeout.InfiniteTimeSpan);
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

    protected override void OnExit(ExitEventArgs e)
    {
        try
        {
            try { _mainWindow?.DisposeApplication(); } catch (Exception error) { Trace.WriteLine($"Final application cleanup failed: {error}"); }
            _exitWatchdog?.Dispose();
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
}
