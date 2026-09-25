using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

public enum ToastTone { Info, Error }

/// <summary>The one recovery an error notification offers.</summary>
public enum ToastAction { None, OpenLogs, Troubleshooting }

/// <summary>
/// One notification. Information and success clear themselves after a few seconds; an
/// error stays until the driver dismisses it, says what failed and offers a recovery.
/// </summary>
public sealed record ToastNotice(long Revision, string Message, ToastTone Tone, ToastAction Action, string? Reference = null)
{
    /// <summary>How long information stays visible while it is not hovered or focused.</summary>
    public static TimeSpan AutoDismissDelay { get; } = TimeSpan.FromSeconds(5);

    public bool AutoDismisses => Tone == ToastTone.Info;

    public string ActionLabel => Action switch
    {
        ToastAction.OpenLogs => "Open logs",
        ToastAction.Troubleshooting => "Troubleshooting",
        _ => string.Empty
    };

    // The app's own wording for an operation that did not complete. A missing tuning
    // recommendation is an answer from the evidence, not a failure.
    private static readonly string[] FailureWording =
    [
        "could not be", "could not start", "was not saved", "were not saved", "was not copied",
        "was not replaced", "needs attention", "is incomplete", "failed"
    ];
    private static readonly string[] AnswerWording = ["could not be recommended"];

    public static ToastTone ToneOf(string message) =>
        FailureWording.Any(word => message.Contains(word, StringComparison.OrdinalIgnoreCase))
        && !AnswerWording.Any(word => message.Contains(word, StringComparison.OrdinalIgnoreCase))
            ? ToastTone.Error
            : ToastTone.Info;

    /// <summary>A specific, bounded message for an error the app recorded in its log.</summary>
    public static string ContainedErrorMessage(RecoverableAppError error)
    {
        var scope = error.Scope.Trim();
        var subject = scope switch
        {
            "user interface" => "This view stopped",
            "app startup" => "Startup did not finish",
            "archived race catalog" => "Saved races could not be listed",
            "archived setup catalog" => "Saved setups could not be listed",
            _ when scope.StartsWith("backend tool ", StringComparison.Ordinal) => "Local analysis failed",
            _ when scope.StartsWith("job ", StringComparison.Ordinal) && scope.Length > 4 => $"{scope[4..]} failed",
            _ when scope.Length > 0 => $"{char.ToUpperInvariant(scope[0])}{scope[1..]} failed",
            _ => "An operation failed"
        };
        var detail = error.Message.Trim().TrimEnd('.');
        if (detail.Length > 180) detail = detail[..179].TrimEnd() + "…";
        return detail.Length == 0 ? $"{subject}." : $"{subject}: {detail}.";
    }
}

public sealed partial class CompanionState
{
    private ToastNotice? _toast;
    private long _toastRevision;

    /// <summary>The notification on screen, with its tone, recovery and revision.</summary>
    public ToastNotice? CurrentToast => _toast;

    private void SetToast(string? message)
    {
        if (string.IsNullOrWhiteSpace(message)) { _toast = null; return; }
        var tone = ToastNotice.ToneOf(message);
        _toast = new(Interlocked.Increment(ref _toastRevision), message, tone,
            tone == ToastTone.Error ? ToastAction.Troubleshooting : ToastAction.None);
    }

    /// <summary>An error that was written to the app log names what failed and links to the log.</summary>
    private void ShowContainedError(RecoverableAppError? error)
    {
        if (error is null) { SetToast("An operation failed; details are in the app log."); return; }
        _toast = new(Interlocked.Increment(ref _toastRevision), ToastNotice.ContainedErrorMessage(error),
            ToastTone.Error, ToastAction.OpenLogs, error.Id);
    }

    /// <summary>Dismisses the notification only if it is still the one the caller saw.</summary>
    public void DismissToast(long revision)
    {
        if (_toast?.Revision != revision) return;
        DismissToast();
    }

    public void RunToastAction()
    {
        switch (_toast?.Action)
        {
            case ToastAction.OpenLogs: OpenLogs(); break;
            case ToastAction.Troubleshooting: OpenTroubleshooting(); break;
        }
    }
}
