using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Nodes;
using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

/// <summary>
/// File-driven, offline backend used only when the explicit QA launch contract is supplied.
/// It never starts the production backend and never contacts Garage61 or an AI service.
/// </summary>
public sealed class QaFixtureBackendClient : IBackendClient
{
    private readonly string _packetRoot;
    private readonly string _fixturesRoot;
    private readonly string _scenario;

    public QaFixtureBackendClient(string packetOrFixtureRoot, string? scenario = null)
    {
        if (string.IsNullOrWhiteSpace(packetOrFixtureRoot)) throw new ArgumentException("A QA fixture root is required.", nameof(packetOrFixtureRoot));
        _packetRoot = Path.GetFullPath(packetOrFixtureRoot);
        _fixturesRoot = Directory.Exists(Path.Combine(_packetRoot, "fixtures")) ? Path.Combine(_packetRoot, "fixtures") : _packetRoot;
        _scenario = string.IsNullOrWhiteSpace(scenario) ? "kentucky" : scenario.Trim().ToLowerInvariant();
        RequireFile("real-derived", "kentucky-fixed-race-analysis.json");
        RequireFile("real-derived", "kentucky-selected-lap-traces.json");
        RequireFile("real-derived", "nhms-open-race-analysis.json");
        RequireFile("synthetic", "live-sdk-replay.json");
    }

    public string PacketRoot => _packetRoot;
    public string FixturesRoot => _fixturesRoot;

    public Task<BackendHealthResult> CheckHealthAsync(BackendConfiguration configuration, CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();
        return Task.FromResult(_scenario == "backend-error"
            ? new BackendHealthResult(false, "iRacing Coach QA fixture", "fixture-error", "offline", 0, TimeSpan.FromMilliseconds(1), "Injected offline backend failure.")
            : new BackendHealthResult(true, "iRacing Coach QA fixture", "final-product-v1", "offline", 16, TimeSpan.FromMilliseconds(1)));
    }

    public Task<JsonElement> CallToolAsync(BackendConfiguration configuration, string toolName, object arguments, CancellationToken cancellationToken = default)
    {
        cancellationToken.ThrowIfCancellationRequested();
        if (_scenario == "backend-error") throw new BackendProtocolException("Injected fixture backend failure from mcp-tool-error.json.");

        var result = toolName switch
        {
            "iracing_companion_dashboard" => _scenario == "empty" ? LoadElement("synthetic", "dashboard-empty.json") : BuildDashboard(),
            "discover_iracing_sessions" => _scenario == "empty" ? EmptyDiscovery() : BuildDiscovery(),
            "analyze_iracing_race" => BuildSelectedAnalysis(arguments),
            "iracing_strategy_history" => BuildStrategyHistory(arguments),
            "catalog_iracing_setups" => _scenario == "empty" ? JsonSerializer.SerializeToElement(new { ok = true, entries = Array.Empty<object>() }) : BuildSetupCatalog(),
            "recommend_open_setup_tuning" => LoadElement("synthetic", _scenario == "damage-blocked" ? "setup-recommendation-damage-blocked.json" : "setup-recommendation.json"),
            "record_open_setup_feedback" => JsonSerializer.SerializeToElement(new { ok = true, recorded = true, source = "qa-fixture" }),
            "copy_iracing_setup_to_coach" => JsonSerializer.SerializeToElement(new { ok = true, copied = false, source = "qa-fixture" }),
            "garage61_auth_status" => Garage61Unconfigured(),
            _ => JsonSerializer.SerializeToElement(new { ok = true, source = "qa-fixture", tool = toolName })
        };
        return Task.FromResult(result);
    }

