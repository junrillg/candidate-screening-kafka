using Confluent.Kafka;
using Confluent.Kafka.Admin;
using Microsoft.Extensions.Configuration;
using Npgsql;

namespace CandidateScreening.Common;

public static class DeploymentInitializer
{
    public static string[] Topics { get; } = ["candidate-submitted", "screening-completed", "candidate-changed", "screening-failed"];
    public static Dictionary<string, string> TopicSettings(bool aws) => new()
    {
        ["retention.ms"] = "86400000",
        ["retention.bytes"] = "8388608",
        ["segment.bytes"] = "8388608",
        ["max.message.bytes"] = "65536",
        ["min.insync.replicas"] = aws ? "2" : "1"
    };

    public static IReadOnlyDictionary<string, string> SafeLoggingSettings { get; } = new Dictionary<string, string>
    {
        ["log_statement"] = "none",
        ["log_min_error_statement"] = "panic",
        ["log_error_verbosity"] = "terse",
        ["log_parameter_max_length"] = "0",
        ["log_parameter_max_length_on_error"] = "0",
        ["log_duration"] = "off",
        ["log_min_duration_statement"] = "-1",
        ["log_min_duration_sample"] = "-1",
        ["log_statement_sample_rate"] = "0",
        ["log_transaction_sample_rate"] = "0"
    };

    public static bool LoggingSettingsAreSafe(IReadOnlyDictionary<string, string> settings) =>
        SafeLoggingSettings.All(pair => settings.TryGetValue(pair.Key, out var value) && value == pair.Value);

