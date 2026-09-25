using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

/// <summary>
/// What a notification tells the driver. Every caller states it; the app never guesses it
/// from the wording. Information and success clear themselves. A warning (the request did
/// not happen and the driver must do something first) and an error (something failed) stay
/// until the driver dismisses them.
/// </summary>
public enum ToastTone { Info, Warning, Error }

/// <summary>The one recovery a notification offers.</summary>
public enum ToastAction { None, OpenLogs, Troubleshooting }

/// <summary>
/// One notification. Information and success clear themselves after a few seconds; a
/// warning or error stays until the driver dismisses it and says what to do next.
/// </summary>
public sealed record ToastNotice(long Revision, string Message, ToastTone Tone, ToastAction Action, string? Reference = null, string? Source = null)
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
    private readonly object _toastGate = new();
    private ToastNotice? _toast;
    // A warning or error that passing information covered; it returns when that clears.
    private ToastNotice? _coveredProblem;
    private long _toastRevision;

    /// <summary>The notification on screen, with its tone, recovery and revision.</summary>
    public ToastNotice? CurrentToast => _toast;

    /// <summary>The text of the notification on screen.</summary>
    public string? Toast => _toast?.Message;

    /// <summary>Shows a notification with the tone the caller states.</summary>
    public void Notify(string message, ToastTone tone)
    {
        ShowToast(message, tone);
        RaiseChanged();
    }

    /// <summary>
    /// Replaces the notification on screen. An error offers Troubleshooting unless the caller
    /// names another recovery. Information never discards an undismissed warning or error: the
    /// problem returns once the information clears.
    /// </summary>
    private void ShowToast(string message, ToastTone tone, ToastAction? action = null, string? reference = null, string? source = null)
    {
        if (string.IsNullOrWhiteSpace(message)) return;
        var notice = new ToastNotice(Interlocked.Increment(ref _toastRevision), message, tone,
            action ?? (tone == ToastTone.Error ? ToastAction.Troubleshooting : ToastAction.None), reference, source);
        lock (_toastGate)
        {
            if (!notice.AutoDismisses) _coveredProblem = null;
            else if (_toast is { AutoDismisses: false } problem) _coveredProblem = problem;
            _toast = notice;
        }
    }

    /// <summary>An error that was written to the app log names what failed and links to the log.</summary>
    private void ShowContainedError(RecoverableAppError? error)
    {
        if (error is null) { ShowToast("An operation failed; details are in the app log.", ToastTone.Error); return; }
        ShowToast(ToastNotice.ContainedErrorMessage(error), ToastTone.Error, ToastAction.OpenLogs, error.Id, error.Scope);
    }

    /// <summary>
    /// Clears information on screen (bringing back a problem it covered). Warnings and errors
    /// stay unless <paramref name="resolvedSource"/> names the work that raised them and that
    /// work has now succeeded.
    /// </summary>
    private void ClearPassingToast(string? resolvedSource = null)
    {
        lock (_toastGate)
        {
            if (resolvedSource is not null && _coveredProblem?.Source == resolvedSource) _coveredProblem = null;
            if (_toast is null) return;
            if (_toast.AutoDismisses || (resolvedSource is not null && _toast.Source == resolvedSource))
            {
                _toast = _toast.AutoDismisses ? _coveredProblem : null;
                _coveredProblem = null;
            }
        }
    }

    /// <summary>The driver (or an information countdown) dismissed the notification on screen.</summary>
    public void DismissToast() => DismissToast(null);

    /// <summary>Dismisses the notification only if it is still the one the caller saw.</summary>
    public void DismissToast(long revision) => DismissToast((long?)revision);

    private void DismissToast(long? revision)
    {
        lock (_toastGate)
        {
            if (_toast is null || (revision is { } seen && _toast.Revision != seen)) return;
            _toast = _toast.AutoDismisses ? _coveredProblem : null;
            _coveredProblem = null;
        }
        RaiseChanged();
    }

    public void RunToastAction()
    {
        switch (_toast?.Action)
        {
            case ToastAction.OpenLogs: OpenLogs(); break;
            case ToastAction.Troubleshooting:
                // Following the recovery answers the notification.
                DismissToast();
                OpenTroubleshooting();
                break;
        }
    }
}
