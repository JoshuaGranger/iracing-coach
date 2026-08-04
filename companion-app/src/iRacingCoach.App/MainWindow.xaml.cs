using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Threading;
using iRacingCoach.Coordinator;
using iRacingCoach.Contracts;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Web.WebView2.Core;
using Forms = System.Windows.Forms;

namespace iRacingCoach.App;

public partial class MainWindow : Window
{
    private const int WmHotkey = 0x0312;
    private const int LiveMonitorHotkeyId = 0x4937;
    private readonly CompanionState _state;
    private readonly ServiceProvider _services;
    private readonly LiveMonitorWindow _liveMonitor;
    private readonly Forms.NotifyIcon _trayIcon;
    private readonly Forms.ToolStripMenuItem _monitorItem;
    private readonly Forms.ToolStripMenuItem _statusItem;
    private readonly Forms.ToolStripMenuItem _pauseItem;
    private readonly bool _qaMode;
    private bool _exitRequested;
    private bool _trayNoticeShown;
    private bool _disposed;
    private HwndSource? _windowSource;
    private DateTimeOffset _lastTrayLiveUpdate = DateTimeOffset.MinValue;

    public MainWindow()
    {
        var services = new ServiceCollection();
        services.AddWpfBlazorWebView();
        var allArguments = Environment.GetCommandLineArgs();
        var qaOptions = QaLaunchOptions.Parse(allArguments);
        _qaMode = qaOptions.Enabled;
        var replayFixture = allArguments.FirstOrDefault(argument => argument.StartsWith("--telemetry-replay=", StringComparison.OrdinalIgnoreCase))?.Split('=', 2)[1]?.Trim().ToLowerInvariant();
        var qaPage = qaOptions.Page;
        var qaScenario = qaOptions.Scenario;
        var qaSize = qaOptions.Size;
        var qaExitAfterReady = allArguments.Any(argument => string.Equals(argument, "--qa-exit-after-ready", StringComparison.OrdinalIgnoreCase));
        var populatedQa = false;
#if DEBUG
        if (!qaOptions.Enabled)
        {
            qaPage = allArguments.FirstOrDefault(argument => argument.StartsWith("--qa-page=", StringComparison.OrdinalIgnoreCase))?.Split('=', 2)[1];
            qaScenario = allArguments.FirstOrDefault(argument => argument.StartsWith("--qa-scenario=", StringComparison.OrdinalIgnoreCase))?.Split('=', 2)[1] ?? string.Empty;
            qaSize = allArguments.FirstOrDefault(argument => argument.StartsWith("--qa-size=", StringComparison.OrdinalIgnoreCase))?.Split('=', 2)[1];
            var legacyFixture = allArguments.FirstOrDefault(argument => argument.StartsWith("--qa-fixture=", StringComparison.OrdinalIgnoreCase))?.Split('=', 2)[1]?.ToLowerInvariant() ?? string.Empty;
            populatedQa = legacyFixture is "populated" or "empty" or "partial" or "error";
            var legacyLiveFixture = replayFixture ?? Environment.GetEnvironmentVariable("IRACING_COACH_DEBUG_LIVE_FIXTURE")?.Trim().ToLowerInvariant();
            ILiveTelemetrySource legacyLiveSource = legacyLiveFixture is "green" or "caution" or "repair" or "fuel" or "brake" or "baseline" or "disconnected"
                ? new DebugLiveTelemetrySource(legacyLiveFixture)
                : new DisconnectedLiveTelemetrySource();
            _state = populatedQa
                ? new CompanionState(new DebugFixtureBackendClient(qaScenario, legacyFixture), new DebugFixtureSettingsStore(qaPage == "first-run"), legacyLiveSource, new DisabledCoachEngineSupervisor(), new DebugCredentialStore())
                : legacyLiveFixture is not null ? new CompanionState(legacyLiveSource) : new CompanionState();
        }
        else
#endif
        if (qaOptions.Enabled)
        {
            ILiveTelemetrySource liveSource = qaOptions.LiveReplayPath is null
                ? new DisconnectedLiveTelemetrySource()
                : new ReplayFileLiveTelemetrySource(qaOptions.LiveReplayPath, qaOptions.TimeScale);
            _state = new CompanionState(
                new QaFixtureBackendClient(qaOptions.FixtureRoot!, qaOptions.Scenario),
                new QaFixtureSettingsStore(qaOptions.ArchiveRoot!),
                liveSource,
                new DisabledCoachEngineSupervisor(),
                new QaFixtureCredentialStore(),
                qaFixtureMode: true);
        }
#if !DEBUG
        else
        {
            _state = replayFixture is "green" or "caution" or "repair" or "fuel" or "brake" or "baseline" or "disconnected"
                ? new CompanionState(new DebugLiveTelemetrySource(replayFixture))
                : new CompanionState();
        }
#endif
        var state = _state ?? throw new InvalidOperationException("The companion state was not initialized.");
        services.AddSingleton(state);
#if DEBUG
        services.AddBlazorWebViewDeveloperTools();
#endif
        _services = services.BuildServiceProvider();
        Resources.Add("services", _services);
        InitializeComponent();
        if ((_qaMode || populatedQa) && qaSize?.Split('x', 2) is [var widthText, var heightText] &&
            double.TryParse(widthText, out var qaWidth) && double.TryParse(heightText, out var qaHeight))
        {
            Width = Math.Max(MinWidth, qaWidth);
            Height = Math.Max(MinHeight, qaHeight);
        }
        if (_qaMode) Title = "iRacing Coach · QA Fixture";
        _liveMonitor = new LiveMonitorWindow(state);

        _monitorItem = new Forms.ToolStripMenuItem("Show Live Monitor", null, (_, _) => _state.ToggleLiveMonitor());
        _statusItem = new Forms.ToolStripMenuItem("Live telemetry: waiting for iRacing") { Enabled = false };
        _pauseItem = new Forms.ToolStripMenuItem("Pause live coaching", null, (_, _) => _state.ToggleLiveCoaching());
        var trayMenu = new Forms.ContextMenuStrip();
        trayMenu.Items.Add(new Forms.ToolStripMenuItem("Open iRacing Coach", null, (_, _) => ShowFromTray()));
        trayMenu.Items.Add(_monitorItem);
        trayMenu.Items.Add(_statusItem);
        trayMenu.Items.Add(_pauseItem);
        trayMenu.Items.Add(new Forms.ToolStripSeparator());
        trayMenu.Items.Add(new Forms.ToolStripMenuItem("Settings", null, (_, _) => OpenSettings()));
        trayMenu.Items.Add(new Forms.ToolStripSeparator());
        trayMenu.Items.Add(new Forms.ToolStripMenuItem("Exit", null, (_, _) => RequestExit()));
        trayMenu.Opening += (_, _) => RefreshTrayMenu();

        var trayIcon = System.Drawing.Icon.ExtractAssociatedIcon(Environment.ProcessPath!);
        _trayIcon = new Forms.NotifyIcon { Icon = trayIcon, Visible = true, Text = "iRacing Coach · Waiting for iRacing", ContextMenuStrip = trayMenu };
        _trayIcon.DoubleClick += (_, _) => ShowFromTray();

        SourceInitialized += (_, _) =>
        {
            ApplyDarkTitleBar();
            _windowSource = HwndSource.FromHwnd(new WindowInteropHelper(this).Handle);
            _windowSource?.AddHook(WindowMessageHook);
            _ = RegisterGlobalHotkey();
        };
        StateChanged += (_, _) => { if (WindowState == WindowState.Minimized) HideToTray(false); };
        Closing += OnClosing;
        _state.Changed += OnStateChanged;
        _state.LiveTelemetryChanged += OnLiveTelemetryChanged;
        _state.LiveMonitorVisibilityRequested += OnMonitorVisibilityRequested;
        _state.RawTelemetryLocateRequested += OnRawTelemetryLocateRequested;
        _state.SettingsSaved += settings =>
        {
            if (!_qaMode && !StartupRegistration.Apply(settings.LaunchAtSignIn))
                _state.Notify("Settings saved, but Windows sign-in startup could not be updated.");
            if (!RegisterGlobalHotkey())
                _state.Notify("Settings saved, but that global Live Monitor hotkey is invalid or already in use.");
        };
        if (!_qaMode) _ = StartupRegistration.Apply(_state.Settings.LaunchAtSignIn);

        if (Environment.GetCommandLineArgs().Any(argument => string.Equals(argument, "--minimized", StringComparison.OrdinalIgnoreCase)))
            Loaded += (_, _) => HideToTray(false);
        var supportedQaPage = qaPage is "home" or "live" or "analysis" or "planning" or "setup" or "tuning" or "connections" or "settings" or "first-run";
        if (_qaMode || populatedQa)
            Loaded += async (_, _) =>
            {
                await _state.InitializeAsync();
                if (supportedQaPage) _state.Navigate(qaPage!);
                if (qaScenario.Length > 0) await PrepareVisualQaScenarioAsync(qaScenario);
                if (qaExitAfterReady) RequestExit();
            };
#if DEBUG
        else if (supportedQaPage)
            Loaded += (_, _) => _state.Navigate(qaPage!);
#endif
        if ((_qaMode && qaOptions.OpenMonitor) || allArguments.Any(argument => string.Equals(argument, "--qa-open-monitor", StringComparison.OrdinalIgnoreCase)))
            Loaded += (_, _) => _state.SetLiveMonitorVisible(true);
        if (!IsWebView2Available()) Content = BuildRuntimeNotice();
    }