    private JsonElement BuildDashboard()
    {
        var races = new JsonArray
        {
            BuildSession("kentucky", "fixture://kentucky-race", "fixture-kentucky-race", isRace: true, includeAnalysis: true),
            BuildSession("nhms", "fixture://nhms-race", "fixture-nhms-race", isRace: true, includeAnalysis: true)
        };
        if (_scenario == "repair") races.Add(BuildSession("repair", "fixture://repair-race", "fixture-repair-race", isRace: true, includeAnalysis: true));
        return Element(new JsonObject
        {
            ["ok"] = true,
            ["contract_version"] = "qa-fixture-v1",
            ["race_count"] = races.Count,
            ["races"] = races,
            ["read_only"] = true,
            ["generated_at"] = "2026-08-03T19:37:00Z"
        });
    }

    private JsonElement BuildDiscovery()
    {
        var sessions = new JsonArray();
        if (_scenario == "with-qualifying")
            sessions.Add(BuildSession("qualifying", "fixture://kentucky-qualifying", "fixture-kentucky-qualifying", isRace: false, includeAnalysis: false));
        sessions.Add(BuildSession("kentucky", "fixture://kentucky-race", "fixture-kentucky-race", isRace: true, includeAnalysis: false));
        sessions.Add(BuildSession("nhms", "fixture://nhms-race", "fixture-nhms-race", isRace: true, includeAnalysis: false));
        if (_scenario == "repair") sessions.Add(BuildSession("repair", "fixture://repair-race", "fixture-repair-race", isRace: true, includeAnalysis: false));
        return Element(new JsonObject
        {
            ["root"] = "QA fixture archive",
            ["session_count"] = sessions.Count,
            ["returned_session_count"] = sessions.Count,
            ["error_count"] = 0,
            ["errors"] = new JsonArray(),
            ["sessions"] = sessions
        });
    }

    private static JsonElement EmptyDiscovery() => JsonSerializer.SerializeToElement(new
    {
        root = "QA fixture archive",
        session_count = 0,
        returned_session_count = 0,
        error_count = 0,
        errors = Array.Empty<object>(),
        sessions = Array.Empty<object>()
    });

    private JsonObject BuildSession(string source, string selector, string groupId, bool isRace, bool includeAnalysis)
    {
        var analysis = source switch
        {
            "nhms" => LoadObject("real-derived", "nhms-open-race-analysis.json"),
            _ => LoadObject("real-derived", "kentucky-fixed-race-analysis.json")
        };
        var identity = analysis["identity"]!.AsObject();
        var summary = analysis["race_summary"]!.AsObject();
        var qualifying = source == "qualifying";
        var eventType = qualifying ? "Qualifying" : "Race";
        var session = new JsonObject
        {
            ["kind"] = "session",
            ["group_id"] = groupId,
            ["source_path"] = selector,
            ["session_id"] = String(identity, "session_id") ?? (source == "nhms" ? "99000000" : "99000002"),
            ["session_unique_id"] = qualifying ? 2 : 3,
            ["sim_session_num"] = qualifying ? 0 : 1,
            ["sim_session_type"] = eventType,
            ["event_type"] = eventType,
            ["event_scope"] = "Official",
            ["is_race"] = isRace,
            ["is_fixed_setup"] = Bool(identity, "is_fixed_setup"),
            ["track_name"] = String(identity, "track_name") ?? "Recorded track",
            ["track_config_name"] = String(identity, "track_config") ?? "Recorded layout",
            ["car_path"] = String(identity, "car_name") ?? "Recorded car",
            ["start_time_utc"] = String(identity, "session_start") ?? "2026-08-03T03:24:10Z",
            ["end_time_utc"] = String(analysis, "analyzed_at") ?? "2026-08-03T04:00:00Z",
            ["file_count"] = 1,
            ["files"] = new JsonArray(selector),
            ["valid"] = true,
            ["series_name"] = source == "nhms" ? "NASCAR Class B" : "NASCAR Truck Series",
            ["season_name"] = "2026 Season 3"
        };
        if (includeAnalysis)
        {
            session["analysis_status"] = "analyzed";
            session["analysis"] = new JsonObject
            {
                ["analysis_available"] = true,
                ["analysis_id"] = String(analysis, "analysis_id") ?? $"qa-{source}",
                ["analysis_path"] = selector,
                ["analyzed_at"] = String(analysis, "analyzed_at") ?? "2026-08-03T19:37:00Z",
                ["race_card_available"] = true,
                ["report_available"] = true,
                ["source_available"] = true,
                ["summary"] = summary.DeepClone()
            };
        }
        return session;
    }

