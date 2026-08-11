using System.ComponentModel;
using System.Diagnostics;
using System.Globalization;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Shapes;
using System.Windows.Threading;
using Forms = System.Windows.Forms;
using Brush = System.Windows.Media.Brush;
using Button = System.Windows.Controls.Button;
using ComboBox = System.Windows.Controls.ComboBox;
using FontFamily = System.Windows.Media.FontFamily;
using HAlignment = System.Windows.HorizontalAlignment;
using Point = System.Windows.Point;
using Size = System.Windows.Size;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.App;

public partial class LiveMonitorWindow : Window
{
    private const double GridViewportWidth = 540;
    private const double GridViewportHeight = 360;
    private const double MinimumFontSize = 11.67;
    private readonly CompanionState _state;
    private readonly DispatcherTimer _saveTimer;
    private readonly List<TileVisual> _tileVisuals = [];
    private readonly Dictionary<string, (double Minimum, double Maximum)> _trendRanges = new(StringComparer.Ordinal);
    private bool _restoring;
    private bool _allowClose;
    private bool _updatingControls;
    private bool _scaleSettingsOpen;
    private int _renderDirty;
    private int _editorCheckQueued;
    private bool _compositionRenderingAttached;
    private bool _trendAnimationActive;
    private bool _lastRenderedConnected;
    private int? _lastRenderedLap;
    private bool _trendSeedPending = true;
    private double _cellWidth = 180;
    private double _cellHeight = 180;
    private string _lastKnownEditorSignature;
    private string _appliedThemeColor = string.Empty;

    public LiveMonitorWindow(CompanionState state)
    {
        _state = state;
        _lastKnownEditorSignature = VisualSignature();
        InitializeComponent();
        SizeToContent = SizeToContent.WidthAndHeight;
        _saveTimer = new DispatcherTimer(TimeSpan.FromMilliseconds(500), DispatcherPriority.Background, (_, _) => SavePlacement(), Dispatcher);
        _saveTimer.Stop();
        Loaded += OnLoaded;
        Closing += OnClosing;
        SourceInitialized += (_, _) => ApplyWindowTreatment();
        LocationChanged += (_, _) => ScheduleSave();
        _state.Changed += OnCompanionStateChanged;
        _state.LiveTelemetryChanged += OnLiveTelemetryChanged;
        Closed += (_, _) =>
        {
            DetachCompositionRendering();
            _saveTimer.Stop();
            _state.Changed -= OnCompanionStateChanged;
            _state.LiveTelemetryChanged -= OnLiveTelemetryChanged;
        };
    }

    public void ShowMonitor(bool activate = true)
    {
        AttachCompositionRendering();
        ShowActivated = activate;
        if (!IsVisible) Show();
        Topmost = true;
        if (activate) Activate();
        RenderAll();
    }

    public void HideMonitor()
    {
        SavePlacement();
        DetachCompositionRendering();
        Hide();
    }

    public void CloseMonitor()
    {
        _allowClose = true;
        Close();
    }

    private void OnClosing(object? sender, CancelEventArgs e)
    {
        if (_allowClose) return;
        e.Cancel = true;
        _state.SetLiveMonitorVisible(false);
    }

