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

public sealed partial class UiPolishRegressionTests
{
    // ---------- 3. Plan builds from the recording ----------

    [TestMethod]
    public async Task PlanBuildsOnceFromDefaultsAndWaitsForTheButtonAfterAnEdit()
    {
        var backend = new PlanBackend();
        using var state = NewState(backend);
        Assert.IsFalse(state.PlanReadyToBuildFromDefaults, "Nothing to build before the inputs are complete.");
        await state.BuildPlanFromDefaultsAsync();
        Assert.IsEmpty(backend.Calls);

        state.SelectedPlanCarId = "new-car";
        state.SelectedPlanTrack = "installed:new-layout";
        state.PlanDistanceValue = 50;
        Assert.IsFalse(state.PlanReadyToBuildFromDefaults, "Without a recording, explicit fuel values are required first.");
        state.PlanGreenBurnGallons = .4;
        state.PlanStartFuelGallons = 20;
        Assert.IsTrue(state.PlanReadyToBuildFromDefaults);

        await state.BuildPlanFromDefaultsAsync();
        Assert.HasCount(1, backend.Calls);
        Assert.IsTrue(state.PlanGenerated, state.PlanMessage);
        await state.BuildPlanFromDefaultsAsync();
        Assert.HasCount(1, backend.Calls, "An existing plan is not rebuilt on every render.");

        state.PlanGreenBurnGallons = .5;
        Assert.IsFalse(state.PlanGenerated);
        Assert.IsFalse(state.PlanReadyToBuildFromDefaults, "After an edit the driver rebuilds with the button.");
        await state.BuildPlanFromDefaultsAsync();
        Assert.HasCount(1, backend.Calls);
        await state.GeneratePlanAsync();
        Assert.HasCount(2, backend.Calls);

        state.SelectedPlanTrack = "installed:another-layout";
        Assert.IsTrue(state.PlanReadyToBuildFromDefaults, "A new event context builds again.");
        await state.BuildPlanFromDefaultsAsync();
        Assert.HasCount(3, backend.Calls);
        Assert.IsTrue(state.PlanGenerated);
    }

    [TestMethod]
    public async Task PlanPageShowsProgressWhileBuildingAndBuildsFromItsDefaults()
    {
        var backend = new PlanBackend { Hold = true };
        using var state = NewState(backend);
        state.SelectedPlanCarId = "new-car";
        state.SelectedPlanTrack = "installed:new-layout";
        state.PlanDistanceValue = 50;
        state.PlanGreenBurnGallons = .4;
        state.PlanStartFuelGallons = 20;
        var pending = state.BuildPlanFromDefaultsAsync();
        await backend.Started.Task.WaitAsync(TimeSpan.FromSeconds(3));

        var html = await Render<PlanningPage>(state, new() { [nameof(PlanningPage.State)] = state });
        StringAssert.Contains(html, "data-plan-building");
        StringAssert.Contains(html, "Building the plan");
        StringAssert.Contains(html, ">Building…</button>");

        backend.Release.TrySetResult();
        await pending;
        html = await Render<PlanningPage>(state, new() { [nameof(PlanningPage.State)] = state });
        Assert.DoesNotContain("data-plan-building", html);
        StringAssert.Contains(html, "data-planning-decision");
        StringAssert.Contains(html, ">Rebuild plan</button>");

        var page = File.ReadAllText(Path.Combine(UiRoot(), "PlanningPage.razor"));
        StringAssert.Contains(page, "protected override async Task OnAfterRenderAsync(bool firstRender)");
        StringAssert.Contains(page, "await State.BuildPlanFromDefaultsAsync();");
    }
}
