using Npgsql;

namespace CandidateScreening.Common;

public static class FlowDatabase
{
    public static NpgsqlCommand Command(NpgsqlConnection connection, string sql, params (string Name, object? Value)[] values)
    {
        var command = connection.CreateCommand();
        command.CommandText = sql;
        foreach (var (name, value) in values) { command.Parameters.AddWithValue(name, value ?? DBNull.Value); }
        return command;
    }

    public static async Task ExecuteAsync(NpgsqlConnection connection, string sql, CancellationToken token,
        params (string Name, object? Value)[] values)
    {
        await using var command = Command(connection, sql, values);
        await command.ExecuteNonQueryAsync(token);
    }

    public static async Task AddEventAsync(NpgsqlConnection connection, string topic, FlowEvent message, CancellationToken token) =>
        await ExecuteAsync(connection, "INSERT INTO flow_outbox(id,topic,payload,expires_at) VALUES(@id,@topic,@payload::jsonb,@expires)", token,
            ("id", message.EventId), ("topic", topic), ("payload", FlowJson.Write(message)), ("expires", message.ExpiresAt));

    public static Task InitializeAsync(NpgsqlDataSource data, string service, CancellationToken token) =>
        SchemaMigrations.InitializeAsync(data, service, token);
}