    private JsonElement BuildSelectedAnalysis(object arguments)
    {
        var selector = Argument(arguments, "selector").ToLowerInvariant();
        if (_scenario == "mapper-error") return JsonSerializer.SerializeToElement(new { ok = true, analysis_id = "fixture-mapper-error", analysis_view = (object?)null });
        if (_scenario == "repair" || selector.Contains("repair", StringComparison.Ordinal)) return BuildAnalysis("repair");
        if (selector.Contains("nhms", StringComparison.Ordinal)) return BuildAnalysis("nhms");
        if (selector.Contains("qualifying", StringComparison.Ordinal)) return BuildAnalysis("qualifying");
        return BuildAnalysis("kentucky");
    }

    private JsonElement BuildAnalysis(string source)
    {
        var baseName = source == "nhms" ? "nhms-open-race-analysis.json" : "kentucky-fixed-race-analysis.json";
        var analysis = LoadObject("real-derived", baseName);
        var view = analysis.DeepClone().AsObject();
        var identity = view["identity"]!.AsObject();
        if (source == "qualifying")
        {
            identity["event_type"] = "Qualifying";
            view["laps"] = new JsonArray(view["laps"]!.AsArray().Where(node => node?["lap"]?.GetValue<int>() is > 0 and <= 3).Select(node => node!.DeepClone()).ToArray());
            var summary = view["race_summary"]!.AsObject();
            summary["recorded_laps"] = 3;
            summary["scheduled_laps"] = 3;
            summary["pit_stops_detected"] = 0;
        }
        if (source == "repair")
        {
            var repair = LoadObject("synthetic", "analyze-repair-heavy.json");
            if (repair["damage_repair"] is { } damage) view["damage_repair"] = damage.DeepClone();
            if (repair["race_summary"] is { } raceSummary) view["race_summary"] = raceSummary.DeepClone();
        }

        PrepareTrackProfile(view);
        view["lap_traces"] = source == "nhms" ? EmptyLapTraces() : BuildKentuckyLapTraces(source == "qualifying" ? 2 : null);
        view["race_grades"] = BuildFixtureGrades(source);
        var card = source == "repair"
            ? LoadObject("synthetic", "analyze-repair-heavy.json")["race_card"]!.DeepClone()
            : BuildRaceCard(view);
        var root = new JsonObject
        {
            ["ok"] = true,
            ["analysis_id"] = String(view, "analysis_id") ?? $"qa-{source}-analysis",
            ["analysis_path"] = $"fixture://{source}-analysis",
            ["analysis_view"] = view,
            ["race_card"] = card,
            ["timing"] = new JsonObject { ["total_ms"] = 12.5, ["source"] = "qa-fixture" },
            ["analysis_cache"] = new JsonObject { ["cache_hit"] = true }
        };
        return Element(root);
    }

