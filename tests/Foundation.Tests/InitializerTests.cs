using CandidateScreening.Common;
using Confluent.Kafka;
using Xunit;

namespace Foundation.Tests;

public sealed class InitializerTests
{
    private static (TopicMetadata? Metadata, IReadOnlyDictionary<string, string> Settings) Ready(bool aws,
        int partitions = 32, int? replicas = null)
    {
        var brokers = Enumerable.Range(1, replicas ?? (aws ? 3 : 1)).ToArray();
        return (new TopicMetadata("synthetic-topic", Enumerable.Range(0, partitions)
            .Select(id => new PartitionMetadata(id, 1, brokers, brokers, ErrorCode.NoError)).ToList(), ErrorCode.NoError),
            DeploymentInitializer.TopicSettings(aws));
    }

    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task TopicCreationWaitsForMetadataAndExactSettingsToConverge(bool aws)
    {
        var calls = 0;
        await DeploymentInitializer.WaitForTopicAsync(_ =>
        {
            calls++;
            if (calls == 1) { throw new KafkaException(ErrorCode.Local_TimedOut); }
            var state = Ready(aws);
            if (calls == 2) { state.Metadata = new TopicMetadata("synthetic-topic", [], ErrorCode.UnknownTopicOrPart); }
            if (calls == 3)
            {
                var settings = DeploymentInitializer.TopicSettings(aws);
                settings.Remove("retention.ms");
                state.Settings = settings;
            }
            return Task.FromResult(state);
        }, aws, TimeSpan.FromSeconds(3), CancellationToken.None);
        Assert.Equal(4, calls);
    }

    [Theory]
    [InlineData("partitions")]
    [InlineData("replicas")]
    [InlineData("retention.ms")]
    [InlineData("retention.bytes")]
    [InlineData("segment.bytes")]
    [InlineData("max.message.bytes")]
    [InlineData("min.insync.replicas")]
    public async Task PersistentTopicTopologyOrSettingMismatchStillFailsClosed(string mismatch)
    {
        var state = Ready(false, mismatch == "partitions" ? 31 : 32, mismatch == "replicas" ? 2 : 1);
        if (mismatch is not ("partitions" or "replicas"))
        {
            var settings = DeploymentInitializer.TopicSettings(false);
            settings[mismatch] = "unexpected";
            state.Settings = settings;
        }
        await Assert.ThrowsAsync<InvalidOperationException>(() => DeploymentInitializer.WaitForTopicAsync(
            _ => Task.FromResult(state), false, TimeSpan.FromMilliseconds(50), CancellationToken.None)
            .WaitAsync(TimeSpan.FromSeconds(3)));
    }

    [Fact]
    public async Task CallerCancellationInterruptsReadinessInsteadOfBecomingAStateGuardFailure()
    {
        using var cancellation = new CancellationTokenSource();
        var reading = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        var wait = DeploymentInitializer.WaitForTopicAsync(async token =>
        {
            reading.SetResult();
            await Task.Delay(Timeout.InfiniteTimeSpan, token);
            return Ready(false);
        }, false, TimeSpan.FromSeconds(20), cancellation.Token);
        await reading.Task.WaitAsync(TimeSpan.FromSeconds(3));
        cancellation.Cancel();
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() => wait.WaitAsync(TimeSpan.FromSeconds(3)));
    }

    [Fact]
    public async Task PermanentBrokerFailureIsNotRetried()
    {
        var calls = 0;
        await Assert.ThrowsAsync<KafkaException>(() => DeploymentInitializer.WaitForTopicAsync(_ =>
        {
            calls++;
            throw new KafkaException(ErrorCode.TopicAuthorizationFailed);
        }, false, TimeSpan.FromSeconds(20), CancellationToken.None));
        Assert.Equal(1, calls);
    }

    [Fact]
    public async Task ReturnedPermanentMetadataErrorIsNotRetried()
    {
        var calls = 0;
        await Assert.ThrowsAsync<KafkaException>(() => DeploymentInitializer.WaitForTopicAsync(_ =>
        {
            calls++;
            var state = Ready(false);
            state.Metadata = new TopicMetadata("synthetic-topic", [], ErrorCode.TopicAuthorizationFailed);
            return Task.FromResult(state);
        }, false, TimeSpan.FromSeconds(20), CancellationToken.None));
        Assert.Equal(1, calls);
    }
}
