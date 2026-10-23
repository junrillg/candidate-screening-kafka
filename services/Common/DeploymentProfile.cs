using Microsoft.Extensions.Configuration;
using Npgsql;

namespace CandidateScreening.Common;

public sealed record DeploymentProfile(bool IsAws)
{
    public const string Region = "us-east-1";
    public const string CertificatePath = "/app/certs/rds-global-bundle.pem";

    public static DeploymentProfile Read(IConfiguration configuration)
    {
        var value = configuration["Lab:Deployment"] ?? "Local";
        if (value is not ("Local" or "Aws") || (value == "Aws" && configuration["Lab:AwsRegion"] != Region))
        { throw new ArgumentException("Invalid deployment profile or region configuration."); }
        return new(value == "Aws");
    }

    public NpgsqlConnectionStringBuilder Database(string host, string database, string username, string password)
    {
        if (string.IsNullOrWhiteSpace(host) || host.Length > 253 || Uri.CheckHostName(host) != UriHostNameType.Dns
            || (IsAws && !host.EndsWith("." + Region + ".rds.amazonaws.com", StringComparison.Ordinal))
            || string.IsNullOrWhiteSpace(username) || username.Length > 63
            || username.Any(character => !char.IsAsciiLetterOrDigit(character) && character != '_')
            || password.Length is < 1 or > 128 || password.Any(char.IsControl))
        { throw new ArgumentException("Invalid deployment database configuration."); }
        var connection = new NpgsqlConnectionStringBuilder
        {
            Host = host,
            Port = 5432,
            Database = database,
            Username = username,
            Password = password,
            IncludeErrorDetail = false,
            LogParameters = false,
            Timeout = 5,
            CommandTimeout = 10,
            MaxPoolSize = 5,
            MinPoolSize = 0
        };
        if (IsAws)
        {
            connection.SslMode = SslMode.VerifyFull;
            connection.RootCertificate = CertificatePath;
            connection.GssEncryptionMode = GssEncryptionMode.Disable;
        }
        return connection;
    }
}
