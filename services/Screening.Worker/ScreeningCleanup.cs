using CandidateScreening.Common;
using Npgsql;

namespace CandidateScreening.Screening;

public static class ScreeningCleanup
{
    public static async Task RunAsync(NpgsqlConnection connection, CancellationToken token)
    {
        var expired = new List<(Guid Id, Guid Owner, DateTimeOffset Expires)>();
        await using (var command = FlowDatabase.Command(connection, """
            UPDATE screening_jobs SET state='Deleted',result=NULL,lease_id=NULL,lease_until=NULL,deleted_at=now()
            WHERE id IN (SELECT id FROM screening_jobs WHERE state<>'Deleted' AND expires_at<=now()
              FOR UPDATE SKIP LOCKED LIMIT 100) RETURNING id,owner_id,expires_at
            """))
        await using (var reader = await command.ExecuteReaderAsync(token))
        {
            while (await reader.ReadAsync(token)) { expired.Add((reader.GetGuid(0), reader.GetGuid(1), reader.GetFieldValue<DateTimeOffset>(2))); }
        }
        foreach (var row in expired)
        { await FlowDatabase.AddEventAsync(connection, "screening-completed", FlowEvent.Create(row.Id, row.Owner, "Deleted", row.Expires), token); }
        await FlowDatabase.ExecuteAsync(connection,
            "DELETE FROM screening_jobs WHERE state='Deleted' AND expires_at<now()-interval '8 days'", token);
    }
}
