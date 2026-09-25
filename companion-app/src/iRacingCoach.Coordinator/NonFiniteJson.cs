using System.Text.Json;

namespace iRacingCoach.Coordinator;

// Older analyzers serialized Python's non-standard NaN, Infinity and -Infinity
// tokens - for example the total_time_s of an infeasible strategy. Strict JSON
// rejects the whole report because of one unknown value. Catalog and metadata
// reads replace only those bare tokens, never text inside strings, with null:
// the value is unknown, never zero. The user's report file is not rewritten.
internal static class NonFiniteJson
{
    private static readonly byte[] Null = "null"u8.ToArray();
    private static readonly byte[][] Tokens = ["-Infinity"u8.ToArray(), "Infinity"u8.ToArray(), "NaN"u8.ToArray()];

    public static JsonDocument Parse(byte[] utf8) => JsonDocument.Parse(NullBareNonFiniteNumbers(utf8, out _));

    /// <summary>
    /// Returns <paramref name="utf8"/> itself when it has no bare non-finite number,
    /// otherwise a copy in which each such value token is <c>null</c>.
    /// </summary>
    public static byte[] NullBareNonFiniteNumbers(byte[] utf8, out int replaced)
    {
        ArgumentNullException.ThrowIfNull(utf8);
        replaced = 0;
        var source = utf8.AsSpan();
        // Nearly every report is standard JSON; a vectorized search keeps them to one pass.
        if (source.IndexOf("Infinity"u8) < 0 && source.IndexOf("NaN"u8) < 0) return utf8;

        MemoryStream? output = null;
        var copied = 0;
        var inString = false;
        var escaped = false;
        byte previous = (byte)'[';
        for (var index = 0; index < source.Length; index++)
        {
            var value = source[index];
            if (inString)
            {
                if (escaped) escaped = false;
                else if (value == (byte)'\\') escaped = true;
                else if (value == (byte)'"') { inString = false; previous = value; }
                continue;
            }
            if (value == (byte)'"') { inString = true; continue; }
            if (IsWhitespace(value)) continue;
            if ((value is (byte)'-' or (byte)'I' or (byte)'N') && (previous is (byte)':' or (byte)',' or (byte)'['))
            {
                var length = MatchToken(source, index);
                if (length > 0)
                {
                    output ??= new MemoryStream(source.Length + 16);
                    output.Write(source[copied..index]);
                    output.Write(Null);
                    index += length - 1;
                    copied = index + 1;
                    replaced++;
                    previous = (byte)'l';
                    continue;
                }
            }
            previous = value;
        }
        if (output is null) return utf8;
        output.Write(source[copied..]);
        return output.ToArray();
    }

    private static int MatchToken(ReadOnlySpan<byte> source, int index)
    {
        foreach (var token in Tokens)
        {
            if (!source[index..].StartsWith(token)) continue;
            var end = index + token.Length;
            if (end == source.Length || IsWhitespace(source[end]) || source[end] is (byte)',' or (byte)']' or (byte)'}')
                return token.Length;
        }
        return 0;
    }

    private static bool IsWhitespace(byte value) => value is (byte)' ' or (byte)'\t' or (byte)'\r' or (byte)'\n';
}
