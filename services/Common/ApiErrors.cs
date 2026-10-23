using Microsoft.AspNetCore.Http;

namespace CandidateScreening.Common;

public sealed class AdmissionRejectedException : Exception { }

public static class ApiErrors
{
    public static IResult Error(string code, int status) => Results.Json(new
    {
        error = new
        {
            code,
            message = code switch
            {
                "validation_failed" => "Check the submitted values.",
                "csrf_failed" => "Refresh the session before trying again.",
                "session_expired" => "The session has expired. Start a new session.",
                "not_found" => "This record is unavailable.",
                "idempotency_conflict" => "This request key was already used.",
                "result_not_ready" => "The result is not available yet.",
                "rate_limited" => "Try again after the current requests finish.",
                _ => "The service could not complete this request. Try again."
            }
        }
    }, statusCode: status);

}
