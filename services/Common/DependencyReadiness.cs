using Confluent.Kafka;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Npgsql;

namespace CandidateScreening.Common;

public sealed record ReadinessSnapshot(bool Database, bool Kafka, DateTimeOffset CheckedAt)
{
    public bool IsReady(DateTimeOffset now) => Database && Kafka
        && CheckedAt <= now && now - CheckedAt <= TimeSpan.FromSeconds(15);
}

public sealed partial class DependencyReadiness(
    NpgsqlDataSource database,
    LabOptions options,
    ILogger<DependencyReadiness> logger) : BackgroundService
{
    private ReadinessSnapshot snapshot = new(false, false, DateTimeOffset.MinValue);

    public ReadinessSnapshot Snapshot => Volatile.Read(ref snapshot);

    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        using var admin = KafkaConfiguration.Admin(options).Build();
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(5));
        try
        {
            do
            {
                var dbTask = CheckDatabaseAsync(stoppingToken);
                // Kafka's metadata API is synchronous; only this dedicated background probe waits for it.
                var kafkaTask = Task.Run(() => CheckKafka(admin), stoppingToken);
                await Task.WhenAll(dbTask, kafkaTask);
                var updated = new ReadinessSnapshot(await dbTask, await kafkaTask, DateTimeOffset.UtcNow);
                var previous = Snapshot;
                Volatile.Write(ref snapshot, updated);
                if (previous.Database != updated.Database || previous.Kafka != updated.Kafka
                    || previous.CheckedAt == DateTimeOffset.MinValue)
                {
                    DependencyState(logger, updated.Database, updated.Kafka);
                }
            } while (await timer.WaitForNextTickAsync(stoppingToken));
        }
        catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested)
        {
            // Host shutdown cancels the probe; the process is no longer ready.
        }
        finally
        {
            Volatile.Write(ref snapshot, new(false, false, DateTimeOffset.UtcNow));
        }
    }

    private async Task<bool> CheckDatabaseAsync(CancellationToken stoppingToken)
    {
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(stoppingToken);
        deadline.CancelAfter(TimeSpan.FromSeconds(2));
        try
        {
            await using var command = database.CreateCommand("SELECT 1");
            return await command.ExecuteScalarAsync(deadline.Token) is int value && value == 1;
        }
        catch (Exception exception) when (exception is NpgsqlException or OperationCanceledException or TimeoutException)
        {
            return false;
        }
    }

    private static bool CheckKafka(IAdminClient admin)
    {
        try
        {
            var metadata = admin.GetMetadata(TimeSpan.FromSeconds(2));
            return metadata.Brokers.Count != 0 && metadata.Brokers.All(broker => broker.BrokerId >= 0);
        }
        catch (KafkaException)
        {
            return false;
        }
    }

    [LoggerMessage(Level = LogLevel.Information, Message = "Dependency readiness changed: database={Database}, kafka={Kafka}")]
    private static partial void DependencyState(ILogger logger, bool database, bool kafka);
}