    private JsonObject BuildRaceCard(JsonObject view)
    {
        var identity = view["identity"]!.AsObject();
        var signals = view["coaching_signals"]?.AsArray() ?? new JsonArray();
        var actions = new JsonArray();
        foreach (var signal in signals.Take(3).OfType<JsonObject>())
        {
            actions.Add(new JsonObject
            {
                ["label"] = String(signal, "priority") is { Length: > 0 } priority ? $"{char.ToUpperInvariant(priority[0])}{priority[1..]} priority" : "Race priority",
                ["evidence_type"] = "derived",
                ["text"] = String(signal, "coaching") ?? String(signal, "finding") ?? "Review the recorded evidence."
            });
        }
        var first = signals.OfType<JsonObject>().FirstOrDefault();
        var rows = new JsonArray();
        if (view["track_profile"]?["detected_corner_segments"] is JsonArray segments)
        {
            foreach (var segment in segments.OfType<JsonObject>())
            {
                var label = String(segment, "label") ?? $"Load zone {Int(segment, "segment")}";
                var minimum = Number(segment, "minimum_speed_mph");
                var steering = Number(segment, "median_steering_rad");
                var observed = minimum.HasValue
                    ? $"Observed minimum {minimum:0.0} mph{(steering.HasValue ? $" with {steering:0.000} rad median steering" : string.Empty)}."
                    : "Compare the selected clean laps through this recorded load zone.";
                rows.Add(new JsonObject
                {
                    ["zone_id"] = $"load-zone-{Int(segment, "segment")}",
                    ["corner_phase"] = label,
                    ["phase_1"] = Claim("derived", observed),
                    ["phase_2"] = Claim("derived", "Use the middle-run trace as the repeatability check; no external target is loaded."),
                    ["phase_3"] = Claim("inferred", String(first, "coaching") ?? "Protect minimum speed by reducing unnecessary steering work."),
                    ["groove"] = Claim("unavailable", "Groove direction is omitted because this fixture does not provide calibrated path sign.")
                });
            }
        }
        return new JsonObject
        {
            ["title"] = $"{String(identity, "track_name")} · {String(identity, "car_name")}",
            ["bottom_line"] = Claim("derived", String(first, "finding") ?? "The recorded race is ready for evidence-based review."),
            ["actions"] = actions,
            ["corner_playbook"] = new JsonObject { ["rows"] = rows },
            ["race_triggers"] = new JsonArray(),
            ["evidence_appendix"] = new JsonArray()
        };
    }

    private JsonObject BuildKentuckyLapTraces(int? maximumTraces)
    {
        var source = LoadObject("real-derived", "kentucky-selected-lap-traces.json");
        var output = new JsonArray();
        var traces = source["traces"]!.AsArray();
        foreach (var trace in traces.Take(maximumTraces ?? traces.Count).OfType<JsonObject>())
        {
            var samples = trace["samples"]!.AsObject();
            var count = Int(trace, "sample_count");
            var points = new JsonArray();
            for (var index = 0; index < count; index++)
            {
                var speedMps = ArrayNumber(samples, "Speed", index);
                var brake = ArrayNumber(samples, "Brake", index);
                var steering = ArrayNumber(samples, "SteeringWheelAngle", index);
                var velocityX = ArrayNumber(samples, "VelocityX", index);
                var velocityY = ArrayNumber(samples, "VelocityY", index);
                points.Add(new JsonObject
                {
                    ["lap_pct"] = ArrayNumber(samples, "LapDistPct", index),
                    ["session_time_s"] = ArrayNumber(samples, "SessionTime", index),
                    ["speed_mph"] = speedMps * 2.2369362920544,
                    ["throttle"] = ArrayNumber(samples, "Throttle", index),
                    ["brake"] = brake,
                    ["steering_rad"] = steering,
                    ["steering_peak_rad"] = Math.Abs(steering),
                    ["slip_angle_deg"] = Math.Atan2(velocityY, Math.Abs(velocityX) < .0001 ? .0001 : velocityX) * 57.29577951308232,
                    ["gear"] = (int)Math.Round(ArrayNumber(samples, "Gear", index)),
                    ["rpm"] = ArrayNumber(samples, "RPM", index),
                    ["yaw_rate_deg_s"] = ArrayNumber(samples, "YawRate", index) * 57.29577951308232,
                    ["lateral_g"] = ArrayNumber(samples, "LatAccel", index) / 9.80665,
                    ["longitudinal_g"] = ArrayNumber(samples, "LongAccel", index) / 9.80665,
                    ["lat"] = ArrayNumber(samples, "Lat", index),
                    ["lon"] = ArrayNumber(samples, "Lon", index),
                    ["tire_stress_proxy"] = Math.Clamp((brake * .4) + (Math.Abs(steering) * .7) + (speedMps / 90 * .15), 0, 1)
                });
            }
            var flag = String(trace, "flag_state") ?? "green";
            output.Add(new JsonObject
            {
                ["lap"] = Int(trace, "lap"),
                ["lap_time_s"] = Number(trace, "lap_time_s"),
                ["complete"] = Bool(trace, "complete") ?? true,
                ["flag_state"] = flag,
                ["green_fraction"] = flag.Equals("green", StringComparison.OrdinalIgnoreCase) ? 1.0 : 0.0,
                ["caution_fraction"] = flag.Contains("yellow", StringComparison.OrdinalIgnoreCase) || flag.Contains("caution", StringComparison.OrdinalIgnoreCase) ? 1.0 : 0.0,
                ["pit_time_s"] = 0.0,
                ["points"] = points
            });
        }
        return new JsonObject
        {
            ["tire_stress_definition"] = "Relative controls-and-load proxy derived from recorded channels; not measured tire wear.",
            ["traces"] = output
        };
    }

