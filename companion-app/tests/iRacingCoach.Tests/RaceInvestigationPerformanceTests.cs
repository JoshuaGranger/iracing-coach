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
    public async Task ThousandLapProgressionScansLapsLinearlyAndKeepsEndpoints()
    {
        var (investigation, laps) = LongInvestigation();
        var html = await Render<RaceProgressionChart>(new() { [nameof(RaceProgressionChart.Investigation)] = investigation });
        Assert.IsLessThan(40 * laps.Count, laps.Reads, "The progression chart must not rescan every lap for each plotted point.");
        StringAssert.Contains(html, "data-lap=\"1\"");
        StringAssert.Contains(html, "data-lap=\"999\"");
        Assert.IsFalse(html.Contains("data-lap=\"1000\"", StringComparison.Ordinal), "A caution lap is not plotted as pace.");
        StringAssert.Contains(html, ">P1<");
    }

    [TestMethod]
    public async Task ThousandLapRunOverlayScansLapsLinearly()
    {
        var (investigation, laps) = LongInvestigation();
        var html = await Render<RunOverlayChart>(new() { [nameof(RunOverlayChart.Investigation)] = investigation });
        Assert.IsLessThan(10 * laps.Count, laps.Reads, "The run overlay must filter laps once per parameter change.");
        StringAssert.Contains(html, "data-lap=\"999\"");
        StringAssert.Contains(html, "Green laps into run");
    }

    internal static async Task<string> Render<TComponent>(Dictionary<string, object?> parameters) where TComponent : IComponent
    {
        using var services = new ServiceCollection().AddSingleton<ILoggerFactory>(Microsoft.Extensions.Logging.Abstractions.NullLoggerFactory.Instance).BuildServiceProvider();
        await using var renderer = new HtmlRenderer(services, services.GetRequiredService<ILoggerFactory>());
        // Decode entities so assertions read the text a driver sees (the renderer encodes "·").
        return System.Net.WebUtility.HtmlDecode(await renderer.Dispatcher.InvokeAsync(async () =>
            (await renderer.RenderComponentAsync<TComponent>(ParameterView.FromDictionary(parameters))).ToHtmlString()));
    }

    private static (RaceInvestigation Investigation, CountedList<RaceInvestigationLap> Laps) LongInvestigation()
    {
        var original = RaceInvestigationMapperTests.CurrentFixture();
        var laps = new CountedList<RaceInvestigationLap>(Enumerable.Range(1, 1000).Select(lap =>
            new RaceInvestigationLap(lap, 15d + (lap - 1) / 999d, 1 + (lap - 1) / 333, lap % 50 == 0 ? "caution" : "green", true, [], 1, lap % 50 != 0,
                GreenExposure: lap)).ToArray());
        return (original with { Laps = laps }, laps);
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
