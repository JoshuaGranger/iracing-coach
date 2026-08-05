using System.Globalization;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Automation;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Shapes;
using System.Windows.Threading;
using Forms = System.Windows.Forms;
using Brush = System.Windows.Media.Brush;
using Button = System.Windows.Controls.Button;
using DragEventArgs = System.Windows.DragEventArgs;
using DataObject = System.Windows.DataObject;
using DragDropEffects = System.Windows.DragDropEffects;
using Cursors = System.Windows.Input.Cursors;
using FontFamily = System.Windows.Media.FontFamily;
using HAlignment = System.Windows.HorizontalAlignment;
using KeyEventArgs = System.Windows.Input.KeyEventArgs;
using MouseEventArgs = System.Windows.Input.MouseEventArgs;
using Panel = System.Windows.Controls.Panel;
using Point = System.Windows.Point;
using Size = System.Windows.Size;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.App;

public partial class LiveMonitorWindow : Window
{
    private const double GridViewportWidth = 444;
    private const double GridViewportHeight = 296;
    private const double DefaultCellSize = 148;
    private const double EditorHeight = 300;
    private const string MetricDragFormat = "iRacingCoach.LiveMetric";
    private const string TileDragFormat = "iRacingCoach.LiveTile";
    private readonly CompanionState _state;
    private readonly DispatcherTimer _saveTimer;
    private readonly DispatcherTimer _renderTimer;
    private readonly Stack<LayoutSnapshot> _undo = [];
    private readonly Dictionary<string, (double Minimum, double Maximum)> _trendRanges = new(StringComparer.Ordinal);
    private bool _restoring;
    private bool _updatingControls;
    private bool _gridSettingsOpen;
    private bool _scaleSettingsOpen;
    private int _renderDirty;
    private DateTimeOffset _lastCatalogRefresh = DateTimeOffset.MinValue;
    private string? _selectedTileId;
    private Point _dragStart;
    private string? _dragTileId;
    private CatalogItem? _dragCatalogItem;
    private Border? _dropPreview;
    private GridSettingsSnapshot? _gridSettingsBackup;
    private LayoutSnapshot[]? _gridSettingsUndoBackup;
    private double? _scaleSettingsBackup;
    private double _cellSize = DefaultCellSize;
    private string _lastKnownEditorSignature;

    public LiveMonitorWindow(CompanionState state)
    {
        _state = state;
        _lastKnownEditorSignature = LiveMonitorLayouts.EditorSignature(Preferences);
        InitializeComponent();
        SizeToContent = SizeToContent.WidthAndHeight;
        _saveTimer = new DispatcherTimer(TimeSpan.FromMilliseconds(500), DispatcherPriority.Background, (_, _) => SavePlacement(), Dispatcher);
        _saveTimer.Stop();
        _renderTimer = new DispatcherTimer(TimeSpan.FromMilliseconds(200), DispatcherPriority.Render, (_, _) =>
        {
            if (Interlocked.Exchange(ref _renderDirty, 0) != 0 && IsVisible) RenderTelemetry();
        }, Dispatcher);
        _renderTimer.Start();
        Loaded += OnLoaded;
        SourceInitialized += (_, _) => ApplyWindowTreatment();
        LocationChanged += (_, _) => ScheduleSave();
        _state.Changed += OnCompanionStateChanged;
        _state.LiveTelemetryChanged += OnLiveTelemetryChanged;
        Closed += (_, _) =>
        {
            _renderTimer.Stop();
            _saveTimer.Stop();
            _state.Changed -= OnCompanionStateChanged;
            _state.LiveTelemetryChanged -= OnLiveTelemetryChanged;
        };
    }

    public void ShowMonitor()
    {
        if (!IsVisible) Show();
        Topmost = true;
        Activate();
        RenderAll();
    }

