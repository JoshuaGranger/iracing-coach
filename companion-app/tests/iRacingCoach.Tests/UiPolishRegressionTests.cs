using System.Collections.Concurrent;
using System.Text.Json;
using System.Text.RegularExpressions;
using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;
using iRacingCoach.UI;
using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Components.Web;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using Microsoft.JSInterop;

namespace iRacingCoach.Tests;

/// <summary>Regressions for the September 24 native polish review at 1280 px.</summary>
[TestClass]
public sealed partial class UiPolishRegressionTests
{
    // ---------- Helpers ----------

    private static string UiRoot() => Path.Combine(TestRepositoryPaths.CompanionAppRoot, "src", "iRacingCoach.UI");

    private static string TempRoot()
    {
        var root = Path.Combine(Path.GetTempPath(), "iracing-ui-polish-tests", Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(root);
        return root;
    }

    private static CompanionState NewState(IBackendClient? backend = null)
    {
        var root = TempRoot();
        var state = new CompanionState(backend ?? new PlanBackend(), null, new DisconnectedLiveTelemetrySource(), null, allowExternalHostActions: false);
        state.Settings.CoachHome = Path.Combine(root, "iRacing Coach");
        state.Settings.LocalStateRootOverride = Path.Combine(root, "local");
        return state;
    }

    private static async Task<string> Render<TComponent>(CompanionState state, Dictionary<string, object?> parameters) where TComponent : IComponent
    {
        using var services = new ServiceCollection()
            .AddSingleton<ILoggerFactory>(Microsoft.Extensions.Logging.Abstractions.NullLoggerFactory.Instance)
            .AddSingleton<IJSRuntime>(new InertJsRuntime())
            .AddSingleton<IErrorBoundaryLogger>(new SilentErrorBoundaryLogger())
            .AddSingleton(state)
            .BuildServiceProvider();
        await using var renderer = new HtmlRenderer(services, services.GetRequiredService<ILoggerFactory>());
        var html = await renderer.Dispatcher.InvokeAsync(async () =>
            (await renderer.RenderComponentAsync<TComponent>(ParameterView.FromDictionary(parameters))).ToHtmlString());
        return System.Net.WebUtility.HtmlDecode(html);
    }

    private sealed class InertJsRuntime : IJSRuntime
    {
        public ValueTask<TValue> InvokeAsync<TValue>(string identifier, object?[]? args) => ValueTask.FromResult(default(TValue)!);
        public ValueTask<TValue> InvokeAsync<TValue>(string identifier, CancellationToken cancellationToken, object?[]? args) => ValueTask.FromResult(default(TValue)!);
    }

    private sealed class SilentErrorBoundaryLogger : IErrorBoundaryLogger
    {
        public ValueTask LogErrorAsync(Exception exception) => ValueTask.CompletedTask;
    }

    private sealed class PlanBackend : IBackendClient
    {
        public ConcurrentQueue<string> Calls { get; } = new();
        public bool Hold { get; init; }
        public TaskCompletionSource Started { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public TaskCompletionSource Release { get; } = new(TaskCreationOptions.RunContinuationsAsynchronously);
        public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default) =>
            Task.FromResult(new BackendHealthResult(true, "test", "1", "test", 1, TimeSpan.Zero));
        public async Task<JsonElement> CallToolAsync(BackendConfiguration configuration, string toolName, object arguments, CancellationToken cancellationToken = default)
        {
            if (toolName != "plan_iracing_race") throw new InvalidOperationException($"Unexpected tool: {toolName}");
            Calls.Enqueue(toolName);
            Started.TrySetResult();
            if (Hold) await Release.Task.WaitAsync(cancellationToken);
            using var document = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "fixtures", "event-plan-estimate.json")));
            return document.RootElement.Clone();
        }
    }
}
