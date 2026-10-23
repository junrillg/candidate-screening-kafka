using System.Net;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.HttpOverrides;
using Microsoft.Extensions.DependencyInjection;

namespace CandidateScreening.Common;

public static class AwsRuntime
{
    public static void ConfigureProxy(IServiceCollection services)
    {
        services.Configure<ForwardedHeadersOptions>(options =>
        {
            options.ForwardedHeaders = ForwardedHeaders.XForwardedFor | ForwardedHeaders.XForwardedProto;
            options.ForwardLimit = 1;
            options.KnownIPNetworks.Clear();
            options.KnownProxies.Clear();
            options.KnownProxies.Add(IPAddress.Loopback);
            options.KnownProxies.Add(IPAddress.IPv6Loopback);
        });
    }

    public static bool HostAllowed(HttpContext context, string publicOrigin) =>
        context.Request.Path is { Value: "/health/live" or "/health/ready" }
        || string.Equals(context.Request.Host.Host, new Uri(publicOrigin).Host, StringComparison.OrdinalIgnoreCase)
        || context.Request.Host.Host is "candidates" or "screening" or "localhost" or "127.0.0.1" or "::1";

    public static void Use(WebApplication app, FlowOptions flow)
    {
        app.UseForwardedHeaders();
        app.Use(async (context, next) =>
        {
            if (!HostAllowed(context, flow.PublicOrigin))
            { await ApiErrors.Error("not_found", 404).ExecuteAsync(context); return; }
            await next();
        });
    }
}
