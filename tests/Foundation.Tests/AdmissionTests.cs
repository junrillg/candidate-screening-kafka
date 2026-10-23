using CandidateScreening.Candidates;
using CandidateScreening.Common;
using Npgsql;
using Xunit;

namespace Foundation.Tests;

public sealed class AdmissionTests
{
    [DatabaseFact]
    [Trait("Category", "Database")]
    public async Task ConcurrentAdmissionWaitsBrieflyWithoutWeakeningQuotasOrSwallowingFailures()
    {
        var settings = new NpgsqlConnectionStringBuilder(Environment.GetEnvironmentVariable("LAB_SCHEMA_TEST_CONNECTION")!)
        { CommandTimeout = 3, Timeout = 3, MaxPoolSize = 2 };
        await using var data = NpgsqlDataSource.Create(settings.ConnectionString);
        using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(30));
        var token = deadline.Token;
        await using var holder = await data.OpenConnectionAsync(token);
        await using var contender = await data.OpenConnectionAsync(token);
        var schema = "lab_admission_" + Guid.NewGuid().ToString("N");
        await FlowDatabase.ExecuteAsync(holder, $"CREATE SCHEMA {schema}; SET search_path={schema}", token);
        try
        {
            await FlowDatabase.ExecuteAsync(holder, SchemaMigrations.Resource("candidates"), token);
            await FlowDatabase.ExecuteAsync(contender, $"SET search_path={schema}; SET lock_timeout='500ms'", token);
            var endpoint = new Uri("http://127.0.0.1:1");
            var options = new FlowOptions
            {
                InternalSecret = new byte[32],
                CandidatesUrl = endpoint,
                ScreeningUrl = endpoint,
                OllamaUrl = endpoint,
                PublicOrigin = endpoint.GetLeftPart(UriPartial.Authority),
                KeysPath = "/unused",
                Model = "synthetic",
                Provider = "Ollama",
                SessionsPerMinute = 1
            };

            // Observe actual lock waiting, then release the competing transaction before the bounded timeout.
            await using (var held = await holder.BeginTransactionAsync(token))
            await using (var transaction = await contender.BeginTransactionAsync(token))
            {
                await FlowDatabase.ExecuteAsync(holder, "SELECT pg_advisory_xact_lock(180722)", token);
                var admission = Admission.ReserveAsync(contender, options, false, token);
                await WaitForLockAsync(holder, contender.ProcessID, admission, token);
                await held.CommitAsync(token);
                Assert.True(await admission);
                Assert.Equal("500ms", await ScalarAsync<string>(contender, "SHOW lock_timeout", token));
                await transaction.CommitAsync(token);
            }

            // A shorter caller timeout remains effective; rejection leaves rollback to the owning caller.
            await FlowDatabase.ExecuteAsync(contender, "SET lock_timeout='50ms'", token);
            await using (var held = await holder.BeginTransactionAsync(token))
            await using (var transaction = await contender.BeginTransactionAsync(token))
            using (var cancellation = CancellationTokenSource.CreateLinkedTokenSource(token))
            {
                await FlowDatabase.ExecuteAsync(holder, "SELECT pg_advisory_xact_lock(180722)", token);
                cancellation.CancelAfter(TimeSpan.FromMilliseconds(150));
                Assert.False(await Admission.ReserveAsync(contender, options, false, cancellation.Token));
                var aborted = await Assert.ThrowsAsync<PostgresException>(() =>
                    ScalarAsync<int>(contender, "SELECT 1", token));
                Assert.Equal(PostgresErrorCodes.InFailedSqlTransaction, aborted.SqlState);
                await transaction.RollbackAsync(token);
                Assert.Equal("50ms", await ScalarAsync<string>(contender, "SHOW lock_timeout", token));
                Assert.Equal(1, await ScalarAsync<int>(contender, "SELECT 1", token));
                await held.CommitAsync(token);
            }

            await using (var transaction = await contender.BeginTransactionAsync(token))
            {
                Assert.True(await Admission.ReserveAsync(contender, options, true, token));
                Assert.False(await Admission.ReserveAsync(contender, options, true, token));
                Assert.Equal(1, await ScalarAsync<int>(contender,
                    "SELECT used FROM admission_windows WHERE kind='session'", token));
                Assert.Equal("50ms", await ScalarAsync<string>(contender, "SHOW lock_timeout", token));
                await transaction.RollbackAsync(token);
            }

            await FlowDatabase.ExecuteAsync(contender, "SET lock_timeout='0'", token);
            await using (var held = await holder.BeginTransactionAsync(token))
            await using (var transaction = await contender.BeginTransactionAsync(token))
            using (var cancellation = CancellationTokenSource.CreateLinkedTokenSource(token))
            {
                await FlowDatabase.ExecuteAsync(holder, "SELECT pg_advisory_xact_lock(180722)", token);
                var admission = Admission.ReserveAsync(contender, options, false, cancellation.Token);
                await WaitForLockAsync(holder, contender.ProcessID, admission, token);
                cancellation.Cancel();
                await Assert.ThrowsAnyAsync<OperationCanceledException>(() => admission);
                await transaction.RollbackAsync(token);
                Assert.Equal("0", await ScalarAsync<string>(contender, "SHOW lock_timeout", token));
                await held.CommitAsync(token);
            }

            await using (var transaction = await contender.BeginTransactionAsync(token))
            {
                await Assert.ThrowsAsync<PostgresException>(() => ScalarAsync<int>(contender, "SELECT 1/0", token));
                var failure = await Assert.ThrowsAsync<PostgresException>(() =>
                    Admission.ReserveAsync(contender, options, false, token));
                Assert.Equal(PostgresErrorCodes.InFailedSqlTransaction, failure.SqlState);
                await transaction.RollbackAsync(token);
            }
        }
        finally
        {
            await FlowDatabase.ExecuteAsync(holder, $"DROP SCHEMA {schema} CASCADE", CancellationToken.None);
        }
    }

    private static async Task WaitForLockAsync(NpgsqlConnection observer, int process, Task admission, CancellationToken token)
    {
        while (!await ScalarAsync<bool>(observer,
            "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=@process AND locktype='advisory' AND NOT granted)", token,
            ("process", process)))
        {
            Assert.False(admission.IsCompleted, "Admission finished before PostgreSQL observed advisory-lock waiting.");
            await Task.Delay(1, token);
        }
    }

    private static async Task<T> ScalarAsync<T>(NpgsqlConnection connection, string sql, CancellationToken token,
        params (string Name, object? Value)[] values)
    {
        await using var command = FlowDatabase.Command(connection, sql, values);
        return (T)(await command.ExecuteScalarAsync(token))!;
    }
}
