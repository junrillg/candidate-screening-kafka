using System.Reflection;
using System.Text.Json;
using CandidateScreening.Common;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.Logging;
using Xunit;
using Xunit.Abstractions;

namespace Foundation.Tests;

[CollectionDefinition("Console logger", DisableParallelization = true)]
public sealed class ConsoleLoggerCollection { }

[Collection("Console logger")]
public sealed class LoggingTests(ITestOutputHelper output)
{
    [Fact]
    public void ActualFlowEmitterHasSafeStructuredStateAndCorrelationFields()
    {
        var values = new Dictionary<string, string?>
        {
            ["Lab:Deployment"] = "Aws",
            ["Lab:AwsRegion"] = "us-east-1",
            ["Lab:ServiceName"] = "candidates",
            ["Lab:DatabaseHost"] = "synthetic.example.us-east-1.rds.amazonaws.com",
            ["Lab:DatabasePassword"] = new string('a', 64),
            ["Lab:KafkaBootstrapServers"] = "b-1.synthetic.example.kafka.us-east-1.amazonaws.com:9098"
        };
        var options = LabOptions.Read(new ConfigurationBuilder().AddInMemoryCollection(values).Build(), "candidates");
        var original = Console.Out;
        using var capture = new StringWriter();
        try
        {
            Console.SetOut(capture);
            using (var factory = LoggerFactory.Create(logging =>
                typeof(FoundationHost).GetMethod("ConfigureLogging", BindingFlags.NonPublic | BindingFlags.Static)!
                    .Invoke(null, [logging, options])))
            {
                var logger = factory.CreateLogger("CandidateScreening.Synthetic");
                typeof(FlowTransport).GetMethod("Signal", BindingFlags.Public | BindingFlags.Static)!
                    .Invoke(null, [logger, "critical_loop_failed_Synthetic", Guid.Empty, Guid.Empty, Guid.Empty, 2, 37L]);
            }
        }
        finally { Console.SetOut(original); }
        var line = capture.ToString().Split('\n', StringSplitOptions.RemoveEmptyEntries).Single();
        using var document = JsonDocument.Parse(line);
        var state = document.RootElement.GetProperty("State");
        Assert.Equal("critical_loop_failed_Synthetic", state.GetProperty("State").GetString());
        Assert.Equal(2, state.GetProperty("Attempt").GetInt32());
        Assert.Equal(37, state.GetProperty("ElapsedMs").GetInt64());
        Assert.Equal(Guid.Empty, state.GetProperty("CandidateId").GetGuid());
        Assert.Equal(Guid.Empty, state.GetProperty("EventId").GetGuid());
        Assert.False(document.RootElement.TryGetProperty("Exception", out _));
        output.WriteLine(line);
    }
}
