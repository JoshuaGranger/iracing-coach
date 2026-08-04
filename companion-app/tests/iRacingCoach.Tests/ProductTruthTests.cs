using System.Text.Json;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class ProductTruthTests
{
    [TestMethod]
    public void RaceGrades_AlwaysExposeFiveCategoriesAndDoNotScoreMissingEvidence()
    {
        using var response = JsonDocument.Parse("""{"analysis_id":"grade-test","analysis_view":{"race_grades":{"overall_grade":"B","categories":[{"key":"pace","label":"Pace execution","grade":"B","score":84,"evidence_type":"derived","explanation":"Supported pace evidence.","improvement":"Review pace.","limitations":"Local only."}]}}}""");

        var workspace = RuntimeMapper.Analysis(response.RootElement);

        Assert.HasCount(5, workspace.Grades);
        Assert.AreEqual("B", workspace.OverallGrade);
        Assert.IsTrue(workspace.Grades.Single(grade => grade.Key == "pace").Available);
        var missing = workspace.Grades.Single(grade => grade.Key == "strategy");
        Assert.IsFalse(missing.Available);
        Assert.IsNull(missing.Score);
        Assert.AreEqual("Not graded", missing.Grade);
        StringAssert.Contains(missing.Limitation, "does not affect");
    }

    [TestMethod]
    public void StartingTunePackage_IsExplicitlyNonSimulatorAndKeepsRaceAndQualifyingSourcesSeparate()
    {
        using var response = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "fixtures", "setup-package.json")));

        var race = RuntimeMapper.SetupPackage(response.RootElement, "Synthetic car", "Synthetic track", "2026S3", "Race");
        Assert.IsFalse(race.SimulatorSetupProduced);
        Assert.IsFalse(race.SourceFilesModified);
        Assert.AreEqual("Race", race.Purpose);
        var exception = Assert.Throws<InvalidDataException>(() => RuntimeMapper.SetupPackage(response.RootElement, "Synthetic car", "Synthetic track", "2026S3", "Qualifying"));
        StringAssert.Contains(exception.Message, "will not be relabeled");
        Assert.HasCount(5, race.BaselineChecks);
    }
}
