using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Confluent.Kafka;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Npgsql;

namespace CandidateScreening.Common;

public sealed partial class FlowTransport(NpgsqlDataSource database, LabOptions lab,
    IEnumerable<ConsumerSubscription> consumers, IEnumerable<Func<CancellationToken, Task>> workers,
    ServiceCleanup cleanup, ChangeHub hub, ILogger<FlowTransport> logger) : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stoppingToken)
    {
        var loops = new List<Func<CancellationToken, Task>> { RelayAsync, CleanupAsync };
        loops.AddRange(consumers.Select(subscription => (Func<CancellationToken, Task>)(token => ConsumeAsync(subscription, token))));
        loops.AddRange(workers);
        try { await FlowTasks.RunAsync(loops, stoppingToken); }
        catch (OperationCanceledException) when (stoppingToken.IsCancellationRequested) { }
        catch (Exception exception)
        { Signal(logger, "critical_loop_failed_" + exception.GetType().Name); throw; }
    }

    private async Task RelayAsync(CancellationToken token)
    {
        using var producer = KafkaConfiguration.Producer(lab).Build();
        while (!token.IsCancellationRequested)
        {
            try
            {
                var lease = Guid.NewGuid();
                Guid id;
                string topic;
                string payload;
                int attempt;
                bool recoveredExhaustion;
                await using (var connection = await database.OpenConnectionAsync(token))
                {
                    await using var command = FlowDatabase.Command(connection, """
                        WITH claimed AS (SELECT id,attempts FROM flow_outbox WHERE sent_at IS NULL AND exhausted_at IS NULL AND due_at<=now()
                          AND (lease_until IS NULL OR lease_until<now()) ORDER BY due_at FOR UPDATE SKIP LOCKED LIMIT 1)
                        UPDATE flow_outbox o SET lease_id=@lease,lease_until=now()+interval '30 seconds',attempts=least(10,o.attempts+1)
                        FROM claimed WHERE o.id=claimed.id RETURNING o.id,o.topic,o.payload::text,o.attempts,claimed.attempts
                        """, ("lease", lease));
                    await using var reader = await command.ExecuteReaderAsync(token);
                    if (!await reader.ReadAsync(token)) { await Task.Delay(250, token); continue; }
                    id = reader.GetGuid(0); topic = reader.GetString(1); payload = reader.GetString(2);
                    attempt = reader.GetInt32(3);
                    recoveredExhaustion = reader.GetInt32(4) >= 10;
                }
                var message = FlowJson.Read<FlowEvent>(payload);
                var started = Stopwatch.GetTimestamp();
                if (recoveredExhaustion)
                {
                    await using var connection = await database.OpenConnectionAsync(token);
                    await FlowDatabase.ExecuteAsync(connection,
                        "UPDATE flow_outbox SET exhausted_at=now(),lease_id=NULL,lease_until=NULL WHERE id=@id AND lease_id=@lease", token,
                        ("id", id), ("lease", lease));
                    Signal(logger, "outbox_exhausted", id, message.CandidateId, id, attempt);
                    continue;
                }
                try
                {
                    await producer.ProduceAsync(topic, new Message<string, string> { Key = message.CandidateId.ToString("N"), Value = payload }, token);
                    await using var connection = await database.OpenConnectionAsync(token);
                    await FlowDatabase.ExecuteAsync(connection,
                        "UPDATE flow_outbox SET sent_at=now(),lease_until=NULL,lease_id=NULL WHERE id=@id AND lease_id=@lease", token,
                        ("id", id), ("lease", lease));
                    Signal(logger, "outbox_published", id, message.CandidateId, id, attempt, (long)Stopwatch.GetElapsedTime(started).TotalMilliseconds);
                }
                catch (Exception exception) when (exception is KafkaException or NpgsqlException or HttpRequestException)
                {
                    await using var connection = await database.OpenConnectionAsync(token);
                    await FlowDatabase.ExecuteAsync(connection, """
                        UPDATE flow_outbox SET lease_until=NULL,lease_id=NULL,
                          exhausted_at=CASE WHEN attempts>=10 THEN now() ELSE NULL END,
                          due_at=now()+(least(60,power(2,least(attempts,6)))+@jitter) * interval '1 second'
                        WHERE id=@id AND lease_id=@lease
                        """, token, ("id", id), ("lease", lease), ("jitter", Random.Shared.NextDouble()));
                    Signal(logger, attempt >= 10 ? "outbox_exhausted" : "outbox_retry", id, message.CandidateId, id,
                        attempt, (long)Stopwatch.GetElapsedTime(started).TotalMilliseconds);
                }
            }
            catch (Exception exception) when (exception is NpgsqlException or TimeoutException)
            { Signal(logger, "outbox_unavailable"); await Task.Delay(1000, token); }
        }
    }

    private Task ConsumeAsync(ConsumerSubscription subscription, CancellationToken token) => Task.Run(async () =>
    {
        var topic = subscription.Topic;
        var group = subscription.Group;
        var latest = subscription.Latest;
        var handle = subscription.Handle;
        using var consumer = KafkaConfiguration.Consumer(lab, group, latest).SetLogHandler((_, _) => { }).SetErrorHandler((_, error) => { if (latest && error.IsError) { hub.CloseAll(); } })
            .SetPartitionsAssignedHandler((_, _) => { if (latest) { hub.ReconcileAll(); } })
            .SetPartitionsRevokedHandler((_, _) => { if (latest) { hub.ReconcileAll(); } }).Build();
        consumer.Subscribe(topic);
        var retryAttempt = 0;
        try
        {
            while (!token.IsCancellationRequested)
            {
                ConsumeResult<string, string>? record = null;
                FlowEvent? message = null;
                var applied = false;
                var boundedHandler = false;
                var started = Stopwatch.GetTimestamp();
                try
                {
                    record = consumer.Consume(TimeSpan.FromMilliseconds(200));
                    if (record is null) { continue; }
                    try
                    {
                        message = FlowJson.ParseEvent(record.Message.Value);
                        boundedHandler = subscription.Exhausted is not null && message.Kind == "Completed";
                        var validKind = topic switch
                        {
                            "candidate-submitted" => message.Kind is "Submitted" or "Deleted",
                            "screening-completed" => message.Kind is "Screening" or "Completed" or "Failed" or "Deleted",
                            _ => message.Kind is "Changed" or "SessionRevoked"
                        };
                        if (!validKind) { throw new JsonException("Unexpected event kind."); }
                    }
                    catch (Exception exception) when (exception is JsonException or ArgumentException)
                    {
                        await using var connection = await database.OpenConnectionAsync(token);
                        await FlowDatabase.ExecuteAsync(connection,
                            "INSERT INTO flow_poison(fingerprint) VALUES(@hash) ON CONFLICT DO NOTHING", token,
                            ("hash", SHA256.HashData(Encoding.UTF8.GetBytes(record.Message.Value ?? ""))));
                        message = null;
                        Signal(logger, "invalid_event_quarantined");
                    }
                    if (message is not null)
                    {
                        if (boundedHandler && await HandlerFailures.ExhaustedAsync(database, message.EventId, token))
                        { consumer.Commit(record); continue; }
                        await handle(message, token);
                        applied = true;
                        if (boundedHandler) { await HandlerFailures.ClearAsync(database, message.EventId, token); }
                    }
                    consumer.Commit(record);
                    if (message is not null)
                    { Signal(logger, "event_applied", message.EventId, message.CandidateId, message.EventId, retryAttempt + 1, (long)Stopwatch.GetElapsedTime(started).TotalMilliseconds); }
                    retryAttempt = 0;
                }
                catch (Exception exception) when (exception is KafkaException or NpgsqlException or HttpRequestException or TimeoutException or JsonException)
                {
                    Signal(logger, "consumer_retry", message?.EventId ?? Guid.Empty, message?.CandidateId ?? Guid.Empty,
                        message?.EventId ?? Guid.Empty, ++retryAttempt, (long)Stopwatch.GetElapsedTime(started).TotalMilliseconds);
                    if (record is not null)
                    {
                        if (message is not null && boundedHandler && !applied)
                        {
                            try
                            {
                                var attempts = await HandlerFailures.RecordAsync(database, topic, message, subscription.Exhausted!, token);
                                if (attempts >= 10)
                                {
                                    Signal(logger, "event_exhausted", message.EventId, message.CandidateId, message.EventId, attempts);
                                    consumer.Commit(record);
                                    retryAttempt = 0;
                                    continue;
                                }
                            }
                            catch (Exception unavailable) when (unavailable is NpgsqlException or TimeoutException or KafkaException)
                            { Signal(logger, "handler_retry_state_unavailable", message.EventId, message.CandidateId, message.EventId); }
                        }
                        try { consumer.Seek(new TopicPartitionOffset(record.TopicPartition, record.Offset)); }
                        catch (KafkaException) { }
                    }
                    await Task.Delay(TimeSpan.FromMilliseconds(1000 + Random.Shared.Next(1000)), token);
                }
                catch (OperationCanceledException) when (!token.IsCancellationRequested)
                {
                    if (record is not null) { consumer.Seek(new TopicPartitionOffset(record.TopicPartition, record.Offset)); }
                    await Task.Delay(1000, token);
                }
            }
        }
        finally { consumer.Close(); }
    }, token);

    private async Task CleanupAsync(CancellationToken token)
    {
        using var timer = new PeriodicTimer(TimeSpan.FromSeconds(5));
        do
        {
            try
            {
                await using var connection = await database.OpenConnectionAsync(token);
                await using var transaction = await connection.BeginTransactionAsync(token);
                await cleanup(connection, token);
                await FlowDatabase.ExecuteAsync(connection, """
                    DELETE FROM flow_outbox WHERE sent_at IS NOT NULL AND created_at<now()-interval '8 days';
                    DELETE FROM flow_inbox WHERE received_at<now()-interval '8 days';
                    DELETE FROM flow_poison WHERE received_at<now()-interval '8 days';
                    DELETE FROM flow_handler_failures WHERE received_at<now()-interval '8 days'
                    """, token);
                await transaction.CommitAsync(token);
            }
            catch (Exception exception) when (exception is NpgsqlException or TimeoutException)
            { Signal(logger, "cleanup_retry"); }
        } while (await timer.WaitForNextTickAsync(token));
    }

    [LoggerMessage(Level = LogLevel.Information, Message = "Flow operation: {State}, operation={OperationId}, candidate={CandidateId}, event={EventId}, attempt={Attempt}, elapsedMs={ElapsedMs}")]
    public static partial void Signal(ILogger logger, string state, Guid operationId = default,
        Guid candidateId = default, Guid eventId = default, int attempt = 0, long elapsedMs = 0);
}
