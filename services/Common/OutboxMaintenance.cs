using Npgsql;

namespace CandidateScreening.Common;

public static class OutboxMaintenance
{
    public static async Task<int> RunAsync(NpgsqlDataSource database, string[] args, FlowOptions flow,
        Func<NpgsqlConnection, FlowEvent, FlowOptions, CancellationToken, Task>? retryEvent)
    {
        using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(10));
        await using var connection = await database.OpenConnectionAsync(deadline.Token);
        if (args is ["--outbox-failures"])
        {
            await using var command = FlowDatabase.Command(connection, """
                SELECT id,topic FROM flow_outbox WHERE exhausted_at IS NOT NULL AND sent_at IS NULL ORDER BY exhausted_at LIMIT 20
                """);
            await using var reader = await command.ExecuteReaderAsync(deadline.Token);
            while (await reader.ReadAsync(deadline.Token)) { Console.WriteLine("{0:D} {1}", reader.GetGuid(0), reader.GetString(1)); }
            return 0;
        }
        if (args is ["--retry-outbox", var input] && Guid.TryParseExact(input, "D", out var id))
        {
            await using var command = FlowDatabase.Command(connection, """
                UPDATE flow_outbox SET attempts=0,exhausted_at=NULL,lease_id=NULL,lease_until=NULL,due_at=now()
                WHERE id=@id AND exhausted_at IS NOT NULL AND sent_at IS NULL RETURNING id
                """, ("id", id));
            return await command.ExecuteScalarAsync(deadline.Token) is null ? 1 : 0;
        }
        if (args is ["--event-failures"])
        {
            await using var command = FlowDatabase.Command(connection,
                "SELECT id,topic FROM flow_handler_failures WHERE exhausted_at IS NOT NULL ORDER BY exhausted_at LIMIT 20");
            await using var reader = await command.ExecuteReaderAsync(deadline.Token);
            while (await reader.ReadAsync(deadline.Token)) { Console.WriteLine("{0:D} {1}", reader.GetGuid(0), reader.GetString(1)); }
            return 0;
        }
        if (args is ["--retry-event", var eventInput] && Guid.TryParseExact(eventInput, "D", out var eventId))
        {
            await using var transaction = await connection.BeginTransactionAsync(deadline.Token);
            FlowEvent? message = null;
            await using (var lookup = FlowDatabase.Command(connection,
                "SELECT payload::text FROM flow_handler_failures WHERE id=@id AND exhausted_at IS NOT NULL FOR UPDATE", ("id", eventId)))
            {
                if (await lookup.ExecuteScalarAsync(deadline.Token) is string payload) { message = FlowJson.ParseEvent(payload); }
            }
            await using var command = FlowDatabase.Command(connection, """
                WITH retry AS (UPDATE flow_handler_failures SET attempts=0,exhausted_at=NULL
                  WHERE id=@id AND exhausted_at IS NOT NULL
                    AND ((payload->>'expiresAt')::timestamptz>now() OR payload->>'kind'='Deleted')
                  RETURNING id,topic,payload)
                INSERT INTO flow_outbox(id,topic,payload,expires_at)
                  SELECT id,topic,payload,(payload->>'expiresAt')::timestamptz FROM retry
                ON CONFLICT(id) DO UPDATE SET attempts=0,exhausted_at=NULL,sent_at=NULL,lease_id=NULL,lease_until=NULL,due_at=now()
                RETURNING id
                """, ("id", eventId));
            var retried = await command.ExecuteScalarAsync(deadline.Token) is not null;
            if (retried && message is not null && retryEvent is not null)
            { await retryEvent(connection, message, flow, deadline.Token); }
            await transaction.CommitAsync(deadline.Token);
            return retried ? 0 : 1;
        }
        await Console.Error.WriteLineAsync("Expected --outbox-failures, --retry-outbox UUID, --event-failures or --retry-event UUID.");
        return 2;
    }
}
