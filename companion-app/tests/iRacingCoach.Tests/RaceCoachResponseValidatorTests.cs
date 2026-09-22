using System.Text.Json;
using iRacingCoach.Coordinator;
using iRacingCoach.UI;
using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Components.Web;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class RaceCoachResponseValidatorTests
{
    private const string Evidence = """
        {"numeric_packet":{"sections":[
          {"subject":"lap_series","available":true,"claim":{"evidence_id":"ev-laps","evidence_class":"measured"},"series":[
            {"name":"observed_lap_number","values":[116,120,155,159]},
            {"name":"late/median_delta","values":[1.496]},
            {"name":"late/baseline/lap_time","values":[15.2,16.696]}]},
          {"subject":"corner_window","available":true,"claim":{"evidence_id":"ev-corners","evidence_class":"derived"},"series":[
            {"name":"late/area-1/steering/baseline","values":[40.12]},
            {"name":"late/area-1/steering/compared","values":[43.77]},
            {"name":"late/area-1/throttle/compared","values":[80]}]},
          {"subject":"tire","available":false,"claim":{"evidence_id":"ev-tire","evidence_class":"inferred"},"series":[]}
        ]},"investigation":{"comparisons":[{"Id":"late","Corners":[{"Id":"area-1","Label":"First corner"},{"Id":"missing-area"}]}]}}
        """;

    [TestMethod]
    public void SupportedResponsePreservesObservationsActionsCornersStrategyAndEvidenceLabels()
    {
        var response = """
            {"summary":"Median pace slowed by 1.496 s between laps 116 and 159.","actions":[
              {"text":"Compare the earlier and later laps before choosing a driving trial.","evidenceClass":"derived","evidenceIds":["ev-laps"]}],
             "cornerCoaching":[{"cornerId":"late/area-1","phase":"center","instruction":"If the extra steering persists in clean air, try a smoother release and compare exit speed.","evidenceClass":"inferred","evidenceIds":["corner:late/area-1"]}],
             "strategy":{"text":"Review the measured pace before deciding whether another stop would help.","evidenceClass":"inferred","evidenceIds":["investigation:late"]},
             "setupExperiment":null,"limitations":["Traffic prevents a causal conclusion."],"followUpNeeded":true}
            """;
        var result = RaceCoachResponseValidator.ValidateAndFormat(response, Evidence);
        StringAssert.Contains(result, "1.496 s");
        StringAssert.Contains(result, "smoother release");
        StringAssert.Contains(result, "Strategy:");
        StringAssert.Contains(result, "[inferred; corner:late/area-1]");
        StringAssert.Contains(result, "Traffic prevents");
        var presentation = RaceCoachResponseValidator.ForPresentation(result);
        StringAssert.Contains(presentation.Text, "First corner · center:");
        StringAssert.Contains(presentation.Text, "[Inferred]");
        StringAssert.Contains(presentation.Text, "[Calculated]");
        Assert.DoesNotContain("late/area-1", presentation.Text);
        Assert.DoesNotContain("ev-laps", presentation.Text);
        Assert.IsTrue(presentation.References.Any(reference => reference.Id == "corner:late/area-1" && reference.EvidenceClass == "Inferred"));
    }

    [TestMethod]
    public async Task AnswerPresentationKeepsReferencesCollapsedAndHtmlAsText()
    {
        var answer = RaceCoachResponseValidator.ValidateAndFormat(Action("Compare the windows; <script>alert('x')</script>", "ev-laps"), Evidence);
        using var services = new ServiceCollection().AddSingleton<ILoggerFactory>(Microsoft.Extensions.Logging.Abstractions.NullLoggerFactory.Instance).BuildServiceProvider();
        await using var renderer = new HtmlRenderer(services, services.GetRequiredService<ILoggerFactory>());
        var html = await renderer.Dispatcher.InvokeAsync(async () => (await renderer.RenderComponentAsync<RaceCoachAnswer>(ParameterView.FromDictionary(
            new Dictionary<string, object?> { [nameof(RaceCoachAnswer.Answer)] = answer }))).ToHtmlString());
        var mainText = html[..html.IndexOf("</div>", StringComparison.Ordinal)];
        Assert.DoesNotContain("ev-laps", mainText);
        StringAssert.Contains(mainText, "[Calculated]");
        StringAssert.Contains(html, "Evidence references");
        StringAssert.Contains(html, ">ev-laps</code>");
        Assert.IsFalse(System.Text.RegularExpressions.Regex.IsMatch(html, @"<details\b[^>]*\bopen(?:\s|=|>)"));
        Assert.DoesNotContain("<script>", html);
        StringAssert.Contains(html, "&lt;script&gt;");
    }

    [TestMethod]
    public void EvidenceGuideOnlyOffersNumericSupportedComparisonAndCornerAliases()
    {
        var guide = RaceCoachResponseValidator.EvidenceInstructions(Evidence);
        StringAssert.Contains(guide, "ev-laps = measured");
        StringAssert.Contains(guide, "investigation:late = derived");
        StringAssert.Contains(guide, "corner:late/area-1 = derived");
        Assert.DoesNotContain("ev-tire", guide);
        Assert.DoesNotContain("missing-area", guide);
    }

    [TestMethod]
    public void UnknownUnavailableOrMissingEvidenceCannotBecomeAdvice()
    {
        foreach (var id in new[] { "ev-unknown", "ev-tire", "investigation:another-race", "corner:late/missing-area" })
            Reject(Action("Compare the two windows.", id));
        Reject("""{"summary":"Pace changed.","actions":[{"text":"Try a smoother release.","evidenceClass":"inferred","evidenceIds":[]}],"followUpNeeded":false}""");
        Reject(Action("Compare the windows. [measured; ev-forged]", "ev-laps"));
    }

    [TestMethod]
    public void MissingContractFieldsMalformedShapesAndRawProseAreRejected()
    {
        foreach (var response in new[]
        {
            "Try driving faster.", "{}", "[]",
            """{"summary":"Pace changed.","actions":[]}""",
            """{"summary":12,"actions":[],"followUpNeeded":false}""",
            """{"summary":"Pace changed.","actions":[],"followUpNeeded":false,"unexpected":"advice"}"""
        }) Reject(response);
    }

    [TestMethod]
    public void DerivedEvidenceCannotBePresentedAsMeasuredAndNumbersMustComeFromLinkedEvidence()
    {
        Reject(Action("Compare the windows.", "ev-corners", "measured"));
        Reject(Action("The difference was 7.123 seconds.", "ev-laps"));
        Reject(Action("The difference was -1.496 seconds.", "ev-laps"));
        RaceCoachResponseValidator.ValidateAndFormat(Action("The difference was 1.50 seconds.", "ev-laps"), Evidence);
    }

    [TestMethod]
    public void ObservedValueIsNotAuthorizationForExactDrivingTarget()
    {
        Reject(Action("Use 80% throttle through the center.", "ev-corners"));
        Reject(Action("Target 43.77 degrees of steering.", "ev-corners"));
        Reject(Action("80% throttle is required through the center.", "ev-corners"));
        Reject(Action("The recorded 80% throttle should be your target.", "ev-corners"));
        Reject(Action("Eighty percent throttle is required through the center.", "ev-corners"));
        RaceCoachResponseValidator.ValidateAndFormat(Action("Recorded steering rose from 40.12 to 43.77 degrees.", "ev-corners"), Evidence);
        RaceCoachResponseValidator.ValidateAndFormat(Action("Recorded throttle was 80% through this area.", "ev-corners"), Evidence);
    }

    [TestMethod]
    public void UnsupportedDamageFocusGrooveAndSetupClaimsAreRejected()
    {
        Reject(Action("The car was undamaged.", "ev-laps"));
        Reject(Action("You lost focus after the stop.", "ev-laps"));
        Reject(Action("Try the higher groove next time.", "ev-laps"));
        Reject("""{"summary":"Pace changed.","actions":[],"setupExperiment":{"change":"Reduce pressure"},"followUpNeeded":false}""");
        Reject("""{"summary":"Pace changed.","actions":[],"limitations":["Use 80% throttle."],"followUpNeeded":false}""");
    }

    [TestMethod]
    public void CornerAdviceMustReferenceTheExactSupportedTrackArea()
    {
        Reject("""{"summary":"Pace changed.","actions":[],"cornerCoaching":[{"cornerId":"late/area-1","phase":"center","instruction":"Compare release timing.","evidenceClass":"inferred","evidenceIds":["ev-laps"]}],"followUpNeeded":false}""");
        Reject("""{"summary":"Pace changed.","actions":[],"cornerCoaching":[{"cornerId":"late/missing-area","phase":"center","instruction":"Compare release timing.","evidenceClass":"inferred","evidenceIds":["ev-corners"]}],"followUpNeeded":false}""");
    }

    private static string Action(string text, string id, string evidenceClass = "derived") => JsonSerializer.Serialize(new
    {
        summary = "The recorded pace changed.",
        actions = new[] { new { text, evidenceClass, evidenceIds = new[] { id } } },
        followUpNeeded = false
    });
    private static void Reject(string response) => Assert.ThrowsExactly<InvalidDataException>(() => RaceCoachResponseValidator.ValidateAndFormat(response, Evidence));
}
