using System.Text.Json;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Http;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Logging.Console;
using Npgsql;
using OpenTelemetry;
using OpenTelemetry.Exporter;
using OpenTelemetry.Logs;
using OpenTelemetry.Resources;

namespace CandidateScreening.Common;

public static class FoundationHost
{
    public static async Task<int> RunAsync(string[] args, string serviceName,
        Action<WebApplicationBuilder, LabOptions, FlowOptions> configureService,
        Action<WebApplication, FlowOptions> configurePipeline,
        Func<NpgsqlConnection, FlowEvent, FlowOptions, CancellationToken, Task>? retryEvent = null)
    {
        if (args is ["--healthcheck"])
        {
            return await CheckHealthAsync();
        }

        try
        {
            if (args is ["--initialize-deployment"])
            {
                var configuration = WebApplication.CreateBuilder([]).Configuration;
                await DeploymentInitializer.RunAsync(configuration);
                return 0;
            }
            var maintenance = args.Length > 0 && args[0] is "--outbox-failures" or "--retry-outbox" or "--event-failures" or "--retry-event";
            var builder = WebApplication.CreateBuilder(maintenance ? [] : args);
            var options = LabOptions.Read(builder.Configuration, serviceName);
            if (maintenance)
            {
                await using var data = options.CreateDataSource();
                var maintenanceFlow = FlowOptions.Read(builder.Configuration, serviceName);
                return await OutboxMaintenance.RunAsync(data, args, maintenanceFlow, retryEvent);
            }
            ConfigureLogging(builder.Logging, options);
            builder.WebHost.ConfigureKestrel(server =>
            {
                server.AddServerHeader = false;
                server.Limits.MaxRequestBodySize = FlowJson.MaximumRequestBytes;
                server.Limits.RequestHeadersTimeout = TimeSpan.FromSeconds(10);
            });
            builder.Services.AddSingleton(options);
            builder.Services.AddSingleton(_ => options.CreateDataSource());
            builder.Services.AddSingleton<DependencyReadiness>();
            builder.Services.AddHostedService(provider => provider.GetRequiredService<DependencyReadiness>());
            var flow = FlowOptions.Read(builder.Configuration, serviceName);
            builder.Services.AddSingleton(flow);
            builder.Services.AddSingleton(_ => new ChangeHub(flow.MaxStreams, flow.MaxStreamsPerOwner));
            builder.Services.AddHttpClient("internal", client => client.Timeout = TimeSpan.FromSeconds(8))
                .RedactLoggedHeaders(["X-Service-Token"])
                .ConfigurePrimaryHttpMessageHandler(() => new SocketsHttpHandler
                {
                    AllowAutoRedirect = false,
                    ConnectTimeout = TimeSpan.FromSeconds(3),
                    MaxConnectionsPerServer = 8,
                    PooledConnectionLifetime = TimeSpan.FromMinutes(5)
                });
            builder.Services.AddHttpClient("inference", client => client.Timeout = TimeSpan.FromSeconds(90))
                .ConfigurePrimaryHttpMessageHandler(() => new SocketsHttpHandler
                {
                    AllowAutoRedirect = false,
                    ConnectTimeout = TimeSpan.FromSeconds(3),
                    MaxConnectionsPerServer = 2,
                    PooledConnectionLifetime = TimeSpan.FromMinutes(5)
                });
            builder.Services.Configure<Microsoft.AspNetCore.Routing.RouteHandlerOptions>(routing => routing.ThrowOnBadRequest = true);
            builder.Services.ConfigureHttpJsonOptions(json =>
            {
                json.SerializerOptions.UnmappedMemberHandling = System.Text.Json.Serialization.JsonUnmappedMemberHandling.Disallow;
                json.SerializerOptions.MaxDepth = 12;
            });
            if (options.Deployment.IsAws) { AwsRuntime.ConfigureProxy(builder.Services); }
            configureService(builder, options, flow);
            builder.Services.AddHostedService<FlowTransport>();
            await using var app = builder.Build();
            await FlowDatabase.InitializeAsync(app.Services.GetRequiredService<NpgsqlDataSource>(), serviceName, app.Lifetime.ApplicationStopping);
            if (options.Deployment.IsAws) { AwsRuntime.Use(app, flow); }
            app.Use(async (context, next) =>
            {
                try { await next(); }
                catch (OperationCanceledException) when (context.RequestAborted.IsCancellationRequested) { }
                catch (AdmissionRejectedException)
                { await ApiErrors.Error("rate_limited", 429).ExecuteAsync(context); }
                catch (Exception exception) when (exception is JsonException or BadHttpRequestException or ArgumentException)
                {
                    if (!context.Response.HasStarted) { await ApiErrors.Error("validation_failed", 400).ExecuteAsync(context); }
                    else { context.Abort(); }
                }
                catch (Exception exception) when (exception is NpgsqlException or TimeoutException or InvalidOperationException)
                {
                    if (!context.Response.HasStarted) { await ApiErrors.Error("internal_error", 503).ExecuteAsync(context); }
                    else { context.Abort(); }
                }
            });
            configurePipeline(app, flow);
            app.MapGet("/health/live", () => Results.Json(new { status = "live" }));
            app.MapGet("/health/ready", (DependencyReadiness readiness) =>
                ReadyResult(readiness.Snapshot, DateTimeOffset.UtcNow));
            await app.RunAsync();
            return 0;
        }
        catch (Exception)
        {
            // Framework/provider exceptions can embed configuration values; report no exception payload.
            await Console.Error.WriteLineAsync("Service startup or execution failed; inspect configuration and dependency availability.");
            return 1;
        }
    }

