using System.Security.Cryptography;
using System.Text;
using Npgsql;

namespace CandidateScreening.Common;

public static class SchemaMigrations
{
    private const string Ledger = """
        CREATE TABLE schema_migrations (
          version integer PRIMARY KEY CHECK(version>0), checksum text NOT NULL CHECK(length(checksum)=64));
        """;

    public static string Resource(string name)
    {
        using var stream = typeof(SchemaMigrations).Assembly.GetManifestResourceStream("CandidateScreening." + name + ".sql")
            ?? throw new InvalidOperationException("Missing immutable schema resource.");
        using var reader = new StreamReader(stream);
        return reader.ReadToEnd();
    }

    public static string Checksum(string sql) => Convert.ToHexStringLower(SHA256.HashData(Encoding.UTF8.GetBytes(sql)));

    public static Task InitializeAsync(NpgsqlDataSource data, string service, CancellationToken token) =>
        InitializeAsync(data, service, token, null);

    internal static async Task InitializeAsync(NpgsqlDataSource data, string service, CancellationToken token,
        Func<NpgsqlConnection, CancellationToken, Task>? beforeCommit)
    {
        if (service is not ("candidates" or "screening")) { throw new ArgumentException("Unknown service schema."); }
        var baseline = Resource("baseline") + Resource(service);
        var upgrade = Resource("upgrade002");
        await using var connection = await data.OpenConnectionAsync(token);
        await using var transaction = await connection.BeginTransactionAsync(token);
        await FlowDatabase.ExecuteAsync(connection, "SELECT pg_advisory_xact_lock(180721); SET LOCAL search_path=public", token);
        var actual = await ShapeAsync(connection, "public", token);

        // The server parses the immutable expected definitions. Catalog comparison excludes OIDs,
        // physical storage and platform collation metadata, while retaining logical SQL definitions.
        var scratch = "lab_expected_" + Guid.NewGuid().ToString("N");
        await FlowDatabase.ExecuteAsync(connection, $"CREATE SCHEMA {scratch}; SET LOCAL search_path={scratch}", token);
        await FlowDatabase.ExecuteAsync(connection, baseline, token);
        var baselineShape = await ShapeAsync(connection, scratch, token);
        await FlowDatabase.ExecuteAsync(connection, Ledger, token);
        var baselineLedgerShape = await ShapeAsync(connection, scratch, token);
        await FlowDatabase.ExecuteAsync(connection, upgrade, token);
        var currentLedgerShape = await ShapeAsync(connection, scratch, token);
        await FlowDatabase.ExecuteAsync(connection, "DROP TABLE schema_migrations", token);
        var currentShape = await ShapeAsync(connection, scratch, token);
        // Recognize the pre-ledger outbox-exhaustion schema by its complete shape.
        await FlowDatabase.ExecuteAsync(connection, "DROP TABLE flow_handler_failures", token);
        var legacyShape = await ShapeAsync(connection, scratch, token);
        await FlowDatabase.ExecuteAsync(connection, $"SET LOCAL search_path=public; DROP SCHEMA {scratch} CASCADE", token);

        await using var ledgerExists = FlowDatabase.Command(connection, "SELECT to_regclass('public.schema_migrations') IS NOT NULL");
        var hasLedger = await ledgerExists.ExecuteScalarAsync(token) is true;
        var applied = new List<(int Version, string Checksum)>();
        if (hasLedger)
        {
            if (actual != baselineLedgerShape && actual != currentLedgerShape) { throw Drift(); }
            await using var read = FlowDatabase.Command(connection, "SELECT version,checksum FROM schema_migrations ORDER BY version");
            await using var rows = await read.ExecuteReaderAsync(token);
            while (await rows.ReadAsync(token)) { applied.Add((rows.GetInt32(0), rows.GetString(1))); }
            if (applied.Count is < 1 or > 2 || applied[0] != (1, Checksum(baseline))
                || (applied.Count == 2 && applied[1] != (2, Checksum(upgrade)))
                || actual != (applied.Count == 1 ? baselineLedgerShape : currentLedgerShape))
            { throw Drift(); }
        }
        else
        {
            if (actual.Length == 0) { await FlowDatabase.ExecuteAsync(connection, baseline, token); }
            else if (actual != baselineShape && actual != currentShape && actual != legacyShape) { throw Drift(); }
            await FlowDatabase.ExecuteAsync(connection, Ledger, token);
            await RecordAsync(connection, 1, baseline, token);
            if (actual == legacyShape)
            {
                // Preserve upgrade002's historical bytes/checksum; only its missing table is applied.
                await FlowDatabase.ExecuteAsync(connection, Resource("legacy002completion"), token);
            }
            if (actual == currentShape || actual == legacyShape)
            {
                await RecordAsync(connection, 2, upgrade, token);
                applied.Add((2, Checksum(upgrade)));
            }
        }
        if (applied.All(item => item.Version != 2))
        {
            await FlowDatabase.ExecuteAsync(connection, upgrade, token);
            await RecordAsync(connection, 2, upgrade, token);
        }
        if (await ShapeAsync(connection, "public", token) != currentLedgerShape) { throw Drift(); }
        if (beforeCommit is not null) { await beforeCommit(connection, token); }
        await transaction.CommitAsync(token);
    }

    private static InvalidOperationException Drift() => new("Unsupported database schema version or structural drift.");

    private static Task RecordAsync(NpgsqlConnection connection, int version, string sql, CancellationToken token) =>
        FlowDatabase.ExecuteAsync(connection, "INSERT INTO schema_migrations(version,checksum) VALUES(@version,@checksum)", token,
            ("version", version), ("checksum", Checksum(sql)));

    private static async Task<string> ShapeAsync(NpgsqlConnection connection, string schema, CancellationToken token)
    {
        await using var command = FlowDatabase.Command(connection, """
            SELECT item FROM (
              SELECT 'table|'||c.relname::text||'|'||c.relkind::text||'|'||c.relrowsecurity::text||'|'||c.relforcerowsecurity::text AS item
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=@schema AND c.relkind NOT IN ('i','I','t')
              UNION ALL
              SELECT 'column|'||c.relname::text||'|'||a.attname::text||'|'||a.attnum::text||'|'||format_type(a.atttypid,a.atttypmod)||'|'
                ||a.attnotnull::text||'|'||coalesce(pg_get_expr(d.adbin,d.adrelid),'')||'|'||a.attidentity::text||'|'||a.attgenerated::text
                FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
                LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
                WHERE n.nspname=@schema AND c.relkind IN ('r','p') AND a.attnum>0 AND NOT a.attisdropped
              UNION ALL
              SELECT 'constraint|'||c.relname::text||'|'||x.conname::text||'|'||pg_get_constraintdef(x.oid,true)
                FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=@schema
              UNION ALL
              SELECT 'index|'||c.relname::text||'|'||pg_get_indexdef(i.indexrelid)
                FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=@schema
              UNION ALL
              SELECT 'trigger|'||pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=@schema AND NOT t.tgisinternal
              UNION ALL
              SELECT 'function|'||p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=@schema
              UNION ALL
              SELECT 'policy|'||p.polname FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=@schema
            ) objects ORDER BY item COLLATE "C"
            """, ("schema", schema));
        var objects = new List<string>();
        await using var rows = await command.ExecuteReaderAsync(token);
        while (await rows.ReadAsync(token))
        {
            objects.Add(rows.GetString(0).Replace('"' + schema + "\".", "", StringComparison.Ordinal)
                .Replace(schema + ".", "", StringComparison.Ordinal));
        }
        return string.Join('\n', objects);
    }
}