    private async Task PrepareVisualQaScenarioAsync(string scenario)
    {
        switch (scenario)
        {
            case "analysis-card":
                if (_state.Races.FirstOrDefault() is { } analysisRace) await _state.OpenRaceAsync(analysisRace);
                break;
            case "planning-result":
                await _state.GeneratePlanAsync();
                break;
            case "tuning-result":
                if (_state.SelectedTuningRace is { } tuningRace) await _state.AnalyzeRaceAsync(tuningRace);
                _state.Navigate("tuning");
                _state.TuningRunPhase = "Late run";
                _state.TuningCornerPhase = "Center";
                _state.TuningBalance = "Tight / understeer";
                _state.TuningSeverity = "Moderate";
                _state.TuningConfidence = "High";
                _state.TuningCorner = "Turns 3–4";
                _state.TuningPriority = true;
                _state.AddTuningFeedback();
                _state.TuningRunPhase = "Early run";
                _state.TuningCornerPhase = "Entry";
                _state.TuningBalance = "Loose / oversteer";
                _state.TuningSeverity = "Mild";
                _state.TuningConfidence = "Medium";
                _state.TuningCorner = "Turns 1–2";
                _state.TuningPriority = false;
                _state.AddTuningFeedback();
                await _state.GenerateExperimentAsync();
                break;
            case "job-running":
                if (_state.Races.FirstOrDefault() is { } jobRace) _ = _state.AnalyzeRaceAsync(jobRace);
                break;
            case "first-run-chatgpt":
                _state.ContinueSetupWithoutConnection();
                break;
            case "first-run-garage61":
                _state.ContinueSetupWithoutConnection(); _state.ContinueSetupWithoutConnection();
                break;
            case "first-run-verify":
                _state.ContinueSetupWithoutConnection(); _state.ContinueSetupWithoutConnection(); _state.ContinueSetupWithoutConnection();
                break;
            case "first-run-ready":
                _state.ContinueSetupWithoutConnection(); _state.ContinueSetupWithoutConnection(); _state.ContinueSetupWithoutConnection(); _state.ContinueSetupWithoutConnection();
                break;
        }
    }

