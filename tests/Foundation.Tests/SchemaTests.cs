using CandidateScreening.Common;
using Npgsql;
using Xunit;

namespace Foundation.Tests;

public sealed class DatabaseFactAttribute : FactAttribute
{
    public DatabaseFactAttribute()
    {
        if (Environment.GetEnvironmentVariable("LAB_SCHEMA_TEST_CONNECTION") is null)
        { Skip = "Requires the opt-in isolated initializer fixture database."; }
    }
}

public sealed class SchemaTests
{
    [DatabaseFact]
    [Trait("Category", "Database")]
    public async Task KnownBaselineUpgradeIsAtomicPreservesDataAndRejectsDrift()
    {
        await using var data = NpgsqlDataSource.Create(Environment.GetEnvironmentVariable("LAB_SCHEMA_TEST_CONNECTION")!);
        using var timeout = new CancellationTokenSource(TimeSpan.FromMinutes(2));
        var token = timeout.Token;
        await using var connection = await data.OpenConnectionAsync(token);
        await FlowDatabase.ExecuteAsync(connection, SchemaMigrations.Resource("baseline") + SchemaMigrations.Resource("candidates"), token);
        var owner = Guid.NewGuid();
        var candidate = Guid.NewGuid();
        await FlowDatabase.ExecuteAsync(connection, """
            INSERT INTO owner_sessions(id,expires_at) VALUES(@owner,now()+interval '7 days');
            INSERT INTO candidates(id,owner_id,request_key,payload_hash,display_name,profile,requirements,status,expires_at)
              VALUES(@candidate,@owner,@key,@hash,'Synthetic migration record','Synthetic retained source','["Kafka"]','Queued',now()+interval '7 days')
            """, token, ("owner", owner), ("candidate", candidate), ("key", Guid.NewGuid()), ("hash", new byte[32]));

        // This hook is test-only and runs inside the actual migration transaction after its SQL.
        await Assert.ThrowsAsync<PostgresException>(() => SchemaMigrations.InitializeAsync(data, "candidates", token,
            (database, cancellation) => FlowDatabase.ExecuteAsync(database, "SELECT 1/0", cancellation)));
        Assert.True(await ScalarAsync<bool>(connection, "SELECT to_regclass('schema_migrations') IS NULL", token));
        Assert.False(await ScalarAsync<bool>(connection, "SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='flow_outbox' AND column_name='exhausted_at')", token));
        await RetainedAsync(connection, candidate, token);

        await SchemaMigrations.InitializeAsync(data, "candidates", token);
        await SchemaMigrations.InitializeAsync(data, "candidates", token);
        Assert.Equal(2L, await ScalarAsync<long>(connection, "SELECT count(*) FROM schema_migrations", token));
        Assert.True(await ScalarAsync<bool>(connection, "SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='flow_outbox' AND column_name='exhausted_at')", token));
        await RetainedAsync(connection, candidate, token);
        await FlowDatabase.ExecuteAsync(connection, "DROP TABLE schema_migrations", token);
        await SchemaMigrations.InitializeAsync(data, "candidates", token);
        await RetainedAsync(connection, candidate, token);

        await FlowDatabase.ExecuteAsync(connection, "UPDATE schema_migrations SET checksum=repeat('0',64) WHERE version=2", token);
        await Assert.ThrowsAsync<InvalidOperationException>(() => SchemaMigrations.InitializeAsync(data, "candidates", token));
        await FlowDatabase.ExecuteAsync(connection, "UPDATE schema_migrations SET checksum=@checksum WHERE version=2", token,
            ("checksum", SchemaMigrations.Checksum(SchemaMigrations.Resource("upgrade002"))));
        await FlowDatabase.ExecuteAsync(connection, "INSERT INTO schema_migrations VALUES(3,repeat('0',64))", token);
        await Assert.ThrowsAsync<InvalidOperationException>(() => SchemaMigrations.InitializeAsync(data, "candidates", token));
        await FlowDatabase.ExecuteAsync(connection, "DELETE FROM schema_migrations WHERE version=3; ALTER TABLE candidates ADD COLUMN unexpected text", token);
        await Assert.ThrowsAsync<InvalidOperationException>(() => SchemaMigrations.InitializeAsync(data, "candidates", token));
        await RetainedAsync(connection, candidate, token);
        await FlowDatabase.ExecuteAsync(connection, "ALTER TABLE candidates DROP COLUMN unexpected", token);
        await SchemaMigrations.InitializeAsync(data, "candidates", token);

        // Only this isolated fixture is returned to the exact pre-ledger local draft shape.
        // The existing outbox exhaustion column and retained candidate must survive adoption.
        await FlowDatabase.ExecuteAsync(connection, "DROP TABLE flow_handler_failures; DROP TABLE schema_migrations", token);
        await Assert.ThrowsAsync<PostgresException>(() => SchemaMigrations.InitializeAsync(data, "candidates", token,
            (database, cancellation) => FlowDatabase.ExecuteAsync(database, "SELECT 1/0", cancellation)));
        Assert.True(await ScalarAsync<bool>(connection, "SELECT to_regclass('schema_migrations') IS NULL AND to_regclass('flow_handler_failures') IS NULL", token));
        Assert.True(await ScalarAsync<bool>(connection, "SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='flow_outbox' AND column_name='exhausted_at')", token));
        await RetainedAsync(connection, candidate, token);

        await FlowDatabase.ExecuteAsync(connection, "ALTER TABLE candidates ADD COLUMN unexpected text", token);
        await Assert.ThrowsAsync<InvalidOperationException>(() => SchemaMigrations.InitializeAsync(data, "candidates", token));
        Assert.True(await ScalarAsync<bool>(connection, "SELECT to_regclass('schema_migrations') IS NULL AND to_regclass('flow_handler_failures') IS NULL", token));
        await RetainedAsync(connection, candidate, token);
        await FlowDatabase.ExecuteAsync(connection, "ALTER TABLE candidates DROP COLUMN unexpected", token);

        await SchemaMigrations.InitializeAsync(data, "candidates", token);
        await SchemaMigrations.InitializeAsync(data, "candidates", token);
        Assert.Equal(2L, await ScalarAsync<long>(connection, "SELECT count(*) FROM schema_migrations", token));
        Assert.True(await ScalarAsync<bool>(connection, "SELECT to_regclass('flow_handler_failures') IS NOT NULL", token));
        await using (var version = FlowDatabase.Command(connection, "SELECT checksum FROM schema_migrations WHERE version=2"))
        { Assert.Equal(SchemaMigrations.Checksum(SchemaMigrations.Resource("upgrade002")), await version.ExecuteScalarAsync(token)); }
        await RetainedAsync(connection, candidate, token);
        Assert.Equal(0L, await ScalarAsync<long>(connection, "SELECT count(*) FROM pg_namespace WHERE nspname LIKE 'lab_expected_%'", token));
    }

    private static async Task RetainedAsync(NpgsqlConnection connection, Guid candidate, CancellationToken token)
    {
        await using var command = FlowDatabase.Command(connection,
            "SELECT profile='Synthetic retained source' AND status='Queued' FROM candidates WHERE id=@id", ("id", candidate));
        Assert.True(await command.ExecuteScalarAsync(token) is true);
    }

    private static async Task<T> ScalarAsync<T>(NpgsqlConnection connection, string sql, CancellationToken token)
    {
        await using var command = FlowDatabase.Command(connection, sql);
        return (T)(await command.ExecuteScalarAsync(token))!;
    }
}
