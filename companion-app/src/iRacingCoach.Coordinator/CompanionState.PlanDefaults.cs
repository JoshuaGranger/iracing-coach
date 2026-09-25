namespace iRacingCoach.Coordinator;

public sealed partial class CompanionState
{
    private string? _planBuiltFromDefaultsFor;

    private string PlanContextKey => string.Join('\u001f', SelectedPlanCarId, SelectedPlanTrack, PlanSetupType, SelectedPlanRaceId);

    /// <summary>
    /// True when every input is filled (from the reference recording, or explicit fuel values
    /// without one) and this car, layout, setup and reference have not been built automatically yet.
    /// </summary>
    public bool PlanReadyToBuildFromDefaults =>
        CanBuildPlan && !PlanGenerated
        && (SelectedPlanRace is not null || PlanGreenBurnGallons is > 0 && PlanStartFuelGallons is > 0)
        && !string.Equals(_planBuiltFromDefaultsFor, PlanContextKey, StringComparison.Ordinal);

    /// <summary>
    /// Builds the plan once per reference context so Plan opens with an answer. After the driver
    /// edits a value in the same context, the plan waits for Build plan.
    /// </summary>
    public Task BuildPlanFromDefaultsAsync(CancellationToken cancellationToken = default)
    {
        if (!PlanReadyToBuildFromDefaults) return Task.CompletedTask;
        _planBuiltFromDefaultsFor = PlanContextKey;
        return GeneratePlanAsync(cancellationToken);
    }
}