    private static JsonObject EmptyLapTraces() => new()
    {
        ["tire_stress_definition"] = "No selected-lap trace fixture is supplied for this event.",
        ["traces"] = new JsonArray()
    };

    private static JsonObject BuildFixtureGrades(string source) => new()
    {
        ["status"] = "graded",
        ["overall_grade"] = source == "nhms" ? "B" : "C+",
        ["overall_score"] = source == "nhms" ? 84.0 : 78.0,
        ["categories"] = new JsonArray
        {
            Grade("pace", "Pace execution", source == "nhms" ? "B+" : "B-", source == "nhms" ? 87 : 81, "Derived from clean recorded lap pace and falloff."),
            Grade("consistency", "Consistency", "B", 84, "Derived from usable-lap variation."),
            Grade("tire_management", "Tire management", "C+", 78, "Uses discrete service readings and a controls-and-load proxy; it is not continuous measured wear.")
        }
    };

    private static JsonObject Grade(string key, string label, string grade, double score, string explanation) => new()
    {
        ["key"] = key,
        ["label"] = label,
        ["grade"] = grade,
        ["score"] = score,
        ["evidence_type"] = "derived",
        ["explanation"] = explanation,
        ["improvement"] = "Compare the selected early-, middle-, and late-run traces.",
        ["limitations"] = "No external field-strength reference is included in offline fixture mode."
    };

    private static JsonObject Claim(string evidence, string text) => new()
    {
        ["evidence_type"] = evidence,
        ["text"] = text
    };

    private static void PrepareTrackProfile(JsonObject view)
    {
        if (view["track_profile"] is not JsonObject track) return;
        if (track["profile"] is JsonArray profile)
        {
            var shape = new JsonArray();
            foreach (var point in profile.OfType<JsonObject>())
            {
                shape.Add(new JsonObject
                {
                    ["lap_pct"] = Number(point, "lap_pct") ?? 0,
                    ["x"] = Number(point, "lon") ?? 0,
                    ["y"] = Number(point, "lat") ?? 0
                });
            }
            track["shape"] = shape;
        }
        if (track["detected_corner_segments"] is JsonArray segments)
        {
            var index = 0;
            foreach (var segment in segments.OfType<JsonObject>())
                segment["label"] = ++index == 1 ? "Turns 1–2" : index == 2 ? "Turns 3–4" : $"Load zone {index}";
        }
    }

    private JsonElement BuildStrategyHistory(object arguments)
    {
        var selector = Argument(arguments, "analysis_path").ToLowerInvariant();
        var analysis = selector.Contains("nhms", StringComparison.Ordinal)
            ? LoadObject("real-derived", "nhms-open-race-analysis.json")
            : LoadObject("real-derived", "kentucky-fixed-race-analysis.json");
        var runs = new JsonArray();
        foreach (var run in analysis["runs"]!.AsArray().OfType<JsonObject>())
        {
            var fuel = run["fuel"] as JsonObject;
            runs.Add(new JsonObject
            {
                ["run_number"] = Int(run, "run_number"),
                ["green_laps"] = Int(run, "green_laps"),
                ["caution_laps"] = Int(run, "caution_laps"),
                ["fuel_used_l"] = fuel is null ? null : Number(fuel, "used_l"),
                ["session_start"] = String(analysis["identity"]!.AsObject(), "session_start") ?? "Recorded fixture session"
            });
        }
        return Element(runs);
    }

