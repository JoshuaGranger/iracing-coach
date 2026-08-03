using System.Diagnostics;
using System.Text.Json;
using iRacingCoach.Coordinator;

if (args.Length != 1 || !File.Exists(args[0]))
{
    Console.Error.WriteLine("Pass the path to a cached analyze_iracing_race JSON result.");
    return 2;
}

var fileBytes = File.ReadAllBytes(args[0]);
var bytes = fileBytes.AsSpan().StartsWith(new byte[] { 0xEF, 0xBB, 0xBF })
    ? fileBytes[3..]
    : fileBytes;
for (var index = 0; index < 100; index++)
{
    using var warmup = JsonDocument.Parse(bytes);
    _ = RuntimeMapper.RaceCard(warmup.RootElement);
}

const int iterations = 2_000;
var parseAndMap = new double[iterations];
for (var index = 0; index < iterations; index++)
{
    var started = Stopwatch.GetTimestamp();
    using var document = JsonDocument.Parse(bytes);
    _ = RuntimeMapper.RaceCard(document.RootElement);
    parseAndMap[index] = Stopwatch.GetElapsedTime(started).TotalMilliseconds;
}

Array.Sort(parseAndMap);
Console.WriteLine(JsonSerializer.Serialize(new
{
    workflow = "cached Race Card JSON parse and UI-model mapping",
    fixture = Path.GetFileName(args[0]),
    bytes = bytes.Length,
    iterations,
    median_ms = Math.Round(parseAndMap[iterations / 2], 4),
    p95_ms = Math.Round(parseAndMap[(int)(iterations * 0.95) - 1], 4),
    max_ms = Math.Round(parseAndMap[^1], 4)
}, new JsonSerializerOptions { WriteIndented = true }));

return 0;
