using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

/// <summary>A diagnostics warning paired with the one action that can clear it.</summary>
public sealed record SettingsIssue(string Label, string Detail, SettingsRecovery Recovery);

public enum SettingsRecovery { None, CheckAgain, Repair, Folders, Backup, Logs }

public sealed partial class CompanionState
{
    /// <summary>
    /// Warnings from the installation checks, each with a recovery the app can actually
    /// perform. Healthy checks and optional providers never appear here; local analysis
    /// does not depend on ChatGPT, Garage61 or the internet.
    /// </summary>
    public IReadOnlyList<SettingsIssue> SettingsIssues => IssuesFrom(Diagnostics);

    public string ConnectionsSummary =>
        $"ChatGPT {(CoachEngine.ChatGptConnected ? "connected" : "not connected")} · Garage61 {Garage61StatusLabel.ToLowerInvariant()}";

    public static IReadOnlyList<SettingsIssue> IssuesFrom(IEnumerable<DiagnosticFact> facts) => facts
        .Where(fact => fact.State == "warning")
        .Select(fact => new SettingsIssue(fact.Label, fact.Value, fact.Label switch
        {
            "Race analysis service" or "Service protocol" => SettingsRecovery.Repair,
            "iRacing folder" or "Coach folder" => SettingsRecovery.Folders,
            "Copy readiness" => SettingsRecovery.Backup,
            "Replay capture" => SettingsRecovery.Logs,
            "Race recordings" or "Race sessions" or "Local setups" or "Installed cars" or "Installed tracks" => SettingsRecovery.CheckAgain,
            _ => SettingsRecovery.None
        }))
        // Coach Engine only serves ChatGPT explanations; Connections owns its repair.
        .Where(issue => issue.Label != "Coach Engine")
        // Two views of one service failure share a recovery; show it once.
        .GroupBy(issue => issue.Recovery == SettingsRecovery.Repair ? "service" : issue.Label)
        .Select(group => group.First())
        .ToArray();
}
