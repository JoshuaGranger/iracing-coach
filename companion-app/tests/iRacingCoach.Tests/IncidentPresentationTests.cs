using iRacingCoach.Contracts;
using iRacingCoach.UI;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class IncidentPresentationTests
{
    [TestMethod]
    public void CounterOnlyEvent_StaysCounterAndTimeWithoutGuessingAType()
    {
        var incident = new AnalysisIncident(15, 4, SessionTimeSeconds: 742.85);

        Assert.AreEqual("x4 \u00B7 12:22.8", IncidentPresentation.Describe(incident, includePoints: false));
        Assert.DoesNotContain("Contact", IncidentPresentation.Describe(incident, includePoints: false));
        Assert.DoesNotContain("Loss", IncidentPresentation.Describe(incident, includePoints: false));
    }

    [TestMethod]
    public void ExactRecordedOffTrackState_IsShownWithoutInferringAnotherType()
    {
        var incident = new AnalysisIncident(7, 1, SessionTimeSeconds: 10, TrackLocation: "Off_track");

        Assert.AreEqual("Off track \u00B7 0:10.0", IncidentPresentation.Describe(incident, includePoints: false));
        Assert.IsTrue(IncidentPresentation.IsMeasuredOffTrack("off-track"));
        Assert.IsFalse(IncidentPresentation.IsMeasuredOffTrack("not off track"));
        Assert.IsFalse(IncidentPresentation.IsMeasuredOffTrack("outside track limits"));
    }

    [TestMethod]
    public void ExplicitContactTypeAndTarget_AreCombinedWithoutDuplicateLabels()
    {
        var incident = new AnalysisIncident(
            7,
            4,
            SessionTimeSeconds: 100.5,
            EventType: "contact",
            ContactTarget: "wall");

        Assert.AreEqual("Wall contact \u00B7 1:40.5", IncidentPresentation.Describe(incident, includePoints: false));
        Assert.AreEqual("Wall contact \u00B7 x4 \u00B7 1:40.5", IncidentPresentation.Describe(incident, includePoints: true));
    }

    [TestMethod]
    public void ExplicitLossOfControl_IsPreservedButPointsAloneNeverCreateIt()
    {
        var explicitIncident = new AnalysisIncident(7, 2, EventType: "loss_of_control");
        var counterOnlyIncident = new AnalysisIncident(8, 2);

        Assert.AreEqual("Loss of control", IncidentPresentation.Describe(explicitIncident, includePoints: false));
        Assert.AreEqual("x2", IncidentPresentation.Describe(counterOnlyIncident, includePoints: false));
    }

    [TestMethod]
    public void ExplicitZeroPointEvent_RemainsVisibleWithoutSynthesizingOne()
    {
        var incident = new AnalysisIncident(8, 0, SessionTimeSeconds: 110);

        Assert.AreEqual("x0 \u00B7 1:50.0", IncidentPresentation.Describe(incident, includePoints: false));
    }
}
