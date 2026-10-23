using System.Security.Cryptography;
using CandidateScreening.Common;
using Microsoft.AspNetCore.Antiforgery;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Routing;
using Npgsql;

namespace CandidateScreening.Candidates;

public sealed class CandidatesFlow(NpgsqlDataSource database, FlowOptions options, ChangeHub hub)
{
    public void Map(IEndpointRouteBuilder endpoints)
    {
        endpoints.MapGet("/api/session", async (HttpContext context, BrowserSession sessions, IAntiforgery csrf) =>
        {
            var owner = context.User.Identity?.IsAuthenticated == true ? BrowserSession.Owner(context) : await sessions.CreateAsync(context);
            return Results.Json(new { ownerId = owner, csrfToken = csrf.GetAndStoreTokens(context).RequestToken });
        });
        endpoints.MapDelete("/api/session", (Delegate)RevokeAsync);
        endpoints.MapPost("/api/candidates", SubmitAsync);
        endpoints.MapGet("/api/candidates", ListAsync);
        endpoints.MapGet("/api/candidates/{id:guid}", GetAsync);
        endpoints.MapGet("/api/candidates/{id:guid}/result", ResultAsync);
        endpoints.MapDelete("/api/candidates/{id:guid}", DeleteAsync);
        endpoints.MapGet("/api/notifications", NotificationsAsync);
        endpoints.MapPost("/api/notifications/{id:guid}/read", ReadNotificationAsync);
        endpoints.MapGet("/api/events", EventsAsync);
        endpoints.MapGet("/internal/candidates/{id:guid}/source", SourceAsync);
    }

    private async Task<IResult> SubmitAsync(HttpContext context, Submission input, BrowserSession sessions)
    {
        Submission normalized;
        try { normalized = FlowJson.Normalize(input); }
        catch (ArgumentException) { return ApiErrors.Error("validation_failed", 400); }
        if (!Guid.TryParseExact(context.Request.Headers["Idempotency-Key"], "D", out var key) || key == Guid.Empty)
        { return ApiErrors.Error("validation_failed", 400); }
        var owner = BrowserSession.Owner(context);
        var token = context.RequestAborted;
        await using var connection = await database.OpenConnectionAsync(token);
        await using var transaction = await connection.BeginTransactionAsync(token);
        if (!await LockSessionAsync(connection, owner, token)) { return ApiErrors.Error("session_expired", 401); }
        var hash = FlowJson.Hash(normalized);
        await using (var command = FlowDatabase.Command(connection,
            "SELECT id,display_name,status,created_at,expires_at,error_code,payload_hash,deleted_at FROM candidates WHERE owner_id=@owner AND request_key=@key",
            ("owner", owner), ("key", key)))
        await using (var reader = await command.ExecuteReaderAsync(token))
        {
            if (await reader.ReadAsync(token))
            {
                if (!CryptographicOperations.FixedTimeEquals(hash, reader.GetFieldValue<byte[]>(6)))
                { return ApiErrors.Error("idempotency_conflict", 409); }
                if (!reader.IsDBNull(7) || reader.GetFieldValue<DateTimeOffset>(4) <= DateTimeOffset.UtcNow)
                { return ApiErrors.Error("not_found", 410); }
                var replay = Summary(reader);
                if (context.Items["sessionExpiresAt"] is DateTimeOffset sessionExpiry) { sessions.RefreshCookie(context, owner, sessionExpiry); }
                return Accepted(replay);
            }
        }
        await using (var limit = FlowDatabase.Command(connection,
            "SELECT count(*) FROM candidates WHERE owner_id=@owner", ("owner", owner)))
        {
            if (Convert.ToInt64(await limit.ExecuteScalarAsync(token), System.Globalization.CultureInfo.InvariantCulture) >= 100)
            { return ApiErrors.Error("rate_limited", 429); }
        }
        if (!await Admission.ReserveAsync(connection, options, false, token)) { return ApiErrors.Error("rate_limited", 429); }
        var id = Guid.NewGuid();
        var now = new DateTimeOffset(DateTimeOffset.UtcNow.UtcTicks / 10 * 10, TimeSpan.Zero);
        var expires = now.AddDays(7);
        await FlowDatabase.ExecuteAsync(connection, """
            INSERT INTO candidates(id,owner_id,request_key,payload_hash,display_name,profile,requirements,status,created_at,expires_at)
            VALUES(@id,@owner,@key,@hash,@name,@profile,@requirements::jsonb,'Queued',@now,@expires)
            """, token, ("id", id), ("owner", owner), ("key", key), ("hash", hash), ("name", normalized.DisplayName),
            ("profile", normalized.Profile), ("requirements", FlowJson.Write(normalized.Requirements)), ("now", now), ("expires", expires));
        await FlowDatabase.ExecuteAsync(connection,
            "UPDATE owner_sessions SET expires_at=greatest(expires_at,@expires) WHERE id=@owner", token, ("expires", expires), ("owner", owner));
        await FlowDatabase.AddEventAsync(connection, "candidate-submitted", FlowEvent.Create(id, owner, "Submitted", expires), token);
        await FlowDatabase.AddEventAsync(connection, "candidate-changed", FlowEvent.Create(id, owner, "Changed", expires), token);
        await transaction.CommitAsync(token);
        sessions.RefreshCookie(context, owner, expires);
        return Accepted(new(id, normalized.DisplayName, "Queued", now, expires, null));
    }

