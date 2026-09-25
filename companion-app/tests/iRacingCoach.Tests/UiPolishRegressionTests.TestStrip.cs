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
    // ---------- 2. Test strip ----------

    [TestMethod]
    public async Task CompactTestStripKeepsTheWholeSuccessRuleReadable()
    {
        using var state = NewState();
        var trial = new RaceTrialSpec("section_time", "Section time", .25, .345, "C", "C", .25, .345,
            1.672, [35, 36, 37], 1.640, [131, 132, 133], 21.8, 23.8, .032, 3, "median_below_baseline", 15.4, 104, "v1");
        var action = new RaceInvestigationAction(1, "Test a smoother steering arc", "Hold one steering input.", [], null, [35, 131], .19, .345,
            ["steering"], SectionId: "C", Trial: trial);
        var html = await Render<TrialAction>(state, new()
        {
            [nameof(TrialAction.Action)] = action,
            [nameof(TrialAction.ComparisonId)] = "r1-r2",
            [nameof(TrialAction.Compact)] = true
        });

        var rule = Regex.Match(html, "<span class=\"ri-test-rule\">(?<text>[^<]*)</span>").Groups["text"].Value;
        Assert.AreEqual("success: 3 screened laps at least 0.032 s under 1.672 s", rule, "The success rule is one unit that wraps as a whole.");
        StringAssert.Contains(html, "Section time 25.0–34.5% · 21.8–23.8 green laps into a run ·");

        var css = File.ReadAllText(Path.Combine(UiRoot(), "wwwroot", "race-investigation.css"));
        var compactTerms = Regex.Match(css, @"\.ri-test\.compact \.ri-test-copy small \{[^}]*\}").Value;
        Assert.IsFalse(string.IsNullOrEmpty(compactTerms));
        Assert.DoesNotContain("text-overflow: ellipsis", compactTerms, "The strip must never cut the success rule at 1280 px.");
        Assert.DoesNotContain("white-space: nowrap", compactTerms);
        StringAssert.Contains(compactTerms, "grid-column: 1 / -1", "Terms take their own full-width line beneath the title.");
    }
}
