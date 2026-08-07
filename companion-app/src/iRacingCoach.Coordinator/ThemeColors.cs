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

    public static IReadOnlyList<ThemeColorPalette> Choices { get; } =
    [
        new(DefaultId, "Mint", "#65D0B6", "#287565", "#328B78", "#17302C", "#91E8D3"),
        new("blue", "Blue", "#61C8F5", "#315F8B", "#3B73A6", "#182B3B", "#A8E0FA"),
        new("violet", "Violet", "#C89CFF", "#68458E", "#7B55A4", "#2D2139", "#E0C5FF"),
        new("coral", "Coral", "#FF8B7D", "#93463F", "#AA574D", "#3C2421", "#FFB8AF"),
        new("amber", "Amber", "#F4C45F", "#856525", "#9B782F", "#372D18", "#FFE29B")
    ];

    public static string Normalize(string? id)
    {
        var requested = id?.Trim();
        return Choices.FirstOrDefault(choice => string.Equals(choice.Id, requested, StringComparison.OrdinalIgnoreCase))?.Id
            ?? DefaultId;
    }

    public static ThemeColorPalette Get(string? id)
    {
        var normalized = Normalize(id);
        return Choices.First(choice => string.Equals(choice.Id, normalized, StringComparison.Ordinal));
    }

    public static string CssClass(string? id) => $"theme-color-{Normalize(id)}";
}