    private static IResult Accepted(CandidateSummary item) => Results.Json(new
    { item.Id, item.DisplayName, status = "Queued", item.CreatedAt, item.ExpiresAt }, statusCode: 202);

    private async Task<IResult> ListAsync(HttpContext context, string? cursor)
    {
        if (!TryCursor(cursor, out var before, out var afterId)) { return ApiErrors.Error("validation_failed", 400); }
        await using var connection = await database.OpenConnectionAsync(context.RequestAborted);
        await using var command = FlowDatabase.Command(connection, """
            SELECT id,display_name,status,created_at,expires_at,error_code FROM candidates
            WHERE owner_id=@owner AND deleted_at IS NULL AND expires_at>now() AND (created_at,id)<(@before,@after)
            ORDER BY created_at DESC,id DESC LIMIT 21
            """, ("owner", BrowserSession.Owner(context)), ("before", before), ("after", afterId));
        await using var reader = await command.ExecuteReaderAsync(context.RequestAborted);
        var items = new List<CandidateSummary>();
        while (await reader.ReadAsync(context.RequestAborted)) { items.Add(Summary(reader)); }
        var more = items.Count > 20;
        if (more) { items.RemoveAt(20); }
        return Results.Json(new { items, nextCursor = more ? Cursor(items[^1].CreatedAt, items[^1].Id) : null });
    }

    private async Task<IResult> GetAsync(Guid id, HttpContext context)
    {
        await using var connection = await database.OpenConnectionAsync(context.RequestAborted);
        await using var command = FlowDatabase.Command(connection, """
            SELECT id,display_name,status,created_at,expires_at,error_code FROM candidates
            WHERE id=@id AND owner_id=@owner AND deleted_at IS NULL AND expires_at>now()
            """, ("id", id), ("owner", BrowserSession.Owner(context)));
        await using var reader = await command.ExecuteReaderAsync(context.RequestAborted);
        return await reader.ReadAsync(context.RequestAborted) ? Results.Json(Summary(reader)) : ApiErrors.Error("not_found", 404);
    }

    private async Task<IResult> ResultAsync(Guid id, HttpContext context)
    {
        await using var connection = await database.OpenConnectionAsync(context.RequestAborted);
        await using var command = FlowDatabase.Command(connection, """
            SELECT result::text FROM candidates WHERE id=@id AND owner_id=@owner AND deleted_at IS NULL AND expires_at>now()
            """, ("id", id), ("owner", BrowserSession.Owner(context)));
        await using var reader = await command.ExecuteReaderAsync(context.RequestAborted);
        if (!await reader.ReadAsync(context.RequestAborted)) { return ApiErrors.Error("not_found", 404); }
        return reader.IsDBNull(0) ? ApiErrors.Error("result_not_ready", 409)
            : Results.Text(reader.GetString(0), "application/json");
    }

    private async Task<IResult> DeleteAsync(Guid id, HttpContext context)
    {
        var token = context.RequestAborted;
        var owner = BrowserSession.Owner(context);
        await using var connection = await database.OpenConnectionAsync(token);
        await using var transaction = await connection.BeginTransactionAsync(token);
        if (!await LockSessionAsync(connection, owner, token)) { return ApiErrors.Error("session_expired", 401); }
        await using var command = FlowDatabase.Command(connection,
            "SELECT expires_at,deleted_at FROM candidates WHERE id=@id AND owner_id=@owner FOR UPDATE", ("id", id), ("owner", owner));
        DateTimeOffset expires;
        bool deleted;
        await using (var reader = await command.ExecuteReaderAsync(token))
        {
            if (!await reader.ReadAsync(token)) { return ApiErrors.Error("not_found", 404); }
            expires = reader.GetFieldValue<DateTimeOffset>(0);
            deleted = !reader.IsDBNull(1);
        }
        if (!deleted) { await DeleteRecordAsync(connection, id, owner, expires, token); }
        await transaction.CommitAsync(token);
        hub.Publish(owner, id);
        return Results.Json(new { id, status = "Deleting" }, statusCode: 202);
    }

