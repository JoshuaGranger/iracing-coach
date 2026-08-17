using System.Text.Json;
using iRacingCoach.Coordinator;

namespace iRacingCoach.Tests;

[TestClass]
public sealed class BackendOperationCoordinatorTests
{
    [TestMethod]
    public async Task CancelingOneSubscriber_DoesNotCancelTheSharedOperation()
    {
        using var coordinator = new BackendOperationCoordinator();
        using var firstCancellation = new CancellationTokenSource();
        var started = NewSignal<CancellationToken>();
        var result = NewSignal<JsonElement>();
        var calls = 0;

        Task<JsonElement> Operation(CancellationToken token)
        {
            Interlocked.Increment(ref calls);
            started.TrySetResult(token);
            return result.Task;
        }

        var first = coordinator.SubscribeAsync("same", Operation, firstCancellation.Token);
        var operationToken = await started.Task;
        var second = coordinator.SubscribeAsync("same", Operation, CancellationToken.None);

        firstCancellation.Cancel();
        await Assert.ThrowsExactlyAsync<TaskCanceledException>(async () => await first);
        Assert.IsFalse(operationToken.IsCancellationRequested);
        Assert.AreEqual(1, calls);

        result.SetResult(JsonSerializer.SerializeToElement(new { value = 7 }));
        Assert.AreEqual(7, (await second).GetProperty("value").GetInt32());
        await WaitUntilAsync(() => coordinator.ActiveCount == 0);
    }

    [TestMethod]
    public async Task CancelingTheLastSubscriber_CancelsOrphanedWork()
    {
        using var coordinator = new BackendOperationCoordinator();
        using var subscriberCancellation = new CancellationTokenSource();
        var operationCanceled = NewSignal();

        async Task<JsonElement> Operation(CancellationToken token)
        {
            using var registration = token.Register(() => operationCanceled.TrySetResult());
            await Task.Delay(Timeout.InfiniteTimeSpan, token);
            return default;
        }

        var subscriber = coordinator.SubscribeAsync("orphan", Operation, subscriberCancellation.Token);
        subscriberCancellation.Cancel();

        await Assert.ThrowsExactlyAsync<TaskCanceledException>(async () => await subscriber);
        await operationCanceled.Task;
        await WaitUntilAsync(() => coordinator.ActiveCount == 0);
    }

    [TestMethod]
    public async Task SubscriberArrivingWhileCanceledWorkUnwinds_StartsFreshWork()
    {
        using var coordinator = new BackendOperationCoordinator();
        using var firstCancellation = new CancellationTokenSource();
        var firstOperationToken = NewSignal<CancellationToken>();
        var firstCancelObserved = NewSignal();
        var firstOperationResult = NewSignal<JsonElement>();
        var secondOperationStarted = NewSignal();
        var calls = 0;

        Task<JsonElement> Operation(CancellationToken token)
        {
            if (Interlocked.Increment(ref calls) == 1)
            {
                firstOperationToken.TrySetResult(token);
                token.Register(() => firstCancelObserved.TrySetResult());
                return firstOperationResult.Task;
            }

            secondOperationStarted.TrySetResult();
            return Task.FromResult(JsonSerializer.SerializeToElement(new { fresh = true }));
        }

        var first = coordinator.SubscribeAsync("retry-window", Operation, firstCancellation.Token);
        var canceledOperationToken = await firstOperationToken.Task;

        await firstCancellation.CancelAsync();
        await Assert.ThrowsExactlyAsync<TaskCanceledException>(async () => await first);
        await firstCancelObserved.Task.WaitAsync(TimeSpan.FromSeconds(5));

        var second = coordinator.SubscribeAsync("retry-window", Operation, CancellationToken.None);
        var started = await Task.WhenAny(secondOperationStarted.Task, Task.Delay(TimeSpan.FromSeconds(5)));
        firstOperationResult.TrySetCanceled(canceledOperationToken);

        Assert.AreSame(secondOperationStarted.Task, started, "the replacement operation never started");
        Assert.AreEqual(2, Volatile.Read(ref calls));
        Assert.IsTrue((await second).GetProperty("fresh").GetBoolean());
        await WaitUntilAsync(() => coordinator.ActiveCount == 0);
    }

