using System.Collections;
using System.Text.Json;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;
using iRacingCoach.UI;
using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Components.Web;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class RaceInvestigationPerformanceTests
{
    [TestMethod]
    public async Task ThousandLapChartScansSeriesLinearlyAndKeepsCoordinateEndpoints()
    {
        var (investigation, laps, points) = LongInvestigation();
        using var services = new ServiceCollection().AddSingleton<ILoggerFactory>(Microsoft.Extensions.Logging.Abstractions.NullLoggerFactory.Instance).BuildServiceProvider();
        await using var renderer = new HtmlRenderer(services, services.GetRequiredService<ILoggerFactory>());
        var html = await renderer.Dispatcher.InvokeAsync(async () =>
        {
            var output = await renderer.RenderComponentAsync<RacePaceInvestigation>(ParameterView.FromDictionary(
                new Dictionary<string, object?> { [nameof(RacePaceInvestigation.Investigation)] = investigation }));
            return output.ToHtmlString();
        });
        Assert.IsLessThan(20 * laps.Count, laps.Reads, "Race chart must not rescan every lap for each plotted point.");
        Assert.IsLessThan(20 * points.Count, points.Reads, "Track chart must not rescan every condition point for each plotted point.");
        StringAssert.Contains(html, "cx=\"50\" cy=\"151\"");
        StringAssert.Contains(html, "cx=\"978\" cy=\"35\"");
        StringAssert.Contains(html, "cx=\"50\" cy=\"62\"");
        StringAssert.Contains(html, "cx=\"978\" cy=\"14\"");
        StringAssert.Contains(html, "50,190");
        StringAssert.Contains(html, "978,212");
    }

    [TestMethod]
    public void RepeatedParametersReuseBoundsUntilInvestigationReferenceChanges()
    {
        var (investigation, laps, points) = LongInvestigation();
        var component = new ParameterHarness();
        component.Apply(investigation);
        var lapReads = laps.Reads;
        var pointReads = points.Reads;
        for (var index = 0; index < 20; index++) component.Apply(investigation);
        Assert.AreEqual(lapReads, laps.Reads);
        Assert.AreEqual(pointReads, points.Reads);
        component.Apply(investigation with { Summary = "Refreshed analysis" });
        Assert.IsGreaterThan(lapReads, laps.Reads);
        Assert.IsGreaterThan(pointReads, points.Reads);
    }

    private static (RaceInvestigation Investigation, CountedList<RaceInvestigationLap> Laps, CountedList<RaceTrackEvolutionPoint> Points) LongInvestigation()
    {
        using var document = JsonDocument.Parse(RaceInvestigationMapperTests.InvestigationJson);
        var original = RaceInvestigationMapper.Map(document.RootElement)!;
        var laps = new CountedList<RaceInvestigationLap>(Enumerable.Range(1, 1000).Select(lap =>
            new RaceInvestigationLap(lap, 15d + (lap - 1) / 999d, 1 + (lap - 1) / 333, "green", true, [], 1, true)).ToArray());
        var points = new CountedList<RaceTrackEvolutionPoint>(Enumerable.Range(1, 1000).Select(lap =>
            new RaceTrackEvolutionPoint(lap, 70d + (lap - 1) * 10d / 999d, (lap - 1) * 100d / 999d)).ToArray());
        return (original with { Laps = laps, TrackEvolution = original.TrackEvolution! with { Points = points, UsageSource = "TrackUsage" } }, laps, points);
    }

    private sealed class ParameterHarness : RacePaceInvestigation
    {
        public void Apply(RaceInvestigation investigation) { Investigation = investigation; OnParametersSet(); }
    }

    private sealed class CountedList<T>(IReadOnlyList<T> items) : IReadOnlyList<T>
    {
        public int Reads { get; private set; }
        public int Count => items.Count;
        public T this[int index] { get { Reads++; return items[index]; } }
        public IEnumerator<T> GetEnumerator()
        {
            foreach (var item in items) { Reads++; yield return item; }
        }
        IEnumerator IEnumerable.GetEnumerator() => GetEnumerator();
    }
}
