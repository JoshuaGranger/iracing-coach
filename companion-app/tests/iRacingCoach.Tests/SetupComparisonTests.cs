using iRacingCoach.Contracts;
using iRacingCoach.UI;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class SetupComparisonTests
{
    private static LocalSetup Setup(string id, params (string Key, string Value)[] fields) => new(id, id, "Toyota Supra Class B", "Bristol Motor Speedway",
        "Recorded", $"{id}.sto", id, "", "", fields.Select(field => new SetupField(field.Key, field.Key, "Tires", field.Value)).ToArray());

    [TestMethod]
    public void MissingOrUnreadableValuesAreUnknownNeverEqual()
    {
        var left = Setup("a", ("lf_pressure", "12.5 psi"), ("rf_pressure", "13.0 psi"), ("lr_pressure", "—"), ("rr_pressure", "14.0 psi"), ("spring", "  400   lb/in "));
        var right = Setup("b", ("lf_pressure", "12.5 psi"), ("rf_pressure", "13.5 psi"), ("lr_pressure", "—"), ("spring", "400 lb/in"), ("bias", "54%"));
        var rows = SetupPage.Differences(left, right).ToDictionary(row => row.Label, row => row.Kind);
        Assert.AreEqual(SetupPage.RowKind.Identical, rows["Tires · lf_pressure"]);
        Assert.AreEqual(SetupPage.RowKind.Changed, rows["Tires · rf_pressure"]);
        Assert.AreEqual(SetupPage.RowKind.Unknown, rows["Tires · lr_pressure"], "Unreadable on both sides is still unknown, not a match.");
        Assert.AreEqual(SetupPage.RowKind.Unknown, rows["Tires · rr_pressure"], "Missing on one side is unknown.");
        Assert.AreEqual(SetupPage.RowKind.Unknown, rows["Tires · bias"]);
        Assert.AreEqual(SetupPage.RowKind.Identical, rows["Tires · spring"], "Whitespace does not create a difference; units stay part of the value.");
    }
}
