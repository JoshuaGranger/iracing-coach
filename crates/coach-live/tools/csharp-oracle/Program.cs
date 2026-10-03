using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Oracle;

var output = args.Length > 0 ? args[0] : "vectors.json";
var commit = args.Length > 1 ? args[1] : "unknown";
var scenarios = Scenarios.All();
var sb = new StringBuilder();
sb.Append("{\"format\":1,\"generated_by\":\"throwaway C# harness: real IRacingSdkTelemetrySource over a synthetic SDK map, real LiveTelemetryService and LiveTelemetryCatalog; companion-app at git ")
  .Append(commit).Append("\",\"scenarios\":[\n");
var first = true;
foreach (var scn in scenarios)
{
    if (!first) sb.Append(",\n");
    first = false;
    sb.Append(Run(scn).ToJsonString(new JsonSerializerOptions { Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping }));
}
var plain = new JsonSerializerOptions { Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping };
sb.Append("\n],\"catalog\":").Append(Emit.Catalog().ToJsonString(plain)).Append("}\n");
File.WriteAllText(output, sb.ToString(), new UTF8Encoding(false));
Console.WriteLine($"{scenarios.Count} scenarios, {sb.Length} bytes -> {output}");

static JsonObject Run(Scn scn)
{
    var vars = Vars.All.Where(v => !scn.Omit.Contains(v.Name)).ToList();
    using var runner = new Runner(vars, scn.SafeGlance);
    var steps = new JsonArray();
    Dictionary<string, JsonNode?>? prevSample = null, prevExpect = null;
    for (var i = 0; i < scn.Steps.Count; i++)
    {
        var step = scn.Steps[i];
        string? text = null;
        var stepJson = new JsonObject();
        if (i == 0) text = scn.Session.Text();
        if (step.NewSession is not null)
        {
            text = step.NewSession.Text();
            stepJson["session"] = step.NewSession.ToJson();
        }
        var outcome = runner.Step(step.T, step.Connected, step.Paused, text, step.Values, scn.TickRate);
        var sample = Emit.Sample(scn, step);
        var expect = Emit.Expect(outcome.State, outcome.Changed, runner.RustEpoch);
        stepJson["t"] = step.T;
        stepJson["connected"] = step.Connected;
        stepJson["paused"] = step.Paused;
        stepJson["tick"] = step.Connected ? runner.Tick : 0;
        stepJson["tick_rate"] = step.Connected ? scn.TickRate : 0;
        stepJson["set"] = Delta(sample, prevSample);
        stepJson["expect"] = Delta(expect, prevExpect);
        if (step.Tiles && step.Connected) stepJson["tiles"] = Emit.Tiles(outcome.State);
        else if (step.Tiles) stepJson["tiles"] = Emit.Tiles(outcome.State);
        prevSample = sample;
        prevExpect = expect;
        steps.Add(stepJson);
    }
    return new JsonObject
    {
        ["name"] = scn.Name,
        ["origin"] = scn.Origin,
        ["safe_glance"] = scn.SafeGlance,
        ["session"] = scn.Session.ToJson(),
        ["omitted_variables"] = new JsonArray(scn.Omit.Select(n => (JsonNode?)n).ToArray()),
        ["steps"] = steps,
    };
}

static JsonObject Delta(Dictionary<string, JsonNode?> current, Dictionary<string, JsonNode?>? previous)
{
    var delta = new JsonObject();
    foreach (var (key, value) in current)
    {
        var text = value?.ToJsonString();
        if (previous is not null && previous.TryGetValue(key, out var old) && old?.ToJsonString() == text) continue;
        delta[key] = value?.DeepClone();
    }
    return delta;
}