    private JsonElement BuildSetupCatalog()
    {
        var setupPackage = LoadObject("synthetic", "setup-package.json");
        var fingerprint = String(setupPackage["baseline"] as JsonObject ?? new JsonObject(), "fingerprint") ?? "qa-fixture-baseline";
        var path = Path.Combine(_fixturesRoot, "synthetic", "setup-package.json");
        return JsonSerializer.SerializeToElement(new
        {
            ok = true,
            source_fixture = "setup-package.json",
            entries = new object[]
            {
                new
                {
                    stem = "Sanitized open-race baseline",
                    car_folder = "Toyota Supra Class B",
                    pair_status = "QA fixture package",
                    filename_identity = new { track_hint = "New Hampshire Motor Speedway", role = "race" },
                    sources = new { sto = new[] { new { path, sha256 = fingerprint } } },
                    parsed_html = new
                    {
                        identity = new { mismatches = new { has_mismatch = false } },
                        fields = new Dictionary<string, object>
                        {
                            ["fixture.source"] = new { raw = "Sanitized setup package", label = "Fixture source", section = "Evidence", unit = (string?)null },
                            ["fixture.provenance"] = new { raw = "No simulator-loadable setup included", label = "Provenance", section = "Evidence", unit = (string?)null }
                        }
                    }
                }
            }
        });
    }

    private JsonElement Garage61Unconfigured()
    {
        var states = LoadObject("synthetic", "garage61-auth-status-states.json");
        return states["unconfigured"] is { } state ? Element(state.DeepClone()) : JsonSerializer.SerializeToElement(new { ok = false, configured = false, status = "not_configured" });
    }

    private string RequireFile(params string[] segments)
    {
        var path = segments.Aggregate(_fixturesRoot, Path.Combine);
        if (!File.Exists(path)) throw new FileNotFoundException($"Required QA fixture is missing: {string.Join('/', segments)}", path);
        return path;
    }

    private JsonObject LoadObject(params string[] segments)
    {
        var node = JsonNode.Parse(File.ReadAllText(RequireFile(segments)), documentOptions: new JsonDocumentOptions { CommentHandling = JsonCommentHandling.Disallow, AllowTrailingCommas = false });
        return node?.AsObject() ?? throw new InvalidDataException($"QA fixture must be a JSON object: {string.Join('/', segments)}");
    }

    private JsonElement LoadElement(params string[] segments)
    {
        using var document = JsonDocument.Parse(File.ReadAllText(RequireFile(segments)));
        return document.RootElement.Clone();
    }

    private static string Argument(object arguments, string property)
    {
        var element = JsonSerializer.SerializeToElement(arguments);
        return element.ValueKind == JsonValueKind.Object && element.TryGetProperty(property, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString() ?? string.Empty
            : string.Empty;
    }

    private static JsonElement Element(JsonNode node)
    {
        using var document = JsonDocument.Parse(node.ToJsonString());
        return document.RootElement.Clone();
    }

    private static string? String(JsonObject? value, string property) =>
        value?[property] is JsonValue node && node.TryGetValue<string>(out var text) ? text : null;

    private static double? Number(JsonObject? value, string property) =>
        value?[property] is JsonValue node && node.TryGetValue<double>(out var number) ? number : null;

    private static int Int(JsonObject? value, string property)
    {
        if (value?[property] is not JsonValue node) return 0;
        if (node.TryGetValue<int>(out var integer)) return integer;
        return node.TryGetValue<double>(out var number) ? (int)Math.Round(number, MidpointRounding.AwayFromZero) : 0;
    }

    private static bool? Bool(JsonObject? value, string property) =>
        value?[property] is JsonValue node && node.TryGetValue<bool>(out var result) ? result : null;

    private static double ArrayNumber(JsonObject value, string property, int index) =>
        value[property] is JsonArray array && index >= 0 && index < array.Count && array[index] is JsonValue item && item.TryGetValue<double>(out var number) ? number : 0;
}

/// <summary>Settings store that confines every portable and machine-local write to one temporary QA root.</summary>
public sealed class QaFixtureSettingsStore : ISettingsStore
{
    private readonly CompanionSettings _settings;