    public static async Task RunAsync(IConfiguration configuration)
    {
        var stage = "configuration";
        try
        {
            using var deadline = new CancellationTokenSource(TimeSpan.FromMinutes(4));
            var token = deadline.Token;
            var profile = DeploymentProfile.Read(configuration);
            var host = configuration["Lab:DatabaseHost"] ?? "";
            var masterName = configuration["Lab:BootstrapDatabaseUsername"] ?? "";
            var master = profile.Database(host, "postgres", masterName, Password(configuration, "BootstrapDatabasePassword"));
            master.Pooling = false;
            var lab = LabOptions.Read(configuration, "candidates", master.ConnectionString);
            var credentials = new Dictionary<string, string>
            {
                ["candidates_app"] = Password(configuration, "CandidatesPassword"),
                ["screening_app"] = Password(configuration, "ScreeningPassword"),
                ["candidates_viewer"] = Password(configuration, "CandidatesViewerPassword"),
                ["screening_viewer"] = Password(configuration, "ScreeningViewerPassword")
            };
            stage = "logging_guard";
            await using var connection = new NpgsqlConnection(master.ConnectionString);
            await connection.OpenAsync(token);
            // Managed database administrators cannot SET every logging control. Verify the provisioned
            // parameter group before sending credential-bearing SQL, and refuse unsafe defaults.
            var logging = new Dictionary<string, string>();
            foreach (var name in SafeLoggingSettings.Keys)
            {
                await using var setting = FlowDatabase.Command(connection, "SELECT current_setting(@name)", ("name", name));
                logging[name] = (string)(await setting.ExecuteScalarAsync(token))!;
            }
            if (!LoggingSettingsAreSafe(logging)) { throw new InvalidOperationException("Unsafe initializer database logging configuration."); }
            await FlowDatabase.ExecuteAsync(connection,
                "SET password_encryption='scram-sha-256'; SELECT pg_advisory_lock(180723)", token);
            stage = "roles";
            foreach (var (role, password) in credentials)
            {
                var limit = role.EndsWith("_viewer", StringComparison.Ordinal) ? 3 : 12;
                await using var exists = FlowDatabase.Command(connection, "SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=@role)", ("role", role));
                if (await exists.ExecuteScalarAsync(token) is false)
                {
                    await FlowDatabase.ExecuteAsync(connection, "SELECT set_config('lab.bootstrap_password',@password,false)", token, ("password", password));
                    // Identifiers come exclusively from the fixed dictionary above. The password never enters client SQL text.
                    await FlowDatabase.ExecuteAsync(connection, $"""
                    DO $bootstrap$ BEGIN EXECUTE format('CREATE ROLE {role} LOGIN CONNECTION LIMIT {limit} PASSWORD %L',
                      current_setting('lab.bootstrap_password')); END $bootstrap$
                    """, token);
                    await FlowDatabase.ExecuteAsync(connection, "RESET lab.bootstrap_password", token);
                }
                await RequireAsync(connection, """
                SELECT rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole AND NOT rolreplication
                  AND NOT rolbypassrls AND rolconnlimit=@limit
                  AND NOT EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r.oid)
                FROM pg_roles r WHERE rolname=@role
                """, token, ("role", role), ("limit", limit));
            }
            foreach (var service in new[] { "candidates", "screening" })
            {
                stage = "database_" + service;
                var app = service + "_app";
                var viewer = service + "_viewer";
                await using var existing = FlowDatabase.Command(connection,
                    "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=@database", ("database", service));
                var owner = await existing.ExecuteScalarAsync(token) as string;
                // CREATEROLE administrators receive ADMIN but not SET membership on PostgreSQL 18.
                // Grant only the temporary SET ability needed to create/secure this owned database.
                await FlowDatabase.ExecuteAsync(connection, $"GRANT {app} TO CURRENT_USER WITH SET TRUE, INHERIT FALSE", token);
                try
                {
                    if (owner is null) { await FlowDatabase.ExecuteAsync(connection, $"CREATE DATABASE {service} OWNER {app}", token); }
                    else
                    {
                        if (owner != app) { throw new InvalidOperationException("Unexpected deployment database ownership."); }
                        await RequireAsync(connection, """
                    SELECT NOT EXISTS(SELECT 1 FROM pg_database d,
                      LATERAL aclexplode(coalesce(d.datacl,acldefault('d',d.datdba))) a
                      WHERE d.datname=@database AND a.grantee=0)
                    """, token, ("database", service));
                    }
                    await FlowDatabase.ExecuteAsync(connection, $"SET ROLE {app}", token);
                    await FlowDatabase.ExecuteAsync(connection, $"REVOKE ALL ON DATABASE {service} FROM PUBLIC; GRANT CONNECT ON DATABASE {service} TO {app},{viewer}", token);
                }
                finally
                {
                    await FlowDatabase.ExecuteAsync(connection, $"RESET ROLE; GRANT {app} TO CURRENT_USER WITH SET FALSE, INHERIT FALSE", CancellationToken.None);
                }
                foreach (var role in new[] { app, viewer })
                {
                    await using var verify = new NpgsqlConnection(profile.Database(host, service, role, credentials[role]).ConnectionString);
                    await verify.OpenAsync(token); // Existing passwords are verified, never rotated.
                }
                var ownerConnection = profile.Database(host, service, app, credentials[app]);
                await using var data = NpgsqlDataSource.Create(ownerConnection.ConnectionString);
                stage = "schema_" + service;
                await FlowDatabase.InitializeAsync(data, service, token);
                await using var owned = await data.OpenConnectionAsync(token);
                await FlowDatabase.ExecuteAsync(owned, $"""
                REVOKE CREATE ON SCHEMA public FROM PUBLIC;
                GRANT USAGE ON SCHEMA public TO {viewer}; GRANT SELECT ON ALL TABLES IN SCHEMA public TO {viewer};
                ALTER DEFAULT PRIVILEGES FOR ROLE {app} IN SCHEMA public GRANT SELECT ON TABLES TO {viewer}
                """, token);
                await RequireAsync(connection, "SELECT NOT has_database_privilege(@other,@database,'CONNECT')", token,
                    ("other", service == "candidates" ? "screening_app" : "candidates_app"), ("database", service));
            }
            stage = "topics";
            using var admin = KafkaConfiguration.Admin(lab).Build();
            foreach (var topic in Topics)
            {
                try
                {
                    await admin.CreateTopicsAsync([new TopicSpecification
                { Name = topic, NumPartitions = 32, ReplicationFactor = profile.IsAws ? (short)3 : (short)1, Configs = TopicSettings(profile.IsAws) }],
                        new CreateTopicsOptions
                        { OperationTimeout = TimeSpan.FromSeconds(10), RequestTimeout = TimeSpan.FromSeconds(20) }).WaitAsync(token);
                }
                catch (CreateTopicsException exception) when (exception.Results.All(result => result.Error.Code == ErrorCode.TopicAlreadyExists)) { }
                // Creation can complete before broker metadata/configuration converges. Never repair
                // an incompatible existing topic: the same exact invariants must pass within the bound.
                await WaitForTopicAsync(async cancellation =>
                {
                    cancellation.ThrowIfCancellationRequested();
                    var metadata = admin.GetMetadata(topic, TimeSpan.FromSeconds(2)).Topics.SingleOrDefault(item => item.Topic == topic);
                    cancellation.ThrowIfCancellationRequested();
                    if (metadata is null || metadata.Error.IsError) { return (metadata, new Dictionary<string, string>()); }
                    var settings = await admin.DescribeConfigsAsync([new ConfigResource { Name = topic, Type = ResourceType.Topic }],
                        new DescribeConfigsOptions { RequestTimeout = TimeSpan.FromSeconds(2) }).WaitAsync(cancellation);
                    return (metadata, settings.Single().Entries.ToDictionary(pair => pair.Key, pair => pair.Value.Value));
                }, profile.IsAws, TimeSpan.FromSeconds(20), token);
            }
            await Console.Out.WriteLineAsync("Deployment initialization verified.");
        }
        catch (Exception exception)
        {
            var category = exception switch
            {
                PostgresException database => "database_" + database.SqlState,
                NpgsqlException => "database_connection",
                KafkaException => "broker",
                InvalidOperationException => "state_guard",
                ArgumentException => "configuration",
                OperationCanceledException => "deadline",
                _ => "internal"
            };
            await Console.Error.WriteLineAsync($"Deployment initialization failed: stage={stage}, category={category}.");
            throw;
        }
    }

