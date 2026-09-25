using System.Collections.Concurrent;
using System.Text.Json;
using System.Text.RegularExpressions;
using iRacingCoach.BackendClient;
using iRacingCoach.Contracts;
using iRacingCoach.Coordinator;
using iRacingCoach.UI;
using Microsoft.AspNetCore.Components;
using Microsoft.AspNetCore.Components.Web;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using Microsoft.JSInterop;

namespace iRacingCoach.Tests;

public sealed partial class UiPolishRegressionTests
{
    // ---------- 1. Notifications ----------

    [TestMethod]
    public void InformationClearsItselfButAnErrorNamesWhatFailedAndOffersARecovery()
    {
        using var state = NewState();

        state.Notify("Test saved. It will be checked against your next compatible recording.");
        Assert.AreEqual(ToastTone.Info, state.CurrentToast!.Tone);
        Assert.IsTrue(state.CurrentToast.AutoDismisses, "Success must not stay on screen until dismissed.");
        Assert.AreEqual(ToastAction.None, state.CurrentToast.Action);
        Assert.IsTrue(ToastNotice.AutoDismissDelay >= TimeSpan.FromSeconds(4) && ToastNotice.AutoDismissDelay <= TimeSpan.FromSeconds(6));

        state.ReportUnhandledException("backend tool analyze_iracing_race", new InvalidOperationException("The recording ended before the green flag."));
        var error = state.CurrentToast!;
        Assert.AreEqual(ToastTone.Error, error.Tone);
        Assert.IsFalse(error.AutoDismisses, "An error stays until the driver dismisses it.");
        Assert.AreEqual(ToastAction.OpenLogs, error.Action);
        Assert.AreEqual("Open logs", error.ActionLabel);
        Assert.AreEqual(state.LastRecoverableError!.Id, error.Reference);
        Assert.AreEqual("Local analysis failed: The recording ended before the green flag.", error.Message);
        Assert.DoesNotContain("Something went wrong", state.Toast!);

        state.Notify("Garage61 could not be connected: the token was rejected.");
        Assert.AreEqual(ToastTone.Error, state.CurrentToast!.Tone);
        Assert.AreEqual(ToastAction.Troubleshooting, state.CurrentToast.Action);
        state.Notify("A safe tuning change could not be recommended from this race.");
        Assert.AreEqual(ToastTone.Info, state.CurrentToast!.Tone, "A missing recommendation is an answer, not a failure.");
        state.Notify("Settings saved.");
        Assert.AreEqual(ToastTone.Info, state.CurrentToast!.Tone);
    }

    [TestMethod]
    public void FailedJobsKeepTheSpecificLoggedReason()
    {
        var error = new RecoverableAppError("abc123", "job Analyze Bristol", "Telemetry file is locked by iRacing", DateTimeOffset.UtcNow);
        Assert.AreEqual("Analyze Bristol failed: Telemetry file is locked by iRacing.", ToastNotice.ContainedErrorMessage(error));
        Assert.AreEqual("This view stopped: Index was out of range.",
            ToastNotice.ContainedErrorMessage(error with { Scope = "user interface", Message = "Index was out of range." }));
        var longMessage = ToastNotice.ContainedErrorMessage(error with { Message = new string('x', 400) });
        Assert.IsLessThanOrEqualTo(220, longMessage.Length, "A toast stays bounded; the log keeps the full record.");
    }

    [TestMethod]
    public void DismissingAnOlderNotificationNeverClearsANewerOne()
    {
        using var state = NewState();
        state.Notify("First");
        var first = state.CurrentToast!.Revision;
        state.Notify("Second");
        state.DismissToast(first);
        Assert.AreEqual("Second", state.Toast, "A countdown for an earlier toast must not dismiss the one now showing.");
        state.DismissToast(state.CurrentToast!.Revision);
        Assert.IsNull(state.Toast);
        Assert.IsNull(state.CurrentToast);
    }

