namespace iRacingCoach.Coordinator;

public sealed record ThemeColorPalette(
    string Id,
    string Name,
    string Accent,
    string Fill,
    string Hover,
    string Subtle,
    string Focus);

public static class ThemeColors
{
    public const string DefaultId = "mint";
    public const string CustomId = "custom";
    public const string DefaultCustomHex = "#5CE8C3";

    public static IReadOnlyList<ThemeColorPalette> Choices { get; } =
    [
        new(DefaultId, "Mint", "#5CE8C3", "#167A66", "#20947B", "#153832", "#9FF8DF"),
        new("blue", "Blue", "#61C8F5", "#315F8B", "#3B73A6", "#182B3B", "#A8E0FA"),
        new("violet", "Violet", "#C89CFF", "#68458E", "#7B55A4", "#2D2139", "#E0C5FF"),
        new("coral", "Coral", "#FF8B7D", "#93463F", "#AA574D", "#3C2421", "#FFB8AF"),
        new("amber", "Amber", "#F4C45F", "#856525", "#9B782F", "#372D18", "#FFE29B"),
        new("lime", "Lime", "#A8D968", "#587B2E", "#688F39", "#27351B", "#CDEFA3"),
        new("rose", "Rose", "#F08FB8", "#884267", "#9E5078", "#38202C", "#F8BDD5"),
        new("indigo", "Indigo", "#879DFF", "#465AA0", "#556CBB", "#202844", "#BBC6FF"),
        new("copper", "Copper", "#DB9966", "#7A4F2F", "#90603B", "#342419", "#F0BF98")
    ];

    public static string Normalize(string? id)
    {
        var requested = id?.Trim();
        if (string.Equals(requested, CustomId, StringComparison.OrdinalIgnoreCase)) return CustomId;
        return Choices.FirstOrDefault(choice => string.Equals(choice.Id, requested, StringComparison.OrdinalIgnoreCase))?.Id
            ?? DefaultId;
    }

    public static ThemeColorPalette Get(string? id, string? customHex = null)
    {
        var normalized = Normalize(id);
        if (string.Equals(normalized, CustomId, StringComparison.Ordinal))
            return BuildCustom(customHex);
        return Choices.First(choice => string.Equals(choice.Id, normalized, StringComparison.Ordinal));
    }

    public static string CssClass(string? id) => $"theme-color-{Normalize(id)}";

    public static string NormalizeCustomHex(string? value) =>
        TryParseHex(value, out var color) ? Hex(color) : DefaultCustomHex;

    public static string CssVariables(string? id, string? customHex)
    {
        if (!string.Equals(Normalize(id), CustomId, StringComparison.Ordinal)) return string.Empty;
        var palette = BuildCustom(customHex);
        return $"--accent:{palette.Accent};--accent-fill:{palette.Fill};--accent-hover:{palette.Hover};--accent-subtle:{palette.Subtle};--focus:{palette.Focus};";
    }

    private static ThemeColorPalette BuildCustom(string? value)
    {
        _ = TryParseHex(NormalizeCustomHex(value), out var requested);
        var accent = KeepAccentVisible(requested);
        var app = (R: 11, G: 16, B: 21);
        var white = (R: 246, G: 248, B: 250);
        var fill = Mix(app, accent, .46);
        while (Contrast(fill, white) < 4.5) fill = Mix(app, fill, .86);
        return new(
            CustomId,
            "Custom",
            Hex(accent),
            Hex(fill),
            Hex(Mix(app, accent, .58)),
            Hex(Mix(app, accent, .17)),
            Hex(Mix(accent, white, .32)));
    }

    private static (int R, int G, int B) KeepAccentVisible((int R, int G, int B) color)
    {
        var white = (R: 232, G: 233, B: 231);
        var black = (R: 20, G: 21, B: 23);
        while (Luminance(color) < .24) color = Mix(color, white, .12);
        while (Luminance(color) > .76) color = Mix(color, black, .08);
        return color;
    }

    private static bool TryParseHex(string? value, out (int R, int G, int B) color)
    {
        var text = value?.Trim();
        if (text is { Length: 7 } && text[0] == '#' &&
            int.TryParse(text.AsSpan(1, 2), System.Globalization.NumberStyles.HexNumber, null, out var r) &&
            int.TryParse(text.AsSpan(3, 2), System.Globalization.NumberStyles.HexNumber, null, out var g) &&
            int.TryParse(text.AsSpan(5, 2), System.Globalization.NumberStyles.HexNumber, null, out var b))
        {
            color = (r, g, b);
            return true;
        }
        color = default;
        return false;
    }

    private static (int R, int G, int B) Mix((int R, int G, int B) first, (int R, int G, int B) second, double secondWeight) =>
        ((int)Math.Round(first.R + ((second.R - first.R) * secondWeight)),
         (int)Math.Round(first.G + ((second.G - first.G) * secondWeight)),
         (int)Math.Round(first.B + ((second.B - first.B) * secondWeight)));

    private static double Contrast((int R, int G, int B) first, (int R, int G, int B) second)
    {
        var a = Luminance(first);
        var b = Luminance(second);
        return (Math.Max(a, b) + .05) / (Math.Min(a, b) + .05);
    }

    private static double Luminance((int R, int G, int B) color)
    {
        static double Linear(int channel)
        {
            var value = channel / 255d;
            return value <= .04045 ? value / 12.92 : Math.Pow((value + .055) / 1.055, 2.4);
        }
        return (.2126 * Linear(color.R)) + (.7152 * Linear(color.G)) + (.0722 * Linear(color.B));
    }

    private static string Hex((int R, int G, int B) color) => $"#{color.R:X2}{color.G:X2}{color.B:X2}";
}
