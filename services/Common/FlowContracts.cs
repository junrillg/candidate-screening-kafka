using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.Extensions.Configuration;

namespace CandidateScreening.Common;

public sealed record Submission(string DisplayName, string Profile, string[] Requirements);
public sealed record CandidateSummary(Guid Id, string DisplayName, string Status, DateTimeOffset CreatedAt,
    DateTimeOffset ExpiresAt, string? ErrorCode);
public sealed record SourceRecord(Guid Id, Guid OwnerId, string Profile, string[] Requirements, DateTimeOffset ExpiresAt);
public sealed record EvidenceItem(string Requirement, string? Evidence, string State);
public sealed record ScreeningResult(Guid CandidateId, EvidenceItem[] Items, bool ReviewRequired, string Provider, string Model);
public sealed record FlowEvent(int Version, Guid EventId, Guid CandidateId, Guid OwnerId, string Kind,
    DateTimeOffset OccurredAt, DateTimeOffset ExpiresAt)
{
    public static FlowEvent Create(Guid id, Guid owner, string kind, DateTimeOffset expires) =>
        new(1, Guid.NewGuid(), id, owner, kind, DateTimeOffset.UtcNow, expires);
}

public static class FlowJson
{
    public const int MaximumRequestBytes = 98304;
    public const int MaximumSourceBytes = 131072;
    public static JsonSerializerOptions Options { get; } = new(JsonSerializerDefaults.Web)
    {
        UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow,
        MaxDepth = 12
    };

    public static string Write<T>(T value) => JsonSerializer.Serialize(value, Options);

    public static T Read<T>(string value) => JsonSerializer.Deserialize<T>(value, Options)
        ?? throw new JsonException("Missing JSON value.");

    public static Submission Normalize(Submission input)
    {
        if (input.DisplayName is null || input.Profile is null || input.Requirements is null
            || input.Requirements.Length is < 1 or > 8 || input.Requirements.Any(item => item is null))
        {
            throw new ArgumentException("Invalid submission.");
        }
        var value = new Submission(input.DisplayName.Trim(), input.Profile.Replace("\r\n", "\n", StringComparison.Ordinal).Replace('\r', '\n').Trim(),
            input.Requirements.Select(item => item.Trim()).ToArray());
        if (value.DisplayName.Length is < 1 or > 80 || value.Profile.Length is < 1 or > 12000
            || value.Requirements.Any(item => item.Length is < 1 or > 200)
            || value.Requirements.Distinct(StringComparer.OrdinalIgnoreCase).Count() != value.Requirements.Length
            || value.Profile.Any(character => char.IsControl(character) && character is not ('\n' or '\t'))
            || value.DisplayName.Any(char.IsControl)
            || value.Requirements.Any(item => item.Any(char.IsControl)))
        {
            throw new ArgumentException("Invalid submission.");
        }
        return value;
    }

    public static byte[] Hash(Submission input) => SHA256.HashData(Encoding.UTF8.GetBytes(Write(input)));

    public static FlowEvent ParseEvent(string payload)
    {
        if (Encoding.UTF8.GetByteCount(payload) > 2048) { throw new JsonException("Event too large."); }
        var message = Read<FlowEvent>(payload);
        if (message.Version != 1 || message.EventId == Guid.Empty || message.OwnerId == Guid.Empty
            || (message.CandidateId == Guid.Empty && message.Kind != "SessionRevoked")
            || message.Kind is not ("Submitted" or "Screening" or "Completed" or "Failed" or "Deleted" or "Changed" or "SessionRevoked")
            || message.OccurredAt > DateTimeOffset.UtcNow.AddMinutes(5)
            || message.ExpiresAt > message.OccurredAt.AddDays(8))
        {
            throw new JsonException("Invalid event.");
        }
        return message;
    }
}

public sealed class FlowOptions
{
    public required byte[] InternalSecret { get; init; }
    public required Uri CandidatesUrl { get; init; }
    public required Uri ScreeningUrl { get; init; }
    public required Uri OllamaUrl { get; init; }
    public required string PublicOrigin { get; init; }
    public required string KeysPath { get; init; }
    public required string Model { get; init; }
    public required string Provider { get; init; }
    public string? DataProtectionPrefix { get; init; }
    public string? DataProtectionKmsKeyArn { get; init; }
    public int MaxQueued { get; init; } = 128;
    public int SubmissionsPerMinute { get; init; } = 60;
    public int SessionsPerMinute { get; init; } = 120;
    public int MaxStreams { get; init; } = 4096;
    public int MaxStreamsPerOwner { get; init; } = 4;
    public string ReplicaId { get; } = Guid.NewGuid().ToString("N");