    [TestMethod]
    public void CountdownHoldsWhileHoveredOrFocusedAndErrorsNeverCountDown()
    {
        var time = new ManualTime();
        var timer = new ToastDismissTimer(time);
        var info = new ToastNotice(1, "Test saved.", ToastTone.Info, ToastAction.None);

        Assert.IsTrue(timer.Track(info));
        Assert.AreEqual((TimeSpan?)ToastNotice.AutoDismissDelay, timer.Remaining);
        time.Advance(TimeSpan.FromSeconds(2));
        Assert.AreEqual((TimeSpan?)(ToastNotice.AutoDismissDelay - TimeSpan.FromSeconds(2)), timer.Remaining);

        Assert.IsTrue(timer.SetHovered(true));
        time.Advance(TimeSpan.FromMinutes(5));
        Assert.IsNull(timer.Remaining, "Hover holds the toast.");
        Assert.IsFalse(timer.IsDue(1));
        timer.SetFocused(true);
        timer.SetHovered(false);
        Assert.IsNull(timer.Remaining, "Keyboard focus still holds it after the pointer leaves.");
        Assert.IsTrue(timer.SetFocused(false));
        Assert.AreEqual((TimeSpan?)(ToastNotice.AutoDismissDelay - TimeSpan.FromSeconds(2)), timer.Remaining, "Release resumes with the time that was left.");
        time.Advance(ToastNotice.AutoDismissDelay);
        Assert.IsTrue(timer.IsDue(1));
        Assert.IsFalse(timer.IsDue(2), "Only the tracked toast is due.");

        // Releasing close to the deadline still leaves time to read.
        timer.Track(info with { Revision = 2 });
        time.Advance(ToastNotice.AutoDismissDelay - TimeSpan.FromMilliseconds(200));
        timer.SetHovered(true);
        timer.SetHovered(false);
        Assert.AreEqual((TimeSpan?)ToastDismissTimer.MinimumAfterRelease, timer.Remaining);

        Assert.IsTrue(timer.Track(new ToastNotice(3, "Local analysis failed: x.", ToastTone.Error, ToastAction.OpenLogs, "abc")));
        time.Advance(TimeSpan.FromHours(1));
        Assert.IsNull(timer.Remaining);
        Assert.IsFalse(timer.IsDue(3));
        Assert.IsFalse(timer.Track(new ToastNotice(3, "Local analysis failed: x.", ToastTone.Error, ToastAction.OpenLogs, "abc")),
            "Re-rendering the same toast does not restart anything.");
    }

    [TestMethod]
    public async Task NotificationsDockInTheTopBarInsteadOfCoveringPageControls()
    {
        using var state = NewState();
        state.Notify("Test saved. It will be checked against your next compatible recording.");
        var html = await Render<AppTopBar>(state, new() { [nameof(AppTopBar.State)] = state });
        var header = Regex.Match(html, "<header class=\"app-topbar\".*?</header>", RegexOptions.Singleline).Value;
        StringAssert.Contains(header, "class=\"toast info docked\"");
        StringAssert.Contains(header, "role=\"status\"");
        StringAssert.Contains(header, "Test saved.");
        Assert.DoesNotContain("toast-action", header, "Information has no recovery button.");

        state.ReportUnhandledException("backend tool analyze_iracing_race", new InvalidOperationException("The recording ended before the green flag."));
        html = await Render<AppTopBar>(state, new() { [nameof(AppTopBar.State)] = state });
        StringAssert.Contains(html, "class=\"toast error docked\"");
        StringAssert.Contains(html, "role=\"alert\"");
        StringAssert.Contains(html, ">Open logs</button>");
        StringAssert.Contains(html, $"Ref {state.LastRecoverableError!.Id}");
        StringAssert.Contains(html, "aria-label=\"Dismiss notification\"");

        var css = File.ReadAllText(Path.Combine(UiRoot(), "wwwroot", "coach.css"));
        var toastRule = Regex.Match(css, @"^\.toast \{[^}]*\}", RegexOptions.Multiline).Value;
        Assert.DoesNotContain("position: fixed", toastRule, "The base toast is laid out in the top bar, not over the page.");
        Assert.DoesNotContain("bottom:", toastRule);
        StringAssert.Contains(css, ".toast.docked {");
        StringAssert.Contains(css, ".reduced-motion .toast { animation: none; }");
        StringAssert.Contains(css, "@media (prefers-reduced-motion: reduce) { .toast { animation: none; } }");
        var shell = File.ReadAllText(Path.Combine(UiRoot(), "CompanionShell.razor"));
        Assert.DoesNotContain("<div class=\"toast\"", shell, "The shell no longer floats a toast over every page.");
    }

    private sealed class ManualTime : TimeProvider
    {
        private DateTimeOffset _now = new(2026, 9, 24, 20, 0, 0, TimeSpan.Zero);
        public override DateTimeOffset GetUtcNow() => _now;
        public void Advance(TimeSpan by) => _now += by;
    }
}
