using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace CandidateScreening.Common;

public static partial class EvidenceValidation
{
    public static ScreeningResult Validate(SourceRecord source, string json, string provider, string model)
    {
        if (Encoding.UTF8.GetByteCount(json) > 16000) { throw new JsonException("Inference content too large."); }
        using var document = JsonDocument.Parse(json, new JsonDocumentOptions { MaxDepth = 8 });
        var root = document.RootElement;
        ExactProperties(root, "items");
        var rows = root.GetProperty("items");
        if (rows.ValueKind != JsonValueKind.Array || rows.GetArrayLength() != source.Requirements.Length)
        { throw new JsonException("Unexpected evidence count."); }
        var items = new EvidenceItem[source.Requirements.Length];
        var seen = new HashSet<int>();
        foreach (var row in rows.EnumerateArray())
        {
            ExactProperties(row, "requirementIndex", "quote");
            if (row.GetProperty("requirementIndex").ValueKind != JsonValueKind.Number || !row.GetProperty("requirementIndex").TryGetInt32(out var index) || index < 0
                || index >= items.Length || !seen.Add(index)) { throw new JsonException("Unexpected evidence index."); }
            var quote = row.GetProperty("quote");
            if (quote.ValueKind == JsonValueKind.Null)
            { items[index] = new(source.Requirements[index], null, "NotEvidenced"); continue; }
            if (quote.ValueKind != JsonValueKind.String) { throw new JsonException("Invalid evidence quote."); }
            var text = quote.GetString() ?? "";
            if (text.Length is < 1 or > 400 || !source.Profile.Contains(text, StringComparison.Ordinal))
            { throw new JsonException("Evidence was not present in source."); }
            var terms = Terms().Matches(source.Requirements[index]).Select(match => match.Value.TrimEnd('.').ToLowerInvariant())
                .Where(term => term.Length > 1 && term is not ("and" or "with" or "the" or "for" or "experience" or "in" or "of" or "using"))
                .Distinct(StringComparer.Ordinal).ToArray();
            var location = source.Profile.IndexOf(text, StringComparison.Ordinal);
            var start = location;
            while (start > 0 && !SentenceEnd(source.Profile, start - 1)) { start--; }
            var end = location + text.Length;
            if (!SentenceEnd(source.Profile, end - 1))
            {
                while (end < source.Profile.Length && !SentenceEnd(source.Profile, end)) { end++; }
                if (end < source.Profile.Length) { end++; }
            }
            var surrounding = source.Profile[start..end].Trim();
            var hasTerms = terms.Length > 0 && terms.All(term => Terms().Matches(text)
                .Any(match => string.Equals(match.Value.TrimEnd('.'), term, StringComparison.OrdinalIgnoreCase)));
            var positive = hasTerms && WorkVerbs().IsMatch(text) && EngineeringContext().IsMatch(text)
                && !Negative().IsMatch(surrounding) && !Injection().IsMatch(source.Profile);
            items[index] = new(source.Requirements[index], text, positive ? "EvidenceFound" : "NeedsReview");

        }
        return new(source.Id, items, true, provider, model);
    }

    private static bool SentenceEnd(string text, int at) => text[at] is '\n' or '!' or '?'
        || (text[at] == '.' && (at + 1 == text.Length || char.IsWhiteSpace(text[at + 1])));

    private static void ExactProperties(JsonElement value, params string[] expected)
    {
        if (value.ValueKind != JsonValueKind.Object) { throw new JsonException("Expected evidence object."); }
        var properties = value.EnumerateObject().Select(property => property.Name).ToArray();
        if (properties.Length != expected.Length || properties.Distinct(StringComparer.Ordinal).Count() != expected.Length
            || properties.Any(property => !expected.Contains(property, StringComparer.Ordinal)))
        { throw new JsonException("Unexpected evidence properties."); }
    }

    [GeneratedRegex(@"[\p{L}\p{N}+#.]+", RegexOptions.CultureInvariant)]
    private static partial Regex Terms();
    [GeneratedRegex(@"\b(built|implemented|developed|delivered|used|worked|maintained|designed|operated|deployed)\b", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant)]
    private static partial Regex WorkVerbs();
    [GeneratedRegex(@"\b(services?|consumers?|producers?|applications?|platforms?|pipelines?|systems?|projects?|engineers?|software|databases?|APIs?|components?)\b", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant)]
    private static partial Regex EngineeringContext();
    [GeneratedRegex(@"\b(no|not|never|without|lack|limited|learning|unfamiliar|aspire|cannot|can['’]t|haven['’]t|hasn['’]t|didn['’]t|don['’]t|doesn['’]t|won['’]t|wouldn['’]t|wasn['’]t|weren['’]t|isn['’]t|aren['’]t)\b", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant)]
    private static partial Regex Negative();
    [GeneratedRegex(@"\b(ignore|instruction|prompt|system message|assistant|output json)\b", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant)]
    private static partial Regex Injection();
}
