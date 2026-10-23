using Amazon;
using AWS.MSK.Auth;
using Confluent.Kafka;

namespace CandidateScreening.Common;

public static class KafkaConfiguration
{
    public static ClientConfig Transport(LabOptions options)
    {
        var config = new ClientConfig { BootstrapServers = options.KafkaBootstrapServers };
        if (options.Deployment.IsAws)
        {
            config.SecurityProtocol = SecurityProtocol.SaslSsl;
            config.SaslMechanism = SaslMechanism.OAuthBearer;
        }
        return config;
    }

    private static Action<IClient, string> RefreshToken()
    {
        var signer = new AWSMSKAuthTokenGenerator();
        return (client, _) =>
        {
            try
            {
                var (token, expiry) = signer.GenerateAuthToken(RegionEndpoint.USEast1);
                client.OAuthBearerSetToken(token, expiry, "ecs-task-role");
            }
            catch (Exception) { client.OAuthBearerSetTokenFailure("IAM authentication unavailable."); }
        };
    }

    public static AdminClientBuilder Admin(LabOptions options)
    {
        var builder = new AdminClientBuilder(new AdminClientConfig(Transport(options))
        { SocketTimeoutMs = 2000, SocketConnectionSetupTimeoutMs = 2000 });
        if (options.Deployment.IsAws) { builder.SetOAuthBearerTokenRefreshHandler(RefreshToken()); }
        return builder.SetLogHandler((_, _) => { }).SetErrorHandler((_, _) => { });
    }

    public static ProducerBuilder<string, string> Producer(LabOptions options)
    {
        var builder = new ProducerBuilder<string, string>(new ProducerConfig(Transport(options))
        {
            EnableIdempotence = true,
            Acks = Acks.All,
            MessageTimeoutMs = 10000,
            SocketTimeoutMs = 5000,
            QueueBufferingMaxMessages = 32,
            QueueBufferingMaxKbytes = 128,
            AllowAutoCreateTopics = false
        });
        if (options.Deployment.IsAws) { builder.SetOAuthBearerTokenRefreshHandler(RefreshToken()); }
        return builder.SetLogHandler((_, _) => { }).SetErrorHandler((_, _) => { });
    }

    public static ConsumerBuilder<string, string> Consumer(LabOptions options, string group, bool latest)
    {
        var builder = new ConsumerBuilder<string, string>(ConsumerOptions(options, group, latest));
        if (options.Deployment.IsAws) { builder.SetOAuthBearerTokenRefreshHandler(RefreshToken()); }
        return builder;
    }

    public static ConsumerConfig ConsumerOptions(LabOptions options, string group, bool latest)
    {
        var config = Consumer(options.KafkaBootstrapServers, group, latest);
        var transport = Transport(options);
        foreach (var pair in transport) { config.Set(pair.Key, pair.Value); }
        return config;
    }

    public static ConsumerConfig Consumer(string brokers, string group, bool latest = false) =>
new ConsumerConfig
{
    BootstrapServers = brokers,
    GroupId = group,
    EnableAutoCommit = false,
    EnableAutoOffsetStore = false,
    AutoOffsetReset = latest ? AutoOffsetReset.Latest : AutoOffsetReset.Earliest,
    AllowAutoCreateTopics = false,
    FetchMaxBytes = 1048576,
    MaxPartitionFetchBytes = 65536,
    QueuedMinMessages = 1,
    QueuedMaxMessagesKbytes = 256,
    MaxPollIntervalMs = 300000,
    SessionTimeoutMs = 10000
};
}
