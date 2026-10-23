using Npgsql;

namespace CandidateScreening.Common;

public sealed record ConsumerSubscription(string Topic, string Group,
    Func<FlowEvent, CancellationToken, Task> Handle, bool Latest = false,
    Func<NpgsqlConnection, FlowEvent, CancellationToken, Task>? Exhausted = null);

public delegate Task ServiceCleanup(NpgsqlConnection connection, CancellationToken token);
