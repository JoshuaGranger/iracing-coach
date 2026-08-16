using System.Text.Json;
using System.Text.RegularExpressions;
using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

internal static partial class StructuredAppLog
{
    private static readonly object Gate = new();

    public static RecoverableAppError Record(
        string scope,
        Exception exception,
        string appVersion,
        string logRoot,
        string profileRoot)
    {
        var id = Guid.NewGuid().ToString("N")[..10];
        var occurred = DateTimeOffset.UtcNow;
        var safeScope = Clean(scope, 80, profileRoot);
        var safeMessage = Clean(exception.Message, 500, profileRoot);
        var error = new RecoverableAppError(id, safeScope, safeMessage, occurred);
        try
        {
            var root = Path.GetFullPath(logRoot);
            Directory.CreateDirectory(root);
            var line = JsonSerializer.Serialize(new
            {
                timestampUtc = occurred,
                eventType = "contained_error",
                correlationId = id,
                area = safeScope,
                exceptionType = exception.GetType().Name,
                message = safeMessage,
                appVersion
            });
            lock (Gate)
            {
                File.AppendAllText(Path.Combine(root, "app-errors.jsonl"), line + Environment.NewLine);
            }
        }
        catch (Exception logFailure) when (logFailure is IOException or UnauthorizedAccessException or ArgumentException or NotSupportedException)
        {
            // Error reporting must never become another application failure.
        }
        return error;
    }

    private static string Clean(string? value, int maximum, string profileRoot)
    {
        var text = string.IsNullOrWhiteSpace(value) ? "Unexpected application error" : value.Trim();
        if (!string.IsNullOrWhiteSpace(profileRoot)) text = text.Replace(profileRoot, "%USERPROFILE%", StringComparison.OrdinalIgnoreCase);
        text = SecretPattern().Replace(text, "$1=[redacted]");
        text = text.Replace('\r', ' ').Replace('\n', ' ');
        return text[..Math.Min(text.Length, maximum)];
    }

    [GeneratedRegex("(?i)(token|api[_ -]?key|authorization|password)\\s*[:=]\\s*[^\\s,;]+")]
    private static partial Regex SecretPattern();
}
