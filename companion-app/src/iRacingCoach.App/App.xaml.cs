using System.Windows;
using System.Security.Cryptography;
using System.Text;

namespace iRacingCoach.App;

public partial class App : System.Windows.Application
{
    private const string MutexName = @"Local\iRacingCoach.SingleInstance";
    private const string ActivationEventName = @"Local\iRacingCoach.Activate";
    private Mutex? _mutex;
    private EventWaitHandle? _activationEvent;
    private RegisteredWaitHandle? _activationWait;
    private MainWindow? _mainWindow;

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
        _mainWindow?.DisposeApplication();
        Shutdown();
    }

    protected override void OnExit(ExitEventArgs e)
    {
        _activationWait?.Unregister(null);
        _activationEvent?.Dispose();
        if (_mutex is not null)
        {
            try { _mutex.ReleaseMutex(); } catch (ApplicationException) { }
            _mutex.Dispose();
        }
        base.OnExit(e);
    }
}
