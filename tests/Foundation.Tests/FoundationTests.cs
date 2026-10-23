using System.Net;
using CandidateScreening.Common;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Hosting.Server;
using Microsoft.AspNetCore.Hosting.Server.Features;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;
using Xunit;

namespace Foundation.Tests;

public sealed class FoundationTests
{
    private static Dictionary<string, string?> ValidConfiguration() => new()
    {
        ["Lab:DatabaseConnection"] = "Host=localhost;Database=candidates;Username=app;Password=synthetic-test-only",
        ["Lab:KafkaBootstrapServers"] = "kafka:9092",
        ["Lab:ServiceName"] = "candidates",
        ["Lab:OtlpEndpoint"] = "http://otel-collector:4318/v1/logs"
    };

    [Fact]
    public async Task ValidConfigurationCreatesBoundedDatabasePool()
    {
        var configuration = new ConfigurationBuilder().AddInMemoryCollection(ValidConfiguration()).Build();
        var options = LabOptions.Read(configuration, "candidates");
        await using var source = options.CreateDataSource();
        var connection = new Npgsql.NpgsqlConnectionStringBuilder(source.ConnectionString);
        Assert.Equal(5, connection.MaxPoolSize);
        Assert.False(connection.IncludeErrorDetail);
        Assert.False(connection.LogParameters);
    }

    [Theory]
    [InlineData("Lab:DatabaseConnection", "Password=do-not-expose;unrecognized-secret=do-not-expose")]
    [InlineData("Lab:DatabaseConnection", "Host=localhost;Database=db;Username=app;Password=do-not-expose;Include Error Detail=true")]
    [InlineData("Lab:KafkaBootstrapServers", "bad/path:9092")]
    [InlineData("Lab:KafkaBootstrapServers", "kafka")]
    [InlineData("Lab:ServiceName", "screening")]
    [InlineData("Lab:OtlpEndpoint", "http://user:do-not-expose@collector/v1/logs")]
    [InlineData("Lab:OtlpEndpoint", "http://collector:4318")]
    public void InvalidConfigurationFailsWithoutValues(string key, string value)
    {
        var values = ValidConfiguration();
        values[key] = value;
        var configuration = new ConfigurationBuilder().AddInMemoryCollection(values).Build();
        var exception = Assert.Throws<OptionsValidationException>(() => LabOptions.Read(configuration, "candidates"));
        Assert.DoesNotContain("do-not-expose", exception.ToString());
        Assert.Contains(key, exception.Message);
    }

    [Theory]
    [InlineData(false, false, 0, false)]
    [InlineData(true, false, 0, false)]
    [InlineData(false, true, 0, false)]
    [InlineData(true, true, 0, true)]
    [InlineData(true, true, 16, false)]
    [InlineData(true, true, -1, false)]
    public void ReadinessRequiresBothDependenciesAndFreshObservation(bool database, bool kafka, int ageSeconds, bool ready)
    {
        var now = DateTimeOffset.UtcNow;
        Assert.Equal(ready, new ReadinessSnapshot(database, kafka, now.AddSeconds(-ageSeconds)).IsReady(now));
    }

    [Fact]
    public async Task ReadinessHttpReturnsSafeStatusAndNeverDependencyPayloads()
    {
        var builder = WebApplication.CreateBuilder();
        builder.Logging.ClearProviders();
        builder.WebHost.UseUrls("http://127.0.0.1:0");
        await using var app = builder.Build();
        var now = DateTimeOffset.UtcNow;
        app.MapGet("/ready", () => FoundationHost.ReadyResult(new(true, true, now), now));
        app.MapGet("/unready", () => FoundationHost.ReadyResult(new(false, true, now), now));
        await app.StartAsync();
        var address = app.Services.GetRequiredService<IServer>().Features.Get<IServerAddressesFeature>()?.Addresses.Single();
        Assert.NotNull(address);
        using var client = new HttpClient { BaseAddress = new Uri(address) };
        using var ready = await client.GetAsync("/ready");
        using var unready = await client.GetAsync("/unready");
        Assert.Equal(HttpStatusCode.OK, ready.StatusCode);
        Assert.Equal("{\"status\":\"ready\"}", await ready.Content.ReadAsStringAsync());
        Assert.Equal(HttpStatusCode.ServiceUnavailable, unready.StatusCode);
        Assert.Equal("{\"status\":\"not_ready\"}", await unready.Content.ReadAsStringAsync());
        await app.StopAsync();
    }
}
