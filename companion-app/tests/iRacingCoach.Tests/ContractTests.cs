using System.Text.Json;
using iRacingCoach.Contracts;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class ContractTests
{
    [TestMethod]
    public void EvidenceTags_AreStableAndLiteral()
    {
        Assert.AreEqual("[M]", EvidenceKind.Measured.Tag());
        Assert.AreEqual("[D]", EvidenceKind.Derived.Tag());
        Assert.AreEqual("[I]", EvidenceKind.Inferred.Tag());
        Assert.AreEqual("[P]", EvidenceKind.Proxy.Tag());
        Assert.AreEqual("[U]", EvidenceKind.Unavailable.Tag());
    }

    [TestMethod]
    public void EveryCheckedInContract_LoadsAsJson()
    {
        var contractRoot = Path.Combine(AppContext.BaseDirectory, "fixtures", "contracts");
        var files = Directory.GetFiles(contractRoot, "*.json", SearchOption.AllDirectories);
        Assert.IsGreaterThanOrEqualTo(16, files.Length);
        foreach (var path in files)
        {
            using var document = JsonDocument.Parse(File.ReadAllText(path));
            Assert.AreEqual(JsonValueKind.Object, document.RootElement.ValueKind, Path.GetFileName(path));
        }
    }

    [TestMethod]
    public void ThemeTextContrast_MeetsNormalTextThreshold()
    {
        var path = Path.Combine(AppContext.BaseDirectory, "fixtures", "theme.dark.json");
        using var document = JsonDocument.Parse(File.ReadAllText(path));
        var colors = document.RootElement.GetProperty("colors");
        var app = colors.GetProperty("app").GetString()!;
        var surface = colors.GetProperty("surface1").GetString()!;
        var primary = colors.GetProperty("textPrimary").GetString()!;
        var secondary = colors.GetProperty("textSecondary").GetString()!;
        Assert.IsGreaterThanOrEqualTo(4.5, Contrast(primary, app));
        Assert.IsGreaterThanOrEqualTo(4.5, Contrast(secondary, surface));
    }

    private static double Contrast(string foreground, string background)
    {
        var high = Math.Max(Luminance(foreground), Luminance(background));
        var low = Math.Min(Luminance(foreground), Luminance(background));
        return (high + 0.05) / (low + 0.05);
    }

    private static double Luminance(string hex)
    {
        var channels = new[] { 1, 3, 5 }
            .Select(index => Convert.ToInt32(hex.Substring(index, 2), 16) / 255d)
            .Select(channel => channel <= 0.04045 ? channel / 12.92 : Math.Pow((channel + 0.055) / 1.055, 2.4))
            .ToArray();
        return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
    }
}