    internal static async Task WaitForTopicAsync(
        Func<CancellationToken, Task<(TopicMetadata? Metadata, IReadOnlyDictionary<string, string> Settings)>> observe,
        bool aws, TimeSpan timeout, CancellationToken token)
    {
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(token);
        deadline.CancelAfter(timeout);
        var expected = TopicSettings(aws);
        try
        {
            while (true)
            {
                deadline.Token.ThrowIfCancellationRequested();
                try
                {
                    var (metadata, settings) = await observe(deadline.Token).WaitAsync(deadline.Token);
                    deadline.Token.ThrowIfCancellationRequested();
                    if (metadata is not null && metadata.Error.IsError && !TopicNotReady(metadata.Error))
                    { throw new KafkaException(metadata.Error); }
                    if (metadata is not null && !metadata.Error.IsError && metadata.Partitions.Count == 32
                        && metadata.Partitions.All(partition => partition.Replicas.Length == (aws ? 3 : 1))
                        && expected.All(pair => settings.TryGetValue(pair.Key, out var value) && value == pair.Value))
                    { return; }
                }
                catch (DescribeConfigsException exception) when (exception.Results.All(result => TopicNotReady(result.Error))) { }
                catch (KafkaException exception) when (TopicNotReady(exception.Error)) { }
                await Task.Delay(TimeSpan.FromMilliseconds(250), deadline.Token);
            }
        }
        catch (OperationCanceledException) when (deadline.IsCancellationRequested && !token.IsCancellationRequested)
        { throw new InvalidOperationException("Unexpected deployment topic topology or settings."); }
    }

    private static bool TopicNotReady(Error error) => error.Code is ErrorCode.UnknownTopicOrPart
        or ErrorCode.LeaderNotAvailable or ErrorCode.Local_TimedOut or ErrorCode.RequestTimedOut;

    private static string Password(IConfiguration config, string name)
    {
        var value = config["Lab:" + name] ?? "";
        if (name == "BootstrapDatabasePassword")
        {
            if (value.Length is < 1 or > 128 || value.Any(char.IsControl)) { throw new ArgumentException("Invalid initializer credential configuration."); }
        }
        else if (value.Length != 64 || !value.All(Uri.IsHexDigit)) { throw new ArgumentException("Invalid initializer credential configuration."); }
        return value;
    }

    private static async Task RequireAsync(NpgsqlConnection connection, string sql, CancellationToken token,
        params (string Name, object? Value)[] parameters)
    {
        await using var command = FlowDatabase.Command(connection, sql, parameters);
        if (await command.ExecuteScalarAsync(token) is not true) { throw new InvalidOperationException("Unexpected deployment privilege state."); }
    }
}
