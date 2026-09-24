using Microsoft.AspNetCore.Components;
using Microsoft.JSInterop;

namespace iRacingCoach.UI;

/// <summary>
/// Renders SVG at the measured pixel size of <see cref="Target"/> so dots stay
/// round and text stays legible at every window size and Windows scaling.
/// Static rendering (tests, prerender) uses the default size.
/// </summary>
public abstract class MeasuredChart : ComponentBase, IAsyncDisposable
{
    [Inject] private IServiceProvider Services { get; set; } = null!;
    protected ElementReference Target;
    protected double Width { get; private set; } = 960;
    protected double Height { get; private set; } = 360;
    private DotNetObjectReference<MeasuredChart>? _reference;
    private IJSRuntime? _js;
    private string? _observed;

    protected override async Task OnAfterRenderAsync(bool firstRender)
    {
        _js ??= Services.GetService(typeof(IJSRuntime)) as IJSRuntime;
        if (_js is null || string.IsNullOrEmpty(Target.Id) || Target.Id == _observed) return;
        try
        {
            _reference ??= DotNetObjectReference.Create(this);
            _observed = Target.Id;
            // Read the laid-out size directly; the observer only reports later changes and
            // is not delivered while the window is hidden.
            var size = await _js.InvokeAsync<double[]>("raceInspection.size", Target);
            if (size is [> 1, > 1] measured) await OnMeasured(measured[0], measured[1]);
            await _js.InvokeVoidAsync("raceInspection.observeSize", Target, _reference);
        }
        catch (JSDisconnectedException) { }
        catch (InvalidOperationException) { }
    }

    [JSInvokable]
    public Task OnMeasured(double width, double height)
    {
        if (Math.Abs(width - Width) < 1 && Math.Abs(height - Height) < 1) return Task.CompletedTask;
        Width = Math.Max(200, width);
        Height = Math.Max(120, height);
        return InvokeAsync(StateHasChanged);
    }

    public virtual async ValueTask DisposeAsync()
    {
        if (_js is not null && !string.IsNullOrEmpty(Target.Id))
        {
            try { await _js.InvokeVoidAsync("raceInspection.unobserve", Target); }
            catch (JSDisconnectedException) { }
            catch (InvalidOperationException) { }
            catch (TaskCanceledException) { }
        }
        _reference?.Dispose();
        GC.SuppressFinalize(this);
    }
}
