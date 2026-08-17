using iRacingCoach.Contracts;

namespace iRacingCoach.Coordinator;

internal static class Garage61StatusReducer
{
    public static Garage61Connection Unprobed(bool credentialSaved) => new(
        credentialSaved ? "saved" : "absent",
        "unverified",
        "unverified",
        "unverified",
        "not_probed",
        credentialSaved
            ? "A protected connection is saved. Garage61 has not been checked yet."
            : "Add a Garage61 token when you want reference laps.");

    public static Garage61Connection ApplyProbe(
        Garage61Connection previous,
        string outcome,
        string detail = "")
    {
        var authentication = previous.Authentication;
        var permission = previous.Permission;
        string availability;

        switch (outcome)
        {
            case "ok":
                authentication = "valid";
                permission = "granted";
                availability = "available";
                break;
            case "unauthorized":
                authentication = "rejected";
                permission = "unverified";
                availability = "available";
                break;
            case "forbidden":
                authentication = "valid";
                permission = "denied";
                availability = "available";
                break;
            case "insufficient_scope":
                authentication = "valid";
                permission = "insufficient_scope";
                availability = "available";
                break;
            case "throttled":
                availability = "throttled";
                break;
            case "timeout":
                availability = "timed_out";
                break;
            case "cancelled":
                availability = "cancelled";
                break;
            case "malformed":
                availability = "malformed";
                break;
            case "dns_failure":
            case "connect_failure":
                availability = "unreachable";
                break;
            default:
                throw new ArgumentOutOfRangeException(nameof(outcome), outcome, "Unknown Garage61 probe outcome.");
        }

        var next = new Garage61Connection(
            previous.Credential,
            authentication,
            permission,
            availability,
            outcome,
            Message(authentication, permission, availability, detail));
        return next;
    }

    public static Garage61Connection ApplyFailure(Garage61Connection previous, Exception exception)
    {
        var outcome = exception switch
        {
            OperationCanceledException => "cancelled",
            TimeoutException => "timeout",
            InvalidDataException or System.Text.Json.JsonException => "malformed",
            _ => "connect_failure"
        };
        return ApplyProbe(previous, outcome, Safe(exception.Message));
    }

    public static string Label(Garage61Connection status) => status.Connected
        ? "Connected"
        : status.Credential == "absent" ? "Not configured"
        : status.Authentication == "rejected" ? "Credential rejected"
        : status.Permission == "insufficient_scope" ? "Permission needed"
        : status.Permission == "denied" ? "Access denied"
        : status.Availability == "timed_out" ? "Timed out"
        : status.Availability is "unreachable" or "throttled" or "malformed" ? "Temporarily unavailable"
        : "Protected connection saved";

    private static string Message(string authentication, string permission, string availability, string detail)
    {
        var message = authentication == "rejected"
            ? "Garage61 rejected this token. Replace the protected connection."
            : permission == "insufficient_scope"
                ? "The token is valid but needs the driving-data permission."
            : permission == "denied"
                ? "The token is valid, but this account cannot access Garage61 reference laps."
            : availability is "unreachable" or "timed_out" or "throttled"
                ? "Garage61 is temporarily unavailable. Local analysis remains ready."
            : availability == "malformed"
                ? "Garage61 returned a response that could not be trusted. Local analysis remains ready."
            : availability == "cancelled"
                ? "The Garage61 check was cancelled."
            : "Garage61 is connected and reference-lap permission is ready.";

        return string.IsNullOrWhiteSpace(detail) ? message : $"{message} {Safe(detail)}";
    }

    private static string Safe(string value)
    {
        var normalized = value.Replace('\r', ' ').Replace('\n', ' ').Trim();
        return normalized.Length <= 160 ? normalized : normalized[..160] + "…";
    }
}
