using System.Text.Json;
using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;

namespace iRacingCoach.Preview;

internal sealed class FixtureBackendClient : IBackendClient
{
    public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default) =>
        Task.FromResult(new BackendHealthResult(true, "iracing-coach-local", "0.3.0", "2025-06-18", 16, TimeSpan.FromMilliseconds(5)));

    public Task<JsonElement> CallToolAsync(BackendConfiguration configuration, string toolName, object arguments, CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();
        var result = toolName switch
        {
            "iracing_companion_dashboard" => Load("dashboard-populated.json"),
            "discover_iracing_sessions" => Load("discovery.json"),
            "analyze_iracing_race" => Load("analyze-repair-heavy.json"),
            "recommend_open_setup_tuning" => Load("setup-recommendation.json"),
            "catalog_iracing_setups" => JsonSerializer.SerializeToElement(new { ok = true, entries = Array.Empty<object>() }),
            "garage61_auth_status" => JsonSerializer.SerializeToElement(new { ok = false, configured = false, status = "not_configured", message = "Not configured in this sanitized preview." }),
            _ => JsonSerializer.SerializeToElement(new { ok = true })
        };
        return Task.FromResult(result);
    }

    private static JsonElement Load(string name)
    {
        var current = new DirectoryInfo(Directory.GetCurrentDirectory());
        while (current is not null)
        {
            var path = Path.Combine(current.FullName, "companion-app-handoff", "fixtures", name);
            if (File.Exists(path))
            {
                using var document = JsonDocument.Parse(File.ReadAllText(path));
                return document.RootElement.Clone();
            }
            current = current.Parent;
        }
        throw new FileNotFoundException($"The sanitized preview fixture was not found: {name}");
    }
}
