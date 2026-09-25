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
    // ---------- 5. Sessions gutter after a race ----------

    [TestMethod]
    public async Task SessionsKeepsTheStandardGutterAfterReturningFromARace()
    {
        Assert.AreEqual("page-frame analysis-page-frame", CompanionShell.PageFrameClass("analysis", analysisWorkspaceOpen: true));
        Assert.AreEqual("page-frame", CompanionShell.PageFrameClass("analysis", analysisWorkspaceOpen: false),
            "Back to Sessions keeps the analysis page but must not keep the edge-to-edge race frame.");
        Assert.AreEqual("page-frame", CompanionShell.PageFrameClass("home", analysisWorkspaceOpen: true));

        using var state = NewState();
        state.Navigate("analysis");
        state.CloseAnalysisWorkspace();
        var html = await Render<CompanionShell>(state, new());
        StringAssert.Contains(html, "data-sessions", "Sessions is what the analysis page shows once the race is closed.");
        StringAssert.Contains(html, "<section class=\"page-frame\"");
        Assert.DoesNotContain("analysis-page-frame", html);
    }
}
