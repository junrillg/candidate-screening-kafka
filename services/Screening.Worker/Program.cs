using Amazon;
using Amazon.BedrockRuntime;
using CandidateScreening.Common;
using CandidateScreening.Screening;

return await FoundationHost.RunAsync(args, "screening", (builder, lab, flow) =>
{
    var services = builder.Services;
    services.AddSingleton<ScreeningFlow>();
    if (lab.Deployment.IsAws)
    {
        services.AddSingleton<IAmazonBedrockRuntime>(_ => new AmazonBedrockRuntimeClient(new AmazonBedrockRuntimeConfig
        { RegionEndpoint = RegionEndpoint.USEast1, MaxErrorRetry = 0, Timeout = TimeSpan.FromSeconds(85) }));
        services.AddSingleton<BedrockInference>();
    }
    services.AddSingleton<Inference>(provider => new(provider.GetRequiredService<IHttpClientFactory>(), flow,
        lab.Deployment.IsAws ? provider.GetRequiredService<BedrockInference>() : null));
    services.AddSingleton<ServiceCleanup>(_ => ScreeningCleanup.RunAsync);
    services.AddSingleton<ConsumerSubscription>(provider => new("candidate-submitted", "screening-v1",
        provider.GetRequiredService<ScreeningFlow>().AcceptAsync));
    services.AddSingleton<Func<CancellationToken, Task>>(provider => provider.GetRequiredService<ScreeningFlow>().ProcessAsync);
}, (app, _) => app.Services.GetRequiredService<ScreeningFlow>().Map(app));
