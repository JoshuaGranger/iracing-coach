using iRacingCoach.Coordinator;
using iRacingCoach.Preview;
using iRacingCoach.Preview.Components;

var builder = WebApplication.CreateBuilder(args);
builder.Services.AddRazorComponents().AddInteractiveServerComponents();
if (string.Equals(Environment.GetEnvironmentVariable("IRACING_COACH_PREVIEW_FIXTURE"), "populated", StringComparison.OrdinalIgnoreCase))
{
    builder.Services.AddSingleton(_ => new CompanionState(new FixtureBackendClient(), null, new FixtureLiveTelemetrySource()));
}
else
{
    builder.Services.AddSingleton<CompanionState>();
}

var app = builder.Build();
if (!app.Environment.IsDevelopment())
{
    app.UseExceptionHandler("/Error", createScopeForErrors: true);
}
app.UseAntiforgery();
app.MapStaticAssets();
app.MapRazorComponents<App>().AddInteractiveServerRenderMode();
app.Run();
