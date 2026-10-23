using Npgsql;

namespace CandidateScreening.Common;

public static class HandlerFailures
{
    public static async Task<bool> ExhaustedAsync(NpgsqlDataSource data, Guid id, CancellationToken token)
    {
        await using var connection = await data.OpenConnectionAsync(token);
        await using var command = FlowDatabase.Command(connection,
            "SELECT exhausted_at IS NOT NULL FROM flow_handler_failures WHERE id=@id", ("id", id));
        return await command.ExecuteScalarAsync(token) is true;
    }

    public static async Task<int> RecordAsync(NpgsqlDataSource data, string topic, FlowEvent message,
        Func<NpgsqlConnection, FlowEvent, CancellationToken, Task> handoff, CancellationToken token)
    {
        await using var connection = await data.OpenConnectionAsync(token);
        await using var transaction = await connection.BeginTransactionAsync(token);
        await using var command = FlowDatabase.Command(connection, """
            INSERT INTO flow_handler_failures(id,topic,payload,attempts) VALUES(@id,@topic,@payload::jsonb,1)
            ON CONFLICT(id) DO UPDATE SET attempts=least(10,flow_handler_failures.attempts+1),
              exhausted_at=CASE WHEN flow_handler_failures.attempts>=9 THEN now() ELSE NULL END
            RETURNING attempts
            """, ("id", message.EventId), ("topic", topic), ("payload", FlowJson.Write(message)));
        var attempts = Convert.ToInt32(await command.ExecuteScalarAsync(token), System.Globalization.CultureInfo.InvariantCulture);
        if (attempts >= 10) { await handoff(connection, message, token); }
        await transaction.CommitAsync(token);
        return attempts;
    }

    public static async Task ClearAsync(NpgsqlDataSource data, Guid id, CancellationToken token)
    {
        await using var connection = await data.OpenConnectionAsync(token);
        await FlowDatabase.ExecuteAsync(connection,
            "DELETE FROM flow_handler_failures WHERE id=@id AND exhausted_at IS NULL", token, ("id", id));
    }
}
