using System.IO;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using WpfBrushes = System.Windows.Media.Brushes;
using WpfButton = System.Windows.Controls.Button;
using WpfColor = System.Windows.Media.Color;
using WpfFontFamily = System.Windows.Media.FontFamily;

namespace iRacingCoach.App;

internal sealed class StartupErrorWindow : Window
{
    public StartupErrorWindow(Exception error)
    {
        Title = "iRacing Coach · Startup problem";
        Width = 680;
        Height = 410;
        MinWidth = 560;
        MinHeight = 340;
        WindowStartupLocation = WindowStartupLocation.CenterScreen;
        Background = Brush(20, 22, 25);
        Foreground = Brush(232, 233, 231);
        try { Icon = BitmapFrame.Create(new Uri("pack://application:,,,/Assets/iRacingCoach.ico", UriKind.Absolute)); } catch (IOException) { }

        var close = new WpfButton
        {
            Content = "Close",
            MinWidth = 110,
            Height = 38,
            HorizontalAlignment = System.Windows.HorizontalAlignment.Right,
            Background = Brush(57, 94, 139),
            Foreground = WpfBrushes.White,
            BorderBrush = Brush(79, 118, 164),
            FontWeight = FontWeights.SemiBold
        };
        close.Click += (_, _) => Close();

        var panel = new StackPanel { MaxWidth = 590, VerticalAlignment = VerticalAlignment.Center };
        panel.Children.Add(new TextBlock
        {
            Text = "iRacing Coach could not start",
            FontSize = 28,
            FontWeight = FontWeights.SemiBold,
            Margin = new Thickness(0, 0, 0, 12)
        });
        panel.Children.Add(new TextBlock
        {
            Text = FriendlyMessage(error),
            Foreground = Brush(185, 190, 198),
            FontSize = 15,
            TextWrapping = TextWrapping.Wrap,
            LineHeight = 23,
            Margin = new Thickness(0, 0, 0, 18)
        });
        panel.Children.Add(new Border
        {
            Background = Brush(27, 30, 34),
            BorderBrush = Brush(55, 61, 69),
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(6),
            Padding = new Thickness(14),
            Margin = new Thickness(0, 0, 0, 20),
            Child = new TextBlock
            {
                Text = error.Message,
                Foreground = Brush(214, 183, 122),
                FontFamily = new WpfFontFamily("Consolas"),
                FontSize = 12,
                TextWrapping = TextWrapping.Wrap
            }
        });
        panel.Children.Add(close);
        Content = new Border { Padding = new Thickness(44), Child = panel };
        SourceInitialized += (_, _) => ApplyDarkTitleBar();
    }

    private static string FriendlyMessage(Exception error) => error is DirectoryNotFoundException or FileNotFoundException or ArgumentException
        ? "Check the launch paths and try again. If a QA path contains spaces, pass the complete --name=value argument in quotes. Your normal Coach data was not changed."
        : "A startup check failed before the main window opened. Your normal Coach data was not changed.";

    private static SolidColorBrush Brush(byte red, byte green, byte blue) => new(WpfColor.FromRgb(red, green, blue));

    private void ApplyDarkTitleBar()
    {
        var handle = new System.Windows.Interop.WindowInteropHelper(this).Handle;
        var enabled = 1;
        if (DwmSetWindowAttribute(handle, 20, ref enabled, sizeof(int)) != 0) _ = DwmSetWindowAttribute(handle, 19, ref enabled, sizeof(int));
        var caption = ColorRef(23, 25, 28);
        var text = ColorRef(232, 233, 231);
        var border = ColorRef(48, 53, 59);
        _ = DwmSetWindowAttribute(handle, 35, ref caption, sizeof(int));
        _ = DwmSetWindowAttribute(handle, 36, ref text, sizeof(int));
        _ = DwmSetWindowAttribute(handle, 34, ref border, sizeof(int));
    }

    private static int ColorRef(byte red, byte green, byte blue) => red | (green << 8) | (blue << 16);
    [DllImport("dwmapi.dll")] private static extern int DwmSetWindowAttribute(IntPtr window, int attribute, ref int value, int valueSize);
}