    public QaFixtureSettingsStore(string archiveRoot)
    {
        if (string.IsNullOrWhiteSpace(archiveRoot)) throw new ArgumentException("A temporary QA archive root is required.", nameof(archiveRoot));
        var root = Path.GetFullPath(archiveRoot);
        Directory.CreateDirectory(root);
        var source = Path.Combine(root, "source");
        Directory.CreateDirectory(Path.Combine(source, "iRacing"));
        Directory.CreateDirectory(Path.Combine(source, "install"));
        _settings = new CompanionSettings
        {
            CoachHome = root,
            IRacingRoot = Path.Combine(source, "iRacing"),
            IRacingInstallRoot = Path.Combine(source, "install"),
            LocalStateRootOverride = Path.Combine(root, "machine-state"),
            FirstRunComplete = true,
            UseReducedMotion = true
        };
    }

    public CompanionSettings Load() => _settings;
    public void Save(CompanionSettings settings) { }
}

/// <summary>Non-persistent credential boundary for fixture mode.</summary>
public sealed class QaFixtureCredentialStore : IGarage61CredentialStore
{
    public bool IsConfigured => false;
    public string CredentialPath => string.Empty;
    public void Store(string token) { }
    public void Remove() { }
}

/// <summary>Deterministic, file-driven connected telemetry source for offline UI and monitor testing.</summary>
public sealed class ReplayFileLiveTelemetrySource : ILiveTelemetrySource
{
    private readonly IReadOnlyList<ReplayFrame> _frames;
    private readonly Stopwatch _clock = Stopwatch.StartNew();
    private readonly double _timeScale;
    private int _lastIndex = -1;
    private DateTimeOffset _lastEndPublish = DateTimeOffset.MinValue;

    public ReplayFileLiveTelemetrySource(string path, double timeScale = 1)
    {
        if (string.IsNullOrWhiteSpace(path)) throw new ArgumentException("A live replay JSON path is required.", nameof(path));
        var fullPath = Path.GetFullPath(path);
        if (!File.Exists(fullPath)) throw new FileNotFoundException("The live replay JSON file was not found.", fullPath);
        if (!double.IsFinite(timeScale) || timeScale is <= 0 or > 100) throw new ArgumentOutOfRangeException(nameof(timeScale), "QA time scale must be greater than 0 and no more than 100.");
        _timeScale = timeScale;
        using var document = JsonDocument.Parse(File.ReadAllText(fullPath));
        if (!document.RootElement.TryGetProperty("frames", out var frames) || frames.ValueKind != JsonValueKind.Array)
            throw new InvalidDataException("The live replay fixture does not contain a frames array.");
        _frames = frames.EnumerateArray().Select((frame, index) => ParseFrame(frame, index)).ToArray();
        if (_frames.Count < 2) throw new InvalidDataException("The live replay fixture needs at least two frames.");
    }

    public int FrameCount => _frames.Count;
    public double DurationSeconds => _frames[^1].AtSeconds;

    public LiveTelemetrySample SampleAt(double playbackSeconds)
    {
        if (!double.IsFinite(playbackSeconds) || playbackSeconds < 0) throw new ArgumentOutOfRangeException(nameof(playbackSeconds));
        var index = playbackSeconds >= DurationSeconds ? _frames.Count - 1 : FindFrame(playbackSeconds);
        return _frames[index].Sample with { Timestamp = DateTimeOffset.UtcNow, Tick = index, TickRate = 20 };
    }

