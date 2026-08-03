using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Components.Web;
using iRacingCoach.Coordinator;

namespace iRacingCoach.UI;

public sealed class CoachErrorBoundary : ErrorBoundary
{
    [Inject] public CompanionState State { get; set; } = null!;

    protected override Task OnErrorAsync(Exception exception)
    {
        State.ReportUnhandledException("user interface", exception);
        return Task.CompletedTask;
    }
}
