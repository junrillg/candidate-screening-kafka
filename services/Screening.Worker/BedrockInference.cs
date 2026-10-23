using System.Text.Json;
using Amazon.BedrockRuntime;
using Amazon.BedrockRuntime.Model;
using Amazon.Runtime;
using CandidateScreening.Common;

namespace CandidateScreening.Screening;

public sealed class BedrockInference(IAmazonBedrockRuntime runtime, FlowOptions options)
{
    public static ConverseRequest Request(SourceRecord source, string model) => new()
    {
        ModelId = model,
        System = [new SystemContentBlock { Text = EvidencePrompt.System +
            " JSON shape: {\"items\":[{\"requirementIndex\":0,\"quote\":null}]}. Include exactly one item for every index, no extra properties, quote at most400 characters. Do not wrap JSON in Markdown." }],
        Messages = [new Message { Role = ConversationRole.User, Content = [new ContentBlock
        { Text = FlowJson.Write(new { narrative = source.Profile, requirements = source.Requirements.Select((text, index) => new { index, text }) }) }] }],
        InferenceConfig = new InferenceConfiguration { MaxTokens = 768, Temperature = 0 }
    };

    public static ScreeningResult Validate(SourceRecord source, ConverseResponse response, string model)
    {
        var message = response.Output?.Message;
        if (response.StopReason != StopReason.End_turn || message?.Role != ConversationRole.Assistant
            || message.Content is null || message.Content.Count != 1 || string.IsNullOrEmpty(message.Content[0].Text)
            || message.Content[0].ToolUse is not null)
        { throw new JsonException("Incomplete inference response."); }
        return EvidenceValidation.Validate(source, message.Content[0].Text, "Bedrock", model);
    }

    public async Task<ScreeningResult> ScreenAsync(SourceRecord source, CancellationToken token)
    {
        try
        {
            var response = await runtime.ConverseAsync(Request(source, options.Model), token);
            return Validate(source, response, options.Model);
        }
        catch (Exception exception) when (exception is AmazonServiceException or AmazonClientException)
        { throw new HttpRequestException("Inference transport unavailable."); }
    }
}
