using System.Text.Json;
using CandidateScreening.Common;
using Npgsql;

namespace CandidateScreening.Candidates;

public sealed class CandidatesCompletion(NpgsqlDataSource database, FlowOptions options, IHttpClientFactory clients)
{
    public async Task HandleAsync(FlowEvent message, CancellationToken token)
    {
        if (message.Kind is not ("Screening" or "Completed" or "Failed" or "Deleted"))
        { throw new JsonException("Unexpected completion event kind."); }
        ScreeningResult? result = null;
        if (message.Kind == "Completed")
        {
            SourceRecord? source = null;
            await using (var lookup = await database.OpenConnectionAsync(token))
            await using (var command = FlowDatabase.Command(lookup, """
                SELECT profile,requirements::text,expires_at FROM candidates WHERE id=@id AND owner_id=@owner
                  AND deleted_at IS NULL AND expires_at>now() AND status IN ('Queued','Screening')
                """, ("id", message.CandidateId), ("owner", message.OwnerId)))
            await using (var reader = await command.ExecuteReaderAsync(token))
            {
                if (await reader.ReadAsync(token))
                { source = new(message.CandidateId, message.OwnerId, reader.GetString(0), FlowJson.Read<string[]>(reader.GetString(1)), reader.GetFieldValue<DateTimeOffset>(2)); }
            }
            if (source is not null)
            {
                using var request = ServiceAuthentication.Request(options.ScreeningUrl, "internal/results/" + message.CandidateId + "?ownerId=" + message.OwnerId, options);
                using var response = await clients.CreateClient("internal").SendAsync(request, HttpCompletionOption.ResponseHeadersRead, token);
                response.EnsureSuccessStatusCode();
                var bytes = await BoundedHttp.ReadAsync(response.Content, 65536, token);
                var proposed = FlowJson.Read<ScreeningResult>(System.Text.Encoding.UTF8.GetString(bytes));
                if (proposed.CandidateId != source.Id || !proposed.ReviewRequired || proposed.Items is null
                    || proposed.Items.Length != source.Requirements.Length || proposed.Provider is not ("Ollama" or "Bedrock")
                    || proposed.Model is null || proposed.Model.Length is < 1 or > 128
                    || proposed.Items.Any(item => item is null))
                { throw new JsonException("Invalid result identity."); }
                for (var index = 0; index < proposed.Items.Length; index++)
                {
                    if (proposed.Items[index].Requirement != source.Requirements[index]) { throw new JsonException("Unexpected result requirement."); }
                }
                result = EvidenceValidation.Validate(source, FlowJson.Write(new
                { items = proposed.Items.Select((item, index) => new { requirementIndex = index, quote = item.Evidence }) }), proposed.Provider, proposed.Model);
            }
        }
        await using var connection = await database.OpenConnectionAsync(token);
        await using var transaction = await connection.BeginTransactionAsync(token);
        await using (var inbox = FlowDatabase.Command(connection,
            "INSERT INTO flow_inbox(id) VALUES(@id) ON CONFLICT DO NOTHING RETURNING id", ("id", message.EventId)))
        { if (await inbox.ExecuteScalarAsync(token) is null) { return; } }
        if (message.Kind == "Deleted")
        {
            await FlowDatabase.ExecuteAsync(connection,
                "UPDATE candidates SET cleanup_confirmed=true WHERE id=@id AND owner_id=@owner AND deleted_at IS NOT NULL", token,
                ("id", message.CandidateId), ("owner", message.OwnerId));
        }
        else if (message.Kind != "Completed" || result is not null)
        {
            await using var update = FlowDatabase.Command(connection, """
                UPDATE candidates SET status=@status,error_code=@error,result=@result::jsonb
                WHERE id=@id AND owner_id=@owner AND deleted_at IS NULL AND expires_at>now()
                  AND status IN ('Queued','Screening') RETURNING id,expires_at
                """, ("id", message.CandidateId), ("owner", message.OwnerId), ("status", message.Kind),
                ("error", message.Kind == "Failed" ? "inference_failed" : null), ("result", result is null ? null : FlowJson.Write(result)));
            DateTimeOffset? expires = null;
            await using (var reader = await update.ExecuteReaderAsync(token))
            { if (await reader.ReadAsync(token)) { expires = reader.GetFieldValue<DateTimeOffset>(1); } }
            if (expires is not null)
            {
                if (message.Kind is "Completed" or "Failed")
                {
                    await FlowDatabase.ExecuteAsync(connection, """
                        INSERT INTO notifications(id,candidate_id,owner_id,status) VALUES(@notice,@id,@owner,@status)
                        ON CONFLICT(candidate_id) DO UPDATE SET status=excluded.status,read=false,created_at=now()
                          WHERE notifications.status<>excluded.status
                        """, token, ("notice", Guid.NewGuid()), ("id", message.CandidateId), ("owner", message.OwnerId), ("status", message.Kind));
                }
                await FlowDatabase.AddEventAsync(connection, "candidate-changed",
                    FlowEvent.Create(message.CandidateId, message.OwnerId, "Changed", expires.Value), token);
            }
        }
        await transaction.CommitAsync(token);
    }