    public static IResult ReadyResult(ReadinessSnapshot snapshot, DateTimeOffset now) =>
        snapshot.IsReady(now)
            ? Results.Json(new { status = "ready" })
            : Results.Json(new { status = "not_ready" }, statusCode: StatusCodes.Status503ServiceUnavailable);

    private static void ConfigureLogging(ILoggingBuilder logging, LabOptions options)
    {
        logging.ClearProviders();
        logging.AddFilter((category, level) => category is not null
            && category.StartsWith("CandidateScreening.", StringComparison.Ordinal) && level >= LogLevel.Information);
        logging.AddJsonConsole(console =>
        {
            console.IncludeScopes = false;
            console.UseUtcTimestamp = true;
            console.TimestampFormat = "O";
        });
        logging.AddConsole(console =>
        {
            console.MaxQueueLength = 256;
            console.QueueFullMode = ConsoleLoggerQueueFullMode.DropWrite;
        });
        if (options.Deployment.IsAws) { return; }
        logging.AddOpenTelemetry(logs =>
        {
            logs.SetResourceBuilder(ResourceBuilder.CreateDefault().AddService(options.ServiceName));
            logs.IncludeFormattedMessage = false;
            logs.IncludeScopes = false;
            logs.AddOtlpExporter((exporter, processor) =>
            {
                exporter.Protocol = OtlpExportProtocol.HttpProtobuf;
                exporter.Endpoint = options.OtlpEndpoint!;
                exporter.TimeoutMilliseconds = 2000;
                processor.ExportProcessorType = ExportProcessorType.Batch;
                processor.BatchExportProcessorOptions.MaxQueueSize = 256;
                processor.BatchExportProcessorOptions.MaxExportBatchSize = 64;
                processor.BatchExportProcessorOptions.ScheduledDelayMilliseconds = 1000;
                processor.BatchExportProcessorOptions.ExporterTimeoutMilliseconds = 3000;
            });
        });
    }

    private static async Task<int> CheckHealthAsync()
    {
        using var client = new HttpClient { Timeout = TimeSpan.FromSeconds(3) };
        try
        {
            using var response = await client.GetAsync("http://127.0.0.1:8080/health/ready");
            return response.StatusCode == System.Net.HttpStatusCode.OK ? 0 : 1;
        }
        catch (Exception exception) when (exception is HttpRequestException or OperationCanceledException)
        {
            return 1;
        }
    }
}