    public event Action? ExitRequested;

    public void ShowFromTray()
    {
        if (_disposed || _exitRequested) return;
        _state.SetPrimaryUiVisible(true);
        Show();
        if (WindowState == WindowState.Minimized) WindowState = WindowState.Normal;
        Activate();
        Topmost = true; Topmost = false;
        Focus();
    }

    private void OpenSettings()
    {
        _state.Navigate("settings");
        ShowFromTray();
    }

    private async void OnRawTelemetryLocateRequested()
    {
        var dialog = new Microsoft.Win32.OpenFileDialog
        {
            Title = "Locate original iRacing telemetry",
            Filter = "iRacing telemetry (*.ibt)|*.ibt",
            Multiselect = false,
            CheckFileExists = true,
            InitialDirectory = Directory.Exists(_state.Settings.IRacingRoot) ? _state.Settings.IRacingRoot : WindowsKnownFolders.Documents
        };
        if (dialog.ShowDialog(this) == true)
            await _state.RegisterLocatedTelemetryAsync(dialog.FileName);
    }

    private void OnClosing(object? sender, CancelEventArgs e)
    {
        if (_exitRequested) return;
        e.Cancel = true;
        HideToTray(true);
    }

    private void HideToTray(bool explain)
    {
        _state.SetPrimaryUiVisible(false);
        Hide();
        if (explain && !_trayNoticeShown)
        {
            _trayNoticeShown = true;
            _trayIcon.BalloonTipTitle = "iRacing Coach is still running";
            _trayIcon.BalloonTipText = "Live telemetry and approved background work continue. Use Exit from the tray menu to stop the app.";
            _trayIcon.ShowBalloonTip(4000);
        }
    }

