using iRacingCoach.Coordinator;

namespace iRacingCoach.UI;

/// <summary>
/// Counts down an information toast. Hovering or focusing the toast holds the countdown;
/// releasing it resumes with the time that was left (never less than
/// <see cref="MinimumAfterRelease"/>, so a toast does not vanish under a leaving pointer).
/// Errors never count down.
/// </summary>
public sealed class ToastDismissTimer(TimeProvider time)
{
    public static TimeSpan MinimumAfterRelease { get; } = TimeSpan.FromSeconds(2);

    private DateTimeOffset _deadline;
    private TimeSpan _remaining;
    private bool _hovered;
    private bool _focused;

    public long? Revision { get; private set; }
    public bool Counting { get; private set; }
    public bool Held => _hovered || _focused;

    /// <summary>Follows the notification on screen. Returns true when the countdown changed.</summary>
    public bool Track(ToastNotice? notice)
    {
        if (notice?.Revision == Revision) return false;
        Revision = notice?.Revision;
        _hovered = _focused = false;
        Counting = notice is { AutoDismisses: true };
        _remaining = ToastNotice.AutoDismissDelay;
        _deadline = time.GetUtcNow() + _remaining;
        return true;
    }

    public bool SetHovered(bool hovered) => Hold(ref _hovered, hovered);
    public bool SetFocused(bool focused) => Hold(ref _focused, focused);

    /// <summary>Time until the toast should clear, or null while held or when it never clears.</summary>
    public TimeSpan? Remaining
    {
        get
        {
            if (!Counting || Held) return null;
            var left = _deadline - time.GetUtcNow();
            return left > TimeSpan.Zero ? left : TimeSpan.Zero;
        }
    }

    public bool IsDue(long revision) => Revision == revision && Remaining == TimeSpan.Zero;

    private bool Hold(ref bool flag, bool value)
    {
        if (flag == value) return false;
        var wasHeld = Held;
        flag = value;
        if (!Counting || wasHeld == Held) return false;
        var now = time.GetUtcNow();
        if (Held)
        {
            _remaining = _deadline - now;
        }
        else
        {
            _deadline = now + (_remaining > MinimumAfterRelease ? _remaining : MinimumAfterRelease);
        }
        return true;
    }
}
