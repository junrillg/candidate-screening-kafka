using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Options;
using Npgsql;

namespace CandidateScreening.Common;

public sealed class LabOptions
{
    private LabOptions(string connection, string brokers, string serviceName, Uri? endpoint, DeploymentProfile deployment)
    {
        DatabaseConnection = connection;
        KafkaBootstrapServers = brokers;
        ServiceName = serviceName;
        OtlpEndpoint = endpoint;
        Deployment = deployment;
    }

    public string DatabaseConnection { get; }
    public string KafkaBootstrapServers { get; }
    public string ServiceName { get; }
    public Uri? OtlpEndpoint { get; }
    public DeploymentProfile Deployment { get; }

    public static LabOptions Read(IConfiguration configuration, string expectedServiceName, string? initializerConnection = null)
    {
        var errors = new List<string>();
        var deployment = DeploymentProfile.Read(configuration);
        var connection = initializerConnection ?? configuration["Lab:DatabaseConnection"] ?? "";
        try
        {
            if (deployment.IsAws && initializerConnection is null)
            {
                var password = configuration["Lab:DatabasePassword"] ?? "";
                if (password.Length != 64 || !password.All(Uri.IsHexDigit)) { throw new ArgumentException("Invalid database credential configuration."); }
                connection = deployment.Database(configuration["Lab:DatabaseHost"] ?? "", expectedServiceName,
                    expectedServiceName + "_app", password).ConnectionString;
            }
            var database = new NpgsqlConnectionStringBuilder(connection);
            if (string.IsNullOrWhiteSpace(database.Host) || string.IsNullOrWhiteSpace(database.Database)
                || string.IsNullOrWhiteSpace(database.Username) || string.IsNullOrWhiteSpace(database.Password)
                || database.IncludeErrorDetail || database.LogParameters)
            {
                errors.Add("Lab:DatabaseConnection requires host, database, username and password; sensitive diagnostics must be disabled.");
            }
        }
        catch (ArgumentException)
        {
            errors.Add("Lab:DatabaseConnection is invalid.");
        }

        var brokers = configuration["Lab:KafkaBootstrapServers"] ?? "";
        if (brokers.Length is 0 or > 512 || brokers.Split(',').Any(broker =>
                !Uri.TryCreate("tcp://" + broker, UriKind.Absolute, out var uri)
                || string.IsNullOrEmpty(uri.Host) || uri.Port is < 1 or > 65535
                || uri.AbsolutePath != "/" || uri.UserInfo.Length != 0
                || uri.Query.Length != 0 || uri.Fragment.Length != 0 || broker.Any(char.IsWhiteSpace)))
        {
            errors.Add("Lab:KafkaBootstrapServers requires a bounded comma-separated host:port list.");
        }

        var serviceName = configuration["Lab:ServiceName"] ?? "";
        if (serviceName != expectedServiceName || serviceName is not ("candidates" or "screening"))
        {
            errors.Add("Lab:ServiceName must match the selected service.");
        }

        var endpointValue = configuration["Lab:OtlpEndpoint"] ?? "";
        var endpointValid = Uri.TryCreate(endpointValue, UriKind.Absolute, out var endpoint)
            && endpoint.Scheme is "http" or "https" && !string.IsNullOrEmpty(endpoint.Host)
            && endpoint.AbsolutePath == "/v1/logs" && endpoint.UserInfo.Length == 0
            && endpoint.Query.Length == 0 && endpoint.Fragment.Length == 0;
        if (!deployment.IsAws && initializerConnection is null && !endpointValid)
        {
            errors.Add("Lab:OtlpEndpoint requires an HTTP(S) /v1/logs endpoint without credentials or query.");
        }

        if (deployment.IsAws && brokers.Split(',').Any(broker => !broker.EndsWith(".kafka." + DeploymentProfile.Region + ".amazonaws.com:9098", StringComparison.Ordinal)))
        { errors.Add("Lab:KafkaBootstrapServers requires private IAM broker endpoints for the selected region."); }
        if (errors.Count != 0)
        {
            throw new OptionsValidationException("Lab", typeof(LabOptions), errors);
        }

        return new LabOptions(connection, brokers, serviceName, deployment.IsAws ? null : endpoint, deployment);
    }

    public NpgsqlDataSource CreateDataSource()
    {
        var connection = new NpgsqlConnectionStringBuilder(DatabaseConnection)
        {
            MaxPoolSize = 5,
            MinPoolSize = 0,
            Timeout = 3,
            CommandTimeout = 3,
            IncludeErrorDetail = false,
            LogParameters = false,
            ApplicationName = ServiceName
        };
        return NpgsqlDataSource.Create(connection.ConnectionString);
    }
}