    private void OnMonitorVisibilityRequested(bool visible)
    {
        if (_disposed || _exitRequested) return;
        if (!Dispatcher.CheckAccess()) { Dispatcher.BeginInvoke(() => OnMonitorVisibilityRequested(visible)); return; }
        if (visible) _liveMonitor.ShowMonitor(); else _liveMonitor.HideMonitor();
        RefreshTrayMenu();
    }

    private void OnStateChanged()
    {
        if (_disposed || _exitRequested || Dispatcher.HasShutdownStarted) return;
        _ = Dispatcher.BeginInvoke(RefreshTrayMenu);
    }

    private void OnLiveTelemetryChanged()
    {
        if (_disposed || _exitRequested) return;
        var now = DateTimeOffset.UtcNow;
        if (now - _lastTrayLiveUpdate < TimeSpan.FromSeconds(1)) return;
        _lastTrayLiveUpdate = now;
        if (!Dispatcher.HasShutdownStarted) _ = Dispatcher.BeginInvoke(RefreshTrayMenu);
    }

    private void RefreshTrayMenu()
    {
        if (_disposed || _exitRequested) return;
        var snapshot = _state.LiveState.Snapshot;
        _monitorItem.Text = _state.Settings.LiveMonitor.Visible ? "Hide Live Monitor" : "Show Live Monitor";
        _pauseItem.Text = _state.LiveCoachingPaused ? "Resume live coaching" : "Pause live coaching";
        _pauseItem.Visible = snapshot.Connected;
        _statusItem.Text = snapshot.Connected
            ? $"Live telemetry: connected · {snapshot.Flag}{(snapshot.Lap.HasValue ? $" · L{snapshot.Lap}" : string.Empty)}"
            : "Live telemetry: waiting for iRacing";
        var tooltip = snapshot.Connected
            ? $"iRacing Coach · Connected · {(snapshot.Lap.HasValue ? $"Race L{snapshot.Lap}" : snapshot.Flag)}"
            : "iRacing Coach · Waiting for iRacing";
        _trayIcon.Text = tooltip.Length <= 127 ? tooltip : tooltip[..127];
    }

    private void RequestExit()
    {
        if (_exitRequested) return;
        _exitRequested = true;
        HideForApplicationExit();
        if (Dispatcher.HasShutdownStarted) ExitRequested?.Invoke();
        else _ = Dispatcher.BeginInvoke(DispatcherPriority.Send, new Action(() => ExitRequested?.Invoke()));
    }

    public void DisposeApplication()
    {
        if (_disposed) return;
        _disposed = true;
        _exitRequested = true;
        HideForApplicationExit();
        _state.Changed -= OnStateChanged;
        _state.LiveTelemetryChanged -= OnLiveTelemetryChanged;
        _state.LiveMonitorVisibilityRequested -= OnMonitorVisibilityRequested;
        _state.RawTelemetryLocateRequested -= OnRawTelemetryLocateRequested;
        if (_windowSource is not null)
        {
            TryCleanup(() => _windowSource.RemoveHook(WindowMessageHook), "remove window hook");
            TryCleanup(() => _ = UnregisterHotKey(_windowSource.Handle, LiveMonitorHotkeyId), "unregister Live Monitor hotkey");
        }
        TryCleanup(_liveMonitor.Close, "close Live Monitor");
        TryCleanup(Close, "close main window");
        TryCleanup(_trayIcon.Dispose, "dispose tray icon");
        TryCleanup(_state.Dispose, "stop application services");
        TryCleanup(_services.Dispose, "dispose application container");
    }

    private void HideForApplicationExit()
    {
        TryCleanup(Hide, "hide main window");
        TryCleanup(_liveMonitor.Hide, "hide Live Monitor");
        TryCleanup(() => _trayIcon.Visible = false, "hide tray icon");
    }

    private static void TryCleanup(Action action, string operation)
    {
        try { action(); }
        catch (Exception error) { Trace.WriteLine($"Could not {operation} during application exit: {error}"); }
    }