    public static FlowOptions Read(IConfiguration configuration, string service)
    {
        var deployment = DeploymentProfile.Read(configuration);
        var secret = configuration["Lab:InternalSecret"] ?? "";
        if (secret.Length != 64 || !secret.All(Uri.IsHexDigit)) { throw new ArgumentException("Invalid internal authentication configuration."); }
        static Uri Endpoint(IConfiguration config, string name, string fallback)
        {
            if (!Uri.TryCreate(config["Lab:" + name] ?? fallback, UriKind.Absolute, out var uri)
                || uri.Scheme is not ("http" or "https") || uri.UserInfo.Length != 0
                || uri.Query.Length != 0 || uri.Fragment.Length != 0 || uri.AbsolutePath != "/")
            {
                throw new ArgumentException("Invalid service endpoint configuration.");
            }
            return uri;
        }
        static int Bound(IConfiguration config, string name, int fallback, int maximum)
        {
            var raw = config["Lab:" + name];
            if (raw is null) { return fallback; }
            if (!int.TryParse(raw, out var value) || value < 1 || value > maximum)
            { throw new ArgumentException("Invalid flow limit configuration."); }
            return value;
        }
        var origin = Endpoint(configuration, "PublicOrigin", "http://127.0.0.1:8094");
        var keysPath = configuration["Lab:KeysPath"] ?? "/keys";
        if (!Path.IsPathFullyQualified(keysPath)) { throw new ArgumentException("Invalid key storage configuration."); }
        var provider = configuration["Lab:AiProvider"] ?? "Ollama";
        if (provider is not ("Ollama" or "Bedrock") || (!deployment.IsAws && provider != "Ollama"))
        { throw new ArgumentException("Unknown or incompatible AI provider."); }
        var model = configuration["Lab:Model"] ?? "qwen2.5:1.5b-instruct";
        if (string.IsNullOrWhiteSpace(model) || model.Length > 128 || model.Any(char.IsControl))
        { throw new ArgumentException("Invalid AI model configuration."); }
        string? prefix = null;
        string? kms = null;
        if (deployment.IsAws)
        {
            if (configuration["Lab:AiProvider"] != "Bedrock" || model != "amazon.nova-micro-v1:0" || origin.Scheme != "https"
                || configuration["Lab:CandidatesUrl"] is null || configuration["Lab:ScreeningUrl"] is null
                || Endpoint(configuration, "CandidatesUrl", "http://candidates:8080").AbsoluteUri != "http://candidates:8080/"
                || Endpoint(configuration, "ScreeningUrl", "http://screening:8080").AbsoluteUri != "http://screening:8080/")
            { throw new ArgumentException("Invalid selected cloud runtime configuration."); }
            if (service == "candidates")
            {
                prefix = configuration["Lab:DataProtectionPrefix"];
                kms = configuration["Lab:DataProtectionKmsKeyArn"];
                if (prefix is null || prefix.Length is < 3 or > 160 || prefix[0] != '/' || prefix.EndsWith('/')
                    || prefix.Contains("//", StringComparison.Ordinal) || prefix.Split('/').Any(part => part is "." or "..")
                    || prefix.Any(character => !char.IsAsciiLetterOrDigit(character) && character is not ('/' or '-' or '_'))
                    || kms is null || !System.Text.RegularExpressions.Regex.IsMatch(kms,
                        "^arn:aws:kms:us-east-1:[0-9]{12}:key/[0-9a-fA-F-]{36}$", System.Text.RegularExpressions.RegexOptions.CultureInvariant)
                    || !Guid.TryParseExact(kms.Split('/')[1], "D", out _))
                { throw new ArgumentException("Invalid shared key storage configuration."); }
            }
        }
        return new FlowOptions
        {
            MaxQueued = Bound(configuration, "MaxQueued", 128, 100000),
            SubmissionsPerMinute = Bound(configuration, "SubmissionsPerMinute", 60, 100000),
            SessionsPerMinute = Bound(configuration, "SessionsPerMinute", 120, 100000),
            MaxStreams = Bound(configuration, "MaxStreams", 4096, 100000),
            MaxStreamsPerOwner = Bound(configuration, "MaxStreamsPerOwner", 4, 32),
            InternalSecret = Convert.FromHexString(secret),
            CandidatesUrl = Endpoint(configuration, "CandidatesUrl", "http://candidates:8080"),
            ScreeningUrl = Endpoint(configuration, "ScreeningUrl", "http://screening:8080"),
            OllamaUrl = Endpoint(configuration, "OllamaUrl", "http://ollama:11434"),
            PublicOrigin = origin.GetLeftPart(UriPartial.Authority),
            KeysPath = keysPath,
            Model = model,
            Provider = provider,
            DataProtectionPrefix = prefix,
            DataProtectionKmsKeyArn = kms
        };
    }
}
