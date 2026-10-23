using System.Diagnostics;
using System.Net;
using System.Text.Json;
using CandidateScreening.Common;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;
using Microsoft.Extensions.Logging;
using Npgsql;

namespace CandidateScreening.Screening;

public sealed class ScreeningFlow(NpgsqlDataSource database, FlowOptions options, IHttpClientFactory clients,
    Inference inference, ILogger<ScreeningFlow> logger)
{
    public void Map(IEndpointRouteBuilder endpoints) => endpoints.MapGet("/internal/results/{id:guid}", ResultAsync);

    private async Task<IResult> ResultAsync(Guid id, Guid ownerId, HttpContext context)
    {
        if (!ServiceAuthentication.Authorized(context, options)) { return ApiErrors.Error("not_found", 404); }
        await using var connection = await database.OpenConnectionAsync(context.RequestAborted);
        await using var command = FlowDatabase.Command(connection, """
            SELECT result::text FROM screening_jobs WHERE id=@id AND owner_id=@owner AND state='Completed' AND expires_at>now()
            """, ("id", id), ("owner", ownerId));
        return await command.ExecuteScalarAsync(context.RequestAborted) is string result
            ? Results.Text(result, "application/json") : ApiErrors.Error("not_found", 404);
    }

    public async Task AcceptAsync(FlowEvent message, CancellationToken token)
    {
        if (message.Kind is not ("Submitted" or "Deleted")) { throw new JsonException("Unexpected submission event kind."); }
        await using var connection = await database.OpenConnectionAsync(token);
        await using var transaction = await connection.BeginTransactionAsync(token);
        await using (var command = FlowDatabase.Command(connection,
            "INSERT INTO flow_inbox(id) VALUES(@id) ON CONFLICT DO NOTHING RETURNING id", ("id", message.EventId)))
        { if (await command.ExecuteScalarAsync(token) is null) { return; } }
        if (message.Kind == "Deleted")
        {
            await FlowDatabase.ExecuteAsync(connection, """
                INSERT INTO screening_jobs(id,owner_id,expires_at,state,deleted_at) VALUES(@id,@owner,@expires,'Deleted',now())
                ON CONFLICT(id) DO UPDATE SET state='Deleted',result=NULL,lease_id=NULL,lease_until=NULL,deleted_at=now()
                  WHERE screening_jobs.owner_id=excluded.owner_id
                """, token, ("id", message.CandidateId), ("owner", message.OwnerId), ("expires", message.ExpiresAt));
            await FlowDatabase.AddEventAsync(connection, "screening-completed",
                FlowEvent.Create(message.CandidateId, message.OwnerId, "Deleted", message.ExpiresAt), token);
        }
        else if (message.ExpiresAt > DateTimeOffset.UtcNow)
        {
            await FlowDatabase.ExecuteAsync(connection, """
                INSERT INTO screening_jobs(id,owner_id,expires_at,state) VALUES(@id,@owner,@expires,'Queued') ON CONFLICT DO NOTHING
                """, token, ("id", message.CandidateId), ("owner", message.OwnerId), ("expires", message.ExpiresAt));
        }
        await transaction.CommitAsync(token);
    }

    public async Task ProcessAsync(CancellationToken token)
    {
        while (!token.IsCancellationRequested)
        {
            Work? work = null;
            var started = Stopwatch.GetTimestamp();
            try
            {
                work = await ClaimAsync(token);
                if (work is null) { await Task.Delay(500, token); continue; }
                FlowTransport.Signal(logger, "screening_started", work.Id, work.Id, attempt: work.Attempt);
                if (work.Attempt > 3) { await FailAsync(work, token); continue; }
                using var deadline = CancellationTokenSource.CreateLinkedTokenSource(token);
                deadline.CancelAfter(TimeSpan.FromSeconds(90));
                var client = clients.CreateClient("internal");
                using var request = ServiceAuthentication.Request(options.CandidatesUrl, "internal/candidates/" + work.Id + "/source?ownerId=" + work.Owner, options);
                using var response = await client.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, deadline.Token);
                if (response.StatusCode == HttpStatusCode.NotFound)
                {
                    await FinishDeletedAsync(work, token);
                    continue;
                }
                response.EnsureSuccessStatusCode();
                var bytes = await BoundedHttp.ReadAsync(response.Content, FlowJson.MaximumSourceBytes, deadline.Token);
                var source = FlowJson.Read<SourceRecord>(System.Text.Encoding.UTF8.GetString(bytes));
                if (source.Id != work.Id || source.OwnerId != work.Owner || source.ExpiresAt <= DateTimeOffset.UtcNow)
                { throw new JsonException("Invalid source identity."); }
                _ = FlowJson.Normalize(new("Synthetic", source.Profile, source.Requirements));
                var result = await inference.ScreenAsync(source, deadline.Token);
                var persisted = await CompleteAsync(work, result, token);
                FlowTransport.Signal(logger, persisted ? "screening_completed" : "screening_fenced", work.Id, work.Id, attempt: work.Attempt,
                    elapsedMs: (long)Stopwatch.GetElapsedTime(started).TotalMilliseconds);
            }
            catch (OperationCanceledException) when (token.IsCancellationRequested) { break; }
            catch (Exception exception) when (exception is HttpRequestException or JsonException or ArgumentException or InvalidOperationException
                or OperationCanceledException or NpgsqlException or TimeoutException)
            {
                FlowTransport.Signal(logger, "screening_retry", work?.Id ?? Guid.Empty, work?.Id ?? Guid.Empty,
                    attempt: work?.Attempt ?? 0, elapsedMs: (long)Stopwatch.GetElapsedTime(started).TotalMilliseconds);
                if (work is not null)
                {
                    try { await FailAsync(work, token); }
                    catch (NpgsqlException) { FlowTransport.Signal(logger, "screening_failure_persist_retry"); }
                }
                await Task.Delay(1000, token);
            }
        }
    }

    private async Task<Work?> ClaimAsync(CancellationToken token)
    {
        await using var connection = await database.OpenConnectionAsync(token);
        await using var transaction = await connection.BeginTransactionAsync(token);
        var lease = Guid.NewGuid();
        Work? work;
        await using (var command = FlowDatabase.Command(connection, """
            WITH claimed AS (SELECT id FROM screening_jobs WHERE state IN ('Queued','Screening') AND due_at<=now()
              AND expires_at>now() AND (lease_until IS NULL OR lease_until<now()) ORDER BY due_at FOR UPDATE SKIP LOCKED LIMIT 1)
            UPDATE screening_jobs j SET state='Screening',lease_id=@lease,lease_until=now()+interval '120 seconds',attempts=attempts+1
            FROM claimed WHERE j.id=claimed.id RETURNING j.id,j.owner_id,j.expires_at,j.attempts
            """, ("lease", lease)))
        await using (var reader = await command.ExecuteReaderAsync(token))
        {
            work = await reader.ReadAsync(token) ? new(reader.GetGuid(0), reader.GetGuid(1), reader.GetFieldValue<DateTimeOffset>(2), lease, reader.GetInt32(3)) : null;
        }
        if (work is not null)
        { await FlowDatabase.AddEventAsync(connection, "screening-completed", FlowEvent.Create(work.Id, work.Owner, "Screening", work.Expires), token); }
        await transaction.CommitAsync(token);
        return work;
    }

    private async Task<bool> CompleteAsync(Work work, ScreeningResult result, CancellationToken token)
    {
        await using var connection = await database.OpenConnectionAsync(token);
        await using var transaction = await connection.BeginTransactionAsync(token);
        await using var command = FlowDatabase.Command(connection, """
            UPDATE screening_jobs SET state='Completed',result=@result::jsonb,lease_id=NULL,lease_until=NULL
            WHERE id=@id AND owner_id=@owner AND lease_id=@lease AND state='Screening' AND expires_at>now() RETURNING id
            """, ("result", FlowJson.Write(result)), ("id", work.Id), ("owner", work.Owner), ("lease", work.Lease));
        var persisted = await command.ExecuteScalarAsync(token) is not null;
        if (persisted)
        { await FlowDatabase.AddEventAsync(connection, "screening-completed", FlowEvent.Create(work.Id, work.Owner, "Completed", work.Expires), token); }
        await transaction.CommitAsync(token);
        return persisted;
    }

    private async Task FailAsync(Work work, CancellationToken token)
    {
        await using var connection = await database.OpenConnectionAsync(token);
        await using var transaction = await connection.BeginTransactionAsync(token);
        var terminal = work.Attempt >= 3;
        await using var command = FlowDatabase.Command(connection, """
            UPDATE screening_jobs SET state=@state,result=NULL,lease_id=NULL,lease_until=NULL,due_at=now()+interval '5 seconds'
            WHERE id=@id AND owner_id=@owner AND lease_id=@lease AND state='Screening' AND expires_at>now() RETURNING id
            """, ("state", terminal ? "Failed" : "Queued"), ("id", work.Id), ("owner", work.Owner), ("lease", work.Lease));
        if (await command.ExecuteScalarAsync(token) is not null && terminal)
        { await FlowDatabase.AddEventAsync(connection, "screening-completed", FlowEvent.Create(work.Id, work.Owner, "Failed", work.Expires), token); }
        await transaction.CommitAsync(token);
    }

    private async Task FinishDeletedAsync(Work work, CancellationToken token)
    {
        await using var connection = await database.OpenConnectionAsync(token);
        await using var transaction = await connection.BeginTransactionAsync(token);
        await FlowDatabase.ExecuteAsync(connection, """
            UPDATE screening_jobs SET state='Deleted',result=NULL,lease_id=NULL,lease_until=NULL,deleted_at=now()
            WHERE id=@id AND owner_id=@owner AND lease_id=@lease
            """, token, ("id", work.Id), ("owner", work.Owner), ("lease", work.Lease));
        await FlowDatabase.AddEventAsync(connection, "screening-completed", FlowEvent.Create(work.Id, work.Owner, "Deleted", work.Expires), token);
        await transaction.CommitAsync(token);
    }

    private sealed record Work(Guid Id, Guid Owner, DateTimeOffset Expires, Guid Lease, int Attempt);
}
