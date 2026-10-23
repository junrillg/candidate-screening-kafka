using System.Text.Json;

namespace CandidateScreening.Common;

public static class BoundedHttp
{
    public static async Task<byte[]> ReadAsync(HttpContent content, int limit, CancellationToken token)
    {
        if (content.Headers.ContentLength > limit) { throw new JsonException("Response too large."); }
        await using var stream = await content.ReadAsStreamAsync(token);
        using var output = new MemoryStream();
        var buffer = new byte[4096];
        int count;
        while ((count = await stream.ReadAsync(buffer, token)) != 0)
        {
            if (output.Length + count > limit) { throw new JsonException("Response too large."); }
            output.Write(buffer, 0, count);
        }
        return output.ToArray();
    }
}
