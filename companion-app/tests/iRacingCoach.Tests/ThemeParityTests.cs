using System.Runtime.CompilerServices;
using System.Text.Json;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class ThemeParityTests
{
    [TestMethod]
    public void CanonicalTheme_IsSynchronizedAcrossWebNativeAndTelemetryPopout()
    {
        var companion = CompanionRoot();
        using var theme = JsonDocument.Parse(File.ReadAllText(Path.Combine(companion, "..", "companion-app-contract", "config", "theme.dark.json")));
        var colors = theme.RootElement.GetProperty("colors");
        var web = File.ReadAllText(Path.Combine(companion, "src", "iRacingCoach.UI", "wwwroot", "theme.generated.css"));
        var native = File.ReadAllText(Path.Combine(companion, "src", "iRacingCoach.App", "Theme.Generated.xaml"));
        var monitor = File.ReadAllText(Path.Combine(companion, "src", "iRacingCoach.App", "LiveMonitorWindow.xaml"));

        var mappings = new[]
        {
            (Json: "app", Css: "app", Xaml: "App"),
            (Json: "navigation", Css: "navigation", Xaml: "Navigation"),
            (Json: "surface1", Css: "surface-1", Xaml: "Surface1"),
            (Json: "surface2", Css: "surface-2", Xaml: "Surface2"),
            (Json: "surface3", Css: "surface-3", Xaml: "Surface3"),
            (Json: "surfaceHover", Css: "surface-hover", Xaml: "SurfaceHover"),
            (Json: "borderSubtle", Css: "border-subtle", Xaml: "BorderSubtle"),
            (Json: "borderStrong", Css: "border-strong", Xaml: "BorderStrong"),
            (Json: "textPrimary", Css: "text-primary", Xaml: "TextPrimary"),
            (Json: "textSecondary", Css: "text-secondary", Xaml: "TextSecondary"),
            (Json: "textMuted", Css: "text-muted", Xaml: "TextMuted"),
            (Json: "textDisabled", Css: "text-disabled", Xaml: "TextDisabled"),
            (Json: "accent", Css: "accent", Xaml: "Accent"),
            (Json: "accentFill", Css: "accent-fill", Xaml: "AccentFill"),
            (Json: "accentHover", Css: "accent-hover", Xaml: "AccentHover"),
            (Json: "accentSubtle", Css: "accent-subtle", Xaml: "AccentSubtle"),
            (Json: "focus", Css: "focus", Xaml: "Focus"),
            (Json: "success", Css: "success", Xaml: "Success"),
            (Json: "warning", Css: "warning", Xaml: "Warning"),
            (Json: "danger", Css: "danger", Xaml: "Danger"),
            (Json: "info", Css: "info", Xaml: "Info"),
            (Json: "unavailable", Css: "unavailable", Xaml: "Unavailable"),
            (Json: "chartBackground", Css: "chart-background", Xaml: "ChartBackground"),
            (Json: "chartGrid", Css: "chart-grid", Xaml: "ChartGrid"),
            (Json: "chartReference", Css: "chart-reference", Xaml: "ChartReference")
        };

        foreach (var mapping in mappings)
        {
            var value = colors.GetProperty(mapping.Json).GetString();
            StringAssert.Contains(web, $"--{mapping.Css}: {value};", $"Web token {mapping.Css} drifted from the canonical theme.");
            StringAssert.Contains(native, $"x:Key=\"{mapping.Xaml}Color\">{value}</Color>", $"Native color {mapping.Xaml} drifted from the canonical theme.");
        }

        var accent = colors.GetProperty("accent").GetString();
        Assert.AreEqual(accent, ThemeColors.Get(ThemeColors.DefaultId).Accent);
        Assert.AreEqual(ThemeColors.DefaultCustomHex, accent);
        StringAssert.Contains(monitor, "Color=\"{StaticResource ChartBackgroundColor}\"");
        StringAssert.Contains(monitor, "Color=\"{StaticResource ChartGridColor}\"");
        StringAssert.Contains(monitor, "Color=\"{StaticResource AccentColor}\"");
        Assert.DoesNotContain("Color=\"#65D0B6\"", monitor);
    }

    private static string CompanionRoot([CallerFilePath] string source = "") =>
        Path.GetFullPath(Path.Combine(Path.GetDirectoryName(source)!, "..", ".."));
}