    [TestMethod]
    public async Task DurableCacheLease_AllowsDetachedWorkToComplete()
    {
        using var coordinator = new BackendOperationCoordinator();
        using var subscriberCancellation = new CancellationTokenSource();
        var started = NewSignal<CancellationToken>();
        var result = NewSignal<JsonElement>();

        Task<JsonElement> Operation(CancellationToken token)
        {
            started.TrySetResult(token);
            return result.Task;
        }

        var subscriber = coordinator.SubscribeAsync(
            "cacheable",
            Operation,
            subscriberCancellation.Token,
            DetachedBackendOperationPolicy.CompleteForCache);
        var operationToken = await started.Task;
        subscriberCancellation.Cancel();

        await Assert.ThrowsExactlyAsync<TaskCanceledException>(async () => await subscriber);
        Assert.IsFalse(operationToken.IsCancellationRequested);

        result.SetResult(JsonSerializer.SerializeToElement(new { cached = true }));
        await WaitUntilAsync(() => coordinator.ActiveCount == 0);
    }

    [TestMethod]
    public async Task SharedFault_ReachesEverySubscriber_AndAReplacementCanStart()
    {
        using var coordinator = new BackendOperationCoordinator();
        var firstResult = NewSignal<JsonElement>();
        var calls = 0;

        Task<JsonElement> Operation(CancellationToken _)
        {
            Interlocked.Increment(ref calls);
            return firstResult.Task;
        }

        var first = coordinator.SubscribeAsync("fault", Operation, CancellationToken.None);
        var second = coordinator.SubscribeAsync("fault", Operation, CancellationToken.None);
        firstResult.SetException(new InvalidDataException("synthetic fault"));

        await Assert.ThrowsExactlyAsync<InvalidDataException>(async () => await first);
        await Assert.ThrowsExactlyAsync<InvalidDataException>(async () => await second);
        await WaitUntilAsync(() => coordinator.ActiveCount == 0);

        var replacement = await coordinator.SubscribeAsync(
            "fault",
            _ => Task.FromResult(JsonSerializer.SerializeToElement(new { recovered = true })),
            CancellationToken.None);
        Assert.IsTrue(replacement.GetProperty("recovered").GetBoolean());
        Assert.AreEqual(1, calls);
    }

    [TestMethod]
    public async Task Dispose_CancelsEvenRetainedDetachedWork()
    {
        var coordinator = new BackendOperationCoordinator();
        var started = NewSignal<CancellationToken>();

        async Task<JsonElement> Operation(CancellationToken token)
        {
            started.TrySetResult(token);
            await Task.Delay(Timeout.InfiniteTimeSpan, token);
            return default;
        }

        var subscriber = coordinator.SubscribeAsync(
            "shutdown",
            Operation,
            CancellationToken.None,
            DetachedBackendOperationPolicy.CompleteForCache);
        var token = await started.Task;

        coordinator.Dispose();

        await Assert.ThrowsExactlyAsync<TaskCanceledException>(async () => await subscriber);
        Assert.IsTrue(token.IsCancellationRequested);
        await Assert.ThrowsExactlyAsync<ObjectDisposedException>(async () =>
            await coordinator.SubscribeAsync("new", Operation, CancellationToken.None));
    }

    private static TaskCompletionSource NewSignal() =>
        new(TaskCreationOptions.RunContinuationsAsynchronously);

    private static TaskCompletionSource<T> NewSignal<T>() =>
        new(TaskCreationOptions.RunContinuationsAsynchronously);

    private static async Task WaitUntilAsync(Func<bool> condition)
    {
        for (var index = 0; index < 1000 && !condition(); index++) await Task.Yield();
        Assert.IsTrue(condition());
    }
}
