using CandidateScreening.Common;
using Npgsql;

namespace CandidateScreening.Candidates;

public static class Admission
{
    public static async Task<bool> ReserveAsync(NpgsqlConnection connection, FlowOptions options, bool newSession, CancellationToken token)
    {
        // Briefly wait for concurrent admissions without extending a stricter caller lock timeout.
        int previousTimeout;
        await using (var setting = FlowDatabase.Command(connection,
            "SELECT setting::integer FROM pg_settings WHERE name='lock_timeout'"))
        { previousTimeout = (int)(await setting.ExecuteScalarAsync(token))!; }
        var timeout = previousTimeout == 0 ? 250 : Math.Min(previousTimeout, 250);
        await FlowDatabase.ExecuteAsync(connection, "SELECT set_config('lock_timeout',@timeout,true)", token,
            ("timeout", timeout.ToString(System.Globalization.CultureInfo.InvariantCulture) + "ms"));
        try
        {
            await using var gate = FlowDatabase.Command(connection, "SELECT pg_advisory_xact_lock(180722)");
            await gate.ExecuteNonQueryAsync(token);
        }
        catch (PostgresException exception) when (exception.SqlState == PostgresErrorCodes.LockNotAvailable)
        { return false; }
        await FlowDatabase.ExecuteAsync(connection, "SELECT set_config('lock_timeout',@timeout,true)", token,
            ("timeout", previousTimeout.ToString(System.Globalization.CultureInfo.InvariantCulture) + "ms"));
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
