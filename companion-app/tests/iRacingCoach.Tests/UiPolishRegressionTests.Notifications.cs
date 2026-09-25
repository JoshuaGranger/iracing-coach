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

        state.Notify("Test saved. It will be checked against your next compatible recording.", ToastTone.Info);
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

        // The caller states the tone; the wording is never parsed for it.
        state.Notify("Garage61 could not be connected: the token was rejected.", ToastTone.Error);
        Assert.AreEqual(ToastTone.Error, state.CurrentToast!.Tone);
        Assert.AreEqual(ToastAction.Troubleshooting, state.CurrentToast.Action);
        Assert.IsFalse(state.CurrentToast.AutoDismisses);
        state.Notify("Choose a local setup file first.", ToastTone.Warning);
        Assert.AreEqual(ToastAction.None, state.CurrentToast!.Action, "A warning names its own next step.");
        Assert.IsFalse(state.CurrentToast.AutoDismisses, "A warning stays until dismissed.");
        state.Notify("Settings saved.", ToastTone.Info);
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
        state.Notify("First", ToastTone.Info);
        var first = state.CurrentToast!.Revision;
        state.Notify("Second", ToastTone.Info);
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
    public async Task NotificationsRenderOutsideTheTopBarWithTheirToneAndRecovery()
    {
        using var state = NewState();
        state.Notify("Test saved. It will be checked against your next compatible recording.", ToastTone.Info);
        var topBar = await Render<AppTopBar>(state, new() { [nameof(AppTopBar.State)] = state });
        Assert.DoesNotContain("toast", topBar, "Navigation never shares its row with a notification.");

        var html = await Render<AppToast>(state, new() { [nameof(AppToast.State)] = state });
        StringAssert.Contains(html, "class=\"toast info\"");
        StringAssert.Contains(html, "role=\"status\"");
        StringAssert.Contains(html, "Test saved.");
        Assert.DoesNotContain("toast-action", html, "Information has no recovery button.");

        state.Notify("That recording is no longer available. Refresh your races and try again.", ToastTone.Warning);
        html = await Render<AppToast>(state, new() { [nameof(AppToast.State)] = state });
        StringAssert.Contains(html, "class=\"toast warning\"");
        StringAssert.Contains(html, "role=\"alert\"");
        Assert.DoesNotContain("toast-action", html);

        state.ReportUnhandledException("backend tool analyze_iracing_race", new InvalidOperationException("The recording ended before the green flag."));
        html = await Render<AppToast>(state, new() { [nameof(AppToast.State)] = state });
        StringAssert.Contains(html, "class=\"toast error\"");
        StringAssert.Contains(html, "role=\"alert\"");
        StringAssert.Contains(html, ">Open logs</button>");
        StringAssert.Contains(html, $"Ref {state.LastRecoverableError!.Id}");
        StringAssert.Contains(html, "aria-label=\"Dismiss notification\"");

        var css = File.ReadAllText(Path.Combine(UiRoot(), "wwwroot", "coach.css"));
        StringAssert.Contains(css, ".toast.warning {");
        StringAssert.Contains(css, ".reduced-motion .toast { animation: none; }");
        StringAssert.Contains(css, "@media (prefers-reduced-motion: reduce) { .toast { animation: none; } }");
    }

    // Main's unreadable saved-analyses notice had its own toast markup in the shell; it now
    // shares the one notification placement and keeps its recovery, dismissal and priority.
    [TestMethod]
    public async Task UnreadableSavedAnalysesNoticeSharesTheOneNotificationPlacement()
    {
        var root = TempRoot();
        var paths = new IsolatedCompanionPathProvider(root);
        var settings = new CompanionSettings(paths) { FirstRunComplete = true };
        Directory.CreateDirectory(settings.IRacingRoot);
        Directory.CreateDirectory(settings.IRacingInstallRoot);
        var report = Path.Combine(settings.ArchiveRoot, "reports", "season-1", "subsession-1000001-2", "broken", "analysis.json");
        Directory.CreateDirectory(Path.GetDirectoryName(report)!);
        File.WriteAllText(report, "{broken");
        using var state = NewSettingsState(root, paths, new MemorySettingsStore(settings));
        await state.InitializeAsync();
        var until = DateTime.UtcNow + TimeSpan.FromSeconds(10);
        while ((state.IsRefreshing || !state.HomeDataReady) && DateTime.UtcNow < until) await Task.Delay(10);
        Assert.AreEqual("1 saved analysis could not be read", state.UnreadableAnalysisNotice);
        Assert.IsNull(state.CurrentToast);

        var html = await Render<AppToast>(state, new() { [nameof(AppToast.State)] = state });
        StringAssert.Contains(html, "data-unreadable-analyses");
        StringAssert.Contains(html, "class=\"toast warning\"");
        StringAssert.Contains(html, "1 saved analysis could not be read");
        StringAssert.Contains(html, ">Open logs</button>");
        StringAssert.Contains(html, "aria-label=\"Dismiss notice\"");

        // Any other notification takes the place first; the notice returns when it clears.
        state.Notify("Settings saved.", ToastTone.Info);
        html = await Render<AppToast>(state, new() { [nameof(AppToast.State)] = state });
        StringAssert.Contains(html, "Settings saved.");
        Assert.DoesNotContain("data-unreadable-analyses", html);
        state.DismissToast(state.CurrentToast!.Revision);
        StringAssert.Contains(await Render<AppToast>(state, new() { [nameof(AppToast.State)] = state }), "data-unreadable-analyses");

        state.DismissUnreadableAnalysisNotice();
        Assert.DoesNotContain("toast", await Render<AppToast>(state, new() { [nameof(AppToast.State)] = state }));

        var shell = File.ReadAllText(Path.Combine(UiRoot(), "CompanionShell.razor"));
        Assert.DoesNotContain("class=\"toast", shell, "The shell has one notification placement: AppToast.");
        var css = File.ReadAllText(Path.Combine(UiRoot(), "wwwroot", "coach.css"));
        Assert.HasCount(1, Regex.Matches(css, @"\.toast \.toast-action \{"), "One rule sizes the recovery button.");
        StringAssert.Contains(css, ".toast .toast-action { width: auto;");
    }

    private sealed class ManualTime : TimeProvider
    {
        private DateTimeOffset _now = new(2026, 9, 24, 20, 0, 0, TimeSpan.Zero);
        public override DateTimeOffset GetUtcNow() => _now;
        public void Advance(TimeSpan by) => _now += by;
    }
}