    public static async Task ExhaustedAsync(NpgsqlConnection connection, FlowEvent message, CancellationToken token)
    {
        await using var update = FlowDatabase.Command(connection, """
            UPDATE candidates SET status='Failed',error_code='inference_failed',result=NULL
            WHERE id=@id AND owner_id=@owner AND deleted_at IS NULL AND expires_at>now()
              AND status IN ('Queued','Screening') RETURNING expires_at
            """, ("id", message.CandidateId), ("owner", message.OwnerId));
        DateTimeOffset? expires = null;
        await using (var reader = await update.ExecuteReaderAsync(token))
        { if (await reader.ReadAsync(token)) { expires = reader.GetFieldValue<DateTimeOffset>(0); } }
        if (expires is not null)
        {
            await FlowDatabase.ExecuteAsync(connection, """
                INSERT INTO notifications(id,candidate_id,owner_id,status) VALUES(@notice,@id,@owner,'Failed')
                ON CONFLICT(candidate_id) DO NOTHING
                """, token, ("notice", Guid.NewGuid()), ("id", message.CandidateId), ("owner", message.OwnerId));
            await FlowDatabase.AddEventAsync(connection, "candidate-changed",
                FlowEvent.Create(message.CandidateId, message.OwnerId, "Changed", expires.Value), token);
        }
    }

    public static async Task RetryAsync(NpgsqlConnection connection, FlowEvent message, FlowOptions options, CancellationToken token)
    {
        if (message.Kind != "Completed") { return; }
        await using (var lookup = FlowDatabase.Command(connection, """
            SELECT 1 FROM candidates WHERE id=@id AND owner_id=@owner AND deleted_at IS NULL AND expires_at>now()
              AND status='Failed' AND error_code='inference_failed' FOR UPDATE
            """, ("id", message.CandidateId), ("owner", message.OwnerId)))
        {
            if (await lookup.ExecuteScalarAsync(token) is null) { return; }
        }
        if (!await Admission.ReserveAsync(connection, options, false, token)) { throw new AdmissionRejectedException(); }
        await using var update = FlowDatabase.Command(connection, """
            UPDATE candidates SET status='Screening',error_code=NULL,result=NULL
            WHERE id=@id AND owner_id=@owner AND deleted_at IS NULL AND expires_at>now()
              AND status='Failed' AND error_code='inference_failed' RETURNING expires_at
            """, ("id", message.CandidateId), ("owner", message.OwnerId));
        DateTimeOffset? expires = null;
        await using (var reader = await update.ExecuteReaderAsync(token))
        { if (await reader.ReadAsync(token)) { expires = reader.GetFieldValue<DateTimeOffset>(0); } }
        if (expires is not null)
        {
            await FlowDatabase.AddEventAsync(connection, "candidate-changed",
                FlowEvent.Create(message.CandidateId, message.OwnerId, "Changed", expires.Value), token);
        }
    }
}
