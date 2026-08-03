using System.Text.Json;
using iRacingCoach.Contracts;

namespace iRacingCoach.BackendClient;

public interface IBackendClient
{
    Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default);
    Task<JsonElement> CallToolAsync(BackendConfiguration configuration, string toolName, object arguments, CancellationToken cancellationToken = default);
}
