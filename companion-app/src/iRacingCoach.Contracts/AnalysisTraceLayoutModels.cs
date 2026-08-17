using System.Text.Json;
using System.Text.Json.Serialization;

namespace iRacingCoach.Contracts;

/// <summary>
/// Portable, Race Analysis-only trace layout preferences. These deliberately
/// remain separate from Live telemetry dashboards because the two workspaces
/// have different rendering and editing contracts.
/// </summary>
public sealed class AnalysisTraceLayoutSet
{
    public const string FactoryDefaultId = "factory-default";

    public string ActiveLayoutId { get; set; } = FactoryDefaultId;
    public List<AnalysisTraceNamedLayout> UserLayouts { get; set; } = [];
    /// <summary>
    /// Records that the pre-named-layout bridge has been considered. This is
    /// deliberately durable: an empty modern layout set can mean the user
    /// deleted every custom layout, not that legacy import should run again.
    /// </summary>
    public bool LegacyLayoutImportCompleted { get; set; }
    [JsonExtensionData]
    public Dictionary<string, JsonElement>? ExtensionData { get; set; }
}

public sealed class AnalysisTraceNamedLayout
{
    public string Id { get; set; } = $"analysis-layout-{Guid.NewGuid():N}";
    public string Name { get; set; } = "Custom";
    public AnalysisTraceLayout Layout { get; set; } = new();
    [JsonExtensionData]
    public Dictionary<string, JsonElement>? ExtensionData { get; set; }
}
