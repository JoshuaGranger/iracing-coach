using System.Collections.Concurrent;
using System.Text.Json;

namespace iRacingCoach.Coordinator;

internal enum DetachedBackendOperationPolicy
{
    CancelWhenNoSubscribers,
    CompleteForCache
}

/// <summary>
/// Coalesces identical backend work without transferring cancellation ownership
/// from one subscriber to another. Each operation owns its cancellation source;
/// callers only detach their own waits.
/// </summary>
internal sealed class BackendOperationCoordinator : IDisposable
{
    private readonly ConcurrentDictionary<string, Entry> _operations = new(StringComparer.Ordinal);
    private int _disposed;

    internal int ActiveCount => _operations.Count;

    internal async Task<JsonElement> SubscribeAsync(
        string operationKey,
        Func<CancellationToken, Task<JsonElement>> operation,
        CancellationToken subscriberCancellation,
        DetachedBackendOperationPolicy detachedPolicy = DetachedBackendOperationPolicy.CancelWhenNoSubscribers)
    {
        ObjectDisposedException.ThrowIf(Volatile.Read(ref _disposed) != 0, this);
        subscriberCancellation.ThrowIfCancellationRequested();

        Entry entry;
        while (true)
        {
            var candidate = new Entry();
            entry = _operations.GetOrAdd(operationKey, candidate);
            if (!ReferenceEquals(entry, candidate)) candidate.Discard();

            if (entry.TryAttach(detachedPolicy == DetachedBackendOperationPolicy.CompleteForCache)) break;
            _operations.TryRemove(new KeyValuePair<string, Entry>(operationKey, entry));
        }

        var task = entry.Start(operation, () =>
            _operations.TryRemove(new KeyValuePair<string, Entry>(operationKey, entry)));
        try
        {
            return await task.WaitAsync(subscriberCancellation).ConfigureAwait(false);
        }
        finally
        {
            entry.Detach();
        }
    }

    public void Dispose()
    {
        if (Interlocked.Exchange(ref _disposed, 1) != 0) return;
        foreach (var entry in _operations.Values) entry.ForceCancel();
    }

    private sealed class Entry
    {
        private readonly object _gate = new();
        private readonly CancellationTokenSource _operationCancellation = new();
        private readonly TaskCompletionSource<JsonElement> _completion =
            new(TaskCreationOptions.RunContinuationsAsynchronously);
        private int _subscribers;
        private bool _retainWhenDetached;
        private bool _acceptingSubscribers = true;
        private bool _started;
        private bool _completed;
        private bool _disposed;

        internal bool TryAttach(bool retainWhenDetached)
        {
            lock (_gate)
            {
                if (_disposed || !_acceptingSubscribers) return false;
                _subscribers++;
                _retainWhenDetached |= retainWhenDetached;
                return true;
            }
        }

        internal Task<JsonElement> Start(
            Func<CancellationToken, Task<JsonElement>> operation,
            Action onCompleted)
        {
            var start = false;
            lock (_gate)
            {
                if (!_started)
                {
                    _started = true;
                    start = true;
                }
            }
            if (start) _ = RunAsync(operation, onCompleted);
            return _completion.Task;
        }

        internal void Detach()
        {
            var cancel = false;
            var dispose = false;
            lock (_gate)
            {
                if (_subscribers > 0) _subscribers--;
                cancel = _subscribers == 0 && !_completed && !_retainWhenDetached;
                if (cancel) _acceptingSubscribers = false;
                dispose = _subscribers == 0 && _completed && !_disposed;
                if (dispose) _disposed = true;
            }
            if (cancel) TryCancel();
            if (dispose) _operationCancellation.Dispose();
        }

        internal void ForceCancel() => TryCancel();

        internal void Discard()
        {
            lock (_gate)
            {
                if (_disposed) return;
                _disposed = true;
            }
            _operationCancellation.Cancel();
            _operationCancellation.Dispose();
        }

        private async Task RunAsync(
            Func<CancellationToken, Task<JsonElement>> operation,
            Action onCompleted)
        {
            try
            {
                var result = await operation(_operationCancellation.Token).ConfigureAwait(false);
                MarkCompleted(onCompleted);
                _completion.TrySetResult(result);
            }
            catch (OperationCanceledException) when (_operationCancellation.IsCancellationRequested)
            {
                MarkCompleted(onCompleted);
                _completion.TrySetCanceled(_operationCancellation.Token);
            }
            catch (Exception ex)
            {
                MarkCompleted(onCompleted);
                _completion.TrySetException(ex);
            }
        }

        private void MarkCompleted(Action onCompleted)
        {
            var dispose = false;
            lock (_gate)
            {
                _completed = true;
                dispose = _subscribers == 0 && !_disposed;
                if (dispose) _disposed = true;
            }
            onCompleted();
            if (dispose) _operationCancellation.Dispose();
        }

        private void TryCancel()
        {
            try { _operationCancellation.Cancel(); }
            catch (ObjectDisposedException) { }
        }
    }
}