    private LiveMonitorLayout Preferences => _state.Settings.LiveMonitor;
    private LiveMonitorLayoutChoice ActiveChoice => LiveMonitorLayouts.Active(Preferences);

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        AttachCompositionRendering();
        RenderAll();
        Dispatcher.BeginInvoke(RestorePlacement, DispatcherPriority.Loaded);
    }

    private void OnCompanionStateChanged()
    {
        Interlocked.Exchange(ref _renderDirty, 1);
        QueueExternalEditorCheck();
    }

    private void OnLiveTelemetryChanged() => Interlocked.Exchange(ref _renderDirty, 1);

    private void AttachCompositionRendering()
    {
        if (_compositionRenderingAttached) return;
        CompositionTarget.Rendering += OnCompositionRendering;
        _compositionRenderingAttached = true;
    }

    private void DetachCompositionRendering()
    {
        if (!_compositionRenderingAttached) return;
        CompositionTarget.Rendering -= OnCompositionRendering;
        _compositionRenderingAttached = false;
    }

    private void OnCompositionRendering(object? sender, EventArgs e)
    {
        if (!IsVisible || _scaleSettingsOpen) return;
        var sourceDirty = Interlocked.Exchange(ref _renderDirty, 0) != 0;
        if (sourceDirty) RenderTelemetry();
        if (!_trendAnimationActive && !sourceDirty) return;

        var keepAnimating = false;
        var frameTimestamp = Stopwatch.GetTimestamp();
        var motionEnabled = _state.LiveState.Snapshot.Connected && !_state.Settings.UseReducedMotion;
        foreach (var visual in _tileVisuals)
            keepAnimating |= visual.AnimateTrend(frameTimestamp, motionEnabled);
        _trendAnimationActive = keepAnimating;
    }

    private void QueueExternalEditorCheck()
    {
        if (Dispatcher.HasShutdownStarted || Interlocked.Exchange(ref _editorCheckQueued, 1) != 0) return;
        _ = Dispatcher.BeginInvoke(() =>
        {
            Interlocked.Exchange(ref _editorCheckQueued, 0);
            CheckForExternalEditorChange();
        }, DispatcherPriority.DataBind);
    }

    private void CheckForExternalEditorChange()
    {
        var signature = VisualSignature();
        if (string.Equals(signature, _lastKnownEditorSignature, StringComparison.Ordinal)) return;
        _lastKnownEditorSignature = signature;
        _scaleSettingsOpen = false;
        _trendRanges.Clear();
        RenderAll();
    }

    private void RenderAll()
    {
        _updatingControls = true;
        try
        {
            ApplyTheme();
            RefreshLayoutSelector();
            ApplyScale();
            RenderSurfaceState();
            RenderGrid();
            RenderTelemetry();
        }
        finally { _updatingControls = false; }
    }

    private string VisualSignature() => $"{LiveMonitorLayouts.EditorSignature(Preferences)}|theme:{ThemeColors.Normalize(_state.Settings.ThemeColor)}:{ThemeColors.NormalizeCustomHex(_state.Settings.CustomThemeColor)}";

    private void ApplyTheme()
    {
        var theme = ThemeColors.Get(_state.Settings.ThemeColor, _state.Settings.CustomThemeColor);
        var signature = $"{theme.Id}:{theme.Accent}";
        if (string.Equals(_appliedThemeColor, signature, StringComparison.Ordinal)) return;
        Resources["MonitorAccentBrush"] = ThemeBrush(theme.Accent);
        Resources["MonitorAccentFillBrush"] = ThemeBrush(theme.Fill);
        Resources["MonitorAccentSubtleBrush"] = ThemeBrush(theme.Subtle);
        Resources["MonitorFocusBrush"] = ThemeBrush(theme.Focus);
        _appliedThemeColor = signature;
    }

    private static SolidColorBrush ThemeBrush(string value) =>
        new((System.Windows.Media.Color)System.Windows.Media.ColorConverter.ConvertFromString(value));

    private void RenderTelemetry()
    {
        var liveState = _state.LiveState;
        var snapshot = liveState.Snapshot;
        if (!_lastRenderedConnected && snapshot.Connected) _trendSeedPending = true;
        if (_lastRenderedConnected && !snapshot.Connected)
        {
            _trendRanges.Clear();
            foreach (var visual in _tileVisuals) visual.ResetTrend();
        }
        else if (snapshot.Connected && _lastRenderedLap.HasValue && snapshot.Lap.HasValue && snapshot.Lap.Value < _lastRenderedLap.Value)
        {
            _trendRanges.Clear();
        }
        _lastRenderedConnected = snapshot.Connected;
        _lastRenderedLap = snapshot.Connected ? snapshot.Lap : null;
        ConnectionDot.Fill = Resource<Brush>(snapshot.Connected ? "MonitorGreenBrush" : "UnavailableBrush");
        var connectionLabel = snapshot.Connected ? $"{snapshot.Flag}, lap {snapshot.Lap}" : "Waiting for iRacing";
        ConnectionDot.ToolTip = connectionLabel;
        AutomationProperties.SetName(ConnectionDot, connectionLabel);
        var lightweightState = liveState.History is { Count: > 0 } ? liveState with { History = [] } : liveState;
        var seedTrends = _trendSeedPending && snapshot.Connected;
        foreach (var visual in _tileVisuals)
        {
            var readingState = seedTrends && visual.UsesTrend ? liveState : lightweightState;
            var reading = LiveTelemetryCatalog.Read(visual.Tile.MetricId, readingState, visual.Tile.Unit, visual.Tile.Precision, visual.Tile.TrendDuration, includeTrend: false);
            visual.Update(reading, liveState);
        }
        _trendAnimationActive = snapshot.Connected && !_state.Settings.UseReducedMotion && _tileVisuals.Any(visual => visual.UsesTrend);
        if (seedTrends) _trendSeedPending = false;
    }

    private void RenderGrid()
    {
        var layout = ActiveChoice.Layout;
        var rows = Math.Max(1, layout.Rows);
        var columns = Math.Max(1, layout.Columns);
        _trendSeedPending = true;
        _tileVisuals.Clear();
        TileGrid.Children.Clear();
        TileGrid.RowDefinitions.Clear();
        TileGrid.ColumnDefinitions.Clear();
        TileGrid.Width = GridViewportWidth;
        TileGrid.Height = GridViewportHeight;
        _cellWidth = GridViewportWidth / columns;
        _cellHeight = GridViewportHeight / rows;
        for (var row = 0; row < rows; row++) TileGrid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        for (var column = 0; column < columns; column++) TileGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });

        foreach (var tile in layout.Tiles.OrderBy(item => item.Row).ThenBy(item => item.Column))
        {
            if (!LiveTelemetryCatalog.TryGet(tile.MetricId, out var definition)) continue;
            var visual = BuildTile(tile, definition);
            Grid.SetRow(visual.Root, tile.Row);
            Grid.SetColumn(visual.Root, tile.Column);
            Grid.SetRowSpan(visual.Root, tile.RowSpan);
            Grid.SetColumnSpan(visual.Root, tile.ColumnSpan);
            TileGrid.Children.Add(visual.Root);
            _tileVisuals.Add(visual);
        }

        if (layout.Tiles.Count == 0)
        {
            var empty = new TextBlock
            {
                Text = "Empty dashboard",
                Foreground = Resource<Brush>("TextMutedBrush"),
                FontSize = 14,
                HorizontalAlignment = HAlignment.Center,
                VerticalAlignment = VerticalAlignment.Center
            };
            Grid.SetRowSpan(empty, rows);
            Grid.SetColumnSpan(empty, columns);
            TileGrid.Children.Add(empty);
        }
    }

    private TileVisual BuildTile(LiveMonitorTile tile, LiveTelemetryMetricDefinition definition)
    {
        var tileWidth = Math.Max(1, tile.ColumnSpan * _cellWidth);
        var tileHeight = Math.Max(1, tile.RowSpan * _cellHeight);
        var compact = tileWidth < 132 || tileHeight < 116;
        var dense = tileWidth < 88 || tileHeight < 64;
        var padding = dense ? 2d : compact ? 7d : 11d;
        var margin = dense ? 1d : 4d;
        var border = new Border
        {
            Margin = new Thickness(margin),
            Padding = new Thickness(padding),
            CornerRadius = new CornerRadius(dense ? 3 : 7),
            ClipToBounds = true,
            Background = Resource<Brush>("MonitorSurfaceBrush"),
            BorderBrush = Resource<Brush>("MonitorBorderBrush"),
            BorderThickness = new Thickness(1),
            Focusable = false
        };

        var root = new Grid();
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });

        var header = new TextBlock
        {
            Text = definition.Name.ToUpper(CultureInfo.CurrentCulture),
            FontSize = MinimumFontSize,
            FontWeight = FontWeights.SemiBold,
            Foreground = Resource<Brush>("TextSecondaryBrush"),
            TextTrimming = TextTrimming.CharacterEllipsis,
            TextWrapping = TextWrapping.NoWrap,
            Margin = new Thickness(0, 0, 0, compact ? 2 : 5),
            Visibility = dense ? Visibility.Collapsed : Visibility.Visible
        };
        Grid.SetRow(header, 0);
        root.Children.Add(header);

        var contentWidth = Math.Max(12, tileWidth - (padding + margin) * 2);
        var headerReserve = dense ? 0 : compact ? 18 : 24;
        var contentHeight = Math.Max(12, tileHeight - (padding + margin) * 2 - headerReserve);
        var readingVisual = BuildReading(tile, definition, contentWidth, contentHeight);
        var missingVisual = BuildMissing(contentHeight);
        var contentHost = new Grid();
        contentHost.Children.Add(readingVisual.Root);
        contentHost.Children.Add(missingVisual.Root);
        Grid.SetRow(contentHost, 1);
        root.Children.Add(contentHost);

        var secondary = new TextBlock
        {
            FontSize = MinimumFontSize,
            Foreground = Resource<Brush>("TextSecondaryBrush"),
            TextTrimming = TextTrimming.CharacterEllipsis,
            TextWrapping = TextWrapping.NoWrap,
            Margin = new Thickness(0, 3, 0, 0),
            Visibility = Visibility.Collapsed
        };
        Grid.SetRow(secondary, 2);
        root.Children.Add(secondary);

        border.Child = root;
        var lastAutomationName = string.Empty;
        var lastToolTip = string.Empty;
        var wasAvailable = false;
        void Update(LiveTelemetryMetricReading reading, LiveMonitorState liveState)
        {
            var toolTip = reading.Available ? definition.Description : reading.AvailabilityMessage;
            if (!string.Equals(lastToolTip, toolTip, StringComparison.Ordinal))
            {
                border.ToolTip = toolTip;
                lastToolTip = toolTip;
            }

            var automationName = $"{definition.Name}. {(reading.Available ? $"{reading.DisplayValue} {reading.Unit}" : reading.AvailabilityMessage)}";
            if (!string.Equals(lastAutomationName, automationName, StringComparison.Ordinal))
            {
                AutomationProperties.SetName(border, automationName);
                lastAutomationName = automationName;
            }

            readingVisual.Root.Visibility = reading.Available ? Visibility.Visible : Visibility.Collapsed;
            missingVisual.Root.Visibility = reading.Available ? Visibility.Collapsed : Visibility.Visible;
            if (reading.Available)
            {
                readingVisual.Update(reading, liveState);
            }
            else
            {
                if (wasAvailable) readingVisual.ResetTrend();
                missingVisual.Update(reading);
            }
            wasAvailable = reading.Available;

            var showSecondary = !compact && reading.Available && !string.IsNullOrWhiteSpace(reading.SecondaryValue);
            secondary.Visibility = showSecondary ? Visibility.Visible : Visibility.Collapsed;
            if (showSecondary)
            {
                SetText(secondary, reading.SecondaryValue);
                secondary.ToolTip = reading.SecondaryValue;
            }
        }

        return new TileVisual(tile, border, Update, readingVisual.ResetTrend, readingVisual.AnimateTrend, readingVisual.UsesTrend);
    }

    private ReadingVisual BuildReading(LiveMonitorTile tile, LiveTelemetryMetricDefinition definition, double width, double height)
    {
        if (width < 62 || height < 46) return BuildNumber(height);
        return tile.DisplayStyle switch
        {
            LiveMonitorDisplayStyle.Bar => BuildBar(AccentFor(tile, definition), height),
            LiveMonitorDisplayStyle.Gauge => BuildGauge(AccentFor(tile, definition), width, height),
            LiveMonitorDisplayStyle.Trend => BuildTrend(tile, definition, AccentFor(tile, definition), width, height),
            LiveMonitorDisplayStyle.Status => BuildStatus(height),
            _ => BuildNumber(height)
        };
    }

    private ReadingVisual BuildNumber(double height)
    {
        var showUnit = height >= 34;
        var panel = new Grid { VerticalAlignment = VerticalAlignment.Stretch };
        panel.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        panel.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var fitted = FittedValue(Math.Min(32, Math.Max(17, height * .43)), FontWeights.SemiBold);
        panel.Children.Add(fitted.Root);
        var unit = new TextBlock
        {
            FontSize = MinimumFontSize,
            Foreground = Resource<Brush>("TextMutedBrush"),
            TextAlignment = TextAlignment.Center,
            HorizontalAlignment = HAlignment.Center,
            TextTrimming = TextTrimming.CharacterEllipsis
        };
        Grid.SetRow(unit, 1);
        panel.Children.Add(unit);
        void Update(LiveTelemetryMetricReading reading, LiveMonitorState state)
        {
            SetText(fitted.Text, reading.DisplayValue);
            unit.Visibility = showUnit && ShowUnit(reading.Unit) ? Visibility.Visible : Visibility.Collapsed;
            SetText(unit, reading.Unit);
        }
        return new ReadingVisual(panel, Update, static () => { }, static (_, _) => false, false);
    }

    private ReadingVisual BuildStatus(double height)
    {
        var value = new TextBlock
        {
            FontSize = Math.Min(18, Math.Max(12, height * .23)),
            FontWeight = FontWeights.SemiBold,
            VerticalAlignment = VerticalAlignment.Center,
            HorizontalAlignment = HAlignment.Center,
            TextAlignment = TextAlignment.Center,
            TextWrapping = TextWrapping.Wrap,
            TextTrimming = TextTrimming.CharacterEllipsis,
            MaxHeight = Math.Max(22, height)
        };
        return new ReadingVisual(value, (reading, _) => SetText(value, reading.DisplayValue), static () => { }, static (_, _) => false, false);
    }

    private MissingVisual BuildMissing(double height)
    {
        var panel = new Grid { VerticalAlignment = VerticalAlignment.Center };
        var value = new TextBlock { Text = "—", FontSize = Math.Min(22, Math.Max(14, height * .28)), FontWeight = FontWeights.Normal, Foreground = Resource<Brush>("UnavailableBrush"), TextAlignment = TextAlignment.Center };
        panel.Children.Add(value);
        return new MissingVisual(panel, _ => { });
    }

    private ReadingVisual BuildBar(Brush accent, double height)
    {
        var panel = new Grid { VerticalAlignment = VerticalAlignment.Center };
        panel.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        panel.RowDefinitions.Add(new RowDefinition { Height = new GridLength(9) });
        var value = BuildValueLine(Math.Max(20, height - 16));
        panel.Children.Add(value.Root);
        var track = new Border { Background = Resource<Brush>("MonitorSurface2Brush"), CornerRadius = new CornerRadius(4), Margin = new Thickness(0, 6, 0, 0) };
        Grid.SetRow(track, 1);
        var fill = new Border { Background = accent, CornerRadius = new CornerRadius(4), HorizontalAlignment = HAlignment.Left };
        track.Child = fill;
        var fraction = 0d;
        double? stableMinimum = null;
        double? stableMaximum = null;
        void ResizeFill() => fill.Width = Math.Max(0, track.ActualWidth * fraction);
        track.SizeChanged += (_, _) => ResizeFill();
        panel.Children.Add(track);
        void Update(LiveTelemetryMetricReading reading, LiveMonitorState state)
        {
            value.Update(reading);
            if (reading.Minimum.HasValue) stableMinimum = stableMinimum.HasValue ? Math.Min(stableMinimum.Value, reading.Minimum.Value) : reading.Minimum;
            if (reading.Maximum.HasValue) stableMaximum = stableMaximum.HasValue ? Math.Max(stableMaximum.Value, reading.Maximum.Value) : reading.Maximum;
            fraction = reading.NumericValue.HasValue && stableMinimum.HasValue && stableMaximum > stableMinimum
                ? Math.Clamp((reading.NumericValue.Value - stableMinimum.Value) / (stableMaximum!.Value - stableMinimum.Value), 0, 1)
                : 0;
            ResizeFill();
        }
        void Reset() { stableMinimum = null; stableMaximum = null; fraction = 0; ResizeFill(); }
        return new ReadingVisual(panel, Update, Reset, static (_, _) => false, false);
    }

    private ReadingVisual BuildGauge(Brush accent, double width, double height)
    {
        var size = Math.Max(34, Math.Min(108, Math.Min(width, height) - 2));
        var canvas = new Canvas { Width = size, Height = size, HorizontalAlignment = HAlignment.Center, VerticalAlignment = VerticalAlignment.Center };
        var stroke = Math.Clamp(size * .085, 4, 8);
        var inset = stroke + 1;
        canvas.Children.Add(new Ellipse { Width = size - inset * 2, Height = size - inset * 2, Margin = new Thickness(inset), Stroke = Resource<Brush>("MonitorSurface2Brush"), StrokeThickness = stroke });
        var radius = size / 2 - inset;
        var start = new Point(size / 2, inset);
        var figure = new PathFigure { StartPoint = start };
        var segment = new ArcSegment(start, new Size(radius, radius), 0, false, SweepDirection.Clockwise, true);
        figure.Segments.Add(segment);
        var arc = new Path { Data = new PathGeometry([figure]), Stroke = accent, StrokeThickness = stroke, StrokeStartLineCap = PenLineCap.Round, StrokeEndLineCap = PenLineCap.Round, Visibility = Visibility.Collapsed };
        canvas.Children.Add(arc);
        var value = BuildValueLine(size * .62);
        value.Root.Width = size * .78;
        value.Root.Height = size * .68;
        Canvas.SetLeft(value.Root, size * .11);
        Canvas.SetTop(value.Root, size * .18);
        canvas.Children.Add(value.Root);
        double? stableMinimum = null;
        double? stableMaximum = null;
        void Update(LiveTelemetryMetricReading reading, LiveMonitorState state)
        {
            value.Update(reading);
            if (reading.Minimum.HasValue) stableMinimum = stableMinimum.HasValue ? Math.Min(stableMinimum.Value, reading.Minimum.Value) : reading.Minimum;
            if (reading.Maximum.HasValue) stableMaximum = stableMaximum.HasValue ? Math.Max(stableMaximum.Value, reading.Maximum.Value) : reading.Maximum;
            if (!reading.NumericValue.HasValue || !stableMinimum.HasValue || stableMaximum <= stableMinimum)
            {
                arc.Visibility = Visibility.Collapsed;
                return;
            }
            var fraction = Math.Clamp((reading.NumericValue.Value - stableMinimum.Value) / (stableMaximum!.Value - stableMinimum.Value), 0, .9999);
            arc.Visibility = fraction <= .0001 ? Visibility.Collapsed : Visibility.Visible;
            var angle = fraction * Math.PI * 2;
            segment.Point = new Point(size / 2 + Math.Sin(angle) * radius, size / 2 - Math.Cos(angle) * radius);
            segment.Size = new Size(radius, radius);
            segment.IsLargeArc = fraction > .5;
        }
        void Reset() { stableMinimum = null; stableMaximum = null; arc.Visibility = Visibility.Collapsed; }
        return new ReadingVisual(canvas, Update, Reset, static (_, _) => false, false);
    }

    private ValueLineVisual BuildValueLine(double height)
    {
        var grid = new Grid();
        grid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        grid.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var fitted = FittedValue(Math.Min(24, Math.Max(14, height * .48)), FontWeights.SemiBold);
        grid.Children.Add(fitted.Root);
        var unit = new TextBlock
        {
            FontSize = MinimumFontSize,
            Foreground = Resource<Brush>("TextMutedBrush"),
            HorizontalAlignment = HAlignment.Center,
            TextAlignment = TextAlignment.Center,
            TextTrimming = TextTrimming.CharacterEllipsis
        };
        Grid.SetRow(unit, 1);
        grid.Children.Add(unit);
        void Update(LiveTelemetryMetricReading reading)
        {
            SetText(fitted.Text, reading.DisplayValue);
            unit.Visibility = ShowUnit(reading.Unit) ? Visibility.Visible : Visibility.Collapsed;
            SetText(unit, reading.Unit);
        }
        return new ValueLineVisual(grid, Update);
    }

    private static FittedTextVisual FittedValue(double fontSize, FontWeight weight)
    {
        var text = new TextBlock
        {
            FontSize = fontSize,
            FontWeight = weight,
            FontFamily = new FontFamily("Cascadia Mono, Consolas"),
            TextAlignment = TextAlignment.Center,
            TextTrimming = TextTrimming.CharacterEllipsis
        };
        return new FittedTextVisual(new Viewbox
        {
            Stretch = Stretch.Uniform,
            StretchDirection = StretchDirection.DownOnly,
            HorizontalAlignment = HAlignment.Stretch,
            VerticalAlignment = VerticalAlignment.Center,
            Child = text
        }, text);
    }

    private static bool ShowUnit(string? unit) => !string.IsNullOrWhiteSpace(unit) && !string.Equals(unit, "time", StringComparison.OrdinalIgnoreCase);

    private ReadingVisual BuildTrend(LiveMonitorTile tile, LiveTelemetryMetricDefinition definition, Brush accent, double width, double height)
    {
        width = Math.Max(30, width - 12);
        height = Math.Max(24, height - 12);
        var canvas = new Canvas { Width = width, Height = height, ClipToBounds = true, VerticalAlignment = VerticalAlignment.Center, HorizontalAlignment = HAlignment.Center };
        for (var index = 1; index < 4; index++)
        {
            var x = Math.Round(width * index / 4) + .5;
            canvas.Children.Add(new Line { X1 = x, X2 = x, Y1 = 0, Y2 = height, Stroke = Resource<Brush>("MonitorChartGridBrush"), StrokeThickness = 1, Opacity = .32 });
        }
        canvas.Children.Add(new Line { X1 = 0, X2 = width, Y1 = Math.Round(height / 2) + .5, Y2 = Math.Round(height / 2) + .5, Stroke = Resource<Brush>("MonitorChartGridBrush"), StrokeThickness = 1, Opacity = .66 });
        var translation = new TranslateTransform();
        var trace = new Path
        {
            Data = Geometry.Empty,
            Stroke = accent,
            StrokeThickness = 2,
            StrokeLineJoin = PenLineJoin.Round,
            StrokeStartLineCap = PenLineCap.Round,
            StrokeEndLineCap = PenLineCap.Round,
            RenderTransform = translation
        };
        canvas.Children.Add(trace);
        var label = new TextBlock { FontSize = 12, FontWeight = FontWeights.SemiBold, Background = Resource<Brush>("MonitorSurfaceBrush"), TextTrimming = TextTrimming.CharacterEllipsis, MaxWidth = Math.Max(24, width - 8), Padding = new Thickness(3, 1, 4, 2) };
        Canvas.SetLeft(label, 3);
        Canvas.SetBottom(label, 2);
        canvas.Children.Add(label);
        var chartWell = new Border
        {
            Padding = new Thickness(5),
            ClipToBounds = true,
            CornerRadius = new CornerRadius(5),
            Background = Resource<Brush>("MonitorChartBrush"),
            BorderBrush = Resource<Brush>("MonitorBorderBrush"),
            BorderThickness = new Thickness(1),
            HorizontalAlignment = HAlignment.Stretch,
            VerticalAlignment = VerticalAlignment.Stretch,
            Child = canvas
        };
        var buffer = new TrendBuffer(tile.TrendDuration, tile.MetricId, tile.Unit);
        void Update(LiveTelemetryMetricReading reading, LiveMonitorState state)
        {
            SetText(label, $"{reading.DisplayValue} {(ShowUnit(reading.Unit) ? reading.Unit : string.Empty)}".Trim());
            if (!buffer.Update(reading, state)) return;
            translation.X = 0;
            var values = buffer.NumericValues();
            trace.Data = values.Count < 2
                ? Geometry.Empty
                : BuildTrendGeometry(buffer, width, height, TrendRange(tile.Id, values), definition.TrendShape);
        }
        void Reset()
        {
            buffer.Clear();
            trace.Data = Geometry.Empty;
            translation.X = 0;
        }
        bool Animate(long frameTimestamp, bool enabled)
        {
            var motion = buffer.DisplayMotion(frameTimestamp, width, enabled);
            var target = -motion.Shift;
            if (Math.Abs(translation.X - target) >= .01) translation.X = target;
            return motion.Continue;
        }
        return new ReadingVisual(chartWell, Update, Reset, Animate, true);
    }

    private (double Minimum, double Maximum) TrendRange(string tileId, IReadOnlyList<double> values)
    {
        var minimum = values[0];
        var maximum = values[0];
        for (var index = 1; index < values.Count; index++)
        {
            minimum = Math.Min(minimum, values[index]);
            maximum = Math.Max(maximum, values[index]);
        }
        if (Math.Abs(maximum - minimum) < .0001) { minimum -= .5; maximum += .5; }
        var padding = (maximum - minimum) * .08;
        minimum -= padding;
        maximum += padding;
        if (_trendRanges.TryGetValue(tileId, out var stable))
        {
            minimum = Math.Min(minimum, stable.Minimum);
            maximum = Math.Max(maximum, stable.Maximum);
        }
        _trendRanges[tileId] = (minimum, maximum);
        return (minimum, maximum);
    }

    private static Geometry BuildTrendGeometry(
        TrendBuffer buffer,
        double width,
        double height,
        (double Minimum, double Maximum) range,
        LiveMonitorTrendShape shape)
    {
        var span = Math.Max(.0001, range.Maximum - range.Minimum);
        var top = 2d;
        var bottom = Math.Max(top + 1, height - 2);
        var bucketCount = Math.Max(1, (int)Math.Ceiling(width));
        var segments = new List<List<TrendVertex>>();
        var segment = new List<TrendVertex>();
        TrendBucket? bucket = null;
        DateTimeOffset? previousAt = null;
        double? previousX = null;
        var order = 0;

        void FlushBucket()
        {
            if (bucket is not { } current) return;
            var candidates = new[] { current.First, current.Minimum, current.Maximum, current.Last }
                .DistinctBy(candidate => candidate.Order)
                .OrderBy(candidate => candidate.Order);
            segment.AddRange(candidates);
            bucket = null;
        }

        void FinishSegment()
        {
            FlushBucket();
            if (segment.Count > 1) segments.Add(segment);
            segment = [];
            previousX = null;
        }

        foreach (var sample in buffer.Samples)
        {
            order++;
            if (!sample.Value.HasValue || !double.IsFinite(sample.Value.Value))
            {
                FinishSegment();
                previousAt = sample.At;
                continue;
            }

            if (previousAt.HasValue && sample.At - previousAt.Value > buffer.GapThreshold) FinishSegment();
            previousAt = sample.At;
            var x = buffer.PositionX(sample, width);
            if (!x.HasValue || !double.IsFinite(x.Value))
            {
                FinishSegment();
                continue;
            }
            if (x < -2 || x > width + 2) continue;
            if (previousX.HasValue && x.Value + .01 < previousX.Value) FinishSegment();
            previousX = x.Value;

            var y = bottom - (sample.Value.Value - range.Minimum) / span * (bottom - top);
            var candidate = new TrendVertex(x.Value, y, sample.Value.Value, order);
            var bucketIndex = Math.Clamp((int)Math.Floor(x.Value), 0, bucketCount - 1);
            if (bucket is not { } current || current.Index != bucketIndex)
            {
                FlushBucket();
                bucket = new TrendBucket(bucketIndex, candidate, candidate, candidate, candidate);
                continue;
            }

            bucket = current with
            {
                Minimum = candidate.Value < current.Minimum.Value ? candidate : current.Minimum,
                Maximum = candidate.Value > current.Maximum.Value ? candidate : current.Maximum,
                Last = candidate
            };
        }
        FinishSegment();
        if (segments.Count == 0) return Geometry.Empty;

        var geometry = new StreamGeometry();
        using (var context = geometry.Open())
        {
            foreach (var vertices in segments)
            {
                context.BeginFigure(new Point(vertices[0].X, vertices[0].Y), false, false);
                var previous = vertices[0];
                for (var index = 1; index < vertices.Count; index++)
                {
                    var next = vertices[index];
                    if (shape == LiveMonitorTrendShape.Step)
                        context.LineTo(new Point(next.X, previous.Y), true, false);
                    context.LineTo(new Point(next.X, next.Y), true, true);
                    previous = next;
                }
            }
        }
        geometry.Freeze();
        return geometry;
    }

    private static void SetText(TextBlock target, string? value)
    {
        value ??= string.Empty;
        if (!string.Equals(target.Text, value, StringComparison.Ordinal)) target.Text = value;
    }

    private void RefreshLayoutSelector()
    {
        var choices = LiveMonitorLayouts.Choices(Preferences).Select(choice => new LayoutItem(choice.Layout.Id, choice.Layout.Name)).ToArray();
        LayoutSelector.ItemsSource = choices;
        LayoutSelector.SelectedValue = Preferences.ActiveLayoutId;
        if (LayoutSelector.SelectedIndex >= 0) return;
        Preferences.ActiveLayoutId = LiveMonitorLayout.FactoryDefaultId;
        LayoutSelector.SelectedValue = Preferences.ActiveLayoutId;
    }

    private void LayoutSelector_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_updatingControls || LayoutSelector.SelectedValue is not string id || id == Preferences.ActiveLayoutId) return;
        _scaleSettingsOpen = false;
        Preferences.ActiveLayoutId = id;
        _trendRanges.Clear();
        SavePreferences();
        RenderAll();
    }

    private void RenderSurfaceState()
    {
        ScaleSettingsButton.Tag = _scaleSettingsOpen ? "selected" : null;
        ScaleSettingsSurface.Visibility = _scaleSettingsOpen ? Visibility.Visible : Visibility.Collapsed;
        TileGrid.Visibility = _scaleSettingsOpen ? Visibility.Collapsed : Visibility.Visible;
    }

    private void ScaleSettingsButton_Click(object sender, RoutedEventArgs e)
    {
        if (_scaleSettingsOpen) { CloseScaleSettings(); return; }
        _scaleSettingsOpen = true;
        UpdateSizePresetSelection();
        RenderSurfaceState();
    }

    private void CloseScaleSettings_Click(object sender, RoutedEventArgs e) => CloseScaleSettings();

    private void CloseScaleSettings()
    {
        _scaleSettingsOpen = false;
        RenderSurfaceState();
    }

    private void SizePresetButton_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button { CommandParameter: string raw } ||
            !double.TryParse(raw, NumberStyles.Float, CultureInfo.InvariantCulture, out var requested)) return;

        // Button.Click occurs after pointer release. Hide the chooser first and
        // defer the one scale/layout commit until the routed input event has
        // unwound, so no control can move while it still owns mouse capture.
        CloseScaleSettings();
        _ = Dispatcher.BeginInvoke(() => ApplySizePreset(requested), DispatcherPriority.ContextIdle);
    }

    private void ApplySizePreset(double requested)
    {
        Preferences.OverallScale = Math.Clamp(requested, .8, 1.25);
        SavePreferences();
        ApplyScale();
        EnsureWindowVisible();
    }

    private void UpdateSizePresetSelection()
    {
        var current = Preferences.OverallScale;
        var nearest = new[] { .8d, 1d, 1.25d }.MinBy(candidate => Math.Abs(candidate - current));
        CompactSizeButton.Tag = Math.Abs(nearest - .8) < .001 ? "selected" : null;
        StandardSizeButton.Tag = Math.Abs(nearest - 1) < .001 ? "selected" : null;
        ExpandedSizeButton.Tag = Math.Abs(nearest - 1.25) < .001 ? "selected" : null;
    }

    private void ApplyScale()
    {
        var scale = Math.Clamp(Preferences.OverallScale, .8, 1.25);
        if (RootScale.LayoutTransform is ScaleTransform current &&
            Math.Abs(current.ScaleX - scale) < .001 && Math.Abs(current.ScaleY - scale) < .001) return;
        RootScale.LayoutTransform = new ScaleTransform(scale, scale);
    }

    private void ControlStrip_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        if (e.LeftButton != MouseButtonState.Pressed || e.OriginalSource is not DependencyObject source) return;
        if (FindAncestor<Button>(source) is not null || FindAncestor<ComboBox>(source) is not null) return;
        try { DragMove(); }
        catch (InvalidOperationException) { }
        e.Handled = true;
    }

    private static T? FindAncestor<T>(DependencyObject? current) where T : DependencyObject
    {
        while (current is not null)
        {
            if (current is T match) return match;
            try { current = VisualTreeHelper.GetParent(current); }
            catch (InvalidOperationException) { current = LogicalTreeHelper.GetParent(current); }
        }
        return null;
    }

    private void ReturnToAppButton_Click(object sender, RoutedEventArgs e) => _state.SetLiveMonitorVisible(false);
    private void CloseButton_Click(object sender, RoutedEventArgs e) => _state.SetLiveMonitorVisible(false);

    private void SavePreferences()
    {
        _lastKnownEditorSignature = LiveMonitorLayouts.EditorSignature(Preferences);
        _state.SaveLiveMonitorPreferences();
    }

    private void RestorePlacement()
    {
        _restoring = true;
        try
        {
            if (Preferences.Left.HasValue && Preferences.Top.HasValue && double.IsFinite(Preferences.Left.Value) && double.IsFinite(Preferences.Top.Value))
            {
                Left = Preferences.Left.Value;
                Top = Preferences.Top.Value;
                Dispatcher.BeginInvoke(EnsureWindowVisible, DispatcherPriority.ContextIdle);
            }
            else RecoverToNearestDisplay();
        }
        finally { _restoring = false; }
    }

    private void EnsureWindowVisible()
    {
        if (!IsLoaded) return;
        var handle = new WindowInteropHelper(this).Handle;
        if (handle == IntPtr.Zero || !GetWindowRect(handle, out var rect)) return;
        var screen = Forms.Screen.FromHandle(handle);
        var work = screen.WorkingArea;
        var width = rect.Right - rect.Left;
        var height = rect.Bottom - rect.Top;
        var x = Math.Clamp(rect.Left, work.Left, Math.Max(work.Left, work.Right - width));
        var y = Math.Clamp(rect.Top, work.Top, Math.Max(work.Top, work.Bottom - height));
        if (x == rect.Left && y == rect.Top) return;
        SetWindowPos(handle, IntPtr.Zero, x, y, 0, 0, 0x0001 | 0x0004 | 0x0010);
        Preferences.PlacementRecoveredAt = DateTimeOffset.UtcNow;
        ScheduleSave();
    }

    private void RecoverToNearestDisplay()
    {
        var screens = Forms.Screen.AllScreens;
        var target = screens.FirstOrDefault(screen => string.Equals(screen.DeviceName, Preferences.MonitorDeviceName, StringComparison.OrdinalIgnoreCase));
        if (target is null && Preferences.Left.HasValue && Preferences.Top.HasValue)
            target = screens.OrderBy(screen => DistanceSquared(Preferences.Left.Value, Preferences.Top.Value, screen.WorkingArea.Left, screen.WorkingArea.Top)).FirstOrDefault();
        target ??= Forms.Screen.PrimaryScreen ?? screens[0];
        var handle = new WindowInteropHelper(this).Handle;
        if (handle == IntPtr.Zero || !GetWindowRect(handle, out var rect)) return;
        var width = rect.Right - rect.Left;
        var x = Math.Max(target.WorkingArea.Left + 24, target.WorkingArea.Right - width - 24);
        var y = target.WorkingArea.Top + 24;
        SetWindowPos(handle, IntPtr.Zero, x, y, 0, 0, 0x0001 | 0x0004 | 0x0010);
        Preferences.MonitorDeviceName = target.DeviceName;
        Preferences.PlacementRecoveredAt = DateTimeOffset.UtcNow;
        ScheduleSave();
    }

    private static double DistanceSquared(double x1, double y1, double x2, double y2) => Math.Pow(x1 - x2, 2) + Math.Pow(y1 - y2, 2);

    private void ScheduleSave()
    {
        if (_restoring || !IsLoaded) return;
        _saveTimer.Stop();
        _saveTimer.Start();
    }

    private void SavePlacement()
    {
        _saveTimer.Stop();
        if (!IsLoaded || _restoring) return;
        Preferences.Left = Left;
        Preferences.Top = Top;
        var handle = new WindowInteropHelper(this).Handle;
        if (handle != IntPtr.Zero) Preferences.MonitorDeviceName = Forms.Screen.FromHandle(handle).DeviceName;
        SavePreferences();
    }

    private void ApplyWindowTreatment()
    {
        var handle = new WindowInteropHelper(this).Handle;
        var corner = 2;
        _ = DwmSetWindowAttribute(handle, 33, ref corner, sizeof(int));
    }

    private Brush AccentFor(LiveMonitorTile tile, LiveTelemetryMetricDefinition definition)
    {
        if (!string.Equals(tile.Accent, "default", StringComparison.OrdinalIgnoreCase))
            return tile.Accent.ToLowerInvariant() switch
            {
                "blue" => Resource<Brush>("MonitorMintBrush"),
                "green" => Resource<Brush>("MonitorGreenBrush"),
                "amber" => Resource<Brush>("MonitorAmberBrush"),
                "coral" => Resource<Brush>("MonitorCoralBrush"),
                "violet" => Resource<Brush>("MonitorVioletBrush"),
                _ => Resource<Brush>("MonitorAccentBrush")
            };
        return definition.Id switch
        {
            "speed" => Resource<Brush>("MonitorMintBrush"),
            "throttle" => Resource<Brush>("MonitorGreenBrush"),
            "brake" => Resource<Brush>("MonitorCoralBrush"),
            "steering" => Resource<Brush>("MonitorVioletBrush"),
            "fuel" or "fuel-laps" => Resource<Brush>("MonitorAmberBrush"),
            _ => Resource<Brush>("MonitorAccentBrush")
        };
    }

    private T Resource<T>(string key) where T : class => (T)FindResource(key);

    private sealed record TileVisual(
        LiveMonitorTile Tile,
        Border Root,
        Action<LiveTelemetryMetricReading, LiveMonitorState> Update,
        Action ResetTrend,
        Func<long, bool, bool> AnimateTrend,
        bool UsesTrend);

    private sealed record ReadingVisual(
        FrameworkElement Root,
        Action<LiveTelemetryMetricReading, LiveMonitorState> Update,
        Action ResetTrend,
        Func<long, bool, bool> AnimateTrend,
        bool UsesTrend);

    private sealed record MissingVisual(FrameworkElement Root, Action<LiveTelemetryMetricReading> Update);
    private sealed record ValueLineVisual(Grid Root, Action<LiveTelemetryMetricReading> Update);
    private sealed record FittedTextVisual(Viewbox Root, TextBlock Text);

    private readonly record struct TrendSample(double? Value, DateTimeOffset At, double? LapProgress);
    private readonly record struct TrendMotion(double Shift, bool Continue);
    private readonly record struct TrendVertex(double X, double Y, double Value, int Order);
    private readonly record struct TrendBucket(int Index, TrendVertex First, TrendVertex Minimum, TrendVertex Maximum, TrendVertex Last);

    private sealed class TrendBuffer
    {
        private readonly LiveMonitorTrendDuration _duration;
        private readonly string _metricId;
        private readonly string _unit;
        private readonly List<TrendSample> _samples = [];
        private int _start;
        private int _activeNumericCount;
        private bool _initialized;
        private long _lastFrame = -1;
        private int? _lastLap;
        private int _sourceTickRate = 60;
        private DateTimeOffset? _latestSourceAt;
        private DateTimeOffset? _latestLapAt;
        private double? _latestLapProgress;
        private double _lapRatePerSecond;
        private long _lastArrivalTimestamp;

        public int Count => _samples.Count - _start;
        public IEnumerable<TrendSample> Samples
        {
            get
            {
                for (var index = _start; index < _samples.Count; index++) yield return _samples[index];
            }
        }
        public TimeSpan GapThreshold => TimeSpan.FromMilliseconds(Math.Max(40, 3500d / _sourceTickRate));

        public TrendBuffer(LiveMonitorTrendDuration duration, string metricId, string unit)
        {
            _duration = duration;
            _metricId = metricId;
            _unit = unit;
        }

        public bool Update(LiveTelemetryMetricReading reading, LiveMonitorState state)
        {
            var snapshot = state.Snapshot;
            if (!snapshot.Connected)
            {
                var changed = Count > 0;
                Clear();
                return changed;
            }

            _sourceTickRate = Math.Clamp(state.SourceTickRate > 0 ? state.SourceTickRate : 60, 1, 240);
            var at = snapshot.SourceTimestamp > DateTimeOffset.MinValue ? snapshot.SourceTimestamp : state.UpdatedAt;
            var progress = LapProgress(snapshot.Lap, snapshot.LapDistancePercent);
            var changedByReset = false;
            if (!_initialized)
            {
                _initialized = true;
                Seed(state);
                changedByReset = Count > 0;
            }

            var lapRegressed = _lastLap.HasValue && snapshot.Lap.HasValue && snapshot.Lap.Value < _lastLap.Value;
            if (lapRegressed || IsClockRegression(at, progress))
            {
                ResetSamples();
                changedByReset = true;
            }

            if (state.FramesRead == _lastFrame)
            {
                _lastLap = snapshot.Lap;
                return changedByReset;
            }

            double? value = reading.NumericValue is { } numeric && double.IsFinite(numeric) ? numeric : null;
            var duplicate = Count > 0 && _samples[^1] is var latest && latest.At == at &&
                Nullable.Equals(latest.LapProgress, progress) && Nullable.Equals(latest.Value, value);
            ObserveClock(at, progress);
            if (!duplicate) AddSample(new TrendSample(value, at, progress));
            _lastArrivalTimestamp = Stopwatch.GetTimestamp();
            _lastFrame = state.FramesRead;
            _lastLap = snapshot.Lap;
            Trim();
            return changedByReset || !duplicate;
        }

        public void Clear()
        {
            ResetSamples();
            _initialized = false;
            _lastFrame = -1;
            _lastLap = null;
        }

        public IReadOnlyList<double> NumericValues()
        {
            var values = new List<double>(_activeNumericCount);
            for (var index = _start; index < _samples.Count; index++)
            {
                var value = _samples[index].Value;
                if (value.HasValue && double.IsFinite(value.Value)) values.Add(value.Value);
            }
            return values;
        }

        public double? PositionX(TrendSample sample, double width)
        {
            var seconds = DurationSeconds();
            if (seconds.HasValue)
            {
                return _latestSourceAt.HasValue
                    ? width - (_latestSourceAt.Value - sample.At).TotalSeconds / seconds.Value * width
                    : null;
            }
            return _latestLapProgress.HasValue && sample.LapProgress.HasValue
                ? width - (_latestLapProgress.Value - sample.LapProgress.Value) / LapWindow() * width
                : null;
        }

        public TrendMotion DisplayMotion(long frameTimestamp, double width, bool enabled)
        {
            if (!enabled || _activeNumericCount < 2 || _lastArrivalTimestamp <= 0)
                return new TrendMotion(0, false);

            var elapsed = Math.Max(0, Stopwatch.GetElapsedTime(_lastArrivalTimestamp, frameTimestamp).TotalSeconds);
            var maximumCoast = 1.5d / _sourceTickRate;
            var coast = Math.Min(elapsed, maximumCoast);
            var continueAnimating = elapsed + .00001 < maximumCoast;
            var seconds = DurationSeconds();
            if (seconds.HasValue && _latestSourceAt.HasValue)
                return new TrendMotion(Math.Max(0, coast / seconds.Value * width), continueAnimating);
            if (_latestLapProgress.HasValue && _lapRatePerSecond > 0)
                return new TrendMotion(Math.Max(0, _lapRatePerSecond * coast / LapWindow() * width), continueAnimating);
            return new TrendMotion(0, false);
        }

        private void Seed(LiveMonitorState state)
        {
            foreach (var point in state.History ?? [])
            {
                if (point.At <= DateTimeOffset.MinValue) continue;
                var progress = LapProgress(point.Lap, point.LapDistancePercent);
                if (IsClockRegression(point.At, progress)) ResetSamples();
                ObserveClock(point.At, progress);
                AddSample(new TrendSample(LiveTelemetryCatalog.TrendValue(_metricId, point, _unit), point.At, progress));
            }
            Trim();
        }

        private bool IsClockRegression(DateTimeOffset at, double? progress) =>
            (_latestSourceAt.HasValue && at < _latestSourceAt.Value) ||
            (_latestLapProgress.HasValue && progress.HasValue && progress.Value < _latestLapProgress.Value - .5);

        private void ObserveClock(DateTimeOffset at, double? progress)
        {
            if (!_latestSourceAt.HasValue || at >= _latestSourceAt.Value) _latestSourceAt = at;
            if (!progress.HasValue || _latestLapAt.HasValue && at < _latestLapAt.Value) return;
            if (_latestLapProgress.HasValue && _latestLapAt.HasValue && at > _latestLapAt.Value)
            {
                var elapsed = (at - _latestLapAt.Value).TotalSeconds;
                var delta = progress.Value - _latestLapProgress.Value;
                _lapRatePerSecond = delta >= 0 && delta < .25 && elapsed > 0 && elapsed <= GapThreshold.TotalSeconds * 3
                    ? Math.Clamp(delta / elapsed, 0, 10)
                    : 0;
            }
            _latestLapProgress = progress;
            _latestLapAt = at;
        }

        private void AddSample(TrendSample sample)
        {
            _samples.Add(sample);
            if (sample.Value.HasValue && double.IsFinite(sample.Value.Value)) _activeNumericCount++;
        }

        private void Trim()
        {
            var seconds = DurationSeconds();
            if (seconds.HasValue && _latestSourceAt.HasValue)
            {
                var cutoff = _latestSourceAt.Value - TimeSpan.FromSeconds(seconds.Value);
                while (_start < _samples.Count && _samples[_start].At < cutoff) RemoveFirst();
            }
            else if (_latestLapProgress.HasValue)
            {
                var cutoff = _latestLapProgress.Value - LapWindow();
                while (_start < _samples.Count && _samples[_start].LapProgress is { } progress && progress < cutoff) RemoveFirst();
            }

            var maximum = MaximumPointCount();
            while (Count > maximum) RemoveFirst();
            if (_start > 1024 && _start > _samples.Count / 2)
            {
                _samples.RemoveRange(0, _start);
                _start = 0;
            }
        }

        private void RemoveFirst()
        {
            if (_start >= _samples.Count) return;
            var sample = _samples[_start++];
            if (sample.Value.HasValue && double.IsFinite(sample.Value.Value)) _activeNumericCount--;
        }

        private void ResetSamples()
        {
            _samples.Clear();
            _start = 0;
            _activeNumericCount = 0;
            _latestSourceAt = null;
            _latestLapAt = null;
            _latestLapProgress = null;
            _lapRatePerSecond = 0;
            _lastArrivalTimestamp = 0;
        }

        private double? DurationSeconds() => _duration switch
        {
            LiveMonitorTrendDuration.Seconds15 => 15,
            LiveMonitorTrendDuration.Seconds30 => 30,
            LiveMonitorTrendDuration.Seconds60 => 60,
            _ => null
        };

        private double LapWindow() => _duration == LiveMonitorTrendDuration.ThreeLaps ? 3 : 1;

        private int MaximumPointCount()
        {
            var seconds = DurationSeconds();
            return seconds.HasValue
                ? Math.Clamp((int)Math.Ceiling(seconds.Value * _sourceTickRate) + 8, 64, 36_000)
                : 36_000;
        }

        private static double? LapProgress(int? lap, double? distance)
        {
            if (!lap.HasValue) return null;
            if (!distance.HasValue || !double.IsFinite(distance.Value)) return lap.Value;
            var fraction = distance.Value > 1 ? distance.Value / 100d : distance.Value;
            return lap.Value + Math.Clamp(fraction, 0, 1);
        }
    }

    private sealed record LayoutItem(string Id, string Name)
    {
        public override string ToString() => Name;
    }

    [StructLayout(LayoutKind.Sequential)] private struct NativeRect { public int Left; public int Top; public int Right; public int Bottom; }
    [DllImport("dwmapi.dll")] private static extern int DwmSetWindowAttribute(IntPtr handle, int attribute, ref int value, int size);
    [DllImport("user32.dll")] private static extern bool GetWindowRect(IntPtr handle, out NativeRect rect);
    [DllImport("user32.dll", SetLastError = true)] private static extern bool SetWindowPos(IntPtr handle, IntPtr insertAfter, int x, int y, int cx, int cy, uint flags);
}
