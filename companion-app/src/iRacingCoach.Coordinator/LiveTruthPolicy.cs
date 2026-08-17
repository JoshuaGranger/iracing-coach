namespace iRacingCoach.Coordinator;

public static class LiveTruthPolicy
{
    public const int ContractVersion = 1;
    public const uint CautionMask = 0x0000C308u;
    private const uint RepairMask = 0x00100000u;

    public static string DecodeRacingState(uint? sessionFlags)
    {
        if (!sessionFlags.HasValue) return "unknown";
        var flags = sessionFlags.Value;
        if ((flags & 0x00020000u) != 0) return "disqualified";
        if ((flags & 0x00010000u) != 0) return "black";
        if ((flags & 0x00000010u) != 0) return "red";
        if ((flags & CautionMask) != 0) return "caution";
        if ((flags & 0x00000001u) != 0) return "checkered";
        if ((flags & 0x00000002u) != 0) return "white";
        if ((flags & (0x00000004u | 0x80000000u)) != 0) return "green";
        return "racing";
    }

    public static string DecodeRepairState(uint? sessionFlags) => sessionFlags switch
    {
        null => "unknown",
        { } flags when (flags & RepairMask) != 0 => "required",
        _ => "not_required"
    };

    public static bool IsUnderCaution(uint? sessionFlags) =>
        DecodeRacingState(sessionFlags) == "caution";

    public static double? NormalizeLapDistance(double? value) =>
        value is >= 0 and <= 1 && double.IsFinite(value.Value) ? value : null;

    public static string DisplayFlag(uint? sessionFlags) => DecodeRacingState(sessionFlags) switch
    {
        "disqualified" => "DISQUALIFIED",
        "black" => "BLACK FLAG",
        "red" => "RED",
        "caution" => "CAUTION",
        "checkered" => "CHECKERED",
        "white" => "WHITE",
        "green" => "GREEN",
        "racing" => "RACING",
        _ => "UNKNOWN"
    };
}
