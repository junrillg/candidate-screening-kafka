using System.Text.Json;
using Amazon.BedrockRuntime;
using Amazon.BedrockRuntime.Model;
using CandidateScreening.Common;
using CandidateScreening.Screening;
using Confluent.Kafka;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Hosting.Server;
using Microsoft.AspNetCore.Hosting.Server.Features;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using Npgsql;
using Xunit;

namespace Foundation.Tests;

public sealed class CloudTests
{
    [Fact]
    public void InitializerRefusesMissingOrUnsafeDatabaseLoggingControls()
    {
        var safe = DeploymentInitializer.SafeLoggingSettings.ToDictionary();
        Assert.True(DeploymentInitializer.LoggingSettingsAreSafe(safe));
        foreach (var name in safe.Keys)
        {
            var missing = safe.Where(pair => pair.Key != name).ToDictionary();
            Assert.False(DeploymentInitializer.LoggingSettingsAreSafe(missing));
            var unsafeSettings = safe.ToDictionary();
            unsafeSettings[name] = "unsafe";
            Assert.False(DeploymentInitializer.LoggingSettingsAreSafe(unsafeSettings));
        }
        Assert.Equal("panic", safe["log_min_error_statement"]);
        Assert.Equal("0", safe["log_parameter_max_length_on_error"]);
    }

    [Fact]
    public void CryptographyLoadsPatchedAssembliesFromTheSharedFramework()
    {
        var shared = Path.GetFullPath(Path.Combine(Path.GetDirectoryName(typeof(WebApplication).Assembly.Location)!, "../.."))
            + Path.DirectorySeparatorChar;
        foreach (var assembly in new[] { typeof(System.Security.Cryptography.Xml.SignedXml).Assembly,
            System.Reflection.Assembly.Load("System.Security.Cryptography.Pkcs") })
        {
            Assert.StartsWith(shared, assembly.Location);
            var version = assembly.GetCustomAttributes(typeof(System.Reflection.AssemblyInformationalVersionAttribute), false)
                .Cast<System.Reflection.AssemblyInformationalVersionAttribute>().Single().InformationalVersion.Split('+')[0];
            Assert.True(Version.Parse(version) >= new Version(10, 0, 13), "Loaded cryptography framework must include the supported security patches.");
        }
    }

    private static Dictionary<string, string?> Configuration() => new()
    {
        ["Lab:Deployment"] = "Aws",
        ["Lab:AwsRegion"] = "us-east-1",
        ["Lab:ServiceName"] = "candidates",
        ["Lab:DatabaseHost"] = "synthetic.example.us-east-1.rds.amazonaws.com",
        ["Lab:DatabasePassword"] = new string('a', 64),
        ["Lab:KafkaBootstrapServers"] = "b-1.synthetic.example.kafka.us-east-1.amazonaws.com:9098",
        ["Lab:InternalSecret"] = new string('b', 64),
        ["Lab:PublicOrigin"] = "https://example.invalid",
        ["Lab:AiProvider"] = "Bedrock",
        ["Lab:Model"] = "amazon.nova-micro-v1:0",
        ["Lab:CandidatesUrl"] = "http://candidates:8080",
        ["Lab:ScreeningUrl"] = "http://screening:8080",
        ["Lab:DataProtectionPrefix"] = "/synthetic-lab/test/keys",
        ["Lab:DataProtectionKmsKeyArn"] = "arn:aws:kms:us-east-1:000000000000:key/00000000-0000-0000-0000-000000000000"
    };

    [Fact]
    public async Task ExplicitCloudProfileRequiresVerifiedTransportAndSeparateSecretFields()
    {
        var config = new ConfigurationBuilder().AddInMemoryCollection(Configuration()).Build();
        var options = LabOptions.Read(config, "candidates");
        Assert.True(options.Deployment.IsAws);
        Assert.Null(options.OtlpEndpoint);
        await using var source = options.CreateDataSource();
        var database = new NpgsqlConnectionStringBuilder(source.ConnectionString);
        Assert.Equal(SslMode.VerifyFull, database.SslMode);
        Assert.Equal(DeploymentProfile.CertificatePath, database.RootCertificate);
        Assert.Equal("candidates_app", database.Username);
        Assert.Equal("candidates", database.Database);
        Assert.False(database.IncludeErrorDetail);
        Assert.False(database.LogParameters);
        var transport = KafkaConfiguration.Transport(options);
        Assert.Equal(SecurityProtocol.SaslSsl, transport.SecurityProtocol);
        Assert.Equal(SaslMechanism.OAuthBearer, transport.SaslMechanism);
        var flow = FlowOptions.Read(config, "candidates");
        Assert.Equal("Bedrock", flow.Provider);
        Assert.Equal("/synthetic-lab/test/keys", flow.DataProtectionPrefix);
    }

    [Theory]
    [InlineData("Lab:AwsRegion", "other-region")]
    [InlineData("Lab:Deployment", "automatic")]
    public void CloudProfileHasNoImplicitRegionOrDeploymentFallback(string key, string value)
    {
        var values = Configuration(); values[key] = value;
        Assert.Throws<ArgumentException>(() => DeploymentProfile.Read(new ConfigurationBuilder().AddInMemoryCollection(values).Build()));
    }

    [Theory]
    [InlineData("Lab:PublicOrigin", "http://example.invalid")]
    [InlineData("Lab:CandidatesUrl", "https://external.invalid")]
    [InlineData("Lab:AiProvider", "Ollama")]
    [InlineData("Lab:Model", "other-model")]
    [InlineData("Lab:DataProtectionPrefix", "/")]
    [InlineData("Lab:DataProtectionKmsKeyArn", "alias/aws/ssm")]
    public void IncompatibleCloudCombinationsFailBeforeClientConstruction(string key, string value)
    {
        var values = Configuration(); values[key] = value;
        Assert.Throws<ArgumentException>(() => FlowOptions.Read(new ConfigurationBuilder().AddInMemoryCollection(values).Build(), "candidates"));
    }

