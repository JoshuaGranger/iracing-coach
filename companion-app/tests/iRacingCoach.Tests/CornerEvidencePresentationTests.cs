using iRacingCoach.UI;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class CornerEvidencePresentationTests
{
    [TestMethod]
    public void ObservedValuesKeepUnitsZeroValuesAndCensoredTiming()
    {
        var text = CornerEvidencePresentation.Describe("Obs E107.1/M105.6/X106.4; B0; BOc/BR19.3/TI19.7");
        Assert.Contains("entry 107.1 mph", text);
        Assert.Contains("minimum 105.6 mph", text);
        Assert.Contains("exit 106.4 mph", text);
        Assert.Contains("Peak brake: 0%", text);
        Assert.Contains("Brake onset: not captured within this section", text);
        Assert.Contains("brake release: 19.3% of lap", text);
        Assert.Contains("turn-in: 19.7% of lap", text);
    }

    [TestMethod]
    public void MissingOrSingleObservationValuesNeverBecomeTargetsOrZeros()
    {
        var text = CornerEvidencePresentation.Describe("Obs E-/M100/X-; B-; BO-/BRn1/TIc");
        Assert.Contains("entry unavailable", text);
        Assert.Contains("Peak brake: unavailable", text);
        Assert.Contains("brake release: only one observation", text);
        Assert.DoesNotContain("0%", text);
        const string target = "Target E126/M113/X123 mph; B21%";
        Assert.AreEqual(target, CornerEvidencePresentation.Describe(target));
        Assert.AreEqual("Unknown format", CornerEvidencePresentation.Describe("Unknown format"));
    }
}
