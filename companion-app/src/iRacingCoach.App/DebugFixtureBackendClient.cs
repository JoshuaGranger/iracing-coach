#if DEBUG
using System.IO;
using System.Text.Json;
using System.Text.Json.Nodes;
using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;

namespace iRacingCoach.App;

/// <summary>Sanitized, debug-only data for native visual and interaction QA.</summary>
internal sealed class DebugFixtureBackendClient(string scenario, string fixture) : IBackendClient
{
    public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default) =>
        Task.FromResult(fixture == "error"
            ? new BackendHealthResult(false, "iracing-coach-local", "unknown", "unknown", 0, TimeSpan.FromMilliseconds(5), "The local race data service did not start.")
            : new BackendHealthResult(true, "iracing-coach-local", "0.3.0", "2025-06-18", 16, TimeSpan.FromMilliseconds(5)));

    public async Task<JsonElement> CallToolAsync(BackendConfiguration configuration, string toolName, object arguments, CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();
        if (fixture == "error") throw new BackendProtocolException("The local race data service did not start.");
        if (toolName == "analyze_iracing_race" && scenario == "job-running")
            await Task.Delay(TimeSpan.FromSeconds(20), cancellationToken);

        return toolName switch
        {
            "iracing_companion_dashboard" => Load(fixture == "empty" ? "dashboard-empty.json" : "dashboard-populated.json"),
            "discover_iracing_sessions" => fixture == "empty" ? JsonSerializer.SerializeToElement(new { ok = true, sessions = Array.Empty<object>() }) : Load("discovery.json"),
            "analyze_iracing_race" => AnalysisResult(),
            "recommend_open_setup_tuning" => Load("setup-recommendation.json"),
            "catalog_iracing_setups" => fixture is "empty" or "partial" ? JsonSerializer.SerializeToElement(new { ok = true, entries = Array.Empty<object>() }) : SetupCatalog(),
            "garage61_auth_status" => JsonSerializer.SerializeToElement(new { ok = false, configured = false, status = "not_configured" }),
            "iracing_strategy_history" => JsonSerializer.SerializeToElement(new[]
            {
                new { run_number = 1, green_laps = 24, caution_laps = 2, fuel_used_l = 34.9, session_start = "Recent comparable run" },
                new { run_number = 2, green_laps = 19, caution_laps = 7, fuel_used_l = 27.1, session_start = "Earlier comparable run" }
            }),
            _ => JsonSerializer.SerializeToElement(new { ok = true })
        };
    }

    private static JsonElement SetupCatalog() => JsonSerializer.SerializeToElement(new
    {
        ok = true,
        entries = new object[]
        {
            new
            {
                stem = "Race - Balanced",
                car_folder = "synthetic test car",
                pair_status = "Matched to a recorded race",
                filename_identity = new { track_hint = "Synthetic Speedway", role = "race" },
                sources = new { sto = new[] { new { path = @"C:\QA\iRacing\setups\synthetic\Race - Balanced.sto", sha256 = "4ae9b727ba0d4b2a9b7e848c305006179f65252cb126f39e94eb13f3f6e2a4d1" } } },
                parsed_html = new { identity = new { mismatches = new { has_mismatch = false } }, fields = SetupFields("25.0 psi", "58.0%", "3/8 in") }
            },
            new
            {
                stem = "Qualifying - Stable",
                car_folder = "synthetic test car",
                pair_status = "Local setup",
                filename_identity = new { track_hint = "Synthetic Speedway", role = "qualifying" },
                sources = new { sto = new[] { new { path = @"C:\QA\iRacing\setups\synthetic\Qualifying - Stable.sto", sha256 = "77d09f3fe4d9bbdb17bd3280e210bf8a5e8942944835a72d09c4dc87eff59491" } } },
                parsed_html = new { identity = new { mismatches = new { has_mismatch = false } }, fields = SetupFields("24.5 psi", "58.5%", "1/4 in") }
            }
        }
    });

    private static Dictionary<string, object> SetupFields(string pressure, string brakeBias, string bar) => new()
    {
        ["tires.left_front.cold_pressure"] = new { raw = pressure, label = "Left-front cold pressure", section = "Tires", unit = (string?)null },
        ["brakes.brake_bias"] = new { raw = brakeBias, label = "Brake bias", section = "Brakes", unit = (string?)null },
        ["chassis.front_anti_roll_bar"] = new { raw = bar, label = "Front anti-roll bar", section = "Anti-roll bars", unit = (string?)null }
    };

    private static JsonElement AnalysisResult()
    {
        var result = JsonNode.Parse(Load("analyze-repair-heavy.json").GetRawText())!.AsObject();
        var visualization = JsonNode.Parse(Load("track-phase-visualization.json").GetRawText())!.AsObject();
        var traceNodes = new JsonArray();
        var lapNodes = new JsonArray();
        var representativeLaps = new[] { 4, 7, 12, 15, 22, 25 };
        var index = 0;
        foreach (var source in visualization["phase_traces"]!.AsArray())
        {
            var trace = source!.AsObject();
            var points = new JsonArray();
            foreach (var sampleNode in trace["samples"]!.AsArray())
            {
                var sample = sampleNode!.AsObject();
                var brake = sample["brake"]?.GetValue<double>() ?? 0;
                var steering = sample["steering_work_abs_rad"]?.GetValue<double>() ?? 0;
                var speed = sample["speed_mph"]?.GetValue<double>() ?? 0;
                points.Add(new JsonObject
                {
                    ["lap_pct"] = sample["lap_pct"]!.DeepClone(), ["speed_mph"] = speed,
                    ["throttle"] = sample["throttle"]!.DeepClone(), ["brake"] = brake,
                    ["steering_rad"] = steering, ["steering_peak_rad"] = steering,
                    ["tire_stress_proxy"] = Math.Clamp((brake * .45) + (steering * 1.8) + (speed / 190 * .2), 0, 1)
                });
            }
            var lap = representativeLaps[index++];
            traceNodes.Add(new JsonObject
            {
                ["lap"] = lap, ["lap_time_s"] = 31.8 + index * .21, ["complete"] = true, ["flag_state"] = "green",
                ["green_fraction"] = 1.0, ["caution_fraction"] = 0.0, ["pit_time_s"] = 0.0, ["points"] = points
            });
            lapNodes.Add(new JsonObject
            {
                ["lap"] = lap, ["lap_time_s"] = 31.8 + index * .21, ["complete"] = true, ["flag_state"] = "green",
                ["green_fraction"] = 1.0, ["caution_fraction"] = 0.0, ["pit_time_s"] = 0.0,
                ["position"] = new JsonObject { ["start"] = 12 - index, ["end"] = 12 - index },
                ["damage_repair_context"] = new JsonObject { ["automatic_coaching_reference_eligible"] = true, ["exclusion_reason_codes"] = new JsonArray() }
            });
        }
        result["analysis_view"] = new JsonObject
        {
            ["schema_version"] = 1,
            ["identity"] = new JsonObject
            {
                ["track_name"] = "Synthetic Speedway", ["track_config"] = "Oval", ["car_name"] = "NASCAR Test Car",
                ["event_type"] = "Race", ["is_fixed_setup"] = false, ["setup_fingerprint"] = "synthetic-same-setup-fingerprint"
            },
            ["race_summary"] = result["race_summary"]!.DeepClone(), ["laps"] = lapNodes,
            ["race_grades"] = new JsonObject
            {
                ["status"] = "graded", ["overall_grade"] = "B+", ["overall_score"] = 88.1,
                ["categories"] = new JsonArray
                {
                    Grade("pace", "Pace execution", "B+", 88.4, "The median clean lap stayed 0.31 seconds from the fastest usable lap.", "Protect minimum speed in the first load zone."),
                    Grade("consistency", "Consistency and execution", "A-", 91.2, "Clean-lap variation stayed below one percent.", "Make the late-run brake release match the early run."),
                    Grade("tire_management", "Tire management", "B", 84.7, "Pace falloff and the load proxy increased late in the run.", "Reduce entry load before adding steering in both long corners."),
                }
            },
            ["runs"] = new JsonArray
            {
                new JsonObject { ["run_number"] = 1, ["lap_numbers"] = new JsonArray(4, 7, 12, 15, 22, 25), ["green_laps"] = 6.0, ["caution_laps"] = 0.0,
                    ["fuel"] = new JsonObject { ["used_gal"] = 6.2 }, ["pace"] = new JsonObject { ["green_lap_time_slope_s_per_lap"] = .08 },
                    ["tire_measurement_status"] = "measured_after_service", ["damage_repair_context"] = new JsonObject { ["automatic_coaching_reference_eligible"] = true, ["reason_codes"] = new JsonArray() } }
            },
            ["lap_traces"] = new JsonObject { ["tire_stress_definition"] = "Relative controls-and-load proxy; not measured per-lap tire wear.", ["traces"] = traceNodes },
            ["track_profile"] = visualization["track_profile"]!.DeepClone(),
            ["strategy"] = new JsonObject
            {
                ["confidence"] = "medium", ["measured_green_fuel_gal_per_lap"] = .47, ["measured_caution_fuel_gal_per_lap"] = .29,
                ["forecast"] = new JsonObject { ["status"] = "usable", ["all_green_range_laps"] = new JsonArray(58.0, 64.0), ["minimum_stops_all_green"] = 1,
                    ["equal_stint_pit_targets_all_green"] = new JsonArray(38), ["assumptions"] = new JsonArray("Uses the recorded green-flag fuel rate and a two-lap operational reserve.", "Future cautions and traffic remain unknown.") }
            },
            ["damage_repair"] = new JsonObject { ["status"] = "recorded_repair_evidence" },
            ["setup_telemetry"] = new JsonObject(), ["data_quality"] = new JsonObject { ["confidence"] = "high" }
        };
        return JsonSerializer.SerializeToElement(result);
    }

    private static JsonObject Grade(string key, string label, string grade, double score, string explanation, string improvement) => new()
    {
        ["key"] = key, ["label"] = label, ["grade"] = grade, ["score"] = score, ["evidence_type"] = key == "tire_management" ? "proxy" : "derived",
        ["explanation"] = explanation, ["improvement"] = improvement, ["limitations"] = key == "pace" ? "Local pace is capped below A+ without an external field-strength reference." : "Confounded laps are excluded."
    };

    private static JsonElement Load(string name)
    {
        var current = new DirectoryInfo(Directory.GetCurrentDirectory());
        while (current is not null)
        {
            var path = Path.Combine(current.FullName, "companion-app-handoff", "fixtures", name);
            if (File.Exists(path))
            {
                using var document = JsonDocument.Parse(File.ReadAllText(path));
                return document.RootElement.Clone();
            }
            current = current.Parent;
        }
        throw new FileNotFoundException($"The sanitized QA fixture was not found: {name}");
    }
}

internal sealed class DebugFixtureSettingsStore : ISettingsStore
{
    private readonly CompanionSettings _settings;

    public DebugFixtureSettingsStore(bool firstRun)
    {
        var root = Path.Combine(Path.GetTempPath(), "iRacingCoach-VisualQa");
        var iracing = Path.Combine(root, "iRacing");
        Directory.CreateDirectory(iracing);
        Directory.CreateDirectory(Path.Combine(root, "install"));
        _settings = new CompanionSettings
        {
            CoachHome = Path.Combine(root, "Coach"),
            IRacingRoot = iracing,
            IRacingInstallRoot = Path.Combine(root, "install"),
            FirstRunComplete = !firstRun
        };
    }

    public CompanionSettings Load() => _settings;
    public void Save(CompanionSettings settings) { }
}

internal sealed class DebugCredentialStore : IGarage61CredentialStore
{
    public bool IsConfigured => false;
    public string CredentialPath => string.Empty;
    public void Store(string token) { }
    public void Remove() { }
}
#endif