    public bool TryRead(out LiveTelemetrySample sample)
    {
        var playbackSeconds = _clock.Elapsed.TotalSeconds * _timeScale;
        var index = playbackSeconds >= DurationSeconds ? _frames.Count - 1 : FindFrame(playbackSeconds);
        if (index == _lastIndex)
        {
            if (index != _frames.Count - 1 || DateTimeOffset.UtcNow - _lastEndPublish < TimeSpan.FromMilliseconds(500))
            {
                sample = new LiveTelemetrySample { Connected = false };
                return false;
            }
            _lastEndPublish = DateTimeOffset.UtcNow;
        }
        _lastIndex = index;
        sample = SampleAt(playbackSeconds);
        return true;
    }

    private int FindFrame(double seconds)
    {
        var low = 0;
        var high = _frames.Count - 1;
        while (low <= high)
        {
            var middle = low + ((high - low) / 2);
            if (_frames[middle].AtSeconds <= seconds) low = middle + 1;
            else high = middle - 1;
        }
        return Math.Clamp(high, 0, _frames.Count - 1);
    }

    private static ReplayFrame ParseFrame(JsonElement frame, int index)
    {
        var at = Number(frame, "timestamp_s") ?? index / 20d;
        var flag = Text(frame, "session_flag")?.ToUpperInvariant() ?? "GREEN";
        var trackF = Number(frame, "track_temp_f");
        var airF = Number(frame, "air_temp_f");
        return new ReplayFrame(at, new LiveTelemetrySample
        {
            Connected = true,
            Flag = flag,
            UnderCaution = flag.Contains("CAUTION", StringComparison.OrdinalIgnoreCase) || flag.Contains("YELLOW", StringComparison.OrdinalIgnoreCase),
            Lap = Integer(frame, "lap"),
            OverallPosition = Integer(frame, "player_position"),
            ClassPosition = Integer(frame, "player_position"),
            GapToLeaderSeconds = Number(frame, "physical_gap_to_leader_s"),
            GapToClassLeaderSeconds = Number(frame, "physical_gap_to_leader_s"),
            LastLapSeconds = Number(frame, "last_lap_time_s"),
            LeaderLastLapSeconds = Number(frame, "leader_last_lap_time_s"),
            FuelLiters = Number(frame, "fuel_level_l"),
            TrackTemperatureC = trackF.HasValue ? (trackF.Value - 32) * 5 / 9 : null,
            AirTemperatureC = airF.HasValue ? (airF.Value - 32) * 5 / 9 : null,
            OnPitRoad = string.Equals(Text(frame, "session_state"), "pit", StringComparison.OrdinalIgnoreCase),
            SteeringWheelAngleRadians = Number(frame, "steering_wheel_angle_rad"),
            Throttle = Number(frame, "throttle"),
            Brake = Number(frame, "brake"),
            Gear = Integer(frame, "gear"),
            Rpm = Number(frame, "rpm"),
            YawRateRadiansPerSecond = Number(frame, "yaw_rate_rad_s"),
            LateralAccelerationG = Number(frame, "lateral_accel_mps2") is { } lateral ? lateral / 9.80665 : null,
            LongitudinalAccelerationG = Number(frame, "longitudinal_accel_mps2") is { } longitudinal ? longitudinal / 9.80665 : null,
            SpeedMetersPerSecond = Number(frame, "speed_mps"),
            LapDistancePercent = Number(frame, "lap_dist_pct"),
            Latitude = Number(frame, "track_y_m"),
            Longitude = Number(frame, "track_x_m"),
            Source = "QA fixture replay · live-sdk-replay.json"
        });
    }

    private static string? Text(JsonElement element, string property) =>
        element.TryGetProperty(property, out var value) && value.ValueKind == JsonValueKind.String ? value.GetString() : null;

    private static double? Number(JsonElement element, string property) =>
        element.TryGetProperty(property, out var value) && value.ValueKind == JsonValueKind.Number && value.TryGetDouble(out var number) ? number : null;

    private static int? Integer(JsonElement element, string property) =>
        element.TryGetProperty(property, out var value) && value.ValueKind == JsonValueKind.Number && value.TryGetInt32(out var integer) ? integer : null;

    public void Dispose() => _clock.Stop();

    private sealed record ReplayFrame(double AtSeconds, LiveTelemetrySample Sample);
}
