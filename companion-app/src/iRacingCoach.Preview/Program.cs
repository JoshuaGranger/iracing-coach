using iRacingCoach.BackendClient;
using iRacingCoach.Coordinator;
using iRacingCoach.Preview.Components;

var builder = WebApplication.CreateBuilder(args);
// Preview is also launched under Production for release-mode visual QA. Explicitly
// load the build-time manifest so referenced-project and framework assets resolve.
builder.WebHost.UseStaticWebAssets();
builder.Services.AddRazorComponents().AddInteractiveServerComponents();
var isolatedCoachHome = builder.Configuration["qa-coach-home"];
if (string.IsNullOrWhiteSpace(isolatedCoachHome))
{
    builder.Services.AddSingleton<CompanionState>();
}
else
{
    var fullCoachHome = Path.GetFullPath(isolatedCoachHome);
    Directory.CreateDirectory(fullCoachHome);
    builder.Services.AddSingleton(_ => new CompanionState(
        new McpBackendClient(),
        new JsonSettingsStore(Path.Combine(fullCoachHome, "settings.json"))));
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
