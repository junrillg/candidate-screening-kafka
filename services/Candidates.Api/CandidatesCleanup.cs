using CandidateScreening.Common;
using Npgsql;

namespace CandidateScreening.Candidates;

public static class CandidatesCleanup
{
    public static async Task RunAsync(NpgsqlConnection connection, CancellationToken token)
    {
        var expired = new List<(Guid Id, Guid Owner, DateTimeOffset Expires)>();
        await using (var command = FlowDatabase.Command(connection, """
            SELECT id,owner_id,expires_at FROM candidates WHERE deleted_at IS NULL AND expires_at<=now()
            ORDER BY expires_at FOR UPDATE SKIP LOCKED LIMIT 100
            """))
        await using (var reader = await command.ExecuteReaderAsync(token))
        {
            while (await reader.ReadAsync(token)) { expired.Add((reader.GetGuid(0), reader.GetGuid(1), reader.GetFieldValue<DateTimeOffset>(2))); }
        }
        foreach (var row in expired) { await CandidatesFlow.DeleteRecordAsync(connection, row.Id, row.Owner, row.Expires, token); }
        await FlowDatabase.ExecuteAsync(connection, """
            DELETE FROM admission_windows WHERE window_at<now()-interval '1 day';
            DELETE FROM candidates c WHERE deleted_at IS NOT NULL AND cleanup_confirmed AND expires_at<now()-interval '8 days'
              AND EXISTS(SELECT 1 FROM owner_sessions s WHERE s.id=c.owner_id AND (s.revoked OR s.expires_at<now()));
            DELETE FROM owner_sessions s WHERE s.expires_at<now()-interval '8 days' AND NOT EXISTS(SELECT 1 FROM candidates c WHERE c.owner_id=s.id)
            """, token);
    }
}
