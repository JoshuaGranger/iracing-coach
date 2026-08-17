using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class CoachToolAuthorityConsumerTests
{
    [TestMethod]
    public void InstalledEffectiveList_IsExactReadOnlyUnion()
    {
        var expected = new[]
        {
            "analyze_iracing_race",
            "catalog_iracing_setups",
            "discover_iracing_sessions",
            "find_iracing_telemetry_events",
            "iracing_companion_dashboard",
            "iracing_setup_history",
            "iracing_strategy_history",
            "query_iracing_telemetry",
            "recommend_open_setup_tuning",
            "recommend_structured_open_setup_tuning"
        };

        CollectionAssert.AreEqual(expected, CoachEngineProvisioner.EnabledCoachTools);
    }

    [TestMethod]
    public void GeneratedConfig_EnforcesExactToolListAndReadOnlyRuntime()
    {
        var settings = new CompanionSettings
        {
            IRacingRoot = @"C:\iRacing",
            IRacingInstallRoot = @"C:\iRacingInstall",
            CoachHome = @"C:\Coach"
        };

        var config = CoachEngineProvisioner.BuildConfig(settings, @"C:\app\start-mcp.ps1");

        StringAssert.Contains(config, "approval_policy = \"never\"");
        StringAssert.Contains(config, "sandbox_mode = \"read-only\"");
        StringAssert.Contains(config, "web_search = \"disabled\"");
        StringAssert.Contains(config, "enabled_tools = [");
        foreach (var tool in CoachEngineProvisioner.EnabledCoachTools)
            StringAssert.Contains(config, $"\"{tool}\"");
        foreach (var denied in new[]
        {
            "archive_iracing_knowledge", "build_open_setup_package", "garage61_auth_status",
            "inventory_iracing_data", "record_open_setup_feedback", "sync_garage61_references"
        })
        {
            Assert.IsFalse(config.Contains($"\"{denied}\"", StringComparison.Ordinal));
        }
    }
}
