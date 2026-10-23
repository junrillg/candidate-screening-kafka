using System.Threading.Channels;

namespace CandidateScreening.Common;

public sealed class ChangeHub(int capacity = 4096, int ownerCapacity = 4)
{
    private readonly object gate = new();
    private readonly Dictionary<Guid, Dictionary<Guid, Subscription>> owners = [];
    private int count;

    public Subscription Subscribe(Guid owner)
    {
        lock (gate)
        {
            if (count >= capacity || (owners.TryGetValue(owner, out var existing) && existing.Count >= ownerCapacity)) { throw new InvalidOperationException("Stream limit reached."); }
            var subscription = new Subscription(owner, this);
            if (!owners.TryGetValue(owner, out var bucket)) { owners[owner] = bucket = []; }
            bucket.Add(subscription.Id, subscription);
            count++;
            return subscription;
        }
    }

    public void Publish(Guid owner, Guid candidate)
    {
        lock (gate)
        {
            if (!owners.TryGetValue(owner, out var subscriptions)) { return; }
            foreach (var subscription in subscriptions.Values.ToArray())
            {
                if (!subscription.Channel.Writer.TryWrite(candidate)) { subscription.Dispose(); }
            }
        }
    }

    public void Revoke(Guid owner)
    {
        lock (gate)
        {
            if (!owners.TryGetValue(owner, out var subscriptions)) { return; }
            foreach (var subscription in subscriptions.Values.ToArray()) { subscription.Dispose(); }
        }
    }

    public void ReconcileAll()
    {
        lock (gate)
        {
            foreach (var owner in owners.Keys.ToArray()) { Publish(owner, Guid.Empty); }
        }
    }

    public void CloseAll()
    {
        lock (gate)
        {
            foreach (var owner in owners.Keys.ToArray()) { Revoke(owner); }
        }
    }

    private void Remove(Subscription subscription)
    {
        lock (gate)
        {
            if (!owners.TryGetValue(subscription.Owner, out var subscriptions) || !subscriptions.Remove(subscription.Id)) { return; }
            count--;
            if (subscriptions.Count == 0) { owners.Remove(subscription.Owner); }
            subscription.Close();
        }
    }

    public sealed class Subscription : IDisposable
    {
        private readonly ChangeHub hub;
        private readonly CancellationTokenSource closed = new();
        internal Subscription(Guid owner, ChangeHub hub)
        { Owner = owner; this.hub = hub; Closed = closed.Token; }
        public Guid Id { get; } = Guid.NewGuid();
        public Guid Owner { get; }
        public CancellationToken Closed { get; }
        public Channel<Guid> Channel { get; } = System.Threading.Channels.Channel.CreateBounded<Guid>(
            new BoundedChannelOptions(32) { SingleReader = true, FullMode = BoundedChannelFullMode.Wait });
        internal void Close()
        { Channel.Writer.TryComplete(); closed.Cancel(); closed.Dispose(); }
        public void Dispose() => hub.Remove(this);
    }
}