    public void HideMonitor()
    {
        SavePlacement();
        Hide();
    }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        RenderAll();
        Dispatcher.BeginInvoke(RestorePlacement, DispatcherPriority.Loaded);
    }

    private void OnCompanionStateChanged()
    {
        Interlocked.Exchange(ref _renderDirty, 1);
        if (Dispatcher.HasShutdownStarted) return;
        if (Dispatcher.CheckAccess()) CheckForExternalEditorChange();
        else Dispatcher.BeginInvoke(CheckForExternalEditorChange, DispatcherPriority.Background);
    }

    private void OnLiveTelemetryChanged() => Interlocked.Exchange(ref _renderDirty, 1);

    private void CheckForExternalEditorChange()
    {
        var signature = LiveMonitorLayouts.EditorSignature(Preferences);
        if (string.Equals(signature, _lastKnownEditorSignature, StringComparison.Ordinal)) return;
        _lastKnownEditorSignature = signature;
        _undo.Clear();
        _selectedTileId = null;
        _gridSettingsBackup = null;
        _gridSettingsUndoBackup = null;
        _scaleSettingsBackup = null;
        _gridSettingsOpen = false;
        _scaleSettingsOpen = false;
        _trendRanges.Clear();
        RenderAll();
        EditorMessage.Text = "Dashboard changed in the main app. Undo history was refreshed.";
    }

    private LiveMonitorLayout Preferences => _state.Settings.LiveMonitor;
    private LiveMonitorLayoutChoice ActiveChoice => LiveMonitorLayouts.Active(Preferences);

    private void RenderAll()
    {
        _updatingControls = true;
        try
        {
            RefreshLayoutSelector();
            ApplyScale();
            RenderGrid();
            RenderEditingState(immediate: true);
            RefreshCatalog(force: true);
            RefreshTileEditor();
        }
        finally { _updatingControls = false; }
    }

    private void RenderTelemetry()
    {
        ConnectionDot.Fill = Resource<Brush>(_state.LiveState.Snapshot.Connected ? "SuccessBrush" : "UnavailableBrush");
        RenderGrid();
        if (!Preferences.IsLocked && DateTimeOffset.UtcNow - _lastCatalogRefresh > TimeSpan.FromSeconds(1)) RefreshCatalog(force: true);
    }

    private void RenderGrid()
    {
        var layout = ActiveChoice.Layout;
        TileGrid.Children.Clear();
        TileGrid.RowDefinitions.Clear();
        TileGrid.ColumnDefinitions.Clear();
        _cellSize = Math.Min(GridViewportWidth / layout.Columns, GridViewportHeight / layout.Rows);
        TileGrid.Width = layout.Columns * _cellSize;
        TileGrid.Height = layout.Rows * _cellSize;
        TileGrid.HorizontalAlignment = HAlignment.Center;
        TileGrid.VerticalAlignment = VerticalAlignment.Center;
        for (var row = 0; row < layout.Rows; row++) TileGrid.RowDefinitions.Add(new RowDefinition { Height = new GridLength(_cellSize) });
        for (var column = 0; column < layout.Columns; column++) TileGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(_cellSize) });

        foreach (var tile in layout.Tiles.OrderBy(item => item.Row).ThenBy(item => item.Column))
        {
            if (!LiveTelemetryCatalog.TryGet(tile.MetricId, out var definition)) continue;
            var reading = LiveTelemetryCatalog.Read(tile.MetricId, _state.LiveState, tile.Unit, tile.Precision, tile.TrendDuration);
            var visual = BuildTile(tile, definition, reading);
            Grid.SetRow(visual, tile.Row);
            Grid.SetColumn(visual, tile.Column);
            Grid.SetRowSpan(visual, tile.RowSpan);
            Grid.SetColumnSpan(visual, tile.ColumnSpan);
            TileGrid.Children.Add(visual);
        }
    }

    private Border BuildTile(LiveMonitorTile tile, LiveTelemetryMetricDefinition definition, LiveTelemetryMetricReading reading)
    {
        var selected = !Preferences.IsLocked && tile.Id == _selectedTileId;
        var border = new Border
        {
            Tag = tile.Id,
            Margin = new Thickness(3),
            Padding = new Thickness(10),
            CornerRadius = new CornerRadius(6),
            ClipToBounds = true,
            Background = Resource<Brush>(selected ? "AccentSubtleBrush" : "Surface1Brush"),
            BorderBrush = Resource<Brush>(selected ? "AccentBrush" : "BorderSubtleBrush"),
            BorderThickness = new Thickness(selected ? 2 : 1),
            Focusable = true,
            Cursor = Preferences.IsLocked ? Cursors.Arrow : Cursors.SizeAll
        };
        AutomationProperties.SetName(border, $"{definition.Name}. {(reading.Available ? $"{reading.DisplayValue} {reading.Unit}" : reading.AvailabilityMessage)}");
        border.ToolTip = reading.Available ? definition.Description : reading.AvailabilityMessage;
        border.PreviewMouseLeftButtonDown += Tile_MouseLeftButtonDown;
        border.PreviewMouseMove += Tile_MouseMove;
        border.MouseLeftButtonUp += Tile_MouseLeftButtonUp;
        border.PreviewKeyDown += Tile_PreviewKeyDown;

        var root = new Grid();
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        var header = new StackPanel();
        header.Children.Add(new TextBlock
        {
            Text = definition.Name.ToUpper(CultureInfo.CurrentCulture),
            FontSize = 10,
            FontWeight = FontWeights.SemiBold,
            Foreground = Resource<Brush>("TextSecondaryBrush"),
            TextWrapping = TextWrapping.Wrap,
            TextTrimming = TextTrimming.None,
            MaxHeight = 34
        });
        var source = new TextBlock
        {
            Text = SourceLabel(definition.Source),
            FontSize = 9,
            Foreground = Resource<Brush>("TextMutedBrush"),
            HorizontalAlignment = HAlignment.Left
        };
        header.Children.Add(source);
        root.Children.Add(header);

        var content = reading.Available ? BuildReading(tile, definition, reading) : BuildMissing(reading);
        Grid.SetRow(content, 1);
        root.Children.Add(content);
        if (reading.Available && !string.IsNullOrWhiteSpace(reading.SecondaryValue))
        {
            var secondary = new TextBlock
            {
                Text = reading.SecondaryValue,
                FontSize = 10,
                Foreground = Resource<Brush>("TextSecondaryBrush"),
                TextWrapping = TextWrapping.Wrap,
                TextTrimming = TextTrimming.CharacterEllipsis,
                MaxHeight = tile.RowSpan > 1 ? 38 : 28
            };
            Grid.SetRow(secondary, 2);
            root.Children.Add(secondary);
        }

        if (!Preferences.IsLocked)
        {
            var edit = IconButton("Edit tile", "Edit display, size, or position", "M2,12 L5,12 L13,4 L10,1 L2,9 Z M9,2 L12,5", (_, _) => SelectTile(tile.Id));
            edit.Width = edit.Height = 28;
            edit.Padding = new Thickness(7);
            edit.HorizontalAlignment = HAlignment.Right;
            edit.VerticalAlignment = VerticalAlignment.Bottom;
            Grid.SetRow(edit, 2);
            root.Children.Add(edit);
        }

        border.Child = root;
        return border;
    }

    private UIElement BuildReading(LiveMonitorTile tile, LiveTelemetryMetricDefinition definition, LiveTelemetryMetricReading reading) => tile.DisplayStyle switch
    {
        LiveMonitorDisplayStyle.Bar => BuildBar(reading, AccentFor(tile, definition)),
        LiveMonitorDisplayStyle.Gauge => BuildGauge(reading, AccentFor(tile, definition)),
        LiveMonitorDisplayStyle.Trend when reading.TrendValues.Count > 1 => BuildTrend(tile, definition, reading, AccentFor(tile, definition)),
        LiveMonitorDisplayStyle.Status => BuildStatus(reading),
        _ => BuildNumber(reading)
    };

    private UIElement BuildNumber(LiveTelemetryMetricReading reading)
    {
        var panel = new StackPanel { VerticalAlignment = VerticalAlignment.Center };
        panel.Children.Add(new TextBlock
        {
            Text = reading.DisplayValue,
            FontSize = 24,
            FontWeight = FontWeights.SemiBold,
            FontFamily = new FontFamily("Cascadia Mono, Consolas"),
            TextTrimming = TextTrimming.CharacterEllipsis
        });
        if (!string.IsNullOrWhiteSpace(reading.Unit)) panel.Children.Add(new TextBlock { Text = reading.Unit, FontSize = 10, Foreground = Resource<Brush>("TextMutedBrush") });
        return panel;
    }

    private UIElement BuildStatus(LiveTelemetryMetricReading reading) => new TextBlock
    {
        Text = reading.DisplayValue,
        FontSize = 16,
        FontWeight = FontWeights.SemiBold,
        VerticalAlignment = VerticalAlignment.Center,
        TextWrapping = TextWrapping.Wrap,
        TextTrimming = TextTrimming.CharacterEllipsis
    };

    private UIElement BuildMissing(LiveTelemetryMetricReading reading)
    {
        var panel = new StackPanel { VerticalAlignment = VerticalAlignment.Center };
        panel.Children.Add(new TextBlock { Text = reading.DisplayValue, FontSize = 16, FontWeight = FontWeights.SemiBold, Foreground = Resource<Brush>("UnavailableBrush") });
        panel.Children.Add(new TextBlock { Text = reading.AvailabilityMessage, FontSize = 10, Foreground = Resource<Brush>("TextMutedBrush"), TextWrapping = TextWrapping.Wrap, MaxHeight = 36 });
        return panel;
    }

    private UIElement BuildBar(LiveTelemetryMetricReading reading, Brush accent)
    {
        var panel = new Grid { VerticalAlignment = VerticalAlignment.Center };
        panel.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        panel.RowDefinitions.Add(new RowDefinition { Height = new GridLength(8) });
        panel.Children.Add(BuildValueLine(reading));
        var track = new Border { Background = Resource<Brush>("Surface3Brush"), CornerRadius = new CornerRadius(4), Margin = new Thickness(0, 7, 0, 0) };
        Grid.SetRow(track, 1);
        if (reading.NumericValue.HasValue && reading.Minimum.HasValue && reading.Maximum > reading.Minimum)
        {
            var fraction = Math.Clamp((reading.NumericValue.Value - reading.Minimum.Value) / (reading.Maximum!.Value - reading.Minimum.Value), 0, 1);
            var fill = new Border { Background = accent, CornerRadius = new CornerRadius(4), HorizontalAlignment = HAlignment.Left };
            track.SizeChanged += (_, _) => fill.Width = track.ActualWidth * fraction;
            track.Child = fill;
        }
        panel.Children.Add(track);
        return panel;
    }

    private UIElement BuildGauge(LiveTelemetryMetricReading reading, Brush accent)
    {
        var size = 76d;
        var canvas = new Canvas { Width = size, Height = size, HorizontalAlignment = HAlignment.Center, VerticalAlignment = VerticalAlignment.Center };
        canvas.Children.Add(new Ellipse { Width = size - 8, Height = size - 8, Margin = new Thickness(4), Stroke = Resource<Brush>("Surface3Brush"), StrokeThickness = 7 });
        if (reading.NumericValue.HasValue && reading.Minimum.HasValue && reading.Maximum > reading.Minimum)
        {
            var fraction = Math.Clamp((reading.NumericValue.Value - reading.Minimum.Value) / (reading.Maximum!.Value - reading.Minimum.Value), 0, .9999);
            var start = new Point(size / 2, 7);
            var angle = fraction * Math.PI * 2;
            var end = new Point(size / 2 + Math.Sin(angle) * (size / 2 - 7), size / 2 - Math.Cos(angle) * (size / 2 - 7));
            var figure = new PathFigure { StartPoint = start };
            figure.Segments.Add(new ArcSegment(end, new Size(size / 2 - 7, size / 2 - 7), 0, fraction > .5, SweepDirection.Clockwise, true));
            canvas.Children.Add(new Path { Data = new PathGeometry([figure]), Stroke = accent, StrokeThickness = 7, StrokeStartLineCap = PenLineCap.Round, StrokeEndLineCap = PenLineCap.Round });
        }
        var value = BuildValueLine(reading);
        value.Width = size;
        value.Height = size;
        canvas.Children.Add(value);
        return canvas;
    }

    private Grid BuildValueLine(LiveTelemetryMetricReading reading)
    {
        var grid = new Grid();
        grid.Children.Add(new TextBlock { Text = reading.DisplayValue, FontSize = 20, FontWeight = FontWeights.SemiBold, FontFamily = new FontFamily("Cascadia Mono, Consolas"), HorizontalAlignment = HAlignment.Center, VerticalAlignment = VerticalAlignment.Center });
        if (!string.IsNullOrWhiteSpace(reading.Unit)) grid.Children.Add(new TextBlock { Text = reading.Unit, FontSize = 9, Foreground = Resource<Brush>("TextMutedBrush"), HorizontalAlignment = HAlignment.Center, VerticalAlignment = VerticalAlignment.Bottom });
        return grid;
    }

    private UIElement BuildTrend(LiveMonitorTile tile, LiveTelemetryMetricDefinition definition, LiveTelemetryMetricReading reading, Brush accent)
    {
        var width = Math.Max(24, tile.ColumnSpan * _cellSize - 28);
        var height = Math.Max(18, tile.RowSpan * _cellSize - 74);
        var canvas = new Canvas { Width = width, Height = height, ClipToBounds = true, VerticalAlignment = VerticalAlignment.Center };
        var range = TrendRange(tile.Id, reading.TrendValues);
        var points = DownsampleMinMax(reading.TrendValues, Math.Max(24, (int)width / 2));
        var collection = new PointCollection();
        for (var index = 0; index < points.Count; index++)
        {
            var x = points.Count == 1 ? 0 : index * width / (points.Count - 1);
            var y = height - (points[index] - range.Minimum) / (range.Maximum - range.Minimum) * height;
            if (definition.TrendShape == LiveMonitorTrendShape.Step && collection.Count > 0)
                collection.Add(new Point(x, collection[^1].Y));
            collection.Add(new Point(x, y));
        }
        canvas.Children.Add(new Line { X1 = 0, X2 = width, Y1 = height / 2, Y2 = height / 2, Stroke = Resource<Brush>("BorderSubtleBrush"), StrokeThickness = 1 });
        canvas.Children.Add(new Polyline { Points = collection, Stroke = accent, StrokeThickness = 2, StrokeLineJoin = PenLineJoin.Round });
        canvas.Children.Add(new TextBlock { Text = $"{reading.DisplayValue} {reading.Unit}".Trim(), FontSize = 12, FontWeight = FontWeights.SemiBold, Background = Resource<Brush>("Surface1Brush") });
        return canvas;
    }

    private (double Minimum, double Maximum) TrendRange(string tileId, IReadOnlyList<double> values)
    {
        var minimum = values.Min();
        var maximum = values.Max();
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

    private static IReadOnlyList<double> DownsampleMinMax(IReadOnlyList<double> values, int buckets)
    {
        if (values.Count <= buckets * 2) return values.ToArray();
        var result = new List<double>(buckets * 2);
        for (var bucket = 0; bucket < buckets; bucket++)
        {
            var start = bucket * values.Count / buckets;
            var end = Math.Max(start + 1, (bucket + 1) * values.Count / buckets);
            var slice = values.Skip(start).Take(end - start).ToArray();
            var minIndex = Array.IndexOf(slice, slice.Min());
            var maxIndex = Array.IndexOf(slice, slice.Max());
            if (minIndex <= maxIndex) { result.Add(slice[minIndex]); if (maxIndex != minIndex) result.Add(slice[maxIndex]); }
            else { result.Add(slice[maxIndex]); result.Add(slice[minIndex]); }
        }
        return result;
    }

    private void RefreshLayoutSelector()
    {
        var choices = LiveMonitorLayouts.Choices(Preferences).Select(choice => new LayoutItem(choice.Layout.Id, choice.Layout.Name, choice.IsFactory)).ToArray();
        LayoutSelector.ItemsSource = choices;
        LayoutSelector.SelectedValue = Preferences.ActiveLayoutId;
        if (LayoutSelector.SelectedIndex < 0)
        {
            Preferences.ActiveLayoutId = LiveMonitorLayout.FactoryDefaultId;
            LayoutSelector.SelectedValue = Preferences.ActiveLayoutId;
        }
        var active = ActiveChoice;
        LayoutNameBox.Text = active.Layout.Name;
        LayoutNameBox.IsReadOnly = active.IsFactory;
        DeleteLayoutButton.IsEnabled = !active.IsFactory;
        ResetLayoutButton.IsEnabled = !active.IsFactory;
        UndoButton.IsEnabled = _undo.Count > 0;
    }

    private void RefreshCatalog(bool force)
    {
        if (!force && DateTimeOffset.UtcNow - _lastCatalogRefresh < TimeSpan.FromSeconds(1)) return;
        _lastCatalogRefresh = DateTimeOffset.UtcNow;
        var search = CatalogSearch.Text?.Trim() ?? string.Empty;
        CatalogList.ItemsSource = LiveTelemetryCatalog.All
            .Where(definition => search.Length == 0 || definition.Name.Contains(search, StringComparison.CurrentCultureIgnoreCase) || definition.Description.Contains(search, StringComparison.CurrentCultureIgnoreCase))
            .Select(definition =>
            {
                var reading = LiveTelemetryCatalog.Read(definition.Id, _state.LiveState, definition.DefaultUnit, definition.DefaultPrecision);
                var detail = reading.Available ? $"{reading.DisplayValue} {reading.Unit}".Trim() : reading.AvailabilityMessage;
                return new CatalogItem(definition.Id, definition.Name, SourceLabel(definition.Source), detail, string.Join(" · ", definition.Styles.Select(StyleLabel)), $"Add {definition.Name}");
            }).ToArray();
    }

    private void RefreshTileEditor()
    {
        var layout = ActiveChoice.Layout;
        var tile = layout.Tiles.FirstOrDefault(item => item.Id == _selectedTileId);
        if (tile is null || Preferences.IsLocked || _gridSettingsOpen || _scaleSettingsOpen)
        {
            TileEditor.Visibility = Visibility.Collapsed;
            return;
        }
        var definition = LiveTelemetryCatalog.Get(tile.MetricId);
        _updatingControls = true;
        try
        {
            TileEditor.Visibility = Visibility.Visible;
            SelectedTileName.Text = definition.Name;
            StyleSelector.DisplayMemberPath = nameof(NamedOption<LiveMonitorDisplayStyle>.Label);
            StyleSelector.SelectedValuePath = nameof(NamedOption<LiveMonitorDisplayStyle>.Value);
            StyleSelector.ItemsSource = definition.Styles
                .Select(value => new NamedOption<LiveMonitorDisplayStyle>(value, StyleLabel(value))).ToArray();
            StyleSelector.SelectedValue = tile.DisplayStyle;
            UnitSelector.ItemsSource = definition.Units;
            UnitSelector.SelectedItem = tile.Unit;
            WidthSelector.ItemsSource = Enumerable.Range(1, layout.Columns).ToArray();
            WidthSelector.SelectedItem = tile.ColumnSpan;
            HeightSelector.ItemsSource = Enumerable.Range(1, layout.Rows).ToArray();
            HeightSelector.SelectedItem = tile.RowSpan;
            PrecisionSelector.ItemsSource = Enumerable.Range(0, 4).ToArray();
            PrecisionSelector.SelectedItem = tile.Precision;
            TrendSelector.DisplayMemberPath = nameof(NamedOption<LiveMonitorTrendDuration>.Label);
            TrendSelector.SelectedValuePath = nameof(NamedOption<LiveMonitorTrendDuration>.Value);
            TrendSelector.ItemsSource = LiveTelemetryCatalog.TrendDurations(tile.MetricId)
                .Select(value => new NamedOption<LiveMonitorTrendDuration>(value, TrendLabel(value))).ToArray();
            TrendSelector.SelectedValue = tile.TrendDuration;
            TrendSelector.IsEnabled = tile.DisplayStyle == LiveMonitorDisplayStyle.Trend;
            AccentSelector.DisplayMemberPath = nameof(NamedOption<string>.Label);
            AccentSelector.SelectedValuePath = nameof(NamedOption<string>.Value);
            AccentSelector.ItemsSource = AccentOptions;
            AccentSelector.SelectedValue = tile.Accent;
        }
        finally { _updatingControls = false; }
    }

    private void SelectTile(string tileId)
    {
        _selectedTileId = tileId;
        RenderGrid();
        RefreshTileEditor();
    }

    private void RenderEditingState(bool immediate = false)
    {
        var unlocked = !Preferences.IsLocked;
        LockButton.Tag = unlocked ? "selected" : null;
        LockButton.ToolTip = unlocked ? "Lock layout for driving" : "Unlock layout editing";
        AutomationProperties.SetName(LockButton, unlocked ? "Lock layout for driving" : "Unlock layout editing");
        LockIcon.Data = Geometry.Parse(unlocked
            ? "M6,7 L6,5 C6,2 11,2 12,4 M3,7 L13,7 L13,15 L3,15 Z"
            : "M5,7 L5,5 C5,1.5 11,1.5 11,5 L11,7 M3,7 L13,7 L13,15 L3,15 Z");
        GridSettingsButton.Tag = _gridSettingsOpen ? "selected" : null;
        ScaleSettingsButton.Tag = _scaleSettingsOpen ? "selected" : null;
        GridSettingsSurface.Visibility = _gridSettingsOpen ? Visibility.Visible : Visibility.Collapsed;
        ScaleSettingsSurface.Visibility = _scaleSettingsOpen ? Visibility.Visible : Visibility.Collapsed;
        var settingsOpen = _gridSettingsOpen || _scaleSettingsOpen;
        TileGrid.Visibility = settingsOpen ? Visibility.Collapsed : Visibility.Visible;
        var showEditor = unlocked && !settingsOpen;
        if (immediate || ReducedMotion())
        {
            EditorPanel.BeginAnimation(HeightProperty, null);
            EditorPanel.Height = showEditor ? EditorHeight : 0;
            EditorPanel.Visibility = showEditor ? Visibility.Visible : Visibility.Collapsed;
        }
        else if (showEditor)
        {
            EditorPanel.Visibility = Visibility.Visible;
            EditorPanel.BeginAnimation(HeightProperty, new DoubleAnimation(0, EditorHeight, TimeSpan.FromMilliseconds(200)) { EasingFunction = new CubicEase { EasingMode = EasingMode.EaseOut } });
            Dispatcher.BeginInvoke(EnsureEditorVisible, DispatcherPriority.ContextIdle);
        }
        else if (EditorPanel.Visibility == Visibility.Visible)
        {
            var animation = new DoubleAnimation(EditorPanel.ActualHeight, 0, TimeSpan.FromMilliseconds(180)) { EasingFunction = new CubicEase { EasingMode = EasingMode.EaseIn } };
            animation.Completed += (_, _) => EditorPanel.Visibility = Visibility.Collapsed;
            EditorPanel.BeginAnimation(HeightProperty, animation);
        }
        RefreshTileEditor();
    }

    private void ApplyScale()
    {
        var scale = Math.Clamp(Preferences.OverallScale, .7, 2);
        RootScale.LayoutTransform = new ScaleTransform(scale, scale);
    }

    private void DragGrip_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        if (e.LeftButton != MouseButtonState.Pressed) return;
        try { DragMove(); }
        catch (InvalidOperationException) { }
        e.Handled = true;
    }

    private void LockButton_Click(object sender, RoutedEventArgs e)
    {
        if (_gridSettingsOpen) CloseGridSettings(commit: true);
        if (_scaleSettingsOpen) CloseScaleSettings(commit: true);
        Preferences.IsLocked = !Preferences.IsLocked;
        if (Preferences.IsLocked) _selectedTileId = null;
        SavePreferences();
        RenderEditingState();
        RenderGrid();
        RefreshLayoutSelector();
    }

    private void GridSettingsButton_Click(object sender, RoutedEventArgs e)
    {
        if (_gridSettingsOpen) { CloseGridSettings(commit: true); return; }
        if (_scaleSettingsOpen) CloseScaleSettings(commit: true);
        _gridSettingsBackup = CaptureGridSettings();
        _gridSettingsUndoBackup = _undo.ToArray();
        _gridSettingsOpen = true;
        var layout = ActiveChoice.Layout;
        _updatingControls = true;
        RowsSlider.Value = layout.Rows;
        ColumnsSlider.Value = layout.Columns;
        RowsValue.Text = layout.Rows.ToString(CultureInfo.CurrentCulture);
        ColumnsValue.Text = layout.Columns.ToString(CultureInfo.CurrentCulture);
        GridSettingsMessage.Text = $"{layout.Columns} × {layout.Rows}";
        _updatingControls = false;
        RenderEditingState();
    }

    private void ScaleSettingsButton_Click(object sender, RoutedEventArgs e)
    {
        if (_scaleSettingsOpen) { CloseScaleSettings(commit: true); return; }
        if (_gridSettingsOpen) CloseGridSettings(commit: true);
        _scaleSettingsBackup = Preferences.OverallScale;
        _scaleSettingsOpen = true;
        _updatingControls = true;
        ScaleSlider.Value = Math.Round(Preferences.OverallScale * 100 / 10) * 10;
        ScaleValue.Text = $"{Preferences.OverallScale:P0}";
        _updatingControls = false;
        RenderEditingState();
    }

    private void CancelGridSettings_Click(object sender, RoutedEventArgs e) => CloseGridSettings(commit: false);
    private void ApplyGridSettings_Click(object sender, RoutedEventArgs e) => CloseGridSettings(commit: true);
    private void CancelScaleSettings_Click(object sender, RoutedEventArgs e) => CloseScaleSettings(commit: false);
    private void ApplyScaleSettings_Click(object sender, RoutedEventArgs e) => CloseScaleSettings(commit: true);

    private void CloseGridSettings(bool commit)
    {
        if (!commit && _gridSettingsBackup is not null)
        {
            RestoreGridSettings(_gridSettingsBackup);
            RestoreUndoHistory(_gridSettingsUndoBackup);
        }
        _gridSettingsOpen = false;
        _gridSettingsBackup = null;
        _gridSettingsUndoBackup = null;
        SavePreferences();
        RenderAll();
    }

    private void CloseScaleSettings(bool commit)
    {
        if (!commit && _scaleSettingsBackup.HasValue) Preferences.OverallScale = _scaleSettingsBackup.Value;
        _scaleSettingsOpen = false;
        _scaleSettingsBackup = null;
        SavePreferences();
        RenderAll();
    }

    private void GridSlider_ValueChanged(object sender, RoutedPropertyChangedEventArgs<double> e)
    {
        if (_updatingControls || !_gridSettingsOpen || !IsLoaded) return;
        var rows = (int)Math.Round(RowsSlider.Value);
        var columns = (int)Math.Round(ColumnsSlider.Value);
        PushUndo();
        var layout = LiveMonitorLayouts.EnsureEditable(Preferences);
        if (!LiveMonitorLayouts.TryResizeGrid(layout, rows, columns, out var moved))
        {
            RollbackFailedMutation();
            layout = ActiveChoice.Layout;
            _updatingControls = true;
            RowsSlider.Value = layout.Rows;
            ColumnsSlider.Value = layout.Columns;
            _updatingControls = false;
            GridSettingsMessage.Foreground = Resource<Brush>("WarningBrush");
            GridSettingsMessage.Text = "Those dimensions cannot hold every tile. Resize or remove a tile before shrinking the grid.";
            return;
        }
        GridSettingsMessage.Foreground = Resource<Brush>("TextSecondaryBrush");
        GridSettingsMessage.Text = $"{columns} × {rows}";
        RowsValue.Text = rows.ToString(CultureInfo.CurrentCulture);
        ColumnsValue.Text = columns.ToString(CultureInfo.CurrentCulture);
        RefreshLayoutSelector();
        RenderGrid();
    }

    private void ScaleSlider_ValueChanged(object sender, RoutedPropertyChangedEventArgs<double> e)
    {
        if (_updatingControls || !_scaleSettingsOpen || !IsLoaded) return;
        Preferences.OverallScale = Math.Clamp(Math.Round(ScaleSlider.Value / 10) / 10, .7, 2);
        ScaleValue.Text = $"{Preferences.OverallScale:P0}";
        ApplyScale();
        Dispatcher.BeginInvoke(EnsureWindowVisible, DispatcherPriority.ContextIdle);
    }

    private void LayoutSelector_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_updatingControls || LayoutSelector.SelectedValue is not string id || id == Preferences.ActiveLayoutId) return;
        Preferences.ActiveLayoutId = id;
        _selectedTileId = null;
        _trendRanges.Clear();
        _undo.Clear();
        SavePreferences();
        RenderAll();
    }

    private void CreateLayout_Click(object sender, RoutedEventArgs e)
    {
        PushUndo();
        LiveMonitorLayouts.Create(Preferences);
        AfterLayoutChange("New layout created. Rename it in the field above.");
    }

    private void DuplicateLayout_Click(object sender, RoutedEventArgs e)
    {
        PushUndo();
        LiveMonitorLayouts.Duplicate(Preferences);
        AfterLayoutChange("Layout duplicated.");
    }

    private void ResetLayout_Click(object sender, RoutedEventArgs e)
    {
        PushUndo();
        if (!LiveMonitorLayouts.ResetActive(Preferences))
        {
            RollbackFailedMutation();
            EditorMessage.Text = "Factory layouts are already at their defaults.";
            return;
        }
        AfterLayoutChange("Layout reset to the factory 3 × 2 grid.");
    }

    private void DeleteLayout_Click(object sender, RoutedEventArgs e)
    {
        PushUndo();
        if (!LiveMonitorLayouts.DeleteActive(Preferences)) { RollbackFailedMutation(); return; }
        _selectedTileId = null;
        AfterLayoutChange("Layout deleted.");
    }

    private void LayoutNameBox_LostKeyboardFocus(object sender, KeyboardFocusChangedEventArgs e)
    {
        if (_updatingControls || ActiveChoice.IsFactory) return;
        var name = LayoutNameBox.Text.Trim();
        if (name.Length == 0 || string.Equals(name, ActiveChoice.Layout.Name, StringComparison.Ordinal)) return;
        PushUndo();
        ActiveChoice.Layout.Name = name;
        AfterLayoutChange("Layout renamed.");
    }

    private void Undo_Click(object sender, RoutedEventArgs e)
    {
        if (_undo.TryPop(out var snapshot))
        {
            Restore(snapshot);
            SavePreferences();
            EditorMessage.Text = "Last layout change undone.";
            RenderAll();
        }
    }

    private void CatalogSearch_TextChanged(object sender, TextChangedEventArgs e)
    {
        if (IsLoaded) RefreshCatalog(force: true);
    }

    private void CatalogAdd_Click(object sender, RoutedEventArgs e)
    {
        if (sender is Button { Tag: string metricId }) AddMetric(metricId);
    }

    private void AddMetric(string metricId)
    {
        PushUndo();
        var layout = LiveMonitorLayouts.EnsureEditable(Preferences);
        if (!LiveMonitorLayouts.TryAddMetric(layout, metricId, out var tileId))
        {
            RollbackFailedMutation();
            EditorMessage.Text = "The grid is full. Increase rows or columns, or remove a tile first.";
            return;
        }
        _selectedTileId = tileId;
        AfterLayoutChange($"{LiveTelemetryCatalog.Get(metricId).Name} added.");
    }

    private void CatalogItem_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        _dragStart = e.GetPosition(this);
        _dragCatalogItem = (sender as FrameworkElement)?.DataContext as CatalogItem;
    }

    private void CatalogItem_MouseMove(object sender, MouseEventArgs e)
    {
        if (e.LeftButton != MouseButtonState.Pressed || _dragCatalogItem is null || !MovedEnough(e.GetPosition(this))) return;
        var data = new DataObject(MetricDragFormat, _dragCatalogItem.MetricId);
        DragDrop.DoDragDrop((DependencyObject)sender, data, DragDropEffects.Copy);
        _dragCatalogItem = null;
    }

    private void Tile_MouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        if (Preferences.IsLocked || sender is not Border { Tag: string tileId }) return;
        _dragStart = e.GetPosition(this);
        _dragTileId = tileId;
        SelectTile(tileId);
    }

    private void Tile_MouseMove(object sender, MouseEventArgs e)
    {
        if (Preferences.IsLocked || e.LeftButton != MouseButtonState.Pressed || _dragTileId is null || !MovedEnough(e.GetPosition(this))) return;
        var data = new DataObject(TileDragFormat, _dragTileId);
        DragDrop.DoDragDrop((DependencyObject)sender, data, DragDropEffects.Move);
        _dragTileId = null;
    }

    private void Tile_MouseLeftButtonUp(object sender, MouseButtonEventArgs e)
    {
        _dragTileId = null;
    }

    private bool MovedEnough(Point current) => Math.Abs(current.X - _dragStart.X) >= SystemParameters.MinimumHorizontalDragDistance || Math.Abs(current.Y - _dragStart.Y) >= SystemParameters.MinimumVerticalDragDistance;

    private void TileGrid_DragOver(object sender, DragEventArgs e)
    {
        if (Preferences.IsLocked) { e.Effects = DragDropEffects.None; return; }
        var (row, column) = DropCell(e.GetPosition(TileGrid));
        ShowDropPreview(row, column);
        e.Effects = e.Data.GetDataPresent(MetricDragFormat) ? DragDropEffects.Copy : e.Data.GetDataPresent(TileDragFormat) ? DragDropEffects.Move : DragDropEffects.None;
        e.Handled = true;
    }

    private void TileGrid_DragLeave(object sender, DragEventArgs e) => RemoveDropPreview();

    private void TileGrid_Drop(object sender, DragEventArgs e)
    {
        var (row, column) = DropCell(e.GetPosition(TileGrid));
        RemoveDropPreview();
        if (e.Data.GetData(MetricDragFormat) is string metricId)
        {
            PushUndo();
            var layout = LiveMonitorLayouts.EnsureEditable(Preferences);
            if (!LiveMonitorLayouts.TryAddMetric(layout, metricId, out var tileId)) { RollbackFailedMutation(); EditorMessage.Text = "The grid is full."; return; }
            _selectedTileId = tileId;
            _ = LiveMonitorLayouts.TryMoveTile(layout, tileId!, row, column);
            AfterLayoutChange($"{LiveTelemetryCatalog.Get(metricId).Name} added.");
        }
        else if (e.Data.GetData(TileDragFormat) is string tileId)
        {
            PushUndo();
            var layout = LiveMonitorLayouts.EnsureEditable(Preferences);
            if (!LiveMonitorLayouts.TryMoveTile(layout, tileId, row, column)) { RollbackFailedMutation(); EditorMessage.Text = "That tile does not fit there."; return; }
            _selectedTileId = tileId;
            AfterLayoutChange("Tiles reflowed without overlap.");
        }
    }

    private (int Row, int Column) DropCell(Point point)
    {
        var layout = ActiveChoice.Layout;
        return (Math.Clamp((int)(point.Y / _cellSize), 0, layout.Rows - 1), Math.Clamp((int)(point.X / _cellSize), 0, layout.Columns - 1));
    }

    private void ShowDropPreview(int row, int column)
    {
        RemoveDropPreview();
        _dropPreview = new Border { Background = Resource<Brush>("AccentSubtleBrush"), BorderBrush = Resource<Brush>("FocusBrush"), BorderThickness = new Thickness(2), CornerRadius = new CornerRadius(6), Margin = new Thickness(3), IsHitTestVisible = false, Opacity = .85 };
        Grid.SetRow(_dropPreview, row);
        Grid.SetColumn(_dropPreview, column);
        Panel.SetZIndex(_dropPreview, 1000);
        TileGrid.Children.Add(_dropPreview);
    }

    private void RemoveDropPreview()
    {
        if (_dropPreview is not null) TileGrid.Children.Remove(_dropPreview);
        _dropPreview = null;
    }

    private void TileOption_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_updatingControls || _selectedTileId is null) return;
        PushUndo();
        var layout = LiveMonitorLayouts.EnsureEditable(Preferences);
        var tile = layout.Tiles.FirstOrDefault(item => item.Id == _selectedTileId);
        if (tile is null) { RollbackFailedMutation(); return; }
        var definition = LiveTelemetryCatalog.Get(tile.MetricId);
        if (StyleSelector.SelectedValue is LiveMonitorDisplayStyle style && definition.Styles.Contains(style)) tile.DisplayStyle = style;
        if (UnitSelector.SelectedItem is string unit && definition.Units.Contains(unit)) tile.Unit = unit;
        if (PrecisionSelector.SelectedItem is int precision) tile.Precision = Math.Clamp(precision, 0, 3);
        if (TrendSelector.SelectedValue is LiveMonitorTrendDuration duration && LiveTelemetryCatalog.TrendDurations(tile.MetricId).Contains(duration)) tile.TrendDuration = duration;
        if (AccentSelector.SelectedValue is string accent && AccentOptions.Any(option => option.Value == accent)) tile.Accent = accent;
        AfterLayoutChange("Tile display updated.");
    }

    private void TileSize_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_updatingControls || _selectedTileId is null || WidthSelector.SelectedItem is not int width || HeightSelector.SelectedItem is not int height) return;
        PushUndo();
        var layout = LiveMonitorLayouts.EnsureEditable(Preferences);
        if (!LiveMonitorLayouts.TryResizeTile(layout, _selectedTileId, height, width))
        {
            RollbackFailedMutation();
            EditorMessage.Text = "That tile size cannot fit in the current grid.";
            RefreshTileEditor();
            return;
        }
        AfterLayoutChange($"Tile resized to {width} × {height}.");
    }

    private void RemoveTile_Click(object sender, RoutedEventArgs e)
    {
        if (_selectedTileId is null) return;
        PushUndo();
        var layout = LiveMonitorLayouts.EnsureEditable(Preferences);
        if (!LiveMonitorLayouts.RemoveTile(layout, _selectedTileId)) { RollbackFailedMutation(); return; }
        _selectedTileId = null;
        AfterLayoutChange("Tile removed.");
    }

    private void Tile_PreviewKeyDown(object sender, KeyEventArgs e)
    {
        if (Preferences.IsLocked || sender is not Border { Tag: string tileId }) return;
        if (e.Key is Key.Enter or Key.Space) { SelectTile(tileId); e.Handled = true; return; }
        if (e.Key == Key.Delete) { _selectedTileId = tileId; RemoveTile_Click(sender, e); e.Handled = true; return; }
        var alt = (Keyboard.Modifiers & ModifierKeys.Alt) != 0;
        var shift = (Keyboard.Modifiers & ModifierKeys.Shift) != 0;
        if (!alt && !shift) return;
        var layout = ActiveChoice.Layout;
        var tile = layout.Tiles.First(item => item.Id == tileId);
        PushUndo();
        var editable = LiveMonitorLayouts.EnsureEditable(Preferences);
        var changed = shift
            ? LiveMonitorLayouts.TryResizeTile(editable, tileId,
                Math.Clamp(tile.RowSpan + (e.Key == Key.Down ? 1 : e.Key == Key.Up ? -1 : 0), 1, editable.Rows),
                Math.Clamp(tile.ColumnSpan + (e.Key == Key.Right ? 1 : e.Key == Key.Left ? -1 : 0), 1, editable.Columns))
            : LiveMonitorLayouts.TryMoveTile(editable, tileId,
                tile.Row + (e.Key == Key.Down ? 1 : e.Key == Key.Up ? -1 : 0),
                tile.Column + (e.Key == Key.Right ? 1 : e.Key == Key.Left ? -1 : 0));
        if (!changed) { RollbackFailedMutation(); EditorMessage.Text = "That keyboard move does not fit."; }
        else AfterLayoutChange(shift ? "Tile resized." : "Tile moved.");
        e.Handled = true;
    }

    private void AfterLayoutChange(string message)
    {
        _trendRanges.Clear();
        SavePreferences();
        EditorMessage.Text = message;
        _updatingControls = true;
        RefreshLayoutSelector();
        _updatingControls = false;
        RenderGrid();
        RefreshTileEditor();
    }

    private void PushUndo()
    {
        _undo.Push(Capture());
        while (_undo.Count > 20)
        {
            var retained = _undo.Take(20).Reverse().ToArray();
            _undo.Clear();
            foreach (var item in retained) _undo.Push(item);
        }
        UndoButton.IsEnabled = true;
    }

    private void RollbackFailedMutation()
    {
        if (!_undo.TryPop(out var snapshot)) return;
        var selectedTileId = _selectedTileId;
        Restore(snapshot);
        if (selectedTileId is not null && ActiveChoice.Layout.Tiles.Any(tile => tile.Id == selectedTileId))
            _selectedTileId = selectedTileId;
        UndoButton.IsEnabled = _undo.Count > 0;
    }

    private void SavePreferences()
    {
        _lastKnownEditorSignature = LiveMonitorLayouts.EditorSignature(Preferences);
        _state.SaveLiveMonitorPreferences();
    }

    private LayoutSnapshot Capture() => new(
        Preferences.ActiveLayoutId,
        Preferences.UserLayouts.Select(LiveMonitorLayouts.Clone).ToList());

    private void Restore(LayoutSnapshot snapshot)
    {
        Preferences.ActiveLayoutId = snapshot.ActiveLayoutId;
        Preferences.UserLayouts = snapshot.UserLayouts.Select(LiveMonitorLayouts.Clone).ToList();
        _selectedTileId = null;
        _trendRanges.Clear();
    }

    private GridSettingsSnapshot CaptureGridSettings() => new(
        Preferences.ActiveLayoutId,
        Preferences.UserLayouts.Select(LiveMonitorLayouts.Clone).ToList());

    private void RestoreGridSettings(GridSettingsSnapshot snapshot)
    {
        Preferences.ActiveLayoutId = snapshot.ActiveLayoutId;
        Preferences.UserLayouts = snapshot.UserLayouts.Select(LiveMonitorLayouts.Clone).ToList();
        _selectedTileId = null;
        _trendRanges.Clear();
    }

    private void RestoreUndoHistory(IReadOnlyList<LayoutSnapshot>? snapshots)
    {
        _undo.Clear();
        if (snapshots is null) return;
        for (var index = snapshots.Count - 1; index >= 0; index--) _undo.Push(snapshots[index]);
    }

    private void CloseButton_Click(object sender, RoutedEventArgs e) => _state.SetLiveMonitorVisible(false);

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
        if (x != rect.Left || y != rect.Top)
        {
            SetWindowPos(handle, IntPtr.Zero, x, y, 0, 0, 0x0001 | 0x0004 | 0x0010);
            Preferences.PlacementRecoveredAt = DateTimeOffset.UtcNow;
            ScheduleSave();
        }
    }

    private void EnsureEditorVisible()
    {
        if (!IsLoaded || EditorPanel.Visibility != Visibility.Visible) return;
        var handle = new WindowInteropHelper(this).Handle;
        if (handle == IntPtr.Zero || !GetWindowRect(handle, out var rect)) return;
        var work = Forms.Screen.FromHandle(handle).WorkingArea;
        var overflow = rect.Bottom - work.Bottom;
        if (overflow > 0)
        {
            SetWindowPos(handle, IntPtr.Zero, rect.Left, Math.Max(work.Top, rect.Top - overflow), 0, 0, 0x0001 | 0x0004 | 0x0010);
            ScheduleSave();
        }
    }

    private void RecoverToNearestDisplay()
    {
        var screens = Forms.Screen.AllScreens;
        var target = screens.FirstOrDefault(screen => string.Equals(screen.DeviceName, Preferences.MonitorDeviceName, StringComparison.OrdinalIgnoreCase));
        if (target is null && Preferences.Left.HasValue && Preferences.Top.HasValue)
        {
            target = screens.OrderBy(screen => DistanceSquared(Preferences.Left.Value, Preferences.Top.Value, screen.WorkingArea.Left, screen.WorkingArea.Top)).FirstOrDefault();
        }
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

    private bool ReducedMotion() => _state.Settings.UseReducedMotion || !SystemParameters.ClientAreaAnimation;

    private Brush AccentFor(LiveMonitorTile tile, LiveTelemetryMetricDefinition definition)
    {
        if (!string.Equals(tile.Accent, "default", StringComparison.OrdinalIgnoreCase))
            return tile.Accent.ToLowerInvariant() switch
            {
                "blue" => Resource<Brush>("TelemetrySpeedBrush"),
                "green" => Resource<Brush>("TelemetryThrottleBrush"),
                "amber" => Resource<Brush>("TelemetryFuelBrush"),
                "coral" => Resource<Brush>("TelemetryBrakeBrush"),
                "violet" => Resource<Brush>("TelemetrySteeringBrush"),
                _ => Resource<Brush>("AccentBrush")
            };
        return definition.Id switch
        {
            "speed" => Resource<Brush>("TelemetrySpeedBrush"),
            "throttle" => Resource<Brush>("TelemetryThrottleBrush"),
            "brake" => Resource<Brush>("TelemetryBrakeBrush"),
            "steering" => Resource<Brush>("TelemetrySteeringBrush"),
            "fuel" or "fuel-laps" => Resource<Brush>("TelemetryFuelBrush"),
            _ => Resource<Brush>("AccentBrush")
        };
    }

    private Button IconButton(string accessibleName, string tooltip, string geometry, RoutedEventHandler click)
    {
        var button = new Button { Style = (Style)FindResource("CompactButton"), ToolTip = tooltip, Content = new Path { Width = 13, Height = 13, Stretch = Stretch.Uniform, Stroke = Resource<Brush>("TextSecondaryBrush"), StrokeThickness = 1.5, Data = Geometry.Parse(geometry) } };
        AutomationProperties.SetName(button, accessibleName);
        button.Click += click;
        return button;
    }

    private static string SourceLabel(LiveMonitorMetricSource source) => source switch
    {
        LiveMonitorMetricSource.Recorded => "Recorded",
        LiveMonitorMetricSource.Calculated => "Calculated",
        _ => "Coach"
    };

    private T Resource<T>(string key) where T : class => (T)FindResource(key);

    private sealed record LayoutItem(string Id, string Name, bool IsFactory)
    {
        public override string ToString() => Name;
    }
    private sealed record CatalogItem(string MetricId, string Name, string SourceLabel, string Detail, string StylesLabel, string AddAccessibleName);
    private sealed record NamedOption<T>(T Value, string Label)
    {
        public override string ToString() => Label;
    }

    private static readonly NamedOption<string>[] AccentOptions =
    [
        new("default", "Automatic"),
        new("blue", "Blue"),
        new("green", "Green"),
        new("amber", "Amber"),
        new("coral", "Coral"),
        new("violet", "Violet")
    ];

    private static string TrendLabel(LiveMonitorTrendDuration duration) => duration switch
    {
        LiveMonitorTrendDuration.Seconds15 => "15 seconds",
        LiveMonitorTrendDuration.Seconds30 => "30 seconds",
        LiveMonitorTrendDuration.Seconds60 => "60 seconds",
        LiveMonitorTrendDuration.OneLap => "1 lap",
        LiveMonitorTrendDuration.ThreeLaps => "3 laps",
        _ => duration.ToString()
    };
    private static string StyleLabel(LiveMonitorDisplayStyle style) => style switch
    {
        LiveMonitorDisplayStyle.Trend => "Chart",
        LiveMonitorDisplayStyle.Status => "Status",
        LiveMonitorDisplayStyle.Gauge => "Gauge",
        LiveMonitorDisplayStyle.Bar => "Bar",
        _ => "Number"
    };
    private sealed record LayoutSnapshot(string ActiveLayoutId, IReadOnlyList<LiveMonitorNamedLayout> UserLayouts);
    private sealed record GridSettingsSnapshot(string ActiveLayoutId, IReadOnlyList<LiveMonitorNamedLayout> UserLayouts);

    [StructLayout(LayoutKind.Sequential)] private struct NativeRect { public int Left; public int Top; public int Right; public int Bottom; }
    [DllImport("dwmapi.dll")] private static extern int DwmSetWindowAttribute(IntPtr handle, int attribute, ref int value, int size);
    [DllImport("user32.dll")] private static extern bool GetWindowRect(IntPtr handle, out NativeRect rect);
    [DllImport("user32.dll", SetLastError = true)] private static extern bool SetWindowPos(IntPtr handle, IntPtr insertAfter, int x, int y, int cx, int cy, uint flags);
}