    internal static async Task DeleteRecordAsync(NpgsqlConnection connection, Guid id, Guid owner,
        DateTimeOffset expires, CancellationToken token)
    {
        await FlowDatabase.ExecuteAsync(connection, """
            UPDATE candidates SET display_name=NULL,profile=NULL,requirements=NULL,result=NULL,error_code=NULL,
                deleted_at=now(),status='Deleting' WHERE id=@id;
            DELETE FROM notifications WHERE candidate_id=@id
            """, token, ("id", id));
        await FlowDatabase.AddEventAsync(connection, "candidate-submitted", FlowEvent.Create(id, owner, "Deleted", expires), token);
        await FlowDatabase.AddEventAsync(connection, "candidate-changed", FlowEvent.Create(id, owner, "Changed", expires), token);
    }

    private async Task<IResult> RevokeAsync(HttpContext context)
    {
        var owner = BrowserSession.Owner(context);
        var token = context.RequestAborted;
        await using var connection = await database.OpenConnectionAsync(token);
        await using var transaction = await connection.BeginTransactionAsync(token);
        await FlowDatabase.ExecuteAsync(connection, "UPDATE owner_sessions SET revoked=true WHERE id=@id", token, ("id", owner));
        await FlowDatabase.AddEventAsync(connection, "candidate-changed", FlowEvent.Create(Guid.Empty, owner, "SessionRevoked", DateTimeOffset.UtcNow.AddDays(7)), token);
        await transaction.CommitAsync(token);
        hub.Revoke(owner);
        context.Response.Cookies.Delete("lab.owner");
        return Results.NoContent();
    }

    private async Task<IResult> NotificationsAsync(HttpContext context, string? cursor)
    {
        if (!TryCursor(cursor, out var before, out var afterId)) { return ApiErrors.Error("validation_failed", 400); }
        await using var connection = await database.OpenConnectionAsync(context.RequestAborted);
        await using var command = FlowDatabase.Command(connection, """
            SELECT n.id,n.candidate_id,n.status,n.read,n.created_at FROM notifications n JOIN candidates c ON c.id=n.candidate_id
            WHERE n.owner_id=@owner AND c.deleted_at IS NULL AND c.expires_at>now() AND (n.created_at,n.id)<(@before,@after)
            ORDER BY n.created_at DESC,n.id DESC LIMIT 21
            """, ("owner", BrowserSession.Owner(context)), ("before", before), ("after", afterId));
        await using var reader = await command.ExecuteReaderAsync(context.RequestAborted);
        var items = new List<Notice>();
        while (await reader.ReadAsync(context.RequestAborted))
        { items.Add(new(reader.GetGuid(0), reader.GetGuid(1), reader.GetString(2), reader.GetBoolean(3), reader.GetFieldValue<DateTimeOffset>(4))); }
        var more = items.Count > 20;
        if (more) { items.RemoveAt(20); }
        return Results.Json(new { items, nextCursor = more ? Cursor(items[^1].CreatedAt, items[^1].Id) : null });
    }

    private async Task<IResult> ReadNotificationAsync(Guid id, HttpContext context)
    {
        await using var connection = await database.OpenConnectionAsync(context.RequestAborted);
        await using var command = FlowDatabase.Command(connection, """
            UPDATE notifications n SET read=true FROM candidates c WHERE n.id=@id AND n.owner_id=@owner
              AND c.id=n.candidate_id AND c.deleted_at IS NULL AND c.expires_at>now() RETURNING n.id
            """, ("id", id), ("owner", BrowserSession.Owner(context)));
        return await command.ExecuteScalarAsync(context.RequestAborted) is null
            ? ApiErrors.Error("not_found", 404) : Results.NoContent();
    }

    private async Task<IResult> SourceAsync(Guid id, Guid ownerId, HttpContext context)
    {
        if (!ServiceAuthentication.Authorized(context, options)) { return ApiErrors.Error("not_found", 404); }
        await using var connection = await database.OpenConnectionAsync(context.RequestAborted);
        await using var command = FlowDatabase.Command(connection, """
            SELECT profile,requirements::text,expires_at FROM candidates WHERE id=@id AND owner_id=@owner
              AND deleted_at IS NULL AND expires_at>now() AND status IN ('Queued','Screening')
            """, ("id", id), ("owner", ownerId));
        await using var reader = await command.ExecuteReaderAsync(context.RequestAborted);
        if (!await reader.ReadAsync(context.RequestAborted)) { return ApiErrors.Error("not_found", 404); }
        return Results.Json(new SourceRecord(id, ownerId, reader.GetString(0), FlowJson.Read<string[]>(reader.GetString(1)), reader.GetFieldValue<DateTimeOffset>(2)));
    }

