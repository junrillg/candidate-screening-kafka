using System.Security.Claims;
using System.Security.Cryptography;
using System.Text;
using CandidateScreening.Common;
using Microsoft.AspNetCore.Antiforgery;
using Microsoft.AspNetCore.DataProtection;
using Microsoft.AspNetCore.Http;
using Npgsql;

namespace CandidateScreening.Candidates;

public sealed class BrowserSession(IDataProtectionProvider protection, NpgsqlDataSource database, FlowOptions options)
{
    private const string Cookie = "lab.owner";
    private readonly IDataProtector protector = protection.CreateProtector("CandidateScreening.Owner.v1");

    public async Task<Guid?> ReadAsync(HttpContext context)
    {
        if (!context.Request.Cookies.TryGetValue(Cookie, out var value) || value.Length > 1024) { return null; }
        Guid owner;
        try
        {
            if (!Guid.TryParseExact(protector.Unprotect(value), "N", out owner)) { return null; }
        }
        catch (Exception exception) when (exception is CryptographicException or FormatException) { return null; }
        await using var connection = await database.OpenConnectionAsync(context.RequestAborted);
        await using var command = FlowDatabase.Command(connection,
            "SELECT expires_at FROM owner_sessions WHERE id=@owner AND NOT revoked AND expires_at>now()", ("owner", owner));
        if (await command.ExecuteScalarAsync(context.RequestAborted) is not DateTime expires) { return null; }
        context.Items["sessionExpiresAt"] = new DateTimeOffset(expires);
        context.User = Principal(owner);
        return owner;
    }

    public async Task<Guid> CreateAsync(HttpContext context)
    {
        var owner = Guid.NewGuid();
        await using var connection = await database.OpenConnectionAsync(context.RequestAborted);
        await using var transaction = await connection.BeginTransactionAsync(context.RequestAborted);
        if (!await Admission.ReserveAsync(connection, options, true, context.RequestAborted)) { throw new AdmissionRejectedException(); }
        await FlowDatabase.ExecuteAsync(connection, "INSERT INTO owner_sessions(id,expires_at) VALUES(@id,now()+interval '7 days')",
            context.RequestAborted, ("id", owner));
        await transaction.CommitAsync(context.RequestAborted);
        RefreshCookie(context, owner, DateTimeOffset.UtcNow.AddDays(7));
        context.User = Principal(owner);
        return owner;
    }

    public void RefreshCookie(HttpContext context, Guid owner, DateTimeOffset expires)
    {
        context.Response.Cookies.Append(Cookie, protector.Protect(owner.ToString("N")), new CookieOptions
        {
            HttpOnly = true,
            Secure = options.PublicOrigin.StartsWith("https://", StringComparison.Ordinal),
            SameSite = SameSiteMode.Strict,
            IsEssential = true,
            Path = "/",
            Expires = expires
        });
    }

    public static Guid Owner(HttpContext context) => Guid.Parse(context.User.FindFirstValue(ClaimTypes.NameIdentifier)
        ?? throw new InvalidOperationException("Missing authenticated session."));

    private static ClaimsPrincipal Principal(Guid owner) => new(new ClaimsIdentity(
        [new Claim(ClaimTypes.NameIdentifier, owner.ToString()), new Claim(ClaimTypes.Name, owner.ToString())], "SyntheticSession"));

    public static async Task MiddlewareAsync(HttpContext context, Func<Task> next, BrowserSession sessions,
        IAntiforgery antiforgery, FlowOptions options)
    {
        if (!context.Request.Path.StartsWithSegments("/api")) { await next(); return; }
        context.Response.Headers.CacheControl = "no-store";
        context.Response.Headers.XContentTypeOptions = "nosniff";
        context.Response.Headers.ContentSecurityPolicy = "default-src 'none'; frame-ancestors 'none'";
        if (context.Request.Headers["Sec-Fetch-Site"] == "cross-site")
        { await ApiErrors.Error("csrf_failed", 403).ExecuteAsync(context); return; }
        var owner = await sessions.ReadAsync(context);
        if (context.Request.Method == "GET" && context.Request.Path == "/api/session")
        { await next(); return; }
        if (owner is null) { await ApiErrors.Error("session_expired", 401).ExecuteAsync(context); return; }
        if (context.Request.Method is not ("GET" or "HEAD"))
        {
            if (!string.Equals(context.Request.Headers.Origin, options.PublicOrigin, StringComparison.Ordinal))
            { await ApiErrors.Error("csrf_failed", 403).ExecuteAsync(context); return; }
            try { await antiforgery.ValidateRequestAsync(context); }
            catch (AntiforgeryValidationException)
            { await ApiErrors.Error("csrf_failed", 403).ExecuteAsync(context); return; }
        }
        await next();
    }
}
