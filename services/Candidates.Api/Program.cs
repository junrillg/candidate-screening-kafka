using System.Text.Json;
using Amazon;
using Amazon.SimpleSystemsManagement;
using CandidateScreening.Candidates;
using CandidateScreening.Common;
using Microsoft.AspNetCore.Antiforgery;
using Microsoft.AspNetCore.DataProtection;

return await FoundationHost.RunAsync(args, "candidates", (builder, lab, flow) =>
{
    var services = builder.Services;
    services.AddSingleton<BrowserSession>();
    services.AddSingleton<CandidatesFlow>();
    services.AddSingleton<CandidatesCompletion>();
    services.AddSingleton<ServiceCleanup>(_ => CandidatesCleanup.RunAsync);
    services.AddSingleton<ConsumerSubscription>(provider => new("screening-completed", "candidates-v1",
        provider.GetRequiredService<CandidatesCompletion>().HandleAsync, Exhausted: CandidatesCompletion.ExhaustedAsync));
    services.AddSingleton<ConsumerSubscription>(provider => new("candidate-changed", "changes-" + flow.ReplicaId, (message, _) =>
    {
        var hub = provider.GetRequiredService<ChangeHub>();
        if (message.Kind == "SessionRevoked") { hub.Revoke(message.OwnerId); }
        else if (message.Kind == "Changed") { hub.Publish(message.OwnerId, message.CandidateId); }
        else { throw new JsonException("Unexpected event kind."); }
        return Task.CompletedTask;
    }, Latest: true));
    var protection = services.AddDataProtection().SetApplicationName("CandidateScreening.v1");
    if (lab.Deployment.IsAws)
    {
        services.AddSingleton<IAmazonSimpleSystemsManagement>(_ => new AmazonSimpleSystemsManagementClient(
            new AmazonSimpleSystemsManagementConfig { RegionEndpoint = RegionEndpoint.USEast1, MaxErrorRetry = 1, Timeout = TimeSpan.FromSeconds(8) }));
        protection.PersistKeysToAWSSystemsManager(flow.DataProtectionPrefix!, options => options.KMSKeyId = flow.DataProtectionKmsKeyArn);
    }
    else
    {
        Directory.CreateDirectory(flow.KeysPath);
        protection.PersistKeysToFileSystem(new DirectoryInfo(flow.KeysPath));
    }
    services.AddAntiforgery(options =>
    {
        options.HeaderName = "X-CSRF-TOKEN";
        options.Cookie.Name = "lab.csrf";
        options.Cookie.HttpOnly = true;
        options.Cookie.SameSite = SameSiteMode.Strict;
        options.Cookie.SecurePolicy = flow.PublicOrigin.StartsWith("https://", StringComparison.Ordinal)
            ? CookieSecurePolicy.Always : CookieSecurePolicy.None;
    });
}, (app, flow) =>
{
    app.Use((context, next) => BrowserSession.MiddlewareAsync(context, next,
        app.Services.GetRequiredService<BrowserSession>(), app.Services.GetRequiredService<IAntiforgery>(), flow));
    app.Services.GetRequiredService<CandidatesFlow>().Map(app);
}, CandidatesCompletion.RetryAsync);
