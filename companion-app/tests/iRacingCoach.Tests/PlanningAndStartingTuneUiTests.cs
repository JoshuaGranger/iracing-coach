
namespace iRacingCoach.Tests;

[TestClass]
public sealed class PlanningAndStartingTuneUiTests
{
    [TestMethod]
    public void StartingTune_UsesAutomaticSeasonAndSearchableLocalEventSelectors()
    {
        var ui = Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI");
        var page = File.ReadAllText(Path.Combine(ui, "SetupPage.razor"));
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));
        var iterationCss = File.ReadAllText(Path.Combine(ui, "wwwroot", "iteration-ux.css"));

        Assert.DoesNotContain("<label>Season", page);
        Assert.DoesNotContain("@bind=\"State.StartingTuneSeason\"", page);
        StringAssert.Contains(page, "FriendlySeason(State.StartingTuneSeason)");
        StringAssert.Contains(page, "class=\"catalog-combobox\"");
        StringAssert.Contains(page, "list=\"starting-tune-cars\"");
        StringAssert.Contains(page, "<datalist id=\"starting-tune-cars\"");
        StringAssert.Contains(page, "list=\"starting-tune-tracks\"");
        StringAssert.Contains(page, "<datalist id=\"starting-tune-tracks\"");
        StringAssert.Contains(page, "aria-invalid=");
        StringAssert.Contains(page, "State.Cars");
        StringAssert.Contains(page, "State.Tracks");
        StringAssert.Contains(page, "@bind:event=\"oninput\"");
        StringAssert.Contains(page, "CatalogSelectionValid");
        Assert.DoesNotContain("class=\"type-or-browse\"", page);
        Assert.DoesNotContain("<select aria-label=\"Browse installed", page);
        StringAssert.Contains(page, "new[] { \"Race\", \"Qualifying\" }");
        StringAssert.Contains(page, "aria-pressed=");
        StringAssert.Contains(page, "CanBuildStartingTune");
        StringAssert.Contains(page, "State.SelectedSetup?.Car ?? recent?.Car");
        StringAssert.Contains(css, ".starting-tune-event-grid");
        StringAssert.Contains(css, ".starting-tune-purpose button.selected");
        StringAssert.Contains(css, ".starting-tune-primary-action");
        StringAssert.Contains(iterationCss, ".starting-tune-step-rail");
        StringAssert.Contains(iterationCss, ".catalog-combobox");
    }

    [TestMethod]
    public void RacePlanning_UsesCompactEvidenceBackedContextAndKeepsManualControls()
    {
        var ui = Path.Combine(CompanionRoot(), "src", "iRacingCoach.UI");
        var page = File.ReadAllText(Path.Combine(ui, "PlanningPage.razor"));
        var css = File.ReadAllText(Path.Combine(ui, "wwwroot", "coach.css"));
        var iterationCss = File.ReadAllText(Path.Combine(ui, "wwwroot", "iteration-ux.css"));

        StringAssert.Contains(page, "planning-setup-card");
        StringAssert.Contains(page, "planning-setup-grid");
        StringAssert.Contains(page, "planning-reference-summary");
        StringAssert.Contains(page, "button primary compact");
        StringAssert.Contains(page, "State.Cars");
        StringAssert.Contains(page, "PlanTracks");
        StringAssert.Contains(page, "MatchingRaces");
        StringAssert.Contains(page, "State.SelectedPlanRaceId");
        StringAssert.Contains(page, "State.PlanDistanceValue");
        StringAssert.Contains(page, "UseReferenceDistance");
        StringAssert.Contains(page, "overview?.ScheduledLaps");
        StringAssert.Contains(page, "overview?.ScheduledMinutes");
        StringAssert.Contains(page, "DeclaredDistance(race.Overview)");
        StringAssert.Contains(page, "DeclaredLapLimit: > 0, DeclaredTimeLimitMinutes: > 0");
        StringAssert.Contains(page, "step=\"@DistanceInputStep\"");
        StringAssert.Contains(page, "double.TryParse");
        Assert.DoesNotContain("State.SelectedPlanRace?.Overview?.RecordedLaps", page);
        StringAssert.Contains(page, "data-planning-decision");
        StringAssert.Contains(page, "OpeningPlanExplanation");
        StringAssert.Contains(page, "@plan.DistanceLabel");
        Assert.DoesNotContain("@plan.ScheduledLaps laps", page);
        StringAssert.Contains(page, "private static int? CalculatedStops");
        StringAssert.Contains(page, "null => \"Finish unresolved\"");
        StringAssert.Contains(page, "null => \"Resolve race distance first\"");
        StringAssert.Contains(page, "Planning assumptions");
        Assert.DoesNotContain(">Start over</button>", page);
        Assert.DoesNotContain("workflow-layout planning-layout", page);
        Assert.DoesNotContain("button primary full", page);
        Assert.DoesNotContain(".planning-layout.setup-only", css);
        StringAssert.Contains(css, ".planning-setup-grid { display: grid;");
        StringAssert.Contains(css, ".planning-results { min-width: 0; display: grid;");
        StringAssert.Contains(css, "@media (max-width: 620px)");
        StringAssert.Contains(iterationCss, ".planning-decision-hero");
    }

    [TestMethod]
    public void InstalledContentDiscovery_DoesNotProbeMappedOrUnavailableDrives()
    {
        var root = CompanionRoot();
        var models = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.Contracts", "Models.cs"));
        var paths = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.Contracts", "CompanionPathProvider.cs"));
        var state = File.ReadAllText(Path.Combine(root, "src", "iRacingCoach.Coordinator", "CompanionState.cs"));

        StringAssert.Contains(paths, "DriveInfo.GetDrives()");
        StringAssert.Contains(paths, ".Where(candidate => candidate.DriveType == DriveType.Fixed && candidate.IsReady)");
        StringAssert.Contains(models, "foreach (var driveRoot in pathProvider.FixedDriveRoots)");
        StringAssert.Contains(state, "foreach (var driveRoot in _pathProvider.FixedDriveRoots)");
        Assert.DoesNotContain("DriveInfo.GetDrives()", state, StringComparison.Ordinal);
        foreach (var source in new[] { paths, models, state })
        {
            Assert.DoesNotContain("Directory.GetLogicalDrives()", source, StringComparison.Ordinal);
        }

        StringAssert.Contains(state, "foreach (var directory in LeafInstalledContent(root, 3))");
        StringAssert.Contains(state, "Path.GetRelativePath(root, directory)");
        Assert.DoesNotContain("foreach (var directory in Directory.EnumerateDirectories(root))", state, StringComparison.Ordinal);
    }

    private static string CompanionRoot() => TestRepositoryPaths.CompanionAppRoot;
}