    private async Task EventsAsync(HttpContext context, BrowserSession sessions)
    {
        var owner = BrowserSession.Owner(context);
        ChangeHub.Subscription subscription;
        try { subscription = hub.Subscribe(owner); }
        catch (InvalidOperationException) { await ApiErrors.Error("rate_limited", 429).ExecuteAsync(context); return; }
        using (subscription)
        using (var lifetime = CancellationTokenSource.CreateLinkedTokenSource(context.RequestAborted, subscription.Closed))
        {
            // Registration precedes the authoritative check and client reconciliation, closing the catch-up race.
            if (await sessions.ReadAsync(context) != owner) { await ApiErrors.Error("session_expired", 401).ExecuteAsync(context); return; }
            var expires = context.Items["sessionExpiresAt"] is DateTimeOffset sessionExpires ? sessionExpires : DateTimeOffset.UtcNow;
            var remaining = expires - DateTimeOffset.UtcNow;
            if (remaining <= TimeSpan.Zero) { return; }
            lifetime.CancelAfter(remaining < TimeSpan.FromMinutes(15) ? remaining : TimeSpan.FromMinutes(15));
            var token = lifetime.Token;
            context.Response.ContentType = "text/event-stream";
            context.Response.Headers["X-Accel-Buffering"] = "no";
            try
            {
                await WriteEventAsync(context, "retry: " + RandomNumberGenerator.GetInt32(1500, 5001).ToString(System.Globalization.CultureInfo.InvariantCulture) + "\n\n", token);
                await WriteEventAsync(context, "event: reconcile\ndata: {}\n\n", token);
                while (!token.IsCancellationRequested)
                {
                    using var heartbeat = CancellationTokenSource.CreateLinkedTokenSource(token);
                    heartbeat.CancelAfter(TimeSpan.FromSeconds(15));
                    Guid changed;
                    try { changed = await subscription.Channel.Reader.ReadAsync(heartbeat.Token); }
                    catch (OperationCanceledException) when (!token.IsCancellationRequested)
                    {
                        await WriteEventAsync(context, ": heartbeat\n\n", token);
                        continue;
                    }
                    catch (System.Threading.Channels.ChannelClosedException) { break; }
                    await WriteEventAsync(context, changed == Guid.Empty ? "event: reconcile\ndata: {}\n\n"
                        : "event: change\ndata: " + FlowJson.Write(new { candidateId = changed }) + "\n\n", token);
                }
            }
            catch (OperationCanceledException) when (token.IsCancellationRequested) { }
            catch (IOException) { }
        }
    }

    private static async Task WriteEventAsync(HttpContext context, string value, CancellationToken token)
    {
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(token);
        deadline.CancelAfter(TimeSpan.FromSeconds(5));
        try
        {
            await context.Response.WriteAsync(value, deadline.Token);
            await context.Response.Body.FlushAsync(deadline.Token);
        }
        catch (OperationCanceledException) when (!token.IsCancellationRequested)
        { context.Abort(); throw; }
    }

    private static async Task<bool> LockSessionAsync(NpgsqlConnection connection, Guid owner, CancellationToken token)
    {
        await using var command = FlowDatabase.Command(connection,
            "SELECT 1 FROM owner_sessions WHERE id=@owner AND NOT revoked AND expires_at>now() FOR UPDATE", ("owner", owner));
        return await command.ExecuteScalarAsync(token) is not null;
    }

    private static CandidateSummary Summary(NpgsqlDataReader reader) => new(reader.GetGuid(0), reader.GetString(1),
        reader.GetString(2), reader.GetFieldValue<DateTimeOffset>(3), reader.GetFieldValue<DateTimeOffset>(4), reader.IsDBNull(5) ? null : reader.GetString(5));

    public static string Cursor(DateTimeOffset at, Guid id) => at.UtcTicks.ToString(System.Globalization.CultureInfo.InvariantCulture) + "_" + id.ToString("N");

    public static bool TryCursor(string? cursor, out DateTimeOffset at, out Guid id)
    {
        at = DateTimeOffset.UtcNow.AddDays(1);
        id = Guid.Empty;
        if (cursor is null) { return true; }
        if (cursor.Length > 80) { return false; }
        var parts = cursor.Split('_');
        if (parts.Length != 2 || !long.TryParse(parts[0], out var ticks) || ticks < DateTimeOffset.UnixEpoch.Ticks
            || ticks > DateTimeOffset.MaxValue.Ticks || !Guid.TryParseExact(parts[1], "N", out id)) { return false; }
        at = new DateTimeOffset(ticks, TimeSpan.Zero);
        return true;
    }

    private sealed record Notice(Guid Id, Guid CandidateId, string Status, bool Read, DateTimeOffset CreatedAt);
}