    private void ApplyDarkTitleBar()
    {
        var handle = new WindowInteropHelper(this).Handle;
        var enabled = 1;
        if (DwmSetWindowAttribute(handle, 20, ref enabled, sizeof(int)) != 0) _ = DwmSetWindowAttribute(handle, 19, ref enabled, sizeof(int));
        var caption = ColorRef(23, 25, 28); var text = ColorRef(232, 233, 231); var border = ColorRef(48, 53, 59);
        _ = DwmSetWindowAttribute(handle, 35, ref caption, sizeof(int)); _ = DwmSetWindowAttribute(handle, 36, ref text, sizeof(int)); _ = DwmSetWindowAttribute(handle, 34, ref border, sizeof(int));
    }

    private static int ColorRef(byte red, byte green, byte blue) => red | (green << 8) | (blue << 16);
    [DllImport("dwmapi.dll")] private static extern int DwmSetWindowAttribute(IntPtr window, int attribute, ref int value, int valueSize);
    [DllImport("user32.dll", SetLastError = true)] private static extern bool RegisterHotKey(IntPtr window, int id, uint modifiers, uint virtualKey);
    [DllImport("user32.dll", SetLastError = true)] private static extern bool UnregisterHotKey(IntPtr window, int id);

    private IntPtr WindowMessageHook(IntPtr hwnd, int message, IntPtr wParam, IntPtr lParam, ref bool handled)
    {
        if (message == WmHotkey && wParam.ToInt32() == LiveMonitorHotkeyId)
        {
            _state.ToggleLiveMonitor();
            handled = true;
        }
        return IntPtr.Zero;
    }

    private bool RegisterGlobalHotkey()
    {
        if (_windowSource is null) return true;
        _ = UnregisterHotKey(_windowSource.Handle, LiveMonitorHotkeyId);
        var configured = _state.Settings.LiveMonitor.GlobalHotkey.Trim();
        if (configured.Length == 0) return true;
        var parts = configured.Split('+', StringSplitOptions.TrimEntries | StringSplitOptions.RemoveEmptyEntries);
        if (parts.Length < 2) return false;
        uint modifiers = 0x4000;
        foreach (var part in parts[..^1])
        {
            if (part.Equals("ctrl", StringComparison.OrdinalIgnoreCase) || part.Equals("control", StringComparison.OrdinalIgnoreCase)) modifiers |= 0x0002;
            else if (part.Equals("shift", StringComparison.OrdinalIgnoreCase)) modifiers |= 0x0004;
            else if (part.Equals("alt", StringComparison.OrdinalIgnoreCase)) modifiers |= 0x0001;
            else return false;
        }
        var keyName = parts[^1].ToUpperInvariant();
        uint key;
        if (keyName.Length == 1 && char.IsLetterOrDigit(keyName[0])) key = keyName[0];
        else if (keyName.StartsWith('F') && int.TryParse(keyName[1..], out var function) && function is >= 1 and <= 24) key = (uint)(0x70 + function - 1);
        else return false;
        return RegisterHotKey(_windowSource.Handle, LiveMonitorHotkeyId, modifiers, key);
    }

    private static bool IsWebView2Available()
    {
        try { return !string.IsNullOrWhiteSpace(CoreWebView2Environment.GetAvailableBrowserVersionString()); }
        catch (WebView2RuntimeNotFoundException) { return false; }
    }

    private static UIElement BuildRuntimeNotice() => new Border
    {
        Background = new SolidColorBrush(System.Windows.Media.Color.FromRgb(23, 25, 28)), Padding = new Thickness(48),
        Child = new StackPanel
        {
            MaxWidth = 560, VerticalAlignment = System.Windows.VerticalAlignment.Center, HorizontalAlignment = System.Windows.HorizontalAlignment.Center,
            Children =
            {
                new TextBlock { Text = "Microsoft Edge WebView2 Runtime is required", Foreground = new SolidColorBrush(System.Windows.Media.Color.FromRgb(231, 233, 237)), FontSize = 26, FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 0, 0, 14) },
                new TextBlock { Text = "Install Microsoft Edge WebView2 Runtime, then reopen iRacing Coach. Your local data and settings are unaffected.", Foreground = new SolidColorBrush(System.Windows.Media.Color.FromRgb(178, 184, 194)), FontSize = 15, TextWrapping = TextWrapping.Wrap, LineHeight = 24 }
            }
        }
    };
}
