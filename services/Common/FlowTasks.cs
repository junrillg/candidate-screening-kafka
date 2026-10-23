namespace CandidateScreening.Common;

public static class FlowTasks
{
    public static async Task RunAsync(IEnumerable<Func<CancellationToken, Task>> loops, CancellationToken stoppingToken)
    {
        using var lifetime = CancellationTokenSource.CreateLinkedTokenSource(stoppingToken);
        var tasks = loops.Select(loop => loop(lifetime.Token)).ToArray();
        try
        {
            await await Task.WhenAny(tasks);
            if (!stoppingToken.IsCancellationRequested)
            { throw new InvalidOperationException("A critical flow loop stopped unexpectedly."); }
        }
        finally
        {
            await lifetime.CancelAsync();
            // Preserve the first failure while draining its canceled siblings.
            try { await Task.WhenAll(tasks); }
            catch (Exception) when (lifetime.IsCancellationRequested) { }
        }
    }
}