    [Fact]
    public void DefaultProfileDoesNotSelectCloudTransport()
    {
        var values = new Dictionary<string, string?>
        {
            ["Lab:DatabaseConnection"] = "Host=localhost;Database=candidates;Username=app;Password=synthetic",
            ["Lab:KafkaBootstrapServers"] = "kafka:9092",
            ["Lab:ServiceName"] = "candidates",
            ["Lab:OtlpEndpoint"] = "http://collector:4318/v1/logs"
        };
        var options = LabOptions.Read(new ConfigurationBuilder().AddInMemoryCollection(values).Build(), "candidates");
        Assert.False(options.Deployment.IsAws);
        Assert.Null(KafkaConfiguration.Transport(options).SaslMechanism);
    }

    [Fact]
    public void BedrockRequestTreatsNarrativeAsDataAndResponseUsesCommonTrustBoundary()
    {
        var source = new SourceRecord(Guid.NewGuid(), Guid.NewGuid(), "I built Kafka consumers.", ["Kafka"], DateTimeOffset.UtcNow.AddDays(1));
        var request = BedrockInference.Request(source, "amazon.nova-micro-v1:0");
        Assert.Null(request.ToolConfig);
        Assert.DoesNotContain(source.Profile, request.System.Single().Text);
        Assert.Equal(ConversationRole.User, request.Messages.Single().Role);
        Assert.Contains(source.Profile, request.Messages.Single().Content.Single().Text);
        var response = new ConverseResponse
        {
            StopReason = StopReason.End_turn,
            Output = new ConverseOutput
            {
                Message = new Message
                {
                    Role = ConversationRole.Assistant,
                    Content = [new ContentBlock { Text = "{\"items\":[{\"requirementIndex\":0,\"quote\":\"I built Kafka consumers.\"}]}" }]
                }
            }
        };
        var result = BedrockInference.Validate(source, response, request.ModelId);
        Assert.True(result.ReviewRequired);
        Assert.Equal("Bedrock", result.Provider);
        Assert.Equal("EvidenceFound", result.Items.Single().State);
        response.StopReason = StopReason.Max_tokens;
        Assert.Throws<JsonException>(() => BedrockInference.Validate(source, response, request.ModelId));
        response.StopReason = StopReason.End_turn;
        response.Output.Message.Content[0].Text = "{\"items\":[{\"requirementIndex\":0,\"quote\":\"Invented evidence\"}]}";
        Assert.Throws<JsonException>(() => BedrockInference.Validate(source, response, request.ModelId));
    }

    [Fact]
    public void CloudHostBoundaryAllowsOnlyExactSafeHealthRoutesOnTargetIp()
    {
        var context = new DefaultHttpContext();
        context.Request.Host = new HostString("10.1.2.3", 8080);
        context.Request.Path = "/health/ready";
        Assert.True(AwsRuntime.HostAllowed(context, "https://example.invalid"));
        context.Request.Path = "/health/ready/extra";
        Assert.False(AwsRuntime.HostAllowed(context, "https://example.invalid"));
        context.Request.Path = "/api/session";
        Assert.False(AwsRuntime.HostAllowed(context, "https://example.invalid"));
        context.Request.Host = new HostString("example.invalid");
        Assert.True(AwsRuntime.HostAllowed(context, "https://example.invalid"));
    }

    [Fact]
    public async Task NativeForwardingTrustsOnlySelectedLoopbackProxyBeforeAppHandling()
    {
        var config = new ConfigurationBuilder().AddInMemoryCollection(Configuration()).Build();
        var flow = FlowOptions.Read(config, "screening");
        var builder = WebApplication.CreateBuilder();
        builder.Logging.ClearProviders();
        builder.WebHost.UseUrls("http://127.0.0.1:0");
        AwsRuntime.ConfigureProxy(builder.Services);
        await using var app = builder.Build();
        app.Use((context, next) =>
        {
            context.Connection.RemoteIpAddress = context.Request.Path == "/untrusted"
                ? System.Net.IPAddress.Parse("10.1.2.3") : System.Net.IPAddress.Loopback;
            return next();
        });
        AwsRuntime.Use(app, flow);
        app.MapGet("/{mode}", (HttpContext context) => new { scheme = context.Request.Scheme, peer = context.Connection.RemoteIpAddress!.ToString() });
        await app.StartAsync();
        var address = app.Services.GetRequiredService<IServer>().Features.Get<IServerAddressesFeature>()!.Addresses.Single();
        using var client = new HttpClient { BaseAddress = new Uri(address) };
        client.DefaultRequestHeaders.Host = "example.invalid";
        client.DefaultRequestHeaders.Add("X-Forwarded-Proto", "https");
        client.DefaultRequestHeaders.Add("X-Forwarded-For", "198.51.100.2");
        using var trusted = JsonDocument.Parse(await client.GetStringAsync("/trusted"));
        Assert.Equal("https", trusted.RootElement.GetProperty("scheme").GetString());
        Assert.Equal("198.51.100.2", trusted.RootElement.GetProperty("peer").GetString());
        using var untrusted = JsonDocument.Parse(await client.GetStringAsync("/untrusted"));
        Assert.Equal("http", untrusted.RootElement.GetProperty("scheme").GetString());
        Assert.Equal("10.1.2.3", untrusted.RootElement.GetProperty("peer").GetString());
        await app.StopAsync();
    }
}
