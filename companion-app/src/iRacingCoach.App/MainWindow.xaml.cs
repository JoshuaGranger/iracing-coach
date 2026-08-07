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
        _state = new CompanionState();
        var state = _state;
        services.AddSingleton(state);
#if DEBUG
        services.AddBlazorWebViewDeveloperTools();
#endif
        _services = services.BuildServiceProvider();
        Resources.Add("services", _services);
        InitializeComponent();
        _liveMonitor = new LiveMonitorWindow(state);

        _monitorItem = new Forms.ToolStripMenuItem("Show telemetry popout", null, (_, _) => _state.ToggleLiveMonitor());
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
            if (!StartupRegistration.Apply(settings.LaunchAtSignIn))
                _state.Notify("Settings saved, but Windows sign-in startup could not be updated.");
            if (!RegisterGlobalHotkey())
                _state.Notify("Settings saved, but that telemetry-popout hotkey is invalid or already in use.");
        };
        _ = StartupRegistration.Apply(_state.Settings.LaunchAtSignIn);

        if (Environment.GetCommandLineArgs().Any(argument => string.Equals(argument, "--minimized", StringComparison.OrdinalIgnoreCase)))
            Loaded += (_, _) => HideToTray(false);
        if (!IsWebView2Available()) Content = BuildRuntimeNotice();
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

    private void OnMonitorVisibilityRequested(bool visible, bool activate)
    {
        if (_disposed || _exitRequested) return;
        if (!Dispatcher.CheckAccess()) { Dispatcher.BeginInvoke(() => OnMonitorVisibilityRequested(visible, activate)); return; }
        if (visible && !_state.LiveMonitorVisible) return;
        if (visible) _liveMonitor.ShowMonitor(activate); else _liveMonitor.HideMonitor();
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
        _monitorItem.Text = _state.Settings.LiveMonitor.Visible ? "Hide telemetry popout" : "Show telemetry popout";
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
        // Arm the process-level deadline on the WinForms callback thread itself.
        // This guarantees Exit even if the WPF dispatcher cannot accept the handoff.
        App.ArmExitDeadline();
        // NotifyIcon callbacks run on the Windows Forms message loop. Move the entire
        // shutdown handoff to WPF before touching windows or application lifetime.
        if (!Dispatcher.CheckAccess())
        {
            if (!Dispatcher.HasShutdownStarted)
                _ = Dispatcher.BeginInvoke(DispatcherPriority.Send, new Action(RequestExit));
            return;
        }
        if (_exitRequested) return;
        _exitRequested = true;
        // App arms the process-level deadline synchronously in this callback
        // before DisposeApplication hides or closes any UI.
        ExitRequested?.Invoke();
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
        TryCleanup(_liveMonitor.CloseMonitor, "close Live Monitor");
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
