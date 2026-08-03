using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Threading;
using Forms = System.Windows.Forms;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.App;

public partial class LiveMonitorWindow : Window
{
    private const int GwlExStyle = -20;
    private const int WsExTransparent = 0x00000020;
    private readonly CompanionState _state;
    private readonly DispatcherTimer _saveTimer;
    private readonly DispatcherTimer _renderTimer;
    private bool _restoring;
    private int _renderDirty;
    private bool? _appliedClickThrough;

    public LiveMonitorWindow(CompanionState state)
    {
        _state = state;
        InitializeComponent();
        if (_state.QaFixtureMode)
        {
            Title = "iRacing Coach Live Monitor · QA Fixture";
            QaFixtureText.Visibility = Visibility.Visible;
        }
        _saveTimer = new DispatcherTimer(TimeSpan.FromMilliseconds(600), DispatcherPriority.Background, (_, _) => SavePlacement(), Dispatcher);
        _saveTimer.Stop();
        _renderTimer = new DispatcherTimer(TimeSpan.FromMilliseconds(200), DispatcherPriority.Render, (_, _) =>
        {
            if (Interlocked.Exchange(ref _renderDirty, 0) != 0 && IsVisible) Render();
        }, Dispatcher);
        _renderTimer.Start();
        Loaded += OnLoaded;
        SourceInitialized += (_, _) => ApplyWindowTreatment();
        LocationChanged += (_, _) => ScheduleSave();
        SizeChanged += (_, _) => ScheduleSave();
        _state.Changed += OnCompanionStateChanged;
        _state.LiveTelemetryChanged += OnCompanionStateChanged;
        Closed += (_, _) =>
        {
            _renderTimer.Stop();
            _state.Changed -= OnCompanionStateChanged;
            _state.LiveTelemetryChanged -= OnCompanionStateChanged;
        };
    }

    public void ShowMonitor()
    {
        if (!IsVisible) Show();
        Topmost = true;
        Activate();
        Render();
    }

