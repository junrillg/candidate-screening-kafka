using System.Security.Cryptography;
using Microsoft.AspNetCore.Http;

namespace CandidateScreening.Common;

public static class ServiceAuthentication
{
    public static bool Authorized(HttpContext context, FlowOptions options)
    {
        var supplied = context.Request.Headers["X-Service-Token"].ToString();
        return supplied.Length == 64 && supplied.All(Uri.IsHexDigit)
            && CryptographicOperations.FixedTimeEquals(Convert.FromHexString(supplied), options.InternalSecret);
    }

    public static HttpRequestMessage Request(Uri address, string path, FlowOptions options)
    {
        var request = new HttpRequestMessage(HttpMethod.Get, new Uri(address, path));
        request.Headers.Add("X-Service-Token", Convert.ToHexString(options.InternalSecret));
        return request;
    }
}
