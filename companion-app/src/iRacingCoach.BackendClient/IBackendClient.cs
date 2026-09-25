using System.Text.Json;
using iRacingCoach.Contracts;

namespace iRacingCoach.BackendClient;

public interface IBackendClient
{
    Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default);
    Task<JsonElement> CallToolAsync(BackendConfiguration configuration, string toolName, object arguments, CancellationToken cancellationToken = default);

    /// <summary>
    /// False when this client refuses the tool before dispatch, so callers can skip
    /// optional work instead of provoking and logging a predictable failure.
    /// </summary>
    bool SupportsTool(string toolName) => true;
}
