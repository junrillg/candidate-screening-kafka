using CandidateScreening.Common;
using Npgsql;

namespace CandidateScreening.Candidates;

public static class Admission
{
    public static async Task<bool> ReserveAsync(NpgsqlConnection connection, FlowOptions options, bool newSession, CancellationToken token)
    {
        // A short transaction budget applies only to admission; status reads and streams never take this lock.
        await using (var gate = FlowDatabase.Command(connection, "SELECT pg_try_advisory_xact_lock(180722)"))
        { if (await gate.ExecuteScalarAsync(token) is not true) { return false; } }
        if (!newSession)
        {
            await using var queue = FlowDatabase.Command(connection, """
                SELECT count(*) FROM (SELECT 1 FROM candidates WHERE deleted_at IS NULL
                  AND status IN ('Queued','Screening') AND expires_at>now() LIMIT @maximum) pending
                """, ("maximum", options.MaxQueued));
            if (Convert.ToInt64(await queue.ExecuteScalarAsync(token), System.Globalization.CultureInfo.InvariantCulture) >= options.MaxQueued)
            { return false; }
        }
        await using var command = FlowDatabase.Command(connection, """
            INSERT INTO admission_windows(kind,window_at,used) VALUES(@kind,date_trunc('minute',now()),1)
            ON CONFLICT(kind,window_at) DO UPDATE SET used=admission_windows.used+1
              WHERE admission_windows.used<@maximum RETURNING used
            """, ("kind", newSession ? "session" : "submission"),
            ("maximum", newSession ? options.SessionsPerMinute : options.SubmissionsPerMinute));
        return await command.ExecuteScalarAsync(token) is not null;
    }
}
