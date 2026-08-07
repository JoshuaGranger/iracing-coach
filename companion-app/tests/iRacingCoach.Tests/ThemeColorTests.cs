using System.Runtime.CompilerServices;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class ThemeColorTests
{
    [TestMethod]
    public void ThemeColors_ExposeCuratedPalettesAndNormalizeUnknownValuesToMint()
    {
        CollectionAssert.AreEqual(
            new[] { "mint", "blue", "violet", "coral", "amber" },
            ThemeColors.Choices.Select(choice => choice.Id).ToArray());
        Assert.AreEqual("violet", ThemeColors.Normalize(" VIOLET "));
        Assert.AreEqual(ThemeColors.DefaultId, ThemeColors.Normalize("not-a-theme"));
        Assert.AreEqual("theme-color-coral", ThemeColors.CssClass("coral"));
        Assert.IsTrue(ThemeColors.Choices.All(choice =>
            choice.Accent.StartsWith('#') && choice.Fill.StartsWith('#') &&
            choice.Subtle.StartsWith('#') && choice.Focus.StartsWith('#')));
    }

    [TestMethod]
    public void SettingsStore_PersistsThemeAndRepairsUnknownPortableValues()
    {
        var directory = Path.Combine(Path.GetTempPath(), "iracing-coach-theme-tests", Guid.NewGuid().ToString("N"));
        var path = Path.Combine(directory, "settings.json");
        var store = new JsonSettingsStore(path);
        var settings = new CompanionSettings { ThemeColor = "violet" };

        store.Save(settings);
        Assert.AreEqual("violet", store.Load().ThemeColor);
        StringAssert.Contains(File.ReadAllText(path), "\"themeColor\": \"violet\"");

        File.WriteAllText(path, "{\"themeColor\":\"unknown-color\"}");
        Assert.AreEqual(ThemeColors.DefaultId, store.Load().ThemeColor);
        StringAssert.Contains(File.ReadAllText(path), "\"themeColor\": \"mint\"");
    }

    [TestMethod]
    public void SettingsAndShell_ProvideImmediateAccessibleThemeSelection()
    {
        var ui = Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI");
        var settings = File.ReadAllText(Path.Combine(ui, "SettingsPage.razor"));
        var shell = File.ReadAllText(Path.Combine(ui, "CompanionShell.razor"));
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));
        var state = File.ReadAllText(Path.Combine(CompanionRoot(), "src", "iRacingCoach.Coordinator", "CompanionState.cs"));

        StringAssert.Contains(settings, "@foreach (var color in ThemeColors.Choices)");
        StringAssert.Contains(settings, "aria-pressed=\"@selected\"");
        StringAssert.Contains(settings, "@onclick=\"() => State.SetThemeColor(color.Id)\"");
        StringAssert.Contains(shell, "@ThemeColors.CssClass(State.Settings.ThemeColor)");
        StringAssert.Contains(state, "public void SetThemeColor(string colorId)");
        StringAssert.Contains(state, "Settings.ThemeColor = ThemeColors.DefaultId;");
        foreach (var id in ThemeColors.Choices.Select(choice => choice.Id))
            StringAssert.Contains(css, $".app-shell.theme-color-{id}");
        StringAssert.Contains(css, ".theme-color-swatch.active");
        StringAssert.Contains(css, ".theme-color-swatch:hover");
    }

    [TestMethod]
    public void NativeTelemetryPopout_UsesTheSameSelectedThemePalette()
    {
        var app = Path.Combine(CompanionRoot(), "src", "iRacingCoach.App");
        var xaml = File.ReadAllText(Path.Combine(app, "LiveMonitorWindow.xaml"));
        var code = File.ReadAllText(Path.Combine(app, "LiveMonitorWindow.xaml.cs"));

        foreach (var resource in new[] { "MonitorAccentBrush", "MonitorAccentFillBrush", "MonitorAccentSubtleBrush", "MonitorFocusBrush" })
            StringAssert.Contains(xaml, $"x:Key=\"{resource}\"");
        StringAssert.Contains(code, "ThemeColors.Get(_state.Settings.ThemeColor)");
        StringAssert.Contains(code, "Resources[\"MonitorAccentBrush\"] = ThemeBrush(theme.Accent);");
        StringAssert.Contains(code, "Resources[\"MonitorAccentFillBrush\"] = ThemeBrush(theme.Fill);");
        StringAssert.Contains(code, "Resources[\"MonitorAccentSubtleBrush\"] = ThemeBrush(theme.Subtle);");
        StringAssert.Contains(code, "Resources[\"MonitorFocusBrush\"] = ThemeBrush(theme.Focus);");
        StringAssert.Contains(code, "|theme:{ThemeColors.Normalize(_state.Settings.ThemeColor)}");
    }

    private static string CompanionRoot([CallerFilePath] string source = "") =>
        Path.GetFullPath(Path.Combine(Path.GetDirectoryName(source)!, "..", ".."));
}