    public void HideMonitor()
    {
        SavePlacement();
        Hide();
    }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        RestorePlacement();
        Render();
    }

    private void OnCompanionStateChanged()
    {
        if (!Dispatcher.HasShutdownStarted) Interlocked.Exchange(ref _renderDirty, 1);
    }

    private void Render()
    {
        var snapshot = _state.LiveState.Snapshot;
        var layout = _state.Settings.LiveMonitor;
        ConnectionDot.Fill = Brush(snapshot.Connected ? "#70B995" : "#6B7480");
        AgeText.Text = snapshot.Connected ? $"{snapshot.DataAge.TotalMilliseconds:0} ms" : "waiting";
        FlagText.Text = snapshot.Flag;
        FlagText.Foreground = Brush(snapshot.Flag.Contains("BLACK", StringComparison.OrdinalIgnoreCase) ? "#E98282" : snapshot.Flag.Contains("CAUTION", StringComparison.OrdinalIgnoreCase) ? "#E0B85C" : snapshot.Connected ? "#E7E9ED" : "#929BA7");
        LapText.Text = snapshot.Lap.HasValue ? snapshot.LapsRemaining.HasValue ? $"L{snapshot.Lap} / {snapshot.Lap.Value + snapshot.LapsRemaining.Value}" : $"L{snapshot.Lap}" : string.Empty;
        PositionText.Visibility = snapshot.OverallPosition is > 0 ? Visibility.Visible : Visibility.Collapsed;
        PositionText.Text = snapshot.OverallPosition is > 0 ? Position(snapshot.OverallPosition) : string.Empty;
        ClassPositionText.Text = snapshot.ClassPosition is > 0 && snapshot.ClassPosition != snapshot.OverallPosition ? $"Class {Position(snapshot.ClassPosition)}" : string.Empty;
        SetGap(LeaderGapPanel, LeaderGapText, LeaderTrendText, snapshot.LeaderGap);
        SetGap(AheadGapPanel, AheadGapText, AheadTrendText, snapshot.AheadGap);
        SetGap(BehindGapPanel, BehindGapText, BehindTrendText, snapshot.BehindGap);
        GapPanel.Visibility = snapshot.Connected && new[] { snapshot.LeaderGap, snapshot.AheadGap, snapshot.BehindGap }.Any(gap => gap.Seconds.HasValue) ? Visibility.Visible : Visibility.Collapsed;
        LastLapPanel.Visibility = snapshot.LastLapSeconds is > 0 ? Visibility.Visible : Visibility.Collapsed;
        LastLapText.Text = snapshot.LastLapSeconds is > 0 ? LapTime(snapshot.LastLapSeconds) : string.Empty;
        TargetPanel.Visibility = snapshot.PaceTarget.MinimumSeconds is > 0 && snapshot.PaceTarget.MaximumSeconds is > 0 ? Visibility.Visible : Visibility.Collapsed;
        TargetText.Text = TargetPanel.Visibility == Visibility.Visible ? $"{LapTime(snapshot.PaceTarget.MinimumSeconds)}–{LapTime(snapshot.PaceTarget.MaximumSeconds)}" : string.Empty;
        var hasPitWindow = snapshot.Pit.WindowOpensInLaps.HasValue && snapshot.Pit.WindowClosesInLaps.HasValue;
        PitPanel.Visibility = hasPitWindow || snapshot.Pit.FuelHardLimitLaps.HasValue ? Visibility.Visible : Visibility.Collapsed;
        PitText.Text = hasPitWindow ? $"Pit {snapshot.Pit.WindowOpensInLaps}–{snapshot.Pit.WindowClosesInLaps} laps" : snapshot.Pit.FuelHardLimitLaps.HasValue ? $"Fuel {snapshot.Pit.FuelHardLimitLaps} laps" : string.Empty;
        TimingPanel.Visibility = snapshot.Connected && new[] { LastLapPanel, TargetPanel, PitPanel }.Any(panel => panel.Visibility == Visibility.Visible) ? Visibility.Visible : Visibility.Collapsed;
        CueText.Text = snapshot.PrimaryCue.Message;
        CueBasisText.Text = snapshot.PrimaryCue.Basis;
        CueBorder.BorderBrush = Brush(snapshot.PrimaryCue.Priority >= LiveCuePriority.Critical ? "#D46A6A" : snapshot.PrimaryCue.Priority >= LiveCuePriority.Traffic ? "#D4AB58" : "#A3ADB8");
        LeaderLastPanel.Visibility = snapshot.Connected && snapshot.LeaderLastLapSeconds is > 0 ? Visibility.Visible : Visibility.Collapsed;
        LeaderLastText.Text = snapshot.LeaderLastLapSeconds is > 0 ? LapTime(snapshot.LeaderLastLapSeconds) : string.Empty;
        PaceDeltaPanel.Visibility = snapshot.Connected && snapshot.LastLapPaceDifferenceSeconds.HasValue ? Visibility.Visible : Visibility.Collapsed;
        PaceDeltaText.Text = snapshot.LastLapPaceDifferenceSeconds.HasValue ? $"{snapshot.LastLapPaceDifferenceSeconds:+0.000;-0.000;0.000} s" : string.Empty;
        TirePhasePanel.Visibility = snapshot.Connected && _state.IsCapabilityVisible(ProductCapability.LiveTirePhase) ? Visibility.Visible : Visibility.Collapsed;
        TirePhaseText.Text = TirePhasePanel.Visibility == Visibility.Visible ? snapshot.TirePhase : string.Empty;
        WeatherPanel.Visibility = snapshot.Connected && (snapshot.TrackTemperatureC.HasValue || snapshot.AirTemperatureC.HasValue) ? Visibility.Visible : Visibility.Collapsed;
        WeatherText.Text = Weather(snapshot.TrackTemperatureC, snapshot.AirTemperatureC);
        ExpandedDetails.Visibility = new[] { LeaderLastPanel, PaceDeltaPanel, TirePhasePanel, WeatherPanel }.Any(panel => panel.Visibility == Visibility.Visible) ? Visibility.Visible : Visibility.Collapsed;
        ExpandedPanel.Visibility = layout.Mode == LiveMonitorMode.Expanded ? Visibility.Visible : Visibility.Collapsed;
        ModeButton.Content = layout.Mode == LiveMonitorMode.Expanded ? "Compact" : "Expand";
        LockButton.Content = layout.PositionLocked ? "Unlock" : "Lock";
        ResizeMode = layout.PositionLocked ? ResizeMode.NoResize : ResizeMode.CanResizeWithGrip;
        ChromePanel.Visibility = layout.ChromeVisible ? Visibility.Visible : Visibility.Collapsed;
        MinimalHandle.Visibility = layout.ChromeVisible ? Visibility.Collapsed : Visibility.Visible;
        ClickThroughButton.Content = layout.ClickThrough ? "Click-through on" : "Click-through off";
        if (Math.Abs(OpacitySlider.Value - layout.Opacity) > .005) OpacitySlider.Value = layout.Opacity;
        Opacity = Math.Clamp(layout.Opacity, .55, 1);
        if (_appliedClickThrough != layout.ClickThrough)
        {
            ApplyClickThrough(layout.ClickThrough);
            _appliedClickThrough = layout.ClickThrough;
        }
    }

    private void DragHandle_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        if (e.LeftButton == MouseButtonState.Pressed && !_state.Settings.LiveMonitor.PositionLocked) DragMove();
    }
    private void ModeButton_Click(object sender, RoutedEventArgs e) { var layout = _state.Settings.LiveMonitor; layout.Mode = layout.Mode == LiveMonitorMode.Compact ? LiveMonitorMode.Expanded : LiveMonitorMode.Compact; Height = layout.Mode == LiveMonitorMode.Expanded ? Math.Max(390, Height) : 275; _state.SaveLiveMonitorPreferences(); Render(); }
    private void LockButton_Click(object sender, RoutedEventArgs e) { _state.Settings.LiveMonitor.PositionLocked = !_state.Settings.LiveMonitor.PositionLocked; _state.SaveLiveMonitorPreferences(); Render(); }
    private void ChromeButton_Click(object sender, RoutedEventArgs e) { _state.Settings.LiveMonitor.ChromeVisible = false; _state.SaveLiveMonitorPreferences(); Render(); }
    private void ShowChromeButton_Click(object sender, RoutedEventArgs e) { _state.Settings.LiveMonitor.ChromeVisible = true; _state.SaveLiveMonitorPreferences(); Render(); }
    private void HideButton_Click(object sender, RoutedEventArgs e) => _state.SetLiveMonitorVisible(false);
    private void ClickThroughButton_Click(object sender, RoutedEventArgs e) { _state.Settings.LiveMonitor.ClickThrough = !_state.Settings.LiveMonitor.ClickThrough; _state.SaveLiveMonitorPreferences(); Render(); }
    private void ResetButton_Click(object sender, RoutedEventArgs e) { ResetPlacement(); _state.SaveLiveMonitorPreferences(); }
    private void OpacitySlider_ValueChanged(object sender, RoutedPropertyChangedEventArgs<double> e) { if (!IsLoaded) return; _state.Settings.LiveMonitor.Opacity = e.NewValue; Opacity = e.NewValue; ScheduleSave(); }

    public void DisableClickThrough()
    {
        _state.Settings.LiveMonitor.ClickThrough = false;
        _state.SaveLiveMonitorPreferences();
        Render();
    }

    private void RestorePlacement()
    {
        _restoring = true;
        try
        {
            var layout = _state.Settings.LiveMonitor;
            Width = Math.Clamp(layout.Width, MinWidth, MaxWidth);
            Height = Math.Clamp(layout.Height, MinHeight, MaxHeight);
            Opacity = Math.Clamp(layout.Opacity, .55, 1);
            if (layout.Left.HasValue && layout.Top.HasValue && double.IsFinite(layout.Left.Value) && double.IsFinite(layout.Top.Value) && PlacementIsVisible(layout.Left.Value, layout.Top.Value, Width, Height))
            {
                Left = layout.Left.Value; Top = layout.Top.Value;
            }
            else ResetPlacement();
        }
        finally { _restoring = false; }
    }

    private void ResetPlacement()
    {
        var work = SystemParameters.WorkArea;
        Width = 560; Height = _state.Settings.LiveMonitor.Mode == LiveMonitorMode.Expanded ? 390 : 275;
        Left = Math.Max(work.Left + 24, work.Right - Width - 32); Top = work.Top + 32;
    }

    private static bool PlacementIsVisible(double left, double top, double width, double height)
    {
        var rect = new Rect(left, top, width, height);
        return Forms.Screen.AllScreens.Any(screen => rect.IntersectsWith(new Rect(screen.WorkingArea.Left, screen.WorkingArea.Top, screen.WorkingArea.Width, screen.WorkingArea.Height)));
    }

    private void ScheduleSave()
    {
        if (_restoring || !IsLoaded) return;
        _saveTimer.Stop(); _saveTimer.Start();
    }
    private void SavePlacement()
    {
        _saveTimer.Stop();
        if (!IsLoaded || _restoring) return;
        var layout = _state.Settings.LiveMonitor;
        layout.Left = Left; layout.Top = Top; layout.Width = ActualWidth; layout.Height = ActualHeight; layout.Opacity = Opacity;
        var handle = new WindowInteropHelper(this).Handle;
        if (handle != IntPtr.Zero) layout.MonitorDeviceName = Forms.Screen.FromHandle(handle).DeviceName;
        _state.SaveLiveMonitorPreferences();
    }

    private void ApplyWindowTreatment()
    {
        var handle = new WindowInteropHelper(this).Handle;
        var corner = 2;
        _ = DwmSetWindowAttribute(handle, 33, ref corner, sizeof(int));
        ApplyClickThrough(_state.Settings.LiveMonitor.ClickThrough);
    }
    private void ApplyClickThrough(bool enabled)
    {
        var handle = new WindowInteropHelper(this).Handle;
        if (handle == IntPtr.Zero) return;
        var style = GetWindowLongPtr(handle, GwlExStyle).ToInt64();
        style = enabled ? style | WsExTransparent : style & ~WsExTransparent;
        _ = SetWindowLongPtr(handle, GwlExStyle, new IntPtr(style));
    }

    private static void SetGap(System.Windows.Controls.StackPanel panel, System.Windows.Controls.TextBlock value, System.Windows.Controls.TextBlock trend, LiveGapState gap) { panel.Visibility = gap.Seconds.HasValue ? Visibility.Visible : Visibility.Collapsed; value.Text = gap.Seconds.HasValue ? $"+{gap.Seconds:0.00}" : string.Empty; trend.Text = gap.Trend is LiveGapTrend.Closing or LiveGapTrend.Growing or LiveGapTrend.Stable ? gap.Trend.ToString() : "Paused"; trend.Foreground = Brush(gap.Trend == LiveGapTrend.Closing ? "#70B995" : gap.Trend == LiveGapTrend.Growing ? "#D4AB58" : "#858E9A"); }
    private static string Position(int? value) => value is > 0 ? $"P{value}" : string.Empty;
    private static string LapTime(double? value) => value is > 0 ? TimeSpan.FromSeconds(value.Value).ToString(value.Value >= 60 ? "m\\:ss\\.fff" : "s\\.fff") : string.Empty;
    private static string Weather(double? track, double? air) => track.HasValue && air.HasValue ? $"{track:0.0}° / {air:0.0}°C" : track.HasValue ? $"Track {track:0.0}°C" : air.HasValue ? $"Air {air:0.0}°C" : string.Empty;
    private static SolidColorBrush Brush(string value) => new((System.Windows.Media.Color)System.Windows.Media.ColorConverter.ConvertFromString(value));

    protected override void OnClosed(EventArgs e) { _state.Changed -= OnCompanionStateChanged; _saveTimer.Stop(); base.OnClosed(e); }
    [DllImport("dwmapi.dll")] private static extern int DwmSetWindowAttribute(IntPtr handle, int attribute, ref int value, int size);
    [DllImport("user32.dll", EntryPoint = "GetWindowLongPtrW")] private static extern IntPtr GetWindowLongPtr64(IntPtr handle, int index);
    [DllImport("user32.dll", EntryPoint = "GetWindowLongW")] private static extern IntPtr GetWindowLongPtr32(IntPtr handle, int index);
    private static IntPtr GetWindowLongPtr(IntPtr handle, int index) => IntPtr.Size == 8 ? GetWindowLongPtr64(handle, index) : GetWindowLongPtr32(handle, index);
    [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW")] private static extern IntPtr SetWindowLongPtr64(IntPtr handle, int index, IntPtr value);
    [DllImport("user32.dll", EntryPoint = "SetWindowLongW")] private static extern IntPtr SetWindowLongPtr32(IntPtr handle, int index, IntPtr value);
    private static IntPtr SetWindowLongPtr(IntPtr handle, int index, IntPtr value) => IntPtr.Size == 8 ? SetWindowLongPtr64(handle, index, value) : SetWindowLongPtr32(handle, index, value);
}
